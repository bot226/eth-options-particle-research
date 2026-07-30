"""REST API маршруты — данные для фронтенда."""

from collections import deque
import sqlite3
import os
import time
import logging
from typing import Optional, Dict, Any

log = logging.getLogger(__name__)

from fastapi import APIRouter
from engine.calculator import Calculator
from engine.manual_setup_classifier import ManualSetupClassifier
from engine.signals import SignalEngine
from engine.manual_logger import ManualLogger
from engine.price_source_engine import PriceSourceEngine

router = APIRouter(prefix="/api")

# Ссылка на DataManager — будет установлена при старте приложения
_dm = None
_manual_logger = None

def get_manual_logger():
    global _manual_logger
    if _manual_logger is None:
        _manual_logger = ManualLogger()
    return _manual_logger
_MANUAL_WATCHLIST = deque(maxlen=100)
_MANUAL_WATCHLIST_COOLDOWN_SEC = 120


def set_data_manager(dm):
    global _dm
    _dm = dm


# ── OHLCV collector reference (set from main.py) ─────────────────────────────
_ohlcv_collector = None

def set_ohlcv_collector(collector):
    """Called from main.py to share the OhlcvCollector instance for sync checks."""
    global _ohlcv_collector
    _ohlcv_collector = collector


# ── Read-only access to mos_research.db ──────────────────────────────────────
_RESEARCH_DB_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', 'data', 'mos_research.db')
)

# Cache for OHLCV range (refreshed every 30s)
_ohlcv_cache: Dict[str, Any] = {}
_ohlcv_cache_ts: float = 0.0
_OHLCV_CACHE_SEC = 30

# Cache for replay reference from event_level_reactions (refreshed every 10s)
_replay_cache: Dict[str, Any] = {}
_replay_cache_ts: float = 0.0
_REPLAY_CACHE_SEC = 10

# TTL config
_LIVE_CONTEXT_TTL_SEC = 300   # live context is always fresh (built from market_state)
_REPLAY_CONTEXT_TTL_SEC = 300  # event_level_reactions replay reference TTL
MAIN_CONTEXT_TTL_SEC = 3600    # main context TTL for diagnostics lookup


def _fetch_ohlcv_range(lookback_candles: int = 60) -> Dict[str, Any]:
    """Read-only: fetch recent OHLCV high/low/close for live level detection.

    Uses last `lookback_candles` 1-minute candles (∼1h window).
    Returns empty dict if DB unavailable or no data.
    Cached for _OHLCV_CACHE_SEC seconds.
    """
    global _ohlcv_cache, _ohlcv_cache_ts
    now = time.time()
    if now - _ohlcv_cache_ts < _OHLCV_CACHE_SEC:
        return _ohlcv_cache
    try:
        conn = sqlite3.connect(
            f"file:{_RESEARCH_DB_PATH}?mode=ro",
            uri=True, timeout=2,
            check_same_thread=False,
        )
        cur = conn.cursor()
        cur.execute("""
            SELECT MAX(high) AS range_high, MIN(low) AS range_low,
                   MAX(timestamp_utc) AS latest_ts
            FROM (
                SELECT high, low, timestamp_utc
                FROM ohlcv_candles
                ORDER BY timestamp_utc DESC
                LIMIT ?
            )
        """, (lookback_candles,))
        row = cur.fetchone()
        conn.close()
        if row and row[0] is not None:
            result = {
                "range_high": row[0],
                "range_low":  row[1],
                "latest_ts":  row[2],
            }
        else:
            result = {}
        _ohlcv_cache = result
        _ohlcv_cache_ts = now
        return result
    except Exception as e:
        log.debug("_fetch_ohlcv_range skipped: %s", e)
        return _ohlcv_cache

def _fetch_recent_klines(limit: int = 6, snapshot_ts: Optional[float] = None) -> list:
    """Read-only: fetch recent OHLCV 1m candles for impulse guard logic.
    Returns list of dicts: [{ts, o, h, l, c, v}, ...] sorted oldest to newest.
    """
    try:
        conn = sqlite3.connect(
            f"file:{_RESEARCH_DB_PATH}?mode=ro",
            uri=True, timeout=2,
            check_same_thread=False,
        )
        cur = conn.cursor()
        
        # Determine fallback mode
        fallback = False
        query_ts = time.time()
        if snapshot_ts is not None and snapshot_ts > 0:
            query_ts = snapshot_ts
        else:
            fallback = True
            
        cur.execute("""
            SELECT timestamp_utc, open, high, low, close, volume
            FROM ohlcv_candles
            WHERE timeframe = '1m' AND timestamp_utc <= ?
            ORDER BY timestamp_utc DESC
            LIMIT ?
        """, (query_ts, limit))
        rows = cur.fetchall()
        
        # If we asked for snapshot_ts but didn't get enough data, fallback to latest
        if not fallback and len(rows) < limit:
            cur.execute("""
                SELECT timestamp_utc, open, high, low, close, volume
                FROM ohlcv_candles
                WHERE timeframe = '1m'
                ORDER BY timestamp_utc DESC
                LIMIT ?
            """, (limit,))
            rows_fallback = cur.fetchall()
            if len(rows_fallback) > len(rows):
                rows = rows_fallback
                fallback = True
                
        conn.close()
        
        # We need them from oldest to newest (like dm.klines)
        rows.reverse()
        klines = []
        for r in rows:
            klines.append({
                "ts": int(r[0]),
                "o": float(r[1]),
                "h": float(r[2]),
                "l": float(r[3]),
                "c": float(r[4]),
                "v": float(r[5]),
                "_fallback": fallback
            })
        return klines
    except Exception as e:
        log.debug("_fetch_recent_klines skipped/failed: %s", e)
        return []


