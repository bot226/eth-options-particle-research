"""HistoryDB — SQLite-based historical data storage.

Replaces history.json for multi-exchange architecture.
Stores snapshots, per-exchange data, and OI history.
Supports automatic migration from legacy history.json on first launch.
"""

import os
import json
import time
import math
import sqlite3
import logging
from typing import Optional

log = logging.getLogger(__name__)

_DB_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
_DB_PATH = os.path.join(_DB_DIR, "history.db")
_LEGACY_JSON = os.path.join(_DB_DIR, "history.json")


class HistoryDB:
    """SQLite storage for MOS historical data."""

    def __init__(self, db_path: str = _DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()
        self._migrate_legacy()

    def _init_db(self):
        """Create tables if they don't exist."""
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")

        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                oi_json TEXT,
                pdf_json TEXT,
                gex_json TEXT,
                term_structure_json TEXT,
                exchange_data_json TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(ts);

            CREATE TABLE IF NOT EXISTS oi_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                symbol TEXT NOT NULL,
                oi REAL NOT NULL,
                exchange TEXT DEFAULT 'bybit'
            );
            CREATE INDEX IF NOT EXISTS idx_oi_ts ON oi_history(ts);
            CREATE INDEX IF NOT EXISTS idx_oi_symbol ON oi_history(symbol);

            CREATE TABLE IF NOT EXISTS option_contract_snapshots (
                snapshot_id INTEGER NOT NULL,
                ts REAL NOT NULL,
                exchange TEXT NOT NULL,
                contract_id TEXT NOT NULL,
                source_symbol TEXT,
                expiry TEXT,
                strike REAL,
                option_type TEXT,
                oi REAL,
                volume_24h REAL,
                mark_iv REAL,
                bid_iv REAL,
                ask_iv REAL,
                delta REAL,
                gamma REAL,
                vega REAL,
                theta REAL,
                mark_price REAL,
                underlying_price REAL,
                exchange_sources_json TEXT NOT NULL DEFAULT '[]',
                PRIMARY KEY (snapshot_id, exchange, contract_id),
                FOREIGN KEY (snapshot_id) REFERENCES snapshots(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_contract_snapshots_ts
                ON option_contract_snapshots(ts);
            CREATE INDEX IF NOT EXISTS idx_contract_snapshots_contract
                ON option_contract_snapshots(exchange, contract_id, ts);
        """)
        self._conn.commit()
        log.info("HistoryDB initialized at %s", self.db_path)

    def _migrate_legacy(self):
        """Migrate data from legacy history.json on first launch."""
        if not os.path.exists(_LEGACY_JSON):
            return

        # Check if we already have data
        row = self._conn.execute(
            "SELECT COUNT(*) FROM snapshots"
        ).fetchone()
        if row and row[0] > 0:
            log.info("HistoryDB already has data, skipping legacy migration")
            return

        try:
            with open(_LEGACY_JSON, 'r') as f:
                history = json.load(f)

            if not isinstance(history, list):
                return

            count = 0
            for snap in history:
                ts = snap.get("ts", 0)
                if ts <= 0:
                    continue

                self._conn.execute(
                    """INSERT INTO snapshots (ts, oi_json, pdf_json, gex_json, term_structure_json)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        ts,
                        json.dumps(snap.get("oi", {})),
                        json.dumps(snap.get("pdf", {})),
                        json.dumps(snap.get("gex", {})),
                        json.dumps(snap.get("term_structure", {})),
                    )
                )
                count += 1

            self._conn.commit()
            log.info("Migrated %d snapshots from legacy history.json", count)

            # Rename legacy file as backup
            backup_path = _LEGACY_JSON + ".bak"
            os.rename(_LEGACY_JSON, backup_path)
            log.info("Renamed legacy history.json to %s", backup_path)

        except Exception as e:
            log.error("Legacy migration failed: %s", e)

    # ── Snapshot operations ──────────────────────────────────────────

    @staticmethod
    def _finite_or_none(value):
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None

    @classmethod
    def _contract_rows(cls, snapshot_id: int, ts: float, contract_data) -> list[tuple]:
        """Normalize exchange contract observations for additive research storage."""
        if not contract_data:
            return []
        items = contract_data.items() if isinstance(contract_data, dict) else enumerate(contract_data)
        rows = []
        seen = set()
        for key, ticker in items:
            if not isinstance(ticker, dict):
                continue
            contract_id = str(
                ticker.get("canonical_id") or ticker.get("symbol") or key or ""
            ).strip()
            if not contract_id:
                continue
            exchange = str(ticker.get("exchange") or "bybit").strip().lower()
            unique_key = (exchange, contract_id)
            if unique_key in seen:
                continue
            seen.add(unique_key)
            option_type = str(ticker.get("type") or "").upper()
            option_type = {"CALL": "C", "PUT": "P"}.get(option_type, option_type)
            if option_type not in {"C", "P"}:
                option_type = None
            sources = ticker.get("exchange_sources")
            if not isinstance(sources, list):
                sources = [exchange] if exchange else []
            rows.append(
                (
                    snapshot_id,
                    ts,
                    exchange,
                    contract_id,
                    ticker.get("symbol"),
                    ticker.get("expiry"),
                    cls._finite_or_none(ticker.get("strike")),
                    option_type,
                    cls._finite_or_none(ticker.get("oi")),
                    cls._finite_or_none(ticker.get("volume", ticker.get("volume_24h"))),
                    cls._finite_or_none(ticker.get("markIv", ticker.get("mark_iv"))),
                    cls._finite_or_none(ticker.get("bidIv", ticker.get("bid_iv"))),
                    cls._finite_or_none(ticker.get("askIv", ticker.get("ask_iv"))),
                    cls._finite_or_none(ticker.get("delta")),
                    cls._finite_or_none(ticker.get("gamma")),
                    cls._finite_or_none(ticker.get("vega")),
                    cls._finite_or_none(ticker.get("theta")),
                    cls._finite_or_none(ticker.get("markPrice", ticker.get("mark_price"))),
                    cls._finite_or_none(
                        ticker.get("underlyingPrice", ticker.get("underlying_price"))
                    ),
                    json.dumps(sorted(set(str(source) for source in sources))),
                )
            )
        return rows

    def save_snapshot(self, ts: float, oi_data: dict, pdf_data: dict = None,
                      gex_data: dict = None, term_data: dict = None,
                      exchange_data: dict = None, contract_data=None):
        """Save one structural snapshot and its optional per-contract observations."""
        try:
            cursor = self._conn.execute(
                """INSERT INTO snapshots
                   (ts, oi_json, pdf_json, gex_json, term_structure_json, exchange_data_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    ts,
                    json.dumps(oi_data) if oi_data else "{}",
                    json.dumps(pdf_data) if pdf_data else "{}",
                    json.dumps(gex_data) if gex_data else "{}",
                    json.dumps(term_data) if term_data else "{}",
                    json.dumps(exchange_data) if exchange_data else "{}",
                )
            )
            snapshot_id = int(cursor.lastrowid)
            contract_rows = self._contract_rows(snapshot_id, ts, contract_data)
            if contract_rows:
                self._conn.executemany(
                    """
                    INSERT INTO option_contract_snapshots (
                        snapshot_id, ts, exchange, contract_id, source_symbol,
                        expiry, strike, option_type, oi, volume_24h, mark_iv,
                        bid_iv, ask_iv, delta, gamma, vega, theta, mark_price,
                        underlying_price, exchange_sources_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    contract_rows,
                )
            self._conn.commit()
            return snapshot_id
        except Exception as e:
            self._conn.rollback()
            log.error("Failed to save snapshot: %s", e)
            return None

    def get_snapshot_at(self, target_ts: float) -> Optional[dict]:
        """Get nearest snapshot to target timestamp."""
        try:
            row = self._conn.execute(
                """SELECT ts, oi_json, pdf_json, gex_json, term_structure_json,
                          exchange_data_json
                   FROM snapshots
                   ORDER BY ABS(ts - ?) ASC
                   LIMIT 1""",
                (target_ts,)
            ).fetchone()

            if not row:
                return None

            return {
                "ts": row[0],
                "oi": json.loads(row[1] or "{}"),
                "pdf": json.loads(row[2] or "{}"),
                "gex": json.loads(row[3] or "{}"),
                "term_structure": json.loads(row[4] or "{}"),
                "exchange_data": json.loads(row[5] or "{}"),
            }
        except Exception as e:
            log.error("Failed to get snapshot: %s", e)
            return None

    def get_all_snapshots_since(self, since_ts: float) -> list[dict]:
        """Get all snapshots since a timestamp (for history views)."""
        try:
            rows = self._conn.execute(
                """SELECT ts, oi_json, pdf_json, gex_json, term_structure_json
                   FROM snapshots
                   WHERE ts >= ?
                   ORDER BY ts ASC""",
                (since_ts,)
            ).fetchall()

            return [
                {
                    "ts": r[0],
                    "oi": json.loads(r[1] or "{}"),
                    "pdf": json.loads(r[2] or "{}"),
                    "gex": json.loads(r[3] or "{}"),
                    "term_structure": json.loads(r[4] or "{}"),
                }
                for r in rows
            ]
        except Exception as e:
            log.error("Failed to get snapshots: %s", e)
            return []

    # ── OI delta ─────────────────────────────────────────────────────

    def get_oi_delta(self, symbol: str, hours_ago: int = 24) -> float:
        """Get OI change percentage for a symbol over hours_ago period."""
        try:
            target_ts = time.time() - (hours_ago * 3600)

            # Get oldest available OI near target
            row = self._conn.execute(
                """SELECT oi FROM snapshots
                   WHERE ts <= ? AND oi_json LIKE ?
                   ORDER BY ts DESC LIMIT 1""",
                (target_ts + 300, f'%"{symbol}"%')
            ).fetchone()

            if not row:
                return 0.0

            # Parse OI from snapshot
            snap = self.get_snapshot_at(target_ts)
            if not snap:
                return 0.0

            old_oi = snap["oi"].get(symbol, 0)
            # Current OI would need to come from the caller
            return 0.0  # Will be computed by DataManager using current tickers

        except Exception as e:
            log.error("Failed to get OI delta: %s", e)
            return 0.0

    # ── Cleanup ──────────────────────────────────────────────────────

    def cleanup_old(self, max_hours: int = 25):
        """Remove snapshots older than max_hours."""
        cutoff = time.time() - (max_hours * 3600)
        try:
            result = self._conn.execute(
                "DELETE FROM snapshots WHERE ts < ?", (cutoff,)
            )
            self._conn.commit()
            if result.rowcount > 0:
                log.info("Cleaned up %d old snapshots", result.rowcount)
        except Exception as e:
            log.error("Cleanup failed: %s", e)

    def close(self):
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
