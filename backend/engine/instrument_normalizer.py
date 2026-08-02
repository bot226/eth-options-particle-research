"""InstrumentNormalizer — Cross-exchange instrument normalization.

Converts exchange-specific option symbol formats into canonical format:
    BTC-YYYYMMDD-STRIKE-TYPE

Examples:
    Deribit:  BTC-26DEC25-90000-C  →  BTC-20251226-90000-C
    Bybit:    BTC-26DEC25-90000-C  →  BTC-20251226-90000-C
    Binance:  BTC-250626-90000-C   →  BTC-20250626-90000-C
    OKX:      BTC-USD-250626-90000-C → BTC-20250626-90000-C
"""

import datetime
import logging
import re
from typing import Optional

log = logging.getLogger(__name__)

# ── Month name → number mapping ─────────────────────────────────────
_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


class NormalizedInstrument:
    """Parsed canonical instrument data."""
    __slots__ = ("canonical_id", "expiry_date", "expiry_str", "strike", "opt_type")

    def __init__(self, canonical_id: str, expiry_date: datetime.date,
                 expiry_str: str, strike: int, opt_type: str):
        self.canonical_id = canonical_id
        self.expiry_date = expiry_date
        self.expiry_str = expiry_str  # YYYYMMDD
        self.strike = strike
        self.opt_type = opt_type  # "C" or "P"

    def dte(self) -> int:
        """Days to expiration from today."""
        return max((self.expiry_date - datetime.date.today()).days, 0)

    def __repr__(self) -> str:
        return f"NormalizedInstrument({self.canonical_id}, DTE={self.dte()})"


class InstrumentNormalizer:
    """Normalizes exchange-specific option symbols to canonical format."""

    # ── Public API ───────────────────────────────────────────────────

    @staticmethod
    def normalize_symbol(exchange_id: str, raw_symbol: str) -> Optional[NormalizedInstrument]:
        """Normalize a raw exchange symbol to canonical format.

        Args:
            exchange_id: "deribit" | "bybit" | "binance" | "okx"
            raw_symbol: Exchange-specific symbol string

        Returns:
            NormalizedInstrument or None if parsing fails
        """
        try:
            parser = _EXCHANGE_PARSERS.get(exchange_id)
            if parser is None:
                log.warning("Unknown exchange_id: %s", exchange_id)
                return None
            return parser(raw_symbol)
        except Exception as e:
            log.debug("Failed to normalize %s symbol '%s': %s",
                      exchange_id, raw_symbol, e)
            return None

    @staticmethod
    def parse_canonical(canonical_id: str) -> Optional[NormalizedInstrument]:
        """Parse a canonical ID back into NormalizedInstrument.

        Args:
            canonical_id: e.g. "BTC-20251226-90000-C"
        """
        try:
            parts = canonical_id.split("-")
            if len(parts) != 4 or parts[0] != "BTC":
                return None
            expiry_str = parts[1]  # YYYYMMDD
            strike = int(parts[2])
            opt_type = parts[3].upper()
            if opt_type not in ("C", "P"):
                return None
            expiry_date = datetime.datetime.strptime(expiry_str, "%Y%m%d").date()
            return NormalizedInstrument(canonical_id, expiry_date, expiry_str,
                                       strike, opt_type)
        except Exception:
            return None

    @staticmethod
    def canonical_expiry_to_dte(expiry_yyyymmdd: str) -> Optional[int]:
        """Convert YYYYMMDD expiry string to DTE.

        Args:
            expiry_yyyymmdd: e.g. "20251226"

        Returns:
            Days to expiration (min 0), or None on parse failure
        """
        try:
            exp_date = datetime.datetime.strptime(expiry_yyyymmdd, "%Y%m%d").date()
            return max((exp_date - datetime.date.today()).days, 0)
        except Exception:
            return None

    @staticmethod
    def normalize_iv(exchange_id: str, iv_value: float) -> float:
        """Normalize IV to decimal format (0.0 – N.0).

        All exchanges in scope return IV as decimal (e.g. 0.45 = 45%).
        This method exists as a safety layer for future exchanges
        that may return IV as percentage (e.g. 45.0 = 45%).

        Returns:
            IV in decimal format
        """
        if iv_value <= 0:
            return 0.0
        # If IV looks like a percentage (> 5.0), convert to decimal
        # Standard BTC IV is typically 0.2 – 2.5 in decimal
        if iv_value > 5.0:
            return iv_value / 100.0
        return iv_value

    @staticmethod
    def normalize_greeks(exchange_id: str, opt_type: str, greeks: dict) -> dict:
        """Normalize Greeks signs and values.

        Ensures consistent sign conventions across all exchanges:
        - Call delta: 0 to 1
        - Put delta: -1 to 0
        - Gamma: always positive
        - Vega: always positive
        - Theta: always negative

        Args:
            exchange_id: exchange identifier
            opt_type: "C" or "P"
            greeks: dict with keys delta, gamma, vega, theta

        Returns:
            Normalized greeks dict
        """
        delta = greeks.get("delta", 0.0)
        gamma = abs(greeks.get("gamma", 0.0))
        vega = abs(greeks.get("vega", 0.0))
        theta = greeks.get("theta", 0.0)

        # Ensure theta is negative
        if theta > 0:
            theta = -theta

        # Ensure delta sign matches option type
        if opt_type == "C" and delta < 0:
            delta = abs(delta)
        elif opt_type == "P" and delta > 0:
            delta = -delta

        return {
            "delta": delta,
            "gamma": gamma,
            "vega": vega,
            "theta": theta,
        }

    @staticmethod
    def normalize_ticker(exchange_id: str, raw_ticker: dict) -> Optional[dict]:
        """Normalize a raw ticker dict into unified format.

        Args:
            exchange_id: exchange identifier
            raw_ticker: exchange-specific ticker data

        Returns:
            Normalized ticker dict with canonical fields, or None
        """
        normalizer_fn = _TICKER_NORMALIZERS.get(exchange_id)
        if normalizer_fn is None:
            log.warning("No ticker normalizer for exchange: %s", exchange_id)
            return None
        try:
            return normalizer_fn(raw_ticker)
        except Exception as e:
            log.debug("Failed to normalize ticker from %s: %s", exchange_id, e)
            return None


