"""Replay outcome analysis for MOS events.

This module is a read-only research layer over persisted snapshots, events,
future labels, and OHLCV candles. It never feeds StateEngine, execution,
signal clustering, event generation, or trading logic.
"""

import csv
import io
import json
import math
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional


EXPAND_15M_RANGE_PCT = 0.50
EXPAND_30M_RANGE_PCT = 0.75
CONTINUE_RETURN_PCT = 0.20
REVERSE_RETURN_PCT = -0.20
LEVEL_NEAR_PCT = 0.35
LEVEL_BREAK_PCT = 0.12
LEVEL_REJECT_PCT = 0.25
RANGE_NEAR_PCT = 0.15
NO_REACTION_RANGE_30M_PCT = 0.25
WEAK_MOVE_RETURN_15M_PCT = 0.12
WEAK_MOVE_RETURN_30M_PCT = 0.18
DIRECTIONAL_RETURN_15M_PCT = 0.20
DIRECTIONAL_RETURN_30M_PCT = 0.30
LEVEL_BREAK_CLOSE_BUFFER_PCT = 0.05
FALSE_BREAK_PIERCE_BUFFER_PCT = 0.10
SUPPORT_DEFENSE_RETURN_15M_PCT = 0.15
SUPPORT_DEFENSE_MAX_UP_15M_PCT = 0.25
RESISTANCE_REJECTION_RETURN_15M_PCT = -0.15
RESISTANCE_REJECTION_MAX_DOWN_15M_PCT = -0.25
NO_REACTION_RANGE_15M_PCT = 0.15
MID_RANGE_EXPANSION_30M_PCT = 0.45
EXPANSION_RANGE_30M_PCT = 0.60
UNKNOWN_FALLBACK_RANGE_30M_PCT = 0.35


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        if math.isnan(out) or math.isinf(out):
            return default
        return out
    except (TypeError, ValueError):
        return default


def utc_iso(ts: Any = None) -> str:
    try:
        if ts is None:
            return datetime.utcnow().isoformat() + "Z"
        return datetime.utcfromtimestamp(float(ts)).isoformat() + "Z"
    except Exception:
        return datetime.utcnow().isoformat() + "Z"


def _pct(end: float, start: float) -> float:
    return ((end / start) - 1.0) * 100.0 if start > 0 and end > 0 else 0.0


def _realized_vol(prices: List[float]) -> float:
    returns = []
    for idx in range(1, len(prices)):
        prev = prices[idx - 1]
        current = prices[idx]
        if prev > 0 and current > 0:
            returns.append((current / prev) - 1.0)
    if len(returns) < 2:
        return 0.0
    mean_ret = sum(returns) / len(returns)
    var = sum((ret - mean_ret) ** 2 for ret in returns) / (len(returns) - 1)
    return math.sqrt(var) * math.sqrt(2102400) * 100.0


