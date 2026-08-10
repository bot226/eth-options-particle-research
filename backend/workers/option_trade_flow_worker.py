"""Standalone public BTC option-trade collector for research.

This observer writes raw, deduplicated Bybit and Deribit option trades to its
own SQLite database. It never reads or changes MOS state, scores, candidates,
manual trading, or the existing research/history database schemas.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

import httpx
import websockets

try:  # Runtime uses backend as cwd; tests import through the backend package.
    from config import BYBIT_REST_URL, BYBIT_WS_OPTION_URL, DERIBIT_REST_URL, DERIBIT_WS_URL
    from engine.instrument_normalizer import InstrumentNormalizer
except ImportError:  # pragma: no cover - exercised only by package-style imports
    from backend.config import (
        BYBIT_REST_URL,
        BYBIT_WS_OPTION_URL,
        DERIBIT_REST_URL,
        DERIBIT_WS_URL,
    )
    from backend.engine.instrument_normalizer import InstrumentNormalizer


log = logging.getLogger(__name__)

SCHEMA_VERSION = "1.1"
DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "option_trade_flow.db"
QUEUE_MAXSIZE = 50_000
WRITE_BATCH_SIZE = 1_000
STATUS_FLUSH_SECONDS = 5.0
DERIBIT_HEARTBEAT_INTERVAL_SECONDS = 20.0
DERIBIT_HEARTBEAT_TIMEOUT_SECONDS = 10.0
BACKFILL_MIN_INTERVAL_SECONDS = 300.0
BYBIT_OPTION_TRADE_TOPIC = "publicTrade.BTC"


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _iv_decimal(exchange: str, value: Any) -> float | None:
    parsed = _float(value)
    if parsed is None:
        return None
    normalized = InstrumentNormalizer.normalize_iv(exchange, parsed)
    return normalized if normalized > 0 else None


def bybit_subscription_confirmed(
    acknowledgement: dict[str, Any], topic: str = BYBIT_OPTION_TRADE_TOPIC
) -> bool:
    """Accept both documented Bybit subscription acknowledgement shapes."""
    if not acknowledgement.get("success", False):
        return False
    if acknowledgement.get("op") == "subscribe":
        return True
    if acknowledgement.get("type") != "COMMAND_RESP":
        return False
    data = acknowledgement.get("data")
    if not isinstance(data, dict):
        return False
    successful = data.get("successTopics") or []
    failed = data.get("failTopics") or []
    return topic in successful and topic not in failed


def _identity(exchange: str, symbol: str) -> tuple[str | None, str | None, float | None, str | None]:
    parsed = InstrumentNormalizer.normalize_symbol(exchange, symbol)
    if parsed is None:
        return None, None, None, None
    return parsed.canonical_id, parsed.expiry_str, float(parsed.strike), parsed.opt_type


def normalize_bybit_trade(raw: dict[str, Any], received_at: float | None = None) -> dict[str, Any] | None:
    symbol = str(raw.get("s") or raw.get("symbol") or "")
    trade_timestamp_ms = _integer(raw.get("T", raw.get("time")))
    side = str(raw.get("S") or raw.get("side") or "").upper()
    if not symbol or trade_timestamp_ms is None or side not in {"BUY", "SELL"}:
        return None
    trade_id = str(raw.get("i") or raw.get("execId") or "")
    if not trade_id:
        trade_id = ":".join(
            str(value)
            for value in (
                symbol,
                raw.get("seq", ""),
                trade_timestamp_ms,
                raw.get("p", raw.get("price", "")),
                raw.get("v", raw.get("size", "")),
                side,
            )
        )
    contract_id, expiry, strike, option_type = _identity("bybit", symbol)
    amount = _float(raw.get("v", raw.get("size")))
    trade_iv = _float(raw.get("iv"))
    mark_iv = _float(raw.get("mIv"))
    return {
        "exchange": "bybit",
        "trade_id": trade_id,
        "trade_sequence": str(raw.get("seq") or "") or None,
        "trade_timestamp_ms": trade_timestamp_ms,
        "trade_timestamp_utc": trade_timestamp_ms / 1000.0,
        "received_at_utc": time.time() if received_at is None else received_at,
        "source_symbol": symbol,
        "contract_id": contract_id,
        "expiry": expiry,
        "strike": strike,
        "option_type": option_type,
        "taker_side": side,
        "price": _float(raw.get("p", raw.get("price"))),
        "amount": amount,
        "contracts": amount,
        "index_price": _float(raw.get("iP")),
        "mark_price": _float(raw.get("mP")),
        "trade_iv": trade_iv,
        "trade_iv_decimal": _iv_decimal("bybit", trade_iv),
        "mark_iv": mark_iv,
        "mark_iv_decimal": _iv_decimal("bybit", mark_iv),
        "tick_direction": None,
        "is_block_trade": int(bool(raw.get("BT", raw.get("isBlockTrade", False)))),
        "is_combo_trade": 0,
        "raw_json": json.dumps(raw, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
    }


def normalize_deribit_trade(raw: dict[str, Any], received_at: float | None = None) -> dict[str, Any] | None:
    symbol = str(raw.get("instrument_name") or "")
    trade_timestamp_ms = _integer(raw.get("timestamp"))
    side = str(raw.get("direction") or "").upper()
    trade_id = str(raw.get("trade_id") or "")
    if not symbol or not trade_id or trade_timestamp_ms is None or side not in {"BUY", "SELL"}:
        return None
    contract_id, expiry, strike, option_type = _identity("deribit", symbol)
    trade_iv = _float(raw.get("iv"))
    return {
        "exchange": "deribit",
        "trade_id": trade_id,
        "trade_sequence": str(raw.get("trade_seq") or "") or None,
        "trade_timestamp_ms": trade_timestamp_ms,
        "trade_timestamp_utc": trade_timestamp_ms / 1000.0,
        "received_at_utc": time.time() if received_at is None else received_at,
        "source_symbol": symbol,
        "contract_id": contract_id,
        "expiry": expiry,
        "strike": strike,
        "option_type": option_type,
        "taker_side": side,
        "price": _float(raw.get("price")),
        "amount": _float(raw.get("amount")),
        "contracts": _float(raw.get("contracts")),
        "index_price": _float(raw.get("index_price")),
        "mark_price": _float(raw.get("mark_price")),
        "trade_iv": trade_iv,
        "trade_iv_decimal": _iv_decimal("deribit", trade_iv),
        "mark_iv": None,
        "mark_iv_decimal": None,
        "tick_direction": _integer(raw.get("tick_direction")),
        "is_block_trade": int(bool(raw.get("block_trade_id") or raw.get("block_rfq_id"))),
        "is_combo_trade": int(bool(raw.get("combo_id") or raw.get("combo_trade_id"))),
        "raw_json": json.dumps(raw, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
    }


TRADE_COLUMNS = (
    "exchange",
    "trade_id",
    "trade_sequence",
    "trade_timestamp_ms",
    "trade_timestamp_utc",
    "received_at_utc",
    "source_symbol",
    "contract_id",
    "expiry",
    "strike",
    "option_type",
    "taker_side",
    "price",
    "amount",
    "contracts",
    "index_price",
    "mark_price",
    "trade_iv",
    "trade_iv_decimal",
    "mark_iv",
    "mark_iv_decimal",
    "tick_direction",
    "is_block_trade",
    "is_combo_trade",
    "raw_json",
)


class OptionTradeFlowStore:
    def __init__(self, path: Path | str = DEFAULT_DB_PATH):
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30.0)
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS option_trades (
                    exchange TEXT NOT NULL,
                    trade_id TEXT NOT NULL,
                    trade_sequence TEXT,
                    trade_timestamp_ms INTEGER NOT NULL,
                    trade_timestamp_utc REAL NOT NULL,
                    received_at_utc REAL NOT NULL,
                    source_symbol TEXT NOT NULL,
                    contract_id TEXT,
                    expiry TEXT,
                    strike REAL,
                    option_type TEXT,
                    taker_side TEXT NOT NULL,
                    price REAL,
                    amount REAL,
                    contracts REAL,
                    index_price REAL,
                    mark_price REAL,
                    trade_iv REAL,
                    trade_iv_decimal REAL,
                    mark_iv REAL,
                    mark_iv_decimal REAL,
                    tick_direction INTEGER,
                    is_block_trade INTEGER NOT NULL DEFAULT 0,
                    is_combo_trade INTEGER NOT NULL DEFAULT 0,
                    raw_json TEXT NOT NULL,
                    PRIMARY KEY (exchange, trade_id)
                );
                CREATE INDEX IF NOT EXISTS idx_option_trades_timestamp
                    ON option_trades(trade_timestamp_utc);
                CREATE INDEX IF NOT EXISTS idx_option_trades_contract_timestamp
                    ON option_trades(contract_id, trade_timestamp_utc);
                CREATE INDEX IF NOT EXISTS idx_option_trades_exchange_timestamp
                    ON option_trades(exchange, trade_timestamp_utc);
                CREATE TABLE IF NOT EXISTS collector_status (
                    exchange TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL DEFAULT '',
                    process_started_at_utc REAL NOT NULL DEFAULT 0,
                    connection_state TEXT NOT NULL,
                    connection_count INTEGER NOT NULL,
                    reconnect_count INTEGER NOT NULL,
                    message_count INTEGER NOT NULL,
                    normalized_trade_count INTEGER NOT NULL,
                    queued_trade_count INTEGER NOT NULL,
                    dropped_trade_count INTEGER NOT NULL,
                    last_message_utc REAL,
                    last_trade_utc REAL,
                    last_error TEXT NOT NULL,
                    updated_at_utc REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS collector_status_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    process_started_at_utc REAL NOT NULL,
                    exchange TEXT NOT NULL,
                    connection_state TEXT NOT NULL,
                    connection_count INTEGER NOT NULL,
                    reconnect_count INTEGER NOT NULL,
                    message_count INTEGER NOT NULL,
                    normalized_trade_count INTEGER NOT NULL,
                    queued_trade_count INTEGER NOT NULL,
                    dropped_trade_count INTEGER NOT NULL,
                    last_message_utc REAL,
                    last_trade_utc REAL,
                    last_error TEXT NOT NULL,
                    updated_at_utc REAL NOT NULL,
                    UNIQUE(session_id, exchange, updated_at_utc)
                );
                CREATE INDEX IF NOT EXISTS idx_option_flow_status_history_time
                    ON collector_status_history(updated_at_utc);
                CREATE INDEX IF NOT EXISTS idx_option_flow_status_history_session
                    ON collector_status_history(session_id, exchange, updated_at_utc);
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            current_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(collector_status)")
            }
            if "session_id" not in current_columns:
                connection.execute(
                    "ALTER TABLE collector_status ADD COLUMN session_id TEXT NOT NULL DEFAULT ''"
                )
            if "process_started_at_utc" not in current_columns:
                connection.execute(
                    "ALTER TABLE collector_status ADD COLUMN "
                    "process_started_at_utc REAL NOT NULL DEFAULT 0"
                )
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
                (SCHEMA_VERSION,),
            )
            connection.commit()
        finally:
            connection.close()

    def insert_trades(self, trades: Iterable[dict[str, Any]]) -> int:
        values = [tuple(trade.get(column) for column in TRADE_COLUMNS) for trade in trades]
        if not values:
            return 0
        placeholders = ",".join("?" for _ in TRADE_COLUMNS)
        columns = ",".join(TRADE_COLUMNS)
        connection = sqlite3.connect(self.path, timeout=30.0)
        try:
            connection.execute("PRAGMA busy_timeout = 30000")
            before = connection.total_changes
            connection.executemany(
                f"INSERT OR IGNORE INTO option_trades ({columns}) VALUES ({placeholders})",
                values,
            )
            connection.commit()
            return connection.total_changes - before
        finally:
            connection.close()

    def write_status(
        self,
        statuses: dict[str, dict[str, Any]],
        session_id: str,
        process_started_at_utc: float,
    ) -> None:
        now = time.time()
        rows = [
            (
                exchange,
                session_id,
                process_started_at_utc,
                status["connection_state"],
                status["connection_count"],
                status["reconnect_count"],
                status["message_count"],
                status["normalized_trade_count"],
                status["queued_trade_count"],
                status["dropped_trade_count"],
                status["last_message_utc"] or None,
                status["last_trade_utc"] or None,
                status["last_error"],
                now,
            )
            for exchange, status in statuses.items()
        ]
        connection = sqlite3.connect(self.path, timeout=30.0)
        try:
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.executemany(
                """
                INSERT INTO collector_status (
                    exchange, session_id, process_started_at_utc, connection_state,
                    connection_count, reconnect_count, message_count,
                    normalized_trade_count, queued_trade_count, dropped_trade_count,
                    last_message_utc, last_trade_utc, last_error, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(exchange) DO UPDATE SET
                    session_id=excluded.session_id,
                    process_started_at_utc=excluded.process_started_at_utc,
                    connection_state=excluded.connection_state,
                    connection_count=excluded.connection_count,
                    reconnect_count=excluded.reconnect_count,
                    message_count=excluded.message_count,
                    normalized_trade_count=excluded.normalized_trade_count,
                    queued_trade_count=excluded.queued_trade_count,
                    dropped_trade_count=excluded.dropped_trade_count,
                    last_message_utc=excluded.last_message_utc,
                    last_trade_utc=excluded.last_trade_utc,
                    last_error=excluded.last_error,
                    updated_at_utc=excluded.updated_at_utc
                """,
                rows,
            )
            connection.executemany(
                """
                INSERT OR IGNORE INTO collector_status_history (
                    exchange, session_id, process_started_at_utc, connection_state,
                    connection_count, reconnect_count, message_count,
                    normalized_trade_count, queued_trade_count, dropped_trade_count,
                    last_message_utc, last_trade_utc, last_error, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            connection.commit()
        finally:
            connection.close()


class OptionTradeFlowCollector:
    def __init__(self, database_path: Path | str = DEFAULT_DB_PATH):
        self.store = OptionTradeFlowStore(database_path)
        self.session_id = uuid.uuid4().hex
        self.process_started_at_utc = time.time()
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self.running = False
        self.last_backfill_utc: dict[str, float] = {"bybit": 0.0, "deribit": 0.0}
        self.deribit_heartbeat_request_id = 1_000_000
        self.statuses = {
            exchange: {
                "connection_state": "idle",
                "connection_count": 0,
                "reconnect_count": 0,
                "message_count": 0,
                "normalized_trade_count": 0,
                "queued_trade_count": 0,
                "dropped_trade_count": 0,
                "last_message_utc": 0.0,
                "last_trade_utc": 0.0,
                "last_error": "",
            }
            for exchange in ("bybit", "deribit")
        }

    def _enqueue(self, exchange: str, trade: dict[str, Any] | None) -> None:
        if trade is None:
            return
        status = self.statuses[exchange]
        status["normalized_trade_count"] += 1
        status["last_trade_utc"] = max(status["last_trade_utc"], trade["trade_timestamp_utc"])
        try:
            self.queue.put_nowait(trade)
            status["queued_trade_count"] += 1
        except asyncio.QueueFull:
            status["dropped_trade_count"] += 1

    async def _backfill(self, exchange: str) -> None:
        now = time.time()
        if now - self.last_backfill_utc[exchange] < BACKFILL_MIN_INTERVAL_SECONDS:
            return
        self.last_backfill_utc[exchange] = now
        try:
            async with httpx.AsyncClient(
                timeout=10.0,
                headers={"Accept": "application/json", "Accept-Encoding": "gzip, deflate"},
            ) as client:
                if exchange == "bybit":
                    response = await client.get(
                        f"{BYBIT_REST_URL}/v5/market/recent-trade",
                        params={"category": "option", "baseCoin": "BTC", "limit": 1000},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    trades = payload.get("result", {}).get("list", [])
                    for raw in reversed(trades):
                        self._enqueue("bybit", normalize_bybit_trade(raw, now))
                else:
                    response = await client.get(
                        f"{DERIBIT_REST_URL}/public/get_last_trades_by_currency",
                        params={"currency": "BTC", "kind": "option", "count": 1000, "sorting": "asc"},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    trades = payload.get("result", {}).get("trades", [])
                    for raw in trades:
                        self._enqueue("deribit", normalize_deribit_trade(raw, now))
        except Exception as exc:
            self.statuses[exchange]["last_error"] = f"backfill:{type(exc).__name__}:{exc}"

    async def _bybit_loop(self) -> None:
        exchange = "bybit"
        attempt = 0
        while self.running:
            status = self.statuses[exchange]
            try:
                status["connection_state"] = "connecting"
                async with websockets.connect(
                    BYBIT_WS_OPTION_URL,
                    ping_interval=None,
                    ping_timeout=None,
                    close_timeout=5,
                    open_timeout=10,
                ) as websocket:
                    attempt = 0
                    status["connection_count"] += 1
                    status["connection_state"] = "subscribing"
                    status["last_error"] = ""
                    await websocket.send(
                        json.dumps({"op": "subscribe", "args": [BYBIT_OPTION_TRADE_TOPIC]})
                    )
                    acknowledgement = json.loads(
                        await asyncio.wait_for(websocket.recv(), timeout=10.0)
                    )
                    if not bybit_subscription_confirmed(acknowledgement):
                        raise RuntimeError(f"subscription_rejected:{acknowledgement}")
                    status["connection_state"] = "subscribed"
                    await self._backfill(exchange)

                    async def receive() -> None:
                        async for message in websocket:
                            payload = json.loads(message)
                            if payload.get("op") == "pong":
                                continue
                            if payload.get("topic") != BYBIT_OPTION_TRADE_TOPIC:
                                continue
                            status["message_count"] += 1
                            status["last_message_utc"] = time.time()
                            for raw in payload.get("data", []):
                                self._enqueue(exchange, normalize_bybit_trade(raw))

                    async def heartbeat() -> None:
                        while self.running:
                            await asyncio.sleep(20.0)
                            await websocket.send(json.dumps({"op": "ping"}))

                    receive_task = asyncio.create_task(receive())
                    heartbeat_task = asyncio.create_task(heartbeat())
                    done, pending = await asyncio.wait(
                        (receive_task, heartbeat_task), return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    for task in done:
                        task.result()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                status["connection_state"] = "reconnecting"
                status["reconnect_count"] += 1
                status["last_error"] = f"{type(exc).__name__}:{exc}"
                attempt += 1
                await asyncio.sleep(min(30.0, 2.0**min(attempt, 5)))

    async def _deribit_loop(self) -> None:
        exchange = "deribit"
        attempt = 0
        while self.running:
            status = self.statuses[exchange]
            try:
                status["connection_state"] = "connecting"
                async with websockets.connect(
                    DERIBIT_WS_URL,
                    ping_interval=None,
                    ping_timeout=None,
                    close_timeout=5,
                    open_timeout=10,
                ) as websocket:
                    attempt = 0
                    status["connection_count"] += 1
                    status["connection_state"] = "subscribing"
                    status["last_error"] = ""
                    await websocket.send(
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "public/subscribe",
                                "params": {"channels": ["trades.option.BTC.100ms"]},
                            }
                        )
                    )
                    acknowledgement = json.loads(
                        await asyncio.wait_for(websocket.recv(), timeout=10.0)
                    )
                    if acknowledgement.get("id") != 1 or acknowledgement.get("error"):
                        raise RuntimeError(f"subscription_rejected:{acknowledgement}")
                    channels = acknowledgement.get("result") or []
                    if "trades.option.BTC.100ms" not in channels:
                        raise RuntimeError(f"subscription_not_confirmed:{acknowledgement}")
                    status["connection_state"] = "subscribed"
                    await self._backfill(exchange)
                    heartbeat_deadline = (
                        asyncio.get_running_loop().time()
                        + DERIBIT_HEARTBEAT_INTERVAL_SECONDS
                    )
                    while self.running:
                        heartbeat_wait = max(
                            0.0,
                            heartbeat_deadline
                            - asyncio.get_running_loop().time(),
                        )
                        try:
                            message = await asyncio.wait_for(
                                websocket.recv(),
                                timeout=heartbeat_wait,
                            )
                        except asyncio.TimeoutError:
                            await self._deribit_application_heartbeat(
                                websocket,
                                status,
                            )
                            heartbeat_deadline = (
                                asyncio.get_running_loop().time()
                                + DERIBIT_HEARTBEAT_INTERVAL_SECONDS
                            )
                            continue
                        self._handle_deribit_stream_payload(
                            status,
                            json.loads(message),
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                status["connection_state"] = "reconnecting"
                status["reconnect_count"] += 1
                status["last_error"] = f"{type(exc).__name__}:{exc}"
                attempt += 1
                await asyncio.sleep(min(30.0, 2.0**min(attempt, 5)))

    def _handle_deribit_stream_payload(
        self,
        status: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        params = payload.get("params", {})
        if params.get("channel") != "trades.option.BTC.100ms":
            return
        status["message_count"] += 1
        status["last_message_utc"] = time.time()
        for raw in params.get("data", []):
            self._enqueue("deribit", normalize_deribit_trade(raw))

    async def _deribit_application_heartbeat(
        self,
        websocket,
        status: dict[str, Any],
    ) -> None:
        """Keep the direct trade stream alive using Deribit JSON-RPC data."""
        self.deribit_heartbeat_request_id += 1
        request_id = self.deribit_heartbeat_request_id
        await websocket.send(json.dumps({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "public/test",
            "params": {},
        }))
        deadline = asyncio.get_running_loop().time() + (
            DERIBIT_HEARTBEAT_TIMEOUT_SECONDS
        )
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            message = await asyncio.wait_for(
                websocket.recv(),
                timeout=remaining,
            )
            payload = json.loads(message)
            if payload.get("id") == request_id:
                if payload.get("error") or "result" not in payload:
                    raise RuntimeError(
                        f"heartbeat_rpc_error:{payload.get('error') or payload}"
                    )
                return
            self._handle_deribit_stream_payload(status, payload)

    async def _writer_loop(self) -> None:
        last_status_flush = 0.0
        while self.running or not self.queue.empty():
            batch: list[dict[str, Any]] = []
            try:
                first = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                batch.append(first)
                while len(batch) < WRITE_BATCH_SIZE:
                    try:
                        batch.append(self.queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
            except asyncio.TimeoutError:
                pass
            if batch:
                await asyncio.to_thread(self.store.insert_trades, batch)
                for _ in batch:
                    self.queue.task_done()
            now = time.time()
            if now - last_status_flush >= STATUS_FLUSH_SECONDS:
                await asyncio.to_thread(
                    self.store.write_status,
                    self.statuses,
                    self.session_id,
                    self.process_started_at_utc,
                )
                last_status_flush = now

    async def run(self) -> None:
        self.store.initialize()
        self.running = True
        writer_task = asyncio.create_task(self._writer_loop(), name="option-flow-writer")
        stream_tasks = [
            asyncio.create_task(self._bybit_loop(), name="option-flow-bybit"),
            asyncio.create_task(self._deribit_loop(), name="option-flow-deribit"),
        ]
        tasks = [writer_task, *stream_tasks]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                task.result()
        finally:
            self.running = False
            for task in stream_tasks:
                task.cancel()
            await asyncio.gather(*stream_tasks, return_exceptions=True)
            if not writer_task.done():
                try:
                    await asyncio.wait_for(self.queue.join(), timeout=10.0)
                    await asyncio.wait_for(writer_task, timeout=2.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    writer_task.cancel()
            await asyncio.gather(writer_task, return_exceptions=True)
            for status in self.statuses.values():
                status["connection_state"] = "stopped"
            await asyncio.to_thread(
                self.store.write_status,
                self.statuses,
                self.session_id,
                self.process_started_at_utc,
            )


async def _main() -> None:
    logging.basicConfig(
        level=os.getenv("MOS_OPTION_FLOW_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    path = Path(os.getenv("MOS_OPTION_FLOW_DB", str(DEFAULT_DB_PATH)))
    collector = OptionTradeFlowCollector(path)
    log.info("Starting option trade-flow observer: %s", path)
    await collector.run()


def main() -> int:
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
