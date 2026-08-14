"""Price Source Engine — MOS Manual v1.2.

Provides explicit separation between:
  - reference_price : ETH index price (preferred: Bybit index endpoint;
                      fallback: median underlyingPrice across valid ETH option tickers)
  - execution_price : Bybit linear ETHUSDT (actual futures/perp for trading)
  - basis           : execution_price - reference_price
  - basis_pct       : basis / reference_price * 100

v1.2 changes:
  - No longer takes the first arbitrary ticker underlyingPrice.
  - Collects ALL valid underlyingPrice candidates, filters outliers vs execution_price
    (threshold: abs deviation > SANITY_PCT_THRESHOLD), then uses the MEDIAN.
  - Adds reference_price_status: OK | SUSPECT | FALLBACK
  - Adds basis_valid flag (0 when reference is SUSPECT or FALLBACK).
  - Adds reference_price_source, reference_candidate_count.
  - When SUSPECT: reference_price in the result is set to execution_price (safe UI fallback)
    so that basis = 0 and no garbage numbers appear on screen.

Read-only: never touches State Machine, ExecutionTimingEngine, SignalCluster.

Usage:
    from engine.price_source_engine import PriceSourceEngine
    info = PriceSourceEngine.get(_dm)
    # info.execution_price, info.reference_price, info.basis, info.basis_pct
"""

import statistics
import time
import logging
import math
from dataclasses import dataclass, field
from typing import Optional, Any, Dict

log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

EXECUTION_VENUE   = "bybit_linear"
EXECUTION_SYMBOL  = "ETHUSDT"
REFERENCE_VENUE   = "bybit_options_index"
REFERENCE_SYMBOL  = "ETHUSD_INDEX"   # underlyingPrice from Bybit options tickers

# Sources used for different MOS Manual purposes
PRICE_SOURCE_FOR_ENTRY   = "execution_price"
PRICE_SOURCE_FOR_LEVELS  = "execution_ohlcv+reference_walls"
PRICE_SOURCE_FOR_OUTCOME = "execution_ohlcv"

_CACHE_TTL_SEC = 5.0   # seconds before re-computing

# If |candidate - execution| / execution > this → mark as SUSPECT
# 0.5% is a reasonable ETH index-to-perp deviation threshold.
# The real ETH perp-to-index basis is ±0.2% on normal days.
SANITY_PCT_THRESHOLD = 0.005   # 0.5%

# Minimum number of valid candidates required to trust median
MIN_CANDIDATES_FOR_MEDIAN = 3


# ── Result dataclass ─────────────────────────────────────────────────────────

