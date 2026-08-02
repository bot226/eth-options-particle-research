"""Research API Router — Provides historical intelligence data for the replay frontend."""

import sqlite3
import os
import time
import json
from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from typing import Optional, List, Dict

router = APIRouter(prefix="/api/research")
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'mos_research.db'))
_dm = None


def set_data_manager(dm):
    """Share the running manager so diagnostics do not duplicate collectors."""
    global _dm
    _dm = dm

def get_db():
    if not os.path.exists(DB_PATH):
        raise HTTPException(status_code=404, detail="Research database not found")
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def parse_resolution(res_str: str) -> Optional[int]:
    if not res_str:
        return None
    res_str = res_str.lower()
    if res_str.endswith('s'):
        return int(res_str[:-1])
    elif res_str.endswith('m'):
        return int(res_str[:-1]) * 60
    elif res_str.endswith('h'):
        return int(res_str[:-1]) * 3600
    elif res_str.endswith('d'):
        return int(res_str[:-1]) * 86400
    return int(res_str)


def get_ohlcv_context(cursor, to_ts: Optional[float] = None) -> dict:
    """Return read-only OHLCV context for validation, not live scoring."""
    if not to_ts:
        cursor.execute("SELECT MAX(timestamp_utc) FROM ohlcv_candles WHERE symbol='BTCUSDT' AND timeframe='1m'")
        row = cursor.fetchone()
        to_ts = row[0] if row and row[0] else None
    if not to_ts:
        return {
            "available": False,
            "reason": "no_ohlcv_candles",
        }

    from_ts = to_ts - 15 * 60
    cursor.execute("""
        SELECT timestamp_utc, open, high, low, close, volume
        FROM ohlcv_candles
        WHERE symbol = 'BTCUSDT'
          AND timeframe = '1m'
          AND timestamp_utc BETWEEN ? AND ?
        ORDER BY timestamp_utc ASC
    """, (from_ts, to_ts))
    rows = [dict(row) for row in cursor.fetchall()]
    if not rows:
        return {
            "available": False,
            "reason": "no_ohlcv_candles_in_window",
            "to": to_ts,
        }

    def _window_return(seconds: int) -> float:
        start_ts = to_ts - seconds
        subset = [r for r in rows if r["timestamp_utc"] >= start_ts]
        if len(subset) < 2 or subset[0]["open"] <= 0:
            return 0.0
        return round((subset[-1]["close"] - subset[0]["open"]) / subset[0]["open"] * 100, 3)

    first_open = rows[0]["open"]
    last_close = rows[-1]["close"]
    high = max(r["high"] for r in rows)
    low = min(r["low"] for r in rows)
    volume_first = sum(r["volume"] for r in rows[:5]) if len(rows) >= 5 else 0
    volume_last = sum(r["volume"] for r in rows[-5:]) if len(rows) >= 5 else 0
    volume_change_15m = (
        round((volume_last - volume_first) / volume_first * 100, 2)
        if volume_first > 0 else 0.0
    )

    return {
        "available": True,
        "source": "ohlcv_candles",
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "from": rows[0]["timestamp_utc"],
        "to": rows[-1]["timestamp_utc"],
        "return_5m": _window_return(5 * 60),
        "return_15m": round((last_close - first_open) / first_open * 100, 3) if first_open > 0 else 0.0,
        "range_15m": round((high - low) / first_open * 100, 3) if first_open > 0 else 0.0,
        "volume_change_15m": volume_change_15m,
        "note": "OHLCV context is read-only validation and does not affect live synthetic_flow_pressure.",
    }


def get_short_term_flow_context(cursor, to_ts: Optional[float] = None, spot_price: Optional[float] = None) -> dict:
    """Return experimental OHLCV short-term flow context for diagnostics only."""
    from engine.short_term_flow_context_engine import ShortTermFlowContextEngine

    if not to_ts:
        cursor.execute("SELECT MAX(timestamp_utc) FROM snapshots WHERE exclude_from_analysis = 0")
        row = cursor.fetchone()
        to_ts = row[0] if row and row[0] else None
    if not to_ts:
        cursor.execute("SELECT MAX(timestamp_utc) FROM ohlcv_candles WHERE symbol='BTCUSDT' AND timeframe='1m'")
        row = cursor.fetchone()
        to_ts = row[0] if row and row[0] else time.time()

    if spot_price is None:
        cursor.execute("""
            SELECT spot_price
            FROM snapshots
            WHERE exclude_from_analysis = 0
              AND timestamp_utc <= ?
            ORDER BY timestamp_utc DESC
            LIMIT 1
        """, (to_ts,))
        row = cursor.fetchone()
        spot_price = float(row["spot_price"] or 0) if row else 0.0

    from_ts = float(to_ts) - 30 * 60
    cursor.execute("""
        SELECT timestamp_utc, open, high, low, close, volume
        FROM ohlcv_candles
        WHERE symbol = 'BTCUSDT'
          AND timeframe = '1m'
          AND timestamp_utc BETWEEN ? AND ?
        ORDER BY timestamp_utc ASC
    """, (from_ts, to_ts))
    rows = [dict(row) for row in cursor.fetchall()]
    return ShortTermFlowContextEngine.calculate(
        timestamp_utc=to_ts,
        spot_price=spot_price or 0.0,
        candles=rows,
    )


def calculate_ohlcv_summary(rows: List[dict]) -> dict:
    """Summarize OHLCV movement for replay validation only."""
    empty = {
        "return": 0.0,
        "range": 0.0,
        "max_5m_up": 0.0,
        "max_15m_up": 0.0,
        "max_30m_up": 0.0,
        "max_5m_down": 0.0,
        "max_15m_down": 0.0,
        "max_30m_down": 0.0,
        "realized_vol": 0.0,
    }
    if not rows:
        return empty

    first_open = float(rows[0].get("open") or 0)
    last_close = float(rows[-1].get("close") or 0)
    if first_open <= 0:
        return empty

    high = max(float(r.get("high") or 0) for r in rows)
    low = min(float(r.get("low") or 0) for r in rows)
    returns_1m = [
        (float(r.get("close") or 0) - float(r.get("open") or 0)) / float(r.get("open") or 1) * 100
        for r in rows
        if float(r.get("open") or 0) > 0
    ]

    def best_move(minutes: int, direction: str) -> float:
        if len(rows) < minutes:
            return 0.0
        best = None
        for idx in range(0, len(rows) - minutes + 1):
            start_open = float(rows[idx].get("open") or 0)
            end_close = float(rows[idx + minutes - 1].get("close") or 0)
            if start_open <= 0:
                continue
            move = (end_close - start_open) / start_open * 100
            if best is None:
                best = move
            elif direction == "up":
                best = max(best, move)
            else:
                best = min(best, move)
        return round(best or 0.0, 3)

    realized_vol = 0.0
    if returns_1m:
        realized_vol = (sum(v * v for v in returns_1m) / len(returns_1m)) ** 0.5

    return {
        "return": round((last_close - first_open) / first_open * 100, 3),
        "range": round((high - low) / first_open * 100, 3),
        "max_5m_up": best_move(5, "up"),
        "max_15m_up": best_move(15, "up"),
        "max_30m_up": best_move(30, "up"),
        "max_5m_down": best_move(5, "down"),
        "max_15m_down": best_move(15, "down"),
        "max_30m_down": best_move(30, "down"),
        "realized_vol": round(realized_vol, 4),
    }


