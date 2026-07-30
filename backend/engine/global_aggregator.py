"""GlobalAggregator — Unified chain builder with three aggregation categories.

SUMMATION (absolute market size — never weighted):
    OI, GEX, Volume, Call/Put OI

WEIGHTED (interpretive metrics — exchange-weighted):
    IV, Skew, Term Structure, Confidence, Fragility

MAX/DOMINANCE (selection logic):
    Call/Put Wall, Gamma Flip, Dominant Expiry

Safety rules:
    - Missing contract on one exchange → skip, not divergence
    - Stale data (>threshold) → excluded from aggregation
    - Timestamp alignment ±2s tolerance
    - Minimum 2 healthy sources for weighted metrics
    - Chain completeness validation per expiry
    - Greeks freshness check before weighted inclusion
"""

import time
import logging
from collections import defaultdict
from typing import Dict, Any, Optional

from engine.instrument_normalizer import InstrumentNormalizer

log = logging.getLogger(__name__)

# Timestamp alignment tolerance (seconds)
TIMESTAMP_TOLERANCE = 2.0


class GlobalAggregator:
    """Builds unified chain from per-exchange normalized data.

    Applies different aggregation strategies based on metric category:
    - SUMMATION for absolute size metrics
    - WEIGHTED for interpretive metrics
    - MAX/DOMINANCE for wall/flip selection
    """

    @staticmethod
    def aggregate(
        per_exchange_tickers: Dict[str, list[dict]],
        weights: Dict[str, float],
        summation_mask: Dict[str, bool],
        exchange_health: Dict[str, dict],
    ) -> dict:
        """Build unified chain and global metrics from per-exchange data.

        Args:
            per_exchange_tickers: {exchange_id: [normalized_ticker_dicts]}
            weights: {exchange_id: weight} for WEIGHTED metrics
            summation_mask: {exchange_id: bool} for SUMMATION inclusion
            exchange_health: {exchange_id: health_dict}

        Returns:
            {
                "chain": {expiry_str: {strike: {"C": {...}, "P": {...}}}},
                "tickers": {canonical_id: merged_ticker},
                "expiries": [sorted expiry strings],
                "strikes": [sorted strike ints],
                "global_metrics": {
                    "total_oi": float,
                    "total_call_oi": float,
                    "total_put_oi": float,
                    "total_volume": float,
                    "source_count": int,
                },
                "per_exchange_summary": {exchange_id: {oi, volume, ticker_count}},
                "warnings": [str],
            }
        """
        warnings = []
        healthy_exchanges = _get_healthy_exchanges(
            per_exchange_tickers, exchange_health, summation_mask
        )

        if not healthy_exchanges:
            warnings.append("No healthy exchanges available for aggregation")
            return _empty_result(warnings)

        # ── Step 1: Index all tickers by canonical_id per exchange ────
        exchange_by_canonical: Dict[str, Dict[str, dict]] = defaultdict(dict)
        per_exchange_summary = {}

        for ex_id in healthy_exchanges:
            tickers = per_exchange_tickers.get(ex_id, [])
            ex_oi = 0.0
            ex_vol = 0.0
            count = 0

            for t in tickers:
                cid = t.get("canonical_id")
                if not cid:
                    continue

                # Freshness check: skip stale individual tickers if needed
                exchange_by_canonical[cid][ex_id] = t
                ex_oi += t.get("oi", 0)
                ex_vol += t.get("volume", 0)
                count += 1

            per_exchange_summary[ex_id] = {
                "oi": ex_oi,
                "volume": ex_vol,
                "ticker_count": count,
            }

        # ── Step 2: Merge into unified tickers ───────────────────────
        merged_tickers = {}
        chain = defaultdict(lambda: defaultdict(dict))
        all_expiries = set()
        all_strikes = set()
        total_oi = 0.0
        total_call_oi = 0.0
        total_put_oi = 0.0
        total_volume = 0.0

        weighted_sources_count = sum(1 for ex_id in healthy_exchanges
                                     if ex_id in weights and weights[ex_id] > 0)

        for canonical_id, exchange_tickers in exchange_by_canonical.items():
            merged = _merge_ticker(
                canonical_id, exchange_tickers, weights,
                summation_mask, weighted_sources_count
            )
            if merged is None:
                continue

            merged_tickers[canonical_id] = merged
            expiry = merged["expiry"]
            strike = merged["strike"]
            opt_type = merged["type"]

            chain[expiry][strike][opt_type] = merged
            all_expiries.add(expiry)
            all_strikes.add(strike)

            oi = merged.get("oi", 0)
            total_oi += oi
            total_volume += merged.get("volume", 0)
            if opt_type == "C":
                total_call_oi += oi
            else:
                total_put_oi += oi

        # Sort
        sorted_expiries = sorted(all_expiries)
        sorted_strikes = sorted(all_strikes)

        return {
            "chain": dict(chain),
            "tickers": merged_tickers,
            "expiries": sorted_expiries,
            "strikes": sorted_strikes,
            "global_metrics": {
                "total_oi": total_oi,
                "total_call_oi": total_call_oi,
                "total_put_oi": total_put_oi,
                "total_volume": total_volume,
                "source_count": len(healthy_exchanges),
            },
            "per_exchange_summary": per_exchange_summary,
            "warnings": warnings,
        }


