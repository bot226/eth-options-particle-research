"""
main_context_lookup.py — Live-safe helper for querying event_level_reactions
from mos_research.db to determine latest main context direction.

Rules:
- Only reads live-safe columns: event_timestamp_utc, reaction_label, level_side, level_price.
- Never touches future_labels, future_return, event_outcomes, post-event MFE/MAE or any
  outcome/replay fields.
- Uses ISO string comparison (not UNIX int comparison) because event_timestamp_utc is TEXT.
- Always returns a complete dict with all diagnostics fields populated.
"""

import sqlite3
import os
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

log = logging.getLogger(__name__)

# ── Direction mapping ─────────────────────────────────────────────────────────
#
# LONG:
#   SUPPORT_DEFENSE + SUPPORT
#   FALSE_BREAK     + SUPPORT
#   LEVEL_BREAK     + RESISTANCE
#
# SHORT:
#   RESISTANCE_REJECTION + RESISTANCE
#   FALSE_BREAK          + RESISTANCE
#   LEVEL_BREAK          + SUPPORT
#
# NEUTRAL / ignore:
#   NO_REACTION, UNKNOWN, WEAK_DIRECTIONAL_MOVE, MID_RANGE*,
#   level_side == MID_RANGE, null/empty labels or sides.
# ─────────────────────────────────────────────────────────────────────────────

_DIRECTIONAL_LABELS = frozenset(
    {"SUPPORT_DEFENSE", "FALSE_BREAK", "LEVEL_BREAK", "RESISTANCE_REJECTION"}
)
_DIRECTIONAL_SIDES = frozenset({"SUPPORT", "RESISTANCE"})

_IGNORE_LABELS = frozenset({
    "NO_REACTION", "UNKNOWN", "WEAK_DIRECTIONAL_MOVE",
    "MID_RANGE", "MID_RANGE_EXPANSION", "MID_RANGE_DIRECTIONAL_MOVE",
})


def _get_direction(reaction_label: Optional[str], level_side: Optional[str]) -> str:
    """Map (reaction_label, level_side) to LONG / SHORT / NEUTRAL."""
    if not reaction_label or not level_side:
        return "NEUTRAL"
    rl = reaction_label.strip().upper()
    ls = level_side.strip().upper()

    if rl in _IGNORE_LABELS or ls not in _DIRECTIONAL_SIDES:
        return "NEUTRAL"

    if (rl == "SUPPORT_DEFENSE" and ls == "SUPPORT") or \
       (rl == "FALSE_BREAK"     and ls == "SUPPORT") or \
       (rl == "LEVEL_BREAK"     and ls == "RESISTANCE"):
        return "LONG"

    if (rl == "RESISTANCE_REJECTION" and ls == "RESISTANCE") or \
       (rl == "FALSE_BREAK"          and ls == "RESISTANCE") or \
       (rl == "LEVEL_BREAK"          and ls == "SUPPORT"):
        return "SHORT"

    return "NEUTRAL"


def _ts_to_utc_iso(unix_sec: float) -> str:
    """Convert UNIX timestamp (float seconds) to UTC ISO string with 'Z' suffix."""
    return datetime.utcfromtimestamp(unix_sec).strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def _parse_ts_safe(ts_raw: Any) -> Optional[float]:
    """Parse an ISO timestamp string (possibly with Z or +00:00) to a float UNIX second."""
    if ts_raw is None:
        return None
    try:
        # Normalise to +00:00 and parse
        ts_str = str(ts_raw).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            # Treat as UTC
            return dt.replace(tzinfo=timezone.utc).timestamp()
        return dt.timestamp()
    except Exception:
        return None


