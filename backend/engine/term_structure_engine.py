"""Volatility Term Structure Engine — Analyzes the shape of the volatility curve.

Tracks:
- Front IV stress vs Back-end IV.
- Term inversion (Backwardation vs Contango).
- Event pricing and expiry-specific panic.
"""

from typing import Dict, Any

class TermStructureEngine:
    @staticmethod
    def calculate(chain: dict, spot: float) -> Dict[str, Any]:
        result = {
            "status": "ok",
            "metrics": {
                "front_iv": 0,
                "back_iv": 0,
                "front_stress_score": 0
            },
            "features": {
                "term_structure_state": "UNKNOWN",
                "volatility_curve_shape": [],
                "expiry_panic_signals": []
            },
            "warnings": []
        }

        if not chain or spot <= 0:
            result["status"] = "degraded"
            result["warnings"].append("Missing chain or spot data for Term Structure")
            return result

        try:
            from engine.calculator import Calculator, _dte_from_expiry_str
            
            curve = []
            front_ivs = []
            back_ivs = []

            for expiry_str, strikes_data in chain.items():
                dte = _dte_from_expiry_str(expiry_str)
                if dte is None or dte < 0:
                    continue

                atm_data = Calculator.get_atm_iv(strikes_data, spot)
                atm_iv = atm_data.get("avg_iv", 0)
                if atm_iv > 0:
                    curve.append({"expiry": expiry_str, "dte": dte, "atm_iv": round(atm_iv, 4)})

            # Sort curve by DTE
            curve.sort(key=lambda x: x["dte"])
            result["features"]["volatility_curve_shape"] = curve

            if not curve:
                result["status"] = "degraded"
                result["warnings"].append("No valid ATM IVs found for curve generation")
                return result

            # Separate front (< 14 DTE) and back (> 30 DTE)
            for point in curve:
                if point["dte"] <= 14:
                    front_ivs.append(point["atm_iv"])
                elif point["dte"] >= 30:
                    back_ivs.append(point["atm_iv"])

            # If no back expiries, use whatever is furthest
            if not back_ivs and len(curve) > 1:
                back_ivs.append(curve[-1]["atm_iv"])
            if not front_ivs and len(curve) > 0:
                front_ivs.append(curve[0]["atm_iv"])

            front_iv = sum(front_ivs) / len(front_ivs) if front_ivs else 0
            back_iv = sum(back_ivs) / len(back_ivs) if back_ivs else 0

            result["metrics"]["front_iv"] = round(front_iv, 4)
            result["metrics"]["back_iv"] = round(back_iv, 4)

            # Determine Term Structure State
            state = "FLAT"
            if front_iv > 0 and back_iv > 0:
                ratio = front_iv / back_iv
                if ratio > 1.05:
                    state = "BACKWARDATION"
                elif ratio < 0.95:
                    state = "CONTANGO"

            result["features"]["term_structure_state"] = state

            # Calculate Front Stress Score (0-100)
            stress = 0
            if state == "BACKWARDATION":
                # Max stress when front IV is 50%+ higher than back IV
                stress = min(100, (ratio - 1.0) / 0.5 * 100)
            result["metrics"]["front_stress_score"] = round(stress, 1)

            # Expiry Panic Signals (Identify specific expiries with abnormal IV spikes)
            # Find expiries that have IV > 15% higher than their immediate neighbors
            panic_signals = []
            for i in range(1, len(curve) - 1):
                prev_iv = curve[i-1]["atm_iv"]
                curr_iv = curve[i]["atm_iv"]
                next_iv = curve[i+1]["atm_iv"]
                
                avg_neighbor_iv = (prev_iv + next_iv) / 2
                if curr_iv > avg_neighbor_iv * 1.15:
                    panic_signals.append({
                        "expiry": curve[i]["expiry"],
                        "dte": curve[i]["dte"],
                        "spike_pct": round((curr_iv / avg_neighbor_iv - 1) * 100, 1)
                    })
            result["features"]["expiry_panic_signals"] = panic_signals

        except Exception as e:
            result["status"] = "error"
            result["warnings"].append(f"Term Structure calculation failed: {str(e)}")

        return result
