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
        self._tickers_cache_ts: float = 0.0
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
        result = await self._rpc_get("get_instruments", {
            "currency": "BTC",
            "kind": "option",
            "expired": "false",
        })
        if result is None:
            return []
        return result if isinstance(result, list) else []

    async def fetch_option_tickers(self) -> list[dict]:
        """Fetch book summaries for all BTC options.

        Uses get_book_summary_by_currency which returns OI, volume,
        mark_iv, greeks, prices for all instruments in one call.
        """
        result = await self._rpc_get("get_book_summary_by_currency", {
            "currency": "BTC",
            "kind": "option",
        })
        if result is None:
            # Fallback to cache if valid
            now = time.time()
            if self._tickers_cache and (now - self._tickers_cache_ts) <= 180:
                log.warning("Deribit fetch_option_tickers failed, using cache (age: %.1fs)", now - self._tickers_cache_ts)
                return self._tickers_cache
            return []

        tickers = result if isinstance(result, list) else []
        self._tickers_cache = tickers
        self._tickers_cache_ts = time.time()

        # Compute aggregates
        total_oi = 0.0
        total_vol = 0.0
        for t in tickers:
            total_oi += float(t.get("open_interest", 0) or 0)
            total_vol += float(t.get("volume_24h", t.get("volume", 0)) or 0)
        self._total_oi = total_oi
        self._total_volume = total_vol

        return tickers

    def get_cached_tickers(self) -> list[dict]:
        """Return cached tickers if available, regardless of age."""
        return self._tickers_cache or []

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
                    log.info("Deribit WS connected")

                    await self._ws_subscribe(ws, [
                        "deribit_price_index.btc_usd",
                    ])

                    async for message in ws:
                        if not self._running:
                            break
                        try:
                            msg = json.loads(message)
                            await self._handle_ws_message(msg)
                        except Exception as e:
                            log.debug("Deribit WS message error: %s", e)

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


    async def _ws_subscribe(self, ws, channels: list[str]):
        """Send JSON-RPC subscribe request."""
        msg = json.dumps({
            "jsonrpc": "2.0",
            "method": "public/subscribe",
            "id": self._next_id(),
            "params": {
                "channels": channels,
            }
        })
        await ws.send(msg)
        log.info("Deribit WS subscribed to %s", channels)

    async def _handle_ws_message(self, msg: dict):
        """Handle incoming WS message (JSON-RPC notification)."""
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
        }
