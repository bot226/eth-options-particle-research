"""Flow Engine (Flow Pressure) for institutional options analysis."""

from typing import Dict, Any

class FlowEngine:
    @staticmethod
    def calculate(chain: dict, spot: float, dm=None) -> Dict[str, Any]:
        result = {
            "status": "ok",
            "metrics": {
                "flow_pressure": 50, # 0-100 scale, 50 is neutral
                "price_velocity": 0,
                "volume_acceleration": 0
            },
            "signals": {
                "flow_bias": "NEUTRAL",
                "pressure_state": "STABLE"
            },
            "confidence": 100,
            "warnings": []
        }

        if dm is None or spot <= 0:
            result["status"] = "degraded"
            result["warnings"].append("Missing dm or spot data")
            result["confidence"] = 0
            return result

        try:
            # Derived flow pressure heuristics
            # In absence of real tape/footprint, we use 24h change as proxy for velocity
            spot_change = getattr(dm, 'spot_24h_change', 0) * 100
            
            result["metrics"]["price_velocity"] = round(spot_change, 2)
            
            # Base pressure 50 + normalized spot change (e.g. 5% change = +25 pressure)
            pressure = 50 + (spot_change * 5)
            pressure = max(0, min(100, pressure))
            
            result["metrics"]["flow_pressure"] = round(pressure, 1)
            
            PRESSURE_BULLISH_THRESHOLD = 65
            PRESSURE_BEARISH_THRESHOLD = 35
            
            if pressure > PRESSURE_BULLISH_THRESHOLD:
                result["signals"]["flow_bias"] = "BULLISH"
                result["signals"]["pressure_state"] = "EXPANDING"
            elif pressure < PRESSURE_BEARISH_THRESHOLD:
                result["signals"]["flow_bias"] = "BEARISH"
                result["signals"]["pressure_state"] = "CONTRACTING"
            else:
                result["signals"]["flow_bias"] = "NEUTRAL"
                
        except Exception as e:
            result["status"] = "error"
            result["warnings"].append(f"Flow calculation failed: {str(e)}")
            result["confidence"] = 0

        return result
