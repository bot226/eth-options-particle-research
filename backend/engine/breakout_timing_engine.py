"""Time-To-Breakout Engine — Estimates timing windows for expansion.

Analyzes compression persistence, IV acceleration, and structural weakening
to estimate WHEN an expansion might occur.
"""

from typing import Dict, Any

class BreakoutTimingEngine:
    @staticmethod
    def calculate(vol_state: dict, rt_state: dict, orderflow_state: dict) -> Dict[str, Any]:
        result = {
            "status": "ok",
            "metrics": {
                "expansion_readiness_score": 0,
            },
            "features": {
                "estimated_breakout_window": "STABLE",
                "compression_persistence_state": "NONE"
            },
            "warnings": []
        }

        try:
            # 1. Gather baseline factors
            iv_regime = vol_state.get("signals", {}).get("iv_regime", "NORMAL")
            iv_velocity = vol_state.get("metrics", {}).get("iv_velocity", 0)
            
            # From Phase 1 Regime Transition
            expansion_prob = rt_state.get("metrics", {}).get("expansion_probability", 0)
            failure_risk = rt_state.get("metrics", {}).get("compression_failure_risk", 0)
            
            # From Phase 2 Orderflow
            flow_momentum = orderflow_state.get("metrics", {}).get("flow_momentum_score", 0)
            
            # 2. Evaluate Compression Persistence
            if iv_regime == "COMPRESSION":
                # If failure risk is high, compression has been persistent but is breaking
                if failure_risk > 60:
                    persistence = "DETERIORATING"
                elif failure_risk > 30:
                    persistence = "MATURE"
                else:
                    persistence = "STABLE"
            elif iv_regime in ["EXPANSION", "VOL EXPANSION", "PANIC"]:
                persistence = "BROKEN"
            else:
                persistence = "NONE"
                
            result["features"]["compression_persistence_state"] = persistence

            # 3. Calculate Readiness Score
            # Base readiness is the expansion probability
            readiness = expansion_prob
            
            # Add velocity and flow multipliers
            if iv_velocity > 0:
                readiness += min(20, iv_velocity * 2)
            
            if abs(flow_momentum) > 20:
                readiness += 15
                
            readiness = min(100, max(0, readiness))
            result["metrics"]["expansion_readiness_score"] = round(readiness, 1)

            # 4. Estimate Breakout Window
            window = "STABLE"
            if persistence == "BROKEN" or iv_regime == "EXPANSION":
                window = "EXPANSION_IN_PROGRESS"
            elif readiness > 85:
                window = "IMMINENT_WITHIN_HOURS"
            elif readiness > 65:
                window = "ELEVATED_WITHIN_2_TO_6_HOURS"
            elif readiness > 40:
                window = "DEVELOPING_WITHIN_24_HOURS"
            elif iv_regime == "COMPRESSION":
                window = "COMPRESSION_STABLE"
                
            result["features"]["estimated_breakout_window"] = window

        except Exception as e:
            result["status"] = "error"
            result["warnings"].append(f"Breakout Timing calculation failed: {str(e)}")

        return result
