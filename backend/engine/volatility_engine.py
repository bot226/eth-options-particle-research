"""Volatility Engine — Institutional volatility regime intelligence module."""

from typing import Dict, Any

class VolatilityEngine:
    # Temporal memory for IV velocity calculation
    _iv_history = []  # list of (timestamp, atm_iv)
    MAX_IV_HISTORY = 120  # keep ~2 hours of snapshots at ~60s intervals
    MIN_RESEARCH_IV_DTE = 2
    _last_debug = {}

    @classmethod
    def calculate(cls, chain: dict, spot: float, dm=None) -> Dict[str, Any]:
        import time

        result = {
            "status": "ok",
            "metrics": {
                "atm_iv": 0,
                "call_iv": 0,
                "put_iv": 0,
                "term_structure_slope": 0,
                "front_premium": 0,
                "iv_velocity": 0,           # dIV/dt per hour
                "iv_velocity_label": "STABLE",
                "iv_acceleration": 0,
            },
            "signals": {
                "iv_regime": "NORMAL",
                "iv_regime_color": "neutral",  # neutral | cyan | orange | red
                "term_structure_label": "FLAT",  # CONTANGO | BACKWARDATION | FLAT
                "volatility_expansion": "STABLE",
                "event_risk": "LOW",
                "event_risk_level": 0,  # 0-3 (LOW/MEDIUM/HIGH/EXTREME)
                "regime_context": "",  # human-readable temporal context
            },
            "confidence": 100,
            "warnings": []
        }

        if not chain or spot <= 0:
            result["status"] = "degraded"
            result["warnings"].append("Missing chain or spot data")
            result["confidence"] = 0
            cls._last_debug = {
                "atm_iv": 0,
                "previous_atm_iv": cls._iv_history[-1][1] if cls._iv_history else None,
                "iv_delta": 0.0,
                "iv_velocity": 0.0,
                "iv_history_len": len(cls._iv_history),
                "iv_history_last_values": [v for _, v in cls._iv_history[-5:]],
                "calculation_mode": "fallback",
                "fallback_used": True,
                "fallback_reason": "options_chain_missing" if not chain else "invalid_spot_price",
                "source": "fallback",
                "options_chain_available": bool(chain),
                "atm_option_found": False,
                "raw_atm_iv": 0,
                "normalized_atm_iv": 0,
                "written_to_market_state": False,
                "written_to_research_logger": False,
                "market_state_volatility_keys": list(result.keys()),
                "research_logger_field": "iv_velocity",
            }
            return result

        try:
            from engine.calculator import Calculator

            # ── ATM IV ──
            nearest = None
            if dm and hasattr(dm, 'get_expiry_nearest'):
                nearest = dm.get_expiry_nearest()

            selected_expiry = None
            selected_dte = None
            selected_atm_data = None
            fallback_used = False
            fallback_reason = None

            def _dte(expiry):
                try:
                    from engine.calculator import _dte_from_expiry_str
                    return _dte_from_expiry_str(expiry)
                except Exception:
                    return None

            expiry_candidates = []
            for expiry in chain.keys():
                dte = _dte(expiry)
                atm_candidate = Calculator.get_atm_iv(chain.get(expiry, {}), spot)
                if dte is not None and atm_candidate.get("avg_iv", 0) > 0:
                    expiry_candidates.append((dte, expiry, atm_candidate))

            expiry_candidates.sort(key=lambda x: x[0])
            for dte, expiry, atm_candidate in expiry_candidates:
                if dte >= cls.MIN_RESEARCH_IV_DTE:
                    selected_dte = dte
                    selected_expiry = expiry
                    selected_atm_data = atm_candidate
                    break

            if not selected_expiry and nearest:
                selected_expiry = nearest
                selected_dte = _dte(nearest)
                selected_atm_data = Calculator.get_atm_iv(chain.get(nearest, {}), spot)
                fallback_used = True
                fallback_reason = (
                    "no_research_iv_expiry_dte_gte_2"
                    if selected_dte is not None and selected_dte < cls.MIN_RESEARCH_IV_DTE
                    else "atm_option_not_found"
                )

            if selected_expiry and selected_atm_data:
                atm_data = selected_atm_data
                result["metrics"]["atm_iv"] = round(atm_data.get("avg_iv", 0) * 100, 2)
                result["metrics"]["call_iv"] = round(atm_data.get("call_iv", 0) * 100, 2)
                result["metrics"]["put_iv"] = round(atm_data.get("put_iv", 0) * 100, 2)
            else:
                result["status"] = "degraded"
                result["warnings"].append("Could not determine nearest expiry for ATM IV")
                fallback_used = True
                fallback_reason = "options_chain_missing" if not chain else "atm_option_not_found"

            # ── Term Structure ──
            ts_data = Calculator.get_iv_term_structure(chain, spot)
            ts_metrics = ts_data.get("metrics", {})

            slope = ts_metrics.get("slope", 0)
            front_prem = ts_metrics.get("front_premium", 0)
            result["metrics"]["term_structure_slope"] = round(slope, 4)
            result["metrics"]["front_premium"] = round(front_prem, 4)

            # Interpret term structure
            if slope > 0.02:
                result["signals"]["term_structure_label"] = "CONTANGO"
            elif slope < -0.02:
                result["signals"]["term_structure_label"] = "BACKWARDATION"
            else:
                result["signals"]["term_structure_label"] = "FLAT"

            # ── IV Velocity (temporal derivative) ──
            now = time.time()
            atm = result["metrics"]["atm_iv"]
            previous_atm_iv = cls._iv_history[-1][1] if cls._iv_history else None
            iv_delta = round(atm - previous_atm_iv, 4) if previous_atm_iv is not None else 0.0
            cls._iv_history.append((now, atm))

            # Trim old entries
            if len(cls._iv_history) > cls.MAX_IV_HISTORY:
                cls._iv_history = cls._iv_history[-cls.MAX_IV_HISTORY:]

            iv_velocity = 0.0
            iv_accel = 0.0
            if len(cls._iv_history) >= 2:
                # Use oldest available point for velocity
                t0, iv0 = cls._iv_history[0]
                t1, iv1 = cls._iv_history[-1]
                dt_hours = (t1 - t0) / 3600.0
                if dt_hours > 0.001:
                    iv_velocity = round((iv1 - iv0) / dt_hours, 2)

                # Acceleration: compare first-half velocity to second-half
                mid = len(cls._iv_history) // 2
                if mid >= 1:
                    t_m, iv_m = cls._iv_history[mid]
                    dt_first = (t_m - t0) / 3600.0
                    dt_second = (t1 - t_m) / 3600.0
                    if dt_first > 0.001 and dt_second > 0.001:
                        v1 = (iv_m - iv0) / dt_first
                        v2 = (iv1 - iv_m) / dt_second
                        iv_accel = round(v2 - v1, 2)

            result["metrics"]["iv_velocity"] = iv_velocity
            result["metrics"]["iv_acceleration"] = iv_accel
            result["debug"] = {
                "atm_iv": atm,
                "previous_atm_iv": previous_atm_iv,
                "iv_delta": iv_delta,
                "iv_velocity": iv_velocity,
                "iv_history_len": len(cls._iv_history),
                "iv_history_last_values": [v for _, v in cls._iv_history[-5:]],
                "calculation_mode": "rolling_delta" if len(cls._iv_history) >= 2 else "fallback",
                "fallback_used": fallback_used or len(cls._iv_history) < 2,
                "fallback_reason": fallback_reason or ("insufficient_iv_history" if len(cls._iv_history) < 2 else None),
                "source": "volatility_engine" if not fallback_used else "fallback",
                "options_chain_available": bool(chain),
                "atm_option_found": bool(selected_atm_data and selected_atm_data.get("avg_iv", 0) > 0),
                "selected_expiry": selected_expiry,
                "selected_expiry_dte": selected_dte,
                "raw_atm_iv": selected_atm_data.get("avg_iv", 0) if selected_atm_data else 0,
                "normalized_atm_iv": atm,
                "written_to_market_state": True,
                "written_to_research_logger": True,
                "market_state_volatility_keys": list(result.keys()),
                "research_logger_field": "iv_velocity",
            }
            cls._last_debug = dict(result["debug"])

            # Interpret velocity
            if abs(iv_velocity) < 0.5:
                result["metrics"]["iv_velocity_label"] = "STABLE"
            elif iv_velocity > 2.0:
                result["metrics"]["iv_velocity_label"] = "EXPANDING RAPIDLY"
            elif iv_velocity > 0.5:
                result["metrics"]["iv_velocity_label"] = "EXPANDING"
            elif iv_velocity < -2.0:
                result["metrics"]["iv_velocity_label"] = "COMPRESSING RAPIDLY"
            elif iv_velocity < -0.5:
                result["metrics"]["iv_velocity_label"] = "COMPRESSING"

            # ── Regime Classification ──
            HIGH_IV = 65
            ELEVATED_IV = 45
            PANIC_SLOPE = -5

            if slope < PANIC_SLOPE:
                result["signals"]["iv_regime"] = "PANIC"
                result["signals"]["iv_regime_color"] = "red"
                result["signals"]["event_risk"] = "EXTREME"
                result["signals"]["event_risk_level"] = 3
            elif atm > HIGH_IV:
                result["signals"]["iv_regime"] = "VOL EXPANSION"
                result["signals"]["iv_regime_color"] = "orange"
            elif atm > ELEVATED_IV:
                result["signals"]["iv_regime"] = "ELEVATED"
                result["signals"]["iv_regime_color"] = "orange"
            elif atm < ELEVATED_IV and abs(slope) < 5:
                result["signals"]["iv_regime"] = "COMPRESSION"
                result["signals"]["iv_regime_color"] = "cyan"

            # Event risk (from front premium)
            if front_prem > 10:
                result["signals"]["event_risk"] = "EXTREME"
                result["signals"]["event_risk_level"] = 3
            elif front_prem > 5:
                result["signals"]["event_risk"] = "HIGH"
                result["signals"]["event_risk_level"] = 2
            elif front_prem > 3:
                result["signals"]["event_risk"] = "MEDIUM"
                result["signals"]["event_risk_level"] = 1
            else:
                result["signals"]["event_risk"] = "LOW"
                result["signals"]["event_risk_level"] = 0

            # Expansion risk (combines velocity + absolute level)
            if iv_velocity > 2.0 or atm > HIGH_IV:
                result["signals"]["volatility_expansion"] = "HIGH"
            elif iv_velocity > 0.5 or atm > ELEVATED_IV:
                result["signals"]["volatility_expansion"] = "ELEVATED"
            elif iv_velocity < -1.0 and atm < 30:
                result["signals"]["volatility_expansion"] = "CRUSHED"
            else:
                result["signals"]["volatility_expansion"] = "LOW"

            # ── Temporal Context (human-readable) ──
            context_parts = []
            if len(cls._iv_history) >= 5:
                duration_min = int((now - cls._iv_history[0][0]) / 60)
                if iv_velocity > 0.5:
                    context_parts.append(f"Рост IV в течение {duration_min} мин")
                elif iv_velocity < -0.5:
                    context_parts.append(f"Снижение IV в течение {duration_min} мин")

                if iv_accel > 0.3:
                    context_parts.append("Ускорение экспансии")
                elif iv_accel < -0.3:
                    context_parts.append("Углубление компрессии")

                regime = result["signals"]["iv_regime"]
                if regime == "COMPRESSION" and iv_velocity > 0:
                    context_parts.append("Ослабление компрессии")
                elif regime == "COMPRESSION" and iv_velocity < -0.5:
                    context_parts.append("Режим глубокой компрессии")

            result["signals"]["regime_context"] = " · ".join(context_parts) if context_parts else "Режим стабилен"

        except Exception as e:
            result["status"] = "error"
            result["warnings"].append(f"Volatility calculation failed: {str(e)}")
            result["confidence"] = 0
            cls._last_debug = {
                "atm_iv": result["metrics"].get("atm_iv", 0),
                "previous_atm_iv": cls._iv_history[-1][1] if cls._iv_history else None,
                "iv_delta": 0.0,
                "iv_velocity": result["metrics"].get("iv_velocity", 0),
                "iv_history_len": len(cls._iv_history),
                "iv_history_last_values": [v for _, v in cls._iv_history[-5:]],
                "calculation_mode": "unknown",
                "fallback_used": True,
                "fallback_reason": f"volatility_calculation_error:{str(e)}",
                "source": "unknown",
                "options_chain_available": bool(chain),
                "atm_option_found": False,
                "raw_atm_iv": 0,
                "normalized_atm_iv": result["metrics"].get("atm_iv", 0),
                "written_to_market_state": False,
                "written_to_research_logger": False,
                "market_state_volatility_keys": list(result.keys()),
                "research_logger_field": "iv_velocity",
            }

        return result

    @classmethod
    def get_debug(cls) -> dict:
        """Return latest volatility routing and IV velocity debug info."""
        return dict(cls._last_debug) if cls._last_debug else {
            "atm_iv": 0,
            "previous_atm_iv": None,
            "iv_delta": 0.0,
            "iv_velocity": 0.0,
            "iv_history_len": len(cls._iv_history),
            "iv_history_last_values": [v for _, v in cls._iv_history[-5:]],
            "calculation_mode": "unknown",
            "fallback_used": True,
            "fallback_reason": "volatility_engine_not_run_yet",
            "source": "unknown",
            "options_chain_available": False,
            "atm_option_found": False,
            "raw_atm_iv": 0,
            "normalized_atm_iv": 0,
            "written_to_market_state": False,
            "written_to_research_logger": False,
            "market_state_volatility_keys": [],
            "research_logger_field": "iv_velocity",
        }
