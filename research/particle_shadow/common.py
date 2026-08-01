"""Shared deterministic helpers for particle replay."""

from __future__ import annotations

import hashlib
import json
import math
import re
from bisect import bisect_right
from dataclasses import dataclass
from typing import Any, Iterable


BYBIT_INSTRUMENT = re.compile(
    r"^(?P<underlying>[A-Z0-9]+)-(?P<expiry>\d{8})-"
    r"(?P<strike>\d+(?:\.\d+)?)-(?P<option_type>[CP])$"
)
DERIBIT_INSTRUMENT = re.compile(
    r"^(?P<underlying>[A-Z0-9]+)-(?P<expiry>\d{1,2}[A-Z]{3}\d{2})-"
    r"(?P<strike>\d+(?:\.\d+)?)-(?P<option_type>[CP])$"
)


@dataclass(frozen=True)
class ContractIdentity:
    underlying: str
    expiry: str
    strike: float
    option_type: str


def stable_id(*parts: object) -> str:
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:32]


def safe_json_loads(value: str | bytes | None, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def finite_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def parse_contract(instrument_name: str) -> ContractIdentity | None:
    match = BYBIT_INSTRUMENT.fullmatch(instrument_name)
    if match is None:
        match = DERIBIT_INSTRUMENT.fullmatch(instrument_name)
    if match is None:
        return None
    return ContractIdentity(
        underlying=match.group("underlying"),
        expiry=match.group("expiry"),
        strike=float(match.group("strike")),
        option_type=match.group("option_type"),
    )


def percentile_strength(value: float, population: Iterable[float]) -> float:
    absolute = sorted(abs(float(item)) for item in population if finite_float(item) is not None)
    if not absolute:
        return 0.0
    rank = bisect_right(absolute, abs(float(value)))
    return round(100.0 * rank / len(absolute), 4)
