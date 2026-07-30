"""Skew Engine for institutional options analysis."""

from typing import Dict, Any

class SkewEngine:
    @staticmethod
    def calculate(chain: dict, spot: float, dm=None) -> Dict[str, Any]:
        result = {
            "status": "ok",
            "metrics": {
                "skew_25d": 0,
                "call_25d_iv": 0,
                "put_25d_iv": 0,
                "skew_momentum": 0
            },
            "signals": {
                "skew_regime": "BALANCED",
                "skew_dominance": "NONE"
            },
            "confidence": 100,
            "warnings": []
        }

        if not chain or spot <= 0:
            result["status"] = "degraded"
            result["warnings"].append("Missing chain or spot data")
            result["confidence"] = 0
            return result

        try:
            from engine.calculator import Calculator
            
            nearest = None
            if dm and hasattr(dm, 'get_expiry_nearest'):
                nearest = dm.get_expiry_nearest()
                
            if nearest:
                skew_data = Calculator.get_25d_skew(chain.get(nearest, {}))
                skew_val = skew_data.get("skew", 0)
                result["metrics"]["skew_25d"] = skew_val
                result["metrics"]["call_25d_iv"] = skew_data.get("call_25d_iv", 0) * 100
                result["metrics"]["put_25d_iv"] = skew_data.get("put_25d_iv", 0) * 100
                
                # Thresholds
                SKEW_DOMINANCE_THRESHOLD = 3.0 # e.g. 3% difference
                
                if skew_val > SKEW_DOMINANCE_THRESHOLD:
                    result["signals"]["skew_regime"] = "CALL_DOMINANCE"
                    result["signals"]["skew_dominance"] = "CALLS"
                elif skew_val < -SKEW_DOMINANCE_THRESHOLD:
                    result["signals"]["skew_regime"] = "PUT_DOMINANCE"
                    result["signals"]["skew_dominance"] = "PUTS"
                else:
                    result["signals"]["skew_regime"] = "BALANCED"
            else:
                result["status"] = "degraded"
                result["warnings"].append("Could not determine nearest expiry for Skew")

        except Exception as e:
            result["status"] = "error"
            result["warnings"].append(f"Skew calculation failed: {str(e)}")
            result["confidence"] = 0

        return result
