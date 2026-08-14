"""BybitAdapter — Bybit exchange adapter for ETH options data.

Wraps existing BybitRestClient and BybitWebSocket into
BaseExchangeAdapter interface. Quality factor: 0.80.

REST: https://api.bybit.com/v5
WS:   wss://stream.bybit.com/v5/public/option
"""

import time
import asyncio
import logging
from typing import Optional

from api.base_adapter import BaseExchangeAdapter
from api.rest_client import BybitRestClient
from config import (
    BYBIT_REST_URL, BYBIT_WS_OPTION_URL, BYBIT_WS_LINEAR_URL,
    WS_PING_INTERVAL, WS_RECONNECT_DELAY, BASE_COIN, LINEAR_SYMBOL,
)

import json
import websockets

log = logging.getLogger(__name__)


class BybitAdapter(BaseExchangeAdapter):
    """Async adapter for Bybit V5 public options API.

    Wraps existing BybitRestClient for REST and implements
    WebSocket connection internally.
    """

    def __init__(self):
        super().__init__(exchange_id="bybit", quality_factor=0.80)
        self._rest = BybitRestClient(base_url=BYBIT_REST_URL)
        self._ws_tasks: list[asyncio.Task] = []
        self._tickers_cache: list[dict] = []
        self._spot_price: float = 0.0
        self._spot_24h_change: float = 0.0
        self._spot_prev_price: float = 0.0
        # Callbacks for legacy DataManager compatibility
        self._on_ticker_cb = None
        self._on_spot_cb = None

    def set_callbacks(self, on_ticker=None, on_spot=None):
        """Set callbacks for legacy DataManager compatibility."""
        self._on_ticker_cb = on_ticker
        self._on_spot_cb = on_spot

    # ── Lifecycle ────────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        self._ws_tasks = [
            asyncio.create_task(self._run_ws(
                BYBIT_WS_OPTION_URL, "tickers.ETH",
                self._handle_option_msg, "option"
            )),
            asyncio.create_task(self._run_ws(
                BYBIT_WS_LINEAR_URL, "tickers.ETHUSDT",
                self._handle_linear_msg, "linear"
            )),
        ]
        log.info("BybitAdapter started")

    async def stop(self) -> None:
        self._running = False
        for t in self._ws_tasks:
            t.cancel()
        await asyncio.gather(*self._ws_tasks, return_exceptions=True)
        await self._rest.close()
        log.info("BybitAdapter stopped")

    # ── REST API ─────────────────────────────────────────────────────

    async def fetch_instruments(self) -> list[dict]:
        """Fetch all active ETH option instruments from Bybit."""
        t0 = time.time()
        result = await self._rest.get_instruments(base_coin=BASE_COIN)
        latency = (time.time() - t0) * 1000
        self.health.update(latency_ms=latency, ws_connected=self.health.ws_connected)
        return result

    async def fetch_option_tickers(self) -> list[dict]:
        """Fetch current tickers for all ETH options."""
        t0 = time.time()
        tickers = await self._rest.get_option_tickers(base_coin=BASE_COIN)
        latency = (time.time() - t0) * 1000
        self.health.update(latency_ms=latency, ws_connected=self.health.ws_connected)

        self._tickers_cache = tickers

        # Compute aggregates
        total_oi = 0.0
        total_vol = 0.0
        for t in tickers:
            try:
                total_oi += float(t.get("openInterest", 0) or 0)
                total_vol += float(t.get("volume24h", 0) or 0)
            except (TypeError, ValueError):
                pass
        self._total_oi = total_oi
        self._total_volume = total_vol

        return tickers

    async def fetch_spot_price(self) -> Optional[float]:
        """Fetch ETH spot price from Bybit linear ticker."""
        t0 = time.time()
        ticker = await self._rest.get_spot_ticker(symbol=LINEAR_SYMBOL)
        latency = (time.time() - t0) * 1000
        self.health.update(latency_ms=latency, ws_connected=self.health.ws_connected)

        if ticker:
            try:
                price = float(ticker.get("lastPrice", 0))
                if price > 0:
                    self._spot_price = price
                    prev = float(ticker.get("prevPrice24h", 0) or 0)
                    if prev > 0:
                        self._spot_prev_price = prev
                        self._spot_24h_change = (price - prev) / prev
                    return price
            except (TypeError, ValueError):
                pass
        return None

    # ── Spot accessors ───────────────────────────────────────────────

    @property
    def spot_price(self) -> float:
        return self._spot_price

    @property
    def spot_24h_change(self) -> float:
        return self._spot_24h_change

    @property
    def spot_prev_price(self) -> float:
        return self._spot_prev_price

    # ── WebSocket ────────────────────────────────────────────────────

    async def _run_ws(self, url: str, topic: str, handler, channel: str):
        """WS connection loop — no library pings, app-level heartbeat, exp. backoff."""
        _HB_INTERVAL = 18   # send {"op":"ping"} every 18s
        _MAX_DELAY   = 30   # max reconnect delay
        attempt = 0
        while self._running:
            delay = min(_MAX_DELAY, WS_RECONNECT_DELAY * (2 ** attempt))
            try:
                async with websockets.connect(
                    url,
                    # Library-level pings DISABLED: Bybit responds to
                    # {"op":"ping"} JSON frames, NOT to raw WS ping frames.
                    # Raw pings cause 1011 "keepalive ping timeout" errors.
                    ping_interval=None,
                    ping_timeout=None,
                    close_timeout=5,
                    open_timeout=10,
                ) as ws:
                    attempt = 0  # reset backoff
                    sub = json.dumps({"op": "subscribe", "args": [topic]})
                    await ws.send(sub)
                    log.info("Bybit %s WS connected, subscribed to %s", channel, topic)

                    if channel == "option":
                        self.health.ws_connected = True

                    async def _recv():
                        async for message in ws:
                            if not self._running:
                                break
                            try:
                                msg = json.loads(message)
                                op = msg.get("op", "")
                                if op == "pong":
                                    log.debug("Bybit %s pong", channel)
                                    continue
                                if "topic" in msg and msg.get("data"):
                                    await handler(msg["data"])
                            except Exception:
                                pass

                    async def _heartbeat():
                        while True:
                            await asyncio.sleep(_HB_INTERVAL)
                            try:
                                await ws.send(json.dumps({"op": "ping"}))
                            except Exception as e:
                                log.warning("Bybit %s heartbeat error: %s", channel, e)
                                raise

                    recv_t = asyncio.create_task(_recv())
                    hb_t   = asyncio.create_task(_heartbeat())
                    done, pending = await asyncio.wait(
                        [recv_t, hb_t],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()
                    for t in done:
                        exc = t.exception()
                        if exc:
                            raise exc

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("Bybit %s WS error: %s", channel, e)
                if channel == "option":
                    self.health.ws_connected = False

            if self._running:
                attempt += 1
                log.info("Bybit %s WS reconnecting in %.0fs (attempt %d)...",
                         channel, delay, attempt)
                await asyncio.sleep(delay)

        if channel == "option":
            self.health.ws_connected = False


    async def _handle_option_msg(self, data: dict):
        """Handle option ticker WS message."""
        self.health.update(
            latency_ms=self.health.latency_ms,
            ws_connected=True,
        )
        # Forward to legacy callback if set
        if self._on_ticker_cb:
            self._on_ticker_cb(data)

    async def _handle_linear_msg(self, data: dict):
        """Handle linear (spot) ticker WS message."""
        try:
            price = float(data.get("lastPrice", 0))
            if price > 0:
                self._spot_price = price
                prev = float(data.get("prevPrice24h", 0) or 0)
                if prev > 0:
                    self._spot_prev_price = prev
                    self._spot_24h_change = (price - prev) / prev
        except (TypeError, ValueError):
            pass
        # Forward to legacy callback if set
        if self._on_spot_cb:
            self._on_spot_cb(data)