# ── Private exchange-specific parsers ────────────────────────────────


def _parse_deribit_symbol(raw: str) -> Optional[NormalizedInstrument]:
    """Parse Deribit format: BTC-26DEC25-90000-C"""
    parts = raw.split("-")
    if len(parts) != 4 or parts[0] != "BTC":
        return None
    expiry_raw = parts[1]  # e.g. "26DEC25"
    strike = int(parts[2])
    opt_type = parts[3].upper()
    if opt_type not in ("C", "P"):
        return None
    expiry_date = _parse_ddmmmyy(expiry_raw)
    if expiry_date is None:
        return None
    expiry_str = expiry_date.strftime("%Y%m%d")
    canonical = f"BTC-{expiry_str}-{strike}-{opt_type}"
    return NormalizedInstrument(canonical, expiry_date, expiry_str, strike, opt_type)


def _parse_bybit_symbol(raw: str) -> Optional[NormalizedInstrument]:
    """Parse Bybit format: BTC-26DEC25-90000-C (same as Deribit)."""
    # Bybit may also have USDT-settled: BTC-26DEC25-90000-C-USDT
    parts = raw.split("-")
    if len(parts) < 4 or parts[0] != "BTC":
        return None
    expiry_raw = parts[1]
    strike = int(parts[2])
    opt_type = parts[3].upper()
    if opt_type not in ("C", "P"):
        return None
    expiry_date = _parse_ddmmmyy(expiry_raw)
    if expiry_date is None:
        return None
    expiry_str = expiry_date.strftime("%Y%m%d")
    canonical = f"BTC-{expiry_str}-{strike}-{opt_type}"
    return NormalizedInstrument(canonical, expiry_date, expiry_str, strike, opt_type)


def _parse_binance_symbol(raw: str) -> Optional[NormalizedInstrument]:
    """Parse Binance EAPI format: BTC-250626-90000-C"""
    parts = raw.split("-")
    if len(parts) != 4 or parts[0] != "BTC":
        return None
    expiry_raw = parts[1]  # YYMMDD
    strike = int(parts[2])
    opt_type = parts[3].upper()
    if opt_type not in ("C", "P"):
        return None
    expiry_date = _parse_yymmdd(expiry_raw)
    if expiry_date is None:
        return None
    expiry_str = expiry_date.strftime("%Y%m%d")
    canonical = f"BTC-{expiry_str}-{strike}-{opt_type}"
    return NormalizedInstrument(canonical, expiry_date, expiry_str, strike, opt_type)


def _parse_okx_symbol(raw: str) -> Optional[NormalizedInstrument]:
    """Parse OKX format: BTC-USD-250626-90000-C"""
    parts = raw.split("-")
    if len(parts) != 5 or parts[0] != "BTC":
        return None
    # parts[1] = "USD" — skip
    expiry_raw = parts[2]  # YYMMDD
    strike = int(parts[3])
    opt_type = parts[4].upper()
    if opt_type not in ("C", "P"):
        return None
    expiry_date = _parse_yymmdd(expiry_raw)
    if expiry_date is None:
        return None
    expiry_str = expiry_date.strftime("%Y%m%d")
    canonical = f"BTC-{expiry_str}-{strike}-{opt_type}"
    return NormalizedInstrument(canonical, expiry_date, expiry_str, strike, opt_type)


