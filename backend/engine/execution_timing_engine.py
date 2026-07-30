"""Execution Timing Layer — Scoring-based structural vulnerability assessment.

Outputs definitive execution readiness states:
- WAIT
- STRUCTURE_UNSTABLE
- EXPANSION_CONFIRMING
- HEDGE_CHASE_STARTING
- EXECUTION_WINDOW_OPEN

Each state is determined by a multi-factor scoring model, NOT boolean conditions.
Uses a persistence window (2 snapshots) to prevent rapid state flipping.

EXECUTION_WINDOW_OPEN requires additional execution confirmation beyond high scores.

This engine is READ-ONLY. It does NOT produce BUY/SELL signals.
It only assesses execution environment quality.
"""

from typing import Dict, Any

VALID_EXECUTION_STATES = {
    "WAIT",
    "STRUCTURE_UNSTABLE",
    "EXPANSION_CONFIRMING",
    "HEDGE_CHASE_STARTING",
    "EXECUTION_WINDOW_OPEN",
}

_STATE_ALIASES = {
    "EXECUTION_WINDOW": "EXECUTION_WINDOW_OPEN",
    "WINDOW_OPEN": "EXECUTION_WINDOW_OPEN",
    "EXECUTION_OPEN": "EXECUTION_WINDOW_OPEN",
    "STRUCTURE_UNSTABLE_": "STRUCTURE_UNSTABLE",
}


def normalize_execution_state(state: str) -> str:
    """Normalize execution state to canonical format."""
    if not state:
        return "WAIT"
    s = str(state).strip().upper().replace(" ", "_").replace("-", "_")
    s = _STATE_ALIASES.get(s, s)
    if s not in VALID_EXECUTION_STATES:
        return "WAIT"
    return s


