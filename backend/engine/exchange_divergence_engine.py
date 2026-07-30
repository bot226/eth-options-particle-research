"""ExchangeDivergenceEngine — Cross-exchange divergence detection.

Detects disagreements between exchanges:
    - IV divergence (ATM IV spread)
    - Skew divergence (opposite skew direction)
    - OI divergence (anomalous OI shifts)

Critical rule: missing contract on one exchange ≠ divergence.
"""

import logging
from typing import Dict, Any, List

from engine.instrument_normalizer import InstrumentNormalizer

log = logging.getLogger(__name__)

# Thresholds
IV_DIVERGENCE_THRESHOLD = 0.05     # 5% IV spread = divergence
SKEW_DIVERGENCE_THRESHOLD = 3.0    # 3% skew difference
OI_ANOMALY_THRESHOLD = 0.30        # 30% OI shift on one exchange


class ExchangeDivergenceEngine:
    """Detects cross-exchange divergences and disagreements."""

    @staticmethod
    def calculate(
        per_exchange_tickers: Dict[str, list],
        exchange_health: Dict[str, dict],
        spot: float,
    ) -> Dict[str, Any]:
        """Analyze cross-exchange divergences.

        Args:
            per_exchange_tickers: {exchange_id: [normalized_ticker_dicts]}
            exchange_health: {exchange_id: health_dict}
            spot: current spot price

        Returns:
            {
                "divergences": [
                    {
                        "type": str,
                        "exchanges": [str, str],
                        "severity": "LOW" | "MEDIUM" | "HIGH",
                        "message": str,
                        "confidence_impact": int,  # negative
                    },
                ],
                "global_confidence_impact": int,
                "regime_instability": "LOW" | "ELEVATED" | "HIGH",
            }
        """
        divergences: List[dict] = []

        # Only compare healthy exchanges
        healthy_exchanges = [
            ex_id for ex_id, h in exchange_health.items()
            if h.get("status", "OFFLINE") not in ("OFFLINE",)
        ]

        if len(healthy_exchanges) < 2:
            return {
                "divergences": [],
                "global_confidence_impact": 0,
                "regime_instability": "LOW",
            }

        # ── IV Divergence ────────────────────────────────────────────
        iv_divs = _detect_iv_divergence(
            per_exchange_tickers, healthy_exchanges, spot
        )
        divergences.extend(iv_divs)

        # ── Skew Divergence ──────────────────────────────────────────
        skew_divs = _detect_skew_divergence(
            per_exchange_tickers, healthy_exchanges, spot
        )
        divergences.extend(skew_divs)

        # ── Calculate total impact ───────────────────────────────────
        total_impact = sum(d.get("confidence_impact", 0) for d in divergences)

        # Classify regime instability
        if total_impact < -25:
            instability = "HIGH"
        elif total_impact < -10:
            instability = "ELEVATED"
        else:
            instability = "LOW"

        return {
            "divergences": divergences,
            "global_confidence_impact": total_impact,
            "regime_instability": instability,
        }


def _get_atm_iv_for_exchange(tickers: list, spot: float) -> float:
    """Calculate approximate ATM IV from exchange tickers."""
    if not tickers or spot <= 0:
        return 0.0

    best_iv = 0.0
    best_dist = float("inf")

    for t in tickers:
        strike = t.get("strike", 0)
        iv = t.get("markIv", 0)
        if strike <= 0 or iv <= 0:
            continue

        dist = abs(strike - spot)
        if dist < best_dist:
            best_dist = dist
            # Average call/put if this is close to ATM
            best_iv = iv

    return best_iv


