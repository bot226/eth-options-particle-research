"""Synthetic Orderflow Engine — Estimates institutional flow pressure.

This engine does NOT represent real footprint/tape data. It estimates
Synthetic Flow Pressure based on derivatives of price, OI, and IV.

Inputs:
- Price velocity / spot momentum
- OI delta
- IV expansion (volatility acceleration)
- Delta shifts / flow bias

Flow scale: -100 to +100, 0 = neutral.
negative = sell pressure, positive = buy pressure.
abs(value) = flow intensity.

Debug breakdown is available via get_debug() for endpoint inspection.
"""

from collections import deque
from typing import Dict, Any


class SyntheticOrderflowEngine:
    _last_debug: dict = {}
    _debug_history = deque(maxlen=1000)

    @staticmethod
    def calculate(vol_state: dict, flow_state: dict, dm=None, spot: float = 0) -> Dict[str, Any]:
        result = {
            "status": "ok",
            "metrics": {
                "synthetic_aggressive_buying": 0,
                "synthetic_aggressive_selling": 0,
                "flow_momentum_score": 0,
            },
            "features": {
                "structural_flow_imbalance": "BALANCED",
                "flow_momentum_direction": "NONE",
                "flow_scale": "-100_to_100_neutral_0"
            },
            "warnings": []
        }

        try:
            # 1. Gather baseline metrics
            iv_velocity = vol_state.get("metrics", {}).get("iv_velocity", 0)
            flow_bias = flow_state.get("signals", {}).get("flow_bias", "NEUTRAL")
            # flow_pressure: 0-100 (50 = neutral, >50 = bullish)
            flow_pressure = flow_state.get("metrics", {}).get("flow_pressure", 50)
            price_velocity = flow_state.get("metrics", {}).get("price_velocity", 0)
            volume_acceleration = flow_state.get("metrics", {}).get("volume_acceleration", 0)

            oi_delta = 0.0
            if dm and hasattr(dm, "get_oi_delta_pct"):
                try:
                    oi_delta = dm.get_oi_delta_pct("ETH", hours_ago=24)
                except Exception:
                    pass

            # 2. Synthesize Aggressive Buying / Selling
            # flow_pressure: 50 = neutral → bull_strength=0, bear_strength=0
            bull_strength = max(0, (flow_pressure - 50) * 2)   # 0 to 100
            bear_strength = max(0, (50 - flow_pressure) * 2)   # 0 to 100

            # Accelerators: IV velocity and OI expansion indicate aggression.
            # This remains the live formula; the detailed component fields below
            # only explain how the current formula behaved.
            aggression_multiplier = 1.0 + (iv_velocity * 0.1) + (max(0, oi_delta) * 0.05)

            raw_buying = bull_strength * aggression_multiplier
            raw_selling = bear_strength * aggression_multiplier
            agg_buying = min(100, raw_buying)
            agg_selling = min(100, raw_selling)
            clamp_applied = raw_buying > 100 or raw_selling > 100

            result["metrics"]["synthetic_aggressive_buying"] = round(agg_buying, 1)
            result["metrics"]["synthetic_aggressive_selling"] = round(agg_selling, 1)

            # 3. Flow Momentum (-100 to +100)
            momentum = agg_buying - agg_selling
            result["metrics"]["flow_momentum_score"] = round(momentum, 1)

            if momentum > 25:
                result["features"]["flow_momentum_direction"] = "BULLISH_ACCELERATING"
            elif momentum > 10:
                result["features"]["flow_momentum_direction"] = "BULLISH_DRIFT"
            elif momentum < -25:
                result["features"]["flow_momentum_direction"] = "BEARISH_ACCELERATING"
            elif momentum < -10:
                result["features"]["flow_momentum_direction"] = "BEARISH_DRIFT"
            else:
                result["features"]["flow_momentum_direction"] = "NEUTRAL"

            # 4. Structural Imbalance
            if agg_buying > 60 and agg_selling < 20:
                result["features"]["structural_flow_imbalance"] = "STRONG_BUY_IMBALANCE"
            elif agg_selling > 60 and agg_buying < 20:
                result["features"]["structural_flow_imbalance"] = "STRONG_SELL_IMBALANCE"
            elif agg_buying > 40 and agg_selling > 40:
                result["features"]["structural_flow_imbalance"] = "HIGH_CONFLICT"
            else:
                result["features"]["structural_flow_imbalance"] = "BALANCED"

            # 5. Build debug breakdown
            if abs(momentum) < 10:
                dominant = "weak_directional_components"
            elif aggression_multiplier < 0:
                dominant = "negative_aggression_multiplier_from_iv_velocity"
            elif momentum < 0:
                dominant = "selling_components_dominate"
            elif momentum > 0:
                dominant = "buying_components_dominate"
            else:
                dominant = "balanced"

            debug = {
                "synthetic_flow_pressure": round(momentum, 1),
                "flow_scale": "-100_to_100_neutral_0",
                "flow_intensity": round(abs(momentum), 1),
                "inputs": {
                    "spot_price": round(spot, 2) if spot else 0,
                    "price_velocity": round(price_velocity, 2),
                    "price_velocity_window_sec": 86400,
                    "oi_delta": round(oi_delta, 4),
                    "iv_velocity": round(iv_velocity, 2),
                    "volume_acceleration": round(volume_acceleration, 2),
                    "liquidation_pressure": 0.0,
                    "flow_pressure": round(flow_pressure, 1),
                    "flow_bias": flow_bias,
                },
                "flow_pressure_input": round(flow_pressure, 1),
                "price_velocity_input": round(price_velocity, 2),
                "volume_acceleration_input": round(volume_acceleration, 2),
                "iv_velocity_input": round(iv_velocity, 2),
                "oi_delta_input": round(oi_delta, 4),
                "aggression_multiplier": round(aggression_multiplier, 3),
                "bull_strength": round(bull_strength, 1),
                "bear_strength": round(bear_strength, 1),
                "buying_components": {
                    "price_velocity_up": round(bull_strength, 1),
                    "breakout_momentum": 0,
                    "recovery_momentum": 0,
                    "oi_expansion_up": round(max(0, oi_delta) * 0.05, 4),
                    "iv_expansion_call_side": round(max(0, iv_velocity) * 0.1, 3),
                    "volume_acceleration_buy": 0,
                    "bull_strength_from_flow_pressure": round(bull_strength, 1),
                    "aggression_boost_iv": round(iv_velocity * 0.1, 3),
                    "aggression_boost_oi": round(max(0, oi_delta) * 0.05, 4),
                    "raw_buying_score": round(agg_buying, 1),
                },
                "selling_components": {
                    "price_velocity_down": round(bear_strength, 1),
                    "breakdown_momentum": 0,
                    "rejection_momentum": 0,
                    "oi_expansion_down": 0,
                    "iv_expansion_put_side": round(max(0, iv_velocity) * 0.1, 3),
                    "volume_acceleration_sell": 0,
                    "bear_strength_from_flow_pressure": round(bear_strength, 1),
                    "aggression_boost_iv": round(iv_velocity * 0.1, 3),
                    "aggression_boost_oi": round(max(0, oi_delta) * 0.05, 4),
                    "raw_selling_score": round(agg_selling, 1),
                },
                "raw_buying_score": round(agg_buying, 1),
                "raw_selling_score": round(agg_selling, 1),
                "baseline_adjustment": 0,
                "clamp_applied": clamp_applied,
                "flow_momentum_score": round(momentum, 1),
                "flow_momentum_direction": result["features"]["flow_momentum_direction"],
                "reason": dominant,
                "note": (
                    "Live flow uses FlowEngine.flow_pressure, which is currently based on 24h spot change. "
                    "OHLCV 1m context is exposed by the research route for validation only and does not affect live flow. "
                    "FLOW_SURGE remains a crossing-event at flow_intensity 35."
                ),
            }
            SyntheticOrderflowEngine._last_debug = debug
            SyntheticOrderflowEngine._debug_history.append(dict(debug))

        except Exception as e:
            result["status"] = "error"
            result["warnings"].append(f"Synthetic Orderflow calculation failed: {str(e)}")

        return result

    @classmethod
    def get_debug(cls) -> dict:
        """Return last debug breakdown for debug endpoint."""
        return dict(cls._last_debug) if cls._last_debug else {}

    @classmethod
    def get_debug_summary(cls) -> dict:
        """Return in-memory flow debug summary for the current backend process."""
        rows = list(cls._debug_history)

        def _avg(key):
            vals = [float(r.get(key, 0) or 0) for r in rows]
            return round(sum(vals) / len(vals), 2) if vals else 0

        def _dist(key):
            out = {}
            for row in rows:
                value = row.get(key, "UNKNOWN")
                out[value] = out.get(value, 0) + 1
            return out

        buckets = {
            "sell_lt_-20": 0,
            "weak_sell": 0,
            "neutral": 0,
            "weak_buy": 0,
            "upper_weak_buy_20_35": 0,
            "surge_35_plus": 0,
        }
        for row in rows:
            flow = float(row.get("synthetic_flow_pressure", 0) or 0)
            if flow < -20:
                buckets["sell_lt_-20"] += 1
            elif flow < -10:
                buckets["weak_sell"] += 1
            elif flow <= 10:
                buckets["neutral"] += 1
            elif flow < 20:
                buckets["weak_buy"] += 1
            elif flow < 35:
                buckets["upper_weak_buy_20_35"] += 1
            else:
                buckets["surge_35_plus"] += 1

        flows = [float(r.get("synthetic_flow_pressure", 0) or 0) for r in rows]
        return {
            "sample_count": len(rows),
            "synthetic_flow_pressure_min": round(min(flows), 2) if flows else 0,
            "synthetic_flow_pressure_avg": _avg("synthetic_flow_pressure"),
            "synthetic_flow_pressure_max": round(max(flows), 2) if flows else 0,
            "flow_bucket_distribution": buckets,
            "direction_distribution": _dist("flow_momentum_direction"),
            "reason_distribution": _dist("reason"),
            "avg_flow_pressure_input": _avg("flow_pressure_input"),
            "avg_price_velocity_input": _avg("price_velocity_input"),
            "avg_iv_velocity_input": _avg("iv_velocity_input"),
            "avg_oi_delta_input": _avg("oi_delta_input"),
            "avg_bull_strength": _avg("bull_strength"),
            "avg_bear_strength": _avg("bear_strength"),
        }
