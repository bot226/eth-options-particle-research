"""Bybit V5 WebSocket клиент (asyncio — websockets).

Stable design:
- Library-level pings DISABLED (Bybit uses own heartbeat protocol)
- Application-level ping every 20s: {"op": "ping"}
- Exponential backoff on reconnect: 1→2→4→8→16→30s
- REST data unaffected by WS drops (WS is supplemental)
"""

import json
import asyncio
import logging
import websockets
from config import BYBIT_WS_OPTION_URL, BYBIT_WS_LINEAR_URL

log = logging.getLogger(__name__)

# Application-level heartbeat: Bybit expects {"op":"ping"} every <20s
_HEARTBEAT_INTERVAL = 18   # seconds
_HEARTBEAT_TIMEOUT  = 8    # seconds to wait for pong response
_MAX_RECONNECT_DELAY = 30  # seconds cap for backoff


class BybitWebSocket:
    """Async WebSocket клиент — два канала: option tickers + linear (ETH spot)."""

    def __init__(self, on_ticker=None, on_spot=None):
        self._on_ticker = on_ticker    # callback(data: dict)
        self._on_spot = on_spot        # callback(data: dict)
        self._running = False
        self._tasks: list[asyncio.Task] = []

    async def start(self):
        self._running = True
        self._tasks = [
            asyncio.create_task(self._run_ws(
                BYBIT_WS_OPTION_URL, "tickers.ETH", self._handle_option, "option"
            )),
            asyncio.create_task(self._run_ws(
                BYBIT_WS_LINEAR_URL, "tickers.ETHUSDT", self._handle_linear, "linear"
            )),
        ]
        log.info("WebSocket tasks started")

    async def stop(self):
        self._running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        log.info("WebSocket stopped")

    async def _run_ws(self, url: str, topic: str, handler, channel: str):
        """Connection loop with exponential backoff and app-level heartbeat."""
        attempt = 0
        while self._running:
            delay = min(_MAX_RECONNECT_DELAY, (2 ** attempt))
            try:
                async with websockets.connect(
                    url,
                    # IMPORTANT: disable library pings — Bybit sends its own
                    # heartbeat. Library pings cause 1011 errors (keepalive
                    # ping timeout) because Bybit does not respond to raw
                    # WebSocket ping frames as expected.
                    ping_interval=None,
                    ping_timeout=None,
                    close_timeout=5,
                    open_timeout=10,
                ) as ws:
                    attempt = 0  # reset backoff on successful connect
                    sub = json.dumps({"op": "subscribe", "args": [topic]})
                    await ws.send(sub)
                    log.info("%s WS connected, subscribed to %s", channel, topic)

                    # Run heartbeat and message receiver concurrently
                    recv_task = asyncio.create_task(
                        self._recv_loop(ws, handler, channel)
                    )
                    hb_task = asyncio.create_task(
                        self._heartbeat_loop(ws, channel)
                    )
                    try:
                        done, pending = await asyncio.wait(
                            [recv_task, hb_task],
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        for t in pending:
                            t.cancel()
                        # Re-raise any exception from completed task
                        for t in done:
                            exc = t.exception()
                            if exc:
                                raise exc
                    except asyncio.CancelledError:
                        recv_task.cancel()
                        hb_task.cancel()
                        raise

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("%s WS error: %s", channel, e)

            if self._running:
                attempt += 1
                log.info("%s WS reconnecting in %ss (attempt %d)...",
                         channel, delay, attempt)
                await asyncio.sleep(delay)

    async def _recv_loop(self, ws, handler, channel: str):
        """Receive messages and dispatch to handler."""
        async for message in ws:
            if not self._running:
                break
            try:
                msg = json.loads(message)
                # Handle Bybit pong response
                op = msg.get("op", "")
                if op == "pong":
                    log.debug("%s WS pong received", channel)
                    continue
                # Dispatch ticker data
                if "topic" in msg and msg.get("data"):
                    await handler(msg["data"])
            except Exception:
                pass

    async def _heartbeat_loop(self, ws, channel: str):
        """Send {"op":"ping"} every N seconds to keep connection alive."""
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
            try:
                await asyncio.wait_for(
                    ws.send(json.dumps({"op": "ping"})),
                    timeout=5,
                )
                log.debug("%s WS ping sent", channel)
            except asyncio.TimeoutError:
                log.warning("%s WS ping send timeout — dropping connection", channel)
                raise ConnectionError("ping send timeout")
            except Exception as e:
                log.warning("%s WS heartbeat error: %s", channel, e)
                raise

    async def _handle_option(self, data: dict):
        if self._on_ticker:
            self._on_ticker(data)

    async def _handle_linear(self, data: dict):
        if self._on_spot:
            self._on_spot(data)
