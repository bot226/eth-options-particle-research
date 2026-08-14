"""Dealer Hedging Pressure Engine — Simulates nonlinear hedging demand/supply.

Two outputs:
1. hedge_acceleration_risk (existing): nonlinear acceleration ratio from scenario analysis
2. dealer_hedging_pressure (new): scoring-based current hedging pressure assessment

Scenario analysis evaluates how dealer hedging scales across dynamic spot displacement:
- Scenarios: ±0.5%, ±1.0%, ±2.0%, ±3.0%

Pressure scoring evaluates current structural conditions:
- gamma_slope_state / gamma_acceleration_state
- flow_intensity (directional scale, abs(flow_momentum_score))
- iv_velocity
- distance to call/put walls
- net_gex magnitude relative to history
"""

from typing import Dict, Any, List


class DealerHedgingEngine:
    # Rolling history for relative GEX comparison
    _net_gex_history: List[float] = []
    _GEX_HISTORY_WINDOW = 120  # ~30 min at 15s interval
    _last_debug: dict = {}

    @classmethod
    def calculate(cls, chain: dict, spot: float,
                  gamma_surface: dict = None,
                  volatility: dict = None,
                  orderflow: dict = None,
                  gamma_metrics: dict = None) -> Dict[str, Any]:
        result = {
            "status": "ok",
            "scenarios": {
                "up": {},
                "down": {}
            },
            "metrics": {
                "max_rebalancing_pressure": 0,
                "hedge_acceleration_risk": "LOW",
                "dealer_hedging_pressure": "LOW",
                "dealer_hedging_pressure_score": 0,
                "dealer_hedging_pressure_reason": "",
            },
            "warnings": []
        }

        if not chain or spot <= 0:
            result["status"] = "degraded"
            result["warnings"].append("Missing chain or spot data for Hedging Engine")
            cls._store_debug(result, {})
            return result

        try:
            from engine.calculator import Calculator
            
            scenarios = [0.005, 0.01, 0.02, 0.03]
            
            base_gex = Calculator.get_gamma_exposure_full(chain, spot)
            base_net = base_gex.get("metrics", {}).get("total_net_gex", 0)

            # Track GEX history for relative comparison
            cls._net_gex_history.append(abs(base_net))
            if len(cls._net_gex_history) > cls._GEX_HISTORY_WINDOW:
                cls._net_gex_history = cls._net_gex_history[-cls._GEX_HISTORY_WINDOW:]

            max_pressure = 0
            
            for move in scenarios:
                # UP scenario
                spot_up = spot * (1 + move)
                gex_up = Calculator.get_gamma_exposure_full(chain, spot_up)
                net_up = gex_up.get("metrics", {}).get("total_net_gex", 0)
                
                avg_gex_up = (base_net + net_up) / 2
                hedge_up = -avg_gex_up * move  
                
                result["scenarios"]["up"][f"+{move*100}%"] = {
                    "simulated_spot": spot_up,
                    "net_gex": net_up,
                    "hedge_requirement_eth": round(hedge_up, 2),
                    "action": "BUY" if hedge_up > 0 else "SELL"
                }
                
                if abs(hedge_up) > abs(max_pressure):
                    max_pressure = hedge_up

                # DOWN scenario
                spot_down = spot * (1 - move)
                gex_down = Calculator.get_gamma_exposure_full(chain, spot_down)
                net_down = gex_down.get("metrics", {}).get("total_net_gex", 0)
                
                avg_gex_down = (base_net + net_down) / 2
                hedge_down = -avg_gex_down * (-move) 

                result["scenarios"]["down"][f"-{move*100}%"] = {
                    "simulated_spot": spot_down,
                    "net_gex": net_down,
                    "hedge_requirement_eth": round(hedge_down, 2),
                    "action": "BUY" if hedge_down > 0 else "SELL"
                }
                
                if abs(hedge_down) > abs(max_pressure):
                    max_pressure = hedge_down

            result["metrics"]["max_rebalancing_pressure"] = round(max_pressure, 2)
            
            # ── Hedge acceleration risk (existing logic) ──────────────
            up_1 = abs(result["scenarios"]["up"].get("+1.0%", {}).get("hedge_requirement_eth", 0))
            up_3 = abs(result["scenarios"]["up"].get("+3.0%", {}).get("hedge_requirement_eth", 0))
            
            down_1 = abs(result["scenarios"]["down"].get("-1.0%", {}).get("hedge_requirement_eth", 0))
            down_3 = abs(result["scenarios"]["down"].get("-3.0%", {}).get("hedge_requirement_eth", 0))
            
            risk = "LOW"
            if (up_1 > 0 and up_3 > up_1 * 4) or (down_1 > 0 and down_3 > down_1 * 4):
                risk = "HIGH"
            elif (up_1 > 0 and up_3 > up_1 * 3.2) or (down_1 > 0 and down_3 > down_1 * 3.2):
                risk = "MEDIUM"
                
            result["metrics"]["hedge_acceleration_risk"] = risk

            # ── Dealer hedging pressure scoring (new) ─────────────────
            pressure_score = 0
            pressure_reasons = []

            # 1. Gamma slope state (from gamma_surface)
            gs_metrics = {}
            if gamma_surface:
                gs_metrics = gamma_surface.get("metrics", {})
            gamma_slope_state = gs_metrics.get("gamma_slope_state", "neutral")
            gamma_accel_state = gs_metrics.get("gamma_acceleration_state", "neutral")

            if gamma_slope_state in ("collapsing",):
                pressure_score += 25
                pressure_reasons.append("gamma_collapsing")
            elif gamma_slope_state in ("weakening", "negative"):
                pressure_score += 15
                pressure_reasons.append(f"gamma_{gamma_slope_state}")

            if gamma_accel_state in ("accelerating", "collapsing"):
                pressure_score += 15
                pressure_reasons.append(f"gamma_accel_{gamma_accel_state}")

            # 2. Flow intensity (directional scale)
            flow_intensity = 0
            if orderflow:
                flow_mom = orderflow.get("metrics", {}).get("flow_momentum_score", 0)
                flow_intensity = abs(flow_mom)

            if flow_intensity >= 50:
                pressure_score += 20
                pressure_reasons.append("strong_flow")
            elif flow_intensity >= 35:
                pressure_score += 12
                pressure_reasons.append("moderate_flow")

            # 3. IV velocity
            iv_velocity = 0
            if volatility:
                iv_velocity = volatility.get("metrics", {}).get("iv_velocity", 0)

            if iv_velocity >= 2.0:
                pressure_score += 10
                pressure_reasons.append("iv_spike")

            # 4. Distance to walls
            call_wall = 0
            put_wall = 0
            if gamma_metrics:
                call_wall = gamma_metrics.get("metrics", {}).get("call_wall", 0)
                put_wall = gamma_metrics.get("metrics", {}).get("put_wall", 0)

            if spot > 0 and call_wall > 0:
                dist_call_pct = abs(call_wall - spot) / spot * 100
                if dist_call_pct < 0.5:
                    pressure_score += 15
                    pressure_reasons.append("near_call_wall")
                elif dist_call_pct < 1.0:
                    pressure_score += 8

            if spot > 0 and put_wall > 0:
                dist_put_pct = abs(spot - put_wall) / spot * 100
                if dist_put_pct < 0.5:
                    pressure_score += 15
                    pressure_reasons.append("near_put_wall")
                elif dist_put_pct < 1.0:
                    pressure_score += 8

            # 5. Net GEX magnitude relative to rolling history
            if len(cls._net_gex_history) >= 10:
                gex_mean = sum(cls._net_gex_history) / len(cls._net_gex_history)
                if gex_mean > 0:
                    gex_ratio = abs(base_net) / gex_mean
                    if gex_ratio > 2.0:
                        pressure_score += 15
                        pressure_reasons.append("gex_elevated")
                    elif gex_ratio > 1.5:
                        pressure_score += 8

            # 6. Hedge acceleration risk from scenario analysis
            if risk == "HIGH":
                pressure_score += 10
                pressure_reasons.append("nonlinear_hedge_acceleration")
            elif risk == "MEDIUM":
                pressure_score += 5

            # Classify
            if pressure_score >= 60:
                dealer_hedging_pressure = "HIGH"
            elif pressure_score >= 35:
                dealer_hedging_pressure = "MEDIUM"
            else:
                dealer_hedging_pressure = "LOW"

            result["metrics"]["dealer_hedging_pressure"] = dealer_hedging_pressure
            result["metrics"]["dealer_hedging_pressure_score"] = pressure_score
            result["metrics"]["dealer_hedging_pressure_reason"] = ";".join(pressure_reasons) if pressure_reasons else "no_pressure_signals"

            # Store debug
            cls._store_debug(result, {
                "pressure_score": pressure_score,
                "pressure_reasons": pressure_reasons,
                "gamma_slope_state": gamma_slope_state,
                "gamma_accel_state": gamma_accel_state,
                "flow_intensity": flow_intensity,
                "iv_velocity": iv_velocity,
                "call_wall": call_wall,
                "put_wall": put_wall,
                "net_gex": base_net,
                "gex_history_len": len(cls._net_gex_history),
                "hedge_acceleration_risk": risk,
            })

        except Exception as e:
            result["status"] = "error"
            result["warnings"].append(f"Hedging simulation failed: {str(e)}")

        return result

    @classmethod
    def _store_debug(cls, result: dict, breakdown: dict):
        """Store debug breakdown for endpoint."""
        cls._last_debug = {
            "dealer_hedging_pressure": result["metrics"].get("dealer_hedging_pressure", "LOW"),
            "dealer_hedging_pressure_score": result["metrics"].get("dealer_hedging_pressure_score", 0),
            "hedge_acceleration_risk": result["metrics"].get("hedge_acceleration_risk", "LOW"),
            "breakdown": breakdown,
        }

    @classmethod
    def get_debug(cls) -> dict:
        """Return last debug breakdown for debug endpoint."""
        return dict(cls._last_debug) if cls._last_debug else {}
