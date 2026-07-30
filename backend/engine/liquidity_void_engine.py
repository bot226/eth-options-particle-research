"""Liquidity Void Engine — Detects true structural market weakness.

A true liquidity void considers multiple factors with weighted partial scoring:
- Low Open Interest
- Low Gamma
- Weak dealer support (low traded volume)
- Wide strike spacing
- Fast delta transition
- Proximity to spot

Outputs a composite void_score (0-100) reflecting structural fragility.
Uses max(contributions) + count_bonus + rolling smoothing.

Each void zone is scored independently with a multi-factor weighted model:
  proximity       25%
  gamma_weakness   25%
  width            15%
  OI weakness      15%
  volume weakness  10%
  strike spacing   10%

Partial voids (not all conditions met) are included with reduced scores.
Debug breakdown is available via get_debug() for endpoint inspection.
"""

from collections import deque
from typing import Dict, Any, List


class LiquidityVoidEngine:
    # Rolling average state for smoothing (2-snapshot window for more responsive signal)
    _score_history: List[float] = []
    _prev_void_score: float = 0.0
    _last_debug: dict = {}
    _debug_history = deque(maxlen=1000)
    _last_gamma_slope_state: str = "neutral"

    @classmethod
    def calculate(cls, chain: dict, spot: float, gamma_surface: dict) -> Dict[str, Any]:
        result = {
            "status": "ok",
            "metrics": {
                "total_void_count": 0,
                "void_score": 0.0,
                "void_score_raw": 0.0,
                "prev_void_score": cls._prev_void_score,
            },
            "features": {
                "true_voids": []
            },
            "warnings": []
        }

        if not chain or spot <= 0:
            result["status"] = "degraded"
            result["warnings"].append("Missing chain or spot data for Liquidity Void Engine")
            cls._apply_smoothing(result, 0.0)
            cls._store_debug(result, {})
            return result

        try:
            from engine.calculator import Calculator

            # 1. Aggregate per-strike liquidity and spacing
            strike_data = {}
            strikes = []

            for expiry, s_data in chain.items():
                for strike, data in s_data.items():
                    if strike not in strike_data:
                        strike_data[strike] = {
                            "oi": 0,
                            "volume": 0,
                            "delta_change": 0
                        }
                        strikes.append(strike)

                    c = data.get("C", {})
                    p = data.get("P", {})
                    strike_data[strike]["oi"] += c.get("oi", 0) + p.get("oi", 0)
                    strike_data[strike]["volume"] += c.get("volume", 0) + p.get("volume", 0)
                    # Rough delta transition: difference between call delta and put delta magnitudes
                    cd = abs(c.get("delta", 0))
                    pd = abs(p.get("delta", 0))
                    strike_data[strike]["delta_change"] = cd + pd

            strikes = sorted(list(set(strikes)))
            if not strikes:
                cls._apply_smoothing(result, 0.0)
                cls._store_debug(result, {})
                return result

            # Find medians for "low" thresholding
            ois = [d["oi"] for d in strike_data.values()]
            vols = [d["volume"] for d in strike_data.values()]
            median_oi = sorted(ois)[len(ois)//2] if ois else 1
            median_vol = sorted(vols)[len(vols)//2] if vols else 1

            low_oi_threshold = median_oi * 0.3
            low_vol_threshold = median_vol * 0.3

            # Incorporate gamma surface voids
            gamma_voids = gamma_surface.get("features", {}).get("gamma_voids", [])
            # Cache gamma_slope_state for fallback
            cls._last_gamma_slope_state = gamma_surface.get("metrics", {}).get("gamma_slope_state", "neutral")

            def is_in_gamma_void(s):
                for v in gamma_voids:
                    if v["start"] <= s <= v["end"]:
                        return True
                return False

            # ── Weighted partial scoring per strike ────────────────────
            # Instead of hard all-conditions logic, score each strike zone
            void_candidates = []
            void_strike_data = []

            for i in range(1, len(strikes)):
                s_prev = strikes[i-1]
                s_curr = strikes[i]
                spacing_pct = (s_curr - s_prev) / s_prev * 100

                data = strike_data[s_curr]
                
                # Calculate partial scores for each factor (0-1)
                wide_spacing = spacing_pct > 1.5
                is_low_oi = data["oi"] < low_oi_threshold
                is_low_vol = data["volume"] < low_vol_threshold
                is_low_gamma = is_in_gamma_void(s_curr)
                fast_delta = data["delta_change"] > 1.0

                # Partial match scoring: count matching conditions
                match_score = 0
                if wide_spacing or is_low_oi:
                    match_score += 1
                if is_low_gamma:
                    match_score += 1
                if is_low_vol:
                    match_score += 1
                if fast_delta:
                    match_score += 1

                # Require at least 2 out of 4 conditions for partial void
                if match_score >= 2:
                    void_candidates.append(s_curr)
                    void_strike_data.append({
                        "strike": s_curr,
                        "oi": data["oi"],
                        "volume": data["volume"],
                        "spacing_pct": spacing_pct,
                        "match_score": match_score,
                        "is_low_oi": is_low_oi,
                        "is_low_gamma": is_low_gamma,
                        "is_low_vol": is_low_vol,
                        "fast_delta": fast_delta,
                        "wide_spacing": wide_spacing,
                    })

            # Group continuous void candidates
            def _group_ranges(void_strikes):
                if not void_strikes:
                    return []
                ranges = []
                start = void_strikes[0]
                prev = void_strikes[0]
                for s in void_strikes[1:]:
                    if s - prev <= spot * 0.05:
                        prev = s
                    else:
                        ranges.append({"start": start, "end": prev})
                        start = s
                        prev = s
                ranges.append({"start": start, "end": prev})
                return ranges

            grouped_voids = _group_ranges(void_candidates)

            result["features"]["true_voids"] = grouped_voids
            result["metrics"]["total_void_count"] = len(grouped_voids)

            # ── Normalized Void Score (0-100) ─────────────────────────
            best_void_score = 0.0
            best_breakdown = {}

            for void in grouped_voids:
                void_center = (void["start"] + void["end"]) / 2
                void_width_pct = (void["end"] - void["start"]) / spot * 100
                dist_pct = abs(void_center - spot) / spot * 100

                # 1. Proximity multiplier (0.0 - 1.0): determines how dangerous this void is
                #    Close to spot = full danger, far = near-zero danger
                if dist_pct <= 5.0:
                    proximity_mult = 1.0
                elif dist_pct <= 15.0:
                    proximity_mult = 0.8 - (dist_pct - 5.0) * 0.03  # 0.8→0.5
                elif dist_pct <= 30.0:
                    proximity_mult = 0.5 - (dist_pct - 15.0) * 0.02  # 0.5→0.2
                elif dist_pct <= 60.0:
                    proximity_mult = 0.2 - (dist_pct - 30.0) * 0.005  # 0.2→0.05
                else:
                    proximity_mult = max(0.02, 0.05 - (dist_pct - 60.0) * 0.001)  # tiny

                # 2. Width score (wider void = more dangerous)
                width_score = min(100.0, void_width_pct * 12.5)

                # 3. Gamma weakness
                void_strikes_in_range = [
                    s for s in void_candidates
                    if void["start"] <= s <= void["end"]
                ]
                gamma_weakness_source = "gamma_voids"
                if void_strikes_in_range and gamma_voids:
                    gamma_void_count = sum(1 for s in void_strikes_in_range if is_in_gamma_void(s))
                    gamma_ratio = gamma_void_count / len(void_strikes_in_range)
                    gamma_weakness = gamma_ratio * 100.0
                elif void_strikes_in_range and not gamma_voids:
                    # Fallback: use gamma_slope_state from GammaSurfaceEngine
                    _slope = cls._last_gamma_slope_state
                    gamma_weakness = {"collapsing": 80.0, "weakening": 60.0, "negative": 40.0}.get(_slope, 0.0)
                    gamma_weakness_source = "state_fallback"
                else:
                    gamma_weakness = 0.0
                    gamma_weakness_source = "no_strikes"

                # 4. OI collapse
                if void_strikes_in_range and median_oi > 0:
                    avg_oi = sum(strike_data[s]["oi"] for s in void_strikes_in_range) / len(void_strikes_in_range)
                    oi_ratio = avg_oi / median_oi
                    oi_weakness = max(0.0, (1.0 - oi_ratio)) * 100
                else:
                    oi_weakness = 0.0

                # 5. Volume weakness
                if void_strikes_in_range and median_vol > 0:
                    avg_vol = sum(strike_data[s]["volume"] for s in void_strikes_in_range) / len(void_strikes_in_range)
                    vol_ratio = avg_vol / median_vol
                    vol_weakness = max(0.0, (1.0 - vol_ratio)) * 100
                else:
                    vol_weakness = 0.0

                # 6. Strike spacing score
                void_spacings = [
                    vsd["spacing_pct"] for vsd in void_strike_data
                    if void["start"] <= vsd["strike"] <= void["end"]
                ]
                avg_spacing = sum(void_spacings) / len(void_spacings) if void_spacings else 0
                spacing_score = min(100.0, max(0.0, (avg_spacing - 1.5) / 4.0 * 100))

                # 7. Match quality boost (partial vs full match)
                match_scores = [
                    vsd["match_score"] for vsd in void_strike_data
                    if void["start"] <= vsd["strike"] <= void["end"]
                ]
                avg_match = sum(match_scores) / len(match_scores) if match_scores else 2
                match_count = len(match_scores)
                # Stepped match_quality: no artificial floor
                # avg_match >= 3 → strong match; ~2 → moderate; <2 → weak
                if avg_match >= 3.0:
                    match_quality = min(1.0, avg_match / 4.0)
                elif avg_match >= 2.0:
                    match_quality = 0.45
                elif avg_match >= 1.5:
                    match_quality = 0.30
                else:
                    match_quality = 0.15

                # proximity_mult floor ONLY with structural confirmation
                if dist_pct <= 10.0 and match_count >= 2:
                    proximity_mult = max(proximity_mult, 0.6)

                # Structural weakness score (proximity-independent)
                structural_weakness = (
                    gamma_weakness * 0.30 +
                    oi_weakness * 0.25 +
                    vol_weakness * 0.15 +
                    width_score * 0.20 +
                    spacing_score * 0.10
                ) * match_quality

                # Final void score: structural weakness × proximity multiplier
                void_contribution = structural_weakness * proximity_mult

                if void_contribution > best_void_score:
                    best_void_score = void_contribution
                    best_breakdown = {
                        "proximity_multiplier": round(proximity_mult, 3),
                        "structural_weakness": round(structural_weakness, 1),
                        "width_score": round(width_score, 1),
                        "gamma_weakness_score": round(gamma_weakness, 1),
                        "gamma_weakness_source": gamma_weakness_source,
                        "gamma_voids_count": len(gamma_voids),
                        "oi_weakness_score": round(oi_weakness, 1),
                        "volume_weakness_score": round(vol_weakness, 1),
                        "strike_spacing_score": round(spacing_score, 1),
                        "match_quality": round(match_quality, 3),
                        "avg_match_score": round(avg_match, 2),
                        "match_count_in_zone": match_count,
                        "void_center": round(void_center, 0),
                        "dist_to_spot_pct": round(dist_pct, 2),
                    }

            # Count bonus: +5 per additional void, max +15
            count_bonus = min(15, (len(grouped_voids) - 1) * 5) if len(grouped_voids) > 1 else 0
            raw_score = min(100.0, best_void_score + count_bonus)

            result["metrics"]["void_score_raw"] = round(raw_score, 1)

            # Apply 2-snapshot rolling average smoothing (less aggressive)
            cls._apply_smoothing(result, raw_score)
            
            # Store debug breakdown
            best_breakdown["raw_score"] = round(raw_score, 1)
            best_breakdown["count_bonus"] = count_bonus
            best_breakdown["pre_smooth_score"] = round(raw_score, 1)
            best_breakdown["smoothed_score"] = result["metrics"]["void_score"]
            best_breakdown["void_count"] = len(grouped_voids)
            best_breakdown["candidate_strikes"] = len(void_candidates)
            cls._store_debug(result, best_breakdown)

        except Exception as e:
            result["status"] = "error"
            result["warnings"].append(f"Liquidity Void calculation failed: {str(e)}")

        return result

    @classmethod
    def _apply_smoothing(cls, result: dict, raw_score: float):
        """Apply 2-snapshot rolling average and track previous score."""
        cls._prev_void_score = cls._score_history[-1] if cls._score_history else 0.0

        cls._score_history.append(raw_score)
        if len(cls._score_history) > 2:
            cls._score_history = cls._score_history[-2:]

        smoothed = sum(cls._score_history) / len(cls._score_history)
        result["metrics"]["void_score"] = round(min(100.0, smoothed), 1)
        result["metrics"]["prev_void_score"] = cls._prev_void_score

    @classmethod
    def _store_debug(cls, result: dict, breakdown: dict):
        """Store debug breakdown for debug endpoint — spec format."""
        void_score = result["metrics"].get("void_score", 0)
        void_score_raw = result["metrics"].get("void_score_raw", 0)
        prev_score = cls._prev_void_score

        # Reason explaining why score is at its level
        if not breakdown:
            reason = "no_void_candidates_found"
        elif breakdown.get("match_count_in_zone", 0) == 0:
            reason = "no_structural_matches_in_zone"
        elif breakdown.get("proximity_multiplier", 0) < 0.05:
            reason = "voids_too_far_from_spot"
        elif breakdown.get("gamma_weakness_score", 0) == 0 and breakdown.get("gamma_weakness_source") in ("none", "no_strikes"):
            reason = "no_gamma_weakness_detected"
        elif void_score_raw > 0 and void_score < void_score_raw * 0.75:
            reason = "score_reduced_by_smoothing"
        elif void_score_raw > 0:
            reason = "partial_void_detected"
        else:
            reason = "structural_conditions_not_met"

        cls._last_debug = {
            # Top-level metrics (spec format)
            "liquidity_void_score": void_score,
            "raw_score": void_score_raw,
            "pre_smooth_score": breakdown.get("pre_smooth_score", void_score_raw),
            "final_score": void_score,
            "components": {
                "proximity_score": round(breakdown.get("proximity_multiplier", 0) * 100, 1),
                "width_score": breakdown.get("width_score", 0),
                "gamma_weakness_score": breakdown.get("gamma_weakness_score", 0),
                "oi_weakness_score": breakdown.get("oi_weakness_score", 0),
                "volume_weakness_score": breakdown.get("volume_weakness_score", 0),
                "strike_spacing_score": breakdown.get("strike_spacing_score", 0),
            },
            "match_quality": breakdown.get("match_quality", 0.0),
            "match_count": breakdown.get("match_count_in_zone", 0),
            "proximity_mult": breakdown.get("proximity_multiplier", 0.0),
            "count_bonus": breakdown.get("count_bonus", 0),
            "penalty": 0,  # No explicit penalty applied currently
            "smoothing": {
                "window": 2,
                "previous_score": round(prev_score, 1),
                "current_raw": void_score_raw,
            },
            "gamma_weakness_source": breakdown.get("gamma_weakness_source", "none"),
            "gamma_voids_count": breakdown.get("gamma_voids_count", 0),
            "clamp_applied": False,
            "reason": reason,
            # Extended context (additional debug info)
            "void_count": result["metrics"].get("total_void_count", 0),
            "candidate_strikes": breakdown.get("candidate_strikes", 0),
            "gamma_slope_state_used": cls._last_gamma_slope_state,
            "structural_weakness": breakdown.get("structural_weakness", 0),
            "avg_match_score": breakdown.get("avg_match_score", 0),
            "void_center": breakdown.get("void_center", 0),
            "dist_to_spot_pct": breakdown.get("dist_to_spot_pct", 0),
            "explanation": (
                "void_score = structural_weakness × proximity_mult. "
                "structural_weakness = weighted(gamma×0.30, oi×0.25, vol×0.15, width×0.20, spacing×0.10) × match_quality. "
                "gamma_weakness_source: 'gamma_voids'=actual zones, 'state_fallback'=slope-based, 'none'=no data."
            ),
        }
        cls._debug_history.append(dict(cls._last_debug))

    @classmethod
    def get_debug(cls) -> dict:
        """Return last debug breakdown for debug endpoint."""
        return dict(cls._last_debug) if cls._last_debug else {}

    @classmethod
    def get_debug_summary(cls) -> dict:
        """Return in-memory liquidity void debug summary for the current backend process."""
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

        def _avg_component(key):
            vals = [float(r.get("components", {}).get(key, 0) or 0) for r in rows]
            return round(sum(vals) / len(vals), 2) if vals else 0

        scores = [float(r.get("liquidity_void_score", 0) or 0) for r in rows]
        buckets = {"0_20": 0, "20_40": 0, "40_50": 0, "50_70": 0, "70_plus": 0}
        for score in scores:
            if score < 20:
                buckets["0_20"] += 1
            elif score < 40:
                buckets["20_40"] += 1
            elif score < 50:
                buckets["40_50"] += 1
            elif score < 70:
                buckets["50_70"] += 1
            else:
                buckets["70_plus"] += 1

        return {
            "sample_count": len(rows),
            "liquidity_void_score_min": round(min(scores), 2) if scores else 0,
            "liquidity_void_score_avg": _avg("liquidity_void_score"),
            "liquidity_void_score_max": round(max(scores), 2) if scores else 0,
            "score_bucket_distribution": buckets,
            "reason_distribution": _dist("reason"),
            "avg_raw_score": _avg("raw_score"),
            "avg_match_quality": _avg("match_quality"),
            "avg_match_count": _avg("match_count"),
            "avg_proximity_mult": _avg("proximity_mult"),
            "avg_components": {
                "proximity_score": _avg_component("proximity_score"),
                "width_score": _avg_component("width_score"),
                "gamma_weakness_score": _avg_component("gamma_weakness_score"),
                "oi_weakness_score": _avg_component("oi_weakness_score"),
                "volume_weakness_score": _avg_component("volume_weakness_score"),
                "strike_spacing_score": _avg_component("strike_spacing_score"),
            },
            "gamma_weakness_source_distribution": _dist("gamma_weakness_source"),
        }
