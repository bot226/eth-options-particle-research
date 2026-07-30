"""Gamma Engine for institutional options analysis."""

import math
from typing import Dict, Any

class GammaEngine:
    @staticmethod
    def calculate(chain: dict, spot: float, dm=None) -> Dict[str, Any]:
        result = {
            "status": "ok",
            "metrics": {
                "net_gex": 0,
                "near_spot_gex": 0,
                "total_positive_gex": 0,
                "total_negative_gex": 0,
                "gamma_flip": 0,
                "call_wall": 0,
                "put_wall": 0,
                "pinning_strength": 0,
                "gamma_acceleration": "STABLE"
            },
            "signals": {
                "gamma_regime": "LOW_GAMMA",
                "dealer_positioning": "NEUTRAL",
                "pinning_bias": "LOW"
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
            from engine.calculator import Calculator, _dte_from_expiry_str
            # Use calculator for raw math, but we interpret it here
            gex_data = Calculator.get_gamma_exposure_full(chain, spot)
            metrics = gex_data.get("metrics", {})
            
            result["metrics"]["net_gex"] = metrics.get("total_net_gex", 0)
            result["metrics"]["near_spot_gex"] = metrics.get("near_spot_gex", 0)
            result["metrics"]["total_positive_gex"] = metrics.get("total_positive_gex", 0)
            result["metrics"]["total_negative_gex"] = metrics.get("total_negative_gex", 0)
            result["metrics"]["gamma_flip"] = metrics.get("gamma_flip", 0)
            result["metrics"]["call_wall"] = metrics.get("gamma_wall_above", 0)
            result["metrics"]["put_wall"] = metrics.get("gamma_wall_below", 0)

            # Heuristics explanation
            # Pinning strength: (near spot gex / spot) normalized
            # High pinning threshold = 70
            HIGH_PINNING_THRESHOLD = 70
            net_gex = result["metrics"]["net_gex"]
            near_spot = result["metrics"]["near_spot_gex"]
            
            # Pinning logic
            pinning_strength = 0
            if near_spot > 0 and abs(net_gex) > 0:
                pinning_strength = min((near_spot / abs(net_gex)) * 100, 100)
            result["metrics"]["pinning_strength"] = round(pinning_strength, 2)
            
            if pinning_strength > HIGH_PINNING_THRESHOLD:
                result["signals"]["pinning_bias"] = "HIGH"
            elif pinning_strength > 30:
                result["signals"]["pinning_bias"] = "MEDIUM"
                
            # Regime
            if net_gex > 0:
                result["signals"]["gamma_regime"] = "POSITIVE_GAMMA"
                result["signals"]["dealer_positioning"] = "STABILIZING"
            elif net_gex < 0:
                result["signals"]["gamma_regime"] = "NEGATIVE_GAMMA"
                result["signals"]["dealer_positioning"] = "DESTABILIZING"

        except Exception as e:
            result["status"] = "error"
            result["warnings"].append(f"Gamma calculation failed: {str(e)}")
            result["confidence"] = 0

        return result