def _distribution(rows: List[dict], key: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for row in rows:
        value = row.get(key) or "UNKNOWN"
        out[str(value)] = out.get(str(value), 0) + 1
    return out


def _metric_summary(rows: List[dict], key: str) -> dict:
    values = [float(row.get(key) or 0) for row in rows]
    if not values:
        return {"min": 0.0, "avg": 0.0, "max": 0.0}
    return {
        "min": round(min(values), 2),
        "avg": round(sum(values) / len(values), 2),
        "max": round(max(values), 2),
    }


def _load_snapshot_rows(cursor, from_ts: Optional[float], to_ts: Optional[float]) -> List[dict]:
    if not to_ts:
        cursor.execute("SELECT MAX(timestamp_utc) FROM snapshots WHERE exclude_from_analysis = 0")
        row = cursor.fetchone()
        to_ts = row[0] if row and row[0] else time.time()
    if not from_ts:
        from_ts = to_ts - 24 * 3600

    cursor.execute("""
        SELECT timestamp_utc, snapshot_sequence_id, current_state, previous_state,
               candidate_state, execution_timing_state, synthetic_flow_pressure,
               liquidity_void_score, expansion_probability, compression_failure_risk,
               signal_cluster_score, iv_velocity, atm_iv, dealer_hedging_pressure,
               gamma_slope_state, gamma_acceleration_state, term_structure_state,
               spot_price, market_phase_hash
        FROM snapshots
        WHERE exclude_from_analysis = 0
          AND timestamp_utc BETWEEN ? AND ?
        ORDER BY timestamp_utc ASC
    """, (from_ts, to_ts))
    return [dict(row) for row in cursor.fetchall()]


def _load_ohlcv_rows(cursor, from_ts: Optional[float], to_ts: Optional[float]) -> List[dict]:
    if not to_ts:
        cursor.execute("SELECT MAX(timestamp_utc) FROM ohlcv_candles WHERE symbol='BTCUSDT' AND timeframe='1m'")
        row = cursor.fetchone()
        to_ts = row[0] if row and row[0] else time.time()
    if not from_ts:
        from_ts = to_ts - 24 * 3600

    cursor.execute("""
        SELECT exchange, symbol, timeframe, timestamp_utc, open, high, low,
               close, volume, quote_volume, trade_count, source_latency_ms,
               created_at_utc
        FROM ohlcv_candles
        WHERE symbol = 'BTCUSDT'
          AND timeframe = '1m'
          AND timestamp_utc BETWEEN ? AND ?
        ORDER BY timestamp_utc ASC
    """, (from_ts, to_ts))
    return [dict(row) for row in cursor.fetchall()]


def _decode_json_field(value):
    if not value:
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {"decode_error": True, "raw": value}


def _extract_short_term_flow_context(row: dict) -> dict:
    persisted = _decode_json_field(row.get("short_term_flow_breakdown_json"))
    if persisted:
        out = dict(persisted)
        out.setdefault("timestamp_utc", row.get("timestamp_utc"))
        out.setdefault("snapshot_sequence_id", row.get("snapshot_sequence_id"))
        return out
    flow = _decode_json_field(row.get("flow_breakdown_json"))
    replay = _decode_json_field(row.get("replay_alignment_json"))
    context = flow.get("short_term_flow_context") or replay.get("short_term_flow_context") or {}
    if not context:
        return {}
    out = dict(context)
    out.setdefault("timestamp_utc", row.get("timestamp_utc"))
    out.setdefault("snapshot_sequence_id", row.get("snapshot_sequence_id"))
    return out


def _short_term_flow_summary(contexts: List[dict]) -> dict:
    direction_keys = [
        "STRONG_SELL",
        "MODERATE_SELL",
        "WEAK_SELL",
        "NEUTRAL",
        "WEAK_BUY",
        "MODERATE_BUY",
        "STRONG_BUY",
    ]
    usable = [ctx for ctx in contexts if ctx.get("available")]
    if not usable:
        return {
            "sample_count": len(contexts),
            "available_count": 0,
            "min": 0.0,
            "avg": 0.0,
            "max": 0.0,
            "min_pressure": 0.0,
            "avg_pressure": 0.0,
            "max_pressure": 0.0,
            "max_intensity": 0.0,
            "distribution": {key: 0 for key in direction_keys},
            "direction_distribution": {key: 0 for key in direction_keys},
            "reason_distribution": {},
            "max_positive_snapshot": {},
            "max_negative_snapshot": {},
            "max_intensity_snapshot": {},
            "avg_components": {},
            "max_components": {},
            "note": "Experimental short-term flow context is diagnostic only.",
        }

    pressures = [float(ctx.get("short_term_flow_pressure") or 0) for ctx in usable]
    intensities = [float(ctx.get("short_term_flow_intensity") or 0) for ctx in usable]
    direction_distribution = {key: 0 for key in direction_keys}
    reason_distribution = {}
    for ctx in usable:
        direction = ctx.get("direction") or "UNKNOWN"
        reason = ctx.get("reason") or "UNKNOWN"
        direction_distribution[direction] = direction_distribution.get(direction, 0) + 1
        reason_distribution[reason] = reason_distribution.get(reason, 0) + 1
    max_ctx = max(usable, key=lambda ctx: float(ctx.get("short_term_flow_intensity") or 0))
    max_positive = max(usable, key=lambda ctx: float(ctx.get("short_term_flow_pressure") or 0))
    max_negative = min(usable, key=lambda ctx: float(ctx.get("short_term_flow_pressure") or 0))
    component_keys = sorted({
        key
        for ctx in usable
        for key in (ctx.get("components") or {}).keys()
    })
    avg_components = {}
    max_components = {}
    for key in component_keys:
        values = [float((ctx.get("components") or {}).get(key) or 0) for ctx in usable]
        avg_components[key] = round(sum(values) / len(values), 2) if values else 0.0
        max_components[key] = round(max(values, key=abs), 2) if values else 0.0
    return {
        "sample_count": len(contexts),
        "available_count": len(usable),
        "min": round(min(pressures), 2),
        "avg": round(sum(pressures) / len(pressures), 2),
        "max": round(max(pressures), 2),
        "min_pressure": round(min(pressures), 2),
        "avg_pressure": round(sum(pressures) / len(pressures), 2),
        "max_pressure": round(max(pressures), 2),
        "max_intensity": round(max(intensities), 2),
        "distribution": direction_distribution,
        "direction_distribution": direction_distribution,
        "reason_distribution": reason_distribution,
        "max_positive_snapshot": max_positive,
        "max_negative_snapshot": max_negative,
        "max_intensity_snapshot": max_ctx,
        "avg_components": avg_components,
        "max_components": max_components,
        "note": "Experimental short-term flow context is diagnostic only and does not affect MOS scoring.",
    }


@router.get("/source-diagnostics")
async def get_source_diagnostics():
    """Return Deribit reliability, active sources logic, and reaction generation statistics."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        # Latest snapshot info
        cursor.execute("""
            SELECT 
                timestamp_utc, active_sources, data_quality, data_quality_reason,
                deribit_status, deribit_age_sec, deribit_records_used, deribit_error,
                option_tickers_count, valid_greeks_count, valid_iv_count, valid_gamma_count,
                calls_count, puts_count, expiries_count, strikes_count
            FROM snapshots 
            ORDER BY timestamp_utc DESC LIMIT 1
        """)
        row = cursor.fetchone()
        snapshot_diagnostics = dict(row) if row else {}
        
        # Recent source breakdown
        cursor.execute("""
            SELECT active_sources, deribit_status, data_quality, COUNT(*) as count 
            FROM snapshots 
            WHERE timestamp_utc > ? 
            GROUP BY active_sources, deribit_status, data_quality
        """, (time.time() - 3600,))
        recent_sources = [dict(r) for r in cursor.fetchall()]
        
        # Reaction stats (last 24 hours)
        cursor.execute("""
            SELECT source, confidence, reaction_label, COUNT(*) as count 
            FROM event_level_reactions 
            WHERE event_timestamp_utc > ?
            GROUP BY source, confidence, reaction_label
        """, (time.time() - 86400,))
        reaction_stats = [dict(r) for r in cursor.fetchall()]

        # Deduplication stats
        cursor.execute("""
            SELECT classification_reason, COUNT(*) as count
            FROM event_level_reactions
            WHERE event_timestamp_utc > ? AND classification_reason = 'duplicate_reaction_cooldown'
            GROUP BY classification_reason
        """, (time.time() - 86400,))
        dedupe_row = cursor.fetchone()
        deduped_count = dedupe_row["count"] if dedupe_row else 0
        
        conn.close()
        
        return {
            "status": "ok",
            "snapshot_diagnostics": snapshot_diagnostics,
            "recent_sources_1h": recent_sources,
            "reaction_stats_24h": reaction_stats,
            "deduped_reactions_24h": deduped_count
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/diagnostics")
async def get_diagnostics():
    """Return comprehensive runtime version, database health, and schema diagnostics."""
    import os
    from pathlib import Path
    from engine.version import (
        CODE_VERSION,
        RESEARCH_SCHEMA_VERSION,
        ENGINE_PATCH_VERSION,
        PARTICLE_LOGIC_VERSION,
        FLOW_PRESSURE_SCALE,
    )
    from engine.history_db import _DB_PATH as HISTORY_DB_PATH
    from engine.ohlcv_collector import OHLCV_ENABLED, OHLCV_EXCHANGE, OHLCV_SYMBOL, OHLCV_TIMEFRAME
    
    cwd = os.getcwd()
    history_db = str(Path(HISTORY_DB_PATH).resolve())
    research_db = str(Path(DB_PATH).resolve())
    
    snapshot_count = 0
    latest_snapshot_ts = None
    latest_snapshot_code_version = None
    latest_snapshot_schema_version = None
    latest_snapshot_engine_patch_version = None
    integrity_check = "unknown"
    quick_check = "unknown"
    wal_mode = "unknown"
    tables = []
    event_count = 0
    event_outcome_count = 0
    event_level_reaction_count = 0
    event_outcome_worker = {}
    future_label_count = 0
    schema_valid = False
    missing_snapshot_columns = []
    ohlcv_count = 0
    latest_ohlcv_ts = None
    ohlcv_lag_sec = None
    debug_snapshot_count = 0
    latest_debug_snapshot_ts = None
    short_term_flow_enabled = True
    latest_short_term_flow_context = {}
    
    # Get schema validation from ResearchLogger
    try:
        from engine.research_logger import ResearchLogger
        # Access class-level instances - ResearchLogger is instantiated in data_manager
        # We re-check schema directly here
        import sqlite3 as _sqlite3
        _conn = _sqlite3.connect(DB_PATH, timeout=10)
        _c = _conn.cursor()
        _c.execute("PRAGMA table_info(snapshots)")
        existing_cols = [info[1] for info in _c.fetchall()]
        required_cols = [
            "snapshot_id", "code_version", "research_schema_version",
            "engine_patch_version", "synthetic_flow_pressure_scale",
            "data_quality_reason", "exclude_from_analysis", "exclude_reason",
            "schema_version", "market_phase_hash", "signal_cluster_score",
            "execution_timing_state", "dealer_hedging_pressure",
        ]
        missing_snapshot_columns = [c for c in required_cols if c not in existing_cols]
        schema_valid = len(missing_snapshot_columns) == 0
        _conn.close()
    except Exception:
        pass
    
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        # Integrity check
        cursor.execute("PRAGMA integrity_check")
        integrity_check = cursor.fetchone()[0]
        
        # Quick check
        cursor.execute("PRAGMA quick_check")
        quick_check = cursor.fetchone()[0]
        
        # WAL mode
        cursor.execute("PRAGMA journal_mode")
        wal_mode = cursor.fetchone()[0]
        
        # Tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cursor.fetchall()]
        
        # Snapshot count
        cursor.execute("SELECT COUNT(*) FROM snapshots")
        row = cursor.fetchone()
        if row:
            snapshot_count = row[0]
            
        # Latest snapshot — full version info
        cursor.execute("""
            SELECT timestamp_utc, code_version, schema_version, engine_patch_version 
            FROM snapshots ORDER BY timestamp_utc DESC LIMIT 1
        """)
        latest_row = cursor.fetchone()
        if latest_row:
            latest_snapshot_ts = latest_row[0]
            latest_snapshot_code_version = latest_row[1]
            latest_snapshot_schema_version = latest_row[2]
            latest_snapshot_engine_patch_version = latest_row[3]
        
        # Event count
        try:
            cursor.execute("SELECT COUNT(*) FROM events")
            event_count = cursor.fetchone()[0]
        except Exception:
            pass

        try:
            from engine.replay_outcome_engine import ReplayOutcomeEngine
            from workers.event_outcome_worker import get_event_outcome_worker_status
            ReplayOutcomeEngine.ensure_schema(conn)
            cursor.execute("SELECT COUNT(*) FROM event_outcomes")
            event_outcome_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM event_level_reactions")
            event_level_reaction_count = cursor.fetchone()[0]
            event_outcome_worker = get_event_outcome_worker_status()
            event_outcome_worker["db_diagnostics"] = ReplayOutcomeEngine.worker_diagnostics(conn)
        except Exception:
            pass
        
        # Future label count
        try:
            cursor.execute("SELECT COUNT(*) FROM future_labels")
            future_label_count = cursor.fetchone()[0]
        except Exception:
            pass

        # OHLCV validation layer diagnostics
        try:
            cursor.execute("""
                SELECT COUNT(*), MAX(timestamp_utc)
                FROM ohlcv_candles
                WHERE exchange = ? AND symbol = ? AND timeframe = ?
            """, (OHLCV_EXCHANGE, OHLCV_SYMBOL, OHLCV_TIMEFRAME))
            ohlcv_row = cursor.fetchone()
            if ohlcv_row:
                ohlcv_count = ohlcv_row[0] or 0
                latest_ohlcv_ts = ohlcv_row[1]
                if latest_ohlcv_ts:
                    ohlcv_lag_sec = round(time.time() - latest_ohlcv_ts, 1)
        except Exception:
            pass

        # Persistent debug breakdown diagnostics
        try:
            cursor.execute("SELECT COUNT(*), MAX(timestamp_utc) FROM debug_snapshots")
            debug_row = cursor.fetchone()
            if debug_row:
                debug_snapshot_count = debug_row[0] or 0
                latest_debug_snapshot_ts = debug_row[1]
        except Exception:
            pass

        try:
            latest_short_term_flow_context = get_short_term_flow_context(cursor)
        except Exception as exc:
            latest_short_term_flow_context = {
                "status": "experimental",
                "available": False,
                "reason": f"diagnostics_short_term_flow_failed:{exc}",
            }
                
        conn.close()
    except Exception as e:
        pass
    
    # History DB diagnostics
    history_db_info = {
        "integrity_check": "unknown",
        "snapshots": 0,
        "oi_history_rows": 0,
        "oi_history_status": "unused_or_deprecated",
        "option_contract_rows": 0,
        "option_contract_latest_ts": None,
        "option_contract_exchange_coverage": [],
    }
    try:
        import sqlite3
        if os.path.exists(HISTORY_DB_PATH):
            hconn = sqlite3.connect(HISTORY_DB_PATH, timeout=10)
            hcursor = hconn.cursor()
            hcursor.execute("PRAGMA integrity_check")
            history_db_info["integrity_check"] = hcursor.fetchone()[0]
            try:
                hcursor.execute("SELECT COUNT(*) FROM snapshots")
                history_db_info["snapshots"] = hcursor.fetchone()[0]
            except Exception:
                pass
            try:
                hcursor.execute("SELECT COUNT(*) FROM oi_history")
                history_db_info["oi_history_rows"] = hcursor.fetchone()[0]
            except Exception:
                pass
            try:
                hcursor.execute(
                    "SELECT COUNT(*), MAX(ts) FROM option_contract_snapshots"
                )
                contract_row = hcursor.fetchone()
                history_db_info["option_contract_rows"] = contract_row[0] or 0
                history_db_info["option_contract_latest_ts"] = contract_row[1]
                hcursor.execute(
                    """
                    SELECT exchange, COUNT(*) AS rows,
                           SUM(mark_iv IS NOT NULL AND mark_iv > 0) AS valid_iv,
                           SUM(volume_24h IS NOT NULL) AS valid_volume,
                           SUM(delta IS NOT NULL AND gamma IS NOT NULL
                               AND vega IS NOT NULL AND theta IS NOT NULL)
                               AS valid_greeks
                    FROM option_contract_snapshots
                    GROUP BY exchange
                    ORDER BY exchange
                    """
                )
                history_db_info["option_contract_exchange_coverage"] = [
                    {
                        "exchange": row[0],
                        "rows": row[1],
                        "valid_iv": row[2],
                        "valid_volume": row[3],
                        "valid_greeks": row[4],
                    }
                    for row in hcursor.fetchall()
                ]
            except Exception:
                pass
            hconn.close()
    except Exception:
        pass
        
    # Warmup suppression info
    warmup_info = {}
    try:
        from engine.state_engine import StateEngine
        warmup_info = {
            "event_warmup_suppression": StateEngine._total_snapshots_processed < StateEngine.WARMUP_MIN_SNAPSHOTS,
            "warmup_min_snapshots": StateEngine.WARMUP_MIN_SNAPSHOTS,
            "total_snapshots_processed": StateEngine._total_snapshots_processed,
        }
    except Exception:
        pass
        
    return {
        "code_version": CODE_VERSION,
        "research_schema_version": RESEARCH_SCHEMA_VERSION,
        "engine_patch_version": ENGINE_PATCH_VERSION,
        "particle_logic_version": PARTICLE_LOGIC_VERSION,
        "flow_pressure_scale": FLOW_PRESSURE_SCALE,
        "research_db_path": research_db,
        "history_db_path": history_db,
        "cwd": cwd,
        "snapshot_count": snapshot_count,
        "event_count": event_count,
        "event_outcome_count": event_outcome_count,
        "event_level_reaction_count": event_level_reaction_count,
        "event_outcome_worker": event_outcome_worker,
        "future_label_count": future_label_count,
        "latest_snapshot_ts": latest_snapshot_ts,
        "latest_snapshot_code_version": latest_snapshot_code_version,
        "latest_snapshot_schema_version": latest_snapshot_schema_version,
        "latest_snapshot_engine_patch_version": latest_snapshot_engine_patch_version,
        "wal_mode": wal_mode,
        "integrity_check": integrity_check,
        "quick_check": quick_check,
        "schema_valid": schema_valid,
        "missing_snapshot_columns": missing_snapshot_columns,
        "tables": tables,
        "history_db": history_db_info,
        "warmup_suppression": warmup_info,
        "ohlcv_enabled": OHLCV_ENABLED,
        "ohlcv_exchange": OHLCV_EXCHANGE,
        "ohlcv_symbol": OHLCV_SYMBOL,
        "ohlcv_timeframe": OHLCV_TIMEFRAME,
        "ohlcv_count": ohlcv_count,
        "latest_ohlcv_ts": latest_ohlcv_ts,
        "ohlcv_lag_sec": ohlcv_lag_sec,
        "debug_snapshot_count": debug_snapshot_count,
        "latest_debug_snapshot_ts": latest_debug_snapshot_ts,
        "short_term_flow_enabled": short_term_flow_enabled,
        "short_term_flow_status": "experimental_read_only",
        "latest_short_term_flow_context": latest_short_term_flow_context,
    }


@router.get("/ohlcv")
async def get_ohlcv(
    symbol: str = Query("BTCUSDT"),
    timeframe: str = Query("1m"),
    from_ts: Optional[float] = Query(None, alias="from"),
    to_ts: Optional[float] = Query(None, alias="to"),
    limit: int = Query(2000, le=10000),
):
    """Fetch stored OHLCV candles for replay/validation."""
    conn = get_db()
    cursor = conn.cursor()

    if not to_ts:
        to_ts = time.time()
    if not from_ts:
        from_ts = to_ts - 24 * 3600

    cursor.execute("""
        SELECT exchange, symbol, timeframe, timestamp_utc,
               open, high, low, close, volume, quote_volume,
               trade_count, source_latency_ms, created_at_utc
        FROM ohlcv_candles
        WHERE symbol = ?
          AND timeframe = ?
          AND timestamp_utc BETWEEN ? AND ?
        ORDER BY timestamp_utc ASC
        LIMIT ?
    """, (symbol, timeframe, from_ts, to_ts, limit))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "from": from_ts,
        "to": to_ts,
        "count": len(rows),
        "candles": rows,
    }


@router.get("/replay-alignment")
async def get_replay_alignment(
    from_ts: Optional[float] = Query(None, alias="from"),
    to_ts: Optional[float] = Query(None, alias="to"),
):
    """Compare OHLCV replay movement with persisted MOS outputs.

    This endpoint is read-only validation. It does not affect live scoring.
    """
    conn = get_db()
    cursor = conn.cursor()

    if not to_ts:
        cursor.execute("SELECT MAX(timestamp_utc) FROM snapshots WHERE exclude_from_analysis = 0")
        row = cursor.fetchone()
        to_ts = row[0] if row and row[0] else time.time()
    if not from_ts:
        from_ts = to_ts - 24 * 3600

    snapshot_rows = _load_snapshot_rows(cursor, from_ts, to_ts)
    ohlcv_rows = _load_ohlcv_rows(cursor, from_ts, to_ts)
    latest_spot = float(snapshot_rows[-1].get("spot_price") or 0) if snapshot_rows else 0.0
    from engine.short_term_flow_context_engine import ShortTermFlowContextEngine
    short_term_flow_context = ShortTermFlowContextEngine.calculate(
        timestamp_utc=to_ts,
        spot_price=latest_spot,
        candles=ohlcv_rows,
    )

    cursor.execute("""
        SELECT event_type, COUNT(*) AS cnt
        FROM events
        WHERE timestamp_utc BETWEEN ? AND ?
        GROUP BY event_type
        ORDER BY cnt DESC
    """, (from_ts, to_ts))
    event_distribution = {row["event_type"]: row["cnt"] for row in cursor.fetchall()}
    conn.close()

    flow_values = [abs(float(row.get("synthetic_flow_pressure") or 0)) for row in snapshot_rows]
    signal_values = [float(row.get("signal_cluster_score") or 0) for row in snapshot_rows]
    expansion_values = [float(row.get("expansion_probability") or 0) for row in snapshot_rows]
    void_values = [float(row.get("liquidity_void_score") or 0) for row in snapshot_rows]

    ohlcv_summary = calculate_ohlcv_summary(ohlcv_rows)
    state_distribution = _distribution(snapshot_rows, "current_state")
    execution_distribution = _distribution(snapshot_rows, "execution_timing_state")
    total_snapshots = len(snapshot_rows) or 1
    notes = []
    if ohlcv_summary["return"] > 0.5:
        notes.append("ohlcv_showed_directional_up_move")
    elif ohlcv_summary["return"] < -0.5:
        notes.append("ohlcv_showed_directional_down_move")
    if flow_values and max(flow_values) < 20:
        notes.append("flow_remained_weak")
    if state_distribution.get("PINNING", 0) / total_snapshots > 0.7:
        notes.append("state_remained_pin_dominant")
    if state_distribution.get("COMPRESSION", 0) / total_snapshots > 0.7:
        notes.append("state_remained_compression_dominant")
    if execution_distribution.get("WAIT", 0) / total_snapshots > 0.8:
        notes.append("execution_remained_wait_dominant")
    if event_distribution.get("VOLATILITY_EXPANSION", 0) > 0:
        notes.append("volatility_expansion_events_detected")
    if event_distribution.get("FLOW_SURGE", 0) == 0:
        notes.append("no_flow_surge_detected")
    if (
        short_term_flow_context.get("available")
        and short_term_flow_context.get("short_term_flow_intensity", 0) >= 20
        and flow_values
        and max(flow_values) < 15
    ):
        notes.append("short_term_flow_detected_legacy_flow_weak")

    return {
        "status": "ok",
        "window": {
            "from": from_ts,
            "to": to_ts,
        },
        "ohlcv_summary": ohlcv_summary,
        "short_term_flow_context": short_term_flow_context,
        "mos_summary": {
            "state_distribution": state_distribution,
            "execution_distribution": execution_distribution,
            "event_distribution": event_distribution,
            "max_signal_cluster_score": round(max(signal_values), 2) if signal_values else 0.0,
            "max_expansion_probability": round(max(expansion_values), 2) if expansion_values else 0.0,
            "max_flow_intensity": round(max(flow_values), 2) if flow_values else 0.0,
            "max_liquidity_void_score": round(max(void_values), 2) if void_values else 0.0,
            "snapshot_count": len(snapshot_rows),
            "ohlcv_count": len(ohlcv_rows),
        },
        "alignment_notes": notes,
        "note": "OHLCV alignment is read-only validation and does not feed live MOS scoring.",
    }


@router.get("/debug-snapshots/latest")
async def get_debug_snapshot_latest(
    decode: bool = Query(True),
):
    """Return the latest persisted per-snapshot debug breakdown."""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT *
            FROM debug_snapshots
            ORDER BY id DESC
            LIMIT 1
        """)
        row = cursor.fetchone()
    except Exception as exc:
        conn.close()
        return {
            "status": "error",
            "reason": f"debug_snapshots_unavailable:{exc}",
        }
    conn.close()

    if not row:
        return {
            "status": "ok",
            "debug_snapshot": None,
        }

    payload = dict(row)
    if decode:
        for key in [
            "flow_breakdown_json",
            "void_breakdown_json",
            "signal_breakdown_json",
            "transition_breakdown_json",
            "execution_breakdown_json",
            "state_breakdown_json",
            "gamma_breakdown_json",
            "replay_alignment_json",
            "short_term_flow_breakdown_json",
        ]:
            payload[key.replace("_json", "")] = _decode_json_field(payload.get(key))

    return {
        "status": "ok",
        "debug_snapshot": payload,
    }


