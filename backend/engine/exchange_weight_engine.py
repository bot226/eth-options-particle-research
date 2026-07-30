"""ExchangeWeightEngine — Weighted exchange influence with latency-awareness.

Calculates dynamic weights for each exchange based on:
    FinalWeight_i = (OIWeight_i × 0.7 + VolumeWeight_i × 0.2 + QualityFactor_i × 0.1) × latency_decay_i

Deribit minimum weight floor (40%) applies ONLY to WEIGHTED metrics
(IV, Skew, Term Structure, etc.), NOT to SUMMATION metrics (OI, GEX, Volume).

Latency decay:
    latency > 2000ms  → weight × 0.75
    stale > 5s        → weight × 0.50
    stale > 10s       → weight = 0 (excluded)
"""

import logging
from typing import Dict, Optional

from config import (
    EXCHANGE_QUALITY_FACTORS,
    WEIGHT_OI_COEFF, WEIGHT_VOLUME_COEFF, WEIGHT_QUALITY_COEFF,
    DERIBIT_MIN_WEIGHT_FLOOR,
    DEGRADED_LATENCY_MS, STALE_THRESHOLD_SECONDS, OFFLINE_THRESHOLD_SECONDS,
)

log = logging.getLogger(__name__)


class ExchangeWeightEngine:
    """Computes exchange weights with latency decay and institutional floor."""

    @staticmethod
    def calculate(exchange_data: Dict[str, dict]) -> dict:
        """Calculate weights for all exchanges.

        Args:
            exchange_data: {
                exchange_id: {
                    "total_oi": float,
                    "total_volume": float,
                    "quality_factor": float,
                    "latency_ms": float,
                    "stale_seconds": float,
                    "status": str,  # ONLINE | DEGRADED | DELAYED | OFFLINE
                }
            }

        Returns:
            {
                "weights": {exchange_id: float},           # for WEIGHTED metrics
                "summation_mask": {exchange_id: bool},     # which exchanges contribute to SUMMATION
                "raw_weights": {exchange_id: float},       # before floor/renormalization
                "excluded": [exchange_id, ...],            # excluded due to stale/offline
                "active_count": int,
                "total_count": int,
            }
        """
        if not exchange_data:
            return {
                "weights": {},
                "summation_mask": {},
                "raw_weights": {},
                "excluded": [],
                "active_count": 0,
                "total_count": 0,
            }

        total_count = len(exchange_data)
        excluded = []
        active = {}

        # ── Step 1: Filter out offline/stale exchanges ───────────────
        for ex_id, data in exchange_data.items():
            stale = data.get("stale_seconds", 0)
            status = data.get("status", "OFFLINE")

            if status == "OFFLINE" or stale > OFFLINE_THRESHOLD_SECONDS:
                excluded.append(ex_id)
                continue

            active[ex_id] = data

        if not active:
            # All exchanges excluded — emergency: include best available
            log.warning("All exchanges excluded! Returning equal weights.")
            weights = {ex_id: 1.0 / total_count for ex_id in exchange_data}
            return {
                "weights": weights,
                "summation_mask": {ex_id: True for ex_id in exchange_data},
                "raw_weights": weights,
                "excluded": [],
                "active_count": total_count,
                "total_count": total_count,
            }

        # ── Step 2: Compute base weights ─────────────────────────────
        total_oi = sum(d.get("total_oi", 0) for d in active.values())
        total_vol = sum(d.get("total_volume", 0) for d in active.values())
        total_quality = sum(d.get("quality_factor", 1.0) for d in active.values())

        raw_weights = {}
        for ex_id, data in active.items():
            oi_w = (data.get("total_oi", 0) / total_oi) if total_oi > 0 else 0
            vol_w = (data.get("total_volume", 0) / total_vol) if total_vol > 0 else 0
            q_w = (data.get("quality_factor", 1.0) / total_quality) if total_quality > 0 else 0

            base = (oi_w * WEIGHT_OI_COEFF +
                    vol_w * WEIGHT_VOLUME_COEFF +
                    q_w * WEIGHT_QUALITY_COEFF)

            raw_weights[ex_id] = base

        # ── Step 3: Apply latency decay ──────────────────────────────
        decayed = {}
        for ex_id, base_w in raw_weights.items():
            decay = _latency_decay(active[ex_id])
            decayed[ex_id] = base_w * decay

        # ── Step 4: Apply Deribit weight floor (WEIGHTED metrics) ────
        weights_for_weighted = _apply_deribit_floor(decayed)

        # ── Step 5: Normalize ────────────────────────────────────────
        weights_for_weighted = _normalize_weights(weights_for_weighted)

        # ── Step 6: Summation mask (all non-excluded exchanges) ──────
        summation_mask = {ex_id: True for ex_id in active}
        for ex_id in excluded:
            summation_mask[ex_id] = False

        return {
            "weights": weights_for_weighted,
            "summation_mask": summation_mask,
            "raw_weights": raw_weights,
            "excluded": excluded,
            "active_count": len(active),
            "total_count": total_count,
        }


def _latency_decay(data: dict) -> float:
    """Compute latency decay factor.

    Returns multiplier in [0.0, 1.0].
    """
    latency_ms = data.get("latency_ms", 0)
    stale_s = data.get("stale_seconds", 0)

    decay = 1.0

    if stale_s > OFFLINE_THRESHOLD_SECONDS:
        return 0.0
    elif stale_s > STALE_THRESHOLD_SECONDS:
        decay *= 0.50
    elif latency_ms > DEGRADED_LATENCY_MS:
        decay *= 0.75

    return decay


def _apply_deribit_floor(weights: dict) -> dict:
    """Apply Deribit minimum weight floor.

    If Deribit is present and its weight < DERIBIT_MIN_WEIGHT_FLOOR,
    raise it to the floor and renormalize others proportionally.
    """
    if "deribit" not in weights:
        return dict(weights)

    result = dict(weights)
    deribit_w = result.get("deribit", 0)
    floor = DERIBIT_MIN_WEIGHT_FLOOR

    if deribit_w >= floor:
        return result

    # Raise Deribit to floor
    result["deribit"] = floor

    # Redistribute remaining weight proportionally
    remaining = 1.0 - floor
    others_sum = sum(w for ex_id, w in weights.items() if ex_id != "deribit")

    if others_sum > 0:
        for ex_id in result:
            if ex_id != "deribit":
                result[ex_id] = weights[ex_id] / others_sum * remaining
    else:
        # Deribit is the only exchange
        result["deribit"] = 1.0

    return result


def _normalize_weights(weights: dict) -> dict:
    """Normalize weights to sum to 1.0."""
    total = sum(weights.values())
    if total <= 0:
        n = len(weights)
        return {k: 1.0 / n for k in weights} if n > 0 else {}
    return {k: v / total for k, v in weights.items()}
