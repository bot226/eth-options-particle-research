"""DeribitAdapter — Deribit exchange adapter for BTC options data.

Primary institutional options venue. Quality factor: 1.50.

REST: https://www.deribit.com/api/v2
WS:   wss://www.deribit.com/ws/api/v2

Deribit uses JSON-RPC 2.0 protocol for both REST and WebSocket.
Public endpoints — no authentication required.
"""

import json
import time
import asyncio
import logging
import httpx
import websockets
from typing import Optional

from api.base_adapter import BaseExchangeAdapter
from config import (
    DERIBIT_REST_URL, DERIBIT_WS_URL,
    WS_PING_INTERVAL, WS_RECONNECT_DELAY,
)

log = logging.getLogger(__name__)

_WS_TICKER_INTERVAL = "agg2"
_WS_SUBSCRIBE_BATCH_SIZE = 500
_WS_SUBSCRIBE_BATCH_DELAY_SEC = 0.35
_WS_INSTRUMENT_REFRESH_SEC = 15 * 60
_WS_INSTRUMENT_RETRY_SEC = 30
_INSTRUMENT_CACHE_TTL_SEC = 15 * 60
_WS_TICKER_CACHE_MAX_AGE_SEC = 30
_WS_MIN_CACHE_COVERAGE_RATIO = 0.70