@dataclass
class PriceSourceInfo:
    """Immutable snapshot of price source data for MOS Manual."""

    # Core prices
    reference_price: Optional[float] = None    # ETH index (median of valid tickers)
    execution_price: Optional[float] = None    # ETHUSDT linear perp

    # Basis
    basis: Optional[float] = None              # execution - reference
    basis_pct: Optional[float] = None          # basis / reference * 100

    # Venue / symbol metadata
    execution_venue:  str = EXECUTION_VENUE
    execution_symbol: str = EXECUTION_SYMBOL
    reference_venue:  str = REFERENCE_VENUE
    reference_symbol: str = REFERENCE_SYMBOL

    # Source labels (for audit / logging)
    price_source_for_entry:   str = PRICE_SOURCE_FOR_ENTRY
    price_source_for_levels:  str = PRICE_SOURCE_FOR_LEVELS
    price_source_for_outcome: str = PRICE_SOURCE_FOR_OUTCOME

    # OHLCV source transparency
    ohlcv_source: str = "bybit_linear_ethusdt"
    ohlcv_exchange: str = "bybit"
    ohlcv_market_type: str = "linear"
    ohlcv_symbol: str = "ETHUSDT"
    ohlcv_timeframe: str = "1m"
    candle_source_verified: int = 0

    # OHLCV execution sync check
    latest_execution_ohlcv_close: Optional[float] = None
    execution_price_ohlcv_diff: Optional[float] = None
    execution_price_ohlcv_diff_pct: Optional[float] = None
    execution_price_ohlcv_sync_status: str = "UNKNOWN"  # OK | WARNING | DEGRADED | UNKNOWN

    # Diagnostics
    computed_at: float = field(default_factory=time.time)
    reference_from: str = "unknown"            # how reference_price was obtained
    execution_from: str = "unknown"            # how execution_price was obtained

    # v1.2: reference sanity fields
    reference_price_status: str = "UNKNOWN"    # OK | SUSPECT | FALLBACK
    basis_valid: int = 0                       # 1 = usable, 0 = do not trust
    reference_price_source: str = "unknown"    # how median was formed
    reference_candidate_count: int = 0         # how many valid candidates were seen

    def to_dict(self) -> dict:
        """Serialise to plain dict for JSON response / snapshot logging."""
        return {
            "reference_price":                 self.reference_price,
            "execution_price":                 self.execution_price,
            "basis":                           round(self.basis, 4) if self.basis is not None else None,
            "basis_pct":                       round(self.basis_pct, 4) if self.basis_pct is not None else None,
            "execution_venue":                 self.execution_venue,
            "execution_symbol":                self.execution_symbol,
            "reference_venue":                 self.reference_venue,
            "reference_symbol":                self.reference_symbol,
            "price_source_for_entry":          self.price_source_for_entry,
            "price_source_for_levels":         self.price_source_for_levels,
            "price_source_for_outcome":        self.price_source_for_outcome,
            # OHLCV source fields
            "ohlcv_source":                    self.ohlcv_source,
            "ohlcv_exchange":                  self.ohlcv_exchange,
            "ohlcv_market_type":               self.ohlcv_market_type,
            "ohlcv_symbol":                    self.ohlcv_symbol,
            "ohlcv_timeframe":                 self.ohlcv_timeframe,
            "candle_source_verified":          self.candle_source_verified,
            # OHLCV sync check
            "latest_execution_ohlcv_close":    self.latest_execution_ohlcv_close,
            "execution_price_ohlcv_diff":      self.execution_price_ohlcv_diff,
            "execution_price_ohlcv_diff_pct":  self.execution_price_ohlcv_diff_pct,
            "execution_price_ohlcv_sync_status": self.execution_price_ohlcv_sync_status,
            # Reference diagnostics
            "reference_from":                  self.reference_from,
            "execution_from":                  self.execution_from,
            # v1.2 sanity fields
            "reference_price_status":          self.reference_price_status,
            "basis_valid":                     self.basis_valid,
            "reference_price_source":          self.reference_price_source,
            "reference_candidate_count":       self.reference_candidate_count,
        }


# ── Cache ────────────────────────────────────────────────────────────────────

_cache: Optional[PriceSourceInfo] = None
_cache_ts: float = 0.0


# ── Engine ───────────────────────────────────────────────────────────────────

