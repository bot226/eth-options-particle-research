"""ConfidenceEngine — Dynamic system confidence with decay factors.

Dynamically reduces system confidence when:
    - Exchanges disagree (divergence)
    - Feeds degrade (latency/stale)
    - Data becomes stale
    - Liquidity weakens
    - Sources go offline

Provides regime_stability multiplier for regime scores.
"""

import logging
from typing import Dict, Any, List

log = logging.getLogger(__name__)


class ConfidenceEngine:
    """Calculates dynamic system confidence from health and divergence data."""

    @staticmethod
    def calculate(
        exchange_health: Dict[str, dict],
        divergences: list,
        weights: dict,
        per_exchange_summary: Dict[str, dict] = None,
    ) -> Dict[str, Any]:
        """Calculate system confidence.

        Args:
            exchange_health: {exchange_id: {status, latency_ms, stale_seconds, ...}}
            divergences: list of divergence dicts from DivergenceEngine
            weights: {weights, excluded, active_count, total_count} from WeightEngine
            per_exchange_summary: {exchange_id: {oi, volume, ticker_count}}

        Returns:
            {
                "system_confidence": int,          # 0-100
                "confidence_factors": [str, ...],  # decay reasons
                "regime_stability": float,         # multiplier 0.0-1.0
                "data_quality_label": str,         # GOOD | DEGRADED | CRITICAL
            }
        """
        confidence = 100
        factors: List[str] = []
        regime_mult = 1.0

        active_count = weights.get("active_count", 0) if isinstance(weights, dict) else 0
        total_count = weights.get("total_count", 0) if isinstance(weights, dict) else 0
        excluded = weights.get("excluded", []) if isinstance(weights, dict) else []

        # ── Factor 1: Offline sources ────────────────────────────────
        for ex_id in excluded:
            confidence -= 20
            factors.append(f"{ex_id} offline (-20)")
            regime_mult *= 0.60

        # ── Factor 2: Degraded / delayed feeds ───────────────────────
        for ex_id, health in exchange_health.items():
            if ex_id in excluded:
                continue
            status = health.get("status", "ONLINE")
            if status == "DEGRADED":
                confidence -= 10
                factors.append(f"{ex_id} degraded latency (-10)")
                regime_mult *= 0.85
            elif status == "DELAYED":
                confidence -= 15
                factors.append(f"{ex_id} delayed feed (-15)")
                regime_mult *= 0.75

        # ── Factor 3: Divergences ────────────────────────────────────
        if divergences:
            for div in divergences:
                impact = div.get("confidence_impact", 0)
                if impact != 0:
                    confidence += impact  # impact is negative
                    severity = div.get("severity", "LOW")
                    div_type = div.get("type", "unknown")
                    factors.append(
                        f"{div_type} ({severity}) ({impact:+d})"
                    )
                    if severity == "HIGH":
                        regime_mult *= 0.70
                    elif severity == "MEDIUM":
                        regime_mult *= 0.85

        # ── Factor 4: Insufficient sources ───────────────────────────
        if active_count < 2 and total_count >= 2:
            confidence -= 15
            factors.append("Insufficient sources for cross-validation (-15)")

        # ── Factor 5: Chain completeness ─────────────────────────────
        if per_exchange_summary:
            for ex_id, summary in per_exchange_summary.items():
                ticker_count = summary.get("ticker_count", 0)
                if ticker_count < 10:
                    confidence -= 5
                    factors.append(f"{ex_id} thin chain ({ticker_count} tickers) (-5)")

        # ── Clamp and classify ───────────────────────────────────────
        confidence = max(0, min(100, confidence))
        regime_mult = max(0.0, min(1.0, regime_mult))

        if confidence >= 80:
            quality = "GOOD"
        elif confidence >= 50:
            quality = "DEGRADED"
        else:
            quality = "CRITICAL"

        return {
            "system_confidence": confidence,
            "confidence_factors": factors,
            "regime_stability": round(regime_mult, 3),
            "data_quality_label": quality,
        }