class DeribitAdapter(BaseExchangeAdapter):
    """Async adapter for Deribit public options API."""

    def __init__(self):
        super().__init__(exchange_id="deribit", quality_factor=1.50)
        self._http = httpx.AsyncClient(
            base_url=DERIBIT_REST_URL,
            headers={"Accept": "application/json"},
            timeout=15,
        )
        self._ws_task: Optional[asyncio.Task] = None
        self._tickers_cache: list[dict] = []
        self._ticker_cache_by_instrument: dict[str, dict] = {}
        self._ticker_cache_ready = asyncio.Event()
        self._ws_subscription_retry_event = asyncio.Event()
        self._tickers_cache_ts: float = 0.0
        self._subscribed_ticker_channels: set[str] = set()
        self._pending_ticker_channels: dict[int, set[str]] = {}
        self._instrument_discovery_lock = asyncio.Lock()
        self._instruments_cache: list[dict] = []
        self._instruments_cache_ts: float = 0.0
        self._ws_instruments_count: int = 0
        self._ws_ticker_message_count: int = 0
        self._ws_last_ticker_ts: float = 0.0
        self._ws_last_subscription_refresh_ts: float = 0.0
        self._ws_subscription_error_count: int = 0
        self._spot_price: float = 0.0
        self._msg_id: int = 0
        
        # Diagnostics tracking
        self.last_success_ts: float = 0.0
        self.last_error_ts: float = 0.0
        self.last_error: str = ""
        self.request_count: int = 0
        self.success_count: int = 0
        self.error_count: int = 0
        
        # Additional state
        self.enabled_config: bool = True
        self.initialized: bool = True
        self.fetch_attempted: bool = False
        self.disabled_reason: str = ""

    # ── Lifecycle ────────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        self._ws_task = asyncio.create_task(self._ws_loop())
        log.info("DeribitAdapter started")

    async def stop(self) -> None:
        self._running = False
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        await self._http.aclose()
        log.info("DeribitAdapter stopped")

    # ── REST API ─────────────────────────────────────────────────────

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    async def _rpc_get(self, method: str, params: dict, max_retries: int = 3) -> Optional[dict]:
        """Deribit REST uses JSON-RPC style but via standard GET with query params."""
        self.fetch_attempted = True
        for attempt in range(max_retries):
            self.request_count += 1
            try:
                t0 = time.time()
                resp = await self._http.get(f"/public/{method}", params=params)
                latency = (time.time() - t0) * 1000
                resp.raise_for_status()
                data = resp.json()
                if "result" not in data:
                    error = data.get("error", {})
                    err_msg = error.get("message", "unknown")
                    self.last_error = f"API Error: {err_msg}"
                    self.last_error_ts = time.time()
                    self.error_count += 1
                    log.error("Deribit API error: %s", err_msg)
                    return None
                
                self.success_count += 1
                self.last_success_ts = time.time()
                self.health.update(latency_ms=latency, ws_connected=self.health.ws_connected)
                return data["result"]
            except Exception as e:
                self.error_count += 1
                self.last_error = str(e)
                self.last_error_ts = time.time()
                log.error("Deribit REST error (%s) attempt %d: %s", method, attempt + 1, e)
                if attempt < max_retries - 1:
                    await asyncio.sleep(1.0 * (2 ** attempt))
                else:
                    self.health.mark_error(str(e))
                    return None
        return None

    async def fetch_instruments(self) -> list[dict]:
        """Fetch all active BTC option instruments."""
        now = time.time()
        if (
            self._instruments_cache
            and now - self._instruments_cache_ts < _INSTRUMENT_CACHE_TTL_SEC
        ):
            return list(self._instruments_cache)

        async with self._instrument_discovery_lock:
            now = time.time()
            if (
                self._instruments_cache
                and now - self._instruments_cache_ts < _INSTRUMENT_CACHE_TTL_SEC
            ):
                return list(self._instruments_cache)

            result = await self._rpc_get("get_instruments", {
                "currency": "BTC",
                "kind": "option",
                "expired": "false",
            }, max_retries=1)
            if isinstance(result, list) and result:
                self._instruments_cache = list(result)
                self._instruments_cache_ts = time.time()
                self.last_error = ""
                return list(self._instruments_cache)

            if self._instruments_cache:
                log.warning(
                    "Deribit instrument discovery failed; using cached set (%d)",
                    len(self._instruments_cache),
                )
                return list(self._instruments_cache)
            return []

    async def fetch_option_tickers(self) -> list[dict]:
        """Return a snapshot of the live Deribit WebSocket ticker cache.

        The previous bulk REST request could block for nearly a minute on the
        collector host.  The hot MOS poll path is now cache-only and therefore
        remains non-blocking.  REST is retained only for instrument discovery.
        """
        if not self._ticker_cache_by_instrument:
            return []

        if not self._has_sufficient_ticker_coverage():
            self.disabled_reason = "deribit_ws_ticker_cache_warming"
            return []

        now = time.time()
        cache_age = now - self._tickers_cache_ts
        if cache_age > _WS_TICKER_CACHE_MAX_AGE_SEC:
            self.disabled_reason = "deribit_ws_ticker_cache_stale"
            return []

        tickers = list(self._ticker_cache_by_instrument.values())
        self._tickers_cache = tickers

        total_oi = 0.0
        total_vol = 0.0
        for t in tickers:
            total_oi += float(t.get("open_interest", 0) or 0)
            stats = t.get("stats") if isinstance(t.get("stats"), dict) else {}
            total_vol += float(
                t.get("volume_24h", t.get("volume", stats.get("volume", 0))) or 0
            )
        self._total_oi = total_oi
        self._total_volume = total_vol
        self.disabled_reason = ""

        return tickers

    def get_cached_tickers(self) -> list[dict]:
        """Return cached tickers if available, regardless of age."""
        if self._ticker_cache_by_instrument:
            return list(self._ticker_cache_by_instrument.values())
        return self._tickers_cache or []

    async def wait_for_option_tickers(self, timeout: float = 20.0) -> bool:
        """Wait until the WebSocket ticker cache has safe chain coverage."""
        if self._has_sufficient_ticker_coverage():
            return True
        deadline = time.monotonic() + timeout
        try:
            await asyncio.wait_for(self._ticker_cache_ready.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        while time.monotonic() < deadline:
            if self._has_sufficient_ticker_coverage():
                return True
            await asyncio.sleep(0.1)
        return False

    def _has_sufficient_ticker_coverage(self) -> bool:
        """Prevent a partially warmed Deribit chain from entering aggregation."""
        cached_count = len(self._ticker_cache_by_instrument)
        if cached_count == 0:
            return False
        if self._ws_instruments_count <= 0:
            return True
        return (
            cached_count / self._ws_instruments_count
            >= _WS_MIN_CACHE_COVERAGE_RATIO
        )

    async def fetch_spot_price(self) -> Optional[float]:
        """Fetch BTC index price from Deribit."""
        result = await self._rpc_get("get_index_price", {
            "index_name": "btc_usd",
        })
        if result and "index_price" in result:
            self._spot_price = float(result["index_price"])
            return self._spot_price
        return None

    # ── WebSocket ────────────────────────────────────────────────────

    async def _ws_loop(self):
        """WebSocket connection loop with exponential backoff reconnect."""
        attempt = 0
        _MAX_DELAY = 300  # 5 minutes cap when unreachable
        while self._running:
            delay = min(_MAX_DELAY, WS_RECONNECT_DELAY * (2 ** attempt))
            try:
                async with websockets.connect(
                    DERIBIT_WS_URL,
                    ping_interval=None,   # disable library pings
                    ping_timeout=None,
                    close_timeout=5,
                    open_timeout=10,
                ) as ws:
                    attempt = 0  # reset on successful connect
                    self.health.ws_connected = True
                    self._subscribed_ticker_channels.clear()
                    self._pending_ticker_channels.clear()
                    self._ws_subscription_retry_event.clear()
                    log.info("Deribit WS connected")

                    await self._ws_subscribe(ws, [
                        "deribit_price_index.btc_usd",
                    ])
                    refresh_task = asyncio.create_task(
                        self._ws_subscription_refresh_loop(ws)
                    )
                    try:
                        async for message in ws:
                            if not self._running:
                                break
                            try:
                                msg = json.loads(message)
                                await self._handle_ws_message(msg)
                            except Exception as e:
                                log.debug("Deribit WS message error: %s", e)
                    finally:
                        refresh_task.cancel()
                        try:
                            await refresh_task
                        except asyncio.CancelledError:
                            pass

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("Deribit WS error: %s", e)
                self.health.ws_connected = False
                self.health.mark_error(str(e))

            if self._running:
                attempt += 1
                log.info("Deribit WS reconnecting in %.0fs (attempt %d)...",
                         delay, attempt)
                await asyncio.sleep(delay)

        self.health.ws_connected = False

    async def _ws_subscription_refresh_loop(self, ws) -> None:
        """Discover new option instruments and subscribe without blocking reads."""
        while self._running:
            refreshed = await self._refresh_ws_option_subscriptions(ws)
            delay = (
                _WS_INSTRUMENT_REFRESH_SEC
                if refreshed
                else _WS_INSTRUMENT_RETRY_SEC
            )
            try:
                await asyncio.wait_for(
                    self._ws_subscription_retry_event.wait(),
                    timeout=delay,
                )
                self._ws_subscription_retry_event.clear()
            except asyncio.TimeoutError:
                pass

    async def _refresh_ws_option_subscriptions(self, ws) -> bool:
        """Subscribe to ticker channels for every active BTC option."""
        try:
            instruments = await asyncio.wait_for(
                self.fetch_instruments(), timeout=20.0
            )
        except asyncio.TimeoutError:
            self.last_error = "instrument_discovery_timeout>20s"
            self.last_error_ts = time.time()
            log.warning("Deribit instrument discovery timed out")
            return False
        except Exception as exc:
            self.last_error = f"instrument_discovery_error:{exc}"
            self.last_error_ts = time.time()
            log.warning("Deribit instrument discovery failed: %s", exc)
            return False

        instrument_names = sorted({
            str(item.get("instrument_name", ""))
            for item in instruments
            if str(item.get("instrument_name", "")).startswith("BTC-")
        })
        if not instrument_names:
            if not self.last_error:
                self.last_error = "instrument_discovery_empty"
            self.last_error_ts = time.time()
            return False

        self._ws_instruments_count = len(instrument_names)
        active_names = set(instrument_names)
        expired_names = set(self._ticker_cache_by_instrument) - active_names
        for instrument_name in expired_names:
            self._ticker_cache_by_instrument.pop(instrument_name, None)

        desired_channels = {
            f"ticker.{name}.{_WS_TICKER_INTERVAL}"
            for name in instrument_names
        }
        missing_channels = sorted(
            desired_channels
            - self._subscribed_ticker_channels
            - {
                channel
                for channels in self._pending_ticker_channels.values()
                for channel in channels
            }
        )
        batches = [
            missing_channels[offset:offset + _WS_SUBSCRIBE_BATCH_SIZE]
            for offset in range(0, len(missing_channels), _WS_SUBSCRIBE_BATCH_SIZE)
        ]
        for batch_index, batch in enumerate(batches):
            if batch_index > 0:
                await asyncio.sleep(_WS_SUBSCRIBE_BATCH_DELAY_SEC)
            await self._ws_subscribe(
                ws,
                batch,
                track_ticker_channels=True,
            )

        self._ws_last_subscription_refresh_ts = time.time()
        if missing_channels:
            log.info(
                "Deribit WS option subscriptions added=%d total=%d",
                len(missing_channels),
                len(desired_channels),
            )
        return True

    async def _ws_subscribe(
        self,
        ws,
        channels: list[str],
        *,
        track_ticker_channels: bool = False,
    ) -> Optional[int]:
        """Send JSON-RPC subscribe request."""
        if not channels:
            return None
        request_id = self._next_id()
        msg = json.dumps({
            "jsonrpc": "2.0",
            "method": "public/subscribe",
            "id": request_id,
            "params": {
                "channels": channels,
            }
        })
        if track_ticker_channels:
            self._pending_ticker_channels[request_id] = set(channels)
        try:
            await ws.send(msg)
        except Exception:
            self._pending_ticker_channels.pop(request_id, None)
            raise
        log.info("Deribit WS subscription request: %d channels", len(channels))
        return request_id

    async def _handle_ws_message(self, msg: dict):
        """Handle incoming WS message (JSON-RPC notification)."""
        request_id = msg.get("id")
        pending_channels = self._pending_ticker_channels.pop(
            request_id, set()
        )

        if msg.get("error"):
            error = msg.get("error")
            self._ws_subscription_error_count += 1
            self.last_error = f"ws_rpc_error:{error}"
            self.last_error_ts = time.time()
            self._ws_subscription_retry_event.set()
            log.warning("Deribit WS RPC error: %s", error)
            return

        if pending_channels:
            result = msg.get("result")
            result_channels = (
                result
                if isinstance(result, list)
                else result.get("channels", [])
                if isinstance(result, dict)
                else []
            )
            acknowledged = set(result_channels).intersection(pending_channels)
            self._subscribed_ticker_channels.update(acknowledged)
            missing_ack = pending_channels - acknowledged
            if missing_ack:
                self.last_error = (
                    f"ws_subscription_partial_ack:{len(missing_ack)}"
                )
                self.last_error_ts = time.time()
                self._ws_subscription_retry_event.set()
            return

        # Deribit WS sends notifications with method="subscription"
        method = msg.get("method")
        if method != "subscription":
            return

        params = msg.get("params", {})
        channel = params.get("channel", "")
        data = params.get("data", {})

        if channel == "deribit_price_index.btc_usd":
            price = data.get("price")
            if price:
                self._spot_price = float(price)
                self.health.update(
                    latency_ms=self.health.latency_ms,
                    ws_connected=True,
                )
            return

        if channel.startswith("ticker.") and channel.endswith(
            f".{_WS_TICKER_INTERVAL}"
        ):
            if not isinstance(data, dict):
                return
            instrument_name = str(data.get("instrument_name", ""))
            if not instrument_name:
                instrument_name = channel[len("ticker."):].rsplit(".", 1)[0]
            if not instrument_name.startswith("BTC-"):
                return

            ticker = dict(data)
            ticker["instrument_name"] = instrument_name
            now = time.time()
            self._ticker_cache_by_instrument[instrument_name] = ticker
            self._tickers_cache_ts = now
            self._ws_last_ticker_ts = now
            self._ws_ticker_message_count += 1
            self._ticker_cache_ready.set()
            self.last_success_ts = now
            self.last_error = ""
            self.disabled_reason = ""

            exchange_timestamp = ticker.get("timestamp")
            latency_ms = self.health.latency_ms
            try:
                if exchange_timestamp:
                    latency_ms = max(
                        0.0,
                        (now - float(exchange_timestamp) / 1000.0) * 1000.0,
                    )
            except (TypeError, ValueError):
                pass
            self.health.update(latency_ms=latency_ms, ws_connected=True)

    def get_diagnostics(self) -> dict:
        """Return explicit diagnostics for Deribit status."""
        return {
            "deribit_enabled_config": self.enabled_config,
            "deribit_adapter_initialized": self.initialized,
            "deribit_fetch_attempted": self.fetch_attempted,
            "deribit_fetch_attempt_count": self.request_count,
            "deribit_fetch_success_count": self.success_count,
            "deribit_fetch_error_count": self.error_count,
            "deribit_last_fetch_ts": self.last_success_ts if self.last_success_ts > self.last_error_ts else self.last_error_ts,
            "deribit_last_success_ts": self.last_success_ts,
            "deribit_last_error_ts": self.last_error_ts,
            "deribit_last_error": self.last_error,
            "deribit_disabled_reason": self.disabled_reason,
            "deribit_data_transport": "websocket_ticker_cache",
            "deribit_ws_instruments_count": self._ws_instruments_count,
            "deribit_instrument_cache_count": len(self._instruments_cache),
            "deribit_instrument_cache_age_sec": (
                round(time.time() - self._instruments_cache_ts, 3)
                if self._instruments_cache_ts > 0
                else None
            ),
            "deribit_ws_subscribed_tickers": len(self._subscribed_ticker_channels),
            "deribit_ws_pending_subscription_requests": len(
                self._pending_ticker_channels
            ),
            "deribit_ws_pending_tickers": sum(
                len(channels)
                for channels in self._pending_ticker_channels.values()
            ),
            "deribit_ws_cached_tickers": len(self._ticker_cache_by_instrument),
            "deribit_ws_cache_coverage_ratio": round(
                len(self._ticker_cache_by_instrument) / self._ws_instruments_count,
                6,
            ) if self._ws_instruments_count > 0 else 0.0,
            "deribit_ws_min_cache_coverage_ratio": _WS_MIN_CACHE_COVERAGE_RATIO,
            "deribit_ws_ticker_message_count": self._ws_ticker_message_count,
            "deribit_ws_subscription_error_count": self._ws_subscription_error_count,
            "deribit_ws_last_ticker_ts": self._ws_last_ticker_ts,
            "deribit_ws_last_subscription_refresh_ts": self._ws_last_subscription_refresh_ts,
            "deribit_ws_cache_age_sec": (
                round(time.time() - self._tickers_cache_ts, 3)
                if self._tickers_cache_ts > 0
                else None
            ),
        }
