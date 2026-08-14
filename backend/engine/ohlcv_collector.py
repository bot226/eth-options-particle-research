"""OHLCV collector for MOS Research Layer replay validation.

v2: Bybit linear ETHUSDT 1m candles (execution instrument).
Previously used Binance spot as proxy — replaced with the actual
execution exchange to ensure MFE/MAE/SL/TP replay uses the correct OHLCV.

Rules:
- exchange  = "bybit"
- market_type = "linear"
- symbol    = "ETHUSDT"
- timeframe = "1m"
- ohlcv_source = "bybit_linear_ethusdt"
- candle_source_verified = 1

If Bybit fetch fails:
  - candle_source_verified = 0
  - mark degraded in get_status()
  - do NOT silently fall back to Binance spot

Read-only relative to MOS intelligence: candles are stored for
replay and MFE/MAE calculation only — never fed to StateEngine,
ExecutionTimingEngine, SignalCluster, or trading logic.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from engine.research_logger import DB_PATH, get_db_connection

log = logging.getLogger(__name__)


# ── Execution instrument constants ───────────────────────────────────────────
OHLCV_ENABLED        = True
OHLCV_EXCHANGE       = "bybit"
OHLCV_MARKET_TYPE    = "linear"
OHLCV_SYMBOL         = "ETHUSDT"
OHLCV_TIMEFRAME      = "1m"
OHLCV_SOURCE_LABEL   = "bybit_linear_ethusdt"
OHLCV_POLL_INTERVAL_SEC = 12

BYBIT_KLINES_URL = "https://api.bybit.com/v5/market/kline"

# Bybit returns klines newest-first; fetch last 1000 (max allowed)
BYBIT_FETCH_LIMIT = 1000

# ── Schema migration helper ───────────────────────────────────────────────────

def _add_ohlcv_column_if_missing(cursor, column: str, col_def: str):
    cursor.execute("PRAGMA table_info(ohlcv_candles)")
    existing = {row[1] for row in cursor.fetchall()}
    if column not in existing:
        cursor.execute(f"ALTER TABLE ohlcv_candles ADD COLUMN {column} {col_def}")


def ensure_ohlcv_schema(db_path: str = DB_PATH):
    """Create / migrate ohlcv_candles table with execution instrument columns."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    # Base table (backward-compatible, minimal columns for first-time create)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS ohlcv_candles (
            exchange TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            timestamp_utc REAL NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            quote_volume REAL,
            trade_count INTEGER,
            source_latency_ms REAL,
            created_at_utc REAL,
            UNIQUE(exchange, symbol, timeframe, timestamp_utc)
        )
        """
    )

    # Idempotent migrations — add new execution-instrument columns
    _add_ohlcv_column_if_missing(cursor, "market_type",           "TEXT")
    _add_ohlcv_column_if_missing(cursor, "ohlcv_source",          "TEXT")
    _add_ohlcv_column_if_missing(cursor, "candle_source_verified", "INTEGER DEFAULT 0")

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ohlcv_candles_lookup
        ON ohlcv_candles(symbol, timeframe, timestamp_utc)
        """
    )
    conn.commit()
    conn.close()


