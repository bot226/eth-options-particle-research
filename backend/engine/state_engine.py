"""State Engine - Pure Orchestrator for MarketState and Temporal Memory."""

import time
from enum import Enum
from collections import deque
from typing import Dict, Any

from engine.gamma_engine import GammaEngine
from engine.volatility_engine import VolatilityEngine
from engine.skew_engine import SkewEngine
from engine.liquidity_engine import LiquidityEngine
from engine.flow_engine import FlowEngine
from engine.meta_state_engine import MetaStateEngine
from engine.scenario_engine import ScenarioEngine
from engine.execution_engine import ExecutionEngine
from engine.narrative_engine import NarrativeEngine

from engine.confidence_engine import ConfidenceEngine
from engine.exchange_divergence_engine import ExchangeDivergenceEngine
from engine.exchange_health_engine import ExchangeHealthEngine
from engine.exchange_leadership_engine import ExchangeLeadershipEngine
from config import MULTI_EXCHANGE_ENABLED

# Phase 1 Advanced Intelligence
from engine.gamma_surface_engine import GammaSurfaceEngine
from engine.liquidity_void_engine import LiquidityVoidEngine
from engine.dealer_hedging_engine import DealerHedgingEngine
from engine.regime_transition_engine import RegimeTransitionEngine

# Phase 2 Advanced Intelligence
from engine.term_structure_engine import TermStructureEngine
from engine.synthetic_orderflow_engine import SyntheticOrderflowEngine
from engine.breakout_timing_engine import BreakoutTimingEngine
from engine.execution_timing_engine import ExecutionTimingEngine

class MarketState(Enum):
    PINNING = "PINNING"
    COMPRESSION = "COMPRESSION"
    TRANSITION = "TRANSITION"
    BREAKOUT_SETUP = "BREAKOUT_SETUP"
    HEDGE_CHASE = "HEDGE_CHASE"
    EXPANSION = "EXPANSION"
    SHORT_SQUEEZE = "SHORT_SQUEEZE"
    LONG_LIQUIDATION = "LONG_LIQUIDATION"
    PANIC = "PANIC"
    EXHAUSTION = "EXHAUSTION"
    REBALANCE = "REBALANCE"
    UNKNOWN = "UNKNOWN"  # kept for backward compat, never actively set

# ── Bucket functions for market_phase_hash ────────────────────────
def bucket_iv_velocity(v):
    if v <= -2.0: return "VOL_CONTRACTING"
    if v < 0.5: return "VOL_STABLE"
    if v < 2.0: return "VOL_RISING"
    return "VOL_EXPANDING"

def bucket_flow(f):
    if f <= -50: return "STRONG_SELL"
    if f <= -20: return "MODERATE_SELL"
    if f < -10: return "WEAK_SELL"
    if f <= 10: return "NEUTRAL"
    if f < 20: return "WEAK_BUY"
    if f < 50: return "MODERATE_BUY"
    return "STRONG_BUY"

def bucket_void(s):
    if s < 20: return "NO_VOID"
    if s < 40: return "WEAK_VOID"
    if s < 60: return "MODERATE_VOID"
    if s < 80: return "SIGNIFICANT_VOID"
    return "EXTREME_VOID"

def bucket_expansion(s):
    if s < 25: return "LOW"
    if s < 50: return "MODERATE"
    if s < 75: return "HIGH"
    return "EXTREME"


