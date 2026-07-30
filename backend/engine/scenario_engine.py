"""Scenario Engine for probability and risk calculations."""

from typing import Dict, Any

class ScenarioEngine:
    @staticmethod
    def calculate(gamma_state: dict, vol_state: dict, liq_state: dict, flow_state: dict, spot: float) -> Dict[str, Any]:
        result = {
            "status": "ok",
            "metrics": {
                "squeeze_risk_score": 0,
                "breakout_risk_score": 0,
                "trap_probability": 0
            },
            "signals": {
                "squeeze_risk": "LOW",
                "breakout_risk": "LOW",
                "current_scenario": "NEUTRAL_RANGE",
                "scenario_probability": 0
            },
            "confidence": 100,
            "warnings": []
        }

        try:
            # Squeeze Risk Heuristics
            # If Negative Gamma + High Flow Pressure + Thin Liquidity -> High Squeeze Risk
            g_regime = gamma_state.get("signals", {}).get("gamma_regime", "LOW_GAMMA")
            f_bias = flow_state.get("signals", {}).get("flow_bias", "NEUTRAL")
            f_pressure = flow_state.get("metrics", {}).get("flow_pressure", 50)
            
            squeeze_score = 0
            if g_regime == "NEGATIVE_GAMMA":
                squeeze_score += 40
            if f_bias in ["BULLISH", "BEARISH"] and f_pressure > 65:
                squeeze_score += 40
                
            result["metrics"]["squeeze_risk_score"] = squeeze_score
            
            if squeeze_score > 70:
                result["signals"]["squeeze_risk"] = "HIGH"
                result["signals"]["current_scenario"] = "SQUEEZE_RISK_ELEVATED"
                result["signals"]["scenario_probability"] = 80
            elif squeeze_score > 30:
                result["signals"]["squeeze_risk"] = "MEDIUM"
                result["signals"]["current_scenario"] = "VOLATILE_RANGE"
                result["signals"]["scenario_probability"] = 60
            else:
                result["signals"]["current_scenario"] = "PINNED_TO_STRIKE"
                result["signals"]["scenario_probability"] = 75

        except Exception as e:
            result["status"] = "error"
            result["warnings"].append(f"Scenario calculation failed: {str(e)}")
            result["confidence"] = 0

        return result