class ExecutionTimingEngine:
    # Persistence window: new state must hold for N snapshots before transition
    _current_execution_state = "WAIT"
    _pending_execution_state = None
    _pending_execution_count = 0
    MIN_CONFIRMATION_SNAPSHOTS = 3

    # Track previous state for transition events
    _previous_execution_state = "WAIT"

    # Store last debug breakdown for debug endpoint
    _last_debug = {}

    @classmethod
    def calculate(cls, phase1: dict, term_structure: dict, orderflow: dict,
                  breakout: dict, void_score: float = 0,
                  signal_cluster_score: float = 0, iv_velocity: float = 0,
                  data_quality: str = "GOOD", active_sources: list = None,
                  transition_score: int = 0) -> Dict[str, Any]:
        result = {
            "status": "ok",
            "features": {
                "execution_state": "WAIT",
                "execution_context": "",
                "raw_state": "WAIT",
                "pending_state": None,
                "pending_count": 0,
            },
            "warnings": []
        }

        try:
            # ── Safety guard ──────────────────────────────────────────
            if data_quality == "CRITICAL" or not active_sources:
                cls._update_persistence("WAIT")
                confirmed = normalize_execution_state(cls._current_execution_state)
                result["features"]["execution_state"] = confirmed
                result["features"]["execution_context"] = "safety_guard_active"
                cls._store_debug(
                    result, {}, {}, {}, {}, 0, False, "safety_guard",
                    failed_guards={"ALL": "data_quality_guard"},
                    selected_state_reason={
                        "raw_state": "WAIT",
                        "confirmed_state": confirmed,
                        "category": "data_quality_guard",
                        "reason": "critical_data_quality_or_no_active_sources",
                    },
                )
                return result

            # ── Gather inputs ─────────────────────────────────────────
            rt_metrics = phase1.get("regime_transition", {}).get("metrics", {})
            expansion_prob = rt_metrics.get("expansion_probability", 0)

            dh_metrics = phase1.get("dealer_hedging", {}).get("metrics", {})
            hedge_risk = dh_metrics.get("hedge_acceleration_risk", "LOW")
            dealer_hedging_pressure = dh_metrics.get("dealer_hedging_pressure", "LOW")

            gs_metrics = phase1.get("gamma_surface", {}).get("metrics", {})
            gamma_slope_state = gs_metrics.get("gamma_slope_state", "neutral")
            gamma_accel_state = gs_metrics.get("gamma_acceleration_state", "neutral")

            ts_features = term_structure.get("features", {})
            ts_state = ts_features.get("term_structure_state", "FLAT")

            # Directional flow scale: -100 to +100, 0 = neutral
            of_momentum = orderflow.get("metrics", {}).get("flow_momentum_score", 0)
            flow_intensity = abs(of_momentum)

            # ── STRUCTURE_UNSTABLE scoring ─────────────────────────────
            structure_score = 0

            if void_score >= 55:
                structure_score += 25
            elif void_score >= 45:
                structure_score += 15

            if flow_intensity >= 35:
                structure_score += 25
            elif flow_intensity >= 20:
                structure_score += 15

            if iv_velocity >= 2.0:
                structure_score += 20
            elif iv_velocity >= 0.5:
                structure_score += 10

            if gamma_slope_state in ("weakening", "collapsing", "negative"):
                structure_score += 15

            if gamma_accel_state in ("accelerating", "collapsing"):
                structure_score += 10

            # Transition pressure contribution (structural metric from StateEngine)
            if transition_score >= 45:
                structure_score += 15
            elif transition_score >= 35:
                structure_score += 8

            # Expansion probability contribution
            if expansion_prob >= 35:
                structure_score += 10

            # Guard: STRUCTURE_UNSTABLE only with structural confirmation
            structure_unstable_allowed = (
                transition_score >= 35
                or gamma_slope_state in ("weakening", "collapsing")
                or gamma_accel_state in ("accelerating", "collapsing")
            )

            # ── EXPANSION_CONFIRMING scoring ───────────────────────────
            expansion_score = 0

            if expansion_prob >= 60:
                expansion_score += 30
            elif expansion_prob >= 50:
                expansion_score += 22
            elif expansion_prob >= 40:
                expansion_score += 14

            if iv_velocity >= 2.0:
                expansion_score += 20
            elif iv_velocity >= 0.5:
                expansion_score += 10

            if flow_intensity >= 35:
                expansion_score += 20
            elif flow_intensity >= 20:
                expansion_score += 10

            if void_score >= 60:
                expansion_score += 15
            elif void_score >= 50:
                expansion_score += 8

            if gamma_slope_state in ("weakening", "collapsing"):
                expansion_score += 12

            # ── HEDGE_CHASE_STARTING scoring ───────────────────────────
            hedge_score = 0

            if dealer_hedging_pressure == "HIGH":
                hedge_score += 35
            elif dealer_hedging_pressure == "MEDIUM":
                hedge_score += 20

            if gamma_accel_state in ("accelerating", "collapsing"):
                hedge_score += 15

            if gamma_slope_state in ("weakening", "collapsing", "negative"):
                hedge_score += 15

            if flow_intensity >= 35:
                hedge_score += 15

            if iv_velocity >= 2.0:
                hedge_score += 10

            # ── EXECUTION_WINDOW_OPEN scoring ──────────────────────────
            window_score = 0

            if signal_cluster_score >= 70:
                window_score += 25
            elif signal_cluster_score >= 60:
                window_score += 18

            if expansion_prob >= 60:
                window_score += 20
            elif expansion_prob >= 50:
                window_score += 12

            if data_quality == "GOOD":
                window_score += 10
            elif data_quality == "DEGRADED":
                window_score += 5

            if active_sources:
                window_score += 5

            if flow_intensity >= 35:
                window_score += 10
            elif flow_intensity >= 25:
                window_score += 6

            if void_score >= 60:
                window_score += 10
            elif void_score >= 55:
                window_score += 5

            if iv_velocity >= 1.0:
                window_score += 10

            if dealer_hedging_pressure == "HIGH":
                window_score += 15
            elif dealer_hedging_pressure == "MEDIUM":
                window_score += 8
            else:
                window_score -= 10

            if ts_state in ("BACKWARDATION", "INVERSION", "FRONT_STRESS"):
                window_score += 8

            # ── Execution confirmation gate ────────────────────────────
            has_execution_confirmation = (
                dealer_hedging_pressure in ("MEDIUM", "HIGH")
                or iv_velocity >= 2.0
                or void_score >= 60
                or flow_intensity >= 50
                or ts_state in ("BACKWARDATION", "INVERSION", "FRONT_STRESS")
            )

            # ── Priority-based state selection ─────────────────────────
            thresholds = {
                "STRUCTURE_UNSTABLE": {"score": "structure_score", "threshold": 30},
                "EXPANSION_CONFIRMING": {"score": "expansion_score", "threshold": 45},
                "HEDGE_CHASE_STARTING": {"score": "hedge_score", "threshold": 55},
                "EXECUTION_WINDOW_OPEN": {"score": "window_score", "threshold": 70},
            }
            passed_scores = {
                "STRUCTURE_UNSTABLE": structure_score >= 30,
                "EXPANSION_CONFIRMING": expansion_score >= 45,
                "HEDGE_CHASE_STARTING": hedge_score >= 55,
                "EXECUTION_WINDOW_OPEN": window_score >= 70,
            }
            failed_guards = {}
            if passed_scores["STRUCTURE_UNSTABLE"] and not structure_unstable_allowed:
                failed_guards["STRUCTURE_UNSTABLE"] = "structure_unstable_guard_failed"
            if passed_scores["EXECUTION_WINDOW_OPEN"] and not has_execution_confirmation:
                failed_guards["EXECUTION_WINDOW_OPEN"] = "execution_confirmation_missing"

            raw_state = "WAIT"

            # STRUCTURE_UNSTABLE: lowered threshold (30) with mandatory guard
            if structure_score >= 30 and structure_unstable_allowed:
                raw_state = "STRUCTURE_UNSTABLE"

            if expansion_score >= 45:
                raw_state = "EXPANSION_CONFIRMING"

            if hedge_score >= 55:
                raw_state = "HEDGE_CHASE_STARTING"

            if window_score >= 70 and has_execution_confirmation:
                raw_state = "EXECUTION_WINDOW_OPEN"
            elif window_score >= 70 and not has_execution_confirmation:
                # Fallback: high window score but no confirmation → EXPANSION_CONFIRMING
                if expansion_score >= 50 or raw_state == "WAIT":
                    raw_state = "EXPANSION_CONFIRMING"

            raw_state = normalize_execution_state(raw_state)

            # ── Apply persistence window ───────────────────────────────
            cls._update_persistence(raw_state)
            confirmed_state = normalize_execution_state(cls._current_execution_state)

            # ── Result ─────────────────────────────────────────────────
            scores = {
                "structure_score": structure_score,
                "expansion_score": expansion_score,
                "hedge_score": hedge_score,
                "window_score": window_score,
            }

            result["features"]["execution_state"] = confirmed_state
            result["features"]["raw_state"] = raw_state
            result["features"]["pending_state"] = cls._pending_execution_state
            result["features"]["pending_count"] = cls._pending_execution_count

            reason_parts = []
            if confirmed_state == "WAIT":
                reason_parts.append("no_state_threshold_met")
            elif confirmed_state == "EXPANSION_CONFIRMING" and window_score >= 70:
                reason_parts.append("expansion_confirming_but_execution_confirmation_missing")
            else:
                reason_parts.append(f"{confirmed_state.lower()}_active")

            result["features"]["execution_context"] = ";".join(reason_parts)

            # ── Build why_not explanation ────────────────────────────
            score_values = {
                "STRUCTURE_UNSTABLE": structure_score,
                "EXPANSION_CONFIRMING": expansion_score,
                "HEDGE_CHASE_STARTING": hedge_score,
                "EXECUTION_WINDOW_OPEN": window_score,
            }
            score_names = {
                "STRUCTURE_UNSTABLE": "structure_score",
                "EXPANSION_CONFIRMING": "expansion_score",
                "HEDGE_CHASE_STARTING": "hedge_score",
                "EXECUTION_WINDOW_OPEN": "window_score",
            }
            priority_order = [
                "STRUCTURE_UNSTABLE",
                "EXPANSION_CONFIRMING",
                "HEDGE_CHASE_STARTING",
                "EXECUTION_WINDOW_OPEN",
            ]
            raw_priority = priority_order.index(raw_state) if raw_state in priority_order else -1
            blocked_state_reasons = {}
            why_not = {}
            for state_name in priority_order:
                score_value = score_values[state_name]
                threshold = thresholds[state_name]["threshold"]
                if score_value < threshold:
                    reason = {
                        "category": "score_below_threshold",
                        "reason": f"{score_names[state_name]}_{score_value}_below_{threshold}",
                        "score": score_value,
                        "threshold": threshold,
                    }
                elif state_name in failed_guards:
                    reason = {
                        "category": "score_passed_but_guard_failed",
                        "reason": failed_guards[state_name],
                        "score": score_value,
                        "threshold": threshold,
                    }
                elif raw_state == state_name and confirmed_state != state_name:
                    reason = {
                        "category": "score_passed_but_persistence_failed",
                        "reason": (
                            f"raw_state_{state_name}_awaiting_persistence_"
                            f"{cls._pending_execution_count}_of_{cls.MIN_CONFIRMATION_SNAPSHOTS}"
                        ),
                        "score": score_value,
                        "threshold": threshold,
                    }
                elif state_name != raw_state and raw_priority >= 0 and priority_order.index(state_name) < raw_priority:
                    reason = {
                        "category": "score_passed_but_priority_overridden",
                        "reason": f"overridden_by_higher_priority_raw_state_{raw_state}",
                        "score": score_value,
                        "threshold": threshold,
                    }
                else:
                    reason = {
                        "category": "passed_or_active",
                        "reason": f"{score_names[state_name]}_{score_value}_passed_{threshold}",
                        "score": score_value,
                        "threshold": threshold,
                    }
                blocked_state_reasons[state_name] = reason
                if confirmed_state != state_name:
                    why_not[state_name] = reason["reason"]

            selected_state_reason = {
                "raw_state": raw_state,
                "confirmed_state": confirmed_state,
                "category": "score_passed_but_persistence_failed" if raw_state != confirmed_state else "selected",
                "reason": (
                    f"raw_state_{raw_state}_awaiting_persistence"
                    if raw_state != confirmed_state
                    else result["features"].get("execution_context", "")
                ),
                "pending_state": cls._pending_execution_state,
                "pending_count": cls._pending_execution_count,
                "required_persistence": cls.MIN_CONFIRMATION_SNAPSHOTS,
            }

            cls._store_debug(result, scores,
                             {"gamma_slope_state": gamma_slope_state,
                              "gamma_accel_state": gamma_accel_state,
                              "iv_velocity": iv_velocity,
                              "void_score": void_score,
                              "expansion_prob": expansion_prob,
                              "signal_cluster_score": signal_cluster_score,
                              "dealer_hedging_pressure": dealer_hedging_pressure,
                              "ts_state": ts_state,
                              "flow_momentum": of_momentum,
                              "flow_intensity": flow_intensity,
                              "data_quality": data_quality,
                              "transition_score": transition_score,
                              "structure_unstable_allowed": structure_unstable_allowed},
                             scores, scores,
                             window_score, has_execution_confirmation, raw_state,
                             why_not=why_not,
                             thresholds=thresholds,
                             passed_scores=passed_scores,
                             failed_guards=failed_guards,
                             selected_state_reason=selected_state_reason,
                             blocked_state_reasons=blocked_state_reasons)

        except Exception as e:
            result["status"] = "error"
            result["warnings"].append(f"Execution Timing calculation failed: {str(e)}")

        return result

    @classmethod
    def _update_persistence(cls, raw_state: str):
        """Apply persistence window to prevent state flipping."""
        if raw_state != cls._current_execution_state:
            if cls._pending_execution_state == raw_state:
                cls._pending_execution_count += 1
            else:
                cls._pending_execution_state = raw_state
                cls._pending_execution_count = 1

            if cls._pending_execution_count >= cls.MIN_CONFIRMATION_SNAPSHOTS:
                cls._previous_execution_state = cls._current_execution_state
                cls._current_execution_state = raw_state
                cls._pending_execution_state = None
                cls._pending_execution_count = 0
        else:
            cls._pending_execution_state = None
            cls._pending_execution_count = 0

    @classmethod
    def _store_debug(cls, result, scores, inputs, structure_detail, 
                     expansion_detail, window_score, has_confirmation, raw_state,
                     why_not=None, thresholds=None, passed_scores=None,
                     failed_guards=None, selected_state_reason=None,
                     blocked_state_reasons=None):
        """Store debug breakdown for debug endpoint."""
        cls._last_debug = {
            "execution_timing_state": result["features"]["execution_state"],
            "raw_state": raw_state,
            "previous_state": cls._previous_execution_state,
            "flow_scale": "-100_to_100_neutral_0",
            "scores": scores if isinstance(scores, dict) else {},
            "inputs": inputs if isinstance(inputs, dict) else {},
            "has_execution_confirmation": has_confirmation,
            "reason": result["features"].get("execution_context", ""),
            "why_not": why_not if why_not else {},
            "thresholds": thresholds if isinstance(thresholds, dict) else {},
            "passed_scores": passed_scores if isinstance(passed_scores, dict) else {},
            "failed_guards": failed_guards if isinstance(failed_guards, dict) else {},
            "cooldowns_active": {},
            "selected_state_reason": selected_state_reason if isinstance(selected_state_reason, dict) else {},
            "blocked_state_reasons": blocked_state_reasons if isinstance(blocked_state_reasons, dict) else {},
        }

    @classmethod
    def get_debug(cls) -> dict:
        """Return last debug breakdown for debug endpoint."""
        return dict(cls._last_debug) if cls._last_debug else {}