class StateEngine:
    _history = deque(maxlen=500)
    _events = []
    
    # Debouncing state
    _current_state = MarketState.TRANSITION
    _previous_state = MarketState.TRANSITION
    _candidate_state = MarketState.UNKNOWN
    _candidate_updates = 0
    _last_state_change_ts = time.time()
    
    # Differentiated persistence rules
    PERSISTENCE_RULES = {
        ("PINNING", "TRANSITION"): 3,
        ("TRANSITION", "PINNING"): 4,
        ("COMPRESSION", "TRANSITION"): 3,
        ("TRANSITION", "COMPRESSION"): 3,
        ("TRANSITION", "BREAKOUT_SETUP"): 2,
        ("BREAKOUT_SETUP", "EXPANSION"): 2,
    }
    DEFAULT_REQUIRED_PERSISTENCE = 5
    
    # Previous void score for VOID_CLEARED detection
    _prev_void_score = 0.0
    _last_void_band = "NONE"
    
    # Previous execution timing state for transition events
    _prev_execution_timing_state = "WAIT"
    # Previous gamma states for transition events
    _prev_gamma_slope_state = "neutral"
    _prev_gamma_acceleration_state = "neutral"
    # PINNING_BREAK tracking: True if already emitted for current transition
    _pinning_break_emitted_for_transition = False
    
    # Score history for delta tracking
    _pinning_score_history = deque(maxlen=20)
    _transition_score_history = deque(maxlen=20)
    
    # Crossing-event state tracking
    _prev_flow_intensity = 0.0
    _prev_flow_pressure = 0.0
    _prev_iv_velocity = 0.0
    _prev_expansion_prob = 0.0
    _current_phase_context = "NORMAL"
    _current_snapshot_sequence_id = 0
    
    # Warmup suppression: suppress crossing events until we have enough history
    # to distinguish startup artifacts from real signals
    WARMUP_MIN_SNAPSHOTS = 5
    _total_snapshots_processed = 0
    
    # Debug storage
    _last_cluster_breakdown = {}
    _cluster_debug_history = deque(maxlen=1000)
    _last_state_debug = {}
    _last_phase_context_debug = {}
    _last_event_skip_debug = {}
    _last_raw_state_debug = {}
    _state_debug_history = deque(maxlen=1000)
    
    _last_events = {}
    EVENT_COOLDOWN = {
        "REGIME_CHANGE": 60,
        "BREAKOUT_ALERT": 300,
        "VOID_DETECTED": 600,
        "VOID_INTENSIFYING": 300,
        "VOID_CLEARED": 300,
        "FLOW_SURGE": 180,
        "GAMMA_COLLAPSE": 300,
        "GAMMA_WEAKENING": 300,
        "EXECUTION_WINDOW_OPEN": 120,
        "VOLATILITY_EXPANSION": 180,
        "PINNING_BREAK": 120,
        "STRUCTURE_UNSTABLE": 300,
    }

    VALID_EVENT_TYPES = {
        "REGIME_CHANGE", "BREAKOUT_ALERT", "FLOW_SURGE", 
        "VOID_DETECTED", "VOID_INTENSIFYING", "VOID_CLEARED", 
        "GAMMA_WEAKENING", "GAMMA_COLLAPSE", "EXECUTION_WINDOW_OPEN", 
        "VOLATILITY_EXPANSION", "PINNING_BREAK", "STRUCTURE_UNSTABLE",
        "OHLCV_PRICE_ACTION",
    }

    @staticmethod
    def normalize_event_type(event_type: str) -> str:
        if not event_type:
            return "UNKNOWN_EVENT"

        e = str(event_type).strip().upper().replace(" ", "_").replace("-", "_")

        aliases = {
            "EXECUTION_WINDOW": "EXECUTION_WINDOW_OPEN",
            "WINDOW_OPEN": "EXECUTION_WINDOW_OPEN",
            "EXECUTION_OPEN": "EXECUTION_WINDOW_OPEN",
            "EXECUTION_WINDOW_START": "EXECUTION_WINDOW_OPEN",
            "STRUCTURE_UNSTABLE_": "STRUCTURE_UNSTABLE",
            "VOL_EXPANSION": "VOLATILITY_EXPANSION",
            "IV_EXPANSION": "VOLATILITY_EXPANSION",
        }

        e = aliases.get(e, e)
        if e not in StateEngine.VALID_EVENT_TYPES:
            return e
        return e

    @classmethod
    def fire_event(cls, ev_type: str, severity: str, msg: str,
                   fingerprint: str = "", payload: dict = None):
        ev_type = cls.normalize_event_type(ev_type)
        now = time.time()
        fp = f"{ev_type}_{fingerprint}"
        cooldown = cls.EVENT_COOLDOWN.get(ev_type, 60)
        last_emitted = cls._last_events.get(fp, 0)
        elapsed = now - last_emitted
        event_payload = dict(payload or {})
        event_payload.setdefault("phase_context", getattr(cls, "_current_phase_context", "NORMAL"))
        if elapsed > cooldown:
            cls._events.append({
                "type": ev_type,
                "severity": severity,
                "message": msg,
                "payload": event_payload,
            })
            cls._last_events[fp] = now
            cls._last_event_skip_debug = {}
            if cls._last_state_debug:
                cls._last_state_debug["event_skip_debug"] = {}
            return True

        cls._last_event_skip_debug = {
            "event_type": ev_type,
            "would_emit": True,
            "skipped": True,
            "skip_reason": "cooldown",
            "fingerprint": fp,
            "memory_cooldown_remaining": round(max(0, cooldown - elapsed), 1),
            "persistent_cooldown_remaining": 0,
            "previous_execution_timing_state": event_payload.get("previous_execution_timing_state"),
            "current_execution_timing_state": event_payload.get("current_execution_timing_state"),
            "snapshot_sequence_id": getattr(cls, "_current_snapshot_sequence_id", 0),
        }
        if cls._last_state_debug:
            cls._last_state_debug["event_skip_debug"] = dict(cls._last_event_skip_debug)
        return False

    @classmethod
    def build_market_state(cls, dm) -> Dict[str, Any]:
        ts = int(time.time())
        spot = dm.spot_price
        chain = dict(dm.chain)
        
        # 1. Gather component states
        gamma = GammaEngine.calculate(chain, spot, dm)
        vol = VolatilityEngine.calculate(chain, spot, dm)
        skew = SkewEngine.calculate(chain, spot, dm)
        liq = LiquidityEngine.calculate(chain, spot, dm)
        flow = FlowEngine.calculate(chain, spot, dm)
        
        meta = MetaStateEngine.calculate(gamma, vol, skew, liq, flow)
        scenario = ScenarioEngine.calculate(gamma, vol, liq, flow, spot)
        execution = ExecutionEngine.calculate(meta, scenario, gamma, spot)

        # 1.5 Multi-Exchange Intelligence
        health_data = getattr(dm, 'exchange_health', {})
        health_report = ExchangeHealthEngine.calculate(health_data, MULTI_EXCHANGE_ENABLED)
        
        per_exchange_tickers = getattr(dm, 'per_exchange_tickers', {})
        weights = getattr(dm, 'exchange_weights', {})
        summary = getattr(dm, 'per_exchange_summary', {})
        
        div_result = ExchangeDivergenceEngine.calculate(per_exchange_tickers, health_data, spot)
        divergences = div_result.get("divergences", [])
        
        conf_result = ConfidenceEngine.calculate(health_data, divergences, weights, summary)
        leadership = ExchangeLeadershipEngine.calculate(per_exchange_tickers, spot)
        
        # 1.6 Phase 1 Advanced Intelligence (READ-ONLY)
        gamma_surface = GammaSurfaceEngine.calculate(chain, spot, dm)
        liquidity_voids = LiquidityVoidEngine.calculate(chain, spot, gamma_surface)
        
        # 1.7 Phase 2 Advanced Intelligence (computed before regime_transition for input)
        term_structure = TermStructureEngine.calculate(chain, spot)
        synthetic_orderflow = SyntheticOrderflowEngine.calculate(vol, flow, dm, spot)
        
        # DealerHedgingEngine now accepts gamma_surface, vol, orderflow for pressure scoring
        dealer_hedging = DealerHedgingEngine.calculate(
            chain, spot,
            gamma_surface=gamma_surface,
            volatility=vol,
            orderflow=synthetic_orderflow,
            gamma_metrics=gamma
        )
        
        # RegimeTransitionEngine now accepts gamma_surface, term_structure, synthetic_orderflow
        regime_transition = RegimeTransitionEngine.calculate(
            gamma, vol, flow, liquidity_voids, dealer_hedging,
            gamma_surface=gamma_surface,
            term_structure=term_structure,
            synthetic_orderflow=synthetic_orderflow
        )
        
        breakout_timing = BreakoutTimingEngine.calculate(vol, regime_transition, synthetic_orderflow)
        
        phase1_payload = {
            "gamma_surface": gamma_surface,
            "liquidity_voids": liquidity_voids,
            "dealer_hedging": dealer_hedging,
            "regime_transition": regime_transition,
        }
        
        # 1.8 Advanced Structural Metrics (computed early for execution_timing safety)
        # active_sources: list of exchange IDs that are not OFFLINE
        DERIBIT_OPTIONS_TTL_SEC = 120
        deribit_status = "MISSING"
        exchange_meta = getattr(dm, 'exchange_meta', {})
        if health_data:
            active_sources = []
            for ex, h in health_data.items():
                meta = exchange_meta.get(ex, {})
                status = meta.get("status", h.get("status", "OFFLINE"))
                stale = h.get("stale_seconds", 0)
                if ex == "deribit":
                    if status in ("ONLINE",) and stale <= DERIBIT_OPTIONS_TTL_SEC:
                        active_sources.append(ex)
                        deribit_status = status
                    elif status == "STALE" or (status in ("ONLINE", "EMPTY", "PARSE_ERROR") and stale > DERIBIT_OPTIONS_TTL_SEC):
                        deribit_status = "STALE"
                    else:
                        deribit_status = status if status in ("EMPTY", "PARSE_ERROR") else ("ERROR" if h.get("error_message") else "OFFLINE")
                else:
                    if status not in ("OFFLINE", None):
                        active_sources.append(ex)
        else:
            # Single-exchange fallback
            active_sources = ["bybit"]
        
        # oi_total / volume_total (must be computed before data_quality)
        global_metrics = getattr(dm, 'global_metrics', {})
        total_oi = global_metrics.get("total_oi", 0)
        total_volume = global_metrics.get("total_volume", 0)
        if total_oi == 0 and total_volume == 0:
            # Fallback to sum of per-exchange totals
            for ex_data in health_data.values():
                total_oi += float(ex_data.get("total_oi", 0))
                total_volume += float(ex_data.get("total_volume", 0))
        
        # data_quality — INFRASTRUCTURE INTEGRITY ONLY
        # Separate from global_confidence (which is structural intelligence quality)
        dq_penalties = 0
        
        # Penalty: stale/high-latency feeds
        for ex_id, h in health_data.items():
            stale = h.get("stale_seconds", 0)
            latency = h.get("latency_ms", 0)
            
            # For Deribit, ignore penalty if it's within TTL, handled differently
            if ex_id == "deribit":
                if stale > DERIBIT_OPTIONS_TTL_SEC:
                    dq_penalties += 20
            else:
                if stale > 120:
                    dq_penalties += 30
                elif stale > 60:
                    dq_penalties += 15
                elif latency > 2000:
                    dq_penalties += 10
        
        # Penalty: insufficient sources
        if not active_sources:
            dq_penalties += 40
        elif len(active_sources) < 2 and MULTI_EXCHANGE_ENABLED:
            dq_penalties += 15
        
        # Penalty: missing critical metrics
        _crit = [spot, total_oi]
        _missing = sum(1 for m in _crit if m == 0)
        dq_penalties += _missing * 15
        
        dq_score = max(0, 100 - dq_penalties)
        if dq_score >= 90:
            dq = "GOOD"
        elif dq_score >= 60:
            dq = "DEGRADED"
        elif dq_score >= 30:
            dq = "PARTIAL"
        else:
            dq = "CRITICAL"

        # ── signal_cluster_score (7-component model with linear interpolation) ─
        cluster_score = 0.0
        cluster_breakdown = {}
        gamma_slope_state = gamma_surface.get("metrics", {}).get("gamma_slope_state", "neutral")
        gamma_accel_state = gamma_surface.get("metrics", {}).get("gamma_acceleration_state", "neutral")
        iv_state = term_structure.get("features", {}).get("term_structure_state", "neutral")
        flow_mom = synthetic_orderflow.get("metrics", {}).get("flow_momentum_score", 0)
        void_score = liquidity_voids.get("metrics", {}).get("void_score", 0)
        expansion_prob = regime_transition.get("metrics", {}).get("expansion_probability", 0)
        hedge_pressure = dealer_hedging.get("metrics", {}).get("dealer_hedging_pressure", "LOW")
        hedge_pressure_score = dealer_hedging.get("metrics", {}).get("dealer_hedging_pressure_score", 0)
        iv_velocity = vol.get("metrics", {}).get("iv_velocity", 0)
        # Directional flow scale: abs(flow_momentum_score)
        flow_intensity = abs(flow_mom)
        
        def _lerp(value, low, high, max_weight):
            """Linear interpolation: 0 at low, max_weight at high, clamped."""
            if value <= low:
                return 0.0
            if value >= high:
                return float(max_weight)
            return float(max_weight) * (value - low) / (high - low)
        
        # 1. Gamma slope (0-20): severity mapping then lerp
        gamma_severity = {"neutral": 0, "negative": 1, "weakening": 2, "collapsing": 3}.get(gamma_slope_state, 0)
        gamma_component = round(_lerp(gamma_severity, 0, 3, 20), 1)
        cluster_score += gamma_component
        cluster_breakdown["gamma_component"] = gamma_component
        cluster_breakdown["gamma_input"] = gamma_slope_state
            
        # 2. IV velocity (0-20): lerp 0→5
        iv_component = round(_lerp(abs(iv_velocity), 0.0, 5.0, 20), 1)
        cluster_score += iv_component
        cluster_breakdown["iv_velocity_component"] = iv_component
        cluster_breakdown["iv_velocity_input"] = round(iv_velocity, 2)
        
        # 3. Flow pressure (0-20, directional scale): lerp 10→50
        flow_component = round(_lerp(flow_intensity, 10, 50, 20), 1)
        cluster_score += flow_component
        cluster_breakdown["flow_component"] = flow_component
        cluster_breakdown["flow_intensity_input"] = round(flow_intensity, 1)
        
        # 4. Liquidity void intensity (0-20): lerp 20→80
        void_component = round(_lerp(void_score, 20, 80, 20), 1)
        cluster_score += void_component
        cluster_breakdown["liquidity_void_component"] = void_component
        cluster_breakdown["void_score_input"] = round(void_score, 1)
        
        # 5. Expansion probability (0-20): lerp 20→70
        expansion_component = round(_lerp(expansion_prob, 20, 70, 20), 1)
        cluster_score += expansion_component
        cluster_breakdown["expansion_component"] = expansion_component
        cluster_breakdown["expansion_prob_input"] = round(expansion_prob, 1)
        
        # 6. Dealer hedging pressure (0-10): lerp from pressure_score 0→100
        hedge_component = round(_lerp(hedge_pressure_score, 0, 100, 10), 1)
        cluster_score += hedge_component
        cluster_breakdown["hedge_component"] = hedge_component
        cluster_breakdown["hedge_pressure_score_input"] = hedge_pressure_score
        cluster_breakdown["hedge_pressure_label"] = hedge_pressure
        
        # 7. Term structure stress (0-10): categorical
        ts_component = 0
        if iv_state in ("BACKWARDATION", "INVERSION", "FRONT_STRESS"):
            ts_component = 10
        elif iv_state in ("FLAT", "MIXED"):
            ts_component = 5
        cluster_score += ts_component
        cluster_breakdown["term_structure_component"] = ts_component
        cluster_breakdown["term_structure_input"] = iv_state
            
        cluster_score = round(min(100, cluster_score), 1)
        cluster_breakdown["total"] = cluster_score
        cluster_breakdown["timestamp"] = ts
        cls._last_cluster_breakdown = cluster_breakdown
        cls._cluster_debug_history.append(dict(cluster_breakdown))

        # Now calculate execution_timing with full context
        # NOTE: transition_score not yet computed here — will be computed below after scores
        # ExecutionTimingEngine.calculate() called later with transition_score
        execution_timing = ExecutionTimingEngine.calculate(
            phase1_payload, term_structure, synthetic_orderflow, breakout_timing,
            void_score=void_score,
            signal_cluster_score=cluster_score,
            iv_velocity=iv_velocity,
            data_quality=dq,
            active_sources=active_sources,
            transition_score=0,  # placeholder — will be recalculated below
        )
        
        phase2_payload = {
            "term_structure": term_structure,
            "synthetic_orderflow": synthetic_orderflow,
            "breakout_timing": breakout_timing,
            "execution_timing": execution_timing
        }
        
        advanced_intelligence = {
            "status": "experimental",
            "phase_1": phase1_payload,
            "phase_2": phase2_payload,
            "confidence": {
                "score": max(0, 100 - len(divergences)*10),
                "reason": ["Experimental engines running in isolated context."]
            }
        }
        
        narrative = NarrativeEngine.generate(
            gamma, vol, skew, liq, flow, meta, scenario, execution, spot,
            divergences=divergences, leadership=leadership,
            advanced_intelligence=advanced_intelligence
        )

        advanced_intelligence["signal_cluster_score"] = cluster_score
        
        # Calculate global_confidence (Intelligence confidence)
        signal_alignment = min(100, cluster_score * 1.5)
        regime_stab = max(0, 100 - (cls._candidate_updates * 5)) if cls._candidate_state != cls._current_state else 100
        struct_coh = 100 if gamma_slope_state not in ("weakening", "collapsing", "negative") else 50
        global_confidence = (signal_alignment * 0.4) + (regime_stab * 0.4) + (struct_coh * 0.2)
        global_confidence = min(100.0, max(0.0, float(global_confidence)))

        # 2. Calculate pinning/transition scores — NO execution_timing_state dependency
        execution_timing_state = execution_timing.get("features", {}).get("execution_state", "WAIT")
        g_regime = gamma.get("signals", {}).get("gamma_regime", "UNKNOWN")
        
        # distance_to_nearest_wall_pct
        call_wall = gamma.get("metrics", {}).get("call_wall", 0)
        put_wall = gamma.get("metrics", {}).get("put_wall", 0)
        distance_to_nearest_wall_pct = 999.0  # default: far from wall
        if spot > 0:
            distances = []
            if call_wall > 0:
                distances.append(abs(call_wall - spot) / spot * 100)
            if put_wall > 0:
                distances.append(abs(spot - put_wall) / spot * 100)
            if distances:
                distance_to_nearest_wall_pct = min(distances)
        
        # ── pinning_score ──
        pinning_score = 0
        if g_regime == "POSITIVE_GAMMA":
            pinning_score += 25
        if distance_to_nearest_wall_pct <= 0.5:
            pinning_score += 25
        elif distance_to_nearest_wall_pct <= 1.0:
            pinning_score += 15
        if iv_velocity <= 0.5:
            pinning_score += 15
        if expansion_prob < 35:
            pinning_score += 15
        if void_score < 45:
            pinning_score += 10
        if flow_intensity < 20:
            pinning_score += 10
        if hedge_pressure == "LOW":
            pinning_score += 10
        pinning_score_components = {
            "positive_gamma": 25 if g_regime == "POSITIVE_GAMMA" else 0,
            "hard_wall_proximity": 25 if distance_to_nearest_wall_pct <= 0.5 else 0,
            "soft_wall_proximity": 15 if 0.5 < distance_to_nearest_wall_pct <= 1.0 else 0,
            "iv_velocity_stable": 15 if iv_velocity <= 0.5 else 0,
            "expansion_probability_low": 15 if expansion_prob < 35 else 0,
            "void_score_low": 10 if void_score < 45 else 0,
            "flow_intensity_low": 10 if flow_intensity < 20 else 0,
            "hedge_pressure_low": 10 if hedge_pressure == "LOW" else 0,
        }
        
        # ── transition_score ──
        transition_score = 0
        if gamma_slope_state == "collapsing":
            transition_score += 25
        elif gamma_slope_state == "weakening":
            transition_score += 18
        elif gamma_slope_state == "negative":
            transition_score += 8
        if gamma_accel_state in ("accelerating", "collapsing"):
            transition_score += 12
        if iv_velocity >= 2.0:
            transition_score += 20
        elif iv_velocity >= 0.5:
            transition_score += 10
        if expansion_prob >= 50:
            transition_score += 20
        elif expansion_prob >= 35:
            transition_score += 10
        if void_score >= 55:
            transition_score += 15
        elif void_score >= 45:
            transition_score += 8
        if flow_intensity >= 35:
            transition_score += 15
        elif flow_intensity >= 20:
            transition_score += 8
        if hedge_pressure == "HIGH":
            transition_score += 15
        elif hedge_pressure == "MEDIUM":
            transition_score += 8
        if distance_to_nearest_wall_pct > 1.0:
            transition_score += 10
        transition_score_components = {
            "gamma_slope": (
                25 if gamma_slope_state == "collapsing"
                else 18 if gamma_slope_state == "weakening"
                else 8 if gamma_slope_state == "negative"
                else 0
            ),
            "gamma_acceleration": 12 if gamma_accel_state in ("accelerating", "collapsing") else 0,
            "iv_velocity": 20 if iv_velocity >= 2.0 else 10 if iv_velocity >= 0.5 else 0,
            "expansion_probability": 20 if expansion_prob >= 50 else 10 if expansion_prob >= 35 else 0,
            "void_score": 15 if void_score >= 55 else 8 if void_score >= 45 else 0,
            "flow_intensity": 15 if flow_intensity >= 35 else 8 if flow_intensity >= 20 else 0,
            "hedge_pressure": 15 if hedge_pressure == "HIGH" else 8 if hedge_pressure == "MEDIUM" else 0,
            "wall_distance": 10 if distance_to_nearest_wall_pct > 1.0 else 0,
        }
        
        # ── pinning_break_factors ──
        pinning_break_factors = 0
        if gamma_slope_state in ("weakening", "collapsing"):
            pinning_break_factors += 1
        if gamma_accel_state in ("accelerating", "collapsing"):
            pinning_break_factors += 1
        if iv_velocity >= 0.5:
            pinning_break_factors += 1
        if expansion_prob >= 35:
            pinning_break_factors += 1
        if flow_intensity >= 20:
            pinning_break_factors += 1
        if void_score >= 45:
            pinning_break_factors += 1
        if hedge_pressure in ("MEDIUM", "HIGH"):
            pinning_break_factors += 1
        if distance_to_nearest_wall_pct > 1.0:
            pinning_break_factors += 1
        
        # ── Score delta tracking (5m ~ 15 snapshots at 20s interval) ──
        cls._pinning_score_history.append((ts, pinning_score))
        cls._transition_score_history.append((ts, transition_score))
        
        pinning_score_delta_5m = 0.0
        transition_score_delta_5m = 0.0
        cutoff_5m = ts - 300
        old_pinning = [s for t, s in cls._pinning_score_history if t <= cutoff_5m]
        old_transition = [s for t, s in cls._transition_score_history if t <= cutoff_5m]
        if old_pinning:
            pinning_score_delta_5m = pinning_score - old_pinning[-1]
        if old_transition:
            transition_score_delta_5m = transition_score - old_transition[-1]
        
        pinning_weakening_fast = pinning_score_delta_5m <= -15
        transition_pressure_rising = transition_score_delta_5m >= 15
        
        # ── Recalculate execution_timing with actual transition_score ──
        # First call above used transition_score=0 (placeholder)
        # Now recalculate with the real transition_score
        execution_timing = ExecutionTimingEngine.calculate(
            phase1_payload, term_structure, synthetic_orderflow, breakout_timing,
            void_score=void_score,
            signal_cluster_score=cluster_score,
            iv_velocity=iv_velocity,
            data_quality=dq,
            active_sources=active_sources,
            transition_score=transition_score,
        )
        phase2_payload["execution_timing"] = execution_timing
        advanced_intelligence["phase_2"] = phase2_payload
        
        # CRITICAL: update execution_timing_state to reflect the FINAL calculation
        # (first call used transition_score=0 placeholder — state may differ)
        execution_timing_state = execution_timing.get("features", {}).get("execution_state", "WAIT")

        # Precompute range-compression structure before raw-state selection.
        # The fuller phase_context block below uses the same conditions for payload/debug labels.
        raw_recent_return_5m = 0.0
        raw_recent_return_15m = 0.0
        raw_recent_range_15m = 0.0
        if len(cls._history) >= 15:
            prices_recent = [f["spot"] for f in list(cls._history)[-15:]]
            if prices_recent and prices_recent[0] > 0:
                raw_recent_return_5m = (prices_recent[-1] - prices_recent[0]) / prices_recent[0] * 100
        if len(cls._history) >= 45:
            prices_15m = [f["spot"] for f in list(cls._history)[-45:]]
            if prices_15m and prices_15m[0] > 0:
                raw_recent_return_15m = (prices_15m[-1] - prices_15m[0]) / prices_15m[0] * 100
                raw_recent_range_15m = (max(prices_15m) - min(prices_15m)) / prices_15m[0] * 100
        range_compression_signal = (
            raw_recent_range_15m > 0
            and raw_recent_range_15m < 0.35
            and flow_intensity < 20
            and expansion_prob < 35
            and void_score < 40
        )
        wall_pinning_confirmed = distance_to_nearest_wall_pct <= 0.5
        compression_score_components = {
            "iv_regime_compression": 30 if vol.get("signals", {}).get("iv_regime", "NORMAL") == "COMPRESSION" else 0,
            "range_compression": 25 if range_compression_signal else 0,
            "low_flow": 10 if flow_intensity < 20 else 0,
            "low_expansion_probability": 10 if expansion_prob < 35 else 0,
            "low_void_score": 10 if void_score < 40 else 0,
            "stable_iv_velocity": 10 if abs(iv_velocity) <= 0.5 else 0,
            "not_hard_wall_pinning": 5 if not wall_pinning_confirmed else 0,
        }
        compression_score = sum(compression_score_components.values())

        # ── Determine raw state — score-based ──
        v_regime = vol.get("signals", {}).get("iv_regime", "NORMAL")
        f_bias = flow.get("signals", {}).get("flow_bias", "NEUTRAL")
        
        raw_state = cls._determine_raw_state(
            gamma, vol, flow, liq,
            expansion_prob=expansion_prob,
            void_score=void_score,
            iv_velocity=iv_velocity,
            dealer_hedging_pressure=hedge_pressure,
            pinning_score=pinning_score,
            transition_score=transition_score,
            pinning_break_factors=pinning_break_factors,
            pinning_weakening_fast=pinning_weakening_fast,
            transition_pressure_rising=transition_pressure_rising,
            range_compression_signal=range_compression_signal,
            wall_pinning_confirmed=wall_pinning_confirmed,
            compression_score=compression_score,
        )
        cls._last_raw_state_debug = {
            "raw_state": raw_state.value,
            "gamma_regime": g_regime,
            "volatility_regime": v_regime,
            "compression_score": compression_score,
            "compression_score_components": compression_score_components,
            "range_compression_signal": range_compression_signal,
            "wall_pinning_confirmed": wall_pinning_confirmed,
            "positive_gamma": g_regime == "POSITIVE_GAMMA",
            "near_wall": distance_to_nearest_wall_pct <= 1.0,
            "hard_wall_pinning": distance_to_nearest_wall_pct <= 0.5,
            "pinning_strength": gamma.get("metrics", {}).get("pinning_strength", 0),
            "dealer_positioning": gamma.get("signals", {}).get("dealer_positioning", "UNKNOWN"),
            "dealer_stability_zone": gamma.get("signals", {}).get("pinning_bias", "UNKNOWN"),
            "raw_recent_return_5m": round(raw_recent_return_5m, 3),
            "raw_recent_return_15m": round(raw_recent_return_15m, 3),
            "raw_recent_range_15m": round(raw_recent_range_15m, 3),
            "pinning_score_components": pinning_score_components,
            "transition_score_components": transition_score_components,
        }
        
        # ── phase_context ──
        phase_context = "NORMAL"
        # Check for recent impulse from history
        recent_impulse_detected = False
        recent_return_5m = 0.0
        recent_return_15m = 0.0
        recent_range_5m = 0.0
        recent_range_15m = 0.0
        recent_recovery_failed = False
        if len(cls._history) >= 15:
            prices_recent = [f["spot"] for f in list(cls._history)[-15:]]
            if prices_recent and prices_recent[0] > 0:
                recent_return_5m = (prices_recent[-1] - prices_recent[0]) / prices_recent[0] * 100
                recent_range_5m = (max(prices_recent) - min(prices_recent)) / prices_recent[0] * 100
                # impulse threshold: >0.3% return or >0.5% range in 5 min
                if abs(recent_return_5m) > 0.3 or recent_range_5m > 0.5:
                    recent_impulse_detected = True
        # 15m return for downtrend detection (45 frames @ ~20s each)
        if len(cls._history) >= 45:
            prices_15m = [f["spot"] for f in list(cls._history)[-45:]]
            if prices_15m and prices_15m[0] > 0:
                recent_return_15m = (prices_15m[-1] - prices_15m[0]) / prices_15m[0] * 100
                recent_range_15m = (max(prices_15m) - min(prices_15m)) / prices_15m[0] * 100
        # Weak recovery failure: price bounced mid-period but couldn't hold
        if len(cls._history) >= 30:
            prices_30 = [f["spot"] for f in list(cls._history)[-30:]]
            if prices_30 and prices_30[0] > 0:
                mid_high = max(prices_30[:15])
                end_price = prices_30[-1]
                mid_return = (mid_high - prices_30[0]) / prices_30[0] * 100
                end_return = (end_price - mid_high) / max(mid_high, 1) * 100
                recent_recovery_failed = mid_return > 0.2 and end_return < -0.15

        recent_down_sweep = recent_return_15m < -0.3 and recent_range_15m >= 0.5
        range_reclaim = (
            recent_down_sweep
            and recent_return_5m > 0.25
            and (flow_mom > 0 or cluster_score >= 50)
        )
        upside_expansion = (
            recent_return_5m > 0.3
            and expansion_prob >= 50
            and (
                flow_mom > 0
                or cluster_score >= 50
                or iv_velocity >= 2.0
            )
        )
        post_expansion_consolidation = (
            recent_impulse_detected
            and recent_return_5m > -0.1
            and expansion_prob < 45
            and flow_intensity < 25
        )
        range_compression = (
            recent_range_15m > 0
            and recent_range_15m < 0.35
            and flow_intensity < 20
            and expansion_prob < 35
            and void_score < 40
        )
        
        if range_reclaim:
            phase_context = "RANGE_RECLAIM"
        elif recent_down_sweep:
            phase_context = "LIQUIDITY_SWEEP_DOWN"
        elif upside_expansion:
            phase_context = "UPSIDE_EXPANSION"
        elif post_expansion_consolidation:
            phase_context = "POST_EXPANSION_CONSOLIDATION"
        elif range_compression:
            phase_context = "RANGE_COMPRESSION"
        elif cls._current_state == MarketState.PINNING or raw_state == MarketState.PINNING:
            if recent_impulse_detected:
                phase_context = "PINNING_AFTER_IMPULSE"
            elif transition_score >= 35:
                phase_context = "PINNING_WEAKENING"
            else:
                phase_context = "NORMAL_PINNING"
        elif cls._current_state == MarketState.COMPRESSION:
            # Downtrend-specific compression contexts (checked first)
            if flow_mom < -20 and recent_return_15m < -0.3:
                phase_context = "DOWNTREND_COMPRESSION"
            elif flow_intensity >= 20 and flow_mom < 0:
                phase_context = "SELL_PRESSURE_COMPRESSION"
            elif recent_recovery_failed and flow_mom < 0:
                phase_context = "WEAK_RECOVERY_FAILURE"
            elif recent_impulse_detected and flow_mom < 0 and expansion_prob < 40:
                phase_context = "POST_BREAKDOWN_CONSOLIDATION"
            elif recent_impulse_detected:
                phase_context = "COMPRESSION_AFTER_SELL_OFF"
            else:
                phase_context = "NORMAL"
        elif recent_impulse_detected and iv_velocity <= 0.5 and flow_intensity < 25 and expansion_prob < 40:
            phase_context = "POST_IMPULSE_COMPRESSION"
        elif transition_score >= 45:
            phase_context = "TRANSITION_PRESSURE"
        elif cls._current_state == MarketState.TRANSITION:
            if flow_mom < -15 and recent_return_15m < -0.2:
                phase_context = "DOWNTREND_COMPRESSION"
            else:
                phase_context = "TRANSITION_ACTIVE"
        elif flow_intensity < 15 and expansion_prob < 35 and void_score < 40:
            phase_context = "RANGE_STABILIZATION"

        phase_context_debug = {
            "recent_down_sweep": recent_down_sweep,
            "range_reclaim": range_reclaim,
            "range_compression": range_compression,
            "upside_expansion": upside_expansion,
            "post_expansion_consolidation": post_expansion_consolidation,
            "recent_return_5m": round(recent_return_5m, 3),
            "recent_return_15m": round(recent_return_15m, 3),
            "recent_range_15m": round(recent_range_15m, 3),
            "flow_mom": round(flow_mom, 1),
            "flow_intensity": round(flow_intensity, 1),
            "expansion_probability": round(expansion_prob, 1),
            "signal_cluster_score": round(cluster_score, 1),
            "iv_velocity": round(iv_velocity, 2),
        }
        cls._current_phase_context = phase_context
        cls._last_phase_context_debug = phase_context_debug
        
        # ── market_phase_hash (bucket-based) ──
        import hashlib
        flow_mom_raw = flow_mom  # synthetic_flow_pressure
        phase_components = (
            f"{raw_state.value}|{phase_context}|{gamma_slope_state}|{gamma_accel_state}"
            f"|{execution_timing_state}|{bucket_iv_velocity(iv_velocity)}"
            f"|{bucket_flow(flow_mom_raw)}|{bucket_void(void_score)}"
            f"|{bucket_expansion(expansion_prob)}|{hedge_pressure}"
        )
        market_phase_hash = hashlib.sha256(phase_components.encode()).hexdigest()[:8]
        advanced_intelligence["market_phase_hash"] = market_phase_hash
        
        # 3. Debounce State Machine
        cls._update_state_machine(raw_state)
        
        # Store comprehensive state debug
        cls._store_state_debug(
            pinning_score, transition_score, pinning_break_factors,
            pinning_score_delta_5m, transition_score_delta_5m,
            phase_context, distance_to_nearest_wall_pct,
            gamma_slope_state, gamma_accel_state, iv_velocity,
            expansion_prob, void_score, flow_mom, flow_intensity,
            hedge_pressure, g_regime, phase_context_debug,
        )
        
        state_persistence_sec = int(time.time() - cls._last_state_change_ts)

        # 4. Temporal Memory snapshot
        frame = {
            "timestamp": ts,
            "spot": spot,
            "state": cls._current_state.value,
            "net_gex": gamma.get("metrics", {}).get("net_gex", 0),
            "atm_iv": vol.get("metrics", {}).get("atm_iv", 0),
            "skew": skew.get("metrics", {}).get("skew_25d", 0),
            "flow_pressure": flow.get("metrics", {}).get("flow_pressure", 50),
            "confidence": global_confidence
        }
        cls._history.append(frame)
        cls._total_snapshots_processed += 1
        cls._current_snapshot_sequence_id = cls._total_snapshots_processed
        
        # Warmup suppression: suppress crossing events on early snapshots (startup artifacts)
        # Startup can produce extreme values: flow=-100, iv_velocity=200+ on snapshot 2-3
        is_warmup = cls._current_snapshot_sequence_id < cls.WARMUP_MIN_SNAPSHOTS

        # Calculate Transition Speed & Stability based on history
        transition_speed = cls._calculate_transition_speed()
        regime_stability = max(0, 100 - (cls._candidate_updates * 5)) if cls._candidate_state != cls._current_state else 100


        state_machine = {
            "current_state": cls._current_state.value,
            "previous_state": cls._previous_state.value,
            "candidate_state": cls._candidate_state.value,
            "transition_state": "TRANSITION" if cls._candidate_state != cls._current_state else "STABLE",
            "transition_speed": transition_speed,
            "state_persistence_sec": state_persistence_sec,
            "candidate_persistence_updates": cls._candidate_updates,
            "regime_stability": regime_stability,
            "previous_execution_timing_state": cls._prev_execution_timing_state,
        }

        # 5. Evaluate Event Triggers
        # ── Transition events: fire only on actual state transitions ──
        # ── Condition events: fire on threshold crossings with cooldown ──
        
        # BREAKOUT_ALERT (condition)
        if iv_state in ["EXPANSION", "BACKWARDATION"] and breakout_timing.get("features", {}).get("estimated_breakout_window") != "UNKNOWN":
            cls.fire_event("BREAKOUT_ALERT", "HIGH", "Volatility expansion with breakout window detected.", iv_state)
        
        # VOID events — band-crossing detection (condition events)
        def _void_band(score):
            if score >= 75: return "HIGH"
            if score >= 50: return "MEDIUM"
            if score >= 40: return "LOW"
            return "NONE"
        
        current_band = _void_band(void_score)
        previous_band = cls._last_void_band
        
        if not is_warmup:
            # VOID_DETECTED — только при warmup завершён (предотвращает false positives от previous_band="NONE" на старте)
            if previous_band in ("NONE", "LOW") and current_band == "MEDIUM":
                cls.fire_event("VOID_DETECTED", "MEDIUM",
                    f"Liquidity void crossed MEDIUM: {void_score:.1f}",
                    fingerprint=current_band,
                    payload={
                        "liquidity_void_score": round(void_score, 1),
                        "previous_band": previous_band,
                        "current_band": current_band,
                        "reason": "void_crossed_medium_threshold",
                    })
            elif previous_band != "HIGH" and current_band == "HIGH":
                cls.fire_event("VOID_DETECTED", "HIGH",
                    f"Liquidity void crossed HIGH: {void_score:.1f}",
                    fingerprint=current_band,
                    payload={
                        "liquidity_void_score": round(void_score, 1),
                        "previous_band": previous_band,
                        "current_band": current_band,
                        "reason": "void_crossed_high_threshold",
                    })
            
            # VOID_INTENSIFYING — подавляем на warmup (prev_void_score=0 на старте создаёт ложный delta>=20)
            if void_score - cls._prev_void_score >= 20:
                _vi_debug = LiquidityVoidEngine.get_debug()
                cls.fire_event("VOID_INTENSIFYING", "MEDIUM",
                    f"Void intensified: {cls._prev_void_score:.1f} \u2192 {void_score:.1f}",
                    fingerprint="intensify",
                    payload={
                        "previous_liquidity_void_score": round(cls._prev_void_score, 1),
                        "current_liquidity_void_score": round(void_score, 1),
                        "delta": round(void_score - cls._prev_void_score, 1),
                        "threshold": "void_intensifying",
                        "components": _vi_debug.get("components", {}),
                        "gamma_weakness_source": _vi_debug.get("gamma_weakness_source", "none"),
                        "reason": "liquidity_void_score_increased",
                    })
        
        # VOID_CLEARED не требует warmup guard (clearing не может быть startup artifact)
        if not is_warmup and previous_band in ("MEDIUM", "HIGH") and current_band in ("NONE", "LOW"):
            cls.fire_event("VOID_CLEARED", "INFO",
                f"Void cleared: {void_score:.1f}",
                fingerprint="clear",
                payload={
                    "liquidity_void_score": round(void_score, 1),
                    "previous_band": previous_band,
                    "current_band": current_band,
                    "reason": "void_cleared",
                })
            
        # Обновляем state tracking всегда (независимо от suppress_crossing_events)
        cls._prev_void_score = void_score
        cls._last_void_band = current_band
        
        # FLOW_SURGE (crossing-event: only on threshold crossing, not repeating condition)
        # Подавляем на warmup: startup artifact flow=-100 сразу переходит через thresholds
        prev_flow_intensity = cls._prev_flow_intensity
        prev_flow_pressure = cls._prev_flow_pressure
        if flow_mom > 0:
            flow_direction = "BUY"
            flow_reason_prefix = "buy_side"
        elif flow_mom < 0:
            flow_direction = "SELL"
            flow_reason_prefix = "sell_side"
        else:
            flow_direction = "NEUTRAL"
            flow_reason_prefix = "neutral"

        if not is_warmup:
            if prev_flow_intensity < 50 and flow_intensity >= 50:
                cls.fire_event("FLOW_SURGE", "HIGH",
                    f"Flow surge crossing 50: {flow_intensity:.1f}",
                    fingerprint="surge_50",
                    payload={
                        "previous_flow_intensity": round(prev_flow_intensity, 1),
                        "current_flow_intensity": round(flow_intensity, 1),
                        "previous_synthetic_flow_pressure": round(prev_flow_pressure, 1),
                        "current_synthetic_flow_pressure": round(flow_mom, 1),
                        "threshold": 50,
                        "crossing": True,
                        "direction": flow_direction,
                        "reason": f"{flow_reason_prefix}_flow_intensity_crossed_threshold",
                    })
            elif prev_flow_intensity < 35 and flow_intensity >= 35:
                cls.fire_event("FLOW_SURGE", "MEDIUM",
                    f"Flow surge crossing 35: {flow_intensity:.1f}",
                    fingerprint="surge_35",
                    payload={
                        "previous_flow_intensity": round(prev_flow_intensity, 1),
                        "current_flow_intensity": round(flow_intensity, 1),
                        "previous_synthetic_flow_pressure": round(prev_flow_pressure, 1),
                        "current_synthetic_flow_pressure": round(flow_mom, 1),
                        "threshold": 35,
                        "crossing": True,
                        "direction": flow_direction,
                        "reason": f"{flow_reason_prefix}_flow_intensity_crossed_threshold",
                    })
        # State tracking обновляется всегда
        cls._prev_flow_intensity = flow_intensity
        cls._prev_flow_pressure = flow_mom
        
        # GAMMA_COLLAPSE (transition: only on entering collapsing from non-collapsing)
        _gamma_collapse_trigger = (
            (cls._prev_gamma_slope_state != "collapsing" and gamma_slope_state == "collapsing")
            or (cls._prev_gamma_acceleration_state != "collapsing" and gamma_accel_state == "collapsing")
        )
        if _gamma_collapse_trigger:
            _gc_payload = {
                "previous_gamma_slope_state": cls._prev_gamma_slope_state,
                "current_gamma_slope_state": gamma_slope_state,
                "previous_gamma_acceleration_state": cls._prev_gamma_acceleration_state,
                "current_gamma_acceleration_state": gamma_accel_state,
                "gamma_slope": round(gamma_surface.get("metrics", {}).get("gamma_slope", 0), 4),
                "gamma_acceleration": round(gamma_surface.get("metrics", {}).get("gamma_acceleration", 0), 4),
                "reason": "gamma_state_transition_to_collapsing",
            }
            cls.fire_event("GAMMA_COLLAPSE", "CRITICAL",
                f"Gamma collapse: slope={gamma_slope_state}, accel={gamma_accel_state}.",
                fingerprint=f"{cls._prev_gamma_slope_state}->{gamma_slope_state}",
                payload=_gc_payload)

        # GAMMA_WEAKENING (transition: only on state change from neutral/negative to weakening)
        if (
            gamma_slope_state == "weakening"
            and cls._prev_gamma_slope_state not in ("weakening", "collapsing")
        ):
            _gw_payload = {
                "previous_gamma_slope_state": cls._prev_gamma_slope_state,
                "current_gamma_slope_state": gamma_slope_state,
                "previous_gamma_acceleration_state": cls._prev_gamma_acceleration_state,
                "current_gamma_acceleration_state": gamma_accel_state,
                "gamma_slope": round(gamma_surface.get("metrics", {}).get("gamma_slope", 0), 4),
                "gamma_acceleration": round(gamma_surface.get("metrics", {}).get("gamma_acceleration", 0), 4),
                "reason": "gamma_slope_state_transition_to_weakening",
            }
            cls.fire_event("GAMMA_WEAKENING", "MEDIUM",
                f"Gamma structure weakening: {gamma_slope_state}.",
                fingerprint=f"{cls._prev_gamma_slope_state}->{gamma_slope_state}",
                payload=_gw_payload)
        cls._prev_gamma_slope_state = gamma_slope_state
        cls._prev_gamma_acceleration_state = gamma_accel_state
        
        # EXECUTION_WINDOW_OPEN (transition: only on state change into EXECUTION_WINDOW_OPEN)
        prev_exec_state = cls._prev_execution_timing_state
        curr_exec_state = execution_timing_state

        if not is_warmup and curr_exec_state == "EXECUTION_WINDOW_OPEN" and prev_exec_state != "EXECUTION_WINDOW_OPEN":
            _exec_debug = ExecutionTimingEngine.get_debug()
            _ew_scores = _exec_debug.get("scores", {})
            _ew_payload = {
                "previous_execution_timing_state": prev_exec_state,
                "current_execution_timing_state": "EXECUTION_WINDOW_OPEN",
                "structure_score": _ew_scores.get("structure_score", transition_score),
                "expansion_score": _ew_scores.get("expansion_score", 0),
                "hedge_score": _ew_scores.get("hedge_score", 0),
                "window_score": _ew_scores.get("window_score", 0),
                "signal_cluster_score": round(cluster_score, 1),
                "expansion_probability": round(expansion_prob, 1),
                "synthetic_flow_pressure": round(flow_mom, 1),
                "flow_intensity": round(flow_intensity, 1),
                "liquidity_void_score": round(void_score, 1),
                "dealer_hedging_pressure": hedge_pressure,
                "gamma_slope_state": gamma_slope_state,
                "reason": "execution_window_score_crossed_threshold",
            }
            # Unique fingerprint per transition direction allows multiple entries if cooldown expires
            _ew_fp = f"{prev_exec_state}->EXECUTION_WINDOW_OPEN"
            cls.fire_event("EXECUTION_WINDOW_OPEN", "HIGH",
                "Execution window confirmed: all structural signals aligned.",
                fingerprint=_ew_fp,
                payload=_ew_payload)
        
        # STRUCTURE_UNSTABLE (transition: only on state change into STRUCTURE_UNSTABLE)
        if not is_warmup and curr_exec_state == "STRUCTURE_UNSTABLE" and prev_exec_state != "STRUCTURE_UNSTABLE":
            _exec_debug = ExecutionTimingEngine.get_debug()
            _su_scores = _exec_debug.get("scores", {})
            su_payload = {
                "previous_execution_timing_state": prev_exec_state,
                "current_execution_timing_state": "STRUCTURE_UNSTABLE",
                "structure_score": _su_scores.get("structure_score", transition_score),
                "transition_score": transition_score,
                "pinning_score": pinning_score,
                "gamma_slope_state": gamma_slope_state,
                "gamma_acceleration_state": gamma_accel_state,
                "flow_intensity": round(flow_intensity, 1),
                "liquidity_void_score": round(void_score, 1),
                "expansion_probability": round(expansion_prob, 1),
                "reason": "structure_score_threshold_reached_with_structural_guard",
            }
            # Unique fingerprint per transition direction allows multiple entries if cooldown expires
            _su_fp = f"{prev_exec_state}->STRUCTURE_UNSTABLE"
            cls.fire_event("STRUCTURE_UNSTABLE", "MEDIUM",
                "Market structure instability detected.",
                fingerprint=_su_fp,
                payload=su_payload)
        
        cls._prev_execution_timing_state = curr_exec_state
        
        # VOLATILITY_EXPANSION (crossing-event: only on threshold crossing)
        # Подавляем на warmup: iv_velocity=294 на snapshot 2 создаёт ложное событие
        iv_regime = vol.get("signals", {}).get("iv_regime", "NORMAL")
        if not is_warmup:
            if cls._prev_iv_velocity < 2.0 and iv_velocity >= 2.0:
                cls.fire_event("VOLATILITY_EXPANSION", "HIGH",
                    f"IV velocity crossed 2.0: {iv_velocity:.2f}",
                    fingerprint="iv_cross_2",
                    payload={"previous_iv_velocity": round(cls._prev_iv_velocity, 2),
                             "current_iv_velocity": round(iv_velocity, 2),
                             "threshold": 2.0, "crossing": True})
            if cls._prev_expansion_prob < 50 and expansion_prob >= 50:
                cls.fire_event("VOLATILITY_EXPANSION", "HIGH",
                    f"Expansion probability crossed 50: {expansion_prob:.1f}",
                    fingerprint="exp_cross_50",
                    payload={"previous_expansion_probability": round(cls._prev_expansion_prob, 1),
                             "current_expansion_probability": round(expansion_prob, 1),
                             "threshold": 50, "crossing": True})
        # State tracking обновляется всегда
        cls._prev_iv_velocity = iv_velocity
        cls._prev_expansion_prob = expansion_prob

        
        # PINNING_BREAK (transition-event with strict guard)
        # Compute valid break conditions
        normal_break = (
            transition_score >= 45
            and pinning_score < 60
            and pinning_break_factors >= 3
        )
        pressure_break = (
            transition_score >= 60
            and pinning_break_factors >= 4
        )
        delta_break = (
            pinning_weakening_fast
            and transition_pressure_rising
            and pinning_break_factors >= 2
        )
        valid_pinning_break = normal_break or pressure_break or delta_break

        if cls._previous_state == MarketState.PINNING and cls._current_state != MarketState.PINNING:
            if not cls._pinning_break_emitted_for_transition:
                if valid_pinning_break:
                    break_type = (
                        "normal_break" if normal_break
                        else ("pressure_break" if pressure_break else "delta_break")
                    )
                    pb_payload = {
                        "previous_state": cls._previous_state.value,
                        "current_state": cls._current_state.value,
                        "pinning_score": pinning_score,
                        "transition_score": transition_score,
                        "pinning_break_factors": pinning_break_factors,
                        "pinning_score_delta_5m": round(pinning_score_delta_5m, 1),
                        "transition_score_delta_5m": round(transition_score_delta_5m, 1),
                        "normal_break": normal_break,
                        "pressure_break": pressure_break,
                        "delta_break": delta_break,
                        "break_type": break_type,
                    }
                    cls.fire_event(
                        "PINNING_BREAK", "HIGH",
                        (f"Pinning broken → {cls._current_state.value}. "
                         f"break_type={break_type}, "
                         f"pinning_score={pinning_score}, transition_score={transition_score}, "
                         f"break_factors={pinning_break_factors}"),
                        fingerprint=f"{cls._previous_state.value}_{cls._current_state.value}",
                        payload=pb_payload,
                    )
                    cls._pinning_break_emitted_for_transition = True
                # If valid_pinning_break == False, do NOT emit event
        else:
            if cls._current_state == MarketState.PINNING:
                cls._pinning_break_emitted_for_transition = False

        # 6. Fire Events (Debounced)
        # OHLCV-only synthetic fallback
        deribit_status = getattr(dm, 'exchange_meta', {}).get('deribit', {}).get('status', 'MISSING')
        if deribit_status in ("MISSING", "EMPTY", "PARSE_ERROR", "STALE"):
            # Check if spot is near recent range extremes to fire synthetic event
            if len(cls._history) >= 45:
                prices_15m = [f["spot"] for f in list(cls._history)[-45:]]
                high_15m = max(prices_15m)
                low_15m = min(prices_15m)
                if high_15m > low_15m:
                    pos = (spot - low_15m) / (high_15m - low_15m)
                    if pos >= 0.85 or pos <= 0.15:
                        cls.fire_event(
                            "OHLCV_PRICE_ACTION", "LOW",
                            "Price testing recent range extremes (OHLCV fallback).",
                            fingerprint=f"range_{'high' if pos >= 0.85 else 'low'}",
                            payload={
                                "is_synthetic": 1,
                                "source": "ohlcv_only",
                                "confidence": "LOW",
                                "deribit_status": deribit_status,
                                "fallback_reason": "deribit_missing_ohlcv_fallback",
                                "range_position": round(pos, 2),
                            }
                        )

        new_events = cls._generate_events()

        market_state = {
            "timestamp": ts,
            "spot": spot,
            "global_confidence": global_confidence,
            "data_quality": dq,
            "active_sources": active_sources,
            "deribit_status": deribit_status,
            "oi_total": total_oi,
            "volume_total": total_volume,
            "state_machine": state_machine,
            "gamma": gamma,
            "volatility": vol,
            "skew": skew,
            "liquidity": liq,
            "flow": flow,
            "meta": meta,
            "scenario": scenario,
            "execution": execution,
            "narrative": narrative,
            "events": new_events,
            
            # Deribit diagnostics
            "deribit_age_sec": getattr(dm, "exchange_meta", {}).get("deribit", {}).get("deribit_age_sec", 0),
            "deribit_records_used": getattr(dm, "exchange_meta", {}).get("deribit", {}).get("deribit_records_used", 0),
            "deribit_error": getattr(dm, "exchange_meta", {}).get("deribit", {}).get("deribit_error", ""),
            "option_tickers_count": getattr(dm, "exchange_meta", {}).get("deribit", {}).get("option_tickers_count", 0),
            "valid_greeks_count": getattr(dm, "exchange_meta", {}).get("deribit", {}).get("valid_greeks_count", 0),
            "valid_iv_count": getattr(dm, "exchange_meta", {}).get("deribit", {}).get("valid_iv_count", 0),
            "valid_gamma_count": getattr(dm, "exchange_meta", {}).get("deribit", {}).get("valid_gamma_count", 0),
            "calls_count": getattr(dm, "exchange_meta", {}).get("deribit", {}).get("calls_count", 0),
            "puts_count": getattr(dm, "exchange_meta", {}).get("deribit", {}).get("puts_count", 0),
            "expiries_count": getattr(dm, "exchange_meta", {}).get("deribit", {}).get("expiries_count", 0),
            "strikes_count": getattr(dm, "exchange_meta", {}).get("deribit", {}).get("strikes_count", 0),
            "deribit_disabled_reason": getattr(dm, "exchange_meta", {}).get("deribit", {}).get("deribit_disabled_reason", ""),
            
            # Multi-exchange data
            "exchange_health": health_report,
            "exchange_weights": weights,
            "global_metrics": global_metrics,
            "divergences": divergences,
            "leadership": leadership,
            "system_confidence": conf_result,
            
            # Phase 1 Advanced Intelligence
            "advanced_intelligence": advanced_intelligence,
        }

        return market_state

    @classmethod
    def _determine_raw_state(cls, gamma, vol, flow, liq,
                              expansion_prob=0, void_score=0,
                              iv_velocity=0, dealer_hedging_pressure="LOW",
                              pinning_score=0, transition_score=0,
                              pinning_break_factors=0,
                              pinning_weakening_fast=False,
                              transition_pressure_rising=False,
                              range_compression_signal=False,
                              wall_pinning_confirmed=False,
                              compression_score=0) -> MarketState:
        """Determine raw market state from engine outputs using score-based logic.
        
        PINNING requires pinning_score >= 60 (not just POSITIVE_GAMMA).
        TRANSITION requires multi-factor confirmation.
        COMPRESSION exit uses raw structural metrics — NOT execution_timing_state.
        """
        g_regime = gamma.get("signals", {}).get("gamma_regime")
        v_regime = vol.get("signals", {}).get("iv_regime")
        f_bias = flow.get("signals", {}).get("flow_bias")
        
        # If signals are not fully formed, return TRANSITION (never UNKNOWN)
        if not g_regime or not v_regime:
            return MarketState.TRANSITION
        
        # ── EXPANSION / SQUEEZE / PANIC (high-priority, unchanged) ──
        if g_regime == "NEGATIVE_GAMMA" and v_regime in ["EXPANSION", "VOL EXPANSION", "PANIC"]:
            if f_bias == "BULLISH":
                return MarketState.SHORT_SQUEEZE
            elif f_bias == "BEARISH":
                return MarketState.PANIC
            else:
                return MarketState.EXPANSION
        
        # ── COMPRESSION (with exit logic) ──
        if v_regime == "COMPRESSION":
            compression_exit_score = 0
            if expansion_prob >= 55:
                compression_exit_score += 1
            if void_score >= 50:
                compression_exit_score += 1
            if iv_velocity >= 2.0:
                compression_exit_score += 1
            if dealer_hedging_pressure in ("MEDIUM", "HIGH"):
                compression_exit_score += 1
            if compression_exit_score >= 3:
                return MarketState.TRANSITION
            if g_regime == "POSITIVE_GAMMA":
                # POSITIVE_GAMMA alone is not enough to call PINNING.
                # In a tight, low-pressure range without hard wall proximity, keep the
                # structural state as COMPRESSION instead of letting PINNING absorb it.
                if (
                    range_compression_signal
                    and not wall_pinning_confirmed
                    and transition_score < 45
                    and compression_score >= 70
                ):
                    return MarketState.COMPRESSION
            else:
                return MarketState.COMPRESSION

        # ── PINNING vs TRANSITION (score-based) ──
        # Normal break: transition confirmed, pinning weakened
        normal_break = (
            transition_score >= 45
            and pinning_score < 60
            and pinning_break_factors >= 3
        )
        
        # Pressure break: strong transition pressure overrides moderate pinning
        pressure_break = (
            transition_score >= 60
            and pinning_break_factors >= 4
        )
        
        # Delta break: rapid weakening of pinning structure
        delta_break = (
            pinning_weakening_fast
            and transition_pressure_rising
            and pinning_break_factors >= 2
        )
        
        if g_regime == "POSITIVE_GAMMA" and v_regime in ["COMPRESSION", "NORMAL"]:
            if normal_break or pressure_break or delta_break:
                return MarketState.TRANSITION
            if cls._current_state == MarketState.TRANSITION and pinning_score < 65:
                return MarketState.TRANSITION
            if pinning_score >= 60:
                return MarketState.PINNING
            # POSITIVE_GAMMA but pinning not confirmed → TRANSITION
            return MarketState.TRANSITION
        
        # ── Other gamma/vol combinations ──
        if g_regime == "TRANSITION" or v_regime == "TRANSITION":
            return MarketState.TRANSITION
        
        return MarketState.REBALANCE

    @classmethod
    def _update_state_machine(cls, raw_state: MarketState):
        """Update state machine with differentiated persistence.
        
        Uses PERSISTENCE_RULES lookup for state-pair-specific confirmation windows.
        """
        # Treat UNKNOWN as TRANSITION (legacy safety)
        if raw_state == MarketState.UNKNOWN:
            raw_state = MarketState.TRANSITION
            
        if raw_state == cls._current_state:
            cls._candidate_state = raw_state
            cls._candidate_updates = 0
        elif raw_state == cls._candidate_state:
            cls._candidate_updates += 1
            # Differentiated persistence
            pair_key = (cls._current_state.value, raw_state.value)
            required = cls.PERSISTENCE_RULES.get(pair_key, cls.DEFAULT_REQUIRED_PERSISTENCE)
            if cls._candidate_updates >= required:
                _rc_payload = {
                    "previous_state": cls._current_state.value,
                    "current_state": raw_state.value,
                    "candidate_state": raw_state.value,
                    "reason": "state_transition_confirmed",
                }
                # fingerprint per transition pair — не один глобальный cooldown
                _rc_fp = f"{cls._current_state.value}->{raw_state.value}"
                cls.fire_event(
                    "REGIME_CHANGE", "HIGH",
                    f"Market State transitioned from {cls._current_state.value} to {raw_state.value}",
                    fingerprint=_rc_fp,
                    payload=_rc_payload,
                )
                cls._previous_state = cls._current_state
                cls._current_state = raw_state
                cls._candidate_updates = 0
                cls._last_state_change_ts = time.time()
                if raw_state == MarketState.PINNING:
                    cls._pinning_break_emitted_for_transition = False
        else:
            cls._candidate_state = raw_state
            cls._candidate_updates = 1

    @classmethod
    def _store_state_debug(cls, pinning_score, transition_score, pinning_break_factors,
                           pinning_score_delta_5m, transition_score_delta_5m,
                           phase_context, distance_to_nearest_wall_pct,
                           gamma_slope_state, gamma_accel_state, iv_velocity,
                           expansion_prob, void_score, flow_mom, flow_intensity,
                           hedge_pressure, g_regime, phase_context_debug=None):
        """Store comprehensive state debug info."""
        # Build why_not_transition
        why_not = []
        if transition_score < 45:
            why_not.append(f"transition_score_{transition_score}_below_45")
        if pinning_score >= 60:
            why_not.append(f"pinning_score_{pinning_score}_still_above_60")
        if pinning_break_factors < 3:
            why_not.append(f"pinning_break_factors_{pinning_break_factors}_below_3")
        
        # Determine reason
        if cls._current_state == MarketState.PINNING:
            if not why_not:
                reason = "transition_conditions_met_awaiting_persistence"
            else:
                reason = "pinning_confirmed_transition_insufficient"
        else:
            reason = f"{cls._current_state.value.lower()}_active"
        
        # Determine required persistence for current transition
        pair_key = (cls._current_state.value, cls._candidate_state.value if cls._candidate_state else "NONE")
        required = cls.PERSISTENCE_RULES.get(pair_key, cls.DEFAULT_REQUIRED_PERSISTENCE)
        raw_debug = dict(cls._last_raw_state_debug) if cls._last_raw_state_debug else {}
        raw_state = raw_debug.get("raw_state")
        blocked_transition_reason = None
        if cls._current_state != cls._candidate_state:
            blocked_transition_reason = "awaiting_persistence"
        if phase_context == "RANGE_COMPRESSION" and cls._current_state == MarketState.PINNING:
            if raw_state == "PINNING":
                blocked_transition_reason = "range_compression_absorbed_by_pinning_raw_state"
            elif raw_state == "COMPRESSION":
                blocked_transition_reason = "range_compression_waiting_for_compression_persistence"
        compression_score = raw_debug.get("compression_score", 0)
        score_gap = pinning_score - compression_score
        range_compression_detected = bool(
            raw_debug.get("range_compression_signal")
            or (phase_context_debug or {}).get("range_compression")
            or phase_context == "RANGE_COMPRESSION"
        )
        pinning_absorbed_range_compression = (
            blocked_transition_reason == "range_compression_absorbed_by_pinning_raw_state"
        )
        if pinning_absorbed_range_compression:
            pinning_absorption_reason = (
                f"raw_state_PINNING_score_gap_{round(score_gap, 1)}_"
                f"pinning_{pinning_score}_compression_{compression_score}"
            )
        else:
            pinning_absorption_reason = ""
        should_allow_compression_candidate = (
            range_compression_detected
            and compression_score >= 70
            and (
                not raw_debug.get("hard_wall_pinning", False)
                or score_gap <= 20
            )
        )
        should_allow_transition_candidate = (
            transition_score >= 45
            or (expansion_prob >= 50 and flow_intensity >= 20)
            or pinning_break_factors >= 3
        )
        
        cls._last_state_debug = {
            "timestamp": time.time(),
            "current_state": cls._current_state.value,
            "previous_state": cls._previous_state.value,
            "candidate_state": cls._candidate_state.value if cls._candidate_state else "NONE",
            "raw_state": raw_state,
            "pinning_score": pinning_score,
            "compression_score": compression_score,
            "transition_score": transition_score,
            "pinning_absorbed_range_compression": pinning_absorbed_range_compression,
            "pinning_absorption_reason": pinning_absorption_reason,
            "pinning_strength": raw_debug.get("pinning_strength", 0),
            "score_gap": round(score_gap, 1),
            "positive_gamma": raw_debug.get("positive_gamma", g_regime == "POSITIVE_GAMMA"),
            "near_wall": raw_debug.get("near_wall", distance_to_nearest_wall_pct <= 1.0),
            "dealer_positioning": raw_debug.get("dealer_positioning", "UNKNOWN"),
            "dealer_stability_zone": raw_debug.get("dealer_stability_zone", "UNKNOWN"),
            "range_compression_detected": range_compression_detected,
            "range_width_pct": (phase_context_debug or {}).get(
                "recent_range_15m",
                raw_debug.get("raw_recent_range_15m", 0),
            ),
            "ohlcv_return_15m": (phase_context_debug or {}).get(
                "recent_return_15m",
                raw_debug.get("raw_recent_return_15m", 0),
            ),
            "ohlcv_range_15m": (phase_context_debug or {}).get(
                "recent_range_15m",
                raw_debug.get("raw_recent_range_15m", 0),
            ),
            "iv_velocity": round(iv_velocity, 2),
            "flow_intensity": round(flow_intensity, 1),
            "expansion_probability": round(expansion_prob, 1),
            "liquidity_void_score": round(void_score, 1),
            "should_allow_compression_candidate": should_allow_compression_candidate,
            "should_allow_transition_candidate": should_allow_transition_candidate,
            "pinning_score_over_cap": pinning_score > 100,
            "pinning_score_delta_5m": round(pinning_score_delta_5m, 1),
            "transition_score_delta_5m": round(transition_score_delta_5m, 1),
            "pinning_break_factors": pinning_break_factors,
            "candidate_persistence_updates": cls._candidate_updates,
            "required_persistence": required,
            "blocked_transition_reason": blocked_transition_reason,
            "phase_context": phase_context,
            "phase_context_debug": phase_context_debug or {},
            "raw_state_debug": raw_debug,
            "distance_to_nearest_wall_pct": round(distance_to_nearest_wall_pct, 2),
            "state_persistence_sec": round(time.time() - cls._last_state_change_ts, 1),
            "reason": reason,
            "event_skip_debug": dict(cls._last_event_skip_debug) if cls._last_event_skip_debug else {},
            "drivers": {
                "gamma_regime": g_regime,
                "gamma_slope_state": gamma_slope_state,
                "gamma_acceleration_state": gamma_accel_state,
                "iv_velocity": round(iv_velocity, 2),
                "expansion_probability": round(expansion_prob, 1),
                "liquidity_void_score": round(void_score, 1),
                "synthetic_flow_pressure": round(flow_mom, 1),
                "flow_intensity": round(flow_intensity, 1),
                "dealer_hedging_pressure": hedge_pressure,
            },
            "why_not_transition": why_not,
        }
        cls._state_debug_history.append(dict(cls._last_state_debug))

    @classmethod
    def _calculate_transition_speed(cls):
        pair_key = (cls._current_state.value, cls._candidate_state.value if cls._candidate_state else "NONE")
        required = cls.PERSISTENCE_RULES.get(pair_key, cls.DEFAULT_REQUIRED_PERSISTENCE)
        return round(min(1.0, cls._candidate_updates / required), 2)

    @classmethod
    def _generate_events(cls):
        ret = list(cls._events)
        cls._events.clear()
        return ret

    @classmethod
    def get_cluster_debug(cls) -> dict:
        """Return last signal cluster score breakdown for debug endpoint."""
        return dict(cls._last_cluster_breakdown) if cls._last_cluster_breakdown else {}

    @classmethod
    def get_cluster_debug_summary(cls) -> dict:
        """Return in-memory signal cluster aggregate for the current backend process."""
        rows = list(cls._cluster_debug_history)

        def _avg(key):
            vals = [float(row.get(key, 0) or 0) for row in rows]
            return round(sum(vals) / len(vals), 2) if vals else 0

        totals = [float(row.get("total", 0) or 0) for row in rows]
        buckets = {"0_20": 0, "20_40": 0, "40_50": 0, "50_plus": 0}
        for score in totals:
            if score < 20:
                buckets["0_20"] += 1
            elif score < 40:
                buckets["20_40"] += 1
            elif score < 50:
                buckets["40_50"] += 1
            else:
                buckets["50_plus"] += 1

        return {
            "sample_count": len(rows),
            "signal_cluster_score_min": round(min(totals), 2) if totals else 0,
            "signal_cluster_score_avg": _avg("total"),
            "signal_cluster_score_max": round(max(totals), 2) if totals else 0,
            "score_bucket_distribution": buckets,
            "avg_components": {
                "gamma_component": _avg("gamma_component"),
                "iv_velocity_component": _avg("iv_velocity_component"),
                "flow_component": _avg("flow_component"),
                "liquidity_void_component": _avg("liquidity_void_component"),
                "expansion_component": _avg("expansion_component"),
                "hedge_component": _avg("hedge_component"),
                "term_structure_component": _avg("term_structure_component"),
            },
        }

    @classmethod
    def get_state_debug(cls) -> dict:
        """Return last state machine debug info for debug endpoint."""
        return dict(cls._last_state_debug) if cls._last_state_debug else {}

    @classmethod
    def get_state_debug_summary(cls, from_ts=None, to_ts=None) -> dict:
        """Return in-memory state debug aggregate for the current backend process."""
        rows = list(cls._state_debug_history)
        if from_ts is not None:
            rows = [r for r in rows if r.get("timestamp", 0) >= from_ts]
        if to_ts is not None:
            rows = [r for r in rows if r.get("timestamp", 0) <= to_ts]

        def _dist(key):
            out = {}
            for row in rows:
                value = row.get(key, "UNKNOWN")
                out[value] = out.get(value, 0) + 1
            return out

        def _avg(key):
            values = [float(row.get(key, 0) or 0) for row in rows]
            return round(sum(values) / len(values), 2) if values else 0

        blocked = {}
        absorbed_rows = []
        close_compression_rows = []
        over_cap_rows = []
        for row in rows:
            current = row.get("current_state")
            candidate = row.get("candidate_state")
            reason_key = row.get("blocked_transition_reason") or "not_blocked"
            if current != candidate:
                key = f"{current}->{candidate}:{reason_key}"
                blocked[key] = blocked.get(key, 0) + 1
            if row.get("pinning_absorbed_range_compression"):
                absorbed_rows.append(row)
            if row.get("range_compression_detected") and abs(
                float(row.get("pinning_score", 0) or 0)
                - float(row.get("compression_score", 0) or 0)
            ) <= 20:
                close_compression_rows.append(row)
            if row.get("pinning_score_over_cap"):
                over_cap_rows.append(row)

        score_gaps = [float(row.get("score_gap", 0) or 0) for row in absorbed_rows]

        return {
            "sample_count": len(rows),
            "state_distribution": _dist("current_state"),
            "raw_state_distribution": _dist("raw_state"),
            "candidate_state_distribution": _dist("candidate_state"),
            "phase_context_distribution": _dist("phase_context"),
            "avg_pinning_score": _avg("pinning_score"),
            "avg_compression_score": _avg("compression_score"),
            "avg_transition_score": _avg("transition_score"),
            "blocked_transitions": blocked,
            "pinning_absorption_summary": {
                "range_compression_absorbed_by_pinning_count": len(absorbed_rows),
                "avg_score_gap": round(sum(score_gaps) / len(score_gaps), 2) if score_gaps else 0,
                "max_score_gap": round(max(score_gaps), 2) if score_gaps else 0,
                "compression_score_close_to_pinning_count": len(close_compression_rows),
                "pinning_score_over_cap_count": len(over_cap_rows),
            },
        }

