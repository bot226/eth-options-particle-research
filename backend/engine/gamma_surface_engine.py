"""Gamma Surface Engine — Analyzes gamma density, slope, and acceleration corridors.

Calculates:
- Gamma density (concentration of GEX).
- Gamma slope (first derivative of GEX).
- Gamma curvature (second derivative).
- Acceleration gradients (rapid transitions from high to low gamma).

Locates:
- Dealer Stability Zones (high gamma density, low slope).
- Structural Instability Regions (low gamma density, high slope).
- Gamma Voids (near-zero gamma density).
- Acceleration Corridors (low gamma with fast delta transitions).
"""

import math
from typing import Dict, Any

class GammaSurfaceEngine:
    # Rolling history for adaptive z-score thresholds
    _decay_ratio_history: list = []
    _accel_history: list = []
    _ROLLING_WINDOW = 120  # ~30 min at 15s interval

    @classmethod
    def calculate(cls, chain: dict, spot: float, dm=None) -> Dict[str, Any]:
        result = {
            "status": "ok",
            "metrics": {
                "max_density_strike": 0,
                "min_density_strike": 0,
                "max_acceleration_gradient": 0,
            },
            "features": {
                "dealer_stability_zones": [],
                "instability_regions": [],
                "gamma_voids": [],
                "acceleration_corridors": []
            },
            "warnings": []
        }

        if not chain or spot <= 0:
            result["status"] = "degraded"
            result["warnings"].append("Missing chain or spot data for Gamma Surface")
            return result

        try:
            from engine.calculator import Calculator
            gex_data = Calculator.get_gamma_exposure_full(chain, spot)
            points = gex_data.get("data", [])
            
            if len(points) < 3:
                result["status"] = "degraded"
                result["warnings"].append("Insufficient strikes for surface analysis")
                return result

            # Sort points by strike just in case
            points.sort(key=lambda x: x["strike"])

            # 1. Calculate Density, Slope, Curvature
            # We calculate derivatives using finite differences
            surface_points = []
            
            # Simple moving average for density smoothing
            window = 3
            for i in range(len(points)):
                strike = points[i]["strike"]
                net_gex = points[i]["net_gex"]
                dist_pct = points[i]["dist_pct"]

                # Density (smoothed absolute GEX)
                start_idx = max(0, i - window // 2)
                end_idx = min(len(points), i + window // 2 + 1)
                subset = points[start_idx:end_idx]
                density = sum(abs(p["net_gex"]) for p in subset) / len(subset) if subset else 0

                surface_points.append({
                    "strike": strike,
                    "net_gex": net_gex,
                    "density": density,
                    "dist_pct": dist_pct,
                    "slope": 0.0,
                    "curvature": 0.0,
                    "acceleration_gradient": 0.0
                })

            # Linear regression helper for windowed slope
            def calc_slope(x, y):
                n = len(x)
                if n < 2: return 0.0
                sum_x = sum(x)
                sum_y = sum(y)
                sum_xy = sum(xi*yi for xi, yi in zip(x, y))
                sum_xx = sum(xi*xi for xi in x)
                denominator = (n * sum_xx - sum_x * sum_x)
                if denominator == 0: return 0.0
                return (n * sum_xy - sum_x * sum_y) / denominator

            # Calculate Windowed Structural Gamma Gradient (Slope)
            slope_window = 5
            for i in range(len(surface_points)):
                start_idx = max(0, i - slope_window // 2)
                end_idx = min(len(surface_points), i + slope_window // 2 + 1)
                subset = surface_points[start_idx:end_idx]
                if len(subset) >= 3:
                    x = [p["strike"] for p in subset]
                    y = [p["density"] for p in subset]
                    surface_points[i]["slope"] = calc_slope(x, y)
                else:
                    surface_points[i]["slope"] = 0.0

            # Calculate Structural Acceleration (Windowed slope of slope)
            for i in range(len(surface_points)):
                start_idx = max(0, i - slope_window // 2)
                end_idx = min(len(surface_points), i + slope_window // 2 + 1)
                subset = surface_points[start_idx:end_idx]
                if len(subset) >= 3:
                    x = [p["strike"] for p in subset]
                    y = [p["slope"] for p in subset]
                    curvature = calc_slope(x, y)
                    surface_points[i]["curvature"] = curvature
                else:
                    surface_points[i]["curvature"] = 0.0
                
                # Acceleration gradient: when slope is negative (density dropping) and curvature is high
                curr_p = surface_points[i]
                accel = -curr_p["slope"] * 100 if curr_p["slope"] < 0 else 0
                curr_p["acceleration_gradient"] = accel

            # Determine thresholds based on max density
            max_density = max((p["density"] for p in surface_points), default=0)
            if max_density == 0:
                max_density = 1
                
            stability_threshold = max_density * 0.4
            void_threshold = max_density * 0.1
            high_accel_threshold = max((p["acceleration_gradient"] for p in surface_points), default=0) * 0.5

            if high_accel_threshold == 0:
                high_accel_threshold = 1

            # 2. Identify Zones
            dealer_zones = []
            instability_regions = []
            voids = []
            accel_corridors = []

            for p in surface_points:
                # We only care about strikes reasonably close to spot (+/- 25%)
                if p["dist_pct"] > 25:
                    continue
                    
                s = p["strike"]
                d = p["density"]
                acc = p["acceleration_gradient"]
                
                if d >= stability_threshold and abs(p["slope"]) < (max_density * 0.01):
                    dealer_zones.append(s)
                elif d < stability_threshold and abs(p["slope"]) > (max_density * 0.05):
                    instability_regions.append(s)
                    
                if d <= void_threshold:
                    voids.append(s)
                    
                if acc >= high_accel_threshold and d < stability_threshold:
                    accel_corridors.append(s)

            # Group continuous strike ranges into corridors
            def _group_ranges(strikes):
                if not strikes:
                    return []
                ranges = []
                start = strikes[0]
                prev = strikes[0]
                for s in strikes[1:]:
                    # If gap is less than 5% of spot, consider it continuous
                    if s - prev <= spot * 0.05:
                        prev = s
                    else:
                        ranges.append({"start": start, "end": prev})
                        start = s
                        prev = s
                ranges.append({"start": start, "end": prev})
                return ranges

            result["features"]["dealer_stability_zones"] = _group_ranges(dealer_zones)
            result["features"]["instability_regions"] = _group_ranges(instability_regions)
            result["features"]["gamma_voids"] = _group_ranges(voids)
            result["features"]["acceleration_corridors"] = _group_ranges(accel_corridors)

            result["metrics"]["max_density_strike"] = max(surface_points, key=lambda x: x["density"])["strike"] if surface_points else 0
            result["metrics"]["min_density_strike"] = min(surface_points, key=lambda x: x["density"])["strike"] if surface_points else 0
            result["metrics"]["max_acceleration_gradient"] = max(surface_points, key=lambda x: x["acceleration_gradient"])["acceleration_gradient"] if surface_points else 0

            # Calculate spot-centric structural metrics over a 10% corridor
            spot_corridor = [p for p in surface_points if abs(p["strike"] - spot) / spot < 0.10]
            if spot_corridor:
                # Weighted average slope/accel closer to spot
                total_w = 0.0
                sum_slope = 0.0
                sum_accel = 0.0
                for p in spot_corridor:
                    dist = abs(p["strike"] - spot) / spot
                    weight = 1.0 - (dist / 0.10)
                    sum_slope += p["slope"] * weight
                    sum_accel += p["acceleration_gradient"] * weight
                    total_w += weight
                avg_slope = sum_slope / total_w if total_w > 0 else 0.0
                avg_accel = sum_accel / total_w if total_w > 0 else 0.0
            else:
                avg_slope = 0.0
                avg_accel = 0.0
                
            # Classify gamma slope state using structural asymmetry + rolling z-score
            # Instead of raw slope (which is in GEX/strike units and hard to normalize),
            # we measure how gamma density decays around spot.
            
            # Split corridor into left (below spot) and right (above spot)
            left_corridor = [p for p in spot_corridor if p["strike"] < spot]
            right_corridor = [p for p in spot_corridor if p["strike"] >= spot]
            
            # Average density on each side
            avg_density_left = sum(p["density"] for p in left_corridor) / len(left_corridor) if left_corridor else 0
            avg_density_right = sum(p["density"] for p in right_corridor) / len(right_corridor) if right_corridor else 0
            
            # Near-spot density (within 2%) vs far-spot density (5-10%)
            near_spot = [p for p in spot_corridor if abs(p["strike"] - spot) / spot < 0.02]
            far_spot = [p for p in spot_corridor if 0.05 <= abs(p["strike"] - spot) / spot < 0.10]
            
            avg_near = sum(p["density"] for p in near_spot) / len(near_spot) if near_spot else 0
            avg_far = sum(p["density"] for p in far_spot) / len(far_spot) if far_spot else 0
            
            # Density decay ratio: how much density drops from near to far
            if avg_near > 0:
                decay_ratio = avg_far / avg_near  # 1.0 = flat, <1 = decaying, >1 = building
            else:
                decay_ratio = 1.0
                
            # Asymmetry ratio: which side has more density
            total_lr = avg_density_left + avg_density_right
            if total_lr > 0:
                asymmetry = (avg_density_right - avg_density_left) / total_lr  # -1 to +1
            else:
                asymmetry = 0.0
                
            # Count of strikes with negative slope in corridor
            declining_count = sum(1 for p in spot_corridor if p["slope"] < 0)
            declining_pct = declining_count / len(spot_corridor) if spot_corridor else 0
            
            # ── Adaptive z-score classification ───────────────────────
            # Track rolling history of decay_ratio for z-score
            cls._decay_ratio_history.append(decay_ratio)
            if len(cls._decay_ratio_history) > cls._ROLLING_WINDOW:
                cls._decay_ratio_history = cls._decay_ratio_history[-cls._ROLLING_WINDOW:]
            
            cls._accel_history.append(avg_accel)
            if len(cls._accel_history) > cls._ROLLING_WINDOW:
                cls._accel_history = cls._accel_history[-cls._ROLLING_WINDOW:]
            
            has_enough_history = len(cls._decay_ratio_history) >= 20
            
            if has_enough_history:
                # Z-score based classification
                dr_mean = sum(cls._decay_ratio_history) / len(cls._decay_ratio_history)
                dr_var = sum((x - dr_mean) ** 2 for x in cls._decay_ratio_history) / len(cls._decay_ratio_history)
                dr_std = dr_var ** 0.5 if dr_var > 0 else 0.001
                
                z_decay = (decay_ratio - dr_mean) / dr_std
                
                slope_state = "neutral"
                if z_decay < -2.0 and declining_pct > 0.6:
                    slope_state = "collapsing"
                elif z_decay < -1.0 and declining_pct > 0.5:
                    slope_state = "weakening"
                elif decay_ratio < 0.7 and declining_pct > 0.5:
                    slope_state = "negative"
                elif z_decay > 1.0 and declining_pct < 0.35:
                    slope_state = "strengthening"
                # else: neutral (default, NOT weakening)
                
                # Acceleration z-score
                ac_mean = sum(cls._accel_history) / len(cls._accel_history)
                ac_var = sum((x - ac_mean) ** 2 for x in cls._accel_history) / len(cls._accel_history)
                ac_std = ac_var ** 0.5 if ac_var > 0 else 0.001
                
                z_accel = (avg_accel - ac_mean) / ac_std
                
                accel_state = "neutral"
                if z_accel > 2.0:
                    accel_state = "collapsing" if slope_state in ("weakening", "collapsing", "negative") else "accelerating"
                elif z_accel > 1.0:
                    accel_state = "accelerating"
                elif z_accel < -1.0:
                    accel_state = "decelerating"
                elif z_accel < -2.0:
                    accel_state = "stable"
                # else: neutral
            else:
                # Conservative fallback with less aggressive thresholds when history < 20
                slope_state = "neutral"
                if decay_ratio < 0.2 and declining_pct > 0.75:
                    slope_state = "collapsing"
                elif decay_ratio < 0.4 and declining_pct > 0.65:
                    slope_state = "weakening"
                elif decay_ratio < 0.6 and declining_pct > 0.55:
                    slope_state = "negative"
                elif decay_ratio > 1.5 and declining_pct < 0.25:
                    slope_state = "strengthening"
                # else: neutral (default, NOT weakening)
                
                accel_state = "neutral"
                if high_accel_threshold > 0:
                    if avg_accel > high_accel_threshold * 0.7:
                        accel_state = "collapsing" if slope_state in ("weakening", "collapsing", "negative") else "accelerating"
                    elif avg_accel > high_accel_threshold * 0.35:
                        accel_state = "accelerating"
                # else: neutral

            result["metrics"]["gamma_slope"] = avg_slope
            result["metrics"]["gamma_slope_decay_ratio"] = round(decay_ratio, 4)
            result["metrics"]["gamma_slope_asymmetry"] = round(asymmetry, 4)
            result["metrics"]["gamma_slope_declining_pct"] = round(declining_pct, 4)
            result["metrics"]["gamma_slope_state"] = slope_state
            result["metrics"]["gamma_acceleration"] = avg_accel
            result["metrics"]["gamma_acceleration_state"] = accel_state
            result["metrics"]["gamma_history_samples"] = len(cls._decay_ratio_history)

        except Exception as e:
            result["status"] = "error"
            result["warnings"].append(f"Gamma Surface calculation failed: {str(e)}")

        return result