class ReplayOutcomeEngine:
    """Compute deterministic event outcomes and level reactions from history."""

    @staticmethod
    def ensure_schema(conn) -> None:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS worker_state (
                key TEXT PRIMARY KEY,
                value_text TEXT,
                updated_at_utc TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS event_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER,
                snapshot_id TEXT,
                snapshot_sequence_id INTEGER,
                event_type TEXT NOT NULL,
                event_timestamp_utc TEXT NOT NULL,
                spot_price REAL,
                future_return_5m REAL,
                future_return_15m REAL,
                future_return_30m REAL,
                future_max_up_5m REAL,
                future_max_down_5m REAL,
                future_max_up_15m REAL,
                future_max_down_15m REAL,
                future_max_up_30m REAL,
                future_max_down_30m REAL,
                future_range_5m REAL,
                future_range_15m REAL,
                future_range_30m REAL,
                future_realized_vol_15m REAL,
                future_realized_vol_30m REAL,
                did_expand_15m INTEGER,
                did_expand_30m INTEGER,
                did_continue_direction_15m INTEGER,
                did_reverse_15m INTEGER,
                outcome_label TEXT,
                created_at_utc TEXT NOT NULL,
                UNIQUE(event_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS event_level_reactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER,
                outcome_id INTEGER,
                snapshot_id TEXT,
                snapshot_sequence_id INTEGER,
                event_type TEXT NOT NULL,
                event_timestamp_utc TEXT NOT NULL,
                spot_price REAL,
                level_type TEXT,
                level_price REAL,
                distance_pct REAL,
                level_side TEXT,
                reaction_label TEXT,
                price_zone_25 REAL,
                price_zone_50 REAL,
                price_zone_100 REAL,
                range_high_15m REAL,
                range_low_15m REAL,
                range_high_1h REAL,
                range_low_1h REAL,
                range_position_15m REAL,
                range_position_1h REAL,
                near_range_high INTEGER,
                near_range_low INTEGER,
                range_1h_partial INTEGER,
                reaction_context TEXT,
                level_result TEXT,
                nearest_level REAL,
                nearest_level_type TEXT,
                distance_to_level_pct REAL,
                future_return_5m REAL,
                future_return_15m REAL,
                future_return_30m REAL,
                future_max_up_15m REAL,
                future_max_down_15m REAL,
                future_range_15m REAL,
                future_range_30m REAL,
                max_high_30m REAL,
                min_low_30m REAL,
                last_close_30m REAL,
                context_notes_json TEXT,
                classification_reason TEXT,
                reaction_skip_reason TEXT,
                source TEXT,
                confidence TEXT,
                deribit_status TEXT,
                is_synthetic INTEGER DEFAULT 0,
                created_at_utc TEXT NOT NULL,
                UNIQUE(event_id)
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_event_outcomes_type ON event_outcomes(event_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_event_outcomes_seq ON event_outcomes(snapshot_sequence_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_event_outcomes_label ON event_outcomes(outcome_label)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_event_level_reactions_type ON event_level_reactions(event_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_event_level_reactions_label ON event_level_reactions(reaction_label)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_event_level_reactions_seq ON event_level_reactions(snapshot_sequence_id)")
        cursor.execute("PRAGMA table_info(event_level_reactions)")
        existing_cols = [row[1] for row in cursor.fetchall()]
        migrations = {
            "price_zone_25": "REAL",
            "price_zone_50": "REAL",
            "price_zone_100": "REAL",
            "range_high_15m": "REAL",
            "range_low_15m": "REAL",
            "range_high_1h": "REAL",
            "range_low_1h": "REAL",
            "range_position_15m": "REAL",
            "range_position_1h": "REAL",
            "near_range_high": "INTEGER",
            "near_range_low": "INTEGER",
            "range_1h_partial": "INTEGER",
            "reaction_context": "TEXT",
            "level_result": "TEXT",
            "nearest_level": "REAL",
            "nearest_level_type": "TEXT",
            "distance_to_level_pct": "REAL",
            "future_return_5m": "REAL",
            "future_return_30m": "REAL",
            "future_max_up_15m": "REAL",
            "future_max_down_15m": "REAL",
            "future_range_15m": "REAL",
            "context_notes_json": "TEXT",
            "classification_reason": "TEXT",
            "reaction_skip_reason": "TEXT",
            "source": "TEXT",
            "confidence": "TEXT",
            "deribit_status": "TEXT",
            "is_synthetic": "INTEGER",
        }
        for col, col_type in migrations.items():
            if col not in existing_cols:
                cursor.execute(f"ALTER TABLE event_level_reactions ADD COLUMN {col} {col_type}")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_event_level_reactions_zone_50 ON event_level_reactions(price_zone_50)")
        conn.commit()

    @staticmethod
    def _decode_payload(value: Any) -> Dict[str, Any]:
        if not value:
            return {}
        try:
            payload = json.loads(value)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _price_at(cursor, target_ts: float, window_sec: float = 120.0) -> Optional[float]:
        cursor.execute(
            """
            SELECT spot_price
            FROM snapshots
            WHERE timestamp_utc BETWEEN ? AND ?
            ORDER BY ABS(timestamp_utc - ?) ASC
            LIMIT 1
            """,
            (target_ts - window_sec, target_ts + window_sec, target_ts),
        )
        row = cursor.fetchone()
        return safe_float(row["spot_price"] if hasattr(row, "keys") else row[0], 0.0) if row else None

    @staticmethod
    def _price_series(cursor, start_ts: float, end_ts: float) -> List[float]:
        cursor.execute(
            """
            SELECT spot_price
            FROM snapshots
            WHERE timestamp_utc BETWEEN ? AND ?
            ORDER BY timestamp_utc ASC
            """,
            (start_ts, end_ts),
        )
        return [safe_float(row["spot_price"] if hasattr(row, "keys") else row[0]) for row in cursor.fetchall()]

    @staticmethod
    def _latest_ohlcv_ts(cursor) -> float:
        cursor.execute(
            """
            SELECT MAX(timestamp_utc) AS latest_ts
            FROM ohlcv_candles
            WHERE symbol = 'BTCUSDT'
              AND timeframe = '1m'
            """
        )
        row = cursor.fetchone()
        return safe_float(row["latest_ts"] if hasattr(row, "keys") else row[0])

    @staticmethod
    def _ohlcv_price_at(cursor, target_ts: float, window_sec: float = 90.0) -> Optional[float]:
        cursor.execute(
            """
            SELECT close
            FROM ohlcv_candles
            WHERE symbol = 'BTCUSDT'
              AND timeframe = '1m'
              AND timestamp_utc BETWEEN ? AND ?
            ORDER BY ABS(timestamp_utc - ?) ASC
            LIMIT 1
            """,
            (target_ts - window_sec, target_ts + window_sec, target_ts),
        )
        row = cursor.fetchone()
        return safe_float(row["close"] if hasattr(row, "keys") else row[0], 0.0) if row else None

    @classmethod
    def _ohlcv_window_metrics(cls, cursor, event_ts: float, spot_price: float, minutes: int) -> Optional[Dict[str, float]]:
        end_ts = event_ts + minutes * 60
        end_price = cls._ohlcv_price_at(cursor, end_ts)
        candles = cls._ohlcv_window(cursor, event_ts, end_ts)
        if not end_price or len(candles) < 2 or spot_price <= 0:
            return None

        highs = [safe_float(row.get("high")) for row in candles]
        lows = [safe_float(row.get("low")) for row in candles]
        closes = [safe_float(row.get("close")) for row in candles]
        max_up = _pct(max(highs), spot_price)
        max_down = _pct(min(lows), spot_price)
        return {
            "return": _pct(end_price, spot_price),
            "max_up": max_up,
            "max_down": max_down,
            "range": max_up - max_down,
            "realized_vol": _realized_vol(closes),
            "max_high": max(highs),
            "min_low": min(lows),
            "last_close": closes[-1],
            "ohlcv_candles": len(candles),
        }

    @classmethod
    def _window_metrics(cls, cursor, event_ts: float, spot_price: float, minutes: int) -> Optional[Dict[str, float]]:
        end_ts = event_ts + minutes * 60
        end_price = cls._price_at(cursor, end_ts)
        prices = cls._price_series(cursor, event_ts, end_ts)
        if not end_price or len(prices) < 2 or spot_price <= 0:
            return None
        max_up = _pct(max(prices), spot_price)
        max_down = _pct(min(prices), spot_price)
        return {
            "return": _pct(end_price, spot_price),
            "max_up": max_up,
            "max_down": max_down,
            "range": max_up - max_down,
            "realized_vol": _realized_vol(prices),
        }

    @classmethod
    def _infer_event_direction(cls, event: Dict[str, Any], snapshot: Dict[str, Any]) -> int:
        payload = cls._decode_payload(event.get("event_payload_json"))
        for key in ("direction", "flow_direction", "side"):
            value = str(payload.get(key, "")).upper()
            if "BUY" in value or "UP" in value or "BULL" in value:
                return 1
            if "SELL" in value or "DOWN" in value or "BEAR" in value:
                return -1

        flow = safe_float(event.get("synthetic_flow_pressure"), safe_float(snapshot.get("synthetic_flow_pressure")))
        if flow >= 10:
            return 1
        if flow <= -10:
            return -1

        event_type = str(event.get("event_type", ""))
        if event_type in {"GAMMA_COLLAPSE", "GAMMA_WEAKENING", "VOLATILITY_EXPANSION", "PINNING_BREAK"}:
            return 0
        return 0

    @staticmethod
    def _outcome_label(metrics_15m: Dict[str, float], metrics_30m: Dict[str, float],
                       direction: int, did_continue: int, did_reverse: int) -> str:
        did_expand = metrics_15m["range"] >= EXPAND_15M_RANGE_PCT or metrics_30m["range"] >= EXPAND_30M_RANGE_PCT
        if did_expand and did_continue:
            return "EXPANSION_CONTINUATION"
        if did_expand and did_reverse:
            return "EXPANSION_REVERSAL"
        if did_expand:
            return "EXPANSION_NO_DIRECTION"
        if did_continue:
            return "DIRECTIONAL_CONTINUATION"
        if did_reverse:
            return "DIRECTIONAL_REVERSAL"
        if direction == 0:
            return "NO_DIRECTIONAL_OUTCOME"
        return "NO_EXPANSION_FOLLOW_THROUGH"

    @classmethod
    def build_event_outcome(cls, cursor, event: Dict[str, Any], snapshot: Dict[str, Any]) -> Optional[tuple]:
        event_ts = safe_float(event.get("timestamp_utc"))
        spot_price = safe_float(event.get("spot_price"), safe_float(snapshot.get("spot_price")))
        if event_ts <= 0 or spot_price <= 0:
            return None

        metrics_5m = cls._ohlcv_window_metrics(cursor, event_ts, spot_price, 5)
        metrics_15m = cls._ohlcv_window_metrics(cursor, event_ts, spot_price, 15)
        metrics_30m = cls._ohlcv_window_metrics(cursor, event_ts, spot_price, 30)
        if not metrics_5m or not metrics_15m or not metrics_30m:
            return None

        direction = cls._infer_event_direction(event, snapshot)
        directional_15m = metrics_15m["return"] * direction
        did_continue = 1 if direction != 0 and directional_15m >= CONTINUE_RETURN_PCT else 0
        did_reverse = 1 if direction != 0 and directional_15m <= REVERSE_RETURN_PCT else 0
        outcome_label = cls._outcome_label(metrics_15m, metrics_30m, direction, did_continue, did_reverse)

        return (
            event.get("id"),
            snapshot.get("snapshot_id"),
            event.get("snapshot_sequence_id") or snapshot.get("snapshot_sequence_id"),
            event.get("event_type"),
            utc_iso(event_ts),
            spot_price,
            metrics_5m["return"],
            metrics_15m["return"],
            metrics_30m["return"],
            metrics_5m["max_up"],
            metrics_5m["max_down"],
            metrics_15m["max_up"],
            metrics_15m["max_down"],
            metrics_30m["max_up"],
            metrics_30m["max_down"],
            metrics_5m["range"],
            metrics_15m["range"],
            metrics_30m["range"],
            metrics_15m["realized_vol"],
            metrics_30m["realized_vol"],
            1 if metrics_15m["range"] >= EXPAND_15M_RANGE_PCT else 0,
            1 if metrics_30m["range"] >= EXPAND_30M_RANGE_PCT else 0,
            did_continue,
            did_reverse,
            outcome_label,
            utc_iso(),
        )

    @classmethod
    def backfill_event_outcomes(cls, conn, limit: int = 1000) -> Dict[str, Any]:
        cls.ensure_schema(conn)
        cursor = conn.cursor()
        latest_ohlcv_ts = cls._latest_ohlcv_ts(cursor)
        if latest_ohlcv_ts <= 0:
            return {
                "status": "ok",
                "candidate_events": 0,
                "inserted": 0,
                "level_reactions_inserted": 0,
                "skipped": 0,
                "reason": "no_ohlcv_candles",
                "note": "Replay outcome backfill writes derived research rows only; live MOS logic is unchanged.",
            }
        cutoff = latest_ohlcv_ts - 1800
        cursor.execute(
            """
            SELECT e.*, s.snapshot_id, s.spot_price AS snapshot_spot_price,
                   s.synthetic_flow_pressure AS snapshot_synthetic_flow_pressure,
                   s.call_wall AS snapshot_call_wall,
                   s.put_wall AS snapshot_put_wall
            FROM events e
            JOIN snapshots s ON s.snapshot_sequence_id = e.snapshot_sequence_id
            LEFT JOIN event_outcomes eo ON eo.event_id = e.id
            WHERE eo.event_id IS NULL
              AND e.timestamp_utc <= ?
            ORDER BY e.timestamp_utc ASC
            LIMIT ?
            """,
            (cutoff, int(limit)),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        inserted = 0
        level_reactions_inserted = 0
        skipped = 0
        skip_reasons: Dict[str, int] = {}
        for event in rows:
            snapshot = {
                "snapshot_id": event.get("snapshot_id"),
                "snapshot_sequence_id": event.get("snapshot_sequence_id"),
                "spot_price": event.get("snapshot_spot_price"),
                "synthetic_flow_pressure": event.get("snapshot_synthetic_flow_pressure"),
                "call_wall": event.get("snapshot_call_wall"),
                "put_wall": event.get("snapshot_put_wall"),
            }
            outcome = cls.build_event_outcome(cursor, event, snapshot)
            if not outcome:
                skipped += 1
                skip_reasons["missing_ohlcv_future_window"] = skip_reasons.get("missing_ohlcv_future_window", 0) + 1
                continue
            cursor.execute(
                """
                INSERT OR IGNORE INTO event_outcomes (
                    event_id, snapshot_id, snapshot_sequence_id,
                    event_type, event_timestamp_utc, spot_price,
                    future_return_5m, future_return_15m, future_return_30m,
                    future_max_up_5m, future_max_down_5m,
                    future_max_up_15m, future_max_down_15m,
                    future_max_up_30m, future_max_down_30m,
                    future_range_5m, future_range_15m, future_range_30m,
                    future_realized_vol_15m, future_realized_vol_30m,
                    did_expand_15m, did_expand_30m,
                    did_continue_direction_15m, did_reverse_15m,
                    outcome_label, created_at_utc
                ) VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                outcome,
            )
            inserted += cursor.rowcount
            cursor.execute("SELECT id FROM event_outcomes WHERE event_id = ?", (event.get("id"),))
            outcome_row = cursor.fetchone()
            outcome_id = outcome_row["id"] if outcome_row and hasattr(outcome_row, "keys") else outcome_row[0] if outcome_row else None
            if outcome_id:
                cursor.execute("SELECT * FROM event_outcomes WHERE id = ?", (outcome_id,))
                persisted_outcome = dict(cursor.fetchone())
                reaction = cls.build_level_reaction(cursor, event, persisted_outcome, snapshot, outcome_id)
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO event_level_reactions (
                        event_id, outcome_id, snapshot_id, snapshot_sequence_id,
                        event_type, event_timestamp_utc, spot_price,
                        level_type, level_price, distance_pct, level_side,
                        reaction_label,
                        price_zone_25, price_zone_50, price_zone_100,
                        range_high_15m, range_low_15m,
                        range_high_1h, range_low_1h,
                        range_position_15m, range_position_1h,
                        near_range_high, near_range_low, range_1h_partial,
                        reaction_context, level_result,
                        nearest_level, nearest_level_type, distance_to_level_pct,
                        future_return_5m, future_return_15m, future_return_30m,
                        future_max_up_15m, future_max_down_15m,
                        future_range_15m, future_range_30m,
                        max_high_30m, min_low_30m, last_close_30m,
                        context_notes_json, classification_reason, reaction_skip_reason,
                        source, confidence, deribit_status, is_synthetic, created_at_utc
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?,
                        ?, ?, ?,
                        ?, ?,
                        ?, ?,
                        ?, ?,
                        ?, ?, ?,
                        ?, ?,
                        ?, ?, ?,
                        ?, ?, ?,
                        ?, ?,
                        ?, ?,
                        ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?, ?, ?
                    )
                    """,
                    reaction,
                )
                level_reactions_inserted += cursor.rowcount
        repaired = cls.backfill_level_reactions(conn, limit=limit)
        conn.commit()
        return {
            "status": "ok",
            "candidate_events": len(rows),
            "inserted": inserted,
            "level_reactions_inserted": level_reactions_inserted,
            "level_reactions_repaired": repaired.get("repaired", 0),
            "skipped": skipped,
            "skip_reasons": skip_reasons,
            "cutoff_timestamp_utc": utc_iso(cutoff),
            "latest_ohlcv_timestamp_utc": utc_iso(latest_ohlcv_ts),
            "note": "Replay outcome backfill writes derived research rows only; live MOS logic is unchanged.",
        }

    @classmethod
    def backfill_level_reactions(cls, conn, limit: int = 1000) -> Dict[str, Any]:
        """Repair or fill persisted level reactions for existing event outcomes."""
        cls.ensure_schema(conn)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT eo.*, e.timestamp_utc AS event_ts_raw, e.event_payload_json,
                   s.call_wall, s.put_wall, s.spot_price AS snapshot_spot_price
            FROM event_outcomes eo
            JOIN events e ON e.id = eo.event_id
            JOIN snapshots s ON s.snapshot_id = eo.snapshot_id
            LEFT JOIN event_level_reactions elr ON elr.event_id = eo.event_id
            WHERE elr.event_id IS NULL
               OR elr.reaction_label IN (
                    'NO_LEVEL_CONTEXT', 'NO_OHLCV_WINDOW',
                    'BREAKOUT_ABOVE_LEVEL', 'BREAKDOWN_BELOW_LEVEL',
                    'REJECTION_FROM_RESISTANCE', 'BOUNCE_FROM_SUPPORT',
                    'LEVEL_TEST_NO_DECISIVE_REACTION', 'FAR_FROM_NEAREST_LEVEL'
               )
               OR elr.price_zone_50 IS NULL
               OR elr.reaction_context IS NULL
               OR elr.reaction_context LIKE 'NEAR_RANGE%'
               OR elr.level_result IS NULL
               OR elr.level_result = 'UNKNOWN'
               OR elr.reaction_label = 'UNKNOWN'
               OR elr.nearest_level IS NULL
               OR elr.distance_to_level_pct IS NULL
               OR elr.level_side IN ('above', 'below')
               OR elr.future_range_15m IS NULL
               OR elr.classification_reason IS NULL
            ORDER BY eo.id ASC
            LIMIT ?
            """,
            (int(limit),),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        repaired = 0
        for row in rows:
            event = {
                "id": row.get("event_id"),
                "timestamp_utc": row.get("event_ts_raw"),
                "event_payload_json": row.get("event_payload_json"),
            }
            snapshot = {
                "call_wall": row.get("call_wall"),
                "put_wall": row.get("put_wall"),
                "spot_price": row.get("snapshot_spot_price"),
            }
            reaction = cls.build_level_reaction(cursor, event, row, row | snapshot, row.get("id"))
            cursor.execute(
                """
                INSERT INTO event_level_reactions (
                    event_id, outcome_id, snapshot_id, snapshot_sequence_id,
                    event_type, event_timestamp_utc, spot_price,
                    level_type, level_price, distance_pct, level_side,
                    reaction_label,
                    price_zone_25, price_zone_50, price_zone_100,
                    range_high_15m, range_low_15m,
                    range_high_1h, range_low_1h,
                    range_position_15m, range_position_1h,
                    near_range_high, near_range_low, range_1h_partial,
                    reaction_context, level_result,
                    nearest_level, nearest_level_type, distance_to_level_pct,
                    future_return_5m, future_return_15m, future_return_30m,
                    future_max_up_15m, future_max_down_15m,
                    future_range_15m, future_range_30m,
                    max_high_30m, min_low_30m, last_close_30m,
                    context_notes_json, classification_reason, reaction_skip_reason,
                    source, confidence, deribit_status, is_synthetic, created_at_utc
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?, ?
                )
                ON CONFLICT(event_id) DO UPDATE SET
                    outcome_id = excluded.outcome_id,
                    snapshot_id = excluded.snapshot_id,
                    snapshot_sequence_id = excluded.snapshot_sequence_id,
                    event_type = excluded.event_type,
                    event_timestamp_utc = excluded.event_timestamp_utc,
                    spot_price = excluded.spot_price,
                    level_type = excluded.level_type,
                    level_price = excluded.level_price,
                    distance_pct = excluded.distance_pct,
                    level_side = excluded.level_side,
                    reaction_label = excluded.reaction_label,
                    price_zone_25 = excluded.price_zone_25,
                    price_zone_50 = excluded.price_zone_50,
                    price_zone_100 = excluded.price_zone_100,
                    range_high_15m = excluded.range_high_15m,
                    range_low_15m = excluded.range_low_15m,
                    range_high_1h = excluded.range_high_1h,
                    range_low_1h = excluded.range_low_1h,
                    range_position_15m = excluded.range_position_15m,
                    range_position_1h = excluded.range_position_1h,
                    near_range_high = excluded.near_range_high,
                    near_range_low = excluded.near_range_low,
                    range_1h_partial = excluded.range_1h_partial,
                    reaction_context = excluded.reaction_context,
                    level_result = excluded.level_result,
                    nearest_level = excluded.nearest_level,
                    nearest_level_type = excluded.nearest_level_type,
                    distance_to_level_pct = excluded.distance_to_level_pct,
                    future_return_5m = excluded.future_return_5m,
                    future_return_15m = excluded.future_return_15m,
                    future_return_30m = excluded.future_return_30m,
                    future_max_up_15m = excluded.future_max_up_15m,
                    future_max_down_15m = excluded.future_max_down_15m,
                    future_range_15m = excluded.future_range_15m,
                    future_range_30m = excluded.future_range_30m,
                    max_high_30m = excluded.max_high_30m,
                    min_low_30m = excluded.min_low_30m,
                    last_close_30m = excluded.last_close_30m,
                    context_notes_json = excluded.context_notes_json,
                    classification_reason = excluded.classification_reason,
                    reaction_skip_reason = excluded.reaction_skip_reason,
                    source = excluded.source,
                    confidence = excluded.confidence,
                    deribit_status = excluded.deribit_status,
                    is_synthetic = excluded.is_synthetic,
                    created_at_utc = excluded.created_at_utc
                """,
                reaction,
            )
            repaired += 1
        conn.commit()
        return {
            "status": "ok",
            "candidates": len(rows),
            "repaired": repaired,
            "note": "Level reaction repair updates derived research rows only; live MOS logic is unchanged.",
        }

    @classmethod
    def backfill_fallback_reactions(cls, conn, limit: int = 1000) -> Dict[str, Any]:
        """Generate synthetic OHLCV fallback reactions for snapshots without events when Deribit is offline."""
        cls.ensure_schema(conn)
        cursor = conn.cursor()
        latest_ohlcv_ts = cls._latest_ohlcv_ts(cursor)
        if latest_ohlcv_ts <= 0:
            return {"inserted": 0, "skipped": 0, "reason": "no_ohlcv_candles"}

        last_processed_ts = 0.0
        cursor.execute("SELECT value_text FROM worker_state WHERE key = 'ohlcv_fallback_last_processed_ts'")
        row = cursor.fetchone()
        if row and row["value_text"]:
            try:
                last_processed_ts = float(row["value_text"])
            except ValueError:
                pass

        cutoff = latest_ohlcv_ts - 60
        cursor.execute(
            """
            SELECT s.*
            FROM snapshots s
            WHERE s.timestamp_utc > ?
              AND s.timestamp_utc <= ?
              AND s.deribit_status IN ('OFFLINE', 'MISSING', 'EMPTY', 'PARSE_ERROR', 'STALE', 'UNKNOWN')
            ORDER BY s.timestamp_utc ASC
            LIMIT ?
            """,
            (last_processed_ts, cutoff, int(limit)),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        inserted = 0
        skipped = 0
        fallback_metrics = {
            "ohlcv_fallback_processed_count": len(rows),
            "ohlcv_fallback_created_count": 0,
            "ohlcv_fallback_deduped_count": 0,
            "ohlcv_fallback_no_reaction_count": 0,
            "ohlcv_fallback_skip_reason_latest": None,
            "ohlcv_fallback_last_processed_ts": last_processed_ts,
            "ohlcv_fallback_latest_snapshot_ts": None,
        }
        
        highest_successful_ts = last_processed_ts
        
        for snapshot in rows:
            # We construct a mock event to pass to _classify_level_reaction
            mock_event = {
                "id": f"fallback_{snapshot['snapshot_id']}",
                "timestamp_utc": snapshot["timestamp_utc"],
                "event_type": "OHLCV_PRICE_ACTION",
                "snapshot_sequence_id": snapshot["snapshot_sequence_id"],
                "event_payload_json": '{"is_synthetic": 1, "fallback_reason": "deribit_offline_snapshot_fallback"}',
            }
            # mock outcome
            mock_outcome = {
                "snapshot_id": snapshot["snapshot_id"],
                "snapshot_sequence_id": snapshot["snapshot_sequence_id"],
                "event_type": "OHLCV_PRICE_ACTION",
                "event_timestamp_utc": utc_iso(snapshot["timestamp_utc"]),
                "spot_price": snapshot["spot_price"],
            }
            
            reaction = cls._classify_level_reaction(cursor, mock_event, mock_outcome, snapshot)
            reaction_label = reaction.get("reaction_label", "UNKNOWN")
            level_side = reaction.get("side", "UNKNOWN")
            level_price = reaction.get("level_price")
            skip_reason = reaction.get("reaction_skip_reason")
            
            # Filter non-structural levels for fallback to reduce noise
            if level_side not in ("SUPPORT", "RESISTANCE") or reaction_label in ("UNKNOWN", "INSUFFICIENT_DATA", "NO_REACTION") or skip_reason == "deduped_recent_reaction":
                fallback_metrics["ohlcv_fallback_no_reaction_count"] += 1
                fallback_metrics["ohlcv_fallback_skip_reason_latest"] = skip_reason or "ignored_non_structural_level"
                if skip_reason == "deduped_recent_reaction":
                    fallback_metrics["ohlcv_fallback_deduped_count"] += 1
                highest_successful_ts = max(highest_successful_ts, snapshot["timestamp_utc"])
                continue

            # Deduplication logic (apply to ALL rows including NO_REACTION)
            rounded_level = round(float(level_price) / 100) * 100 if level_price else 0
            dedupe_cutoff = snapshot["timestamp_utc"] - 300 # 5 min cooldown for directional
            cursor.execute(
                """
                SELECT COUNT(*) as count
                FROM event_level_reactions
                WHERE source = 'ohlcv_only'
                  AND reaction_label = ?
                  AND level_side = ?
                  AND ROUND(level_price / 100) * 100 = ?
                  AND event_timestamp_utc >= ?
                """,
                (reaction_label, level_side, rounded_level, utc_iso(dedupe_cutoff))
            )
            if cursor.fetchone()["count"] > 0:
                fallback_metrics["ohlcv_fallback_deduped_count"] += 1
                fallback_metrics["ohlcv_fallback_skip_reason_latest"] = "deduped_recent_reaction"
                highest_successful_ts = max(highest_successful_ts, snapshot["timestamp_utc"])
                continue
            
            fallback_metrics["ohlcv_fallback_created_count"] += 1
            inserted += 1
            
            # Insert the fallback reaction
            cursor.execute("""
                INSERT INTO event_level_reactions (
                    event_id, snapshot_id, snapshot_sequence_id,
                    event_type, event_timestamp_utc, spot_price,
                    level_type, level_price, distance_pct, level_side,
                    reaction_label,
                    price_zone_25, price_zone_50, price_zone_100,
                    range_high_15m, range_low_15m,
                    range_high_1h, range_low_1h,
                    range_position_15m, range_position_1h,
                    near_range_high, near_range_low, range_1h_partial,
                    reaction_context, level_result,
                    nearest_level, nearest_level_type, distance_to_level_pct,
                    future_return_5m, future_return_15m, future_return_30m,
                    future_max_up_15m, future_max_down_15m,
                    future_range_15m, future_range_30m,
                    max_high_30m, min_low_30m, last_close_30m,
                    context_notes_json, classification_reason, reaction_skip_reason,
                    source, confidence, deribit_status, created_at_utc,
                    is_synthetic
                ) VALUES (
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?,
                    ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?,
                    ?
                )
            """, (
                f"fallback_{snapshot['snapshot_id']}", snapshot["snapshot_id"], snapshot["snapshot_sequence_id"],
                "OHLCV_PRICE_ACTION", utc_iso(snapshot["timestamp_utc"]), snapshot["spot_price"],
                reaction.get("level_type"), reaction.get("level_price"), reaction.get("distance_pct"), reaction.get("side"),
                reaction_label,
                reaction.get("price_zone_25"), reaction.get("price_zone_50"), reaction.get("price_zone_100"),
                reaction.get("range_high_15m"), reaction.get("range_low_15m"),
                reaction.get("range_high_1h"), reaction.get("range_low_1h"),
                reaction.get("range_position_15m"), reaction.get("range_position_1h"),
                reaction.get("near_range_high"), reaction.get("near_range_low"), reaction.get("range_1h_partial"),
                reaction.get("reaction_context"), reaction.get("level_result"),
                reaction.get("nearest_level"), reaction.get("nearest_level_type"), reaction.get("distance_to_level_pct"),
                reaction.get("future_return_5m"), reaction.get("future_return_15m"), reaction.get("future_return_30m"),
                reaction.get("future_max_up_15m"), reaction.get("future_max_down_15m"),
                reaction.get("future_range_15m"), reaction.get("future_range_30m"),
                reaction.get("max_high"), reaction.get("min_low"), reaction.get("last_close"),
                '{"is_synthetic": 1, "fallback_reason": "deribit_offline_snapshot_fallback"}',
                reaction.get("classification_reason"), skip_reason,
                "ohlcv_only", "LOW", snapshot.get("deribit_status", "OFFLINE"), utc_iso(),
                1
            ))
            highest_successful_ts = max(highest_successful_ts, snapshot["timestamp_utc"])

        if highest_successful_ts > last_processed_ts:
            cursor.execute("""
                INSERT INTO worker_state (key, value_text, updated_at_utc)
                VALUES ('ohlcv_fallback_last_processed_ts', ?, datetime('now'))
                ON CONFLICT(key) DO UPDATE SET value_text = excluded.value_text, updated_at_utc = excluded.updated_at_utc
            """, (str(highest_successful_ts),))
            fallback_metrics["ohlcv_fallback_last_processed_ts"] = highest_successful_ts
            fallback_metrics["ohlcv_fallback_latest_snapshot_ts"] = highest_successful_ts

        conn.commit()
        return {
            "inserted": inserted,
            "skipped": skipped,
            **fallback_metrics
        }

    @classmethod
    def worker_diagnostics(cls, conn) -> Dict[str, Any]:
        cls.ensure_schema(conn)
        cursor = conn.cursor()
        latest_ohlcv_ts = cls._latest_ohlcv_ts(cursor)
        cutoff = latest_ohlcv_ts - 1800 if latest_ohlcv_ts > 0 else 0.0
        cursor.execute("SELECT COUNT(*) AS n FROM event_outcomes")
        outcome_count = cursor.fetchone()["n"]
        cursor.execute("SELECT COUNT(*) AS n FROM event_level_reactions")
        reaction_count = cursor.fetchone()["n"]
        cursor.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN reaction_context IS NOT NULL
                          AND reaction_context NOT IN ('UNKNOWN', 'INSUFFICIENT_HISTORY')
                         THEN 1 ELSE 0 END) AS with_context,
                SUM(CASE WHEN reaction_context IS NULL
                          OR reaction_context IN ('UNKNOWN', 'INSUFFICIENT_HISTORY')
                          OR reaction_label = 'NO_LEVEL_CONTEXT'
                         THEN 1 ELSE 0 END) AS no_context,
                SUM(CASE WHEN reaction_label = 'UNKNOWN'
                          OR level_result = 'UNKNOWN'
                         THEN 1 ELSE 0 END) AS unknown_count,
                SUM(CASE WHEN reaction_label = 'NO_REACTION'
                          OR level_result = 'NO_REACTION'
                         THEN 1 ELSE 0 END) AS no_reaction_count,
                SUM(CASE WHEN level_price IS NULL THEN 1 ELSE 0 END) AS missing_level_price,
                SUM(CASE WHEN reaction_label IS NULL THEN 1 ELSE 0 END) AS missing_reaction_label
            FROM event_level_reactions
            """
        )
        level_context_row = cursor.fetchone()
        cursor.execute(
            """
            SELECT classification_reason, COUNT(*) AS n
            FROM event_level_reactions
            GROUP BY classification_reason
            ORDER BY n DESC
            """
        )
        classification_reason_distribution = [dict(row) for row in cursor.fetchall()]
        reaction_total = level_context_row["total"] or 0
        unknown_count = level_context_row["unknown_count"] or 0
        cursor.execute(
            """
            SELECT COUNT(*) AS n
            FROM events e
            LEFT JOIN event_outcomes eo ON eo.event_id = e.id
            WHERE eo.event_id IS NULL
              AND e.timestamp_utc <= ?
            """,
            (cutoff,),
        )
        eligible = cursor.fetchone()["n"]
        cursor.execute(
            """
            SELECT e.event_type, COUNT(*) AS n
            FROM events e
            LEFT JOIN event_outcomes eo ON eo.event_id = e.id
            WHERE eo.event_id IS NULL
              AND e.timestamp_utc <= ?
            GROUP BY e.event_type
            ORDER BY n DESC
            """,
            (cutoff,),
        )
        eligible_distribution = [dict(row) for row in cursor.fetchall()]
        return {
            "configured": True,
            "read_only_research_layer": True,
            "latest_ohlcv_timestamp_utc": utc_iso(latest_ohlcv_ts) if latest_ohlcv_ts else None,
            "eligibility_cutoff_utc": utc_iso(cutoff) if cutoff else None,
            "eligible_events_without_outcome": eligible,
            "eligible_distribution": eligible_distribution,
            "event_outcome_count": outcome_count,
            "event_level_reaction_count": reaction_count,
            "event_level_reactions_count": reaction_count,
            "level_reactions_with_context": level_context_row["with_context"] or 0,
            "level_reactions_no_context": level_context_row["no_context"] or 0,
            "level_reactions_total": reaction_total,
            "level_reactions_unknown_count": unknown_count,
            "level_reactions_unknown_rate": (unknown_count / reaction_total) if reaction_total else 0.0,
            "level_reactions_no_reaction_count": level_context_row["no_reaction_count"] or 0,
            "level_reactions_classified_count": reaction_total - unknown_count,
            "level_reactions_missing_level_price": level_context_row["missing_level_price"] or 0,
            "level_reactions_missing_reaction_label": level_context_row["missing_reaction_label"] or 0,
            "classification_reason_distribution": classification_reason_distribution,
            "readiness_rule": "event_timestamp_utc <= latest_ohlcv_timestamp_utc - 30 minutes",
        }

    @classmethod
    def outcome_summary(cls, conn, event_type: Optional[str] = None) -> Dict[str, Any]:
        cls.ensure_schema(conn)
        cursor = conn.cursor()
        where = ""
        params: List[Any] = []
        if event_type:
            where = "WHERE event_type = ?"
            params.append(event_type)
        cursor.execute(
            f"""
            SELECT event_type,
                   COUNT(*) AS n,
                   AVG(future_return_5m) AS avg_return_5m,
                   AVG(future_return_15m) AS avg_return_15m,
                   AVG(future_return_30m) AS avg_return_30m,
                   AVG(future_range_15m) AS avg_range_15m,
                   AVG(future_range_30m) AS avg_range_30m,
                   SUM(did_expand_15m) AS expand_15m,
                   SUM(did_expand_30m) AS expand_30m,
                   SUM(did_continue_direction_15m) AS continue_15m,
                   SUM(did_reverse_15m) AS reverse_15m
            FROM event_outcomes
            {where}
            GROUP BY event_type
            ORDER BY n DESC
            """,
            params,
        )
        by_type = [dict(row) for row in cursor.fetchall()]
        cursor.execute(
            f"""
            SELECT outcome_label, COUNT(*) AS n
            FROM event_outcomes
            {where}
            GROUP BY outcome_label
            ORDER BY n DESC
            """,
            params,
        )
        label_counts: Dict[str, int] = {}
        for row in cursor.fetchall():
            label = row["outcome_label"] if hasattr(row, "keys") else row[0]
            count = row["n"] if hasattr(row, "keys") else row[1]
            if label == "FALSE_POSITIVE":
                label = "NO_EXPANSION_FOLLOW_THROUGH"
            label_counts[label] = label_counts.get(label, 0) + int(count or 0)
        labels = [
            {"outcome_label": label, "n": count}
            for label, count in sorted(label_counts.items(), key=lambda item: item[1], reverse=True)
        ]
        cursor.execute(f"SELECT COUNT(*) AS n FROM event_outcomes {where}", params)
        total_row = cursor.fetchone()
        return {
            "status": "ok",
            "sample_count": total_row["n"] if hasattr(total_row, "keys") else total_row[0],
            "by_event_type": by_type,
            "outcome_distribution": labels,
            "thresholds": {
                "did_expand_15m_range_pct": EXPAND_15M_RANGE_PCT,
                "did_expand_30m_range_pct": EXPAND_30M_RANGE_PCT,
                "continue_return_pct": CONTINUE_RETURN_PCT,
                "reverse_return_pct": REVERSE_RETURN_PCT,
            },
        }

    @classmethod
    def _spot_price_for_level(cls, event: Dict[str, Any], outcome: Dict[str, Any],
                              snapshot: Dict[str, Any]) -> float:
        for value in (
            outcome.get("spot_price"),
            snapshot.get("snapshot_spot_price"),
            snapshot.get("spot_price"),
            event.get("spot_price"),
        ):
            spot = safe_float(value)
            if spot > 0:
                return spot
        payload = cls._decode_payload(event.get("event_payload_json"))
        for key in ("spot_price", "spot", "price", "mark_price", "underlying_price", "btc_price"):
            spot = safe_float(payload.get(key))
            if spot > 0:
                return spot
        return 0.0

    @staticmethod
    def _level_distance(spot_price: float, level_price: float) -> Optional[float]:
        level = safe_float(level_price)
        if spot_price <= 0 or level <= 0:
            return None
        return abs(spot_price - level) / spot_price * 100.0

    @classmethod
    def _nearest_level(cls, context: Dict[str, Any], spot_price: float) -> Dict[str, Any]:
        def _candidate(level_type: str, level_price: Any, side: str) -> Dict[str, Any]:
            level = safe_float(level_price)
            return {
                "level_type": level_type,
                "level_price": level if level > 0 else None,
                "distance_pct": cls._level_distance(spot_price, level),
                "side": side,
            }

        if context.get("near_range_high"):
            candidates = [
                _candidate("RANGE_HIGH_15M", context.get("range_high_15m"), "RESISTANCE"),
                _candidate("RANGE_HIGH_1H", context.get("range_high_1h"), "RESISTANCE"),
            ]
            candidates = [item for item in candidates if item["distance_pct"] is not None]
            if candidates:
                return min(candidates, key=lambda item: item["distance_pct"])

        if context.get("near_range_low"):
            candidates = [
                _candidate("RANGE_LOW_15M", context.get("range_low_15m"), "SUPPORT"),
                _candidate("RANGE_LOW_1H", context.get("range_low_1h"), "SUPPORT"),
            ]
            candidates = [item for item in candidates if item["distance_pct"] is not None]
            if candidates:
                return min(candidates, key=lambda item: item["distance_pct"])

        round_level = _candidate("ROUND_LEVEL", context.get("price_zone_50"), "ROUND")
        if round_level["distance_pct"] is not None:
            return round_level
        return {"level_type": None, "level_price": None, "distance_pct": None, "side": "UNKNOWN"}

    @classmethod
    def _ohlcv_window(cls, cursor, start_ts: float, end_ts: float) -> List[Dict[str, float]]:
        cursor.execute(
            """
            SELECT timestamp_utc, open, high, low, close, volume
            FROM ohlcv_candles
            WHERE symbol = 'BTCUSDT'
              AND timeframe = '1m'
              AND timestamp_utc BETWEEN ? AND ?
            ORDER BY timestamp_utc ASC
            """,
            (start_ts, end_ts),
        )
        return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def _round_zone(spot_price: float, step: int) -> float:
        return round(spot_price / step) * step if spot_price > 0 else 0.0

    @classmethod
    def _past_range_context(cls, cursor, event_ts: float, spot_price: float) -> Dict[str, Any]:
        notes: Dict[str, Any] = {}
        candles_15m = cls._ohlcv_window(cursor, event_ts - 15 * 60, event_ts)
        candles_1h = cls._ohlcv_window(cursor, event_ts - 60 * 60, event_ts)
        range_1h_partial = 1 if 0 < len(candles_1h) < 60 else 0
        if range_1h_partial:
            notes["range_1h_partial"] = True
            notes["range_1h_candles"] = len(candles_1h)

        def _range(rows: List[Dict[str, float]]) -> Dict[str, Optional[float]]:
            if not rows:
                return {"high": None, "low": None, "position": None}
            high = max(safe_float(row.get("high")) for row in rows)
            low = min(safe_float(row.get("low")) for row in rows)
            if high <= low or spot_price <= 0:
                position = None
            else:
                position = max(0.0, min(1.0, (spot_price - low) / (high - low)))
            return {"high": high, "low": low, "position": position}

        r15 = _range(candles_15m)
        r1h = _range(candles_1h)
        near_high = 0
        near_low = 0
        high_positions = [p for p in (r15["position"], r1h["position"]) if p is not None]
        low_positions = high_positions
        high_candidate = any(position >= 0.80 for position in high_positions)
        low_candidate = any(position <= 0.20 for position in low_positions)
        high_distances = [
            cls._level_distance(spot_price, r15["high"]),
            cls._level_distance(spot_price, r1h["high"]),
        ]
        low_distances = [
            cls._level_distance(spot_price, r15["low"]),
            cls._level_distance(spot_price, r1h["low"]),
        ]
        high_distance = min([d for d in high_distances if d is not None], default=None)
        low_distance = min([d for d in low_distances if d is not None], default=None)
        if high_candidate and low_candidate:
            if high_distance is not None and low_distance is not None and high_distance <= low_distance:
                near_high = 1
            else:
                near_low = 1
        else:
            near_high = 1 if high_candidate else 0
            near_low = 1 if low_candidate else 0

        if not candles_15m:
            reaction_context = "INSUFFICIENT_HISTORY"
            notes["insufficient_history"] = "no_15m_ohlcv_before_event"
        elif near_high:
            reaction_context = "RESISTANCE_TEST"
        elif near_low:
            reaction_context = "SUPPORT_TEST"
        elif r1h["position"] is not None:
            reaction_context = "MID_RANGE_REACTION"
        else:
            reaction_context = "ROUND_LEVEL_REACTION"

        return {
            "price_zone_25": cls._round_zone(spot_price, 25),
            "price_zone_50": cls._round_zone(spot_price, 50),
            "price_zone_100": cls._round_zone(spot_price, 100),
            "range_high_15m": r15["high"],
            "range_low_15m": r15["low"],
            "range_high_1h": r1h["high"],
            "range_low_1h": r1h["low"],
            "range_position_15m": r15["position"],
            "range_position_1h": r1h["position"],
            "near_range_high": near_high,
            "near_range_low": near_low,
            "range_1h_partial": range_1h_partial,
            "reaction_context": reaction_context,
            "context_notes": notes,
        }

    @staticmethod
    def _level_side_for_context(reaction_context: str) -> str:
        if reaction_context == "RESISTANCE_TEST":
            return "RESISTANCE"
        if reaction_context == "SUPPORT_TEST":
            return "SUPPORT"
        if reaction_context == "MID_RANGE_REACTION":
            return "MID_RANGE"
        if reaction_context == "ROUND_LEVEL_REACTION":
            return "ROUND"
        return "UNKNOWN"

    @staticmethod
    def _has_number(value: Any) -> bool:
        try:
            out = float(value)
            return not math.isnan(out) and not math.isinf(out)
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _result(label: str, reason: str) -> Dict[str, str]:
        return {"level_result": label, "classification_reason": reason}

    @classmethod
    def _effective_level_side(cls, reaction_context: str, level_side: str,
                              nearest_level_type: Optional[str],
                              range_position_15m: Optional[float],
                              range_position_1h: Optional[float]) -> tuple:
        if reaction_context == "RESISTANCE_TEST" or level_side == "RESISTANCE":
            return "RESISTANCE", None
        if reaction_context == "SUPPORT_TEST" or level_side == "SUPPORT":
            return "SUPPORT", None
        if nearest_level_type == "ROUND_LEVEL" or level_side == "ROUND":
            if range_position_1h is not None and range_position_1h >= 0.80:
                return "RESISTANCE", "round_level_fallback_to_resistance"
            if range_position_1h is not None and range_position_1h <= 0.20:
                return "SUPPORT", "round_level_fallback_to_support"
            if range_position_15m is not None and range_position_15m >= 0.80:
                return "RESISTANCE", "round_level_fallback_to_resistance"
            if range_position_15m is not None and range_position_15m <= 0.20:
                return "SUPPORT", "round_level_fallback_to_support"
            return "MID_RANGE", "round_level_fallback_to_mid_range"
        if reaction_context == "MID_RANGE_REACTION" or level_side == "MID_RANGE":
            return "MID_RANGE", None
        return "UNKNOWN", None

    @classmethod
    def _future_level_result(cls, reaction_context: str, level_side: str, nearest_level: float,
                             nearest_level_type: Optional[str], spot_price: float,
                             outcome: Dict[str, Any], candles_15m: List[Dict[str, float]],
                             range_position_15m: Optional[float],
                             range_position_1h: Optional[float]) -> Dict[str, str]:
        if not candles_15m:
            return cls._result("INSUFFICIENT_DATA", "insufficient_future_data")
        if reaction_context == "INSUFFICIENT_HISTORY":
            return cls._result("INSUFFICIENT_DATA", "insufficient_history")
        if spot_price <= 0 or nearest_level <= 0:
            return cls._result("INSUFFICIENT_DATA", "insufficient_spot_or_level")

        required_15m = ("future_return_15m", "future_range_15m")
        if any(not cls._has_number(outcome.get(key)) for key in required_15m):
            return cls._result("INSUFFICIENT_DATA", "insufficient_future_data")

        close_15m = safe_float(candles_15m[-1].get("close"))
        future_return_15m = safe_float(outcome.get("future_return_15m"))
        future_return_30m = safe_float(outcome.get("future_return_30m"))
        future_max_up_15m = safe_float(outcome.get("future_max_up_15m"))
        future_max_down_15m = safe_float(outcome.get("future_max_down_15m"))
        future_range_15m = safe_float(outcome.get("future_range_15m"))
        future_range_30m = safe_float(outcome.get("future_range_30m"))
        has_30m = cls._has_number(outcome.get("future_return_30m")) and cls._has_number(outcome.get("future_range_30m"))
        effective_side, side_reason = cls._effective_level_side(
            reaction_context,
            level_side,
            nearest_level_type,
            range_position_15m,
            range_position_1h,
        )
        if has_30m:
            low_range = future_range_15m < NO_REACTION_RANGE_15M_PCT or future_range_30m < NO_REACTION_RANGE_30M_PCT
            fallback_low_range = future_range_30m < UNKNOWN_FALLBACK_RANGE_30M_PCT
        else:
            low_range = future_range_15m < NO_REACTION_RANGE_15M_PCT
            fallback_low_range = low_range

        if effective_side == "RESISTANCE":
            if future_max_up_15m >= FALSE_BREAK_PIERCE_BUFFER_PCT and close_15m < nearest_level:
                return cls._result("FALSE_BREAK", "false_break_resistance_pierce_return")
            if future_return_15m <= RESISTANCE_REJECTION_RETURN_15M_PCT:
                return cls._result("RESISTANCE_REJECTION", "resistance_rejection_future_return_15m")
            if future_max_down_15m <= RESISTANCE_REJECTION_MAX_DOWN_15M_PCT:
                return cls._result("RESISTANCE_REJECTION", "resistance_rejection_max_down_15m")
            close_above = close_15m > nearest_level * (1 + LEVEL_BREAK_CLOSE_BUFFER_PCT / 100.0)
            if future_return_15m >= DIRECTIONAL_RETURN_15M_PCT or (future_max_up_15m >= 0.30 and close_above):
                return cls._result("LEVEL_BREAK", "resistance_level_break_future_return_15m")
            if low_range:
                reason = "no_reaction_low_range_15m" if future_range_15m < NO_REACTION_RANGE_15M_PCT else "no_reaction_low_range_30m"
                return cls._result("NO_REACTION", reason)
            if abs(future_return_15m) >= WEAK_MOVE_RETURN_15M_PCT:
                return cls._result("WEAK_DIRECTIONAL_MOVE", "weak_directional_move_return_15m")
            if has_30m and abs(future_return_30m) >= WEAK_MOVE_RETURN_30M_PCT:
                return cls._result("WEAK_DIRECTIONAL_MOVE", "weak_directional_move_return_30m")
            if fallback_low_range:
                return cls._result("NO_REACTION", "no_reaction_low_range_30m")
            return cls._result("UNKNOWN", "unknown_conflicting_conditions")

        if effective_side == "SUPPORT":
            if future_max_down_15m <= -FALSE_BREAK_PIERCE_BUFFER_PCT and close_15m > nearest_level:
                return cls._result("FALSE_BREAK", "false_break_support_pierce_return")
            if future_return_15m >= SUPPORT_DEFENSE_RETURN_15M_PCT:
                return cls._result("SUPPORT_DEFENSE", "support_defense_future_return_15m")
            if future_max_up_15m >= SUPPORT_DEFENSE_MAX_UP_15M_PCT:
                return cls._result("SUPPORT_DEFENSE", "support_defense_future_max_up_15m")
            close_below = close_15m < nearest_level * (1 - LEVEL_BREAK_CLOSE_BUFFER_PCT / 100.0)
            if future_return_15m <= -DIRECTIONAL_RETURN_15M_PCT or (future_max_down_15m <= -0.30 and close_below):
                return cls._result("LEVEL_BREAK", "support_level_break_future_return_15m")
            if low_range:
                reason = "no_reaction_low_range_15m" if future_range_15m < NO_REACTION_RANGE_15M_PCT else "no_reaction_low_range_30m"
                return cls._result("NO_REACTION", reason)
            if abs(future_return_15m) >= WEAK_MOVE_RETURN_15M_PCT:
                return cls._result("WEAK_DIRECTIONAL_MOVE", "weak_directional_move_return_15m")
            if has_30m and abs(future_return_30m) >= WEAK_MOVE_RETURN_30M_PCT:
                return cls._result("WEAK_DIRECTIONAL_MOVE", "weak_directional_move_return_30m")
            if fallback_low_range:
                return cls._result("NO_REACTION", "no_reaction_low_range_30m")
            return cls._result("UNKNOWN", "unknown_conflicting_conditions")

        if effective_side == "MID_RANGE":
            if has_30m and future_range_30m >= MID_RANGE_EXPANSION_30M_PCT:
                return cls._result("MID_RANGE_EXPANSION", "mid_range_expansion_range_30m")
            if abs(future_return_15m) >= DIRECTIONAL_RETURN_15M_PCT:
                return cls._result("MID_RANGE_DIRECTIONAL_MOVE", "mid_range_directional_move_return_15m")
            if has_30m and abs(future_return_30m) >= DIRECTIONAL_RETURN_30M_PCT:
                return cls._result("MID_RANGE_DIRECTIONAL_MOVE", "mid_range_directional_move_return_30m")
            if has_30m and future_range_30m < NO_REACTION_RANGE_30M_PCT:
                return cls._result("NO_REACTION", "no_reaction_low_range_30m")
            if future_range_15m < NO_REACTION_RANGE_15M_PCT:
                return cls._result("NO_REACTION", "no_reaction_low_range_15m")
            if abs(future_return_15m) >= WEAK_MOVE_RETURN_15M_PCT:
                return cls._result("WEAK_DIRECTIONAL_MOVE", "weak_directional_move_return_15m")
            if has_30m and abs(future_return_30m) >= WEAK_MOVE_RETURN_30M_PCT:
                return cls._result("WEAK_DIRECTIONAL_MOVE", "weak_directional_move_return_30m")
            if fallback_low_range:
                return cls._result("NO_REACTION", "no_reaction_low_range_30m")
            if side_reason:
                return cls._result("UNKNOWN", side_reason)
            return cls._result("UNKNOWN", "unknown_conflicting_conditions")

        return cls._result("UNKNOWN", "unknown_conflicting_conditions")

    @classmethod
    def _synthetic_live_reaction(cls, reaction_context: str, level_side: str, nearest_level: float,
                                 spot_price: float, candles_15m: List[Dict[str, float]]) -> Dict[str, str]:
        if not candles_15m:
            return cls._result("NO_REACTION", "insufficient_ohlcv_for_live")
        if spot_price <= 0 or nearest_level <= 0:
            return cls._result("NO_REACTION", "insufficient_spot_or_level")

        dist_pct = cls._level_distance(spot_price, nearest_level)
        if dist_pct is None or dist_pct > 0.20:
            return cls._result("NO_REACTION", "live_ohlcv_level_interaction")
            
        latest_candle = candles_15m[-1]
        latest_close = safe_float(latest_candle.get("close"))
        latest_open = safe_float(latest_candle.get("open"))
        if len(candles_15m) >= 2:
            prev_close = safe_float(candles_15m[-2].get("close"))
        else:
            prev_close = latest_open

        if level_side == "SUPPORT":
            # SUPPORT_DEFENSE
            if latest_close >= nearest_level and (latest_close >= prev_close or latest_close >= latest_open):
                return cls._result("SUPPORT_DEFENSE", "live_ohlcv_level_interaction")
            # LEVEL_BREAK
            if latest_close < nearest_level * (1 - 0.05 / 100.0):
                return cls._result("LEVEL_BREAK", "live_ohlcv_level_interaction")
                
        elif level_side == "RESISTANCE":
            # RESISTANCE_REJECTION
            if latest_close <= nearest_level and (latest_close <= prev_close or latest_close <= latest_open):
                return cls._result("RESISTANCE_REJECTION", "live_ohlcv_level_interaction")
            # LEVEL_BREAK
            if latest_close > nearest_level * (1 + 0.05 / 100.0):
                return cls._result("LEVEL_BREAK", "live_ohlcv_level_interaction")
                
        return cls._result("NO_REACTION", "live_ohlcv_level_interaction")

    @classmethod
    def _classify_level_reaction(cls, cursor, event: Dict[str, Any], outcome: Dict[str, Any],
                                 snapshot: Dict[str, Any]) -> Dict[str, Any]:
        spot_price = cls._spot_price_for_level(event, outcome, snapshot)
        event_ts = safe_float(event.get("timestamp_utc"))
        if spot_price <= 0:
            return {
                "reaction_label": "INSUFFICIENT_DATA",
                "level_result": "INSUFFICIENT_DATA",
                "reaction_context": "UNKNOWN",
                "level_type": None,
                "level_price": None,
                "distance_pct": None,
                "side": "UNKNOWN",
                "nearest_level": None,
                "nearest_level_type": None,
                "distance_to_level_pct": None,
                "classification_reason": "insufficient_spot_or_level",
                "reaction_skip_reason": "insufficient_spot_or_level",
                "context_notes": {"missing_spot_price": True},
                "is_synthetic": 0,
            }
        
        payload_json = event.get("event_payload_json") or "{}"
        payload = cls._decode_payload(payload_json)
        is_synthetic = int(payload.get("is_synthetic", 0))
        fallback_reason = payload.get("fallback_reason")
        range_context = cls._past_range_context(cursor, event_ts, spot_price)
        level = cls._nearest_level(range_context, spot_price)
        reaction_context = range_context.get("reaction_context") or "UNKNOWN"
        level_side = cls._level_side_for_context(reaction_context)
        candles_30m = cls._ohlcv_window(cursor, event_ts, event_ts + 1800)
        candles_15m = [row for row in candles_30m if safe_float(row.get("timestamp_utc")) <= event_ts + 900]
        
        if is_synthetic:
            skip_reason = None
            past_candles_15m = cls._ohlcv_window(cursor, event_ts - 900, event_ts)
            result = cls._synthetic_live_reaction(
                reaction_context,
                level_side,
                safe_float(level.get("level_price")),
                spot_price,
                past_candles_15m
            )
        elif not candles_30m:
            result = cls._result("INSUFFICIENT_DATA", "insufficient_future_data")
            skip_reason = "no_future_window_yet"
        elif reaction_context == "INSUFFICIENT_HISTORY":
            result = cls._result("INSUFFICIENT_DATA", "insufficient_history")
            skip_reason = "insufficient_ohlcv"
        else:
            skip_reason = None
            result = cls._future_level_result(
                reaction_context,
                level_side,
                safe_float(level.get("level_price")),
                level.get("level_type"),
                spot_price,
                outcome,
                candles_15m,
                range_context.get("range_position_15m"),
                range_context.get("range_position_1h"),
            )
        level_result = result["level_result"]

        max_high = max((safe_float(row.get("high")) for row in candles_30m), default=None)
        min_low = min((safe_float(row.get("low")) for row in candles_30m), default=None)
        last_close = safe_float(candles_30m[-1].get("close")) if candles_30m else None

        import json
        deribit_status = snapshot.get("deribit_status", "UNKNOWN")
        records_used = snapshot.get("deribit_records_used", 0) or 0
        valid_greeks = snapshot.get("valid_greeks_count", 0) or 0
        valid_gamma = snapshot.get("valid_gamma_count", 0) or 0
        
        active_sources_raw = snapshot.get("active_sources", "[]")
        try:
            active_sources = json.loads(active_sources_raw) if active_sources_raw else []
        except:
            active_sources = []
            
        has_deribit = "deribit" in active_sources
        
        is_deribit_healthy = (
            deribit_status in ("ONLINE", "OK") and 
            records_used > 0 and 
            (valid_greeks > 0 or valid_gamma > 0) and 
            has_deribit
        )

        if not is_deribit_healthy or is_synthetic:
            source = "ohlcv_only"
            confidence = "LOW"
            if not is_synthetic and deribit_status not in ("STALE", "MISSING", "ERROR", "OFFLINE", "EMPTY", "PARSE_ERROR"):
                deribit_status = "UNKNOWN"
        else:
            source = "gamma_walls+ohlcv_1h"
            confidence = "NORMAL"

        # Deduplication check (5 min for synthetic, 10 min for normal)
        cooldown_sec = 300 if is_synthetic else 600
        if level_result not in ("UNKNOWN", "INSUFFICIENT_DATA", "NO_REACTION") and level.get("level_price"):
            cursor.execute("""
                SELECT 1 FROM event_level_reactions
                WHERE reaction_label = ? 
                  AND level_side = ?
                  AND ABS(level_price - ?) < 25
                  AND source = ?
                  AND event_timestamp_utc >= ?
                  AND reaction_label NOT IN ('UNKNOWN', 'INSUFFICIENT_DATA', 'NO_REACTION')
                LIMIT 1
            """, (level_result, level_side, float(level["level_price"]), source, utc_iso(event_ts - cooldown_sec)))
            if cursor.fetchone():
                level_result = "NO_REACTION"
                result["classification_reason"] = "duplicate_reaction_cooldown"
                skip_reason = "deduped_recent_reaction"
        
        if level_result == "UNKNOWN" and skip_reason is None:
            skip_reason = "no_directional_mapping"
        elif level_result == "INSUFFICIENT_DATA" and skip_reason is None:
            skip_reason = "insufficient_ohlcv"
        elif level_result == "NO_REACTION" and skip_reason is None and is_synthetic:
            skip_reason = "no_confirmed_price_reaction"
        elif not level.get("level_price") and skip_reason is None:
            skip_reason = "no_near_level"

        return {
            **range_context,
            **level,
            "spot_price": spot_price,
            "reaction_label": level_result,
            "level_result": level_result,
            "nearest_level": level.get("level_price"),
            "nearest_level_type": level.get("level_type"),
            "distance_to_level_pct": level.get("distance_pct"),
            "reaction_context": reaction_context,
            "level_side": level_side,
            "classification_reason": result.get("classification_reason"),
            "reaction_skip_reason": skip_reason,
            "ohlcv_candles": len(candles_30m),
            "max_high": round(max_high, 2) if max_high is not None else None,
            "min_low": round(min_low, 2) if min_low is not None else None,
            "last_close": round(last_close, 2) if last_close is not None else None,
            "deribit_status": deribit_status,
            "source": source,
            "confidence": confidence,
            "is_synthetic": is_synthetic,
        }

    @classmethod
    def build_level_reaction(cls, cursor, event: Dict[str, Any], outcome: Dict[str, Any],
                             snapshot: Dict[str, Any], outcome_id: int) -> tuple:
        reaction = cls._classify_level_reaction(cursor, event, outcome, snapshot)
        return (
            event.get("id"),
            outcome_id,
            outcome.get("snapshot_id"),
            outcome.get("snapshot_sequence_id"),
            outcome.get("event_type"),
            outcome.get("event_timestamp_utc"),
            reaction.get("spot_price", outcome.get("spot_price")),
            reaction.get("level_type"),
            reaction.get("level_price"),
            reaction.get("distance_pct"),
            reaction.get("level_side", reaction.get("side")),
            reaction.get("reaction_label"),
            reaction.get("price_zone_25"),
            reaction.get("price_zone_50"),
            reaction.get("price_zone_100"),
            reaction.get("range_high_15m"),
            reaction.get("range_low_15m"),
            reaction.get("range_high_1h"),
            reaction.get("range_low_1h"),
            reaction.get("range_position_15m"),
            reaction.get("range_position_1h"),
            reaction.get("near_range_high"),
            reaction.get("near_range_low"),
            reaction.get("range_1h_partial"),
            reaction.get("reaction_context"),
            reaction.get("level_result"),
            reaction.get("nearest_level"),
            reaction.get("nearest_level_type"),
            reaction.get("distance_to_level_pct"),
            outcome.get("future_return_5m"),
            outcome.get("future_return_15m"),
            outcome.get("future_return_30m"),
            outcome.get("future_max_up_15m"),
            outcome.get("future_max_down_15m"),
            outcome.get("future_range_15m"),
            outcome.get("future_range_30m"),
            reaction.get("max_high"),
            reaction.get("min_low"),
            reaction.get("last_close"),
            json.dumps(reaction.get("context_notes", {}), sort_keys=True),
            reaction.get("classification_reason"),
            reaction.get("reaction_skip_reason"),
            reaction.get("source", "UNKNOWN"),
            reaction.get("confidence", "UNKNOWN"),
            reaction.get("deribit_status", "UNKNOWN"),
            reaction.get("is_synthetic", 0),
            utc_iso(),
        )

    @staticmethod
    def _normalize_time_filter(value: Optional[Any]) -> Optional[str]:
        if value in (None, ""):
            return None
        try:
            return utc_iso(float(value))
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _dominant(counts: Dict[str, int]) -> Optional[str]:
        if not counts:
            return None
        return max(counts.items(), key=lambda item: item[1])[0]

    @classmethod
    def level_reaction_summary(cls, conn, event_type: Optional[str] = None, limit: int = 1000,
                               from_utc: Optional[Any] = None, to_utc: Optional[Any] = None) -> Dict[str, Any]:
        cls.ensure_schema(conn)
        cursor = conn.cursor()
        params: List[Any] = []
        filters = []
        if event_type:
            filters.append("event_type = ?")
            params.append(event_type)
        from_filter = cls._normalize_time_filter(from_utc)
        to_filter = cls._normalize_time_filter(to_utc)
        if from_filter:
            filters.append("event_timestamp_utc >= ?")
            params.append(from_filter)
        if to_filter:
            filters.append("event_timestamp_utc <= ?")
            params.append(to_filter)
        where_sql = " AND ".join(filters)
        if where_sql:
            where_sql = f"AND {where_sql}"
        cursor.execute(
            f"""
            SELECT *
            FROM event_level_reactions
            WHERE 1 = 1
              {where_sql}
            ORDER BY id DESC
            LIMIT ?
            """,
            params + [int(limit)],
        )
        rows = [dict(row) for row in cursor.fetchall()]

        distribution: Dict[str, int] = {}
        level_type_distribution: Dict[str, int] = {}
        context_distribution: Dict[str, int] = {}
        result_distribution: Dict[str, int] = {}
        zone_50_distribution: Dict[str, int] = {}
        reason_distribution: Dict[str, int] = {}
        unknown_by_event_type: Dict[str, int] = {}
        unknown_by_level_side: Dict[str, int] = {}
        unknown_by_level_type: Dict[str, int] = {}
        zones: Dict[float, Dict[str, Any]] = {}
        for reaction in rows:
            label = reaction.get("reaction_label", "UNKNOWN")
            distribution[label] = distribution.get(label, 0) + 1
            level_type = reaction.get("level_type", "NONE")
            level_type_distribution[level_type] = level_type_distribution.get(level_type, 0) + 1
            context = reaction.get("reaction_context", "UNKNOWN")
            context_distribution[context] = context_distribution.get(context, 0) + 1
            result = reaction.get("level_result", "UNKNOWN")
            result_distribution[result] = result_distribution.get(result, 0) + 1
            reason = reaction.get("classification_reason", "UNKNOWN")
            reason_distribution[reason] = reason_distribution.get(reason, 0) + 1
            if label == "UNKNOWN" or result == "UNKNOWN":
                event_name = reaction.get("event_type") or "UNKNOWN"
                unknown_by_event_type[event_name] = unknown_by_event_type.get(event_name, 0) + 1
                side_name = reaction.get("level_side") or "UNKNOWN"
                unknown_by_level_side[side_name] = unknown_by_level_side.get(side_name, 0) + 1
                type_name = reaction.get("level_type") or "UNKNOWN"
                unknown_by_level_type[type_name] = unknown_by_level_type.get(type_name, 0) + 1
            zone_50 = reaction.get("price_zone_50")
            if zone_50 is not None:
                zone_key = str(int(zone_50)) if safe_float(zone_50) else str(zone_50)
                zone_50_distribution[zone_key] = zone_50_distribution.get(zone_key, 0) + 1
                zone = safe_float(zone_50)
                bucket = zones.setdefault(zone, {
                    "price_zone_50": zone,
                    "event_count": 0,
                    "event_types": {},
                    "reactions": {},
                    "future_return_15m_sum": 0.0,
                    "future_return_15m_count": 0,
                })
                bucket["event_count"] += 1
                event_name = reaction.get("event_type") or "UNKNOWN"
                bucket["event_types"][event_name] = bucket["event_types"].get(event_name, 0) + 1
                result_name = reaction.get("level_result") or reaction.get("reaction_label") or "UNKNOWN"
                bucket["reactions"][result_name] = bucket["reactions"].get(result_name, 0) + 1
                if reaction.get("future_return_15m") is not None:
                    bucket["future_return_15m_sum"] += safe_float(reaction.get("future_return_15m"))
                    bucket["future_return_15m_count"] += 1

        zone_rows = []
        for bucket in zones.values():
            count = bucket["future_return_15m_count"]
            avg_return = bucket["future_return_15m_sum"] / count if count else None
            zone_rows.append({
                "price_zone_50": bucket["price_zone_50"],
                "event_count": bucket["event_count"],
                "dominant_event_type": cls._dominant(bucket["event_types"]),
                "dominant_reaction": cls._dominant(bucket["reactions"]),
                "avg_future_return_15m": avg_return,
            })
        zone_rows.sort(key=lambda item: item["event_count"], reverse=True)

        def _zones_for(result: str) -> List[Dict[str, Any]]:
            return [item for item in zone_rows if item.get("dominant_reaction") == result]

        decisive = [
            item for item in rows
            if item.get("reaction_label") in {
                "RESISTANCE_REJECTION",
                "SUPPORT_DEFENSE",
                "LEVEL_BREAK",
                "FALSE_BREAK",
                "MID_RANGE_DIRECTIONAL_MOVE",
                "MID_RANGE_EXPANSION",
                "WEAK_DIRECTIONAL_MOVE",
            }
        ]
        unknown_count = sum(
            1 for item in rows
            if item.get("reaction_label") == "UNKNOWN" or item.get("level_result") == "UNKNOWN"
        )
        no_reaction_count = sum(
            1 for item in rows
            if item.get("reaction_label") == "NO_REACTION" or item.get("level_result") == "NO_REACTION"
        )
        return {
            "status": "ok",
            "sample_count": len(rows),
            "decisive_reaction_count": len(decisive),
            "level_reactions_total": len(rows),
            "level_reactions_unknown_count": unknown_count,
            "level_reactions_unknown_rate": (unknown_count / len(rows)) if rows else 0.0,
            "level_reactions_no_reaction_count": no_reaction_count,
            "level_reactions_classified_count": len(rows) - unknown_count,
            "reaction_distribution": distribution,
            "reaction_label_distribution": distribution,
            "level_type_distribution": level_type_distribution,
            "level_side_distribution": {
                side: sum(1 for item in rows if (item.get("level_side") or "UNKNOWN") == side)
                for side in sorted({item.get("level_side") or "UNKNOWN" for item in rows})
            },
            "reaction_context_distribution": context_distribution,
            "level_result_distribution": result_distribution,
            "price_zone_50_distribution": zone_50_distribution,
            "unknown_by_event_type": unknown_by_event_type,
            "unknown_by_level_side": unknown_by_level_side,
            "unknown_by_level_type": unknown_by_level_type,
            "classification_reason_distribution": reason_distribution,
            "top_event_zones": zone_rows[:25],
            "support_defense_zones": _zones_for("SUPPORT_DEFENSE"),
            "resistance_rejection_zones": _zones_for("RESISTANCE_REJECTION"),
            "level_break_zones": _zones_for("LEVEL_BREAK"),
            "false_break_zones": _zones_for("FALSE_BREAK"),
            "no_reaction_zones": _zones_for("NO_REACTION"),
            "thresholds": {
                "near_level_pct": LEVEL_NEAR_PCT,
                "near_range_pct": RANGE_NEAR_PCT,
                "no_reaction_range_15m_pct": NO_REACTION_RANGE_15M_PCT,
                "no_reaction_range_30m_pct": NO_REACTION_RANGE_30M_PCT,
                "weak_move_return_15m_pct": WEAK_MOVE_RETURN_15M_PCT,
                "weak_move_return_30m_pct": WEAK_MOVE_RETURN_30M_PCT,
                "directional_return_15m_pct": DIRECTIONAL_RETURN_15M_PCT,
                "directional_return_30m_pct": DIRECTIONAL_RETURN_30M_PCT,
                "level_break_close_buffer_pct": LEVEL_BREAK_CLOSE_BUFFER_PCT,
                "false_break_pierce_buffer_pct": FALSE_BREAK_PIERCE_BUFFER_PCT,
                "support_defense_return_15m_pct": SUPPORT_DEFENSE_RETURN_15M_PCT,
                "support_defense_max_up_15m_pct": SUPPORT_DEFENSE_MAX_UP_15M_PCT,
                "resistance_rejection_return_15m_pct": RESISTANCE_REJECTION_RETURN_15M_PCT,
                "resistance_rejection_max_down_15m_pct": RESISTANCE_REJECTION_MAX_DOWN_15M_PCT,
                "mid_range_expansion_30m_pct": MID_RANGE_EXPANSION_30M_PCT,
                "expansion_range_30m_pct": EXPANSION_RANGE_30M_PCT,
                "unknown_fallback_range_30m_pct": UNKNOWN_FALLBACK_RANGE_30M_PCT,
            },
            "examples": rows[:25],
            "note": "Level reactions are persisted replay-only diagnostics from option levels, round price zones, range context, and OHLCV.",
        }

    @classmethod
    def pine_export(cls, conn, limit: int = 500) -> str:
        cls.ensure_schema(conn)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT event_timestamp_utc, event_type, spot_price, outcome_label,
                   future_return_15m, future_range_30m
            FROM event_outcomes
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(limit),),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow([
            "event_timestamp_utc",
            "event_type",
            "spot_price",
            "outcome_label",
            "future_return_15m",
            "future_range_30m",
        ])
        for row in rows:
            writer.writerow([
                row.get("event_timestamp_utc"),
                row.get("event_type"),
                row.get("spot_price"),
                row.get("outcome_label"),
                row.get("future_return_15m"),
                row.get("future_range_30m"),
            ])
        return out.getvalue()

    @classmethod
    def pine_script(cls, conn, limit: int = 100) -> str:
        cls.ensure_schema(conn)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT event_timestamp_utc, event_type, spot_price, outcome_label,
                   future_return_15m, future_range_30m
            FROM event_outcomes
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(limit),),
        )
        rows = list(reversed([dict(row) for row in cursor.fetchall()]))
        lines = [
            "//@version=5",
            'indicator("MOS Replay Event Outcomes", overlay=true, max_labels_count=500)',
            "",
            "// Generated from MOS event_outcomes. Replay-only; not a trading signal.",
        ]
        for idx, row in enumerate(rows):
            ts = str(row.get("event_timestamp_utc") or "")
            try:
                dt = datetime.fromisoformat(ts.replace("Z", ""))
                ts_expr = f'timestamp("UTC", {dt.year}, {dt.month}, {dt.day}, {dt.hour}, {dt.minute})'
            except Exception:
                continue
            event_type = str(row.get("event_type") or "EVENT").replace('"', "'")
            outcome = str(row.get("outcome_label") or "UNKNOWN").replace('"', "'")
            price = round(safe_float(row.get("spot_price")), 2)
            ret15 = round(safe_float(row.get("future_return_15m")), 3)
            range30 = round(safe_float(row.get("future_range_30m")), 3)
            color_name = "color.gray"
            if "CONTINUATION" in outcome:
                color_name = "color.green"
            elif "REVERSAL" in outcome:
                color_name = "color.orange"
            elif "FALSE" in outcome:
                color_name = "color.red"
            text = f"{event_type}\\n{outcome}\\n15m {ret15}% / range30 {range30}%"
            lines.extend([
                f"mos_ts_{idx} = {ts_expr}",
                f"if time >= mos_ts_{idx} and time[1] < mos_ts_{idx}",
                f'    label.new(bar_index, {price}, "{text}", style=label.style_label_down, textcolor=color.white, color={color_name}, size=size.tiny)',
            ])
        return "\n".join(lines) + "\n"