def lookup_latest_main_context(
    research_db_path: str,
    current_ts: float,
    lookback_sec: int = 600,
    manual_bias: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Query event_level_reactions in mos_research.db and return full diagnostics dict.

    Parameters
    ----------
    research_db_path : str
        Absolute path to mos_research.db. Never use a relative path.
    current_ts : float
        Current UNIX timestamp (seconds, UTC). Used to build the time window.
    lookback_sec : int
        How many seconds back to look. Default 600 s (10 minutes).
    manual_bias : str or None
        "LONG" / "SHORT" / None. Used only for conflict detection.

    Returns
    -------
    dict with keys:
        checked_main_context_count      INTEGER
        latest_main_context_direction   TEXT   ("LONG" | "SHORT" | "NONE")
        latest_main_context_reaction_label TEXT | None
        latest_main_context_level_side  TEXT | None
        latest_main_context_level_price REAL | None
        latest_main_context_ts          TEXT | None   (ISO with Z)
        latest_main_context_age_sec     REAL | None
        latest_main_context_source      TEXT | None
        latest_main_context_confidence  TEXT | None
        latest_main_context_deribit_status TEXT | None
        main_context_conflict_active    INTEGER (0 | 1)
        main_context_conflict_reason    TEXT | None
        _debug                          dict   (raw numbers for log)
    """
    # ── Defaults (returned when nothing is found or on error) ─────────────────
    result: Dict[str, Any] = {
        "checked_main_context_count":     0,
        "latest_main_context_direction":  "NONE",
        "latest_main_context_reaction_label": None,
        "latest_main_context_level_side": None,
        "latest_main_context_level_price": None,
        "latest_main_context_ts":         None,
        "latest_main_context_age_sec":    None,
        "latest_main_context_source":     None,
        "latest_main_context_confidence": None,
        "latest_main_context_deribit_status": None,
        "main_context_conflict_active":   0,
        "main_context_conflict_reason":   None,
        
        "main_context_none_reason":       None,
        "main_context_lookup_from_ts":    None,
        "main_context_lookup_to_ts":      None,
        "main_context_candidate_count":   0,
        "main_context_directional_candidate_count": 0,
        "latest_rejected_context_ts":     None,
        "latest_rejected_context_label":  None,
        "latest_rejected_context_level_side": None,
        "latest_rejected_context_direction": None,
        "latest_rejected_context_reject_reason": None,
        
        "_debug": {},
    }

    # ── Build ISO window for SQL comparison ───────────────────────────────────
    current_iso  = _ts_to_utc_iso(current_ts)
    cutoff_sec   = current_ts - lookback_sec
    cutoff_iso   = _ts_to_utc_iso(cutoff_sec)

    cwd         = os.getcwd()
    db_exists   = os.path.exists(research_db_path)

    debug: Dict[str, Any] = {
        "cwd":              cwd,
        "research_db_path": research_db_path,
        "research_db_exists": db_exists,
        "current_manual_ts":  current_ts,
        "current_iso":  current_iso,
        "cutoff_iso":   cutoff_iso,
        "lookback_sec": lookback_sec,
        "manual_bias":  manual_bias,
        "event_level_reactions_total_count": 0,
        "latest_event_level_reaction_ts":    None,
        "raw_rows_returned":      0,
        "directional_rows_count": 0,
        "latest_raw_row":         None,
        "latest_directional_row": None,
        "final_direction":        "NONE",
        "error":                  None,
    }
    result["_debug"] = debug

    if not db_exists:
        debug["error"] = "research_db_not_found"
        result["main_context_none_reason"] = "db_lookup_failed"
        log.warning(
            f"[MainContextLookup] DB not found: {research_db_path}\n"
            f"  cwd: {cwd}\n"
            f"  current_iso: {current_iso}\n"
            f"  cutoff_iso: {cutoff_iso}"
        )
        return result

    conn = None
    try:
        conn = sqlite3.connect(research_db_path, timeout=5.0)
        conn.execute("PRAGMA query_only=ON;")
        cursor = conn.cursor()

        # ── Sanity: total count + latest timestamp ────────────────────────────
        cursor.execute(
            "SELECT COUNT(*), MAX(event_timestamp_utc) FROM event_level_reactions"
        )
        row = cursor.fetchone()
        total_count = row[0] if row else 0
        latest_ts_raw = row[1] if row else None
        debug["event_level_reactions_total_count"] = total_count
        debug["latest_event_level_reaction_ts"]    = latest_ts_raw
        
        if total_count == 0:
            result["main_context_none_reason"] = "no_event_level_reactions"

        # ── Main query: ISO string comparison ────────────────────────────────
        # Only live-safe columns; ORDER BY DESC so latest comes first.
        cursor.execute(
            """
            SELECT event_timestamp_utc, reaction_label, level_side, level_price,
                   source, confidence, deribit_status
            FROM event_level_reactions
            WHERE event_timestamp_utc >= ?
              AND event_timestamp_utc <= ?
            ORDER BY event_timestamp_utc DESC
            LIMIT 20
            """,
            (cutoff_iso, current_iso),
        )
        raw_rows = cursor.fetchall()
        debug["raw_rows_returned"] = len(raw_rows)
        result["main_context_candidate_count"] = len(raw_rows)
        
        if raw_rows:
            debug["latest_raw_row"] = raw_rows[0]
        elif total_count > 0:
            result["main_context_none_reason"] = "stale_context"
            
        # timestamp_mismatch check: if no raw rows in window but latest_ts_raw is > current_iso
        if len(raw_rows) == 0 and latest_ts_raw and latest_ts_raw > current_iso:
            result["main_context_none_reason"] = "timestamp_mismatch"

    except Exception as exc:
        debug["error"] = str(exc)
        result["main_context_none_reason"] = "db_lookup_failed"
        log.error(
            f"[MainContextLookup] DB query failed: {exc}\n"
            f"  research_db_path: {research_db_path}\n"
            f"  current_iso: {current_iso}\n"
            f"  cutoff_iso: {cutoff_iso}"
        )
        return result
    finally:
        if conn:
            conn.close()

    # ── Filter for directional rows ───────────────────────────────────────────
    directional_rows = []
    first_rejected_row = None
    first_rejected_reason = None
    
    for ev_ts_raw, r_label, l_side, l_price, src, conf, deribit_st in raw_rows:
        direction = _get_direction(r_label, l_side)
        if direction in ("LONG", "SHORT"):
            directional_rows.append((ev_ts_raw, r_label, l_side, l_price, direction, src, conf, deribit_st))
        else:
            if first_rejected_row is None:
                first_rejected_row = (ev_ts_raw, r_label, l_side, l_price, direction)
                if not r_label or not l_side:
                    first_rejected_reason = "mapping_failed"
                elif r_label in _IGNORE_LABELS:
                    first_rejected_reason = "confidence_filter"
                else:
                    first_rejected_reason = "mapping_failed"

    debug["directional_rows_count"] = len(directional_rows)
    result["main_context_directional_candidate_count"] = len(directional_rows)
    
    if directional_rows:
        debug["latest_directional_row"] = directional_rows[0]
    elif len(raw_rows) > 0:
        result["main_context_none_reason"] = "no_directional_reactions"
        
    if first_rejected_row is not None:
        result["latest_rejected_context_ts"] = first_rejected_row[0]
        result["latest_rejected_context_label"] = first_rejected_row[1]
        result["latest_rejected_context_level_side"] = first_rejected_row[2]
        result["latest_rejected_context_direction"] = first_rejected_row[4]
        result["latest_rejected_context_reject_reason"] = first_rejected_reason

    # ── Emit debug log when 0 directional rows but DB has rows ───────────────
    if len(directional_rows) == 0 and total_count > 0:
        log.warning(
            f"[MainContextLookup] checked_main_context_count=0 but DB has rows.\n"
            f"  cwd: {cwd}\n"
            f"  research_db_path: {research_db_path}\n"
            f"  research_db_exists: {db_exists}\n"
            f"  event_level_reactions_total_count: {total_count}\n"
            f"  latest_event_level_reaction_ts: {latest_ts_raw}\n"
            f"  current_manual_ts: {current_ts}\n"
            f"  current_iso: {current_iso}\n"
            f"  cutoff_iso: {cutoff_iso}\n"
            f"  raw_rows_returned: {len(raw_rows)}\n"
            f"  directional_rows_count: 0\n"
            f"  final_direction: NONE\n"
            f"  none_reason: {result.get('main_context_none_reason')}"
        )

    # ── No directional context found ──────────────────────────────────────────
    if not directional_rows:
        debug["final_direction"] = "NONE"
        result["checked_main_context_count"] = 0
        
        if raw_rows:
            ev_ts_raw, r_label, l_side, l_price, src, conf, deribit_st = raw_rows[0]
            ev_ts_unix = _parse_ts_safe(ev_ts_raw)
            age_sec = (current_ts - ev_ts_unix) if ev_ts_unix is not None else None
            
            result["latest_main_context_reaction_label"] = r_label
            result["latest_main_context_level_side"] = l_side
            result["latest_main_context_level_price"]= l_price
            result["latest_main_context_ts"]         = ev_ts_raw
            result["latest_main_context_age_sec"]    = age_sec
            result["latest_main_context_source"]     = src
            result["latest_main_context_confidence"] = conf
            result["latest_main_context_deribit_status"] = deribit_st

        return result

    # ── Populate result from the latest directional row ───────────────────────
    ev_ts_raw, r_label, l_side, l_price, direction, src, conf, deribit_st = directional_rows[0]
    ev_ts_unix = _parse_ts_safe(ev_ts_raw)
    age_sec = (current_ts - ev_ts_unix) if ev_ts_unix is not None else None

    result["checked_main_context_count"]     = len(directional_rows)
    result["latest_main_context_direction"]  = direction
    result["latest_main_context_reaction_label"] = r_label
    result["latest_main_context_level_side"] = l_side
    result["latest_main_context_level_price"]= l_price
    result["latest_main_context_ts"]         = ev_ts_raw
    result["latest_main_context_age_sec"]    = age_sec
    result["latest_main_context_source"]     = src
    result["latest_main_context_confidence"] = conf
    result["latest_main_context_deribit_status"] = deribit_st

    debug["final_direction"] = direction

    # ── Conflict Detection ────────────────────────────────────────────────────
    result["main_context_none_reason"] = None
    result["main_context_lookup_from_ts"] = cutoff_iso
    result["main_context_lookup_to_ts"] = current_iso
    result["main_context_candidate_count"] = len(raw_rows)
    result["main_context_directional_candidate_count"] = len(directional_rows)
    result["latest_rejected_context_ts"] = None
    result["latest_rejected_context_label"] = None
    result["latest_rejected_context_level_side"] = None
    result["latest_rejected_context_direction"] = None
    result["latest_rejected_context_reject_reason"] = None
    
    debug["final_direction"] = direction

    # ── Conflict detection (diagnostics-only — no status change here) ─────────
    if manual_bias in ("LONG", "SHORT"):
        if manual_bias == "LONG" and direction == "SHORT":
            result["main_context_conflict_active"] = 1
            result["main_context_conflict_reason"] = "MANUAL_LONG_VS_MAIN_SHORT"
        elif manual_bias == "SHORT" and direction == "LONG":
            result["main_context_conflict_active"] = 1
            result["main_context_conflict_reason"] = "MANUAL_SHORT_VS_MAIN_LONG"

    log.info(
        f"[MainContextLookup] result:\n"
        f"  research_db_path: {research_db_path}\n"
        f"  current_iso: {current_iso}\n"
        f"  cutoff_iso: {cutoff_iso}\n"
        f"  total_elr_rows: {total_count}\n"
        f"  raw_rows_returned: {len(raw_rows)}\n"
        f"  directional_rows_count: {len(directional_rows)}\n"
        f"  latest_direction: {direction}\n"
        f"  reaction_label: {r_label}\n"
        f"  level_side: {l_side}\n"
        f"  age_sec: {age_sec}\n"
        f"  manual_bias: {manual_bias}\n"
        f"  conflict_active: {result['main_context_conflict_active']}\n"
        f"  conflict_reason: {result['main_context_conflict_reason']}"
    )

    return result