def _fetch_replay_level_context() -> Dict[str, Any]:
    """Read-only: fetch latest event_level_reactions record as REPLAY REFERENCE ONLY.

    This data is NEVER used for live decisions — it is an outcome/replay table.
    Used only for raw_* debug fields and audit logging.
    """
    global _replay_cache, _replay_cache_ts
    now = time.time()
    if now - _replay_cache_ts < _REPLAY_CACHE_SEC:
        return _replay_cache
    try:
        conn = sqlite3.connect(
            f"file:{_RESEARCH_DB_PATH}?mode=ro",
            uri=True, timeout=2,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT id, event_type, level_result, nearest_level, level_side,
                   event_timestamp_utc
            FROM event_level_reactions
            WHERE level_result IS NOT NULL AND level_result != 'NO_REACTION'
              AND nearest_level IS NOT NULL AND nearest_level > 0
            ORDER BY id DESC LIMIT 1
        """)
        row = cur.fetchone()
        conn.close()
        result = {}
        if row:
            result = {
                "reaction_id":  row["id"],
                "event_type":   row["event_type"],
                "level_result": row["level_result"],
                "nearest_level":row["nearest_level"],
                "level_side":   row["level_side"],
                "reaction_ts":  row["event_timestamp_utc"],
            }
        _replay_cache = result
        _replay_cache_ts = now
        return result
    except Exception as e:
        log.debug("_fetch_replay_level_context skipped: %s", e)
        return _replay_cache


def _classify_live_level_result(level_side: str, dist_pct: float) -> str:
    """Classify live level interaction from price proximity only.

    No future fields. Uses proximity to nearest structural level.
    """
    if dist_pct <= 0.15:  # AT the level (within 0.15%)
        if level_side == "SUPPORT":
            return "AT_SUPPORT"
        elif level_side == "RESISTANCE":
            return "AT_RESISTANCE"
    elif dist_pct <= 0.5:  # NEAR the level (0.15-0.5%)
        if level_side == "SUPPORT":
            return "NEAR_SUPPORT"
        elif level_side == "RESISTANCE":
            return "NEAR_RESISTANCE"
    # More than 0.5% from any structural level = mid-range
    return "MID_RANGE_EXPANSION"


def _build_live_level_context(market_state: Dict[str, Any]) -> Dict[str, Any]:
    """Build live-safe level context from real-time data only.

    Sources (no future fields, no outcome fields):
    1. Gamma call_wall / put_wall  - live options GEX levels (from market_state)
    2. OHLCV range_high / range_low - recent 1h price range (from ohlcv_candles)
    3. Latest live event type       - from market_state.events

    Returns live_level_context dict suitable for ManualSetupClassifier.
    live_context_used = True always (it's built from live data).
    """
    from datetime import datetime as _dt

    spot = market_state.get("spot", 0)
    # execution_price from injected price_source_info (may be same as spot)
    _ps_info = market_state.get("_price_source_info")
    _basis = _ps_info.basis if (_ps_info is not None and _ps_info.basis is not None) else 0.0
    _exec_price = _ps_info.execution_price if (_ps_info is not None and _ps_info.execution_price is not None) else spot

    if not spot or spot <= 0:
        return {
            "live_level_result":   None,
            "live_nearest_level":  None,
            "live_level_side":     None,
            "live_level_type":     None,
            "live_distance_pct":   None,
            "live_context_ts":     _dt.utcnow().isoformat() + "Z",
            "live_context_age_sec": 0,
            "live_context_source": "none",
            "live_context_used":   False,
            "live_source_event_id": None,
            "live_source_snapshot_id": None,
            "live_source_snapshot_sequence_id": None,
        }

    # --- Gamma walls from live market_state ---
    gamma_metrics = market_state.get("gamma", {}).get("metrics", {})
    call_wall = gamma_metrics.get("call_wall", 0) or 0
    put_wall  = gamma_metrics.get("put_wall", 0)  or 0

    # --- OHLCV 1h range (last 60 candles) ---
    ohlcv = _fetch_ohlcv_range(60)
    range_high = ohlcv.get("range_high", 0) or 0
    range_low  = ohlcv.get("range_low", 0)  or 0

    # --- Live context does not come from an event ---
    source_event_id = None
    
    live_source_snapshot_id = market_state.get("snapshot_id")
    live_source_snapshot_sequence_id = market_state.get("snapshot_sequence_id")
    db_synthetic_flow_pressure = None
    if not live_source_snapshot_id:
        try:
            from engine.research_logger import get_db_connection
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT snapshot_id, snapshot_sequence_id, synthetic_flow_pressure FROM snapshots ORDER BY snapshot_sequence_id DESC LIMIT 1")
            r = c.fetchone()
            if r:
                live_source_snapshot_id = r[0]
                live_source_snapshot_sequence_id = r[1]
                db_synthetic_flow_pressure = r[2]
            conn.close()
        except Exception:
            pass

    # --- Build candidates: (level_side, level_type, price, distance_pct) ---
    support_candidates = []
    resistance_candidates = []

    # OHLCV range levels (higher precision than gamma walls for near-term)
    if range_high > spot:
        d = (range_high - spot) / spot * 100
        resistance_candidates.append(("RESISTANCE", "OHLCV_RANGE_HIGH_1H", range_high, d))
    if range_low > 0 and range_low < spot:
        d = (spot - range_low) / spot * 100
        support_candidates.append(("SUPPORT", "OHLCV_RANGE_LOW_1H", range_low, d))

    # Gamma walls (GEX key levels) — use as fallback or additional context
    if call_wall > spot:
        d = (call_wall - spot) / spot * 100
        resistance_candidates.append(("RESISTANCE", "GAMMA_CALL_WALL", call_wall, d))
    if put_wall > 0 and put_wall < spot:
        d = (spot - put_wall) / spot * 100
        support_candidates.append(("SUPPORT", "GAMMA_PUT_WALL", put_wall, d))

    context_ts = _dt.utcnow().isoformat() + "Z"
    base_context = {
        "live_context_ts": context_ts,
        "live_context_age_sec": 0,
        "live_context_used": True,
        "live_source_event_id": source_event_id,
        "live_source_snapshot_id": live_source_snapshot_id,
        "live_source_snapshot_sequence_id": live_source_snapshot_sequence_id,
        "live_synthetic_flow_pressure": db_synthetic_flow_pressure,
        # Execution-adjusted gamma walls (reference walls + basis)
        "reference_call_wall":              call_wall if call_wall > 0 else None,
        "reference_put_wall":               put_wall  if put_wall  > 0 else None,
        "execution_adjusted_call_wall":     round(call_wall + _basis, 1) if call_wall > 0 else None,
        "execution_adjusted_put_wall":      round(put_wall  + _basis, 1) if put_wall  > 0 else None,
    }

    if not support_candidates and not resistance_candidates:
        base_context.update({
            "live_level_result":   "MID_RANGE_EXPANSION",
            "live_nearest_level":  None,
            "live_level_side":     "MID_RANGE",
            "live_level_type":     "no_structural_level",
            "live_distance_pct":   None,
            "live_context_source": "gamma_walls+ohlcv",
            
            "live_support_level": None,
            "live_support_distance_pct": None,
            "live_support_source": None,
            "live_support_type": None,
            "live_support_result": None,
            
            "live_resistance_level": None,
            "live_resistance_distance_pct": None,
            "live_resistance_source": None,
            "live_resistance_type": None,
            "live_resistance_result": None,
            
            "primary_live_level": None,
            "primary_live_side": None,
            "primary_live_distance_pct": None,
            "primary_live_source": None,
            "primary_live_result": None,
        })
        return base_context

    support_candidates.sort(key=lambda x: x[3])
    resistance_candidates.sort(key=lambda x: x[3])

    live_support = support_candidates[0] if support_candidates else None
    live_resistance = resistance_candidates[0] if resistance_candidates else None

    # Determine context source label
    sources_used = set()
    if range_high > 0 or range_low > 0:
        sources_used.add("ohlcv_1h")
    if call_wall > 0 or put_wall > 0:
        sources_used.add("gamma_walls")
    context_source = "+".join(sorted(sources_used)) or "gamma_walls"

    if live_support:
        base_context.update({
            "live_support_level": round(live_support[2], 1),
            "live_support_distance_pct": round(live_support[3], 3),
            "live_support_source": context_source,
            "live_support_type": live_support[1],
            "live_support_result": _classify_live_level_result(live_support[0], live_support[3]),
        })
    else:
        base_context.update({
            "live_support_level": None,
            "live_support_distance_pct": None,
            "live_support_source": None,
            "live_support_type": None,
            "live_support_result": None,
        })

    if live_resistance:
        base_context.update({
            "live_resistance_level": round(live_resistance[2], 1),
            "live_resistance_distance_pct": round(live_resistance[3], 3),
            "live_resistance_source": context_source,
            "live_resistance_type": live_resistance[1],
            "live_resistance_result": _classify_live_level_result(live_resistance[0], live_resistance[3]),
        })
    else:
        base_context.update({
            "live_resistance_level": None,
            "live_resistance_distance_pct": None,
            "live_resistance_source": None,
            "live_resistance_type": None,
            "live_resistance_result": None,
        })

    # Determine primary nearest level
    if live_support and live_resistance:
        primary_candidate = live_support if live_support[3] < live_resistance[3] else live_resistance
    elif live_support:
        primary_candidate = live_support
    else:
        primary_candidate = live_resistance

    primary_side, primary_type, primary_level, primary_dist_pct = primary_candidate
    primary_result = _classify_live_level_result(primary_side, primary_dist_pct)

    base_context.update({
        "primary_live_level": round(primary_level, 1),
        "primary_live_side": primary_side,
        "primary_live_distance_pct": round(primary_dist_pct, 3),
        "primary_live_source": context_source,
        "primary_live_result": primary_result,
        
        # Compatibility fields (to be overridden by classifier if it chooses a specific side)
        "live_nearest_level": round(primary_level, 1),
        "live_level_side": primary_side,
        "live_level_type": primary_type,
        "live_distance_pct": round(primary_dist_pct, 3),
        "live_level_result": primary_result,
        "live_context_source": context_source,
    })

    return base_context


def _enrich_market_state(market_state: Dict[str, Any]) -> Dict[str, Any]:
    """Inject live level context + replay reference into a shallow copy of market_state.

    Read-only: does NOT mutate the original StateEngine market_state.

    Hierarchy:
    1. live_level_context  — built from real-time gamma walls + OHLCV (always fresh)
       → Used by ManualSetupClassifier for FORMING/ACTIONABLE/ENTRY_CANDIDATE
    2. replay_level_context — from event_level_reactions (outcome/replay table)
       → Stored as raw_* fields for audit/debug only. NEVER used for live decisions.
    """
    from datetime import datetime as _dt
    now = time.time()
    enriched = dict(market_state)

    # ── 1. LIVE level context (real-time, always fresh) ────────────────────
    live_ctx = _build_live_level_context(market_state)
    enriched["_live_level_ctx"] = live_ctx
    # Expose live fields at top level for classifier
    enriched["_live_level_result"]  = live_ctx.get("live_level_result")
    enriched["_live_nearest_level"] = live_ctx.get("live_nearest_level")
    enriched["_live_level_side"]    = live_ctx.get("live_level_side")
    enriched["_live_context_used"]  = live_ctx.get("live_context_used", True)
    enriched["_live_context_age_sec"] = 0  # always 0 — computed from current snapshot

    # ── 2. REPLAY reference (event_level_reactions — outcome table) ───────
    replay_ctx = _fetch_replay_level_context()
    replay_is_stale = True
    replay_age_sec = None

    level_meta = {
        "reaction_id":          None,
        "reaction_ts":          None,
        "age_sec":              None,
        "ttl_sec":              _REPLAY_CONTEXT_TTL_SEC,
        "is_stale":             True,
        "source":               "event_level_reactions",
        "used_for_live_decision": False,   # NEVER True
        "raw_level_result":     None,
        "raw_nearest_level":    None,
        "raw_level_side":       None,
    }

    if replay_ctx:
        ts_str = replay_ctx.get("reaction_ts") or ""
        try:
            ts_clean = ts_str.replace("Z", "+00:00") if ts_str else ""
            if ts_clean:
                from datetime import datetime as _dt2
                replay_dt = _dt2.fromisoformat(ts_clean)
                replay_age_sec = round(now - replay_dt.timestamp(), 1)
        except Exception:
            pass
        replay_is_stale = (replay_age_sec is None) or (replay_age_sec > _REPLAY_CONTEXT_TTL_SEC)

        level_meta["reaction_id"]       = replay_ctx.get("reaction_id")
        level_meta["reaction_ts"]        = ts_str
        level_meta["age_sec"]            = replay_age_sec
        level_meta["is_stale"]           = replay_is_stale
        level_meta["raw_level_result"]   = replay_ctx.get("level_result")
        level_meta["raw_nearest_level"]  = replay_ctx.get("nearest_level")
        level_meta["raw_level_side"]     = replay_ctx.get("level_side")
        # used_for_live_decision always False for replay context

    enriched["_level_context_meta"]   = level_meta
    # Legacy staleness flags (kept for backward compat with classifier)
    enriched["_level_context_stale"]  = True   # replay is always stale for live use
    enriched["_level_context_age_sec"]= replay_age_sec

    # ── 3. Expose synthetic_flow_pressure for flow fallback in classifier ──
    # Classifier uses SFP when short_term_flow_direction is NEUTRAL/missing.
    def _nested_get(d, *keys, default=None):
        for k in keys:
            if not isinstance(d, dict):
                return default
            d = d.get(k)
        return d if d is not None else default

    sfp = (
        market_state.get("synthetic_flow_pressure")
        or _nested_get(market_state, "advanced_intelligence", "phase_2",
                       "synthetic_orderflow", "metrics", "flow_momentum_score")
        or _nested_get(market_state, "advanced_intelligence", "phase_2",
                       "synthetic_orderflow", "metrics", "synthetic_flow_pressure")
        or _nested_get(market_state, "advanced_intelligence", "phase_1",
                       "short_term_flow_context", "metrics", "synthetic_flow_pressure")
        or _nested_get(market_state, "flow", "synthetic_flow_pressure")
        or enriched.get("_live_level_ctx", {}).get("live_synthetic_flow_pressure")
    )
    if sfp is not None:
        enriched["synthetic_flow_pressure"] = sfp

    return enriched


def sanitize_manual_payload(value):
    import math
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    elif isinstance(value, str):
        # some optional strings when missing are sent as empty strings, converting to None if requested
        if value.strip() == "":
            return None
        return value
    elif isinstance(value, dict):
        return {k: sanitize_manual_payload(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [sanitize_manual_payload(v) for v in value]
    return value


def _parse_ts_safe(ts_val):
    if not ts_val:
        return 0.0
    try:
        return float(ts_val)
    except ValueError:
        from datetime import datetime
        try:
            return datetime.fromisoformat(str(ts_val).replace('Z', '+00:00')).timestamp()
        except ValueError:
            return 0.0


def _build_manual_trading_payload(market_state):
    try:
        # ── Price Source Info (execution_price vs reference_price) ──
        # Must be computed BEFORE enrichment so _build_live_level_context can see it.
        price_source_info = PriceSourceEngine.get(_dm)
        # Inject into market_state shallow copy for downstream consumers
        market_state = dict(market_state)
        market_state["_price_source_info"] = price_source_info

        snapshot_ts = market_state.get("timestamp")
        current_ts = _parse_ts_safe(snapshot_ts)
        if current_ts <= 0:
            current_ts = time.time()
        elif current_ts > 1e11:
            current_ts /= 1000.0
        try:
            from engine.main_context_lookup import lookup_latest_main_context
            _mc = lookup_latest_main_context(
                research_db_path=_RESEARCH_DB_PATH,
                current_ts=current_ts,
                lookback_sec=MAIN_CONTEXT_TTL_SEC,
                manual_bias=None,
            )
            market_state["latest_main_context_direction"]      = _mc.get("latest_main_context_direction", "NONE")
            market_state["latest_main_context_reaction_label"] = _mc.get("latest_main_context_reaction_label")
            market_state["latest_main_context_level_side"]     = _mc.get("latest_main_context_level_side")
            market_state["latest_main_context_level_price"]    = _mc.get("latest_main_context_level_price")
            market_state["latest_main_context_ts"]             = _mc.get("latest_main_context_ts")
            market_state["latest_main_context_age_sec"]        = _mc.get("latest_main_context_age_sec")
            market_state["latest_main_context_source"]         = _mc.get("latest_main_context_source")
            market_state["latest_main_context_confidence"]     = _mc.get("latest_main_context_confidence")
            market_state["latest_main_context_deribit_status"] = _mc.get("latest_main_context_deribit_status")
            market_state["checked_main_context_count"]         = _mc.get("checked_main_context_count", 0)
        except Exception as e:
            log.error(f"Error early MAIN_CONTEXT_LOOKUP: {e}")



        enriched = _enrich_market_state(market_state)
        manual_setup = ManualSetupClassifier.classify(enriched)
        sf = ManualSetupClassifier.source_fields(enriched)

        # Inject main context fields into manual_setup for candidate key and later conflict logic
        for k in ["latest_main_context_direction", "latest_main_context_reaction_label", 
                  "latest_main_context_level_side", "latest_main_context_level_price", 
                  "latest_main_context_ts", "latest_main_context_age_sec", 
                  "latest_main_context_source", "latest_main_context_confidence", 
                  "latest_main_context_deribit_status", "checked_main_context_count"]:
            if k in market_state:
                manual_setup[k] = market_state[k]

    
        snapshot_ts = market_state.get("timestamp")
        current_ts = _parse_ts_safe(snapshot_ts)
        if current_ts <= 0:
            current_ts = time.time()
        elif current_ts > 1e11:
            current_ts /= 1000.0

        # ── FALLING KNIFE GUARD / IMPULSE METRICS ──
        klines = getattr(_dm, "klines", [])
        if not klines or len(klines) < 20:
            klines = _fetch_recent_klines(limit=20, snapshot_ts=current_ts)
            if klines and klines[0].get("_fallback"):
                price_context_source = "ohlcv_candles_latest_fallback"
            else:
                price_context_source = "ohlcv_candles"
        else:
            price_context_source = "dm.klines"
        
        price_context_candle_count = len(klines)
        price_context_latest_ts = klines[-1]["ts"] if klines else None

        recent_return_3m = None
        recent_return_5m = None
        latest_close = None
        latest_low = None
        latest_high = None
        previous_3m_low = None
        previous_3m_high = None
        close_vs_selected_level = None
        fresh_lower_low_after_touch = False
        fresh_higher_high_after_touch = False
        price_confirmation_status = ""
        falling_knife_guard = 0
        impulse_guard_reason = ""
        decision_blocker = ""
    
        try:
            selected_setup_level = float(manual_setup.get("selected_setup_level"))
        except (TypeError, ValueError):
            selected_setup_level = None
    
        if price_context_candle_count < 5:
            price_confirmation_status = "PRICE_CONTEXT_MISSING"
            decision_blocker = "PRICE_CONTEXT_MISSING"
            if manual_setup.get("manual_status") == "ENTRY_CANDIDATE":
                manual_setup["manual_status"] = "WATCH"
                manual_setup["actionability"] = "FORMING"
                manual_setup["setup_quality"] = "FORMING"
                manual_setup["manual_reason"] = "Данные цены недоступны (PRICE_CONTEXT_MISSING). Вход заблокирован."
                manual_setup["confirmation_needed"] = "Ждать загрузки ценовых данных (OHLCV)."
        else:
            latest_candle = klines[-1]
            latest_close = latest_candle["c"]
            latest_low = latest_candle["l"]
            latest_high = latest_candle["h"]
        
            candle_3m_ago = klines[-4] # 3m return is from the open 3 candles ago
            candle_5m_ago = klines[-6] if len(klines) >= 6 else klines[0]
        
            if candle_3m_ago["o"] > 0:
                recent_return_3m = (latest_close - candle_3m_ago["o"]) / candle_3m_ago["o"] * 100.0
            if candle_5m_ago["o"] > 0:
                recent_return_5m = (latest_close - candle_5m_ago["o"]) / candle_5m_ago["o"] * 100.0
            
            previous_3m_low = min(k["l"] for k in klines[-4:-1])
            previous_3m_high = max(k["h"] for k in klines[-4:-1])
        
            if selected_setup_level is not None:
                close_vs_selected_level = latest_close - selected_setup_level
            
                if manual_setup.get("manual_setup_type") == "SUPPORT_DEFENSE_REVERSAL_SETUP" and manual_setup.get("manual_bias") == "LONG":
                    if previous_3m_low is not None and latest_low < previous_3m_low and latest_close < selected_setup_level:
                        fresh_lower_low_after_touch = True
                    
                    if (recent_return_3m is not None and recent_return_3m <= -0.25) or (recent_return_5m is not None and recent_return_5m <= -0.35) or latest_close < selected_setup_level or fresh_lower_low_after_touch:
                        falling_knife_guard = 1
                        decision_blocker = "FALLING_KNIFE_GUARD"
                        price_confirmation_status = "FALLING_KNIFE_BLOCKED"
                        impulse_guard_reason = "Поддержка рядом, flow бычий, но цена ещё в нисходящем импульсе. Вход заблокирован до подтверждения удержания уровня."
                    else:
                        price_confirmation_status = "CONFIRMED_HOLD" if latest_close >= selected_setup_level else "PENDING_HOLD"
                    
                elif manual_setup.get("manual_setup_type") == "RESISTANCE_REJECTION_REVERSAL_SETUP" and manual_setup.get("manual_bias") == "SHORT" and manual_setup.get("selected_setup_side") == "RESISTANCE":
                    if previous_3m_high is not None and latest_high > previous_3m_high and latest_close > selected_setup_level:
                        fresh_higher_high_after_touch = True
                    
                    if (recent_return_3m is not None and recent_return_3m >= 0.25) or (recent_return_5m is not None and recent_return_5m >= 0.35) or latest_close > selected_setup_level or fresh_higher_high_after_touch:
                        falling_knife_guard = 1
                        decision_blocker = "STRONG_UP_IMPULSE_GUARD"
                        price_confirmation_status = "STRONG_UP_IMPULSE_BLOCKED"
                        impulse_guard_reason = "Сопротивление рядом и flow медвежий, но цена всё ещё в восходящем импульсе. Short заблокирован до подтверждения отказа от уровня."
                    else:
                        price_confirmation_status = "CONFIRMED_REJECTION" if latest_close <= selected_setup_level else "PENDING_REJECTION"
                else:
                    price_confirmation_status = "NOT_APPLICABLE"

            if falling_knife_guard == 1 and manual_setup.get("manual_status") == "ENTRY_CANDIDATE":
                manual_setup["manual_status"] = "WATCH"
                manual_setup["actionability"] = "FORMING"
                manual_setup["setup_quality"] = "FORMING"
                manual_setup["decision_blocker"] = decision_blocker
                manual_setup["manual_reason"] = impulse_guard_reason
                if manual_setup.get("manual_bias") == "LONG":
                    manual_setup["confirmation_needed"] = "Ждать удержание поддержки / возврат выше уровня."
                else:
                    manual_setup["confirmation_needed"] = "Ждать отказ от сопротивления / возврат ниже уровня."

        manual_setup["recent_return_3m"] = round(recent_return_3m, 4) if recent_return_3m is not None else None
        manual_setup["recent_return_5m"] = round(recent_return_5m, 4) if recent_return_5m is not None else None
        manual_setup["latest_close"] = latest_close
        manual_setup["latest_low"] = latest_low
        manual_setup["latest_high"] = latest_high
        manual_setup["close_vs_selected_level"] = round(close_vs_selected_level, 2) if close_vs_selected_level is not None else None
        manual_setup["fresh_lower_low_after_touch"] = int(fresh_lower_low_after_touch)
        manual_setup["fresh_higher_high_after_touch"] = int(fresh_higher_high_after_touch)
        manual_setup["price_confirmation_status"] = price_confirmation_status
        manual_setup["falling_knife_guard"] = falling_knife_guard
        manual_setup["impulse_guard_reason"] = impulse_guard_reason
        manual_setup["price_context_source"] = price_context_source
        manual_setup["price_context_candle_count"] = price_context_candle_count
        manual_setup["price_context_latest_ts"] = price_context_latest_ts
        if "decision_blocker" not in manual_setup:
            manual_setup["decision_blocker"] = decision_blocker
        # ── END IMPULSE METRICS ──
    
        candidate_cooldown_sec = 900
        candidate_is_new = 1
        candidate_cooldown_active = 0
        candidate_key = None
        candidate_first_seen_ts = None
        candidate_last_seen_ts = None
        candidate_age_sec = 0

        status = manual_setup.get("manual_status")
        setup_type = manual_setup.get("manual_setup_type", "")
        bias = manual_setup.get("manual_bias", "")
        side = manual_setup.get("selected_setup_side", "")
        level = selected_setup_level
        try:
            inv_level = float(manual_setup.get("invalidation_level"))
        except (TypeError, ValueError):
            inv_level = None
        src = manual_setup.get("selected_setup_level_source", "")
        actionability = manual_setup.get("setup_quality")

        if status == "ENTRY_CANDIDATE":
            actionability = "ACTIONABLE"
            manual_setup["setup_quality"] = "ACTIONABLE"

        is_valid_candidate = (
            status == "ENTRY_CANDIDATE"
            and actionability == "ACTIONABLE"
            and bool(setup_type)
            and bias in ("LONG", "SHORT")
            and bool(side)
            and level is not None
            and inv_level is not None
        )

        if is_valid_candidate:
            symbol = market_state.get("symbol") or "BTCUSD"
            try:
                l_val = round(float(level) / 10.0) * 10
                i_val = round(float(inv_level) / 10.0) * 10
                
                mc_source = manual_setup.get("latest_main_context_source") or ""
                if "ohlcv_only" in mc_source and manual_setup.get("latest_main_context_deribit_status") != "ACTIVE":
                    key_src = "ohlcv_only"
                else:
                    key_src = "gamma_walls+ohlcv_1h" if src and "gamma_walls" in src else (src or "")
                
                candidate_key = f"{symbol}_{setup_type}_{bias}_{side}_{l_val}_{i_val}_{key_src}"
            
                logger = get_manual_logger()
                import sqlite3
                conn = sqlite3.connect(logger.db_path)
                cursor = conn.cursor()
                now_sec = current_ts
                cutoff_sec = now_sec - candidate_cooldown_sec
            
                cursor.execute('''
                    SELECT ts, candidate_first_seen_ts 
                    FROM manual_trading_snapshots 
                    WHERE candidate_key = ?
                    ORDER BY id DESC LIMIT 500
                ''', (candidate_key,))
                rows = cursor.fetchall()
                conn.close()
                
                row = None
                for r in reversed(rows):
                    r_ts = _parse_ts_safe(r[0])
                    if r_ts >= cutoff_sec:
                        row = r
                        break
            
                from datetime import datetime
                if row:
                    first_ts_db, first_seen_iso = row
                    candidate_is_new = 0
                    candidate_cooldown_active = 1
                
                    # Use existing forming_first_seen_ts or fallback to converting its ts
                    if first_seen_iso:
                        candidate_first_seen_ts = first_seen_iso
                    else:
                        try:
                            candidate_first_seen_ts = datetime.utcfromtimestamp(float(first_ts_db)).isoformat() + "Z"
                        except Exception:
                            candidate_first_seen_ts = datetime.utcnow().isoformat() + "Z"
                
                    try:
                        first_dt = datetime.fromisoformat(candidate_first_seen_ts.replace('Z', '+00:00'))
                        candidate_age_sec = int(now_sec - first_dt.timestamp())
                    except Exception:
                        candidate_age_sec = 0
                
                log.info(
                    f"ENTRY_CANDIDATE Generated:\n"
                    f"  candidate_key: {candidate_key}\n"
                    f"  selected_setup_level: {level} (bucket: {l_val})\n"
                    f"  invalidation_level: {inv_level} (bucket: {i_val})\n"
                    f"  found_existing_candidate: {'true' if row else 'false'}\n"
                    f"  candidate_is_new: {candidate_is_new}\n"
                    f"  candidate_cooldown_active: {candidate_cooldown_active}\n"
                    f"  candidate_age_sec: {candidate_age_sec}"
                )
            except Exception as e:
                log.error(f"Error checking candidate dedupe: {e}")

            if candidate_is_new == 1:
                from datetime import datetime
                candidate_first_seen_ts = datetime.utcnow().isoformat() + "Z"
                candidate_age_sec = 0

        manual_setup["candidate_key"] = candidate_key
        manual_setup["candidate_is_new"] = candidate_is_new
        manual_setup["candidate_cooldown_active"] = candidate_cooldown_active
        manual_setup["candidate_first_seen_ts"] = candidate_first_seen_ts
        manual_setup["candidate_cooldown_sec"] = candidate_cooldown_sec
        manual_setup["candidate_age_sec"] = candidate_age_sec

        forming_key = None
        forming_is_new = 1
        forming_cooldown_active = 0
        forming_first_seen_ts = None
        forming_age_sec = 0

        is_forming_setup = (
            (status == "WATCH" and actionability == "FORMING") or
            is_valid_candidate
        )

        if is_forming_setup and bool(setup_type) and bias in ("LONG", "SHORT") and bool(side) and level is not None and inv_level is not None:
            symbol = market_state.get("symbol") or "BTCUSD"
            try:
                l_val = round(float(level) / 10.0) * 10
                i_val = round(float(inv_level) / 10.0) * 10
                forming_key = f"{symbol}_{setup_type}_{bias}_{side}_{l_val}_{i_val}_{src}"
            
                logger = get_manual_logger()
                import sqlite3
                conn = sqlite3.connect(logger.db_path)
                cursor = conn.cursor()
                now_sec = current_ts
                cutoff_sec = now_sec - candidate_cooldown_sec
            
                cursor.execute('''
                    SELECT ts, forming_first_seen_ts 
                    FROM manual_trading_snapshots 
                    WHERE forming_key = ?
                    ORDER BY id DESC LIMIT 500
                ''', (forming_key,))
                rows = cursor.fetchall()
                conn.close()
                
                row = None
                for r in reversed(rows):
                    r_ts = _parse_ts_safe(r[0])
                    if r_ts >= cutoff_sec:
                        row = r
                        break
            
                from datetime import datetime
                if row:
                    first_ts_db, first_seen_iso = row
                    forming_is_new = 0
                    forming_cooldown_active = 1
                
                    # Use existing forming_first_seen_ts or fallback to converting its ts
                    if first_seen_iso:
                        forming_first_seen_ts = first_seen_iso
                    else:
                        try:
                            forming_first_seen_ts = datetime.utcfromtimestamp(float(first_ts_db)).isoformat() + "Z"
                        except Exception:
                            forming_first_seen_ts = datetime.utcnow().isoformat() + "Z"
                
                    try:
                        first_dt = datetime.fromisoformat(forming_first_seen_ts.replace('Z', '+00:00'))
                        forming_age_sec = int(now_sec - first_dt.timestamp())
                    except Exception:
                        forming_age_sec = 0

                log.info(
                    f"FORMING dedupe lookup:\n"
                    f"  forming_key: {forming_key}\n"
                    f"  cutoff_ts: {cutoff_sec}\n"
                    f"  found_existing: {'true' if row else 'false'}\n"
                    f"  forming_is_new: {forming_is_new}\n"
                    f"  forming_cooldown_active: {forming_cooldown_active}\n"
                    f"  forming_age_sec: {forming_age_sec}"
                )
            except Exception as e:
                log.error(f"Error checking forming dedupe: {e}")

            if forming_is_new == 1:
                from datetime import datetime
                forming_first_seen_ts = datetime.utcnow().isoformat() + "Z"
                forming_age_sec = 0

        # ── MAIN CONTEXT CONFLICT GUARD (v51) ──────────────────────────────────
        main_context_conflict_active = 0
        main_context_conflict_side = None
        main_context_conflict_reason = None
        main_context_conflict_reaction_label = None
        main_context_conflict_level_side = None
        main_context_conflict_level_price = None
        main_context_conflict_ts = None
        main_context_conflict_age_sec = None
        main_context_conflict_stale = 0
        main_context_conflict_source = None
        latest_main_context_direction = "NONE"
        latest_main_context_reaction_label = None
        latest_main_context_level_side = None
        latest_main_context_level_price = None
        latest_main_context_ts = None
        latest_main_context_age_sec = None
        latest_main_context_source = None
        latest_main_context_confidence = None
        latest_main_context_deribit_status = None
        checked_main_context_count = 0

        # v51: Always run for EVERY snapshot — diagnostics must be populated
        # regardless of status/bias. Never gated by _guard_should_run.
        try:
            from engine.main_context_lookup import lookup_latest_main_context

            _mc = lookup_latest_main_context(
                research_db_path=_RESEARCH_DB_PATH,
                current_ts=current_ts,
                lookback_sec=MAIN_CONTEXT_TTL_SEC,
                manual_bias=bias if bias in ("LONG", "SHORT") else None,
            )

            checked_main_context_count         = _mc.get("checked_main_context_count", 0)
            latest_main_context_direction      = _mc.get("latest_main_context_direction", "NONE")
            latest_main_context_reaction_label = _mc.get("latest_main_context_reaction_label")
            latest_main_context_level_side     = _mc.get("latest_main_context_level_side")
            latest_main_context_level_price    = _mc.get("latest_main_context_level_price")
            latest_main_context_ts             = _mc.get("latest_main_context_ts")
            latest_main_context_age_sec        = _mc.get("latest_main_context_age_sec")
            latest_main_context_source         = _mc.get("latest_main_context_source")
            latest_main_context_confidence     = _mc.get("latest_main_context_confidence")
            latest_main_context_deribit_status = _mc.get("latest_main_context_deribit_status")

            # Conflict processing — only when bias is directional
            _conflict_from_helper = _mc.get("main_context_conflict_active", 0)

            if bias in ("LONG", "SHORT") and latest_main_context_direction in ("LONG", "SHORT") and latest_main_context_direction != bias:
                main_context_conflict_active       = 1
                main_context_conflict_side         = latest_main_context_direction
                main_context_conflict_reason       = (
                    "MANUAL_LONG_VS_MAIN_SHORT" if bias == "LONG"
                    else "MANUAL_SHORT_VS_MAIN_LONG"
                )
                main_context_conflict_reaction_label = latest_main_context_reaction_label
                main_context_conflict_level_side   = latest_main_context_level_side
                main_context_conflict_level_price  = latest_main_context_level_price
                main_context_conflict_ts           = latest_main_context_ts
                main_context_conflict_age_sec      = latest_main_context_age_sec
                main_context_conflict_stale        = 0
                main_context_conflict_source       = "mos_research_db"

                log.info(
                    f"Main Context Diagnostics: CONFLICT DETECTED\n"
                    f"  latest_main_context_direction: {latest_main_context_direction}\n"
                    f"  manual_bias: {bias}\n"
                    f"  conflict_reason: {main_context_conflict_reason}\n"
                    f"  reaction_label: {latest_main_context_reaction_label} / level_side: {latest_main_context_level_side}\n"
                    f"  age_sec: {latest_main_context_age_sec}\n"
                    f"  checked_main_context_count: {checked_main_context_count}"
                )

            elif checked_main_context_count > 0 and latest_main_context_direction in ("LONG", "SHORT"):
                main_context_conflict_active = 0
                main_context_conflict_reason = None
                log.info(
                    f"Main Context Diagnostics: AGREE_OR_NO_CONFLICT\n"
                    f"  direction: {latest_main_context_direction} / bias: {bias}\n"
                    f"  count: {checked_main_context_count} / age: {latest_main_context_age_sec}"
                )
            else:
                main_context_conflict_active = 0
                main_context_conflict_reason = None
                log.info(
                    f"Main Context Diagnostics: NO_CONTEXT\n"
                    f"  bias: {bias} / count: {checked_main_context_count}"
                )

            # Debug logging as required:
            _dbg = _mc.get("_debug", {})
            if _dbg.get("event_level_reactions_total_count", 0) > 0 and checked_main_context_count == 0:
                log.warning(
                    f"Main Context Diagnostics: OVER-FILTERING DETECTED\n"
                    f"  current_manual_ts: {_dbg.get('current_manual_ts')}\n"
                    f"  current_iso: {_dbg.get('current_iso')}\n"
                    f"  cutoff_iso: {_dbg.get('cutoff_iso')}\n"
                    f"  research_db_path: {_dbg.get('research_db_path')}\n"
                    f"  raw_rows_returned: {_dbg.get('raw_rows_returned')}\n"
                    f"  directional_rows_count: {_dbg.get('directional_rows_count')}\n"
                    f"  latest_raw_row: {_dbg.get('latest_raw_row')}\n"
                    f"  latest_directional_row: {_dbg.get('latest_directional_row')}\n"
                    f"  final_direction: {_dbg.get('final_direction')}"
                )

        except Exception as _mc_exc:
            log.error(f"Error evaluating MAIN_CONTEXT_CONFLICT_GUARD: {_mc_exc}")
            latest_main_context_direction = "NONE"
            checked_main_context_count = 0
            main_context_conflict_active = 0
            main_context_conflict_reason = None
            main_context_conflict_side = None
            main_context_conflict_reaction_label = None
            main_context_conflict_level_side = None
            main_context_conflict_level_price = None
            main_context_conflict_ts = None
            main_context_conflict_age_sec = None
            main_context_conflict_stale = 0
            main_context_conflict_source = None

        # Write ALL guard fields unconditionally. Nothing after this point must
        # overwrite checked_main_context_count, latest_main_context_direction,
        # latest_main_context_reaction_label, latest_main_context_level_side,
        # latest_main_context_level_price, latest_main_context_ts,
        # latest_main_context_age_sec, main_context_conflict_active,
        # or main_context_conflict_reason.
        manual_setup["main_context_conflict_active"]         = main_context_conflict_active
        manual_setup["main_context_conflict_side"]           = main_context_conflict_side
        manual_setup["main_context_conflict_reason"]         = main_context_conflict_reason
        manual_setup["main_context_conflict_reaction_label"] = main_context_conflict_reaction_label
        manual_setup["main_context_conflict_level_side"]     = main_context_conflict_level_side
        manual_setup["main_context_conflict_level_price"]    = main_context_conflict_level_price
        manual_setup["main_context_conflict_ts"]             = main_context_conflict_ts
        manual_setup["main_context_conflict_age_sec"]        = main_context_conflict_age_sec
        manual_setup["main_context_conflict_stale"]          = main_context_conflict_stale
        manual_setup["main_context_conflict_source"]         = main_context_conflict_source
        manual_setup["latest_main_context_direction"]        = latest_main_context_direction
        manual_setup["latest_main_context_reaction_label"]   = latest_main_context_reaction_label
        manual_setup["latest_main_context_level_side"]       = latest_main_context_level_side
        manual_setup["latest_main_context_level_price"]      = latest_main_context_level_price
        manual_setup["latest_main_context_ts"]               = latest_main_context_ts
        manual_setup["latest_main_context_age_sec"]          = latest_main_context_age_sec
        manual_setup["latest_main_context_source"]           = latest_main_context_source
        manual_setup["latest_main_context_confidence"]       = latest_main_context_confidence
        manual_setup["latest_main_context_deribit_status"]   = latest_main_context_deribit_status
        manual_setup["checked_main_context_count"]           = checked_main_context_count
        
        manual_setup["main_context_none_reason"]             = _mc.get("main_context_none_reason") if "_mc" in locals() else None
        manual_setup["main_context_lookup_from_ts"]          = _mc.get("main_context_lookup_from_ts") if "_mc" in locals() else None
        manual_setup["main_context_lookup_to_ts"]            = _mc.get("main_context_lookup_to_ts") if "_mc" in locals() else None
        manual_setup["main_context_candidate_count"]         = _mc.get("main_context_candidate_count", 0) if "_mc" in locals() else 0
        manual_setup["main_context_directional_candidate_count"] = _mc.get("main_context_directional_candidate_count", 0) if "_mc" in locals() else 0
        manual_setup["latest_rejected_context_ts"]           = _mc.get("latest_rejected_context_ts") if "_mc" in locals() else None
        manual_setup["latest_rejected_context_label"]        = _mc.get("latest_rejected_context_label") if "_mc" in locals() else None
        manual_setup["latest_rejected_context_level_side"]   = _mc.get("latest_rejected_context_level_side") if "_mc" in locals() else None
        manual_setup["latest_rejected_context_direction"]    = _mc.get("latest_rejected_context_direction") if "_mc" in locals() else None
        manual_setup["latest_rejected_context_reject_reason"] = _mc.get("latest_rejected_context_reject_reason") if "_mc" in locals() else None
        # ── END MAIN CONTEXT CONFLICT GUARD (v51) ──


        # ── LADDER GUARD (+1R CONFIRMATION) ──
        previous_candidate_key = None
        previous_candidate_ts = None
        previous_candidate_entry_price = None
        previous_candidate_level = None
        previous_candidate_invalidation = None
        previous_candidate_mfe_r = None
        previous_candidate_reached_1r = None
        level_ladder_guard_active = 0
        
        if status == "ENTRY_CANDIDATE" and actionability == "ACTIONABLE" and level is not None and inv_level is not None:
            is_long_support = (setup_type == "SUPPORT_DEFENSE_REVERSAL_SETUP" and bias == "LONG" and side == "SUPPORT")
            is_short_resistance = (setup_type == "RESISTANCE_REJECTION_REVERSAL_SETUP" and bias == "SHORT" and side == "RESISTANCE")
            
            if is_long_support or is_short_resistance:
                try:
                    logger = get_manual_logger()
                    import sqlite3
                    conn = sqlite3.connect(logger.db_path)
                    cursor = conn.cursor()
                    now_sec = current_ts
                    cutoff_sec = now_sec - 900
                    
                    query = '''
                        SELECT candidate_key, ts, price, selected_setup_level, invalidation_level 
                        FROM manual_trading_snapshots 
                        WHERE manual_status = 'ENTRY_CANDIDATE'
                          AND actionability = 'ACTIONABLE'
                          AND manual_setup_type = ?
                          AND manual_bias = ?
                          AND selected_setup_side = ?
                          AND selected_setup_level IS NOT NULL
                          AND invalidation_level IS NOT NULL
                          AND price IS NOT NULL
                        ORDER BY id DESC LIMIT 200
                    '''
                    params = [setup_type, bias, side]
                    cursor.execute(query, tuple(params))
                    all_candidates = cursor.fetchall()
                    conn.close()
                    
                    prev_row = None
                    for r in all_candidates:
                        r_key = r[0]
                        r_ts_val = _parse_ts_safe(r[1])
                        
                        if r_ts_val >= now_sec:
                            continue
                        if r_ts_val < cutoff_sec:
                            continue
                            
                        prev_row = r
                        break
                    
                    if prev_row:
                        prev_key, prev_ts_raw, prev_price, prev_level, prev_inval = prev_row
                        prev_ts = _parse_ts_safe(prev_ts_raw)
                        previous_candidate_key = prev_key
                        previous_candidate_ts = prev_ts
                        previous_candidate_entry_price = prev_price
                        previous_candidate_level = prev_level
                        previous_candidate_invalidation = prev_inval
                        
                        if prev_price is not None and prev_inval is not None:
                            if is_long_support:
                                risk = prev_price - prev_inval
                            else:
                                risk = prev_inval - prev_price
                                
                            if risk > 0:
                                max_high = None
                                min_low = None
                                
                                for k in klines:
                                    k_ts = k.get("ts")
                                    if k_ts is not None and prev_ts <= k_ts < current_ts:
                                        h = k.get("h")
                                        l = k.get("l")
                                        if h is not None and (max_high is None or h > max_high):
                                            max_high = h
                                        if l is not None and (min_low is None or l < min_low):
                                            min_low = l
                                
                                is_hard_block = False
                                
                                if is_long_support and max_high is not None:
                                    previous_candidate_mfe_r = (max_high - prev_price) / risk
                                    previous_candidate_reached_1r = 1 if max_high >= prev_price + risk else 0
                                    
                                    if previous_candidate_reached_1r == 0 and level < prev_level:
                                        if main_context_conflict_active:
                                            is_hard_block = True
                                        elif latest_main_context_direction == "NONE" and price_confirmation_status not in ("CONFIRMED_HOLD", "CONFIRMED_REJECTION"):
                                            is_hard_block = True
                                        elif previous_candidate_mfe_r < 0 and latest_main_context_direction != "LONG":
                                            is_hard_block = True
                                            
                                        if is_hard_block:
                                            status = "WATCH"
                                            actionability = "FORMING"
                                            decision_blocker = "SUPPORT_LADDER_DOWN_GUARD"
                                            manual_setup["manual_status"] = status
                                            manual_setup["actionability"] = actionability
                                            manual_setup["setup_quality"] = "FORMING"
                                            manual_setup["decision_blocker"] = decision_blocker
                                            manual_setup["confirmation_needed"] = "Предыдущий LONG от поддержки ещё не дал +1R. Ждать стабилизацию или подтверждение удержания."
                                        level_ladder_guard_active = 1
                                        
                                elif is_short_resistance and min_low is not None:
                                    previous_candidate_mfe_r = (prev_price - min_low) / risk
                                    previous_candidate_reached_1r = 1 if min_low <= prev_price - risk else 0
                                    
                                    if previous_candidate_reached_1r == 0 and level > prev_level:
                                        if main_context_conflict_active:
                                            is_hard_block = True
                                        elif latest_main_context_direction == "NONE" and price_confirmation_status not in ("CONFIRMED_HOLD", "CONFIRMED_REJECTION"):
                                            is_hard_block = True
                                        elif previous_candidate_mfe_r < 0 and latest_main_context_direction != "SHORT":
                                            is_hard_block = True
                                            
                                        if is_hard_block:
                                            status = "WATCH"
                                            actionability = "FORMING"
                                            decision_blocker = "RESISTANCE_LADDER_UP_GUARD"
                                            manual_setup["manual_status"] = status
                                            manual_setup["actionability"] = actionability
                                            manual_setup["setup_quality"] = "FORMING"
                                            manual_setup["decision_blocker"] = decision_blocker
                                            manual_setup["confirmation_needed"] = "Предыдущий SHORT от сопротивления ещё не дал +1R. Ждать rejection / возврат ниже сопротивления."
                                        level_ladder_guard_active = 1

                    log.info(
                        f"Ladder Guard Debug:\n"
                        f"  current_ts: {current_ts}\n"
                        f"  previous_candidate_query_count: {len(all_candidates)}\n"
                        f"  previous_candidate_key: {previous_candidate_key}\n"
                        f"  previous_candidate_mfe_r: {previous_candidate_mfe_r}\n"
                        f"  previous_candidate_reached_1r: {previous_candidate_reached_1r}\n"
                        f"  ladder_guard_decision: {'ACTIVE' if level_ladder_guard_active else 'INACTIVE'}"
                    )
                except Exception as e:
                    log.error(f"Error evaluating ladder guard: {e}")
                    
        manual_setup["previous_candidate_key"] = previous_candidate_key
        manual_setup["previous_candidate_ts"] = previous_candidate_ts
        manual_setup["previous_candidate_entry_price"] = previous_candidate_entry_price
        manual_setup["previous_candidate_level"] = previous_candidate_level
        manual_setup["previous_candidate_invalidation"] = previous_candidate_invalidation
        manual_setup["previous_candidate_mfe_r"] = previous_candidate_mfe_r
        manual_setup["previous_candidate_reached_1r"] = previous_candidate_reached_1r
        manual_setup["level_ladder_guard_active"] = level_ladder_guard_active
        # ── END LADDER GUARD ──

        if actionability == "FORMING" and not forming_key:
            actionability = "NOT_ACTIONABLE"
            if status != "AVOID":
                status = "WATCH"
            manual_setup["actionability"] = actionability
            manual_setup["manual_status"] = status
            manual_setup["setup_quality"] = "INCOMPLETE"
            manual_setup["confirmation_needed"] = "Ждать полноценный setup с выбранным уровнем, направлением и уровнем отмены."
            
            live_used = enriched.get("_live_context_used", False)
            exec_state = sf.get("execution_timing_state", "WAIT")
            
            if bias == "NEUTRAL":
                decision_blocker = "FLOW_NOT_ALIGNED"
                manual_setup["manual_reason"] = "Flow не подтверждает направление, поэтому setup не считается FORMING."
            elif level is None:
                decision_blocker = "NO_SELECTED_LEVEL"
                manual_setup["manual_reason"] = "Setup context exists, but forming key is missing because selected level is incomplete."
            elif not side:
                decision_blocker = "NO_SELECTED_SIDE"
                manual_setup["manual_reason"] = "Setup context exists, but forming key is missing because selected direction is incomplete."
            elif inv_level is None:
                decision_blocker = "NO_INVALIDATION"
                manual_setup["manual_reason"] = "Setup context exists, but forming key is missing because invalidation level is incomplete."
            elif exec_state == "WAIT":
                decision_blocker = "NO_ACTIVE_EXECUTION"
                manual_setup["manual_reason"] = "Setup context exists, but forming key is missing because execution window is not active."
            elif not live_used:
                decision_blocker = "NO_LIVE_CONTEXT"
                manual_setup["manual_reason"] = "Setup context exists, but forming key is missing because live context was not used."
            else:
                decision_blocker = "FORMING_KEY_MISSING"
                manual_setup["manual_reason"] = "Setup context exists, but forming key is missing."
                
            # Only overwrite decision_blocker if not set by main context conflict guard or ladder guard
            if not manual_setup.get("decision_blocker"):
                manual_setup["decision_blocker"] = decision_blocker

        if actionability != "FORMING":
            forming_is_new = 0
            forming_cooldown_active = 0
            forming_first_seen_ts = None
            forming_age_sec = None

        manual_setup["forming_key"] = forming_key
        manual_setup["forming_is_new"] = forming_is_new
        manual_setup["forming_cooldown_active"] = forming_cooldown_active
        manual_setup["forming_first_seen_ts"] = forming_first_seen_ts
        manual_setup["forming_age_sec"] = forming_age_sec

        if status != "ENTRY_CANDIDATE" or actionability != "ACTIONABLE" or not manual_setup.get("candidate_key"):
            manual_setup["candidate_key"] = None
            manual_setup["candidate_is_new"] = 0
            manual_setup["candidate_cooldown_active"] = 0
            manual_setup["candidate_first_seen_ts"] = None
            manual_setup["candidate_last_seen_ts"] = None
            manual_setup["candidate_age_sec"] = None

        if "missing_conditions" not in manual_setup or manual_setup["missing_conditions"] is None:
            manual_setup["missing_conditions"] = []
            
        if manual_setup.get("setup_quality") == "ACTIONABLE" and manual_setup.get("candidate_key"):
            if not actionability:
                actionability = "ACTIONABLE"
                
        manual_setup["actionability"] = actionability

        log.info(
            f"MOS Manual Snapshot Final State:\n"
            f"  manual_status: {status}\n"
            f"  actionability: {actionability}\n"
            f"  candidate_key: {manual_setup.get('candidate_key')}\n"
            f"  candidate_is_new: {manual_setup.get('candidate_is_new')}\n"
            f"  selected_setup_level: {level}\n"
            f"  invalidation_level: {inv_level}"
        )
        
        from datetime import datetime
        canonical_ts = datetime.utcnow().isoformat() + "Z"

        # ── Price source fields ── expose full ps_dict at manual_setup level ──
        ps_dict = price_source_info.to_dict()

        # ── Execution OHLCV sync check (Step 3) ───────────────────────────────
        # Compare execution_price with latest Bybit linear OHLCV close
        latest_exec_ohlcv_close = None
        if _ohlcv_collector is not None:
            try:
                latest_exec_ohlcv_close = _ohlcv_collector.get_latest_close()
            except Exception as _exc:
                log.debug("OHLCV sync check: get_latest_close failed: %s", _exc)
        ohlcv_sync = PriceSourceEngine.compute_ohlcv_sync_check(
            ps_dict.get("execution_price"),
            latest_exec_ohlcv_close,
        )
        # Merge OHLCV sync check results back into ps_dict
        ps_dict["latest_execution_ohlcv_close"]    = ohlcv_sync["latest_execution_ohlcv_close"]
        ps_dict["execution_price_ohlcv_diff"]      = ohlcv_sync["execution_price_ohlcv_diff"]
        ps_dict["execution_price_ohlcv_diff_pct"]  = ohlcv_sync["execution_price_ohlcv_diff_pct"]
        ps_dict["execution_price_ohlcv_sync_status"] = ohlcv_sync["execution_price_ohlcv_sync_status"]
        # Add OHLCV collector status to ps_dict
        if _ohlcv_collector is not None:
            coll_status = _ohlcv_collector.get_status()
            ps_dict["candle_source_verified"] = coll_status.get("candle_source_verified", 0)
        else:
            ps_dict["candle_source_verified"] = 0

        # OVERRIDE FOR FIX
        if (ps_dict.get("ohlcv_exchange") == "bybit" and
            ps_dict.get("ohlcv_source") == "bybit_linear_btcusdt" and
            ps_dict.get("execution_price_ohlcv_sync_status") == "OK"):
            ps_dict["candle_source_verified"] = 1

        # ── Propagate all price source fields to manual_setup (for snapshot) ──
        manual_setup["reference_price"]                  = ps_dict["reference_price"]
        manual_setup["execution_price"]                  = ps_dict["execution_price"]
        manual_setup["basis"]                            = ps_dict["basis"]
        manual_setup["basis_pct"]                        = ps_dict["basis_pct"]
        manual_setup["execution_venue"]                  = ps_dict["execution_venue"]
        manual_setup["execution_symbol"]                 = ps_dict["execution_symbol"]
        manual_setup["reference_venue"]                  = ps_dict["reference_venue"]
        manual_setup["reference_symbol"]                 = ps_dict["reference_symbol"]
        manual_setup["ohlcv_source"]                     = ps_dict["ohlcv_source"]
        manual_setup["ohlcv_exchange"]                   = ps_dict["ohlcv_exchange"]
        manual_setup["ohlcv_market_type"]                = ps_dict["ohlcv_market_type"]
        manual_setup["ohlcv_symbol"]                     = ps_dict["ohlcv_symbol"]
        manual_setup["ohlcv_timeframe"]                  = ps_dict["ohlcv_timeframe"]
        manual_setup["candle_source_verified"]           = ps_dict["candle_source_verified"]
        manual_setup["latest_execution_ohlcv_close"]     = ps_dict["latest_execution_ohlcv_close"]
        manual_setup["execution_price_ohlcv_diff"]       = ps_dict["execution_price_ohlcv_diff"]
        manual_setup["execution_price_ohlcv_diff_pct"]   = ps_dict["execution_price_ohlcv_diff_pct"]
        manual_setup["execution_price_ohlcv_sync_status"] = ps_dict["execution_price_ohlcv_sync_status"]
        manual_setup["price_source_for_entry"]           = ps_dict["price_source_for_entry"]
        manual_setup["price_source_for_levels"]          = ps_dict["price_source_for_levels"]
        manual_setup["price_source_for_outcome"]         = ps_dict["price_source_for_outcome"]

        # ── Reference sanity diagnostics (Step 4) ────────────────────────────
        manual_setup["reference_price_status"]           = ps_dict.get("reference_price_status")
        manual_setup["basis_valid"]                      = ps_dict.get("basis_valid")
        manual_setup["reference_price_source"]           = ps_dict.get("reference_price_source")
        manual_setup["reference_candidate_count"]        = ps_dict.get("reference_candidate_count")

        # ── Execution SL/TP/MFE fields (Step 5) ──────────────────────────────
        # Computed from execution_price (Bybit linear) and invalidation level.
        # Do NOT use reference_price for these fields.
        entry_exec = ps_dict.get("execution_price")
        inv_lv = manual_setup.get("invalidation_level")
        bias_dir = manual_setup.get("manual_bias")

        protective_stop_exec = None
        tp1_exec = None
        tp2_exec = None
        tp3_exec = None
        execution_risk_abs = None
        execution_risk_pct = None
        protective_stop_source = None
        tp_source = None

        if entry_exec and inv_lv and bias_dir in ("LONG", "SHORT"):
            try:
                entry_f = float(entry_exec)
                inv_f   = float(inv_lv)
                if bias_dir == "LONG":
                    risk = entry_f - inv_f
                    if risk > 0:
                        protective_stop_exec = round(inv_f, 2)
                        tp1_exec = round(entry_f + 1.0 * risk, 2)
                        tp2_exec = round(entry_f + 2.0 * risk, 2)
                        tp3_exec = round(entry_f + 3.0 * risk, 2)
                        execution_risk_abs = round(risk, 2)
                        execution_risk_pct = round((risk / entry_f) * 100, 4)
                        protective_stop_source = "invalidation_level"
                        tp_source = "execution_price_r_multiple"
                elif bias_dir == "SHORT":
                    risk = inv_f - entry_f
                    if risk > 0:
                        protective_stop_exec = round(inv_f, 2)
                        tp1_exec = round(entry_f - 1.0 * risk, 2)
                        tp2_exec = round(entry_f - 2.0 * risk, 2)
                        tp3_exec = round(entry_f - 3.0 * risk, 2)
                        execution_risk_abs = round(risk, 2)
                        execution_risk_pct = round((risk / entry_f) * 100, 4)
                        protective_stop_source = "invalidation_level"
                        tp_source = "execution_price_r_multiple"
            except Exception as _exc:
                log.debug("TP/SL calc failed: %s", _exc)

        # ENTRY_CANDIDATE requires a valid protective stop
        if status == "ENTRY_CANDIDATE" and protective_stop_exec is None:
            status = "WATCH"
            if actionability == "ACTIONABLE":
                actionability = "FORMING"
            manual_setup["decision_blocker"] = "MISSING_PROTECTIVE_STOP"
            manual_setup["manual_status"] = status
            manual_setup["actionability"] = actionability
            manual_setup["candidate_key"] = None
            manual_setup["candidate_is_new"] = 0
            manual_setup["candidate_cooldown_active"] = 0
            manual_setup["candidate_first_seen_ts"] = None
            manual_setup["candidate_last_seen_ts"] = None
            manual_setup["candidate_age_sec"] = None
            log.info("ENTRY_CANDIDATE demoted to WATCH due to MISSING_PROTECTIVE_STOP")

        manual_setup["entry_execution_price"]            = entry_exec
        manual_setup["protective_stop_execution_price"]  = protective_stop_exec
        manual_setup["stop_execution_price"]             = protective_stop_exec
        manual_setup["tp1_execution_price"]              = tp1_exec
        manual_setup["tp2_execution_price"]              = tp2_exec
        manual_setup["tp3_execution_price"]              = tp3_exec
        manual_setup["execution_risk_abs"]               = execution_risk_abs
        manual_setup["execution_risk_pct"]               = execution_risk_pct
        manual_setup["protective_stop_source"]           = protective_stop_source
        manual_setup["tp_source"]                        = tp_source
        # MFE/MAE are execution-based (ohlcv_candles = Bybit linear)
        manual_setup["mfe_r_execution"] = manual_setup.get("previous_candidate_mfe_r")
        manual_setup["mae_r_execution"] = None  # reserved for future MAE tracking

        payload = {
            "status": "ok",
            "manual_setup": manual_setup,
            "source_fields": sf,
            "live_level_context": enriched.get("_live_level_ctx", {}),
            "level_context_meta": enriched.get("_level_context_meta", {}),
            "timestamp": canonical_ts,
            "price_source_info": ps_dict,
            "main_context_info": {
                "checked_main_context_count": manual_setup.get("checked_main_context_count", 0),
                "latest_main_context_direction": manual_setup.get("latest_main_context_direction", "NONE"),
                "latest_main_context_reaction_label": manual_setup.get("latest_main_context_reaction_label"),
                "latest_main_context_level_side": manual_setup.get("latest_main_context_level_side"),
                "latest_main_context_level_price": manual_setup.get("latest_main_context_level_price"),
                "latest_main_context_ts": manual_setup.get("latest_main_context_ts"),
                "latest_main_context_age_sec": manual_setup.get("latest_main_context_age_sec"),
                "latest_main_context_source": manual_setup.get("latest_main_context_source"),
                "latest_main_context_confidence": manual_setup.get("latest_main_context_confidence"),
                "latest_main_context_deribit_status": manual_setup.get("latest_main_context_deribit_status"),
                "main_context_conflict_active": manual_setup.get("main_context_conflict_active", 0),
                "main_context_conflict_reason": manual_setup.get("main_context_conflict_reason")
            }
        }
        return sanitize_manual_payload(payload)

    except Exception as e:
        log.error(f"Error in _build_manual_trading_payload: {e}")
        return {
            "status": "DEGRADED",
            "manual_status": "DEGRADED",
            "actionability": "NOT_ACTIONABLE",
            "error_reason": "CURRENT_CONTEXT_UNAVAILABLE",
            "error_message": str(e),
            "price": None,
            "chart_status": "DEGRADED"
        }


def _build_manual_watchlist_row(market_state, manual_setup=None, source_fields=None):
    manual_setup = manual_setup or ManualSetupClassifier.classify(market_state)
    source_fields = source_fields or ManualSetupClassifier.source_fields(market_state)
    return {
        "time": market_state.get("timestamp"),
        "setup_type": manual_setup.get("manual_setup_type"),
        "manual_status": manual_setup.get("manual_status"),
        "manual_bias": manual_setup.get("manual_bias"),
        "price": source_fields.get("price"),
        "nearest_level": source_fields.get("nearest_level"),
        "current_state": source_fields.get("current_state"),
        "execution_timing_state": source_fields.get("execution_timing_state"),
        "event_type": source_fields.get("event_type"),
        "level_result": source_fields.get("level_result"),
        "short_term_flow_direction": source_fields.get("short_term_flow_direction"),
        "confirmation_needed": _manual_watchlist_text_ru(manual_setup.get("confirmation_needed")),
        "invalidation_level": manual_setup.get("invalidation_level"),
        "setup_quality": manual_setup.get("setup_quality"),
        "candidate_key": manual_setup.get("candidate_key"),
        "candidate_is_new": manual_setup.get("candidate_is_new"),
        "candidate_cooldown_active": manual_setup.get("candidate_cooldown_active"),
        "candidate_first_seen_ts": manual_setup.get("candidate_first_seen_ts"),
        "candidate_age_sec": manual_setup.get("candidate_age_sec"),
        "missing_conditions": [
            _manual_watchlist_text_ru(item)
            for item in (manual_setup.get("missing_conditions") or [])
        ],
    }


def _manual_watchlist_text_ru(text):
    if not text:
        return text
    translations = {
        "Wait for failed downside continuation, reclaim above support, and BUY flow flip.": (
            "Ждать неудачного продолжения вниз, возврата выше поддержки и разворота потока в BUY."
        ),
        "Look for chart confirmation: higher low, reclaim, or close holding above support.": (
            "Искать подтверждение на графике: higher low, возврат или закрытие выше поддержки."
        ),
        "Look for chart confirmation: lower high, rejection wick, or close below resistance.": (
            "Искать подтверждение на графике: lower high, фитиль отказа или закрытие ниже сопротивления."
        ),
        "Look for chart confirmation: retest hold and continuation away from the broken level.": (
            "Искать подтверждение на графике: удержание ретеста и продолжение от пробитого уровня."
        ),
        "Look for chart confirmation: range edge break, acceptance, and directional follow-through.": (
            "Искать подтверждение на графике: пробой края диапазона, принятие цены и направленное продолжение."
        ),
        "Wait for level interaction and chart acceptance in the expansion direction.": (
            "Ждать взаимодействия с уровнем и принятия цены в направлении расширения."
        ),
        "Wait for a fresh MOS expansion or level reaction context.": (
            "Ждать нового контекста расширения MOS или реакции от уровня."
        ),
        "Wait for the structure to stabilize or for a confirmed level reclaim/rejection.": (
            "Ждать стабилизации структуры или подтвержденного возврата/отказа от уровня."
        ),
        "Wait for a cleaner MOS setup and independent chart confirmation.": (
            "Ждать более чистого MOS-сетапа и независимого подтверждения на графике."
        ),
        "Wait for a clean MOS setup and price confirmation on the chart.": (
            "Ждать чистого MOS-сетапа и подтверждения цены на графике."
        ),
        "Look for failed downside continuation, reclaim hold, and bullish follow-through.": (
            "Искать неудачное продолжение вниз, удержание возврата и бычье продолжение."
        ),
        "Look for failed upside continuation, rejection hold, and bearish follow-through.": (
            "Искать неудачное продолжение вверх, удержание отказа и медвежье продолжение."
        ),
        "no confirmed reversal level reaction": "нет подтвержденной разворотной реакции от уровня",
        "flow has not flipped bullish": "поток еще не развернулся в bullish / BUY",
        "no actionable invalidation level": "нет рабочего уровня отмены",
        "no valid nearest_level": "нет валидного ближайшего уровня",
        "no actionable level_result": "нет рабочего результата реакции уровня",
        "confirmation logic is incomplete": "логика подтверждения неполная",
        "flow is not aligned with manual_bias": "поток не совпадает с manual_bias",
        "signal_cluster_score not ready": "signal_cluster_score еще не готов",
        "no event_type": "нет event_type",
        "no level_result": "нет level_result",
        "no nearest_level": "нет nearest_level",
        "no invalidation level": "нет уровня отмены",
        # UNSTABLE_STRUCTURE_AVOID
        "Structure is unstable. Avoid manual entry until structure stabilizes and a clean level reaction appears.": (
            "Структура нестабильна. Избегать ручного входа до стабилизации и появления чистой реакции от уровня."
        ),
        "Wait for the structure to stabilize, then look for a confirmed level reclaim or rejection.": (
            "Ждать стабилизации структуры, затем искать подтвержденный возврат или отказ от уровня."
        ),
        "No actionable invalidation exists while structure is unstable.": (
            "Нет рабочего уровня отмены пока структура нестабильна."
        ),
        "structure is unstable (STRUCTURE_UNSTABLE or REGIME_CHANGE)": (
            "структура нестабильна (STRUCTURE_UNSTABLE или REGIME_CHANGE)"
        ),
        "no clear confirmation/invalidation structure": "нет чёткой структуры подтверждения/отмены",
        # NO_ACTIONABLE_SETUP updated
        "Active environment, but no actionable event, level reaction, nearest level, or invalidation.": (
            "Активная среда, но нет рабочего события, реакции уровня, ближайшего уровня или уровня отмены."
        ),
        # NO_TRADE_CHOP updated
        "No aligned manual setup: chop / pinning / compression with no event, no level reaction, and weak or neutral flow.": (
            "Нет выравненного сетапа: чоп / пиннинг / компрессия — нет события, реакции уровня, поток слабый или нейтральный."
        ),
        "Low signal_cluster_score and low expansion_probability — chop / inactive market.": (
            "Низкий signal_cluster_score и expansion_probability — чоп / неактивный рынок."
        ),
    }
    return translations.get(str(text), str(text))


def _record_manual_watchlist(market_state, manual_setup=None, source_fields=None):
    manual_setup = manual_setup or ManualSetupClassifier.classify(market_state)
    source_fields = source_fields or ManualSetupClassifier.source_fields(market_state)
    status = manual_setup.get("manual_status")
    if status not in ("WATCH", "ENTRY_CANDIDATE", "AVOID"):
        return None

    row = _build_manual_watchlist_row(market_state, manual_setup, source_fields)
    key = (
        row.get("candidate_key") if row.get("candidate_key") else (
            row.get("setup_type"),
            row.get("manual_status"),
            row.get("manual_bias"),
            row.get("current_state"),
            row.get("short_term_flow_direction")
        )
    )
    row_time = _row_time_seconds(row) or time.time()
    for existing in _MANUAL_WATCHLIST:
        existing_key = (
            existing.get("candidate_key") if existing.get("candidate_key") else (
                existing.get("setup_type"),
                existing.get("manual_status"),
                existing.get("manual_bias"),
                existing.get("current_state"),
                existing.get("short_term_flow_direction")
            )
        )
        existing_time = _row_time_seconds(existing) or row_time
        if existing_key == key and abs(row_time - existing_time) <= _MANUAL_WATCHLIST_COOLDOWN_SEC:
            existing.update(row)
            existing["updated_count"] = int(existing.get("updated_count") or 1) + 1
            existing["last_updated_time"] = row.get("time")
            return existing

    row["updated_count"] = 1
    row["last_updated_time"] = row.get("time")
    _MANUAL_WATCHLIST.appendleft(row)
    return row


def _row_time_seconds(row):
    try:
        value = float(row.get("time"))
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return value / 1000.0 if value > 1000000000000 else value


def _filter_manual_watchlist(rows, statuses=None, biases=None, limit=50):
    status_set = {item.strip().upper() for item in (statuses or "").split(",") if item.strip()}
    bias_set = {item.strip().upper() for item in (biases or "").split(",") if item.strip()}
    filtered = []
    for row in rows:
        if status_set and row.get("manual_status") not in status_set:
            continue
        if bias_set and row.get("manual_bias") not in bias_set:
            continue
        filtered.append(row)
        if len(filtered) >= limit:
            break
    return filtered


def _price_zone(level, price=None, width_pct=0.0015):
    if level is None:
        return None
    try:
        center = float(level)
        reference = float(price or level or 0)
    except (TypeError, ValueError):
        return None
    if center <= 0:
        return None
    width = max(reference, center) * width_pct if reference > 0 else center * width_pct
    return {
        "low": round(center - width, 2),
        "high": round(center + width, 2),
        "center": round(center, 2),
    }


def _confirmation_zone(manual_setup, source_fields):
    price = source_fields.get("price")
    nearest_level = source_fields.get("nearest_level")
    bias = manual_setup.get("manual_bias")
    setup_type = manual_setup.get("manual_setup_type")
    if nearest_level is None:
        return None

    base_zone = _price_zone(nearest_level, price, width_pct=0.0025)
    if not base_zone:
        return None

    if bias == "LONG":
        condition = "Look for acceptance above the confirmation zone."
        base_zone["low"] = base_zone["center"]
    elif bias == "SHORT":
        condition = "Look for acceptance below the confirmation zone."
        base_zone["high"] = base_zone["center"]
    else:
        condition = "Wait for directional acceptance away from the zone."

    base_zone.update({
        "label": "confirmation_zone",
        "bias": bias,
        "setup_type": setup_type,
        "condition": condition,
    })
    return base_zone


def _build_manual_chart_overlay(market_state, watchlist_rows=None):
    manual_setup = ManualSetupClassifier.classify(market_state)
    source_fields = ManualSetupClassifier.source_fields(market_state)
    timestamp = market_state.get("timestamp")
    price = source_fields.get("price")
    nearest_level = source_fields.get("nearest_level")
    invalidation_level = manual_setup.get("invalidation_level")

    key_zones = []
    nearest_zone = _price_zone(nearest_level, price, width_pct=0.0015)
    if nearest_zone:
        nearest_zone.update({
            "type": "nearest_level_zone",
            "label": "current_nearest_level",
            "level_side": source_fields.get("level_side"),
        })
        key_zones.append(nearest_zone)

    invalidation_zone = _price_zone(invalidation_level, price, width_pct=0.001)
    if invalidation_zone:
        invalidation_zone.update({
            "type": "invalidation_zone",
            "label": "invalidation",
            "condition": manual_setup.get("invalidation_condition"),
        })
        key_zones.append(invalidation_zone)

    confirmation_zone = _confirmation_zone(manual_setup, source_fields)
    if confirmation_zone:
        key_zones.append({"type": "confirmation_zone", **confirmation_zone})

    event_markers = []
    for event in market_state.get("events") or []:
        if not isinstance(event, dict):
            continue
        event_markers.append({
            "time": event.get("timestamp") or timestamp,
            "price": price,
            "event_type": event.get("event_type") or event.get("type"),
            "severity": event.get("severity"),
            "label": event.get("event_type") or event.get("type") or "EVENT",
            "text": event.get("message") or event.get("reason"),
        })

    level_reaction_markers = []
    if source_fields.get("level_result"):
        level_reaction_markers.append({
            "time": timestamp,
            "price": nearest_level or price,
            "level_result": source_fields.get("level_result"),
            "level_side": source_fields.get("level_side"),
            "label": source_fields.get("level_result"),
        })

    setup_labels = [{
        "time": timestamp,
        "price": price,
        "setup_type": manual_setup.get("manual_setup_type"),
        "manual_status": manual_setup.get("manual_status"),
        "manual_bias": manual_setup.get("manual_bias"),
        "manual_confidence": manual_setup.get("manual_confidence"),
        "label": f"{manual_setup.get('manual_status')} / {manual_setup.get('manual_setup_type')}",
        "text": manual_setup.get("manual_reason"),
    }]

    for row in list(watchlist_rows or [])[:20]:
        if row.get("level_result"):
            level_reaction_markers.append({
                "time": row.get("time"),
                "price": row.get("nearest_level") or row.get("price"),
                "level_result": row.get("level_result"),
                "level_side": None,
                "label": row.get("level_result"),
            })
        setup_labels.append({
            "time": row.get("time"),
            "price": row.get("price"),
            "setup_type": row.get("setup_type"),
            "manual_status": row.get("manual_status"),
            "manual_bias": row.get("manual_bias"),
            "manual_confidence": None,
            "label": f"{row.get('manual_status')} / {row.get('setup_type')}",
            "text": row.get("confirmation_needed"),
        })

    return {
        "key_zones": key_zones,
        "current_nearest_level": {
            "price": nearest_level,
            "level_side": source_fields.get("level_side"),
            "level_result": source_fields.get("level_result"),
        },
        "event_markers": event_markers,
        "level_reaction_markers": level_reaction_markers,
        "setup_labels": setup_labels,
        "invalidation_line": {
            "price": invalidation_level,
            "condition": manual_setup.get("invalidation_condition"),
            "label": "invalidation",
        },
        "confirmation_zone": confirmation_zone,
        "source_fields": source_fields,
        "manual_setup": manual_setup,
        "timestamp": timestamp,
    }


@router.get("/snapshot")
async def get_snapshot():
    """Полный snapshot всех данных для фронтенда."""
    if _dm is None:
        return {"status": "loading", "data": None, "debug": "dm is None"}
    if getattr(_dm, "spot_price", 0) <= 0 and not getattr(_dm, "klines", []):
        return {
            "status": "loading",
            "data": None,
            "debug": f"spot_price and klines empty, "
                     f"tickers_count={len(getattr(_dm, 'tickers', []))}, spot={getattr(_dm, 'spot_price', 0)}"
        }

    spot = _dm.spot_price
    nearest = _dm.get_expiry_nearest()
    chain = dict(_dm.chain)


    # Heatmap — ближайший expiry
    heatmap = []
    heatmap_metadata = {}
    if nearest:
        heatmap = Calculator.get_heatmap_data(_dm.get_chain_for_expiry(nearest), spot, _dm)
        heatmap_metadata = Calculator.get_heatmap_metadata(heatmap)

    # Skew — ближайший expiry
    skew = {"call_25d_iv": 0, "put_25d_iv": 0, "skew": 0}
    if nearest:
        skew = Calculator.get_25d_skew(_dm.get_chain_for_expiry(nearest))

    # IV Term Structure
    current_ts = Calculator.get_iv_term_structure(chain, spot)
    
    def get_ts_from_history(hours):
        import time
        target_ts = time.time() - (hours * 3600)
        best_diff = float('inf')
        best_ts = {"data": [], "metrics": {}}
        for snap in _dm.oi_history:
            diff = abs(snap['ts'] - target_ts)
            if diff < best_diff and snap.get('term_structure'):
                best_diff = diff
                best_ts = snap['term_structure']
        return best_ts

    term_structure = {
        "current": current_ts.get("data", []),
        "metrics": current_ts.get("metrics", {}),
        "h24_ago": get_ts_from_history(24).get("data", [])
    }

    # OI by Expiry
    oi_by_expiry = Calculator.get_oi_by_expiry(chain)

    # Probability
    prob = {"current": {"x": [], "pdf": [], "metrics": {}}, "h1_ago": {"x": [], "pdf": []}, "h24_ago": {"x": [], "pdf": []}}
    if nearest:
        from engine.calculator import _dte_from_expiry_str
        import time
        dte = _dte_from_expiry_str(nearest) or 7
        current_pdf = Calculator.probability_distribution(
            _dm.get_chain_for_expiry(nearest), spot, dte
        )
        
        def get_pdf_from_history(hours):
            target_ts = time.time() - (hours * 3600)
            best_diff = float('inf')
            best_pdf = {"x": [], "pdf": []}
            for snap in _dm.oi_history:
                diff = abs(snap['ts'] - target_ts)
                if diff < best_diff and snap.get('pdf'):
                    best_diff = diff
                    best_pdf = snap['pdf']
            return best_pdf

        prob = {
            "current": current_pdf,
            "h1_ago": get_pdf_from_history(1),
            "h24_ago": get_pdf_from_history(24)
        }

    # GEX (all expiries, DTE-weighted)
    gex = {"current": [], "h24_ago": [], "metrics": {}}
    if chain:
        import time
        current_gex = Calculator.get_gamma_exposure_full(chain, spot)
        
        def get_gex_from_history(hours):
            target_ts = time.time() - (hours * 3600)
            best_diff = float('inf')
            best_gex = {"data": [], "metrics": {}}
            for snap in _dm.oi_history:
                diff = abs(snap['ts'] - target_ts)
                if diff < best_diff and snap.get('gex'):
                    best_diff = diff
                    best_gex = snap['gex']
            return best_gex

        gex = {
            "current": current_gex.get("data", []),
            "metrics": current_gex.get("metrics", {}),
            "h24_ago": get_gex_from_history(24).get("data", [])
        }

    # Major OI Levels
    oi_levels = []
    if nearest:
        oi_levels = Calculator.get_major_oi_levels(
            _dm.get_chain_for_expiry(nearest), top_n=8
        )

    # Signals
    signals = SignalEngine.generate(chain, spot, nearest)

    # Summary
    total_call_oi = 0
    total_put_oi = 0
    for exp_data in chain.values():
        for strike_data in exp_data.values():
            total_call_oi += strike_data.get("C", {}).get("oi", 0)
            total_put_oi += strike_data.get("P", {}).get("oi", 0)
    total_oi = total_call_oi + total_put_oi
    pc_ratio = total_put_oi / total_call_oi if total_call_oi > 0 else 0

    atm_iv = {"call_iv": 0, "put_iv": 0, "avg_iv": 0}
    if nearest:
        atm_iv = Calculator.get_atm_iv(_dm.get_chain_for_expiry(nearest), spot)

    from engine.state_engine import StateEngine
    market_state = StateEngine.build_market_state(_dm)
    manual_setup = ManualSetupClassifier.classify(market_state)
    market_state["manual_setup"] = manual_setup
    _record_manual_watchlist(market_state, manual_setup)

    return {
        "status": "ok",
        "market_state": market_state,
        "data": {
            "spot": spot,
            "spot_24h_change": _dm.spot_24h_change,
            "expiries": _dm.expiries,
            "nearest_expiry": nearest,
            "heatmap": heatmap,
            "heatmap_metadata": heatmap_metadata,
            "skew": skew,
            "term_structure": term_structure,
            "oi_by_expiry": oi_by_expiry,
            "probability": prob,
            "gex": gex,
            "oi_levels": oi_levels,
            "signals": signals,
            "klines": _dm.klines,
            "summary": {
                "total_oi": total_oi,
                "total_call_oi": total_call_oi,
                "total_put_oi": total_put_oi,
                "put_call_ratio": round(pc_ratio, 3),
                "atm_iv": atm_iv,
            },
            "last_update": getattr(_dm, "last_update", time.time()),
            "status": "ok"
        }
    }


_last_immediate_candidate_key = None
_last_canonical_status = None

def _check_and_trigger_immediate_snapshot(payload: Dict[str, Any]):
    global _last_immediate_candidate_key, _last_canonical_status
    
    manual_setup = payload.get("manual_setup") or {}
    current_status = manual_setup.get("manual_status")
    candidate_key = manual_setup.get("candidate_key")
    candidate_is_new = manual_setup.get("candidate_is_new") == 1
    
    trigger = False
    if current_status == "ENTRY_CANDIDATE" and candidate_key:
        if candidate_is_new:
            trigger = True
        elif candidate_key != _last_immediate_candidate_key:
            trigger = True
        elif _last_canonical_status != "ENTRY_CANDIDATE":
            trigger = True
            
    _last_canonical_status = current_status
    
    if trigger:
        _last_immediate_candidate_key = candidate_key
        try:
            import asyncio
            from main import server_snapshot_worker
            if server_snapshot_worker:
                log.info(f"Triggering immediate server snapshot for candidate_key={candidate_key}")
                asyncio.create_task(server_snapshot_worker.write_immediate_snapshot(payload))
        except Exception as e:
            log.error(f"Failed to check/trigger immediate snapshot: {e}")

@router.get("/manual-trading/current")
async def get_manual_trading_current():
    """Latest read-only manual trading context from already-computed MOS fields."""
    if _dm is None:
        return {
            "status": "loading",
            "manual_setup": None,
            "source_fields": {},
            "debug": "dm is None",
        }
    if getattr(_dm, "spot_price", 0) <= 0 and not getattr(_dm, "klines", []):
        return {
            "status": "loading",
            "manual_setup": None,
            "source_fields": {},
            "debug": f"spot_price and klines empty, "
                     f"tickers_count={len(getattr(_dm, 'tickers', []))}, spot={getattr(_dm, 'spot_price', 0)}",
        }

    from engine.state_engine import StateEngine
    market_state = StateEngine.build_market_state(_dm)
    payload = _build_manual_trading_payload(market_state)
    if payload.get("status") == "ok":
        _record_manual_watchlist(market_state, payload.get("manual_setup"), payload.get("source_fields"))
        _check_and_trigger_immediate_snapshot(payload)
    return payload


@router.get("/manual-trading/watchlist")
async def get_manual_trading_watchlist(
    statuses: Optional[str] = None,
    biases: Optional[str] = None,
    limit: int = 50,
):
    """Recent ManualSetupClassifier contexts kept in memory only."""
    if _dm is not None and _dm.expiries:
        from engine.state_engine import StateEngine
        market_state = StateEngine.build_market_state(_dm)
        payload = _build_manual_trading_payload(market_state)
        if payload.get("status") == "ok":
            _record_manual_watchlist(market_state, payload.get("manual_setup"), payload.get("source_fields"))

    safe_limit = max(1, min(int(limit or 50), 100))
    rows = _filter_manual_watchlist(_MANUAL_WATCHLIST, statuses, biases, safe_limit)
    return {
        "status": "ok",
        "rows": rows,
        "count": len(rows),
        "available_count": len(_MANUAL_WATCHLIST),
        "filters": {
            "statuses": statuses,
            "biases": biases,
            "limit": safe_limit,
        },
    }


@router.get("/manual-trading/chart-overlay")
async def get_manual_trading_chart_overlay():
    """Read-only chart overlay data for manual trading context."""
    if _dm is None:
        return {
            "status": "loading",
            "overlay": None,
            "debug": "dm is None",
        }
    if getattr(_dm, "spot_price", 0) <= 0 and not getattr(_dm, "klines", []):
        return {
            "status": "loading",
            "overlay": None,
            "debug": f"spot_price and klines empty, "
                     f"tickers_count={len(getattr(_dm, 'tickers', []))}, spot={getattr(_dm, 'spot_price', 0)}",
        }

    from engine.state_engine import StateEngine
    market_state = StateEngine.build_market_state(_dm)
    payload = _build_manual_trading_payload(market_state)
    _record_manual_watchlist(market_state, payload["manual_setup"], payload["source_fields"])
    overlay = _build_manual_chart_overlay(market_state, _MANUAL_WATCHLIST)
    return {
        "status": "ok",
        "overlay": overlay,
    }


@router.post("/manual-trading/log-snapshot")
async def post_manual_trading_log_snapshot(payload: Dict[str, Any]):
    """Log manual trading state snapshot."""
    try:
        # ServerSnapshotWorker is now the single canonical writer for snapshots.
        # Frontend snapshots are ignored to prevent incomplete trade snapshots.
        return {"status": "ok", "message": "Ignored in favor of ServerSnapshotWorker"}
    except Exception as e:
        log.error(f"Error logging manual snapshot: {e}")
        return {"status": "error", "message": str(e)}

@router.post("/manual-trading/log-event")
async def post_manual_trading_log_event(payload: Dict[str, Any]):
    """Log manual trading state transition event."""
    try:
        get_manual_logger().log_event(payload)
        return {"status": "ok"}
    except Exception as e:
        log.error(f"Error logging manual event: {e}")
        return {"status": "error", "message": str(e)}

@router.post("/manual-trading/log-health")
async def post_manual_trading_log_health(payload: Dict[str, Any]):
    """Log manual trading chart update health."""
    try:
        get_manual_logger().log_health(payload)
        return {"status": "ok"}
    except Exception as e:
        log.error(f"Error logging manual chart health: {e}")
        return {"status": "error", "message": str(e)}

@router.get("/manual-trading/logging-status")
async def get_manual_trading_logging_status():
    """Get manual trading logging statistics."""
    try:
        return get_manual_logger().get_logging_status()
    except Exception as e:
        log.error(f"Error getting manual logging status: {e}")
        return {"status": "error", "message": str(e)}


@router.get("/kline")
async def get_kline():
    """Свечные данные BTCUSDT."""
    if _dm is None:
        return {"klines": []}
    return {"klines": _dm.klines}


@router.get("/manual-trading/kline")
async def get_manual_trading_kline():
    """1m свечи BTCUSDT для Manual Trading чарта — приоритет локальной БД."""
    EMPTY = {"status": "DEGRADED", "candles": [], "error_reason": "NO_KLINE_DATA"}

    # Попытка получить свечи из локальной БД (быстро, без rate limit и таймаутов)
    try:
        from engine.research_logger import get_db_connection
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT timestamp_utc, open, high, low, close, volume 
            FROM ohlcv_candles 
            ORDER BY timestamp_utc DESC LIMIT 1440
        ''')
        rows = cursor.fetchall()
        conn.close()

        if rows and len(rows) > 0:
            klines = []
            import math
            for r in reversed(rows):
                try:
                    ts = int(r[0] * 1000)
                    o = float(r[1])
                    h = float(r[2])
                    l = float(r[3])
                    c = float(r[4])
                    v = float(r[5]) if r[5] is not None else 0.0
                    
                    if ts <= 0: continue
                    if any(math.isnan(x) or math.isinf(x) for x in (o, h, l, c)): continue
                    if h < l: continue
                    
                    klines.append({
                        "ts": ts,
                        "o": o,
                        "h": h,
                        "l": l,
                        "c": c,
                        "v": v
                    })
                except (IndexError, TypeError, ValueError):
                    continue
                    
            if not klines:
                return EMPTY
            return {"status": "OK", "klines": klines, "interval": "1", "limit": 120, "source": "db"}
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("DB klines error: %s", e)

    # Fallback to Bybit
    rest_client = None
    if _dm is not None:
        if hasattr(_dm, "rest"):
            rest_client = _dm.rest                         # legacy DataManager
        elif hasattr(_dm, "adapters") and "bybit" in _dm.adapters:
            rest_client = _dm.adapters["bybit"]._rest      # MultiExchangeDataManager

    if rest_client is None:
        # Last-resort: direct httpx call
        try:
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=10) as cl:
                r = await cl.get(
                    "https://api.bybit.com/v5/market/kline",
                    params={"category": "linear", "symbol": "BTCUSDT",
                            "interval": "1", "limit": "120"},
                )
                data = r.json()
                raw = data.get("result", {}).get("list", [])
        except Exception as e:
            import logging; logging.getLogger(__name__).warning("kline httpx fallback failed: %s", e)
            return {"status": "DEGRADED", "candles": [], "error_reason": "KLINE_QUERY_FAILED", "error_message": str(e)}
    else:
        try:
            raw = await rest_client.get_kline(interval="1", limit=120)
        except Exception as e:
            import logging; logging.getLogger(__name__).warning("kline rest_client failed: %s", e)
            return {"status": "DEGRADED", "candles": [], "error_reason": "KLINE_QUERY_FAILED", "error_message": str(e)}

    # Bybit returns newest-first; normalise to oldest-first {ts,o,h,l,c,v}
    klines = []
    import math
    for k in reversed(raw):
        try:
            ts = int(k[0])
            o = float(k[1])
            h = float(k[2])
            l = float(k[3])
            c = float(k[4])
            v = float(k[5])
            
            if ts <= 0: continue
            if any(math.isnan(x) or math.isinf(x) for x in (o, h, l, c)): continue
            if h < l: continue
            
            klines.append({
                "ts": ts,
                "o": o,
                "h": h,
                "l": l,
                "c": c,
                "v": v,
            })
        except (IndexError, TypeError, ValueError):
            continue
            
    if not klines:
        return EMPTY
        
    return {"status": "OK", "klines": klines, "interval": "1", "limit": 120}

# ── Paper Trade Endpoints ─────────────────────────────────────────────────────

@router.get("/manual-trading/paper-summary")
async def manual_paper_summary():
    import sqlite3
    logger = get_manual_logger()
    conn = sqlite3.connect(logger.db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT * FROM paper_trades ORDER BY id DESC")
        rows = cursor.fetchall()
        
        trades = [dict(r) for r in rows]
        open_trades = [t for t in trades if t['status'] == 'OPEN']
        closed_trades = [t for t in trades if t['status'] == 'CLOSED']
        
        # Calculate stats
        models = {}
        for t in closed_trades:
            m = t['exit_model']
            if m not in models:
                models[m] = {'total': 0, 'wins': 0, 'tp3': 0, 'stop': 0, 'context': 0, 'r': 0.0, 'best': 0.0, 'worst': 0.0, 'mfe': 0.0, 'mae': 0.0}
            models[m]['total'] += 1
            if t['result_r'] and t['result_r'] > 0:
                models[m]['wins'] += 1
            if t['exit_reason'] == 'TP3': models[m]['tp3'] += 1
            if t['exit_reason'] == 'STOP': models[m]['stop'] += 1
            if t['exit_reason'] == 'CONTEXT_EXIT': models[m]['context'] += 1
            models[m]['r'] += (t['result_r'] or 0)
            models[m]['best'] = max(models[m]['best'], t['result_r'] or 0)
            models[m]['worst'] = min(models[m]['worst'], t['result_r'] or 0)
            models[m]['mfe'] += (t['max_mfe_r'] or 0)
            models[m]['mae'] += (t['max_mae_r'] or 0)
            
        for m in models:
            n = models[m]['total']
            models[m]['win_rate'] = round(models[m]['wins'] / n * 100, 1) if n > 0 else 0
            models[m]['avg_r'] = round(models[m]['r'] / n, 2) if n > 0 else 0
            models[m]['avg_mfe'] = round(models[m]['mfe'] / n, 2) if n > 0 else 0
            models[m]['avg_mae'] = round(models[m]['mae'] / n, 2) if n > 0 else 0
            
        return {
            "status": "ok",
            "total_trades": len(trades),
            "open_count": len(open_trades),
            "closed_count": len(closed_trades),
            "models": models,
            "active_trades": open_trades,
            "recent_closed": closed_trades[:10]
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()

@router.get("/manual-trading/paper-trades")
async def manual_paper_trades(limit: int = 100):
    import sqlite3
    logger = get_manual_logger()
    conn = sqlite3.connect(logger.db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM paper_trades ORDER BY id DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        return {"status": "ok", "trades": [dict(r) for r in rows]}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()

@router.get("/manual-trading/diagnostics")
async def manual_diagnostics():
    import os
    import sqlite3
    from engine.version import CODE_VERSION, ENGINE_PATCH_VERSION
    
    logger = get_manual_logger()
    db_path = os.path.abspath(logger.db_path)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Snapshot counts
        cursor.execute("SELECT COUNT(*), MAX(ts) FROM manual_trading_snapshots")
        ss_row = cursor.fetchone()
        manual_snapshot_count = ss_row[0] if ss_row else 0
        latest_manual_snapshot_ts = ss_row[1] if ss_row else None
        
        # Decision events
        cursor.execute("SELECT COUNT(*), MAX(ts) FROM manual_decision_events")
        de_row = cursor.fetchone()
        manual_decision_event_count = de_row[0] if de_row else 0
        latest_decision_event_ts = de_row[1] if de_row else None
        
        # Chart health
        cursor.execute("SELECT COUNT(*) FROM manual_chart_health")
        ch_row = cursor.fetchone()
        manual_chart_health_count = ch_row[0] if ch_row else 0
    except Exception as e:
        log.error(f"Error querying diagnostics: {e}")
        manual_snapshot_count = manual_decision_event_count = manual_chart_health_count = 0
        latest_manual_snapshot_ts = latest_decision_event_ts = None
    finally:
        conn.close()
    
    # Worker status
    ss_worker_status = {}
    from main import server_snapshot_worker
    if server_snapshot_worker:
        ss_worker_status = server_snapshot_worker.get_status()
    
    return {
        "status": "ok",
        "cwd": os.getcwd(),
        "db_path": db_path,
        "manual_snapshot_count": manual_snapshot_count,
        "manual_decision_event_count": manual_decision_event_count,
        "manual_chart_health_count": manual_chart_health_count,
        "latest_manual_snapshot_ts": latest_manual_snapshot_ts,
        "latest_decision_event_ts": latest_decision_event_ts,
        "server_snapshot_worker_running": server_snapshot_worker is not None,
        "server_snapshot_worker_last_tick_ts": ss_worker_status.get("last_snapshot_ts"),
        "server_snapshot_worker_last_success_ts": ss_worker_status.get("last_snapshot_ts"),
        "server_snapshot_worker_last_error": ss_worker_status.get("last_error"),
        "code_version": CODE_VERSION,
        "engine_patch_version": ENGINE_PATCH_VERSION
    }

# ── Debug Endpoints ───────────────────────────────────────────────────────────

@router.get("/debug/void")
async def debug_void():
    """LiquidityVoidEngine последний breakdown — компоненты void score."""
    from engine.liquidity_void_engine import LiquidityVoidEngine
    return {"status": "ok", "debug": LiquidityVoidEngine.get_debug()}


@router.get("/debug/flow")
async def debug_flow():
    """SyntheticOrderflowEngine последний breakdown — buying/selling components."""
    from engine.synthetic_orderflow_engine import SyntheticOrderflowEngine
    return {"status": "ok", "debug": SyntheticOrderflowEngine.get_debug()}


@router.get("/debug/execution")
async def debug_execution():
    """ExecutionTimingEngine последний breakdown — scores, why_not, inputs."""
    from engine.execution_timing_engine import ExecutionTimingEngine
    return {"status": "ok", "debug": ExecutionTimingEngine.get_debug()}


@router.get("/debug/state")
async def debug_state():
    """StateEngine последний debug — pinning/transition scores, phase_context, drivers."""
    from engine.state_engine import StateEngine
    return {"status": "ok", "debug": StateEngine.get_state_debug()}


@router.get("/debug/cluster")
async def debug_cluster():
    """StateEngine последний signal cluster score breakdown."""
    from engine.state_engine import StateEngine
    return {"status": "ok", "debug": StateEngine.get_cluster_debug()}


@router.get("/debug/all")
async def debug_all():
    """Все debug endpoints за один запрос."""
    from engine.liquidity_void_engine import LiquidityVoidEngine
    from engine.synthetic_orderflow_engine import SyntheticOrderflowEngine
    from engine.execution_timing_engine import ExecutionTimingEngine
    from engine.state_engine import StateEngine
    return {
        "status": "ok",
        "void": LiquidityVoidEngine.get_debug(),
        "flow": SyntheticOrderflowEngine.get_debug(),
        "execution": ExecutionTimingEngine.get_debug(),
        "state": StateEngine.get_state_debug(),
        "cluster": StateEngine.get_cluster_debug(),
    }