@router.get("/debug-snapshots/summary")
async def get_debug_snapshots_summary(
    from_sequence: Optional[int] = Query(None),
    to_sequence: Optional[int] = Query(None),
    from_ts: Optional[float] = Query(None, alias="from"),
    to_ts: Optional[float] = Query(None, alias="to"),
):
    """Return health summary for persisted debug breakdown rows."""
    where = []
    params = []
    if from_sequence is not None:
        where.append("d.snapshot_sequence_id >= ?")
        params.append(from_sequence)
    if to_sequence is not None:
        where.append("d.snapshot_sequence_id <= ?")
        params.append(to_sequence)
    if from_ts is not None:
        where.append("s.timestamp_utc >= ?")
        params.append(from_ts)
    if to_ts is not None:
        where.append("s.timestamp_utc <= ?")
        params.append(to_ts)
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    source_sql = "debug_snapshots d JOIN snapshots s ON s.snapshot_id = d.snapshot_id"

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(f"""
            SELECT COUNT(*) AS n,
                   MIN(d.snapshot_sequence_id) AS min_seq,
                   MAX(d.snapshot_sequence_id) AS max_seq,
                   MAX(d.timestamp_utc) AS latest_ts
            FROM {source_sql}
            {where_sql}
        """, params)
        base = dict(cursor.fetchone())

        cursor.execute(f"""
            SELECT d.code_version, d.research_schema_version, d.engine_patch_version, COUNT(*) AS n
            FROM {source_sql}
            {where_sql}
            GROUP BY d.code_version, d.research_schema_version, d.engine_patch_version
        """, params)
        versions = [dict(row) for row in cursor.fetchall()]

        cursor.execute(f"""
            SELECT d.current_state, COUNT(*) AS n
            FROM {source_sql}
            {where_sql}
            GROUP BY d.current_state
            ORDER BY n DESC
        """, params)
        state_distribution = [dict(row) for row in cursor.fetchall()]

        cursor.execute(f"""
            SELECT d.execution_timing_state, COUNT(*) AS n
            FROM {source_sql}
            {where_sql}
            GROUP BY d.execution_timing_state
            ORDER BY n DESC
        """, params)
        execution_distribution = [dict(row) for row in cursor.fetchall()]

        missing = {}
        for field in [
            "flow_breakdown_json",
            "void_breakdown_json",
            "signal_breakdown_json",
            "transition_breakdown_json",
            "execution_breakdown_json",
            "state_breakdown_json",
            "gamma_breakdown_json",
            "replay_alignment_json",
            "short_term_flow_breakdown_json",
        ]:
            cursor.execute(f"""
                SELECT COUNT(*) AS n
                FROM {source_sql}
                {where_sql}
                {"AND" if where_sql else "WHERE"} (d.{field} IS NULL OR d.{field} = '')
            """, params)
            missing[field] = cursor.fetchone()["n"]

        cursor.execute(f"""
            SELECT COUNT(*) AS n
            FROM {source_sql}
            {where_sql}
        """, params)
        linked_snapshots = cursor.fetchone()["n"]

        cursor.execute(f"""
            SELECT d.state_breakdown_json
            FROM {source_sql}
            {where_sql}
        """, params)
        state_debug_rows = [_decode_json_field(row["state_breakdown_json"]) for row in cursor.fetchall()]
        absorbed_rows = [
            row for row in state_debug_rows
            if row.get("pinning_absorbed_range_compression")
        ]
        close_rows = [
            row for row in state_debug_rows
            if row.get("range_compression_detected")
            and abs(float(row.get("score_gap", 0) or 0)) <= 20
        ]
        over_cap_rows = [
            row for row in state_debug_rows
            if row.get("pinning_score_over_cap")
        ]
        score_gaps = [float(row.get("score_gap", 0) or 0) for row in absorbed_rows]
        pinning_absorption_summary = {
            "range_compression_absorbed_by_pinning_count": len(absorbed_rows),
            "avg_score_gap": round(sum(score_gaps) / len(score_gaps), 2) if score_gaps else 0,
            "max_score_gap": round(max(score_gaps), 2) if score_gaps else 0,
            "compression_score_close_to_pinning_count": len(close_rows),
            "pinning_score_over_cap_count": len(over_cap_rows),
        }

        cursor.execute(f"""
            SELECT d.timestamp_utc, d.snapshot_sequence_id,
                   d.flow_breakdown_json, d.replay_alignment_json,
                   d.short_term_flow_breakdown_json
            FROM {source_sql}
            {where_sql}
        """, params)
        short_term_contexts = [
            _extract_short_term_flow_context(dict(row))
            for row in cursor.fetchall()
        ]
        short_term_flow = _short_term_flow_summary(short_term_contexts)
    except Exception as exc:
        conn.close()
        return {
            "status": "error",
            "reason": f"debug_snapshots_unavailable:{exc}",
        }
    conn.close()

    return {
        "status": "ok",
        "summary": {
            **base,
            "linked_snapshots": linked_snapshots,
            "versions": versions,
            "state_distribution": state_distribution,
            "execution_distribution": execution_distribution,
            "missing_breakdown_fields": missing,
            "pinning_absorption_summary": pinning_absorption_summary,
            "short_term_flow": short_term_flow,
            "short_term_flow_summary": short_term_flow,
        },
    }