# ── Date parsing helpers ─────────────────────────────────────────────


def _parse_ddmmmyy(s: str) -> Optional[datetime.date]:
    """Parse '26DEC25' → date(2025, 12, 26)."""
    if len(s) < 7:
        return None
    try:
        day = int(s[:2])
        month_str = s[2:5].upper()
        year_suffix = s[5:]
        month = _MONTHS.get(month_str)
        if month is None:
            return None
        year = 2000 + int(year_suffix) if len(year_suffix) == 2 else int(year_suffix)
        return datetime.date(year, month, day)
    except (ValueError, IndexError):
        return None


def _parse_yymmdd(s: str) -> Optional[datetime.date]:
    """Parse '250626' → date(2025, 6, 26)."""
    if len(s) != 6:
        return None
    try:
        year = 2000 + int(s[:2])
        month = int(s[2:4])
        day = int(s[4:6])
        return datetime.date(year, month, day)
    except (ValueError, IndexError):
        return None


# ── Ticker normalizers ───────────────────────────────────────────────


def _safe_float(val, default=0.0) -> float:
    """Safely convert to float."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _normalize_deribit_ticker(raw: dict) -> Optional[dict]:
    """Normalize Deribit book_summary / ticker to unified format."""
    instrument = raw.get("instrument_name", "")
    parsed = _parse_deribit_symbol(instrument)
    if parsed is None:
        return None

    nested_greeks = raw.get("greeks")
    if not isinstance(nested_greeks, dict):
        nested_greeks = {}
    stats = raw.get("stats")
    if not isinstance(stats, dict):
        stats = {}

    iv = InstrumentNormalizer.normalize_iv("deribit", _safe_float(raw.get("mark_iv")))
    greeks = InstrumentNormalizer.normalize_greeks("deribit", parsed.opt_type, {
        "delta": _safe_float(raw.get("delta", nested_greeks.get("delta"))),
        "gamma": _safe_float(raw.get("gamma", nested_greeks.get("gamma"))),
        "vega": _safe_float(raw.get("vega", nested_greeks.get("vega"))),
        "theta": _safe_float(raw.get("theta", nested_greeks.get("theta"))),
    })

    return {
        "canonical_id": parsed.canonical_id,
        "exchange": "deribit",
        "symbol": instrument,
        "expiry": parsed.expiry_str,
        "strike": parsed.strike,
        "type": parsed.opt_type,
        "oi": _safe_float(raw.get("open_interest")),
        "volume": _safe_float(
            raw.get("volume_24h", raw.get("volume", stats.get("volume")))
        ),
        "markIv": iv,
        "bidIv": InstrumentNormalizer.normalize_iv("deribit", _safe_float(raw.get("bid_iv"))),
        "askIv": InstrumentNormalizer.normalize_iv("deribit", _safe_float(raw.get("ask_iv"))),
        "delta": greeks["delta"],
        "gamma": greeks["gamma"],
        "vega": greeks["vega"],
        "theta": greeks["theta"],
        "markPrice": _safe_float(raw.get("mark_price")),
        "lastPrice": _safe_float(raw.get("last", raw.get("last_price"))),
        "bidPrice": _safe_float(raw.get("bid_price", raw.get("best_bid_price"))),
        "askPrice": _safe_float(raw.get("ask_price", raw.get("best_ask_price"))),
        "underlyingPrice": _safe_float(raw.get("underlying_price")),
    }


def _normalize_bybit_ticker(raw: dict) -> Optional[dict]:
    """Normalize Bybit V5 ticker to unified format."""
    symbol = raw.get("symbol", "")
    parsed = _parse_bybit_symbol(symbol)
    if parsed is None:
        return None

    iv = InstrumentNormalizer.normalize_iv("bybit", _safe_float(raw.get("markIv")))
    greeks = InstrumentNormalizer.normalize_greeks("bybit", parsed.opt_type, {
        "delta": _safe_float(raw.get("delta")),
        "gamma": _safe_float(raw.get("gamma")),
        "vega": _safe_float(raw.get("vega")),
        "theta": _safe_float(raw.get("theta")),
    })

    return {
        "canonical_id": parsed.canonical_id,
        "exchange": "bybit",
        "symbol": symbol,
        "expiry": parsed.expiry_str,
        "strike": parsed.strike,
        "type": parsed.opt_type,
        "oi": _safe_float(raw.get("openInterest")),
        "volume": _safe_float(raw.get("volume24h")),
        "markIv": iv,
        "bidIv": InstrumentNormalizer.normalize_iv("bybit", _safe_float(raw.get("bid1Iv"))),
        "askIv": InstrumentNormalizer.normalize_iv("bybit", _safe_float(raw.get("ask1Iv"))),
        "delta": greeks["delta"],
        "gamma": greeks["gamma"],
        "vega": greeks["vega"],
        "theta": greeks["theta"],
        "markPrice": _safe_float(raw.get("markPrice")),
        "lastPrice": _safe_float(raw.get("lastPrice")),
        "bidPrice": _safe_float(raw.get("bid1Price")),
        "askPrice": _safe_float(raw.get("ask1Price")),
        "underlyingPrice": _safe_float(raw.get("underlyingPrice")),
    }


def _normalize_binance_ticker(raw: dict) -> Optional[dict]:
    """Normalize Binance EAPI ticker to unified format."""
    symbol = raw.get("symbol", "")
    parsed = _parse_binance_symbol(symbol)
    if parsed is None:
        return None

    iv = InstrumentNormalizer.normalize_iv("binance", _safe_float(raw.get("markIv")))
    greeks = InstrumentNormalizer.normalize_greeks("binance", parsed.opt_type, {
        "delta": _safe_float(raw.get("delta")),
        "gamma": _safe_float(raw.get("gamma")),
        "vega": _safe_float(raw.get("vega")),
        "theta": _safe_float(raw.get("theta")),
    })

    return {
        "canonical_id": parsed.canonical_id,
        "exchange": "binance",
        "symbol": symbol,
        "expiry": parsed.expiry_str,
        "strike": parsed.strike,
        "type": parsed.opt_type,
        "oi": _safe_float(raw.get("openInterest")),
        "volume": _safe_float(raw.get("volume")),
        "markIv": iv,
        "bidIv": InstrumentNormalizer.normalize_iv("binance", _safe_float(raw.get("bidIV"))),
        "askIv": InstrumentNormalizer.normalize_iv("binance", _safe_float(raw.get("askIV"))),
        "delta": greeks["delta"],
        "gamma": greeks["gamma"],
        "vega": greeks["vega"],
        "theta": greeks["theta"],
        "markPrice": _safe_float(raw.get("markPrice")),
        "lastPrice": _safe_float(raw.get("lastPrice")),
        "bidPrice": _safe_float(raw.get("bidPrice")),
        "askPrice": _safe_float(raw.get("askPrice")),
        "underlyingPrice": _safe_float(raw.get("underlyingPrice")),
    }


def _normalize_okx_ticker(raw: dict) -> Optional[dict]:
    """Normalize OKX V5 ticker to unified format."""
    inst_id = raw.get("instId", "")
    parsed = _parse_okx_symbol(inst_id)
    if parsed is None:
        return None

    iv = InstrumentNormalizer.normalize_iv("okx", _safe_float(raw.get("markVol")))
    greeks = InstrumentNormalizer.normalize_greeks("okx", parsed.opt_type, {
        "delta": _safe_float(raw.get("delta")),
        "gamma": _safe_float(raw.get("gamma")),
        "vega": _safe_float(raw.get("vega")),
        "theta": _safe_float(raw.get("theta")),
    })

    return {
        "canonical_id": parsed.canonical_id,
        "exchange": "okx",
        "symbol": inst_id,
        "expiry": parsed.expiry_str,
        "strike": parsed.strike,
        "type": parsed.opt_type,
        "oi": _safe_float(raw.get("oi")),
        "volume": _safe_float(raw.get("vol24h", raw.get("volCcy24h"))),
        "markIv": iv,
        "bidIv": InstrumentNormalizer.normalize_iv("okx", _safe_float(raw.get("bidVol"))),
        "askIv": InstrumentNormalizer.normalize_iv("okx", _safe_float(raw.get("askVol"))),
        "delta": greeks["delta"],
        "gamma": greeks["gamma"],
        "vega": greeks["vega"],
        "theta": greeks["theta"],
        "markPrice": _safe_float(raw.get("markPx")),
        "lastPrice": _safe_float(raw.get("last")),
        "bidPrice": _safe_float(raw.get("bidPx")),
        "askPrice": _safe_float(raw.get("askPx")),
        "underlyingPrice": _safe_float(raw.get("fwdPx", raw.get("idxPx"))),
    }


# ── Parser/normalizer registries ─────────────────────────────────────

_EXCHANGE_PARSERS = {
    "deribit": _parse_deribit_symbol,
    "bybit": _parse_bybit_symbol,
    "binance": _parse_binance_symbol,
    "okx": _parse_okx_symbol,
}

_TICKER_NORMALIZERS = {
    "deribit": _normalize_deribit_ticker,
    "bybit": _normalize_bybit_ticker,
    "binance": _normalize_binance_ticker,
    "okx": _normalize_okx_ticker,
}
