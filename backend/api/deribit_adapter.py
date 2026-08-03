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
from pathlib import Path
from typing import Optional

from api.base_adapter import BaseExchangeAdapter
from config import (
    DERIBIT_REST_URL, DERIBIT_WS_URL,
    WS_PING_INTERVAL, WS_RECONNECT_DELAY,
)

log = logging.getLogger(__name__)

_WS_TICKER_CHANNEL_PREFIX = "incremental_ticker."
_WS_CORE_UNIVERSE_SIZE = 240
_WS_SUBSCRIBE_BATCH_SIZE = _WS_CORE_UNIVERSE_SIZE
_WS_SUBSCRIBE_BATCH_DELAY_SEC = 1.0
_WS_SUBSCRIBE_ACK_TIMEOUT_SEC = 30.0
_WS_INSTRUMENT_REFRESH_SEC = 15 * 60
_WS_INSTRUMENT_RETRY_SEC = 30
_INSTRUMENT_CACHE_TTL_SEC = 15 * 60
_INSTRUMENT_WS_FALLBACK_TIMEOUT_SEC = 20.0
_INSTRUMENT_DISK_CACHE_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "deribit_instruments_cache.json"
)
_WS_TICKER_CACHE_MAX_AGE_SEC = 5 * 60
_WS_MIN_CACHE_COVERAGE_RATIO = 0.70
_WS_TICKER_IDLE_TIMEOUT_SEC = 60.0
_WS_RECEIVE_POLL_SEC = 5.0
_WS_HEARTBEAT_TIMEOUT_SEC = 10.0
_WS_HEARTBEAT_RECHECK_SEC = 30.0
_WS_SOFT_RESUBSCRIBE_COOLDOWN_SEC = 5 * 60.0
_WS_SOFT_RESUBSCRIBE_GRACE_SEC = 90.0
_WS_SPOT_CACHE_MAX_AGE_SEC = 60.0
_TICKER_BOOTSTRAP_START_DELAY_SEC = 5.0
_TICKER_BOOTSTRAP_BATCH_SIZE = 2
_TICKER_BOOTSTRAP_BATCH_INTERVAL_SEC = 1.0
_TICKER_HEALTHY_BATCH_SIZE = 1
_TICKER_HEALTHY_BATCH_INTERVAL_SEC = 4.0
_TICKER_BOOTSTRAP_MAX_EMPTY_BATCHES = 3
_TICKER_CORE_REFRESH_AGE_SEC = 60.0
_TICKER_HEALTHY_CORE_REFRESH_AGE_SEC = 4 * 60.0
_TICKER_LOW_RATE_COVERAGE_RATIO = 0.80
_TICKER_CORE_BATCHES_PER_TAIL_BATCH = 9
_TICKER_BOOTSTRAP_IDLE_SEC = 1.0
_REST_FAST_REQUEST_TIMEOUT_SEC = 5.0
_REST_DISCOVERY_TIMEOUT_SEC = 15.0
_REST_CIRCUIT_FAILURE_THRESHOLD = 3
_REST_CIRCUIT_INITIAL_BACKOFF_SEC = 30.0
_REST_CIRCUIT_MAX_BACKOFF_SEC = 5 * 60.0