@router.get("/signal-debug/latest")
async def get_signal_debug():
    """Return the last signal cluster score breakdown."""
    from engine.state_engine import StateEngine
    raw = StateEngine.get_cluster_debug()
    return {
        "status": "ok",
        "signal_cluster_score": raw.get("total", 0),
        "components": {
            "gamma_component": raw.get("gamma_component", 0),
            "iv_velocity_component": raw.get("iv_velocity_component", 0),
            "flow_component": raw.get("flow_component", 0),
            "liquidity_void_component": raw.get("liquidity_void_component", 0),
            "expansion_component": raw.get("expansion_component", 0),
            "hedge_component": raw.get("hedge_component", 0),
            "term_structure_component": raw.get("term_structure_component", 0),
        },
        "inputs": {
            "gamma_slope_state": raw.get("gamma_input", {}).get("gamma_slope_state"),
            "gamma_acceleration_state": raw.get("gamma_input", {}).get("gamma_acceleration_state"),
            "iv_velocity": raw.get("iv_velocity_input"),
            "synthetic_flow_pressure": raw.get("flow_momentum_input"),
            "flow_intensity": raw.get("flow_intensity_input"),
            "liquidity_void_score": raw.get("void_score_input"),
            "expansion_probability": raw.get("expansion_prob_input"),
            "dealer_hedging_pressure": raw.get("hedge_pressure_label"),
            "term_structure_state": raw.get("term_structure_input"),
        },
        "reason": raw.get("reason", "component_breakdown"),
        "cluster_breakdown": raw,
    }


@router.get("/signal-debug/summary")
async def get_signal_debug_summary(
    from_ts: Optional[float] = Query(None, alias="from"),
    to_ts: Optional[float] = Query(None, alias="to"),
):
    """Return DB-backed signal cluster summary plus in-memory component context."""
    from engine.state_engine import StateEngine
    conn = get_db()
    cursor = conn.cursor()
    rows = _load_snapshot_rows(cursor, from_ts, to_ts)
    conn.close()

    if not rows:
        return {
            "status": "ok",
            "sample_count": 0,
            "signal_summary": StateEngine.get_cluster_debug_summary(),
        }

    scores = [float(row.get("signal_cluster_score") or 0) for row in rows]
    buckets = {"0_20": 0, "20_40": 0, "40_50": 0, "50_plus": 0}
    for score in scores:
        if score < 20:
            buckets["0_20"] += 1
        elif score < 40:
            buckets["20_40"] += 1
        elif score < 50:
            buckets["40_50"] += 1
        else:
            buckets["50_plus"] += 1

    max_snapshot = max(rows, key=lambda r: float(r.get("signal_cluster_score") or 0))
    missing_components = []
    if max(abs(float(r.get("synthetic_flow_pressure") or 0)) for r in rows) < 20:
        missing_components.append("flow_component")
    if max(float(r.get("liquidity_void_score") or 0) for r in rows) < 50:
        missing_components.append("liquidity_void_component")
    return {
        "status": "ok",
        "sample_count": len(rows),
        "score": _metric_summary(rows, "signal_cluster_score"),
        "score_bucket_distribution": buckets,
        "max_score_snapshot": max_snapshot,
        "average_component_contributions": StateEngine.get_cluster_debug_summary().get("avg_components", {}),
        "top_missing_components": missing_components,
        "why_not_cross_50": (
            "flow_and_void_components_low"
            if max(scores) < 50 else "score_crossed_50"
        ),
        "cluster_summary": StateEngine.get_cluster_debug_summary(),
    }


@router.get("/volatility-debug/latest")
async def get_volatility_debug():
    """Return the last volatility routing and IV velocity debug info."""
    from engine.volatility_engine import VolatilityEngine
    return {
        "status": "ok",
        "volatility_debug": VolatilityEngine.get_debug(),
    }


@router.get("/void-debug/latest")
async def get_void_debug():
    """Return the last liquidity void score breakdown."""
    from engine.liquidity_void_engine import LiquidityVoidEngine
    return {
        "status": "ok",
        "void_debug": LiquidityVoidEngine.get_debug(),
    }


