"""Regime Transition Probability Engine — Multi-factor transition forecasting.

Combines structural factors to predict expansion risk:
- IV velocity / pressure
- Gamma slope state / acceleration state
- Flow intensity (directional scale)
- Liquidity void score
- Dealer stability decay
- Term structure state

Uses smoothing (0.65 previous + 0.35 current) to prevent erratic jumps.
"""

from collections import deque
from typing import Dict, Any


class RegimeTransitionEngine:
    # Smoothing state
    _prev_expansion_probability = 10.0
    _prev_compression_failure_risk = 0.0
    _last_debug: dict = {}
    _debug_history = deque(maxlen=1000)

    @classmethod
    def calculate(cls, gamma_state: dict, vol_state: dict, flow_state: dict, 
                  voids_state: dict, hedging_state: dict,
                  gamma_surface: dict = None, term_structure: dict = None,
                  synthetic_orderflow: dict = None) -> Dict[str, Any]:
        result = {
            "status": "ok",
            "metrics": {
                "expansion_probability": 0,
                "compression_failure_risk": 0,
                "regime_stability_decay": 0,
                "transition_velocity": 0
            },
            "warnings": []
        }

        try:
            # ── Gather inputs ─────────────────────────────────────────
            iv_regime = vol_state.get("signals", {}).get("iv_regime", "NORMAL")
            iv_velocity = vol_state.get("metrics", {}).get("iv_velocity", 0)
            event_risk = vol_state.get("metrics", {}).get("event_risk", "Low")

            gamma_regime = gamma_state.get("signals", {}).get("gamma_regime", "LOW_GAMMA")
            
            # Gamma surface states
            gs_metrics = {}
            if gamma_surface:
                gs_metrics = gamma_surface.get("metrics", {})
            gamma_slope_state = gs_metrics.get("gamma_slope_state", "neutral")
            gamma_accel_state = gs_metrics.get("gamma_acceleration_state", "neutral")

            # Flow
            flow_bias = flow_state.get("signals", {}).get("flow_bias", "NEUTRAL")
            flow_pressure = flow_state.get("metrics", {}).get("flow_pressure", 50)

            # Synthetic flow (directional scale)
            flow_intensity = 0
            if synthetic_orderflow:
                flow_mom = synthetic_orderflow.get("metrics", {}).get("flow_momentum_score", 0)
                flow_intensity = abs(flow_mom)

            # Void score
            void_score = voids_state.get("metrics", {}).get("void_score", 0)
            void_count = voids_state.get("metrics", {}).get("total_void_count", 0)

            # Dealer hedging
            hedge_risk = hedging_state.get("metrics", {}).get("hedge_acceleration_risk", "LOW")
            dealer_pressure = hedging_state.get("metrics", {}).get("dealer_hedging_pressure", "LOW")

            # Term structure
            ts_state = "CONTANGO"
            if term_structure:
                ts_state = term_structure.get("features", {}).get("term_structure_state", "CONTANGO")

            # ── Expansion Probability (multi-factor scoring) ──────────
            score = 10  # base
            expansion_contributions = {"base": 10}

            # 1. IV velocity
            if iv_velocity > 5.0:
                expansion_contributions["iv_velocity"] = 25
            elif iv_velocity > 2.0:
                expansion_contributions["iv_velocity"] = 18
            elif iv_velocity > 0.5:
                expansion_contributions["iv_velocity"] = 8
            elif iv_velocity < -2.0:
                expansion_contributions["iv_velocity"] = -5
            else:
                expansion_contributions["iv_velocity"] = 0
            score += expansion_contributions["iv_velocity"]

            # 2. Liquidity void score
            if void_score > 70:
                expansion_contributions["liquidity_void"] = 20
            elif void_score > 60:
                expansion_contributions["liquidity_void"] = 15
            elif void_score > 50:
                expansion_contributions["liquidity_void"] = 8
            else:
                expansion_contributions["liquidity_void"] = 0
            score += expansion_contributions["liquidity_void"]

            # 3. Gamma slope state
            if gamma_slope_state == "collapsing":
                expansion_contributions["gamma_slope_state"] = 20
            elif gamma_slope_state in ("weakening", "negative"):
                expansion_contributions["gamma_slope_state"] = 10
            else:
                expansion_contributions["gamma_slope_state"] = 0
            score += expansion_contributions["gamma_slope_state"]

            # 4. Gamma acceleration state
            if gamma_accel_state in ("accelerating", "collapsing"):
                expansion_contributions["gamma_acceleration_state"] = 6
            else:
                expansion_contributions["gamma_acceleration_state"] = 0
            score += expansion_contributions["gamma_acceleration_state"]

            # 5. Flow intensity (directional scale)
            if flow_intensity > 50:
                expansion_contributions["flow_intensity"] = 15
            elif flow_intensity > 35:
                expansion_contributions["flow_intensity"] = 10
            elif flow_intensity > 20:
                expansion_contributions["flow_intensity"] = 5
            else:
                expansion_contributions["flow_intensity"] = 0
            score += expansion_contributions["flow_intensity"]

            # Also consider flow_pressure from FlowEngine (0-100, 50=neutral)
            flow_imbalance = abs(flow_pressure - 50)
            if flow_imbalance > 25:
                expansion_contributions["flow_pressure_imbalance"] = 5
            else:
                expansion_contributions["flow_pressure_imbalance"] = 0
            score += expansion_contributions["flow_pressure_imbalance"]

            # 6. Term structure state
            if ts_state in ("BACKWARDATION", "INVERSION", "FRONT_STRESS"):
                expansion_contributions["term_structure"] = 15
            elif ts_state in ("FLAT", "MIXED"):
                expansion_contributions["term_structure"] = 5
            else:
                expansion_contributions["term_structure"] = 0
            score += expansion_contributions["term_structure"]

            # 7. Dealer hedging pressure
            if dealer_pressure == "HIGH":
                expansion_contributions["dealer_hedging_pressure"] = 10
            elif dealer_pressure == "MEDIUM":
                expansion_contributions["dealer_hedging_pressure"] = 5
            else:
                expansion_contributions["dealer_hedging_pressure"] = 0
            score += expansion_contributions["dealer_hedging_pressure"]

            # 8. Gamma regime
            if gamma_regime == "NEGATIVE_GAMMA":
                expansion_contributions["gamma_regime"] = 10
            elif gamma_regime == "LOW_GAMMA":
                expansion_contributions["gamma_regime"] = 5
            else:
                expansion_contributions["gamma_regime"] = 0
            score += expansion_contributions["gamma_regime"]

            # Clamp
            raw_expansion = max(0, min(100, score))

            # Smoothing: 0.65 previous + 0.35 current
            prev_expansion_probability = cls._prev_expansion_probability
            expansion_prob = prev_expansion_probability * 0.65 + raw_expansion * 0.35
            expansion_prob = max(0, min(100, expansion_prob))
            cls._prev_expansion_probability = expansion_prob

            result["metrics"]["expansion_probability"] = round(expansion_prob, 1)
            
            # ── Compression failure risk ──────────────────────────────
            if iv_regime == "COMPRESSION":
                gamma_score = 0
                if gamma_slope_state in ("weakening", "collapsing", "negative"):
                    gamma_score += 30
                if gamma_accel_state in ("accelerating", "collapsing"):
                    gamma_score += 15
                void_contrib = min(25, void_score * 0.35)
                hedge_contrib = {"HIGH": 20, "MEDIUM": 10, "LOW": 0}.get(dealer_pressure, 0)
                
                raw_failure = min(100, gamma_score + void_contrib + hedge_contrib)
                failure_risk = cls._prev_compression_failure_risk * 0.65 + raw_failure * 0.35
                cls._prev_compression_failure_risk = failure_risk
            else:
                failure_risk = cls._prev_compression_failure_risk * 0.8  # decay
                cls._prev_compression_failure_risk = failure_risk

            result["metrics"]["compression_failure_risk"] = round(failure_risk, 1)
            
            # ── Stability decay ───────────────────────────────────────
            stability_score = 0
            if gamma_slope_state in ("weakening", "collapsing", "negative"):
                stability_score += 25
            if dealer_pressure in ("MEDIUM", "HIGH"):
                stability_score += 20
            if void_score > 50:
                stability_score += min(25, (void_score - 50))
            if flow_intensity > 30:
                stability_score += 15
            
            decay = min(100, stability_score)
            result["metrics"]["regime_stability_decay"] = round(decay, 1)

            # ── Transition velocity ───────────────────────────────────
            velocity_score = 0
            if void_score > 50:
                velocity_score += 20
            if flow_intensity > 30:
                velocity_score += 20
            if iv_velocity > 2.0:
                velocity_score += 25
            if gamma_slope_state in ("weakening", "collapsing"):
                velocity_score += 15

            velocity = min(100, velocity_score)
            result["metrics"]["transition_velocity"] = round(velocity, 1)

            debug = {
                "expansion_probability": round(expansion_prob, 1),
                "raw_expansion_probability": round(raw_expansion, 1),
                "previous_expansion_probability": round(prev_expansion_probability, 1),
                "smoothing": {"previous_weight": 0.65, "current_weight": 0.35},
                "contributions": expansion_contributions,
                "inputs": {
                    "iv_regime": iv_regime,
                    "iv_velocity": round(iv_velocity, 2),
                    "gamma_regime": gamma_regime,
                    "gamma_slope_state": gamma_slope_state,
                    "gamma_acceleration_state": gamma_accel_state,
                    "flow_pressure": round(flow_pressure, 1),
                    "flow_intensity": round(flow_intensity, 1),
                    "liquidity_void_score": round(void_score, 1),
                    "void_count": void_count,
                    "dealer_hedging_pressure": dealer_pressure,
                    "hedge_acceleration_risk": hedge_risk,
                    "term_structure_state": ts_state,
                },
                "why_weak": [
                    key for key, value in expansion_contributions.items()
                    if key != "base" and value == 0
                ],
            }
            cls._last_debug = debug
            cls._debug_history.append(dict(debug))

        except Exception as e:
            result["status"] = "error"
            result["warnings"].append(f"Regime Transition calculation failed: {str(e)}")

        return result

    @classmethod
    def get_debug(cls) -> dict:
        """Return latest expansion probability debug breakdown."""
        return dict(cls._last_debug) if cls._last_debug else {}

    @classmethod
    def get_debug_summary(cls) -> dict:
        """Return in-memory expansion probability debug summary for the current process."""
        rows = list(cls._debug_history)

        def _avg(key):
            vals = [float(r.get(key, 0) or 0) for r in rows]
            return round(sum(vals) / len(vals), 2) if vals else 0

        contribution_totals = {}
        zero_counts = {}
        for row in rows:
            for key, value in row.get("contributions", {}).items():
                contribution_totals[key] = contribution_totals.get(key, 0) + float(value or 0)
                if key != "base" and float(value or 0) == 0:
                    zero_counts[key] = zero_counts.get(key, 0) + 1

        return {
            "sample_count": len(rows),
            "avg_expansion_probability": _avg("expansion_probability"),
            "avg_raw_expansion_probability": _avg("raw_expansion_probability"),
            "avg_contributions": {
                key: round(total / len(rows), 2) if rows else 0
                for key, total in contribution_totals.items()
            },
            "zero_contribution_counts": zero_counts,
        }