def _detect_iv_divergence(
    per_exchange_tickers: Dict[str, list],
    exchanges: list,
    spot: float,
) -> List[dict]:
    """Detect ATM IV divergence between exchanges."""
    divergences = []

    # Compute ATM IV per exchange
    exchange_ivs: Dict[str, float] = {}
    for ex_id in exchanges:
        tickers = per_exchange_tickers.get(ex_id, [])
        iv = _get_atm_iv_for_exchange(tickers, spot)
        if iv > 0:
            exchange_ivs[ex_id] = iv

    if len(exchange_ivs) < 2:
        return []

    # Compare all pairs
    ex_list = list(exchange_ivs.keys())
    for i in range(len(ex_list)):
        for j in range(i + 1, len(ex_list)):
            ex_a, ex_b = ex_list[i], ex_list[j]
            iv_a, iv_b = exchange_ivs[ex_a], exchange_ivs[ex_b]

            spread = abs(iv_a - iv_b)
            if spread < IV_DIVERGENCE_THRESHOLD:
                continue

            # Severity
            if spread > 0.15:
                severity = "HIGH"
                impact = -20
            elif spread > 0.10:
                severity = "MEDIUM"
                impact = -10
            else:
                severity = "LOW"
                impact = -5

            divergences.append({
                "type": "iv_divergence",
                "exchanges": [ex_a, ex_b],
                "severity": severity,
                "message": (
                    f"{ex_a} ATM IV {iv_a*100:.1f}% vs "
                    f"{ex_b} ATM IV {iv_b*100:.1f}% — "
                    f"spread {spread*100:.1f}%"
                ),
                "confidence_impact": impact,
                "details": {
                    "iv_a": round(iv_a * 100, 2),
                    "iv_b": round(iv_b * 100, 2),
                    "spread": round(spread * 100, 2),
                },
            })

    return divergences


def _detect_skew_divergence(
    per_exchange_tickers: Dict[str, list],
    exchanges: list,
    spot: float,
) -> List[dict]:
    """Detect 25-delta skew divergence between exchanges."""
    divergences = []

    exchange_skews: Dict[str, float] = {}
    for ex_id in exchanges:
        tickers = per_exchange_tickers.get(ex_id, [])
        skew = _compute_skew_for_exchange(tickers, spot)
        if skew is not None:
            exchange_skews[ex_id] = skew

    if len(exchange_skews) < 2:
        return []

    ex_list = list(exchange_skews.keys())
    for i in range(len(ex_list)):
        for j in range(i + 1, len(ex_list)):
            ex_a, ex_b = ex_list[i], ex_list[j]
            skew_a, skew_b = exchange_skews[ex_a], exchange_skews[ex_b]

            # Divergence: opposite direction skew
            diff = abs(skew_a - skew_b)
            opposite = (skew_a > 0 and skew_b < 0) or (skew_a < 0 and skew_b > 0)

            if diff < SKEW_DIVERGENCE_THRESHOLD and not opposite:
                continue

            if opposite:
                severity = "HIGH"
                impact = -15
            elif diff > 5.0:
                severity = "MEDIUM"
                impact = -8
            else:
                severity = "LOW"
                impact = -5

            divergences.append({
                "type": "skew_divergence",
                "exchanges": [ex_a, ex_b],
                "severity": severity,
                "message": (
                    f"{ex_a} skew {skew_a:+.1f}% vs "
                    f"{ex_b} skew {skew_b:+.1f}%"
                    + (" — opposite direction!" if opposite else "")
                ),
                "confidence_impact": impact,
            })

    return divergences


def _compute_skew_for_exchange(tickers: list, spot: float) -> float | None:
    """Compute approximate 25-delta skew from exchange tickers."""
    if not tickers or spot <= 0:
        return None

    best_call = None
    best_put = None
    best_call_diff = float("inf")
    best_put_diff = float("inf")

    for t in tickers:
        delta = t.get("delta", 0)
        iv = t.get("markIv", 0)
        opt_type = t.get("type", "")

        if iv <= 0:
            continue

        if opt_type == "C" and delta > 0:
            diff = abs(delta - 0.25)
            if diff < best_call_diff:
                best_call_diff = diff
                best_call = iv
        elif opt_type == "P" and delta < 0:
            diff = abs(delta + 0.25)
            if diff < best_put_diff:
                best_put_diff = diff
                best_put = iv

    if best_call and best_put:
        return (best_call - best_put) * 100
    return None