@router.get("/void-debug/summary")
async def get_void_debug_summary(
    from_ts: Optional[float] = Query(None, alias="from"),
    to_ts: Optional[float] = Query(None, alias="to"),
):
    """Return DB-backed liquidity void score summary plus in-memory component context."""
    from engine.liquidity_void_engine import LiquidityVoidEngine
    conn = get_db()
    cursor = conn.cursor()
    rows = _load_snapshot_rows(cursor, from_ts, to_ts)
    conn.close()

    if not rows:
        return {
            "status": "ok",
            "sample_count": 0,
            "void_summary": LiquidityVoidEngine.get_debug_summary(),
        }

    distribution = {"0_20": 0, "20_40": 0, "40_60": 0, "60_80": 0, "80_100": 0}
    for row in rows:
        score = float(row.get("liquidity_void_score") or 0)
        if score < 20:
            distribution["0_20"] += 1
        elif score < 40:
            distribution["20_40"] += 1
        elif score < 60:
            distribution["40_60"] += 1
        elif score < 80:
            distribution["60_80"] += 1
        else:
            distribution["80_100"] += 1

    max_snapshot = max(rows, key=lambda r: float(r.get("liquidity_void_score") or 0))
    return {
        "status": "ok",
        "sample_count": len(rows),
        "score": _metric_summary(rows, "liquidity_void_score"),
        "score_distribution": distribution,
        "max_score_snapshot": max_snapshot,
        "limiting_reasons": (
            ["no_40_plus_void_scores_in_window"]
            if max(float(r.get("liquidity_void_score") or 0) for r in rows) < 40 else []
        ),
        "void_summary": LiquidityVoidEngine.get_debug_summary(),
    }


@router.get("/state-debug/latest")
async def get_state_debug():
    """Return the last state machine debug info."""
    from engine.state_engine import StateEngine
    debug = StateEngine.get_state_debug()
    try:
        conn = get_db()
        cursor = conn.cursor()
        debug["ohlcv_context"] = get_ohlcv_context(cursor)
        conn.close()
    except Exception as e:
        debug["ohlcv_context"] = {
            "available": False,
            "reason": f"ohlcv_context_failed:{str(e)}",
        }
    return {
        "status": "ok",
        "state_debug": debug,
    }


@router.get("/state-debug/summary")
async def get_state_debug_summary(
    from_ts: Optional[float] = Query(None, alias="from"),
    to_ts: Optional[float] = Query(None, alias="to"),
):
    """Return state machine debug aggregates for persisted snapshots and current process."""
    from engine.state_engine import StateEngine
    conn = get_db()
    cursor = conn.cursor()
    rows = _load_snapshot_rows(cursor, from_ts, to_ts)
    conn.close()

    blocked = {}
    for row in rows:
        current = row.get("current_state")
        candidate = row.get("candidate_state")
        if current and candidate and current != candidate:
            key = f"{current}->{candidate}:persisted_candidate_not_confirmed"
            blocked[key] = blocked.get(key, 0) + 1

    pinning_count = sum(1 for r in rows if r.get("current_state") == "PINNING")
    pinning_share = round(pinning_count / len(rows) * 100, 2) if rows else 0
    memory_summary = StateEngine.get_state_debug_summary(from_ts=from_ts, to_ts=to_ts)
    return {
        "status": "ok",
        "sample_count": len(rows),
        "state_distribution": _distribution(rows, "current_state"),
        "candidate_state_distribution": _distribution(rows, "candidate_state"),
        "raw_state_distribution": {},
        "execution_distribution": _distribution(rows, "execution_timing_state"),
        "avg_scores": {
            "pinning_score": memory_summary.get("avg_pinning_score", 0),
            "compression_score": memory_summary.get("avg_compression_score", 0),
            "transition_score": memory_summary.get("avg_transition_score", 0),
            "expansion_score": 0,
        },
        "blocked_transitions": blocked,
        "phase_context_distribution": memory_summary.get("phase_context_distribution", {}),
        "pinning_share_pct": pinning_share,
        "top_transition_block_reasons": list(blocked.keys())[:10],
        "state_debug_summary": memory_summary,
    }


@router.get("/execution-debug/latest")
async def get_execution_debug():
    """Return the last execution timing scoring breakdown."""
    from engine.execution_timing_engine import ExecutionTimingEngine
    return {
        "status": "ok",
        "execution_debug": ExecutionTimingEngine.get_debug(),
    }


@router.get("/execution-debug/summary")
async def get_execution_debug_summary(
    from_ts: Optional[float] = Query(None, alias="from"),
    to_ts: Optional[float] = Query(None, alias="to"),
):
    """Return persisted execution state distribution and latest score context."""
    from engine.execution_timing_engine import ExecutionTimingEngine
    conn = get_db()
    cursor = conn.cursor()
    rows = _load_snapshot_rows(cursor, from_ts, to_ts)
    conn.close()

    latest_debug = ExecutionTimingEngine.get_debug()
    scores = latest_debug.get("scores", {}) if latest_debug else {}
    why_not = latest_debug.get("why_not", {}) if latest_debug else {}
    structure_rows = [
        row for row in rows
        if row.get("execution_timing_state") == "STRUCTURE_UNSTABLE"
    ]

    return {
        "status": "ok",
        "sample_count": len(rows),
        "execution_distribution": _distribution(rows, "execution_timing_state"),
        "latest_scores": scores,
        "latest_why_not": why_not,
        "structure_unstable_snapshots": structure_rows[:25],
        "top_why_not_reasons": list(why_not.values())[:10],
        "note": "Historical per-score maxima require live debug history; persisted snapshots keep final execution state only.",
    }


@router.get("/flow-debug/latest")
async def get_flow_debug():
    """Return the last synthetic flow pressure debug breakdown.
    
    Use this to understand why buy/sell pressure is dominant.
    - flow_pressure > 50 → bull_strength > 0
    - flow_pressure < 50 → bear_strength > 0
    - Negative flow_momentum_score is expected in bearish/neutral market.
    """
    from engine.synthetic_orderflow_engine import SyntheticOrderflowEngine
    flow_debug = SyntheticOrderflowEngine.get_debug()
    try:
        conn = get_db()
        cursor = conn.cursor()
        ohlcv_context = get_ohlcv_context(cursor)
        short_term_flow_context = get_short_term_flow_context(cursor)
        flow_debug["ohlcv_1m_context"] = ohlcv_context
        flow_debug["ohlcv_context"] = ohlcv_context
        flow_debug["short_term_flow_context"] = short_term_flow_context
        flow_debug["short_term_flow_note"] = (
            "Experimental OHLCV 1m context only; live synthetic_flow_pressure and FLOW_SURGE are unchanged."
        )
        conn.close()
    except Exception as e:
        context_error = {
            "available": False,
            "reason": f"ohlcv_context_failed:{str(e)}",
        }
        flow_debug["ohlcv_1m_context"] = context_error
        flow_debug["ohlcv_context"] = context_error
        flow_debug["short_term_flow_context"] = context_error
    return {
        "status": "ok",
        "flow_debug": flow_debug,
    }


@router.get("/flow-debug/summary")
async def get_flow_debug_summary(
    from_ts: Optional[float] = Query(None, alias="from"),
    to_ts: Optional[float] = Query(None, alias="to"),
):
    """Return DB-backed synthetic flow aggregate plus in-memory component context."""
    from engine.synthetic_orderflow_engine import SyntheticOrderflowEngine
    conn = get_db()
    cursor = conn.cursor()

    if not to_ts:
        cursor.execute("SELECT MAX(timestamp_utc) FROM snapshots WHERE exclude_from_analysis = 0")
        row = cursor.fetchone()
        to_ts = row[0] if row and row[0] else time.time()
    if not from_ts:
        from_ts = to_ts - 24 * 3600

    cursor.execute("""
        SELECT timestamp_utc, snapshot_sequence_id, current_state, execution_timing_state,
               synthetic_flow_pressure, iv_velocity, liquidity_void_score,
               expansion_probability, signal_cluster_score
        FROM snapshots
        WHERE exclude_from_analysis = 0
          AND timestamp_utc BETWEEN ? AND ?
        ORDER BY timestamp_utc ASC
    """, (from_ts, to_ts))
    rows = [dict(row) for row in cursor.fetchall()]

    def _empty_summary():
        return {
            "min_flow": 0,
            "avg_flow": 0,
            "max_flow": 0,
            "neutral_count": 0,
            "weak_buy_count": 0,
            "weak_sell_count": 0,
            "moderate_buy_count": 0,
            "moderate_sell_count": 0,
            "strong_buy_count": 0,
            "strong_sell_count": 0,
            "max_buy_snapshot": {},
            "max_sell_snapshot": {},
            "avg_buying_components": {},
            "avg_selling_components": {},
            "max_buying_components": {},
            "max_selling_components": {},
            "reason_distribution": {},
            "ohlcv_1m_context": get_ohlcv_context(cursor, to_ts),
            "short_term_flow_context": get_short_term_flow_context(cursor, to_ts),
            "short_term_flow_summary": _short_term_flow_summary([]),
        }

    if not rows:
        summary = _empty_summary()
        conn.close()
        return {
            "status": "ok",
            "from": from_ts,
            "to": to_ts,
            "sample_count": 0,
            "flow_summary": summary,
        }

    flows = [float(r["synthetic_flow_pressure"] or 0) for r in rows]
    counts = {
        "neutral_count": 0,
        "weak_buy_count": 0,
        "weak_sell_count": 0,
        "moderate_buy_count": 0,
        "moderate_sell_count": 0,
        "strong_buy_count": 0,
        "strong_sell_count": 0,
    }
    for flow in flows:
        if flow <= -50:
            counts["strong_sell_count"] += 1
        elif flow <= -20:
            counts["moderate_sell_count"] += 1
        elif flow < -10:
            counts["weak_sell_count"] += 1
        elif flow <= 10:
            counts["neutral_count"] += 1
        elif flow < 20:
            counts["weak_buy_count"] += 1
        elif flow < 50:
            counts["moderate_buy_count"] += 1
        else:
            counts["strong_buy_count"] += 1

    max_buy = max(rows, key=lambda r: float(r["synthetic_flow_pressure"] or 0))
    max_sell = min(rows, key=lambda r: float(r["synthetic_flow_pressure"] or 0))
    in_memory_summary = SyntheticOrderflowEngine.get_debug_summary()
    latest_debug = SyntheticOrderflowEngine.get_debug()
    conn_context = get_ohlcv_context(cursor, to_ts)
    ohlcv_rows = _load_ohlcv_rows(cursor, from_ts, to_ts)
    ohlcv_summary = calculate_ohlcv_summary(ohlcv_rows)
    from engine.short_term_flow_context_engine import ShortTermFlowContextEngine
    latest_spot = float(rows[-1].get("spot_price") or 0) if rows else 0.0
    short_term_flow_context = ShortTermFlowContextEngine.calculate(
        timestamp_utc=to_ts,
        spot_price=latest_spot,
        candles=ohlcv_rows,
    )
    cursor.execute("""
        SELECT d.timestamp_utc, d.snapshot_sequence_id,
               d.flow_breakdown_json, d.replay_alignment_json,
               d.short_term_flow_breakdown_json
        FROM debug_snapshots d
        JOIN snapshots s ON s.snapshot_id = d.snapshot_id
        WHERE s.timestamp_utc BETWEEN ? AND ?
        ORDER BY s.timestamp_utc ASC
    """, (from_ts, to_ts))
    short_term_contexts = [
        _extract_short_term_flow_context(dict(row))
        for row in cursor.fetchall()
    ]
    short_term_flow_summary = _short_term_flow_summary(short_term_contexts)
    conn.close()

    return {
        "status": "ok",
        "from": from_ts,
        "to": to_ts,
        "sample_count": len(rows),
        "flow_summary": {
            "min_flow": round(min(flows), 2),
            "avg_flow": round(sum(flows) / len(flows), 2),
            "max_flow": round(max(flows), 2),
            **counts,
            "distribution": {
                "strong_sell": counts["strong_sell_count"],
                "moderate_sell": counts["moderate_sell_count"],
                "weak_sell": counts["weak_sell_count"],
                "neutral": counts["neutral_count"],
                "weak_buy": counts["weak_buy_count"],
                "moderate_buy": counts["moderate_buy_count"],
                "strong_buy": counts["strong_buy_count"],
            },
            "max_buy_snapshot": max_buy,
            "max_sell_snapshot": max_sell,
            "avg_buying_components": {
                "bull_strength": in_memory_summary.get("avg_bull_strength", 0),
                "price_velocity_up": in_memory_summary.get("avg_bull_strength", 0),
            },
            "avg_selling_components": {
                "bear_strength": in_memory_summary.get("avg_bear_strength", 0),
                "price_velocity_down": in_memory_summary.get("avg_bear_strength", 0),
            },
            "max_buying_components": latest_debug.get("buying_components", {}),
            "max_selling_components": latest_debug.get("selling_components", {}),
            "reason_distribution": in_memory_summary.get("reason_distribution", {}),
            "component_summary_source": "in_memory_current_process_for_components_db_for_snapshots",
            "ohlcv_1m_context": conn_context,
            "ohlcv_context": conn_context,
            "ohlcv_summary": ohlcv_summary,
            "short_term_flow_context": short_term_flow_context,
            "short_term_flow_summary": short_term_flow_summary,
        },
    }