class PriceSourceEngine:
    """Stateless engine — call PriceSourceEngine.get(dm) anywhere."""

    @staticmethod
    def get(dm: Any) -> PriceSourceInfo:
        """Return cached PriceSourceInfo, refreshing every _CACHE_TTL_SEC seconds.

        Parameters
        ----------
        dm : DataManager or MultiExchangeDataManager
            The live data manager. Must have .spot_price (ETHUSDT linear perp).
            May have .tickers (Bybit options) with underlyingPrice.
        """
        global _cache, _cache_ts
        now = time.time()
        if _cache is not None and (now - _cache_ts) < _CACHE_TTL_SEC:
            return _cache

        info = PriceSourceEngine._compute(dm)
        _cache = info
        _cache_ts = now
        return info

    @staticmethod
    def _compute(dm: Any) -> PriceSourceInfo:
        """Actually compute PriceSourceInfo from DataManager state."""

        # ── 1. execution_price: ETHUSDT linear perp ──────────────────────────
        execution_price: Optional[float] = None
        execution_from  = "unavailable"

        try:
            if dm is not None:
                ep = getattr(dm, "spot_price", None)
                if ep and float(ep) > 0:
                    execution_price = float(ep)
                    execution_from  = "dm.spot_price"
        except Exception as e:
            log.debug("PriceSourceEngine: execution_price error: %s", e)

        # ── 2. reference_price: median of valid underlyingPrice candidates ────
        #
        # Strategy (v1.2):
        #   a) Collect ALL underlyingPrice values from dm.tickers
        #   b) Filter: must be > 0, finite, not NaN
        #   c) If execution_price is known: filter candidates where
        #      abs(candidate - execution) / execution > SANITY_PCT_THRESHOLD
        #      Those candidates are likely forward prices from far-dated contracts.
        #   d) If >= MIN_CANDIDATES_FOR_MEDIAN pass: use median → status = OK
        #   e) If < MIN_CANDIDATES_FOR_MEDIAN pass but some exist: use median of
        #      all remaining → status = SUSPECT (basis_valid = 0)
        #   f) If 0 pass: reference = execution_price → status = FALLBACK

        reference_price: Optional[float] = None
        reference_from  = "unavailable"
        reference_price_status = "UNKNOWN"
        basis_valid = 0
        reference_price_source = "none"
        reference_candidate_count = 0

        raw_candidates: list[float] = []
        sane_candidates: list[float] = []

        try:
            if dm is not None:
                tickers = getattr(dm, "tickers", {})
                if tickers:
                    for sym, data in tickers.items():
                        up = data.get("underlyingPrice")
                        if up is None:
                            continue
                        try:
                            fup = float(up)
                        except (TypeError, ValueError):
                            continue
                        if not math.isfinite(fup) or fup <= 0:
                            continue
                        raw_candidates.append(fup)

                    # Sanity filter vs execution_price
                    if execution_price and execution_price > 0:
                        for c in raw_candidates:
                            dev = abs(c - execution_price) / execution_price
                            if dev <= SANITY_PCT_THRESHOLD:
                                sane_candidates.append(c)
                    else:
                        # No execution_price to compare → accept all
                        sane_candidates = list(raw_candidates)

                    reference_candidate_count = len(sane_candidates)

                    if len(sane_candidates) >= MIN_CANDIDATES_FOR_MEDIAN:
                        # Good: enough sane candidates → use median
                        reference_price = statistics.median(sane_candidates)
                        reference_from = (
                            f"median_underlying_price "
                            f"({len(sane_candidates)}/{len(raw_candidates)} sane)"
                        )
                        reference_price_source = "median_sane_tickers"
                        reference_price_status = "OK"
                        basis_valid = 1

                    elif len(sane_candidates) > 0:
                        # Some sane but few — use them, mark SUSPECT
                        reference_price = statistics.median(sane_candidates)
                        reference_from = (
                            f"median_underlying_price_suspect "
                            f"({len(sane_candidates)}/{len(raw_candidates)} sane, "
                            f"below MIN={MIN_CANDIDATES_FOR_MEDIAN})"
                        )
                        reference_price_source = "median_few_sane_tickers"
                        reference_price_status = "SUSPECT"
                        basis_valid = 0
                        log.warning(
                            "PriceSourceEngine: only %d/%d sane candidates "
                            "(threshold %.1f%%) — reference SUSPECT",
                            len(sane_candidates), len(raw_candidates),
                            SANITY_PCT_THRESHOLD * 100,
                        )

                    elif len(raw_candidates) > 0:
                        # All candidates are outside sanity range → SUSPECT
                        # (This happens when tickers only have far-dated contracts)
                        # Use median of raw as a last resort but mark SUSPECT
                        reference_price = statistics.median(raw_candidates)
                        reference_candidate_count = len(raw_candidates)
                        reference_from = (
                            f"median_underlying_price_all_suspect "
                            f"(0/{len(raw_candidates)} sane, all deviated > "
                            f"{SANITY_PCT_THRESHOLD*100:.1f}%)"
                        )
                        reference_price_source = "median_all_unsane_tickers"
                        reference_price_status = "SUSPECT"
                        basis_valid = 0
                        log.warning(
                            "PriceSourceEngine: ALL %d candidates exceeded sanity "
                            "threshold %.1f%% vs execution=%.2f — SUSPECT",
                            len(raw_candidates),
                            SANITY_PCT_THRESHOLD * 100,
                            execution_price or 0,
                        )

        except Exception as e:
            log.debug("PriceSourceEngine: reference_price error: %s", e)

        # ── 3. Fallback: reference unavailable → use execution_price ──────────
        if reference_price is None and execution_price is not None:
            reference_price = execution_price
            reference_from  = "fallback_from_execution_price"
            reference_price_source = "execution_price_fallback"
            reference_price_status = "FALLBACK"
            basis_valid = 0
            log.warning(
                "PriceSourceEngine: reference_price unavailable, "
                "falling back to execution_price — basis will be 0"
            )

        # ── 4. SUSPECT safety: replace reference with execution for UI safety ──
        #
        # If reference is SUSPECT: the displayed reference_price is the
        # suspicious (potentially forward) value — bad for the UI.
        # Replace it with execution_price so basis = 0 and no garbage appears.
        # The original suspicious value is only logged, not stored in the result.
        raw_reference_price = reference_price  # keep for logging
        if reference_price_status == "SUSPECT" and execution_price is not None:
            reference_price = execution_price
            log.warning(
                "PriceSourceEngine: SUSPECT reference_price=%.2f replaced with "
                "execution_price=%.2f for UI safety (basis forced to 0). "
                "source: %s",
                raw_reference_price or 0,
                execution_price,
                reference_price_source,
            )

        # ── 5. basis ──────────────────────────────────────────────────────────
        basis: Optional[float] = None
        basis_pct: Optional[float] = None

        if execution_price is not None and reference_price is not None and reference_price > 0:
            basis = execution_price - reference_price
            basis_pct = basis / reference_price * 100.0

        info = PriceSourceInfo(
            reference_price          = reference_price,
            execution_price          = execution_price,
            basis                    = basis,
            basis_pct                = basis_pct,
            reference_from           = reference_from,
            execution_from           = execution_from,
            reference_price_status   = reference_price_status,
            basis_valid              = basis_valid,
            reference_price_source   = reference_price_source,
            reference_candidate_count = reference_candidate_count,
        )

        log.debug(
            "PriceSourceEngine computed: ref=%.2f exec=%.2f basis=%.2f (%.4f%%) "
            "status=%s valid=%d candidates=%d/%d",
            reference_price or 0,
            execution_price or 0,
            basis or 0,
            basis_pct or 0,
            reference_price_status,
            basis_valid,
            len(sane_candidates),
            len(raw_candidates),
        )
        return info

    @staticmethod
    def compute_ohlcv_sync_check(
        execution_price: Optional[float],
        latest_ohlcv_close: Optional[float],
    ) -> Dict[str, Any]:
        """Compare execution_price with latest Bybit linear OHLCV close.

        Returns dict with:
            latest_execution_ohlcv_close    float | None
            execution_price_ohlcv_diff      float | None
            execution_price_ohlcv_diff_pct  float | None
            execution_price_ohlcv_sync_status  str  (OK | WARNING | DEGRADED | UNKNOWN)
        """
        if execution_price is None or latest_ohlcv_close is None:
            return {
                "latest_execution_ohlcv_close":   latest_ohlcv_close,
                "execution_price_ohlcv_diff":     None,
                "execution_price_ohlcv_diff_pct": None,
                "execution_price_ohlcv_sync_status": "UNKNOWN",
            }

        diff = execution_price - latest_ohlcv_close
        diff_pct = abs(diff) / execution_price * 100.0

        if diff_pct <= 0.10:
            sync_status = "OK"
        elif diff_pct <= 0.25:
            sync_status = "WARNING"
        else:
            sync_status = "DEGRADED"
            log.warning(
                "PriceSourceEngine: execution_price=%.2f vs latest_ohlcv_close=%.2f "
                "diff_pct=%.3f%% — DEGRADED. Do not trust SL/TP replay.",
                execution_price, latest_ohlcv_close, diff_pct,
            )

        return {
            "latest_execution_ohlcv_close":   round(latest_ohlcv_close, 2),
            "execution_price_ohlcv_diff":     round(diff, 2),
            "execution_price_ohlcv_diff_pct": round(diff_pct, 4),
            "execution_price_ohlcv_sync_status": sync_status,
        }

    @staticmethod
    def invalidate_cache() -> None:
        """Force cache refresh on next get() call."""
        global _cache_ts
        _cache_ts = 0.0