class OhlcvCollector:
    """Async Bybit linear ETHUSDT 1m OHLCV collector with SQLite UPSERT.

    Switched from Binance spot (proxy) to Bybit linear (execution instrument)
    so that MFE/MAE/replay outcome uses the actual futures candles.
    """

    def __init__(
        self,
        db_path: str = DB_PATH,
        exchange: str = OHLCV_EXCHANGE,
        market_type: str = OHLCV_MARKET_TYPE,
        symbol: str = OHLCV_SYMBOL,
        timeframe: str = OHLCV_TIMEFRAME,
        ohlcv_source: str = OHLCV_SOURCE_LABEL,
        poll_interval_sec: int = OHLCV_POLL_INTERVAL_SEC,
    ):
        self.db_path         = db_path
        self.exchange        = exchange
        self.market_type     = market_type
        self.symbol          = symbol
        self.timeframe       = timeframe
        self.ohlcv_source    = ohlcv_source
        self.poll_interval_sec = poll_interval_sec
        self.enabled         = OHLCV_ENABLED

        self._task: asyncio.Task | None = None
        self._client: httpx.AsyncClient | None = None
        self._last_error: str = ""
        self._last_fetch_ts: float = 0.0
        self._last_inserted_ts: float = 0.0
        self._consecutive_errors: int = 0

    async def start(self):
        if not self.enabled:
            return
        ensure_ohlcv_schema(self.db_path)
        self._client = httpx.AsyncClient(timeout=10.0)
        self._task = asyncio.create_task(self._loop())
        log.info(
            "OHLCV collector started: %s/%s %s %s (source=%s)",
            self.exchange, self.market_type, self.symbol, self.timeframe,
            self.ohlcv_source,
        )

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()
            self._client = None
        log.info("OHLCV collector stopped")

    async def _loop(self):
        while True:
            try:
                await self.collect_once()
                self._consecutive_errors = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = str(exc)
                self._consecutive_errors += 1
                log.warning(
                    "OHLCV collector update failed (error #%d): %s",
                    self._consecutive_errors, exc
                )
                # Strictly no Binance fallback — mark degraded only
                if self._consecutive_errors == 1:
                    log.error(
                        "OHLCV DEGRADED: Bybit linear fetch failed. "
                        "execution_ohlcv_status=UNAVAILABLE. "
                        "Will NOT fall back to Binance spot."
                    )
            await asyncio.sleep(self.poll_interval_sec)

    async def collect_once(self):
        """Fetch Bybit linear ETHUSDT 1m candles and upsert into ohlcv_candles."""
        if not self._client:
            self._client = httpx.AsyncClient(timeout=10.0)

        started = time.time()
        response = await self._client.get(
            BYBIT_KLINES_URL,
            params={
                "category": "linear",
                "symbol":   self.symbol,
                "interval": "1",           # Bybit uses "1" for 1m
                "limit":    str(BYBIT_FETCH_LIMIT),
            },
        )
        response.raise_for_status()
        latency_ms = round((time.time() - started) * 1000, 1)

        data = response.json()
        ret_code = data.get("retCode", -1)
        if ret_code != 0:
            raise RuntimeError(
                f"Bybit klines retCode={ret_code} retMsg={data.get('retMsg')}"
            )

        raw_rows = data.get("result", {}).get("list", [])
        # Bybit returns newest-first; reverse to oldest-first
        raw_rows = list(reversed(raw_rows))

        candles = [
            self._parse_bybit_kline(row, latency_ms)
            for row in raw_rows
        ]
        await asyncio.to_thread(self._upsert_candles, candles)
        self._last_fetch_ts = time.time()
        if candles:
            self._last_inserted_ts = max(c["timestamp_utc"] for c in candles)
        self._last_error = ""

    def _parse_bybit_kline(self, row: list, latency_ms: float) -> Dict[str, Any]:
        """
        Bybit kline format (v5):
        [startTime(ms), open, high, low, close, volume, turnover]
        """
        return {
            "exchange":              self.exchange,
            "market_type":           self.market_type,
            "symbol":                self.symbol,
            "timeframe":             self.timeframe,
            "ohlcv_source":          self.ohlcv_source,
            "candle_source_verified": 1,
            "timestamp_utc":         float(row[0]) / 1000.0,
            "open":                  float(row[1]),
            "high":                  float(row[2]),
            "low":                   float(row[3]),
            "close":                 float(row[4]),
            "volume":                float(row[5]),
            "quote_volume":          float(row[6]) if len(row) > 6 else None,
            "trade_count":           None,         # Bybit v5 klines don't include trade_count
            "source_latency_ms":     latency_ms,
            "created_at_utc":        time.time(),
        }

    def _upsert_candles(self, candles: List[Dict[str, Any]]):
        if not candles:
            return
        ensure_ohlcv_schema(self.db_path)
        conn = get_db_connection(self.db_path)
        cursor = conn.cursor()
        cursor.executemany(
            """
            INSERT INTO ohlcv_candles (
                exchange, market_type, symbol, timeframe,
                ohlcv_source, candle_source_verified,
                timestamp_utc,
                open, high, low, close, volume, quote_volume, trade_count,
                source_latency_ms, created_at_utc
            ) VALUES (
                :exchange, :market_type, :symbol, :timeframe,
                :ohlcv_source, :candle_source_verified,
                :timestamp_utc,
                :open, :high, :low, :close, :volume, :quote_volume, :trade_count,
                :source_latency_ms, :created_at_utc
            )
            ON CONFLICT(exchange, symbol, timeframe, timestamp_utc) DO UPDATE SET
                market_type           = excluded.market_type,
                ohlcv_source          = excluded.ohlcv_source,
                candle_source_verified = excluded.candle_source_verified,
                open                  = excluded.open,
                high                  = excluded.high,
                low                   = excluded.low,
                close                 = excluded.close,
                volume                = excluded.volume,
                quote_volume          = excluded.quote_volume,
                source_latency_ms     = excluded.source_latency_ms,
                created_at_utc        = excluded.created_at_utc
            """,
            candles,
        )
        conn.commit()
        conn.close()

        try:
            import os
            from engine.paper_trade_engine import PaperTradeEngine
            manual_db_path = os.path.abspath(os.path.join(os.path.dirname(self.db_path), 'mos_manual.db'))
            pte = PaperTradeEngine.get_instance(manual_db_path)
            pte.update_open_trades_from_db(self.db_path)
        except Exception as exc:
            log.error(f"PaperTradeEngine update_trades failed: {exc}")

    def get_latest_close(self, db_path: Optional[str] = None) -> Optional[float]:
        """Read latest Bybit linear close from ohlcv_candles.
        Returns None if unavailable (use for sync check).
        """
        try:
            conn = get_db_connection(db_path or self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT close FROM ohlcv_candles
                WHERE exchange = ? AND symbol = ? AND timeframe = ?
                  AND candle_source_verified = 1
                ORDER BY timestamp_utc DESC LIMIT 1
                """,
                (self.exchange, self.symbol, self.timeframe),
            )
            row = cursor.fetchone()
            conn.close()
            return float(row[0]) if row else None
        except Exception as exc:
            log.debug("OhlcvCollector.get_latest_close error: %s", exc)
            return None

    def get_status(self) -> Dict[str, Any]:
        degraded = bool(self._consecutive_errors > 0)
        return {
            "ohlcv_enabled":          self.enabled,
            "ohlcv_exchange":         self.exchange,
            "ohlcv_market_type":      self.market_type,
            "ohlcv_symbol":           self.symbol,
            "ohlcv_timeframe":        self.timeframe,
            "ohlcv_source":           self.ohlcv_source,
            "ohlcv_poll_interval_sec": self.poll_interval_sec,
            "last_fetch_ts":          self._last_fetch_ts,
            "latest_ohlcv_ts":        self._last_inserted_ts,
            "last_error":             self._last_error,
            "consecutive_errors":     self._consecutive_errors,
            "execution_ohlcv_status": "UNAVAILABLE" if degraded else "OK",
            "candle_source_verified": 0 if degraded else 1,
            # Transparency fields
            "ohlcv_source_type":      "execution",
            "ohlcv_source_label":     self.ohlcv_source,
            "ohlcv_note": (
                "v2: Bybit linear ETHUSDT 1m used as execution OHLCV. "
                "MFE/MAE/SL/TP replay uses futures candles, not spot proxy."
            ),
        }