@router.get("/short-term-flow-debug/latest")
async def get_short_term_flow_debug_latest():
    """Return experimental short-term OHLCV flow context.

    This endpoint is diagnostic only. It does not affect live synthetic flow,
    state, execution timing, signal cluster, expansion probability, or events.
    """
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT timestamp_utc, snapshot_sequence_id,
                   flow_breakdown_json, replay_alignment_json,
                   short_term_flow_breakdown_json
            FROM debug_snapshots
            ORDER BY id DESC
            LIMIT 1
        """)
        row = cursor.fetchone()
        if row:
            persisted = _extract_short_term_flow_context(dict(row))
            if persisted:
                conn.close()
                return {
                    "status": "ok",
                    "source": "debug_snapshots.short_term_flow_breakdown_json",
                    "short_term_flow_context": persisted,
                    "note": "Experimental read-only context; live MOS scoring is unchanged.",
                }
        computed = get_short_term_flow_context(cursor)
    finally:
        conn.close()

    return {
        "status": "ok",
        "source": "computed_from_ohlcv_candles",
        "short_term_flow_context": computed,
        "note": "Experimental read-only context; live MOS scoring is unchanged.",
    }


@router.get("/short-term-flow-debug/summary")
async def get_short_term_flow_debug_summary(
    from_ts: Optional[float] = Query(None, alias="from"),
    to_ts: Optional[float] = Query(None, alias="to"),
):
    """Return persisted experimental short-term flow summary."""
    conn = get_db()
    cursor = conn.cursor()
    if not to_ts:
        cursor.execute("SELECT MAX(timestamp_utc) FROM snapshots WHERE exclude_from_analysis = 0")
        row = cursor.fetchone()
        to_ts = row[0] if row and row[0] else time.time()
    if not from_ts:
        from_ts = to_ts - 24 * 3600

    cursor.execute("""
        SELECT d.timestamp_utc, d.snapshot_sequence_id,
               d.flow_breakdown_json, d.replay_alignment_json,
               d.short_term_flow_breakdown_json
        FROM debug_snapshots d
        JOIN snapshots s ON s.snapshot_id = d.snapshot_id
        WHERE s.exclude_from_analysis = 0
          AND s.timestamp_utc BETWEEN ? AND ?
        ORDER BY s.timestamp_utc ASC
    """, (from_ts, to_ts))
    contexts = [
        _extract_short_term_flow_context(dict(row))
        for row in cursor.fetchall()
    ]
    computed_latest = get_short_term_flow_context(cursor, to_ts)
    conn.close()

    return {
        "status": "ok",
        "from": from_ts,
        "to": to_ts,
        "summary": _short_term_flow_summary(contexts),
        "latest_computed_context": computed_latest,
        "note": "Experimental read-only context; live MOS scoring and events are unchanged.",
    }


@router.get("/expansion-debug/latest")
async def get_expansion_debug():
    """Return latest expansion probability contribution breakdown."""
    from engine.regime_transition_engine import RegimeTransitionEngine
    return {
        "status": "ok",
        "expansion_debug": RegimeTransitionEngine.get_debug(),
    }


@router.get("/expansion-debug/summary")
async def get_expansion_debug_summary(
    from_ts: Optional[float] = Query(None, alias="from"),
    to_ts: Optional[float] = Query(None, alias="to"),
):
    """Return DB-backed expansion probability summary plus in-memory components."""
    from engine.regime_transition_engine import RegimeTransitionEngine
    conn = get_db()
    cursor = conn.cursor()
    rows = _load_snapshot_rows(cursor, from_ts, to_ts)
    conn.close()

    if not rows:
        return {
            "status": "ok",
            "sample_count": 0,
            "expansion_summary": RegimeTransitionEngine.get_debug_summary(),
        }

    max_snapshot = max(rows, key=lambda r: float(r.get("expansion_probability") or 0))
    max_flow_intensity = max(abs(float(r.get("synthetic_flow_pressure") or 0)) for r in rows)
    max_void = max(float(r.get("liquidity_void_score") or 0) for r in rows)
    limiting = []
    if max_flow_intensity < 20:
        limiting.append("flow_intensity_below_20")
    if max_void < 50:
        limiting.append("liquidity_void_below_50")
    return {
        "status": "ok",
        "sample_count": len(rows),
        "expansion_probability": _metric_summary(rows, "expansion_probability"),
        "compression_failure_risk": _metric_summary(rows, "compression_failure_risk"),
        "max_snapshot": max_snapshot,
        "limiting_components": limiting,
        "why_not_cross_50": "low_flow_or_void_confirmation" if max_snapshot.get("expansion_probability", 0) < 50 else "crossed_50",
        "expansion_summary": RegimeTransitionEngine.get_debug_summary(),
    }


@router.get("/transition-debug/latest")
async def get_transition_debug():
    """Alias for expansion probability debug, using task terminology."""
    from engine.regime_transition_engine import RegimeTransitionEngine
    debug = RegimeTransitionEngine.get_debug()
    return {
        "status": "ok",
        "transition_debug": debug,
        "expansion_debug": debug,
    }


@router.get("/transition-debug/summary")
async def get_transition_debug_summary(
    from_ts: Optional[float] = Query(None, alias="from"),
    to_ts: Optional[float] = Query(None, alias="to"),
):
    """Alias for DB-backed expansion probability summary."""
    return await get_expansion_debug_summary(from_ts=from_ts, to_ts=to_ts)


@router.get("/hedging-debug/latest")
async def get_hedging_debug():
    """Return the last dealer hedging pressure breakdown."""
    from engine.dealer_hedging_engine import DealerHedgingEngine
    return {
        "status": "ok",
        "hedging_debug": DealerHedgingEngine.get_debug(),
    }


@router.get("/market-memory/readiness")
async def get_market_memory_readiness():
    """Assess database readiness for future Market Memory Engine.
    
    Does NOT build Market Memory. Only evaluates data quality prerequisites.
    """
    from engine.version import RESEARCH_SCHEMA_VERSION, FLOW_PRESSURE_SCALE
    
    reasons = []
    metrics = {}
    
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        # Integrity
        cursor.execute("PRAGMA integrity_check")
        integrity = cursor.fetchone()[0]
        metrics["sqlite_integrity"] = integrity
        if integrity != "ok":
            reasons.append("datastore_integrity_failed")
        
        # Schema version
        metrics["schema_version"] = RESEARCH_SCHEMA_VERSION
        metrics["flow_scale"] = FLOW_PRESSURE_SCALE
        
        # Snapshot count
        cursor.execute("SELECT COUNT(*) FROM snapshots WHERE exclude_from_analysis = 0")
        snapshots = cursor.fetchone()[0]
        metrics["snapshots"] = snapshots
        if snapshots < 500:
            reasons.append("insufficient_snapshots")
        
        # Future labels
        try:
            cursor.execute("SELECT COUNT(*) FROM future_labels")
            labels = cursor.fetchone()[0]
            metrics["future_labels"] = labels
            if labels < 300:
                reasons.append("future_labels_below_300")
        except Exception:
            metrics["future_labels"] = 0
            reasons.append("future_labels_table_missing")
        
        # Events
        try:
            cursor.execute("SELECT COUNT(*) FROM events")
            events = cursor.fetchone()[0]
            metrics["events"] = events
        except Exception:
            metrics["events"] = 0
        
        # Distinct states
        try:
            cursor.execute("SELECT COUNT(DISTINCT current_state) FROM snapshots WHERE exclude_from_analysis = 0")
            metrics["distinct_current_states"] = cursor.fetchone()[0]
            if metrics["distinct_current_states"] < 2:
                reasons.append("state_distribution_too_uniform")
        except Exception:
            metrics["distinct_current_states"] = 0
        
        # Distinct execution states
        try:
            cursor.execute("SELECT COUNT(DISTINCT execution_timing_state) FROM snapshots WHERE exclude_from_analysis = 0")
            metrics["distinct_execution_states"] = cursor.fetchone()[0]
            if metrics["distinct_execution_states"] < 2:
                reasons.append("execution_state_not_distributed")
        except Exception:
            metrics["distinct_execution_states"] = 0
        
        # Distinct phase hashes
        try:
            cursor.execute("SELECT COUNT(DISTINCT market_phase_hash) FROM snapshots WHERE exclude_from_analysis = 0")
            metrics["distinct_market_phase_hashes"] = cursor.fetchone()[0]
        except Exception:
            metrics["distinct_market_phase_hashes"] = 0
        
        # History duration
        try:
            cursor.execute("SELECT MIN(timestamp_utc), MAX(timestamp_utc) FROM snapshots WHERE exclude_from_analysis = 0")
            row = cursor.fetchone()
            if row and row[0] and row[1]:
                duration_hours = (row[1] - row[0]) / 3600
                metrics["history_hours"] = round(duration_hours, 1)
                if duration_hours < 72:
                    reasons.append("history_less_than_3_days")
            else:
                metrics["history_hours"] = 0
                reasons.append("no_snapshot_data")
        except Exception:
            metrics["history_hours"] = 0
        
        # Dealer hedging pressure distribution
        try:
            cursor.execute("""
                SELECT dealer_hedging_pressure, COUNT(*) 
                FROM snapshots 
                WHERE exclude_from_analysis = 0 
                GROUP BY dealer_hedging_pressure
            """)
            pressure_dist = {row[0]: row[1] for row in cursor.fetchall()}
            metrics["dealer_hedging_distribution"] = pressure_dist
            if len(pressure_dist) < 2:
                reasons.append("dealer_hedging_pressure_not_distributed")
        except Exception:
            pass
        
        # Void score compression check
        try:
            cursor.execute("""
                SELECT MIN(liquidity_void_score), MAX(liquidity_void_score), AVG(liquidity_void_score)
                FROM snapshots WHERE exclude_from_analysis = 0
            """)
            row = cursor.fetchone()
            if row and row[0] is not None:
                void_range = row[1] - row[0]
                metrics["void_score_range"] = round(void_range, 1)
                if void_range < 15:
                    reasons.append("liquidity_void_score_too_compressed")
        except Exception:
            pass
        
        conn.close()
        
    except Exception as e:
        reasons.append("database_access_failed")
        metrics["error"] = str(e)
    
    # Determine status
    if not reasons:
        status = "OK"
        ready = True
    elif "datastore_integrity_failed" in reasons or "database_access_failed" in reasons:
        status = "DATASTORE_INTEGRITY_FAILED"
        ready = False
    elif "insufficient_snapshots" in reasons or "no_snapshot_data" in reasons:
        status = "INSUFFICIENT_DATA"
        ready = False
    elif "future_labels_below_300" in reasons:
        status = "FUTURE_LABELS_MISSING"
        ready = False
    elif "history_less_than_3_days" in reasons:
        status = "LOW_SAMPLE_SIZE"
        ready = False
    else:
        status = "SIGNALS_NOT_READY"
        ready = False
    
    return {
        "ready": ready,
        "status": status,
        "reasons": reasons,
        "metrics": metrics,
    }

@router.get("/timeline")
async def get_timeline(
    from_ts: Optional[float] = Query(None, alias="from"),
    to_ts: Optional[float] = Query(None, alias="to"),
    limit: int = Query(5000, le=10000),
    offset: int = 0,
    resolution: Optional[str] = None,
    include_events: bool = False,
    include_labels: bool = False,
    severity_min: Optional[str] = None,
    event_types: Optional[str] = None
):
    """Fetch synchronized historical snapshots and future labels."""
    conn = get_db()
    cursor = conn.cursor()
    
    # Default to last 24 hours if not provided
    if not to_ts:
        to_ts = time.time()
    if not from_ts:
        from_ts = to_ts - (24 * 3600)
        
    # Enforce maximum 7 days replay window
    if (to_ts - from_ts) > (7 * 24 * 3600):
        conn.close()
        raise HTTPException(status_code=400, detail="Maximum replay window is 7 days")
        
    res_seconds = parse_resolution(resolution)
    
    # Base query for snapshots
    select_fields = "s.*"
    if include_labels:
        select_fields += ", f.future_return_5m, f.future_return_15m, f.future_return_30m, f.future_max_up_30m, f.future_max_down_30m, f.future_realized_vol_30m, f.future_range_30m, f.future_breakout_strength"
    
    # labels_ready flag
    select_fields += ", (f.timestamp_utc IS NOT NULL) AS labels_ready"
    
    join_clause = "LEFT JOIN future_labels f ON s.timestamp_utc = f.timestamp_utc"
    
    if res_seconds:
        # Downsampling: last snapshot in each bucket
        query = f'''
            SELECT * FROM (
                SELECT {select_fields}
                FROM snapshots s
                {join_clause}
                WHERE s.timestamp_utc IN (
                    SELECT MAX(timestamp_utc)
                    FROM snapshots
                    WHERE timestamp_utc BETWEEN ? AND ?
                    GROUP BY CAST(timestamp_utc / ? AS INTEGER)
                )
                ORDER BY s.timestamp_utc DESC
                LIMIT ? OFFSET ?
            ) ORDER BY timestamp_utc ASC
        '''
        params = (from_ts, to_ts, res_seconds, limit, offset)
    else:
        query = f'''
            SELECT * FROM (
                SELECT {select_fields}
                FROM snapshots s
                {join_clause}
                WHERE s.timestamp_utc BETWEEN ? AND ?
                ORDER BY s.timestamp_utc DESC
                LIMIT ? OFFSET ?
            ) ORDER BY timestamp_utc ASC
        '''
        params = (from_ts, to_ts, limit, offset)
        
    cursor.execute(query, params)
    snapshots_data = [dict(row) for row in cursor.fetchall()]
    
    response = {
        "status": "ok",
        "count": len(snapshots_data),
        "data": snapshots_data
    }
    
    if include_events:
        event_query = "SELECT * FROM events WHERE timestamp_utc BETWEEN ? AND ?"
        event_params = [from_ts, to_ts]
        
        if severity_min:
            # We can map severities if needed, or exact match. Assuming exact or simplified for now.
            # Usually severity is LOW, MEDIUM, HIGH, CRITICAL. Let's do exact match or IN clause.
            # If complex filtering is needed, add logic here. For now, exact.
            event_query += " AND severity = ?"
            event_params.append(severity_min.upper())
            
        if event_types:
            types_list = [t.strip() for t in event_types.split(',')]
            placeholders = ','.join(['?'] * len(types_list))
            event_query += f" AND event_type IN ({placeholders})"
            event_params.extend(types_list)
            
        event_query += " ORDER BY timestamp_utc ASC"
        cursor.execute(event_query, event_params)
        response["events"] = [dict(row) for row in cursor.fetchall()]
        
    conn.close()
    return response

@router.get("/events")
async def get_events(
    from_ts: Optional[float] = Query(None, alias="from"),
    to_ts: Optional[float] = Query(None, alias="to"),
    severity_min: Optional[str] = None,
    event_types: Optional[str] = None
):
    """Fetch institutional intelligence events for the replay overlay."""
    conn = get_db()
    cursor = conn.cursor()
    
    if not to_ts:
        to_ts = time.time()
    if not from_ts:
        from_ts = to_ts - (24 * 3600)
        
    query = "SELECT * FROM events WHERE timestamp_utc BETWEEN ? AND ?"
    params = [from_ts, to_ts]
    
    if severity_min:
        query += " AND severity = ?"
        params.append(severity_min.upper())
        
    if event_types:
        types_list = [t.strip() for t in event_types.split(',')]
        placeholders = ','.join(['?'] * len(types_list))
        query += f" AND event_type IN ({placeholders})"
        params.extend(types_list)
        
    query += " ORDER BY timestamp_utc ASC"
    
    cursor.execute(query, params)
    result = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return {"status": "ok", "count": len(result), "data": result}


@router.post("/event-outcomes/backfill")
async def backfill_event_outcomes(
    limit: int = Query(1000, ge=1, le=10000),
):
    """Backfill replay-only event outcome rows from persisted events and future price data."""
    from engine.replay_outcome_engine import ReplayOutcomeEngine

    conn = get_db()
    try:
        result = ReplayOutcomeEngine.backfill_event_outcomes(conn, limit=limit)
    finally:
        conn.close()
    return result


@router.get("/event-outcomes")
async def get_event_outcomes(
    event_type: Optional[str] = None,
    outcome_label: Optional[str] = None,
    limit: int = Query(500, ge=1, le=5000),
):
    """Fetch persisted replay-only event outcomes."""
    from engine.replay_outcome_engine import ReplayOutcomeEngine

    conn = get_db()
    cursor = conn.cursor()
    try:
        ReplayOutcomeEngine.ensure_schema(conn)
        where = []
        params = []
        if event_type:
            where.append("event_type = ?")
            params.append(event_type)
        if outcome_label:
            where.append("outcome_label = ?")
            params.append(outcome_label)
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        cursor.execute(
            f"""
            SELECT *
            FROM event_outcomes
            {where_sql}
            ORDER BY id DESC
            LIMIT ?
            """,
            params + [limit],
        )
        rows = [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

    return {
        "status": "ok",
        "count": len(rows),
        "data": rows,
        "note": "Event outcomes are replay-only diagnostics and do not affect live MOS logic.",
    }


@router.get("/event-outcomes/summary")
async def get_event_outcomes_summary(
    event_type: Optional[str] = None,
):
    """Summarize which MOS events were followed by expansion, continuation, reversal, or no outcome."""
    from engine.replay_outcome_engine import ReplayOutcomeEngine

    conn = get_db()
    try:
        summary = ReplayOutcomeEngine.outcome_summary(conn, event_type=event_type)
    finally:
        conn.close()
    return summary


@router.get("/level-reactions/summary")
async def get_level_reactions_summary(
    event_type: Optional[str] = None,
    from_utc: Optional[str] = Query(None, alias="from"),
    to_utc: Optional[str] = Query(None, alias="to"),
    limit: int = Query(1000, ge=1, le=5000),
):
    """Summarize replay-only event reactions around option levels, ranges, and round price zones."""
    from engine.replay_outcome_engine import ReplayOutcomeEngine

    conn = get_db()
    try:
        summary = ReplayOutcomeEngine.level_reaction_summary(
            conn,
            event_type=event_type,
            limit=limit,
            from_utc=from_utc,
            to_utc=to_utc,
        )
    finally:
        conn.close()
    return summary


@router.post("/level-reactions/backfill")
async def backfill_level_reactions(
    limit: int = Query(1000, ge=1, le=10000),
):
    """Repair/fill persisted replay-only level reactions for existing event outcomes."""
    from engine.replay_outcome_engine import ReplayOutcomeEngine

    conn = get_db()
    try:
        result = ReplayOutcomeEngine.backfill_level_reactions(conn, limit=limit)
    finally:
        conn.close()
    return result


@router.get("/level-reactions")
async def get_level_reactions(
    event_type: Optional[str] = None,
    reaction_label: Optional[str] = None,
    from_utc: Optional[str] = Query(None, alias="from"),
    to_utc: Optional[str] = Query(None, alias="to"),
    limit: int = Query(500, ge=1, le=5000),
):
    """Fetch persisted replay-only level reactions."""
    from engine.replay_outcome_engine import ReplayOutcomeEngine

    conn = get_db()
    cursor = conn.cursor()
    try:
        ReplayOutcomeEngine.ensure_schema(conn)
        where = []
        params = []
        if event_type:
            where.append("event_type = ?")
            params.append(event_type)
        if reaction_label:
            where.append("reaction_label = ?")
            params.append(reaction_label)
        if from_utc:
            where.append("event_timestamp_utc >= ?")
            params.append(ReplayOutcomeEngine._normalize_time_filter(from_utc))
        if to_utc:
            where.append("event_timestamp_utc <= ?")
            params.append(ReplayOutcomeEngine._normalize_time_filter(to_utc))
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        cursor.execute(
            f"""
            SELECT *
            FROM event_level_reactions
            {where_sql}
            ORDER BY id DESC
            LIMIT ?
            """,
            params + [limit],
        )
        rows = [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()
    return {
        "status": "ok",
        "count": len(rows),
        "data": rows,
        "note": "Level reactions are replay-only diagnostics and do not affect live MOS logic.",
    }


@router.get("/event-outcomes/pine-export", response_class=PlainTextResponse)
async def export_event_outcomes_for_pine(
    limit: int = Query(500, ge=1, le=5000),
):
    """Export event outcomes as CSV for TradingView/Pine external inspection workflows."""
    from engine.replay_outcome_engine import ReplayOutcomeEngine

    conn = get_db()
    try:
        csv_text = ReplayOutcomeEngine.pine_export(conn, limit=limit)
    finally:
        conn.close()
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=mos_event_outcomes.csv"},
    )


@router.get("/event-outcomes/pine-script", response_class=PlainTextResponse)
async def export_event_outcomes_pine_script(
    limit: int = Query(100, ge=1, le=500),
):
    """Export event outcomes as a TradingView Pine Script label overlay."""
    from engine.replay_outcome_engine import ReplayOutcomeEngine

    conn = get_db()
    try:
        script = ReplayOutcomeEngine.pine_script(conn, limit=limit)
    finally:
        conn.close()
    return PlainTextResponse(
        script,
        media_type="text/plain",
        headers={"Content-Disposition": "attachment; filename=mos_event_outcomes.pine"},
    )

@router.get("/export")
async def export_csv(
    from_ts: Optional[float] = Query(None, alias="from"),
    to_ts: Optional[float] = Query(None, alias="to"),
    resolution: Optional[str] = None
):
    """API endpoint to download the mos_research.db snapshots as a CSV file."""
    try:
        import pandas as pd
        conn = get_db()
        
        # Default to all if not provided, though it's better to provide limits
        where_clause = ""
        params = []
        if from_ts and to_ts:
            where_clause = "WHERE s.timestamp_utc BETWEEN ? AND ?"
            params = [from_ts, to_ts]
            
        res_seconds = parse_resolution(resolution)
        
        if res_seconds and where_clause:
            query = f'''
                SELECT s.*, 
                       f.future_return_5m, f.future_return_15m, f.future_return_30m,
                       f.future_max_up_30m, f.future_max_down_30m, f.future_realized_vol_30m,
                       f.future_range_30m, f.future_breakout_strength
                FROM snapshots s
                LEFT JOIN future_labels f ON s.timestamp_utc = f.timestamp_utc
                WHERE s.timestamp_utc IN (
                    SELECT MAX(timestamp_utc)
                    FROM snapshots
                    {where_clause}
                    GROUP BY CAST(timestamp_utc / ? AS INTEGER)
                )
                ORDER BY s.timestamp_utc ASC
            '''
            params.append(res_seconds)
        else:
            query = f'''
                SELECT s.*, 
                       f.future_return_5m, f.future_return_15m, f.future_return_30m,
                       f.future_max_up_30m, f.future_max_down_30m, f.future_realized_vol_30m,
                       f.future_range_30m, f.future_breakout_strength
                FROM snapshots s
                LEFT JOIN future_labels f ON s.timestamp_utc = f.timestamp_utc
                {where_clause}
                ORDER BY s.timestamp_utc ASC
            '''
            
        df = pd.read_sql_query(query, conn, params=params)
        conn.close()
        
        export_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'mos_research_export.csv'))
        os.makedirs(os.path.dirname(export_path), exist_ok=True)
        df.to_csv(export_path, index=False)
        
        return FileResponse(export_path, media_type='text/csv', filename="mos_research_export.csv")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/regime-distribution")
async def get_regime_distribution(
    from_ts: Optional[float] = Query(None, alias="from"),
    to_ts: Optional[float] = Query(None, alias="to")
):
    """Distribution of market regimes over time period.
    
    Helps evaluate whether:
    - market was truly pinned
    - or regime classifier is over-biased toward PINNING
    """
    conn = get_db()
    cursor = conn.cursor()

    if not to_ts:
        to_ts = time.time()
    if not from_ts:
        from_ts = to_ts - (24 * 3600)

    cursor.execute("""
        SELECT current_state, COUNT(*) as count
        FROM snapshots
        WHERE timestamp_utc BETWEEN ? AND ?
        GROUP BY current_state
        ORDER BY count DESC
    """, (from_ts, to_ts))

    distribution = {}
    total = 0
    for row in cursor.fetchall():
        state_name = row["current_state"]
        count = row["count"]
        distribution[state_name] = count
        total += count

    # Add percentage breakdown
    pct_distribution = {}
    for state_name, count in distribution.items():
        pct_distribution[state_name] = round(count / total * 100, 1) if total > 0 else 0

    conn.close()
    return {
        "status": "ok",
        "from": from_ts,
        "to": to_ts,
        "total_snapshots": total,
        "distribution": distribution,
        "distribution_pct": pct_distribution
    }


@router.delete("/clear")
async def clear_research_data():
    """Полностью очищает все таблицы в mos_research.db."""
    conn = get_db()
    cursor = conn.cursor()
    try:
        tables = [
            "snapshots", "events", "future_labels", "event_outcomes",
            "event_level_reactions", "bad_snapshots", "bookmarks", "event_cooldowns",
        ]
        deleted = {}
        for table in tables:
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                count = cursor.fetchone()[0]
                cursor.execute(f"DELETE FROM {table}")
                deleted[table] = count
            except Exception:
                deleted[table] = 0
        conn.commit()
        # VACUUM to reclaim disk space
        conn.execute("VACUUM")
        conn.close()
        total = sum(deleted.values())
        return {"status": "ok", "message": f"Удалено {total} записей", "details": deleted}
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/deribit-smoke-test")
async def deribit_smoke_test():
    """Standalone Deribit discovery and WebSocket ticker smoke test."""
    from api.deribit_adapter import DeribitAdapter
    import asyncio
    import time
    
    adapter = None
    owns_adapter = False
    if _dm is not None and hasattr(_dm, "adapters"):
        adapter = _dm.adapters.get("deribit")
    if adapter is None:
        adapter = DeribitAdapter()
        owns_adapter = True
    
    try:
        t0 = time.time()
        instruments = await adapter.fetch_instruments()
        if owns_adapter:
            await adapter.start()
        cache_ready = await adapter.wait_for_option_tickers(timeout=120.0)
        if cache_ready:
            await asyncio.sleep(3.0)
        options = await adapter.fetch_option_tickers()
        t1 = time.time()
        
        sample_names = [i.get("instrument_name") for i in instruments[:3]]
        sample_keys = [o.get("instrument_name") for o in options[:3]]
        valid_greeks = sum(
            1 for o in options
            if isinstance(o.get("greeks"), dict)
            and o["greeks"].get("delta") is not None
        )
        valid_iv = sum(1 for o in options if o.get("mark_iv") is not None and o.get("mark_iv", 0) > 0)
        valid_gamma = sum(
            1 for o in options
            if isinstance(o.get("greeks"), dict)
            and o["greeks"].get("gamma") is not None
        )
        calls_count = sum(1 for o in options if o.get("instrument_name", "").endswith("-C"))
        puts_count = sum(1 for o in options if o.get("instrument_name", "").endswith("-P"))
        expiries = set(o.get("instrument_name", "").split("-")[1] for o in options if "-" in o.get("instrument_name", ""))
        strikes = set(o.get("instrument_name", "").split("-")[2] for o in options if len(o.get("instrument_name", "").split("-")) > 2)
        
        return {
            "status": "ok" if options and valid_greeks else "degraded",
            "request_attempted": True,
            "method": "REST discovery + WebSocket core cache + adaptive RPC bootstrap",
            "endpoint_used": "get_instruments & incremental_ticker.<instrument> & public/ticker",
            "collector_reused": not owns_adapter,
            "raw_instruments_count": len(instruments),
            "raw_book_summary_count": 0,
            "raw_ws_ticker_count": len(options),
            "ws_cache_ready": cache_ready,
            "sample_instrument_names": sample_names,
            "sample_ticker_keys": sample_keys,
            "parsed_options_count": len(options),
            "valid_greeks_count": valid_greeks,
            "valid_iv_count": valid_iv,
            "valid_gamma_count": valid_gamma,
            "calls_count": calls_count,
            "puts_count": puts_count,
            "expiries_count": len(expiries),
            "strikes_count": len(strikes),
            "elapsed_ms": round((t1 - t0) * 1000, 2),
            "error_type": None,
            "error_message": None,
            "diagnostics": adapter.get_diagnostics()
        }
    except Exception as e:
        return {
            "status": "error",
            "request_attempted": True,
            "method": "REST discovery + WebSocket core cache + adaptive RPC bootstrap",
            "endpoint_used": "get_instruments & incremental_ticker.<instrument> & public/ticker",
            "collector_reused": not owns_adapter,
            "raw_instruments_count": 0,
            "raw_book_summary_count": 0,
            "raw_ws_ticker_count": 0,
            "ws_cache_ready": False,
            "sample_instrument_names": [],
            "sample_ticker_keys": [],
            "parsed_options_count": 0,
            "valid_greeks_count": 0,
            "valid_iv_count": 0,
            "valid_gamma_count": 0,
            "calls_count": 0,
            "puts_count": 0,
            "expiries_count": 0,
            "strikes_count": 0,
            "elapsed_ms": 0,
            "error_type": type(e).__name__,
            "error_message": str(e),
            "diagnostics": adapter.get_diagnostics()
        }
    finally:
        if owns_adapter:
            await adapter.stop()