class DeribitAdapter(BaseExchangeAdapter):
    """Async adapter for Deribit public options API."""

    def __init__(
        self,
        instrument_cache_path: Optional[Path] = _INSTRUMENT_DISK_CACHE_PATH,
    ):
        super().__init__(exchange_id="deribit", quality_factor=1.50)
        self._http = httpx.AsyncClient(
            base_url=DERIBIT_REST_URL,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=15,
        )
        self._ws_task: Optional[asyncio.Task] = None
        self._ticker_bootstrap_task: Optional[asyncio.Task] = None
        self._ticker_bootstrap_instrument_names: list[str] = []
        self._tickers_cache: list[dict] = []
        self._ticker_cache_by_instrument: dict[str, dict] = {}
        self._ticker_received_ts_by_instrument: dict[str, float] = {}
        self._ws_ticker_received_ts_by_instrument: dict[str, float] = {}
        self._ticker_cache_ready = asyncio.Event()
        self._ws_subscription_retry_event = asyncio.Event()
        self._tickers_cache_ts: float = 0.0
        self._subscribed_ticker_channels: set[str] = set()
        self._pending_ticker_channels: dict[int, set[str]] = {}
        self._subscription_ack_waiters: dict[int, asyncio.Future] = {}
        self._instrument_discovery_lock = asyncio.Lock()
        self._instruments_cache: list[dict] = []
        self._instruments_cache_ts: float = 0.0
        self._instrument_cache_path = (
            Path(instrument_cache_path)
            if instrument_cache_path is not None
            else None
        )
        self._instrument_cache_source: str = "none"
        self._instrument_disk_cache_saved_ts: float = 0.0
        self._instrument_disk_cache_load_count: int = 0
        self._instrument_disk_cache_write_count: int = 0
        self._instrument_disk_cache_error_count: int = 0
        self._instrument_http_content_encoding: str = "unknown"
        self._instrument_http_download_bytes: int = 0
        self._instrument_http_decoded_bytes: int = 0
        self._instrument_http_elapsed_ms: float = 0.0
        self._instrument_ws_fallback_attempt_count: int = 0
        self._instrument_ws_fallback_success_count: int = 0
        self._instrument_ws_fallback_error_count: int = 0
        self._instrument_ws_fallback_last_error: str = ""
        self._ws_instruments_count: int = 0
        self._ws_core_instrument_names: set[str] = set()
        self._ws_ticker_message_count: int = 0
        self._ws_last_ticker_ts: float = 0.0
        self._ws_last_subscription_refresh_ts: float = 0.0
        self._ws_subscription_error_count: int = 0
        self._ws_ticker_watch_started_ts: float = 0.0
        self._ws_last_idle_reconnect_ts: float = 0.0
        self._ws_connection_started_ts: float = 0.0
        self._ws_connection_count: int = 0
        self._ws_reconnect_count: int = 0
        self._ws_idle_reconnect_count: int = 0
        self._ws_heartbeat_attempt_count: int = 0
        self._ws_heartbeat_success_count: int = 0
        self._ws_heartbeat_error_count: int = 0
        self._ws_heartbeat_last_attempt_ts: float = 0.0
        self._ws_heartbeat_last_success_ts: float = 0.0
        self._ws_heartbeat_last_rtt_ms: float = 0.0
        self._ws_heartbeat_last_error: str = ""
        self._ws_soft_resubscribe_requested: bool = False
        self._ws_soft_resubscribe_in_progress: bool = False
        self._ws_soft_resubscribe_requested_ts: float = 0.0
        self._ws_soft_resubscribe_last_ack_ts: float = 0.0
        self._ws_soft_resubscribe_baseline_ticker_count: int = 0
        self._ws_soft_resubscribe_attempt_count: int = 0
        self._ws_soft_resubscribe_success_count: int = 0
        self._ws_soft_resubscribe_error_count: int = 0
        self._ws_soft_resubscribe_last_success_ts: float = 0.0
        self._ws_soft_resubscribe_last_error: str = ""
        self._ws_refresh_loop_error_count: int = 0
        self._ws_refresh_task_running: bool = False
        self._ws_receiver_state: str = "idle"
        self._ticker_bootstrap_state: str = "idle"
        self._ticker_bootstrap_phase: str = "idle"
        self._ticker_bootstrap_target_count: int = 0
        self._ticker_bootstrap_cycle_target_count: int = 0
        self._ticker_bootstrap_request_count: int = 0
        self._ticker_bootstrap_success_count: int = 0
        self._ticker_bootstrap_error_count: int = 0
        self._ticker_bootstrap_last_error: str = ""
        self._ticker_bootstrap_last_success_ts: float = 0.0
        self._ticker_bootstrap_core_request_count: int = 0
        self._ticker_bootstrap_tail_request_count: int = 0
        self._ticker_bootstrap_recovery_request_count: int = 0
        self._ticker_bootstrap_low_rate_request_count: int = 0
        self._ticker_bootstrap_mode: str = "idle"
        self._ticker_bootstrap_current_interval_sec: float = 0.0
        self._ticker_bootstrap_current_batch_size: int = 0
        self._ticker_bootstrap_last_request_ts: float = 0.0
        self._ticker_bootstrap_started_ts: float = 0.0
        self._ticker_bootstrap_completed_ts: float = 0.0
        self._rest_circuit_consecutive_failures: int = 0
        self._rest_circuit_open_until_ts: float = 0.0
        self._rest_circuit_backoff_level: int = 0
        self._rest_circuit_open_count: int = 0
        self._rest_circuit_skip_count: int = 0
        self._rest_circuit_probe_count: int = 0
        self._rest_circuit_recovery_count: int = 0
        self._rest_circuit_probe_in_flight: bool = False
        self._rest_circuit_last_open_ts: float = 0.0
        self._rest_circuit_last_failure_ts: float = 0.0
        self._rest_circuit_last_error: str = ""
        self._spot_price: float = 0.0
        self._ws_last_spot_ts: float = 0.0
        self._spot_rest_fallback_count: int = 0
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
        self._load_instrument_cache_from_disk()

    # ── Lifecycle ────────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        self._ws_task = asyncio.create_task(self._ws_loop())
        log.info("DeribitAdapter started")

    async def stop(self) -> None:
        self._running = False
        if self._ticker_bootstrap_task:
            self._ticker_bootstrap_task.cancel()
            try:
                await self._ticker_bootstrap_task
            except asyncio.CancelledError:
                pass
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

    def _rest_circuit_state(self, now: Optional[float] = None) -> str:
        """Return closed, open, or half_open without issuing a request."""
        current_ts = time.time() if now is None else now
        if self._rest_circuit_open_until_ts <= 0:
            return "closed"
        if current_ts < self._rest_circuit_open_until_ts:
            return "open"
        return "half_open"

    def _rest_circuit_retry_after_sec(
        self,
        now: Optional[float] = None,
    ) -> float:
        current_ts = time.time() if now is None else now
        return max(0.0, self._rest_circuit_open_until_ts - current_ts)

    def _acquire_rest_request_slot(self) -> bool:
        """Permit normal calls, but allow only one probe after backoff."""
        state = self._rest_circuit_state()
        if state == "open":
            self._rest_circuit_skip_count += 1
            return False
        if state == "half_open":
            if self._rest_circuit_probe_in_flight:
                self._rest_circuit_skip_count += 1
                return False
            self._rest_circuit_probe_in_flight = True
            self._rest_circuit_probe_count += 1
        return True

    def _record_rest_success(self) -> None:
        """Close the circuit after any valid Deribit REST response."""
        recovering = (
            self._rest_circuit_open_until_ts > 0
            or self._rest_circuit_backoff_level > 0
        )
        if recovering:
            self._rest_circuit_recovery_count += 1
        self._rest_circuit_consecutive_failures = 0
        self._rest_circuit_open_until_ts = 0.0
        self._rest_circuit_backoff_level = 0
        self._rest_circuit_probe_in_flight = False
        self._rest_circuit_last_error = ""

    def _record_rest_failure(self, error: object) -> None:
        """Open the shared REST circuit after repeated transport failures."""
        now = time.time()
        error_text = str(error).strip() or type(error).__name__
        self._rest_circuit_consecutive_failures += 1
        self._rest_circuit_last_failure_ts = now
        self._rest_circuit_last_error = error_text
        was_probe = self._rest_circuit_probe_in_flight
        self._rest_circuit_probe_in_flight = False
        should_open = bool(
            self._rest_circuit_consecutive_failures
            >= _REST_CIRCUIT_FAILURE_THRESHOLD
            and (
                was_probe
                or self._rest_circuit_open_until_ts <= now
            )
        )
        if not should_open:
            return
        self._rest_circuit_backoff_level += 1
        delay = min(
            _REST_CIRCUIT_MAX_BACKOFF_SEC,
            _REST_CIRCUIT_INITIAL_BACKOFF_SEC
            * (2 ** (self._rest_circuit_backoff_level - 1)),
        )
        self._rest_circuit_open_until_ts = now + delay
        self._rest_circuit_last_open_ts = now
        self._rest_circuit_open_count += 1

    @staticmethod
    def _valid_instruments(value: object) -> list[dict]:
        """Return only structurally valid BTC option instrument records."""
        if not isinstance(value, list):
            return []
        return [
            dict(item)
            for item in value
            if isinstance(item, dict)
            and str(item.get("instrument_name", "")).startswith("BTC-")
        ]

    def _load_instrument_cache_from_disk(self) -> None:
        """Restore the last successful discovery without touching databases."""
        path = self._instrument_cache_path
        if path is None or not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            instruments = self._valid_instruments(payload.get("instruments"))
            saved_at = float(payload.get("saved_at") or 0.0)
            if not instruments or saved_at <= 0:
                raise ValueError("invalid instrument disk cache")
            self._instruments_cache = instruments
            self._instruments_cache_ts = min(saved_at, time.time())
            self._instrument_disk_cache_saved_ts = saved_at
            self._instrument_disk_cache_load_count += 1
            self._instrument_cache_source = "disk"
        except Exception as exc:
            self._instrument_disk_cache_error_count += 1
            log.warning("Deribit instrument disk cache ignored: %s", exc)

    def _write_instrument_cache_file(self, instruments: list[dict]) -> None:
        """Atomically persist the latest complete discovery response."""
        path = self._instrument_cache_path
        if path is None:
            return
        saved_at = time.time()
        payload = {
            "saved_at": saved_at,
            "currency": "BTC",
            "kind": "option",
            "instruments": instruments,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_suffix(path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps(payload, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary_path.replace(path)
        self._instrument_disk_cache_saved_ts = saved_at
        self._instrument_disk_cache_write_count += 1

    async def _persist_instrument_cache(self, instruments: list[dict]) -> None:
        """Persist discovery off the event loop; failure never blocks collection."""
        if self._instrument_cache_path is None:
            return
        try:
            await asyncio.to_thread(
                self._write_instrument_cache_file,
                list(instruments),
            )
        except Exception as exc:
            self._instrument_disk_cache_error_count += 1
            log.warning("Deribit instrument disk cache write failed: %s", exc)

    async def _fetch_instruments_ws_fallback(self) -> list[dict]:
        """Use one compressed WebSocket RPC when REST discovery cannot finish."""
        self._instrument_ws_fallback_attempt_count += 1
        request_id = self._next_id()
        try:
            async with websockets.connect(
                DERIBIT_WS_URL,
                ping_interval=None,
                ping_timeout=None,
                close_timeout=5,
                open_timeout=10,
                compression="deflate",
            ) as ws:
                await ws.send(json.dumps({
                    "jsonrpc": "2.0",
                    "method": "public/get_instruments",
                    "id": request_id,
                    "params": {
                        "currency": "BTC",
                        "kind": "option",
                        "expired": False,
                    },
                }))
                raw_message = await asyncio.wait_for(
                    ws.recv(),
                    timeout=_INSTRUMENT_WS_FALLBACK_TIMEOUT_SEC,
                )
            message = json.loads(raw_message)
            if message.get("id") != request_id or message.get("error"):
                raise RuntimeError(
                    f"instrument_ws_rpc_error:{message.get('error')}"
                )
            instruments = self._valid_instruments(message.get("result"))
            if not instruments:
                raise RuntimeError("instrument_ws_rpc_empty")
            self._instrument_ws_fallback_success_count += 1
            self._instrument_ws_fallback_last_error = ""
            return instruments
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._instrument_ws_fallback_error_count += 1
            self._instrument_ws_fallback_last_error = str(exc)
            log.warning("Deribit instrument WebSocket fallback failed: %s", exc)
            return []

    async def _rpc_get(
        self,
        method: str,
        params: dict,
        max_retries: int = 3,
    ) -> Optional[object]:
        """Deribit REST uses JSON-RPC style but via standard GET with query params."""
        self.fetch_attempted = True
        for attempt in range(max_retries):
            if not self._acquire_rest_request_slot():
                return None
            self.request_count += 1
            try:
                t0 = time.time()
                timeout_sec = (
                    _REST_DISCOVERY_TIMEOUT_SEC
                    if method == "get_instruments"
                    else _REST_FAST_REQUEST_TIMEOUT_SEC
                )
                resp = await self._http.get(
                    f"/public/{method}",
                    params=params,
                    timeout=timeout_sec,
                )
                latency = (time.time() - t0) * 1000
                resp.raise_for_status()
                if method == "get_instruments":
                    self._instrument_http_content_encoding = (
                        resp.headers.get("content-encoding") or "identity"
                    )
                    self._instrument_http_download_bytes = int(
                        resp.num_bytes_downloaded
                    )
                    self._instrument_http_decoded_bytes = len(resp.content)
                    self._instrument_http_elapsed_ms = latency
                data = resp.json()
                if "result" not in data:
                    error = data.get("error", {})
                    err_msg = error.get("message", "unknown")
                    self.last_error = f"API Error: {err_msg}"
                    self.last_error_ts = time.time()
                    self.error_count += 1
                    # A valid JSON-RPC error proves that the REST transport is
                    # reachable.  It must not trip the network-outage circuit.
                    self._record_rest_success()
                    log.error("Deribit API error: %s", err_msg)
                    return None

                self.success_count += 1
                self.last_success_ts = time.time()
                self._record_rest_success()
                self.health.update(latency_ms=latency, ws_connected=self.health.ws_connected)
                return data["result"]
            except Exception as e:
                error_text = str(e).strip() or type(e).__name__
                self.error_count += 1
                self.last_error = error_text
                self.last_error_ts = time.time()
                self._record_rest_failure(error_text)
                log.error(
                    "Deribit REST error (%s) attempt %d: %s",
                    method,
                    attempt + 1,
                    error_text,
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(1.0 * (2 ** attempt))
                else:
                    self.health.mark_error(error_text)
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
            instruments = self._valid_instruments(result)
            source = "rest_compressed" if (
                self._instrument_http_content_encoding != "identity"
            ) else "rest"
            if not instruments and self._instruments_cache:
                self._instrument_cache_source = "stale_cache"
                log.warning(
                    "Deribit instrument discovery failed; using cached set (%d)",
                    len(self._instruments_cache),
                )
                return list(self._instruments_cache)

            if not instruments:
                instruments = await self._fetch_instruments_ws_fallback()
                source = "websocket_rpc"

            if instruments:
                self._instruments_cache = instruments
                self._instruments_cache_ts = time.time()
                self._instrument_cache_source = source
                await self._persist_instrument_cache(instruments)
                self.last_error = ""
                return list(self._instruments_cache)

            return []

    async def fetch_option_tickers(self) -> list[dict]:
        """Return a snapshot of the live Deribit WebSocket ticker cache.

        The previous bulk REST request could block for nearly a minute on the
        collector host.  The hot MOS poll path is now cache-only and therefore
        remains non-blocking.  REST runs only in background discovery and
        adaptive ticker recovery; this method never waits for network work.
        """
        tickers = self._fresh_tickers()
        if not tickers:
            if self._ticker_cache_by_instrument:
                self.disabled_reason = "deribit_ws_ticker_cache_stale"
            return []

        if not self._has_sufficient_ticker_coverage():
            self.disabled_reason = "deribit_ws_ticker_cache_warming"
            return []

        now = time.time()
        cache_age = now - self._tickers_cache_ts
        if cache_age > _WS_TICKER_CACHE_MAX_AGE_SEC:
            self.disabled_reason = "deribit_ws_ticker_cache_stale"
            return []

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
        """Return only fresh cached tickers; stale data is never reused."""
        return self._fresh_tickers()

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
        """Require safe coverage of the balanced research-core universe."""
        return self._core_ticker_coverage_ratio() >= _WS_MIN_CACHE_COVERAGE_RATIO

    def _core_ticker_coverage_ratio(self) -> float:
        """Return fresh complete coverage for the balanced research core."""
        fresh_names = self._fresh_ticker_names()
        target_names = self._ws_core_instrument_names
        cached_count = (
            sum(
                self._ticker_has_full_option_data(name)
                for name in fresh_names.intersection(target_names)
            )
            if target_names
            else len(fresh_names)
        )
        if cached_count == 0:
            return 0.0
        target_count = len(target_names) or self._ws_instruments_count
        if target_count <= 0:
            return 1.0
        return cached_count / target_count

    def _is_ws_ticker_transport_healthy(self) -> bool:
        """Require a live receiver and the complete core subscription set."""
        core_count = len(self._ws_core_instrument_names)
        ticker_liveness_qualified = bool(
            not self._is_ws_ticker_stream_idle()
            or (
                self._ws_heartbeat_last_success_ts > 0
                and time.time() - self._ws_heartbeat_last_success_ts
                <= _WS_HEARTBEAT_RECHECK_SEC
            )
        )
        return bool(
            core_count > 0
            and self.health.ws_connected
            and self._ws_receiver_state == "receiving"
            and len(self._subscribed_ticker_channels) >= core_count
            and ticker_liveness_qualified
        )

    def _fresh_ticker_names(self) -> set[str]:
        """Return instruments refreshed within one structural snapshot interval."""
        now = time.time()
        return {
            instrument_name
            for instrument_name in self._ticker_cache_by_instrument
            if now - self._ticker_received_ts_by_instrument.get(
                instrument_name, 0.0
            ) <= _WS_TICKER_CACHE_MAX_AGE_SEC
        }

    def _fresh_tickers(self) -> list[dict]:
        """Return per-instrument snapshots refreshed within the safe window."""
        fresh_names = self._fresh_ticker_names()
        return [
            ticker
            for instrument_name, ticker in self._ticker_cache_by_instrument.items()
            if instrument_name in fresh_names
        ]

    async def fetch_spot_price(self) -> Optional[float]:
        """Prefer the live index subscription and use REST only if it is stale."""
        if (
            self._spot_price > 0
            and time.time() - self._ws_last_spot_ts
            <= _WS_SPOT_CACHE_MAX_AGE_SEC
        ):
            return self._spot_price

        self._spot_rest_fallback_count += 1
        result = await self._rpc_get("get_index_price", {
            "index_name": "btc_usd",
        }, max_retries=1)
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
                    self._ws_connection_count += 1
                    self._ws_connection_started_ts = time.time()
                    self._ws_ticker_watch_started_ts = 0.0
                    self._ws_heartbeat_last_attempt_ts = 0.0
                    self._ws_heartbeat_last_success_ts = 0.0
                    self._ws_soft_resubscribe_requested = False
                    self._ws_soft_resubscribe_in_progress = False
                    self._ws_soft_resubscribe_requested_ts = 0.0
                    self._ws_soft_resubscribe_last_ack_ts = 0.0
                    self._ws_receiver_state = "receiving"
                    self._subscribed_ticker_channels.clear()
                    self._pending_ticker_channels.clear()
                    for waiter in self._subscription_ack_waiters.values():
                        if not waiter.done():
                            waiter.cancel()
                    self._subscription_ack_waiters.clear()
                    self._ws_subscription_retry_event.clear()
                    log.info("Deribit WS connected")

                    await self._ws_subscribe(ws, [
                        "deribit_price_index.btc_usd",
                    ])
                    await self._supervise_ws_connection(ws)

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("Deribit WS error: %s", e)
                self._ws_receiver_state = "reconnecting"
                self.health.ws_connected = False
                self.health.mark_error(str(e))

            if self._running:
                attempt += 1
                self._ws_reconnect_count += 1
                log.info("Deribit WS reconnecting in %.0fs (attempt %d)...",
                         delay, attempt)
                await asyncio.sleep(delay)

        self.health.ws_connected = False
        self._ws_receiver_state = "stopped"

    async def _supervise_ws_connection(self, ws) -> None:
        """Reconnect when either receiving or subscription maintenance stops."""
        receive_task = asyncio.create_task(self._ws_receive_loop(ws))
        refresh_task = asyncio.create_task(
            self._ws_subscription_refresh_loop(ws)
        )
        tasks = (receive_task, refresh_task)
        done = set()
        try:
            done, _ = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        for task in done:
            task.result()
        if self._running:
            raise RuntimeError("deribit_ws_background_task_stopped")

    def _is_ws_ticker_stream_idle(self, now: Optional[float] = None) -> bool:
        """Return whether confirmed ticker subscriptions stopped producing."""
        if not self._subscribed_ticker_channels:
            return False
        reference_ts = max(
            self._ws_ticker_watch_started_ts,
            self._ws_last_ticker_ts,
        )
        if reference_ts <= 0:
            return False
        current_ts = time.time() if now is None else now
        return current_ts - reference_ts >= _WS_TICKER_IDLE_TIMEOUT_SEC

    async def _ws_protocol_heartbeat(self, ws) -> bool:
        """Qualify socket liveness without adding Deribit API requests."""
        self._ws_heartbeat_attempt_count += 1
        self._ws_heartbeat_last_attempt_ts = time.time()
        started = time.perf_counter()
        try:
            pong_waiter = await ws.ping()
            await asyncio.wait_for(
                pong_waiter,
                timeout=_WS_HEARTBEAT_TIMEOUT_SEC,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error_text = str(exc).strip() or type(exc).__name__
            self._ws_heartbeat_error_count += 1
            self._ws_heartbeat_last_error = error_text
            return False

        self._ws_heartbeat_success_count += 1
        self._ws_heartbeat_last_success_ts = time.time()
        self._ws_heartbeat_last_rtt_ms = (
            time.perf_counter() - started
        ) * 1000.0
        self._ws_heartbeat_last_error = ""
        return True

    def _request_ws_soft_resubscribe(self, now: Optional[float] = None) -> None:
        """Ask the refresh task to rebuild core subscriptions in-place."""
        current_ts = time.time() if now is None else now
        self._ws_soft_resubscribe_requested = True
        self._ws_soft_resubscribe_in_progress = True
        self._ws_soft_resubscribe_requested_ts = current_ts
        self._ws_soft_resubscribe_last_ack_ts = 0.0
        self._ws_soft_resubscribe_baseline_ticker_count = (
            self._ws_ticker_message_count
        )
        self._ws_soft_resubscribe_attempt_count += 1
        self._ws_subscription_retry_event.set()

    async def _qualify_ws_ticker_idle(self, ws) -> None:
        """Use heartbeat and in-place resubscription before reconnecting."""
        now = time.time()
        if not self._is_ws_ticker_stream_idle(now=now):
            return

        if self._ws_soft_resubscribe_in_progress:
            recovery_age = now - self._ws_soft_resubscribe_requested_ts
            if recovery_age < _WS_SOFT_RESUBSCRIBE_GRACE_SEC:
                return
            self._ws_soft_resubscribe_error_count += 1
            self._ws_soft_resubscribe_last_error = (
                "ticker_snapshot_grace_timeout"
            )
            self._ws_idle_reconnect_count += 1
            self._ws_last_idle_reconnect_ts = now
            raise RuntimeError(
                "deribit_ws_ticker_recovery_timeout:"
                f">{_WS_SOFT_RESUBSCRIBE_GRACE_SEC:.0f}s"
            )

        heartbeat_age = now - self._ws_heartbeat_last_attempt_ts
        if (
            self._ws_heartbeat_last_attempt_ts > 0
            and heartbeat_age < _WS_HEARTBEAT_RECHECK_SEC
        ):
            return

        if not await self._ws_protocol_heartbeat(ws):
            self._ws_idle_reconnect_count += 1
            self._ws_last_idle_reconnect_ts = time.time()
            raise RuntimeError("deribit_ws_heartbeat_timeout")

        resubscribe_age = now - self._ws_soft_resubscribe_requested_ts
        if (
            self._ws_soft_resubscribe_requested_ts <= 0
            or resubscribe_age >= _WS_SOFT_RESUBSCRIBE_COOLDOWN_SEC
        ):
            self._request_ws_soft_resubscribe(now=now)

    async def _ws_receive_loop(self, ws) -> None:
        """Receive messages and qualify ticker silence before reconnecting."""
        while self._running:
            try:
                message = await asyncio.wait_for(
                    ws.recv(),
                    timeout=_WS_RECEIVE_POLL_SEC,
                )
            except asyncio.TimeoutError:
                if self._is_ws_ticker_stream_idle():
                    await self._qualify_ws_ticker_idle(ws)
                continue

            try:
                msg = json.loads(message)
                await self._handle_ws_message(msg)
            except Exception as exc:
                log.debug("Deribit WS message error: %s", exc)

    async def _ws_subscription_refresh_loop(self, ws) -> None:
        """Discover new option instruments and subscribe without blocking reads."""
        self._ws_refresh_task_running = True
        try:
            while self._running:
                try:
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
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._ws_refresh_loop_error_count += 1
                    self.last_error = f"ws_refresh_loop_error:{exc}"
                    self.last_error_ts = time.time()
                    raise
        finally:
            self._ws_refresh_task_running = False

    def _select_core_instrument_names(
        self,
        instruments: list[dict],
    ) -> list[str]:
        """Build a bounded ATM-focused universe distributed across expiries."""
        records = []
        strikes = []
        for item in instruments:
            instrument_name = str(item.get("instrument_name", ""))
            if not instrument_name.startswith("BTC-"):
                continue
            try:
                strike = float(item.get("strike") or 0.0)
            except (TypeError, ValueError):
                strike = 0.0
            try:
                expiry = int(item.get("expiration_timestamp") or 0)
            except (TypeError, ValueError):
                expiry = 0
            if strike > 0:
                strikes.append(strike)
            records.append((instrument_name, expiry, strike))

        if not records:
            return []
        target_count = min(_WS_CORE_UNIVERSE_SIZE, len(records))
        if len(records) <= target_count:
            return sorted(name for name, _, _ in records)

        if self._spot_price > 0:
            reference_spot = self._spot_price
        elif strikes:
            ordered_strikes = sorted(strikes)
            reference_spot = ordered_strikes[len(ordered_strikes) // 2]
        else:
            reference_spot = 0.0

        by_expiry: dict[int, list[tuple[str, float]]] = {}
        for instrument_name, expiry, strike in records:
            distance = (
                abs(strike - reference_spot) / reference_spot
                if reference_spot > 0 and strike > 0
                else float("inf")
            )
            by_expiry.setdefault(expiry, []).append(
                (instrument_name, distance)
            )
        for expiry_records in by_expiry.values():
            expiry_records.sort(key=lambda record: (record[1], record[0]))

        selected = []
        expiry_keys = sorted(by_expiry)
        depth = 0
        while len(selected) < target_count:
            added = False
            for expiry in expiry_keys:
                expiry_records = by_expiry[expiry]
                if depth < len(expiry_records):
                    selected.append(expiry_records[depth][0])
                    added = True
                    if len(selected) >= target_count:
                        break
            if not added:
                break
            depth += 1
        return selected

    async def _refresh_ws_option_subscriptions(self, ws) -> bool:
        """Subscribe to a balanced core and bootstrap the full chain."""
        force_core_resubscribe = self._ws_soft_resubscribe_requested
        if force_core_resubscribe:
            self._ws_soft_resubscribe_requested = False
        try:
            instruments = await asyncio.wait_for(
                self.fetch_instruments(), timeout=40.0
            )
        except asyncio.TimeoutError:
            self.last_error = "instrument_discovery_timeout>40s"
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
        core_instrument_names = self._select_core_instrument_names(instruments)
        self._ws_core_instrument_names = set(core_instrument_names)
        prioritized_instrument_names = core_instrument_names + [
            name
            for name in instrument_names
            if name not in self._ws_core_instrument_names
        ]
        self._ensure_ticker_bootstrap(prioritized_instrument_names)
        active_names = set(instrument_names)
        expired_names = set(self._ticker_cache_by_instrument) - active_names
        for instrument_name in expired_names:
            self._ticker_cache_by_instrument.pop(instrument_name, None)
            self._ticker_received_ts_by_instrument.pop(instrument_name, None)
            self._ws_ticker_received_ts_by_instrument.pop(
                instrument_name,
                None,
            )

        desired_channels = {
            f"{_WS_TICKER_CHANNEL_PREFIX}{name}"
            for name in core_instrument_names
        }
        if force_core_resubscribe:
            active_core_channels = sorted(
                desired_channels.intersection(
                    self._subscribed_ticker_channels
                )
            )
            for offset in range(
                0,
                len(active_core_channels),
                _WS_SUBSCRIBE_BATCH_SIZE,
            ):
                await self._ws_unsubscribe(
                    ws,
                    active_core_channels[
                        offset:offset + _WS_SUBSCRIBE_BATCH_SIZE
                    ],
                )
        obsolete_channels = sorted(
            self._subscribed_ticker_channels - desired_channels
        )
        for offset in range(
            0, len(obsolete_channels), _WS_SUBSCRIBE_BATCH_SIZE
        ):
            await self._ws_unsubscribe(
                ws,
                obsolete_channels[offset:offset + _WS_SUBSCRIBE_BATCH_SIZE],
            )
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
        self._ws_last_subscription_refresh_ts = time.time()
        for batch_index, batch in enumerate(batches):
            if batch_index > 0:
                await asyncio.sleep(_WS_SUBSCRIBE_BATCH_DELAY_SEC)
            request_id = await self._ws_subscribe(
                ws,
                batch,
                track_ticker_channels=True,
            )
            if request_id is None or not await self._wait_for_subscription_ack(
                request_id,
                timeout=_WS_SUBSCRIBE_ACK_TIMEOUT_SEC,
            ):
                if force_core_resubscribe:
                    self._ws_soft_resubscribe_error_count += 1
                    self._ws_soft_resubscribe_last_error = (
                        self.last_error or "subscription_ack_failed"
                    )
                return False

        if force_core_resubscribe:
            self._ws_soft_resubscribe_last_ack_ts = time.time()

        if missing_channels:
            log.info(
                "Deribit WS option subscriptions added=%d total=%d",
                len(missing_channels),
                len(desired_channels),
            )
        return True

    def _ticker_has_full_option_data(self, instrument_name: str) -> bool:
        """Return whether the cache has the fields required by Particle Logic."""
        ticker = self._ticker_cache_by_instrument.get(instrument_name)
        if not isinstance(ticker, dict):
            return False
        greeks = ticker.get("greeks")
        if not isinstance(greeks, dict):
            return False
        return (
            ticker.get("mark_iv") is not None
            and all(
                greeks.get(key) is not None
                for key in ("delta", "gamma", "vega", "theta")
            )
        )

    def _ticker_has_fresh_full_option_data(self, instrument_name: str) -> bool:
        """Return whether a complete ticker is still valid for aggregation."""
        if not self._ticker_has_full_option_data(instrument_name):
            return False
        return (
            time.time() - self._ticker_received_ts_by_instrument.get(
                instrument_name, 0.0
            )
            <= _WS_TICKER_CACHE_MAX_AGE_SEC
        )

    def _ensure_ticker_bootstrap(self, instrument_names: list[str]) -> None:
        """Keep one rate-limited core-first refresh scheduler running."""
        self._ticker_bootstrap_instrument_names = list(dict.fromkeys(
            instrument_names
        ))
        if (
            self._ticker_bootstrap_task is not None
            and not self._ticker_bootstrap_task.done()
        ):
            return
        self._ticker_bootstrap_task = asyncio.create_task(
            self._bootstrap_full_tickers(
                list(self._ticker_bootstrap_instrument_names)
            )
        )

    def _bootstrap_refresh_baselines(
        self,
        instrument_names: list[str],
    ) -> dict[str, float]:
        """Freeze only missing or stale tickers as targets for one cycle."""
        return {
            name: self._ticker_received_ts_by_instrument.get(name, 0.0)
            for name in instrument_names
            if not self._ticker_has_fresh_full_option_data(name)
        }

    def _next_ticker_refresh_batch(
        self,
        instrument_names: list[str],
        cursor: int,
        *,
        max_age_sec: float,
        batch_size: int = _TICKER_BOOTSTRAP_BATCH_SIZE,
    ) -> tuple[list[str], int]:
        """Select one fair round-robin batch of missing or aging tickers."""
        if not instrument_names:
            return [], 0

        batch = []
        index = cursor % len(instrument_names)
        now = time.time()
        for _ in range(len(instrument_names)):
            instrument_name = instrument_names[index]
            index = (index + 1) % len(instrument_names)
            received_ts = self._ticker_received_ts_by_instrument.get(
                instrument_name, 0.0
            )
            if (
                not self._ticker_has_full_option_data(instrument_name)
                or now - received_ts >= max_age_sec
            ):
                batch.append(instrument_name)
                if len(batch) >= batch_size:
                    break
        return batch, index

    async def _bootstrap_full_tickers(
        self,
        instrument_names: list[str],
    ) -> None:
        """Warm quickly, then use REST only as a low-rate WS safety net."""
        self._ticker_bootstrap_instrument_names = list(dict.fromkeys(
            instrument_names
        ))
        self._ticker_bootstrap_state = "running"
        self._ticker_bootstrap_phase = "starting"
        self._ticker_bootstrap_target_count = len(
            self._ticker_bootstrap_instrument_names
        )
        self._ticker_bootstrap_started_ts = time.time()
        self._ticker_bootstrap_completed_ts = 0.0
        self._ticker_bootstrap_last_error = ""
        self._ticker_bootstrap_cycle_target_count = 0

        try:
            await asyncio.sleep(_TICKER_BOOTSTRAP_START_DELAY_SEC)
            core_cursor = 0
            tail_cursor = 0
            core_batches_since_tail = 0
            empty_batches = 0

            while self._running:
                active_names = list(self._ticker_bootstrap_instrument_names)
                rest_circuit_state = self._rest_circuit_state()
                if rest_circuit_state == "open":
                    retry_after_sec = self._rest_circuit_retry_after_sec()
                    self._ticker_bootstrap_mode = "network_backoff"
                    self._ticker_bootstrap_phase = "rest_circuit_open"
                    self._ticker_bootstrap_current_batch_size = 0
                    self._ticker_bootstrap_current_interval_sec = round(
                        retry_after_sec,
                        3,
                    )
                    await asyncio.sleep(min(max(retry_after_sec, 0.1), 30.0))
                    continue
                core_set = self._ws_core_instrument_names
                core_names = [
                    name for name in active_names if name in core_set
                ]
                tail_names = [
                    name for name in active_names if name not in core_set
                ]
                self._ticker_bootstrap_target_count = len(active_names)
                self._ticker_bootstrap_cycle_target_count = sum(
                    not self._ticker_has_fresh_full_option_data(name)
                    for name in active_names
                )

                core_coverage_ratio = self._core_ticker_coverage_ratio()
                core_ready = (
                    core_coverage_ratio >= _WS_MIN_CACHE_COVERAGE_RATIO
                )
                low_rate_mode = bool(
                    core_coverage_ratio >= _TICKER_LOW_RATE_COVERAGE_RATIO
                    and self._is_ws_ticker_transport_healthy()
                )
                self._ticker_bootstrap_mode = (
                    "healthy_low_rate" if low_rate_mode else "warmup_recovery"
                )
                batch_size = (
                    _TICKER_HEALTHY_BATCH_SIZE
                    if low_rate_mode
                    else _TICKER_BOOTSTRAP_BATCH_SIZE
                )
                batch_interval_sec = (
                    _TICKER_HEALTHY_BATCH_INTERVAL_SEC
                    if low_rate_mode
                    else _TICKER_BOOTSTRAP_BATCH_INTERVAL_SEC
                )
                if rest_circuit_state == "half_open":
                    self._ticker_bootstrap_mode = "network_probe"
                    batch_size = 1
                core_refresh_age_sec = (
                    _TICKER_HEALTHY_CORE_REFRESH_AGE_SEC
                    if low_rate_mode
                    else _TICKER_CORE_REFRESH_AGE_SEC
                )
                self._ticker_bootstrap_current_batch_size = batch_size
                self._ticker_bootstrap_current_interval_sec = (
                    batch_interval_sec
                )
                prefer_tail = (
                    low_rate_mode
                    and core_batches_since_tail
                    >= _TICKER_CORE_BATCHES_PER_TAIL_BATCH
                )
                if not low_rate_mode:
                    core_batches_since_tail = 0
                batch: list[str] = []
                lane = "core"

                if prefer_tail:
                    batch, tail_cursor = self._next_ticker_refresh_batch(
                        tail_names,
                        tail_cursor,
                        max_age_sec=_WS_TICKER_CACHE_MAX_AGE_SEC,
                        batch_size=batch_size,
                    )
                    if batch:
                        lane = "tail"
                        core_batches_since_tail = 0

                if not batch:
                    batch, core_cursor = self._next_ticker_refresh_batch(
                        core_names,
                        core_cursor,
                        max_age_sec=core_refresh_age_sec,
                        batch_size=batch_size,
                    )
                    if batch:
                        lane = "core"
                        if low_rate_mode:
                            core_batches_since_tail += 1

                if not batch and not prefer_tail and low_rate_mode:
                    batch, tail_cursor = self._next_ticker_refresh_batch(
                        tail_names,
                        tail_cursor,
                        max_age_sec=_WS_TICKER_CACHE_MAX_AGE_SEC,
                        batch_size=batch_size,
                    )
                    if batch:
                        lane = "tail"
                        core_batches_since_tail = 0

                if not batch:
                    self._ticker_bootstrap_phase = (
                        "maintaining_core"
                        if low_rate_mode
                        else "recovering_core"
                        if core_ready
                        else "warming_core"
                    )
                    await asyncio.sleep(_TICKER_BOOTSTRAP_IDLE_SEC)
                    continue

                self._ticker_bootstrap_phase = (
                    "warming_core"
                    if not core_ready
                    else "recovering_core"
                    if not low_rate_mode
                    else "backfilling_chain"
                    if lane == "tail"
                    else "maintaining_core"
                )
                if lane == "core":
                    self._ticker_bootstrap_core_request_count += len(batch)
                else:
                    self._ticker_bootstrap_tail_request_count += len(batch)
                if low_rate_mode:
                    self._ticker_bootstrap_low_rate_request_count += len(batch)
                else:
                    self._ticker_bootstrap_recovery_request_count += len(batch)

                self._ticker_bootstrap_last_request_ts = time.time()
                successes = await self._bootstrap_ticker_rest_batch(batch)
                empty_batches = 0 if successes else empty_batches + 1
                if empty_batches >= _TICKER_BOOTSTRAP_MAX_EMPTY_BATCHES:
                    await asyncio.sleep(5.0)
                    empty_batches = 0
                await asyncio.sleep(
                    batch_interval_sec
                    * (2 if successes < len(batch) else 1)
                )
        except asyncio.CancelledError:
            self._ticker_bootstrap_state = "cancelled"
            self._ticker_bootstrap_phase = "cancelled"
            raise
        except Exception as exc:
            self._ticker_bootstrap_state = "degraded"
            self._ticker_bootstrap_phase = "failed"
            self._ticker_bootstrap_last_error = (
                f"bootstrap_scheduler_error:{exc}"
            )
            log.exception("Deribit ticker refresh scheduler failed")
        finally:
            self._ticker_bootstrap_completed_ts = time.time()

    async def _bootstrap_ticker_rest_batch(
        self,
        instrument_names: list[str],
    ) -> int:
        """Request lightweight per-contract public/ticker snapshots over REST."""
        async def fetch_one(instrument_name: str) -> bool:
            self._ticker_bootstrap_request_count += 1
            result = await self._rpc_get(
                "ticker",
                {"instrument_name": instrument_name},
                max_retries=1,
            )
            if not isinstance(result, dict):
                self._ticker_bootstrap_error_count += 1
                self._ticker_bootstrap_last_error = (
                    self.last_error or "ticker_rest_missing_result"
                )
                return False
            self._store_ticker_snapshot(
                result,
                instrument_name=instrument_name,
                stream_message=False,
            )
            self._ticker_bootstrap_success_count += 1
            self._ticker_bootstrap_last_success_ts = time.time()
            self._ticker_bootstrap_last_error = ""
            return True

        results = await asyncio.gather(*(
            fetch_one(instrument_name)
            for instrument_name in instrument_names
        ))
        return sum(results)

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
            self._subscription_ack_waiters[request_id] = (
                asyncio.get_running_loop().create_future()
            )
        try:
            await ws.send(msg)
        except Exception:
            self._pending_ticker_channels.pop(request_id, None)
            waiter = self._subscription_ack_waiters.pop(request_id, None)
            if waiter is not None and not waiter.done():
                waiter.cancel()
            raise
        log.info("Deribit WS subscription request: %d channels", len(channels))
        return request_id

    async def _ws_unsubscribe(self, ws, channels: list[str]) -> None:
        """Remove channels that left the rolling research-core universe."""
        if not channels:
            return
        request_id = self._next_id()
        await ws.send(json.dumps({
            "jsonrpc": "2.0",
            "method": "public/unsubscribe",
            "id": request_id,
            "params": {"channels": channels},
        }))
        self._subscribed_ticker_channels.difference_update(channels)
        log.info("Deribit WS option subscriptions removed=%d", len(channels))

    async def _wait_for_subscription_ack(
        self,
        request_id: int,
        *,
        timeout: float,
    ) -> bool:
        """Wait for one subscription response before sending the next batch."""
        waiter = self._subscription_ack_waiters.get(request_id)
        if waiter is None:
            return False
        try:
            return bool(await asyncio.wait_for(waiter, timeout=timeout))
        except asyncio.TimeoutError:
            pending_count = len(
                self._pending_ticker_channels.pop(request_id, set())
            )
            self.last_error = f"ws_subscription_ack_timeout:{pending_count}"
            self.last_error_ts = time.time()
            return False
        finally:
            self._subscription_ack_waiters.pop(request_id, None)

    def _store_ticker_snapshot(
        self,
        data: dict,
        *,
        instrument_name: str = "",
        stream_message: bool,
    ) -> bool:
        """Merge one full or incremental ticker into the shared cache."""
        if not isinstance(data, dict):
            return False
        instrument_name = str(
            data.get("instrument_name") or instrument_name
        )
        if not instrument_name.startswith("BTC-"):
            return False

        previous_ticker = self._ticker_cache_by_instrument.get(
            instrument_name, {}
        )
        ticker = dict(previous_ticker)
        ticker.update(data)
        for nested_key in ("stats", "greeks"):
            previous_nested = previous_ticker.get(nested_key)
            current_nested = data.get(nested_key)
            if isinstance(previous_nested, dict) and isinstance(
                current_nested, dict
            ):
                ticker[nested_key] = {
                    **previous_nested,
                    **current_nested,
                }

        ticker["instrument_name"] = instrument_name
        now = time.time()
        self._ticker_cache_by_instrument[instrument_name] = ticker
        self._ticker_received_ts_by_instrument[instrument_name] = now
        self._tickers_cache_ts = now
        if stream_message:
            self._ws_ticker_received_ts_by_instrument[instrument_name] = now
            self._ws_last_ticker_ts = now
            self._ws_ticker_message_count += 1
            if self._ws_soft_resubscribe_in_progress:
                self._ws_soft_resubscribe_in_progress = False
                self._ws_soft_resubscribe_success_count += 1
                self._ws_soft_resubscribe_last_success_ts = now
                self._ws_soft_resubscribe_last_error = ""
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
        return True

    async def _handle_ws_message(self, msg: dict):
        """Handle incoming WS message (JSON-RPC notification)."""
        request_id = msg.get("id")
        pending_channels = self._pending_ticker_channels.pop(
            request_id, set()
        )
        ack_waiter = self._subscription_ack_waiters.get(request_id)

        if msg.get("error"):
            error = msg.get("error")
            self._ws_subscription_error_count += 1
            self.last_error = f"ws_rpc_error:{error}"
            self.last_error_ts = time.time()
            self._ws_subscription_retry_event.set()
            if ack_waiter is not None and not ack_waiter.done():
                ack_waiter.set_result(False)
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
            if acknowledged and self._ws_ticker_watch_started_ts <= 0:
                self._ws_ticker_watch_started_ts = time.time()
            missing_ack = pending_channels - acknowledged
            if missing_ack:
                self.last_error = (
                    f"ws_subscription_partial_ack:{len(missing_ack)}"
                )
                self.last_error_ts = time.time()
                self._ws_subscription_retry_event.set()
            if ack_waiter is not None and not ack_waiter.done():
                ack_waiter.set_result(not missing_ack)
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
                self._ws_last_spot_ts = time.time()
                self.health.update(
                    latency_ms=self.health.latency_ms,
                    ws_connected=True,
                )
            return

        if channel.startswith(_WS_TICKER_CHANNEL_PREFIX):
            if not isinstance(data, dict):
                return
            instrument_name = str(data.get("instrument_name", ""))
            if not instrument_name:
                instrument_name = channel[len(_WS_TICKER_CHANNEL_PREFIX):]
            self._store_ticker_snapshot(
                data,
                instrument_name=instrument_name,
                stream_message=True,
            )

    def get_diagnostics(self) -> dict:
        """Return explicit diagnostics for Deribit status."""
        now = time.time()
        fresh_names = self._fresh_ticker_names()
        core_names = self._ws_core_instrument_names
        fresh_core_names = fresh_names.intersection(core_names)
        recent_ws_names = {
            name
            for name, received_ts in (
                self._ws_ticker_received_ts_by_instrument.items()
            )
            if now - received_ts <= _WS_TICKER_CACHE_MAX_AGE_SEC
        }
        recent_ws_core_names = recent_ws_names.intersection(core_names)
        full_ticker_count = sum(
            self._ticker_has_full_option_data(name)
            for name in self._ticker_cache_by_instrument
        )
        fresh_full_ticker_count = sum(
            self._ticker_has_full_option_data(name)
            for name in fresh_names
        )
        full_core_ticker_count = sum(
            self._ticker_has_full_option_data(name)
            for name in fresh_core_names
        )
        core_count = len(core_names)
        core_coverage_ratio = (
            full_core_ticker_count / core_count
            if core_count > 0
            else 0.0
        )
        core_ticker_ages = [
            now - self._ticker_received_ts_by_instrument.get(name, 0.0)
            for name in core_names
            if self._ticker_has_full_option_data(name)
        ]
        ticker_watch_reference_ts = max(
            self._ws_ticker_watch_started_ts,
            self._ws_last_ticker_ts,
        )
        ticker_idle_age_sec = (
            round(now - ticker_watch_reference_ts, 3)
            if self._subscribed_ticker_channels
            and ticker_watch_reference_ts > 0
            else None
        )
        rest_circuit_state = self._rest_circuit_state(now=now)
        rest_retry_after_sec = self._rest_circuit_retry_after_sec(now=now)
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
            "deribit_data_transport": (
                "compressed_rest_discovery+websocket_incremental_ticker_cache"
                "+circuit_broken_adaptive_rest_ticker_recovery"
            ),
            "deribit_ticker_bootstrap_transport": (
                "circuit_broken_adaptive_rest_public_ticker"
            ),
            "deribit_rest_circuit_state": rest_circuit_state,
            "deribit_rest_circuit_failure_threshold": (
                _REST_CIRCUIT_FAILURE_THRESHOLD
            ),
            "deribit_rest_circuit_consecutive_failures": (
                self._rest_circuit_consecutive_failures
            ),
            "deribit_rest_circuit_backoff_level": (
                self._rest_circuit_backoff_level
            ),
            "deribit_rest_circuit_current_backoff_sec": round(
                max(
                    0.0,
                    self._rest_circuit_open_until_ts
                    - self._rest_circuit_last_open_ts,
                ),
                3,
            ),
            "deribit_rest_circuit_retry_after_sec": round(
                rest_retry_after_sec,
                3,
            ),
            "deribit_rest_circuit_next_probe_ts": (
                self._rest_circuit_open_until_ts
            ),
            "deribit_rest_circuit_open_count": (
                self._rest_circuit_open_count
            ),
            "deribit_rest_circuit_skip_count": (
                self._rest_circuit_skip_count
            ),
            "deribit_rest_circuit_probe_count": (
                self._rest_circuit_probe_count
            ),
            "deribit_rest_circuit_recovery_count": (
                self._rest_circuit_recovery_count
            ),
            "deribit_rest_circuit_probe_in_flight": (
                self._rest_circuit_probe_in_flight
            ),
            "deribit_rest_circuit_last_open_ts": (
                self._rest_circuit_last_open_ts
            ),
            "deribit_rest_circuit_last_failure_ts": (
                self._rest_circuit_last_failure_ts
            ),
            "deribit_rest_circuit_last_error": (
                self._rest_circuit_last_error
            ),
            "deribit_rest_fast_request_timeout_sec": (
                _REST_FAST_REQUEST_TIMEOUT_SEC
            ),
            "deribit_rest_discovery_timeout_sec": (
                _REST_DISCOVERY_TIMEOUT_SEC
            ),
            "deribit_ws_instruments_count": self._ws_instruments_count,
            "deribit_ws_core_instruments_count": core_count,
            "deribit_instrument_cache_count": len(self._instruments_cache),
            "deribit_instrument_cache_age_sec": (
                round(time.time() - self._instruments_cache_ts, 3)
                if self._instruments_cache_ts > 0
                else None
            ),
            "deribit_instrument_cache_source": self._instrument_cache_source,
            "deribit_instrument_http_accept_encoding": (
                self._http.headers.get("accept-encoding", "")
            ),
            "deribit_instrument_http_content_encoding": (
                self._instrument_http_content_encoding
            ),
            "deribit_instrument_http_download_bytes": (
                self._instrument_http_download_bytes
            ),
            "deribit_instrument_http_decoded_bytes": (
                self._instrument_http_decoded_bytes
            ),
            "deribit_instrument_http_elapsed_ms": round(
                self._instrument_http_elapsed_ms,
                3,
            ),
            "deribit_instrument_disk_cache_saved_ts": (
                self._instrument_disk_cache_saved_ts
            ),
            "deribit_instrument_disk_cache_load_count": (
                self._instrument_disk_cache_load_count
            ),
            "deribit_instrument_disk_cache_write_count": (
                self._instrument_disk_cache_write_count
            ),
            "deribit_instrument_disk_cache_error_count": (
                self._instrument_disk_cache_error_count
            ),
            "deribit_instrument_ws_fallback_attempt_count": (
                self._instrument_ws_fallback_attempt_count
            ),
            "deribit_instrument_ws_fallback_success_count": (
                self._instrument_ws_fallback_success_count
            ),
            "deribit_instrument_ws_fallback_error_count": (
                self._instrument_ws_fallback_error_count
            ),
            "deribit_instrument_ws_fallback_last_error": (
                self._instrument_ws_fallback_last_error
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
            "deribit_ws_fresh_tickers": len(fresh_names),
            "deribit_ws_core_fresh_tickers": len(fresh_core_names),
            "deribit_ws_cache_coverage_ratio": round(
                core_coverage_ratio,
                6,
            ),
            "deribit_ws_chain_coverage_ratio": round(
                len(fresh_names) / self._ws_instruments_count,
                6,
            ) if self._ws_instruments_count > 0 else 0.0,
            "deribit_ws_min_cache_coverage_ratio": _WS_MIN_CACHE_COVERAGE_RATIO,
            "deribit_ws_ticker_message_count": self._ws_ticker_message_count,
            "deribit_ws_last_spot_ts": self._ws_last_spot_ts,
            "deribit_ws_spot_cache_max_age_sec": _WS_SPOT_CACHE_MAX_AGE_SEC,
            "deribit_spot_rest_fallback_count": self._spot_rest_fallback_count,
            "deribit_ws_recent_stream_tickers": len(recent_ws_names),
            "deribit_ws_recent_stream_core_tickers": len(
                recent_ws_core_names
            ),
            "deribit_ws_subscription_error_count": self._ws_subscription_error_count,
            "deribit_ws_receiver_state": self._ws_receiver_state,
            "deribit_ws_connection_count": self._ws_connection_count,
            "deribit_ws_reconnect_count": self._ws_reconnect_count,
            "deribit_ws_idle_reconnect_count": self._ws_idle_reconnect_count,
            "deribit_ws_liveness_state": (
                "ticker_active"
                if not self._is_ws_ticker_stream_idle(now=now)
                else "soft_resubscribe_waiting"
                if self._ws_soft_resubscribe_in_progress
                else "stream_quiet_heartbeat_alive"
                if self._ws_heartbeat_last_success_ts > 0
                and now - self._ws_heartbeat_last_success_ts
                <= _WS_HEARTBEAT_RECHECK_SEC
                else "stream_quiet_unqualified"
            ),
            "deribit_ws_heartbeat_timeout_sec": _WS_HEARTBEAT_TIMEOUT_SEC,
            "deribit_ws_heartbeat_recheck_sec": _WS_HEARTBEAT_RECHECK_SEC,
            "deribit_ws_heartbeat_attempt_count": (
                self._ws_heartbeat_attempt_count
            ),
            "deribit_ws_heartbeat_success_count": (
                self._ws_heartbeat_success_count
            ),
            "deribit_ws_heartbeat_error_count": (
                self._ws_heartbeat_error_count
            ),
            "deribit_ws_heartbeat_last_attempt_ts": (
                self._ws_heartbeat_last_attempt_ts
            ),
            "deribit_ws_heartbeat_last_success_ts": (
                self._ws_heartbeat_last_success_ts
            ),
            "deribit_ws_heartbeat_last_rtt_ms": round(
                self._ws_heartbeat_last_rtt_ms,
                3,
            ),
            "deribit_ws_heartbeat_last_error": (
                self._ws_heartbeat_last_error
            ),
            "deribit_ws_soft_resubscribe_cooldown_sec": (
                _WS_SOFT_RESUBSCRIBE_COOLDOWN_SEC
            ),
            "deribit_ws_soft_resubscribe_grace_sec": (
                _WS_SOFT_RESUBSCRIBE_GRACE_SEC
            ),
            "deribit_ws_soft_resubscribe_requested": (
                self._ws_soft_resubscribe_requested
            ),
            "deribit_ws_soft_resubscribe_in_progress": (
                self._ws_soft_resubscribe_in_progress
            ),
            "deribit_ws_soft_resubscribe_attempt_count": (
                self._ws_soft_resubscribe_attempt_count
            ),
            "deribit_ws_soft_resubscribe_success_count": (
                self._ws_soft_resubscribe_success_count
            ),
            "deribit_ws_soft_resubscribe_error_count": (
                self._ws_soft_resubscribe_error_count
            ),
            "deribit_ws_soft_resubscribe_requested_ts": (
                self._ws_soft_resubscribe_requested_ts
            ),
            "deribit_ws_soft_resubscribe_last_ack_ts": (
                self._ws_soft_resubscribe_last_ack_ts
            ),
            "deribit_ws_soft_resubscribe_last_success_ts": (
                self._ws_soft_resubscribe_last_success_ts
            ),
            "deribit_ws_soft_resubscribe_last_error": (
                self._ws_soft_resubscribe_last_error
            ),
            "deribit_ws_refresh_loop_error_count": (
                self._ws_refresh_loop_error_count
            ),
            "deribit_ws_refresh_task_running": self._ws_refresh_task_running,
            "deribit_ws_ticker_idle_timeout_sec": (
                _WS_TICKER_IDLE_TIMEOUT_SEC
            ),
            "deribit_ws_ticker_idle_age_sec": ticker_idle_age_sec,
            "deribit_ws_ticker_watch_started_ts": (
                self._ws_ticker_watch_started_ts
            ),
            "deribit_ws_last_idle_reconnect_ts": (
                self._ws_last_idle_reconnect_ts
            ),
            "deribit_ws_connection_started_ts": (
                self._ws_connection_started_ts
            ),
            "deribit_ws_current_connection_age_sec": (
                round(now - self._ws_connection_started_ts, 3)
                if self._ws_connection_started_ts > 0
                else None
            ),
            "deribit_ws_full_tickers": full_ticker_count,
            "deribit_ws_fresh_full_tickers": fresh_full_ticker_count,
            "deribit_ws_core_full_tickers": full_core_ticker_count,
            "deribit_ws_core_oldest_ticker_age_sec": (
                round(max(core_ticker_ages), 3)
                if core_ticker_ages
                else None
            ),
            "deribit_ws_bootstrap_state": self._ticker_bootstrap_state,
            "deribit_ws_bootstrap_phase": self._ticker_bootstrap_phase,
            "deribit_ws_bootstrap_policy": (
                "circuit_breaker_30_to_300s+"
                "adaptive_recovery_2rps_healthy_0.25rps"
            ),
            "deribit_ws_bootstrap_mode": self._ticker_bootstrap_mode,
            "deribit_ws_bootstrap_current_batch_size": (
                self._ticker_bootstrap_current_batch_size
            ),
            "deribit_ws_bootstrap_current_interval_sec": (
                self._ticker_bootstrap_current_interval_sec
            ),
            "deribit_ws_bootstrap_low_rate_coverage_ratio": (
                _TICKER_LOW_RATE_COVERAGE_RATIO
            ),
            "deribit_ws_bootstrap_target_count": self._ticker_bootstrap_target_count,
            "deribit_ws_bootstrap_cycle_target_count": (
                self._ticker_bootstrap_cycle_target_count
            ),
            "deribit_ws_bootstrap_request_count": self._ticker_bootstrap_request_count,
            "deribit_ws_bootstrap_success_count": self._ticker_bootstrap_success_count,
            "deribit_ws_bootstrap_error_count": self._ticker_bootstrap_error_count,
            "deribit_ws_bootstrap_core_request_count": (
                self._ticker_bootstrap_core_request_count
            ),
            "deribit_ws_bootstrap_tail_request_count": (
                self._ticker_bootstrap_tail_request_count
            ),
            "deribit_ws_bootstrap_recovery_request_count": (
                self._ticker_bootstrap_recovery_request_count
            ),
            "deribit_ws_bootstrap_low_rate_request_count": (
                self._ticker_bootstrap_low_rate_request_count
            ),
            "deribit_ws_bootstrap_core_batches_per_tail_batch": (
                _TICKER_CORE_BATCHES_PER_TAIL_BATCH
            ),
            "deribit_ws_bootstrap_core_refresh_age_sec": (
                _TICKER_CORE_REFRESH_AGE_SEC
            ),
            "deribit_ws_bootstrap_healthy_core_refresh_age_sec": (
                _TICKER_HEALTHY_CORE_REFRESH_AGE_SEC
            ),
            "deribit_ws_bootstrap_healthy_interval_sec": (
                _TICKER_HEALTHY_BATCH_INTERVAL_SEC
            ),
            "deribit_ws_bootstrap_last_request_ts": (
                self._ticker_bootstrap_last_request_ts
            ),
            "deribit_ws_bootstrap_pending_tickers": max(
                0,
                self._ticker_bootstrap_target_count - full_ticker_count,
            ),
            "deribit_ws_core_pending_tickers": max(
                0,
                core_count - full_core_ticker_count,
            ),
            "deribit_ws_bootstrap_last_error": self._ticker_bootstrap_last_error,
            "deribit_ws_bootstrap_last_success_ts": (
                self._ticker_bootstrap_last_success_ts
            ),
            "deribit_ws_bootstrap_elapsed_sec": (
                round(
                    (
                        self._ticker_bootstrap_completed_ts or time.time()
                    ) - self._ticker_bootstrap_started_ts,
                    3,
                )
                if self._ticker_bootstrap_started_ts > 0
                else None
            ),
            "deribit_ws_last_ticker_ts": self._ws_last_ticker_ts,
            "deribit_ws_last_subscription_refresh_ts": self._ws_last_subscription_refresh_ts,
            "deribit_ws_cache_age_sec": (
                round(time.time() - self._tickers_cache_ts, 3)
                if self._tickers_cache_ts > 0
                else None
            ),
        }