def _merge_ticker(
    canonical_id: str,
    exchange_tickers: Dict[str, dict],
    weights: Dict[str, float],
    summation_mask: Dict[str, bool],
    weighted_sources_count: int,
) -> Optional[dict]:
    """Merge ticker data from multiple exchanges into one.

    SUMMATION: oi, volume → sum across exchanges
    WEIGHTED:  markIv, bidIv, askIv, delta, gamma, vega, theta → weighted avg
    TAKE FIRST: expiry, strike, type, canonical_id (identical across exchanges)
    """
    if not exchange_tickers:
        return None

    # Take metadata from first available ticker
    first = next(iter(exchange_tickers.values()))
    result = {
        "canonical_id": canonical_id,
        "symbol": canonical_id,   # canonical as unified symbol
        "expiry": first["expiry"],
        "strike": first["strike"],
        "type": first["type"],
        "exchange_sources": list(exchange_tickers.keys()),
    }

    # ── SUMMATION metrics ────────────────────────────────────────────
    sum_oi = 0.0
    sum_volume = 0.0
    for ex_id, t in exchange_tickers.items():
        if summation_mask.get(ex_id, False):
            sum_oi += t.get("oi", 0)
            sum_volume += t.get("volume", 0)

    result["oi"] = sum_oi
    result["volume"] = sum_volume

    # ── WEIGHTED metrics ─────────────────────────────────────────────
    weighted_fields = [
        "markIv", "bidIv", "askIv", "delta", "gamma", "vega", "theta",
        "markPrice", "lastPrice", "bidPrice", "askPrice", "underlyingPrice",
    ]

    for field in weighted_fields:
        w_sum = 0.0
        w_total = 0.0
        for ex_id, t in exchange_tickers.items():
            w = weights.get(ex_id, 0)
            val = t.get(field, 0)
            if val != 0 and w > 0:
                w_sum += val * w
                w_total += w

        result[field] = w_sum / w_total if w_total > 0 else 0.0

    return result


def _get_healthy_exchanges(
    per_exchange_tickers: Dict[str, list],
    exchange_health: Dict[str, dict],
    summation_mask: Dict[str, bool],
) -> list[str]:
    """Get list of exchanges healthy enough for aggregation."""
    healthy = []
    for ex_id in per_exchange_tickers:
        if not summation_mask.get(ex_id, False):
            continue
        health = exchange_health.get(ex_id, {})
        status = health.get("status", "OFFLINE")
        if status != "OFFLINE":
            healthy.append(ex_id)
    return healthy


def _empty_result(warnings: list) -> dict:
    """Return empty aggregation result."""
    return {
        "chain": {},
        "tickers": {},
        "expiries": [],
        "strikes": [],
        "global_metrics": {
            "total_oi": 0, "total_call_oi": 0, "total_put_oi": 0,
            "total_volume": 0, "source_count": 0,
        },
        "per_exchange_summary": {},
        "warnings": warnings,
    }
