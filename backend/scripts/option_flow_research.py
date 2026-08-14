"""Frozen statistical foundation for public ETH option trade-flow research.

This module is offline-only. It audits a MOS Dataset Exporter directory or ZIP,
freezes the protocol hash, and provides leakage-safe walk-forward and
multiple-testing primitives. It never imports or changes live MOS scoring.
"""

from __future__ import annotations

import argparse
import bisect
import contextlib
import hashlib
import json
import math
import sqlite3
import tempfile
import zipfile
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

import numpy as np
from scipy import stats


REQUIRED_FILES = ("mos_research.db", "history.db", "option_trade_flow.db")
OPTION_FLOW_RESEARCH_VERSION = "1.1.2"
DEFAULT_PROTOCOL_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "mos"
    / "MOS_OPTION_FLOW_PREREG_V1.json"
)


class ResearchInputError(RuntimeError):
    pass


@dataclass(frozen=True)
class ThresholdObservation:
    timestamp_utc: float
    value: float
    threshold: float


@dataclass(frozen=True)
class EnrichedOptionTrade:
    exchange: str
    trade_id: str
    timestamp_utc: float
    contract_id: str
    expiry: str | None
    strike: float | None
    option_type: str
    taker_side: str
    size: float
    trade_iv_decimal: float | None
    is_block_trade: bool
    is_combo_trade: bool
    snapshot_timestamp_utc: float | None
    delta: float | None
    gamma: float | None
    vega: float | None
    snapshot_mark_iv: float | None
    underlying_price: float | None


@dataclass
class FlowAccumulator:
    contract_num: float = 0.0
    contract_den: float = 0.0
    delta_num: float = 0.0
    delta_den: float = 0.0
    gamma_num: float = 0.0
    gamma_den: float = 0.0
    vega_num: float = 0.0
    vega_den: float = 0.0
    trade_count: int = 0
    greek_matched_count: int = 0

    def add(self, trade: EnrichedOptionTrade) -> None:
        side = 1.0 if trade.taker_side == "BUY" else -1.0
        option_direction = 1.0 if trade.option_type == "C" else -1.0
        self.contract_num += side * option_direction * trade.size
        self.contract_den += trade.size
        self.trade_count += 1
        if trade.delta is not None:
            exposure = trade.delta * trade.size
            self.delta_num += side * exposure
            self.delta_den += abs(exposure)
            self.greek_matched_count += 1
        if trade.gamma is not None:
            exposure = trade.gamma * trade.size
            self.gamma_num += side * exposure
            self.gamma_den += abs(exposure)
        if trade.vega is not None:
            exposure = trade.vega * trade.size
            self.vega_num += side * exposure
            self.vega_den += abs(exposure)

    def merge(self, other: "FlowAccumulator") -> None:
        for name in (
            "contract_num",
            "contract_den",
            "delta_num",
            "delta_den",
            "gamma_num",
            "gamma_den",
            "vega_num",
            "vega_den",
            "trade_count",
            "greek_matched_count",
        ):
            setattr(self, name, getattr(self, name) + getattr(other, name))

    def copy(self) -> "FlowAccumulator":
        result = FlowAccumulator()
        result.merge(self)
        return result

    def subtract(self, other: "FlowAccumulator") -> None:
        for name in (
            "contract_num",
            "contract_den",
            "delta_num",
            "delta_den",
            "gamma_num",
            "gamma_den",
            "vega_num",
            "vega_den",
            "trade_count",
            "greek_matched_count",
        ):
            setattr(self, name, getattr(self, name) - getattr(other, name))


@dataclass(frozen=True)
class FlowFeaturePoint:
    timestamp_utc: float
    scope: str
    trade_filter: str
    segment: str
    lookback_sec: int
    call_put_contract_imbalance: float | None
    signed_delta_imbalance: float | None
    absolute_signed_gamma_imbalance: float | None
    absolute_signed_vega_imbalance: float | None
    trade_intensity: int
    greek_match_ratio: float


@dataclass(frozen=True)
class FuturesCandle:
    timestamp_utc: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class FuturesOutcome:
    decision_timestamp_utc: float
    horizon_sec: int
    start_close: float
    end_close: float
    future_return_pct: float
    future_range_pct: float
    false_sweep_direction: int
    trailing_returns_pct: Mapping[int, float]


@dataclass(frozen=True)
class DecisionContext:
    decision_timestamp_utc: float
    snapshot_timestamp_utc: float
    current_state: str
    gamma_regime: str
    execution_timing_state: str


@dataclass(frozen=True)
class DirectionRuleSpec:
    scope: str
    trade_filter: str
    segment: str
    lookback_sec: int
    feature: str
    context: str
    horizon_sec: int
    mode: str

    @property
    def rule_id(self) -> str:
        return "|".join(
            str(value)
            for value in (
                self.scope,
                self.trade_filter,
                self.segment,
                self.lookback_sec,
                self.feature,
                self.context,
                self.horizon_sec,
                self.mode,
            )
        )

    @property
    def cross_exchange_key(self) -> tuple[Any, ...]:
        return (
            self.trade_filter,
            self.segment,
            self.lookback_sec,
            self.feature,
            self.context,
            self.horizon_sec,
            self.mode,
        )


@dataclass(frozen=True)
class RangeRuleSpec:
    scope: str
    trade_filter: str
    segment: str
    lookback_sec: int
    feature: str
    context: str
    horizon_sec: int

    @property
    def rule_id(self) -> str:
        return "|".join(
            str(value)
            for value in (
                self.scope,
                self.trade_filter,
                self.segment,
                self.lookback_sec,
                self.feature,
                self.context,
                self.horizon_sec,
            )
        )

    @property
    def cross_exchange_key(self) -> tuple[Any, ...]:
        return (
            self.trade_filter,
            self.segment,
            self.lookback_sec,
            self.feature,
            self.context,
            self.horizon_sec,
        )


def _utc_day(timestamp_utc: float) -> str:
    return datetime.fromtimestamp(timestamp_utc, tz=timezone.utc).date().isoformat()


def protocol_sha256(path: Path | str = DEFAULT_PROTOCOL_PATH) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def protocol_content_sha256(protocol: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        protocol, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def load_protocol(path: Path | str = DEFAULT_PROTOCOL_PATH) -> dict[str, Any]:
    protocol = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {
        "protocol",
        "training_days",
        "minimum_total_days",
        "minimum_test_days",
        "minimum_trades",
        "signal_quantile",
        "futures_round_trip_cost_bps",
        "bootstrap_samples",
        "permutation_samples",
        "random_seed",
        "promotion_gate",
    }
    missing = sorted(required - set(protocol))
    if missing:
        raise ResearchInputError(f"Protocol is missing required fields: {missing}")
    return protocol


@contextlib.contextmanager
def dataset_files(input_path: Path | str) -> Iterator[dict[str, Path]]:
    """Resolve an exporter directory or safely extract only known ZIP members."""
    source = Path(input_path).resolve()
    if source.is_dir():
        paths = {name: source / name for name in REQUIRED_FILES}
        missing = [name for name, path in paths.items() if not path.is_file()]
        if missing:
            raise ResearchInputError(f"Dataset is missing: {', '.join(missing)}")
        yield paths
        return
    if not source.is_file() or source.suffix.lower() != ".zip":
        raise ResearchInputError("Input must be a dataset directory or ZIP archive")
    with tempfile.TemporaryDirectory(prefix="mos_option_flow_research_") as directory:
        target = Path(directory)
        with zipfile.ZipFile(source) as archive:
            names = set(archive.namelist())
            missing = [name for name in REQUIRED_FILES if name not in names]
            if missing:
                raise ResearchInputError(f"Archive is missing: {', '.join(missing)}")
            for name in REQUIRED_FILES:
                member = archive.getinfo(name)
                if member.is_dir() or Path(member.filename).name != member.filename:
                    raise ResearchInputError(f"Unsafe archive member: {member.filename}")
                with archive.open(member) as source_file, (target / name).open("wb") as output:
                    while chunk := source_file.read(1024 * 1024):
                        output.write(chunk)
        yield {name: target / name for name in REQUIRED_FILES}


def _read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30.0)
    connection.row_factory = sqlite3.Row
    return connection


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    if not table.replace("_", "").isalnum():
        raise ResearchInputError(f"Unsafe table name: {table}")
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _scalar(connection: sqlite3.Connection, query: str, params: Sequence[Any] = ()) -> Any:
    row = connection.execute(query, params).fetchone()
    return row[0] if row else None


def audit_dataset(paths: Mapping[str, Path], protocol: Mapping[str, Any]) -> dict[str, Any]:
    """Audit whether a dataset can enter the frozen option-flow test."""
    flow = _read_only(paths["option_trade_flow.db"])
    research = _read_only(paths["mos_research.db"])
    history = _read_only(paths["history.db"])
    try:
        flow_tables = _table_names(flow)
        required_flow_tables = {
            "option_trades",
            "collector_status",
            "collector_status_history",
            "metadata",
        }
        missing_flow_tables = sorted(required_flow_tables - flow_tables)
        flow_column_requirements = {
            "option_trades": {
                "exchange", "trade_timestamp_utc", "contract_id",
                "trade_iv_decimal", "taker_side", "contracts", "amount",
                "option_type", "expiry", "strike", "is_block_trade",
                "is_combo_trade",
            },
            "collector_status_history": {
                "exchange", "session_id", "connection_state",
                "dropped_trade_count", "updated_at_utc",
            },
            "metadata": {"key", "value"},
        }
        missing_flow_columns = {
            table: sorted(columns - _column_names(flow, table))
            for table, columns in flow_column_requirements.items()
            if table in flow_tables and columns - _column_names(flow, table)
        }
        schema_version = (
            _scalar(flow, "SELECT value FROM metadata WHERE key='schema_version'")
            if "metadata" in flow_tables and "metadata" not in missing_flow_columns
            else None
        )
        integrity = _scalar(flow, "PRAGMA quick_check")
        trade_rows = []
        if "option_trades" in flow_tables and "option_trades" not in missing_flow_columns:
            trade_rows = [
                dict(row)
                for row in flow.execute(
                    """
                    SELECT exchange, COUNT(*) AS trades,
                           MIN(trade_timestamp_utc) AS first_trade_utc,
                           MAX(trade_timestamp_utc) AS last_trade_utc,
                           SUM(contract_id IS NOT NULL) AS normalized_contracts,
                           SUM(trade_iv_decimal IS NOT NULL) AS valid_trade_iv
                    FROM option_trades
                    GROUP BY exchange ORDER BY exchange
                    """
                ).fetchall()
            ]
        session_rows: list[dict[str, Any]] = []
        status_samples: list[dict[str, Any]] = []
        if (
            "collector_status_history" in flow_tables
            and "collector_status_history" not in missing_flow_columns
        ):
            session_rows = [
                dict(row)
                for row in flow.execute(
                    """
                    SELECT exchange, session_id,
                           MIN(updated_at_utc) AS first_sample_utc,
                           MAX(updated_at_utc) AS last_sample_utc,
                           COUNT(*) AS samples,
                           SUM(connection_state != 'subscribed') AS unhealthy_samples,
                           MAX(dropped_trade_count) AS dropped_trades
                    FROM collector_status_history
                    GROUP BY exchange, session_id
                    ORDER BY first_sample_utc
                    """
                ).fetchall()
            ]
            status_samples = [
                dict(row)
                for row in flow.execute(
                    """
                    SELECT exchange, session_id, connection_state,
                           dropped_trade_count, updated_at_utc
                    FROM collector_status_history
                    ORDER BY exchange, session_id, updated_at_utc
                    """
                ).fetchall()
            ]
        bad_sessions = {
            (str(row["exchange"]), str(row["session_id"]))
            for row in session_rows
            if int(row["dropped_trades"] or 0) > 0
        }
        max_sample_gap = 0.0
        previous: dict[tuple[str, str], float] = {}
        healthy_bucket_samples: dict[str, dict[int, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        for row in status_samples:
            key = (str(row["exchange"]), str(row["session_id"]))
            timestamp = float(row["updated_at_utc"])
            if key in previous:
                max_sample_gap = max(max_sample_gap, timestamp - previous[key])
            previous[key] = timestamp
            if (
                key not in bad_sessions
                and row["connection_state"] == "subscribed"
                and int(row["dropped_trade_count"] or 0) == 0
            ):
                healthy_bucket_samples[key[0]][int(timestamp // 300)] += 1
        minimum_status_samples = int(
            protocol["minimum_status_samples_per_5m_per_exchange"]
        )
        healthy_buckets = {
            exchange: {
                bucket
                for bucket, samples in counts.items()
                if samples >= minimum_status_samples
            }
            for exchange, counts in healthy_bucket_samples.items()
        }
        exchanges = {str(row["exchange"]) for row in trade_rows}
        common_healthy = set.intersection(
            *(healthy_buckets.get(exchange, set()) for exchange in ("bybit", "deribit"))
        ) if healthy_buckets else set()
        healthy_days = len(common_healthy) * 300.0 / 86400.0
        longest_lookback = max(int(value) for value in protocol["lookbacks_sec"])
        longest_steps = max(1, math.ceil(longest_lookback / 300))
        full_lookback_buckets = {
            bucket
            for bucket in common_healthy
            if all(bucket - offset in common_healthy for offset in range(longest_steps))
        }
        full_lookback_days = len(full_lookback_buckets) * 300.0 / 86400.0

        research_tables = _table_names(research)
        research_column_requirements = {
            "ohlcv_candles": {
                "timestamp_utc", "symbol", "timeframe", "high", "low", "close",
            },
            "snapshots": {
                "timestamp_utc", "current_state", "gamma_regime",
                "execution_timing_state", "exclude_from_analysis",
            },
        }
        missing_research_columns = {
            table: sorted(columns - _column_names(research, table))
            for table, columns in research_column_requirements.items()
            if table in research_tables and columns - _column_names(research, table)
        }
        if "ohlcv_candles" in research_tables and "ohlcv_candles" not in missing_research_columns:
            candle_range = dict(
                research.execute(
                    """
                    SELECT COUNT(*) AS candles,
                           MIN(timestamp_utc) AS first_candle_utc,
                           MAX(timestamp_utc) AS last_candle_utc
                    FROM ohlcv_candles
                    WHERE symbol='ETHUSDT' AND timeframe='1m'
                    """
                ).fetchone()
            )
        else:
            candle_range = {"candles": 0, "first_candle_utc": None, "last_candle_utc": None}
        history_tables = _table_names(history)
        history_required_columns = {
            "ts", "exchange", "contract_id", "delta", "gamma", "vega",
            "mark_iv", "underlying_price",
        }
        missing_history_columns = (
            sorted(
                history_required_columns
                - _column_names(history, "option_contract_snapshots")
            )
            if "option_contract_snapshots" in history_tables
            else sorted(history_required_columns)
        )
        contract_range = {"contract_snapshots": 0, "first_snapshot_utc": None, "last_snapshot_utc": None}
        if "option_contract_snapshots" in history_tables and not missing_history_columns:
            contract_range = dict(
                history.execute(
                    """
                    SELECT COUNT(*) AS contract_snapshots,
                           MIN(ts) AS first_snapshot_utc,
                           MAX(ts) AS last_snapshot_utc
                    FROM option_contract_snapshots
                    """
                ).fetchone()
            )

        starts = [
            float(row["first_trade_utc"])
            for row in trade_rows
            if row["first_trade_utc"] is not None
        ]
        ends = [
            float(row["last_trade_utc"])
            for row in trade_rows
            if row["last_trade_utc"] is not None
        ]
        if candle_range["first_candle_utc"] is not None:
            starts.append(float(candle_range["first_candle_utc"]))
            ends.append(float(candle_range["last_candle_utc"]))
        if contract_range["first_snapshot_utc"] is not None:
            starts.append(float(contract_range["first_snapshot_utc"]))
            ends.append(float(contract_range["last_snapshot_utc"]))
        overlap_days = max(0.0, (min(ends) - max(starts)) / 86400.0) if starts and ends else 0.0
        total_trades = sum(int(row["trades"]) for row in trade_rows)
        normalized = sum(int(row["normalized_contracts"] or 0) for row in trade_rows)
        valid_iv = sum(int(row["valid_trade_iv"] or 0) for row in trade_rows)
        historical_drops = sum(int(row["dropped_trades"] or 0) for row in session_rows)
        minimum_days = float(protocol["minimum_total_days"])
        blockers = []
        if integrity != "ok":
            blockers.append("option_flow_quick_check_failed")
        if missing_flow_tables:
            blockers.append("option_flow_schema_incomplete")
        if missing_flow_columns:
            blockers.append("option_flow_columns_incomplete")
        if "snapshots" not in research_tables or "ohlcv_candles" not in research_tables:
            blockers.append("research_tables_incomplete")
        if missing_research_columns:
            blockers.append("research_columns_incomplete")
        if missing_history_columns:
            blockers.append("contract_snapshot_columns_incomplete")
        if schema_version != "1.1":
            blockers.append("quality_history_schema_not_v1_1")
        if exchanges != {"bybit", "deribit"}:
            blockers.append("both_exchanges_not_present")
        if overlap_days < minimum_days:
            blockers.append("insufficient_common_calendar_days")
        required_full_lookback_days = max(
            0.0, minimum_days - (longest_steps - 1) * 300.0 / 86400.0
        )
        if full_lookback_days < required_full_lookback_days:
            blockers.append("insufficient_healthy_dual_exchange_days")
        if not contract_range["contract_snapshots"]:
            blockers.append("contract_greeks_missing")
        if not candle_range["candles"]:
            blockers.append("futures_ohlcv_missing")
        return {
            "status": "ready" if not blockers else "not_ready",
            "blockers": blockers,
            "protocol": protocol["protocol"],
            "protocol_sha256": protocol_content_sha256(protocol),
            "flow_schema_version": schema_version,
            "missing_flow_columns": missing_flow_columns,
            "missing_research_columns": missing_research_columns,
            "missing_contract_snapshot_columns": missing_history_columns,
            "quick_check": integrity,
            "trades": total_trades,
            "trade_exchanges": trade_rows,
            "normalized_contract_ratio": normalized / total_trades if total_trades else 0.0,
            "valid_trade_iv_ratio": valid_iv / total_trades if total_trades else 0.0,
            "sessions": session_rows,
            "historical_dropped_trades": historical_drops,
            "max_status_sample_gap_sec": max_sample_gap or None,
            "common_overlap_days": overlap_days,
            "healthy_dual_exchange_days": healthy_days,
            "healthy_full_lookback_days": full_lookback_days,
            "required_healthy_full_lookback_days": required_full_lookback_days,
            "calendar_days_remaining": max(0.0, minimum_days - overlap_days),
            "healthy_days_remaining": max(
                0.0, required_full_lookback_days - full_lookback_days
            ),
            "healthy_progress_ratio": (
                min(1.0, full_lookback_days / required_full_lookback_days)
                if required_full_lookback_days > 0
                else 1.0
            ),
            "ohlcv": candle_range,
            "contract_snapshots": contract_range,
        }
    finally:
        flow.close()
        research.close()
        history.close()


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def load_enriched_option_trades(
    paths: Mapping[str, Path], *, greek_max_age_sec: float
) -> list[EnrichedOptionTrade]:
    """Join each trade to the most recent earlier snapshot of the same contract."""
    flow_path = paths["option_trade_flow.db"].resolve()
    history_path = paths["history.db"].resolve()
    connection = _read_only(flow_path)
    try:
        connection.execute("ATTACH DATABASE ? AS history", (f"file:{history_path}?mode=ro",))
        rows = connection.execute(
            """
            SELECT t.exchange, t.trade_id, t.trade_timestamp_utc, t.contract_id,
                   t.expiry, t.strike, t.option_type, t.taker_side,
                   t.contracts, t.amount, t.trade_iv_decimal,
                   t.is_block_trade, t.is_combo_trade,
                   s.ts AS snapshot_timestamp_utc, s.delta, s.gamma, s.vega,
                   s.mark_iv AS snapshot_mark_iv, s.underlying_price
            FROM option_trades AS t
            LEFT JOIN history.option_contract_snapshots AS s
              ON s.rowid = (
                  SELECT earlier.rowid
                  FROM history.option_contract_snapshots AS earlier
                  WHERE earlier.exchange = t.exchange
                    AND earlier.contract_id = t.contract_id
                    AND earlier.ts <= t.trade_timestamp_utc
                  ORDER BY earlier.ts DESC
                  LIMIT 1
              )
            WHERE t.contract_id IS NOT NULL
              AND t.taker_side IN ('BUY', 'SELL')
            ORDER BY t.trade_timestamp_utc, t.exchange, t.trade_id
            """
        ).fetchall()
    finally:
        connection.close()
    trades: list[EnrichedOptionTrade] = []
    for row in rows:
        timestamp = float(row["trade_timestamp_utc"])
        snapshot_timestamp = _finite_float(row["snapshot_timestamp_utc"])
        greek_is_fresh = (
            snapshot_timestamp is not None
            and 0.0 <= timestamp - snapshot_timestamp <= greek_max_age_sec
        )
        size = _finite_float(row["contracts"])
        if size is None or size <= 0:
            size = _finite_float(row["amount"])
        if size is None or size <= 0:
            continue
        trades.append(
            EnrichedOptionTrade(
                exchange=str(row["exchange"]),
                trade_id=str(row["trade_id"]),
                timestamp_utc=timestamp,
                contract_id=str(row["contract_id"]),
                expiry=str(row["expiry"]) if row["expiry"] else None,
                strike=_finite_float(row["strike"]),
                option_type=str(row["option_type"]),
                taker_side=str(row["taker_side"]),
                size=size,
                trade_iv_decimal=_finite_float(row["trade_iv_decimal"]),
                is_block_trade=bool(row["is_block_trade"]),
                is_combo_trade=bool(row["is_combo_trade"]),
                snapshot_timestamp_utc=snapshot_timestamp if greek_is_fresh else None,
                delta=_finite_float(row["delta"]) if greek_is_fresh else None,
                gamma=_finite_float(row["gamma"]) if greek_is_fresh else None,
                vega=_finite_float(row["vega"]) if greek_is_fresh else None,
                snapshot_mark_iv=(
                    _finite_float(row["snapshot_mark_iv"]) if greek_is_fresh else None
                ),
                underlying_price=(
                    _finite_float(row["underlying_price"]) if greek_is_fresh else None
                ),
            )
        )
    return trades


def _trade_segments(trade: EnrichedOptionTrade) -> tuple[str, ...]:
    segments = ["all"]
    if trade.expiry and len(trade.expiry) == 8 and trade.expiry.isdigit():
        expiry = datetime.strptime(trade.expiry, "%Y%m%d").replace(tzinfo=timezone.utc)
        days = (expiry.timestamp() - trade.timestamp_utc) / 86400.0
        if days <= 7:
            segments.append("expiry_0_7d")
        elif days <= 30:
            segments.append("expiry_8_30d")
        else:
            segments.append("expiry_31d_plus")
    if (
        trade.strike is not None
        and trade.underlying_price is not None
        and trade.underlying_price > 0
    ):
        distance = abs(trade.strike / trade.underlying_price - 1.0)
        segments.append("atm_5pct" if distance <= 0.05 else "wing_over_5pct")
    return tuple(segments)


def aggregate_trade_flow_buckets(
    trades: Iterable[EnrichedOptionTrade], *, interval_sec: int = 300
) -> dict[tuple[float, str, str, str], FlowAccumulator]:
    """Build five-minute raw sums by exchange, filter and economic segment."""
    buckets: dict[tuple[float, str, str, str], FlowAccumulator] = defaultdict(
        FlowAccumulator
    )
    for trade in trades:
        if trade.option_type not in {"C", "P"} or trade.exchange not in {
            "bybit",
            "deribit",
        }:
            continue
        bucket_end = float((int(trade.timestamp_utc) // interval_sec + 1) * interval_sec)
        filters = ["all"]
        if not trade.is_block_trade and not trade.is_combo_trade:
            filters.append("non_block_non_combo")
        for trade_filter in filters:
            for segment in _trade_segments(trade):
                buckets[(bucket_end, trade.exchange, trade_filter, segment)].add(trade)
    combined: dict[tuple[float, str, str, str], FlowAccumulator] = defaultdict(
        FlowAccumulator
    )
    for (timestamp, exchange, trade_filter, segment), accumulator in buckets.items():
        combined[(timestamp, "combined", trade_filter, segment)].merge(accumulator)
    buckets.update(combined)
    return dict(buckets)


def _ratio(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return max(-1.0, min(1.0, numerator / denominator))


def rolling_flow_feature_points(
    buckets: Mapping[tuple[float, str, str, str], FlowAccumulator],
    *,
    lookbacks_sec: Iterable[int],
    interval_sec: int = 300,
) -> list[FlowFeaturePoint]:
    """Turn raw buckets into fixed-lookback ratio features without future rows."""
    grouped: dict[tuple[str, str, str], dict[float, FlowAccumulator]] = defaultdict(dict)
    for (timestamp, scope, trade_filter, segment), accumulator in buckets.items():
        grouped[(scope, trade_filter, segment)][timestamp] = accumulator
    points: list[FlowFeaturePoint] = []
    for (scope, trade_filter, segment), series in grouped.items():
        if not series:
            continue
        first = int(min(series))
        last = int(max(series))
        timestamps = list(range(first, last + interval_sec, interval_sec))
        for lookback in sorted(set(int(value) for value in lookbacks_sec)):
            if lookback < interval_sec or lookback % interval_sec:
                raise ValueError("Lookbacks must be positive multiples of the interval")
            active: deque[tuple[float, FlowAccumulator]] = deque()
            total = FlowAccumulator()
            for timestamp in timestamps:
                current = series.get(float(timestamp))
                if current is not None:
                    active.append((float(timestamp), current))
                    total.merge(current)
                cutoff = timestamp - lookback
                while active and active[0][0] <= cutoff:
                    _, expired = active.popleft()
                    total.subtract(expired)
                if total.trade_count <= 0:
                    continue
                gamma = _ratio(total.gamma_num, total.gamma_den)
                vega = _ratio(total.vega_num, total.vega_den)
                points.append(
                    FlowFeaturePoint(
                        timestamp_utc=float(timestamp),
                        scope=scope,
                        trade_filter=trade_filter,
                        segment=segment,
                        lookback_sec=lookback,
                        call_put_contract_imbalance=_ratio(
                            total.contract_num, total.contract_den
                        ),
                        signed_delta_imbalance=_ratio(total.delta_num, total.delta_den),
                        absolute_signed_gamma_imbalance=(abs(gamma) if gamma is not None else None),
                        absolute_signed_vega_imbalance=(abs(vega) if vega is not None else None),
                        trade_intensity=total.trade_count,
                        greek_match_ratio=(
                            total.greek_matched_count / total.trade_count
                            if total.trade_count
                            else 0.0
                        ),
                    )
                )
    return points


def load_futures_candles(paths: Mapping[str, Path]) -> list[FuturesCandle]:
    connection = _read_only(paths["mos_research.db"])
    try:
        rows = connection.execute(
            """
            SELECT timestamp_utc, high, low, close
            FROM ohlcv_candles
            WHERE symbol='ETHUSDT' AND timeframe='1m'
              AND high IS NOT NULL AND low IS NOT NULL AND close IS NOT NULL
            ORDER BY timestamp_utc
            """
        ).fetchall()
    finally:
        connection.close()
    return [
        FuturesCandle(
            timestamp_utc=float(row["timestamp_utc"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
        )
        for row in rows
        if float(row["close"]) > 0
    ]


def load_decision_contexts(
    paths: Mapping[str, Path],
    decision_timestamps: Iterable[float],
    *,
    max_age_sec: float,
) -> dict[float, DecisionContext]:
    """Join each decision to the latest earlier MOS snapshot, never the future."""
    connection = _read_only(paths["mos_research.db"])
    try:
        rows = connection.execute(
            """
            SELECT timestamp_utc, current_state, gamma_regime,
                   execution_timing_state
            FROM snapshots
            WHERE exclude_from_analysis = 0 OR exclude_from_analysis IS NULL
            ORDER BY timestamp_utc
            """
        ).fetchall()
    finally:
        connection.close()
    snapshot_times = [float(row["timestamp_utc"]) for row in rows]
    result: dict[float, DecisionContext] = {}
    for decision in sorted(set(float(value) for value in decision_timestamps)):
        index = bisect.bisect_right(snapshot_times, decision) - 1
        if index < 0 or decision - snapshot_times[index] > max_age_sec:
            continue
        row = rows[index]
        result[decision] = DecisionContext(
            decision_timestamp_utc=decision,
            snapshot_timestamp_utc=snapshot_times[index],
            current_state=str(row["current_state"] or "UNKNOWN"),
            gamma_regime=str(row["gamma_regime"] or "UNKNOWN"),
            execution_timing_state=str(row["execution_timing_state"] or "UNKNOWN"),
        )
    return result


def _context_matches(
    context_name: str,
    outcome: FuturesOutcome,
    decision_context: DecisionContext | None,
) -> bool:
    if context_name == "all":
        return True
    if context_name == "false_sweep_30m_15m_2bps":
        return bool(outcome.false_sweep_direction)
    if decision_context is None:
        return False
    if context_name == "mos_compression_or_pinning":
        return decision_context.current_state in {"COMPRESSION", "PINNING"}
    if context_name == "mos_expansion_or_breakout":
        return decision_context.current_state in {
            "EXPANSION",
            "BREAKOUT_SETUP",
            "SQUEEZE",
            "PANIC",
        }
    if context_name == "negative_gamma":
        return decision_context.gamma_regime == "NEGATIVE_GAMMA"
    if context_name == "positive_gamma":
        return decision_context.gamma_regime == "POSITIVE_GAMMA"
    if context_name == "execution_active":
        return decision_context.execution_timing_state in {
            "EXPANSION_CONFIRMING",
            "EXECUTION_WINDOW_OPEN",
            "EXECUTION_WINDOW",
        }
    raise ValueError(f"Unknown preregistered context: {context_name}")


def _false_sweep_direction(
    candles: Sequence[FuturesCandle],
    right_index: int,
    *,
    reference_window_sec: int,
    probe_window_sec: int,
    minimum_break_bps: float,
    requires_close_back_inside: bool,
    minimum_path_coverage_ratio: float,
) -> int:
    decision_ts = candles[right_index].timestamp_utc
    reference = [
        candle
        for candle in candles[: right_index + 1]
        if decision_ts - reference_window_sec
        < candle.timestamp_utc
        <= decision_ts - probe_window_sec
    ]
    probe = [
        candle
        for candle in candles[: right_index + 1]
        if decision_ts - probe_window_sec < candle.timestamp_utc <= decision_ts
    ]
    expected_reference = max(1, math.ceil((reference_window_sec - probe_window_sec) / 60))
    expected_probe = max(1, math.ceil(probe_window_sec / 60))
    if (
        len(reference) / expected_reference < minimum_path_coverage_ratio
        or len(probe) / expected_probe < minimum_path_coverage_ratio
    ):
        return 0
    reference_high = max(candle.high for candle in reference)
    reference_low = min(candle.low for candle in reference)
    close = probe[-1].close
    break_fraction = minimum_break_bps / 10_000.0
    upper = max(candle.high for candle in probe) >= reference_high * (1 + break_fraction)
    lower = min(candle.low for candle in probe) <= reference_low * (1 - break_fraction)
    upper_rejected = upper and (
        not requires_close_back_inside or close <= reference_high
    )
    lower_rejected = lower and (
        not requires_close_back_inside or close >= reference_low
    )
    if upper_rejected == lower_rejected:
        return 0
    return -1 if upper_rejected else 1


def build_futures_outcomes(
    candles: Sequence[FuturesCandle],
    decision_timestamps: Iterable[float],
    *,
    horizons_sec: Iterable[int],
    max_alignment_sec: float,
    false_sweep_break_bps: float = 2.0,
    false_sweep_reference_window_sec: int = 1800,
    false_sweep_probe_window_sec: int = 900,
    false_sweep_requires_close_back_inside: bool = True,
    trailing_lookbacks_sec: Iterable[int] = (300, 900, 1800),
    minimum_path_coverage_ratio: float = 0.95,
) -> dict[tuple[float, int], FuturesOutcome]:
    """Align decisions and future labels; future candles are never used in features."""
    ordered = sorted(candles, key=lambda candle: candle.timestamp_utc)
    timestamps = [candle.timestamp_utc for candle in ordered]
    outcomes: dict[tuple[float, int], FuturesOutcome] = {}
    for decision in sorted(set(float(value) for value in decision_timestamps)):
        right = bisect.bisect_right(timestamps, decision) - 1
        if right < 0 or decision - timestamps[right] > max_alignment_sec:
            continue
        start = ordered[right]
        trailing_returns: dict[int, float] = {}
        for lookback in sorted(set(int(value) for value in trailing_lookbacks_sec)):
            trailing_target = decision - lookback
            trailing_index = bisect.bisect_left(timestamps, trailing_target)
            if (
                trailing_index < len(ordered)
                and abs(timestamps[trailing_index] - trailing_target) <= max_alignment_sec
                and ordered[trailing_index].close > 0
                and (
                    right - trailing_index + 1
                )
                / max(1, math.ceil(lookback / 60) + 1)
                >= minimum_path_coverage_ratio
            ):
                trailing_returns[lookback] = (
                    start.close / ordered[trailing_index].close - 1.0
                ) * 100.0
        sweep_direction = _false_sweep_direction(
            ordered,
            right,
            reference_window_sec=false_sweep_reference_window_sec,
            probe_window_sec=false_sweep_probe_window_sec,
            minimum_break_bps=false_sweep_break_bps,
            requires_close_back_inside=false_sweep_requires_close_back_inside,
            minimum_path_coverage_ratio=minimum_path_coverage_ratio,
        )
        for horizon in sorted(set(int(value) for value in horizons_sec)):
            target = decision + horizon
            end_index = bisect.bisect_left(timestamps, target)
            if end_index >= len(ordered) or abs(timestamps[end_index] - target) > max_alignment_sec:
                continue
            end = ordered[end_index]
            path = ordered[right + 1 : end_index + 1]
            expected_path_candles = max(1, math.ceil(horizon / 60))
            if (
                not path
                or len(path) / expected_path_candles < minimum_path_coverage_ratio
            ):
                continue
            future_return = (end.close / start.close - 1.0) * 100.0
            future_high = max(candle.high for candle in path)
            future_low = min(candle.low for candle in path)
            future_range = (future_high - future_low) / start.close * 100.0
            outcomes[(decision, horizon)] = FuturesOutcome(
                decision_timestamp_utc=decision,
                horizon_sec=horizon,
                start_close=start.close,
                end_close=end.close,
                future_return_pct=future_return,
                future_range_pct=future_range,
                false_sweep_direction=sweep_direction,
                trailing_returns_pct=trailing_returns,
            )
    return outcomes


def prior_day_quantile_observations(
    observations: Iterable[tuple[float, float]],
    *,
    training_days: int,
    quantile: float,
) -> list[ThresholdObservation]:
    """Apply thresholds learned only from complete prior UTC days."""
    cleaned, by_day, days, thresholds = prior_day_quantile_thresholds(
        observations, training_days=training_days, quantile=quantile
    )
    del cleaned
    selected: list[ThresholdObservation] = []
    for day in days:
        threshold = thresholds.get(day)
        if threshold is None:
            continue
        for timestamp, value in by_day[day]:
            if abs(value) >= threshold:
                selected.append(ThresholdObservation(timestamp, value, threshold))
    return selected


def prior_day_quantile_thresholds(
    observations: Iterable[tuple[float, float]],
    *,
    training_days: int,
    quantile: float,
) -> tuple[
    list[tuple[float, float]],
    dict[str, list[tuple[float, float]]],
    list[str],
    dict[str, float],
]:
    """Return each test day's threshold and the chronologically grouped inputs."""
    if training_days < 1 or not 0 < quantile < 1:
        raise ValueError("Invalid walk-forward threshold configuration")
    cleaned = sorted(
        (float(timestamp), float(value))
        for timestamp, value in observations
        if math.isfinite(float(timestamp)) and math.isfinite(float(value))
    )
    by_day: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for timestamp, value in cleaned:
        by_day[_utc_day(timestamp)].append((timestamp, value))
    days = sorted(by_day)
    thresholds: dict[str, float] = {}
    for index, day in enumerate(days):
        if index < training_days:
            continue
        prior_days = days[:index]
        training_values = [
            abs(value)
            for prior_day in prior_days
            for _, value in by_day[prior_day]
        ]
        if not training_values:
            continue
        thresholds[day] = float(np.quantile(np.asarray(training_values), quantile))
    return cleaned, dict(by_day), days, thresholds


def healthy_decision_timestamps(
    paths: Mapping[str, Path],
    *,
    interval_sec: int = 300,
    minimum_samples_per_exchange: int = 48,
) -> set[float]:
    """Return decision ends backed by clean within-bucket health on both feeds."""
    connection = _read_only(paths["option_trade_flow.db"])
    try:
        session_rows = connection.execute(
            """
            SELECT exchange, session_id, MAX(dropped_trade_count) AS drops
            FROM collector_status_history
            GROUP BY exchange, session_id
            """
        ).fetchall()
        bad_sessions = {
            (str(row["exchange"]), str(row["session_id"]))
            for row in session_rows
            if int(row["drops"] or 0) > 0
        }
        rows = connection.execute(
            """
            SELECT exchange, session_id, connection_state,
                   dropped_trade_count, updated_at_utc
            FROM collector_status_history
            ORDER BY updated_at_utc
            """
        ).fetchall()
    finally:
        connection.close()
    counts: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        key = (str(row["exchange"]), str(row["session_id"]))
        if (
            key in bad_sessions
            or row["connection_state"] != "subscribed"
            or int(row["dropped_trade_count"] or 0) != 0
        ):
            continue
        bucket = int(float(row["updated_at_utc"]) // interval_sec)
        counts[key[0]][bucket] += 1
    healthy = {
        exchange: {
            bucket
            for bucket, samples in exchange_counts.items()
            if samples >= minimum_samples_per_exchange
        }
        for exchange, exchange_counts in counts.items()
    }
    common = healthy.get("bybit", set()) & healthy.get("deribit", set())
    return {float((bucket + 1) * interval_sec) for bucket in common}


def _feature_value(point: FlowFeaturePoint, feature: str) -> float | None:
    if feature == "delta_contract_consensus":
        delta = point.signed_delta_imbalance
        contract = point.call_put_contract_imbalance
        if delta is None or contract is None or delta * contract <= 0:
            return 0.0
        value = (delta + contract) / 2.0
    elif feature == "delta_gamma_interaction":
        delta = point.signed_delta_imbalance
        gamma = point.absolute_signed_gamma_imbalance
        value = delta * gamma if delta is not None and gamma is not None else None
    elif feature == "gamma_vega_joint":
        gamma = point.absolute_signed_gamma_imbalance
        vega = point.absolute_signed_vega_imbalance
        value = min(gamma, vega) if gamma is not None and vega is not None else None
    elif feature == "delta_activity_interaction":
        delta = point.signed_delta_imbalance
        value = abs(delta) * math.log1p(point.trade_intensity) if delta is not None else None
    else:
        value = getattr(point, feature)
    if value is None:
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _healthy_lookback_window(
    timestamp_utc: float,
    lookback_sec: int,
    healthy_timestamps: set[float],
    *,
    interval_sec: int,
) -> bool:
    if lookback_sec < interval_sec or lookback_sec % interval_sec:
        return False
    return all(
        timestamp_utc - offset in healthy_timestamps
        for offset in range(0, lookback_sec, interval_sec)
    )


def _summarize_direction_rule(
    spec: DirectionRuleSpec,
    trades: Sequence[tuple[float, float, float, float]],
    protocol: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, list[float]]]:
    costs = [float(value) for value in protocol["futures_round_trip_cost_bps"]]
    mean_net = {
        str(int(cost) if cost.is_integer() else cost): float(
            np.mean([gross - cost / 100.0 for _, gross, _, _ in trades])
        )
        for cost in costs
    }
    highest_cost = max(costs)
    day_returns: dict[str, list[float]] = defaultdict(list)
    vs_continuation: dict[str, list[float]] = defaultdict(list)
    vs_reversal: dict[str, list[float]] = defaultdict(list)
    for timestamp, gross, continuation_gross, reversal_gross in trades:
        day = _utc_day(timestamp)
        day_returns[day].append(gross - highest_cost / 100.0)
        vs_continuation[day].append(gross - continuation_gross)
        vs_reversal[day].append(gross - reversal_gross)
    day_means = [float(np.mean(values)) for values in day_returns.values()]
    positive_ratio = (
        sum(value > 0 for value in day_means) / len(day_means) if day_means else 0.0
    )
    raw_p = one_sided_daily_p_value(day_returns)
    preliminary_bootstrap = (
        len(trades) >= int(protocol["minimum_trades"])
        and len(day_returns) >= int(protocol["minimum_test_days"])
        and mean_net[str(int(highest_cost) if highest_cost.is_integer() else highest_cost)] > 0
        and raw_p < 0.05
    )
    if preliminary_bootstrap:
        seed_material = hashlib.sha256(spec.rule_id.encode("utf-8")).digest()[:8]
        seed = int.from_bytes(seed_material, "big") ^ int(protocol["random_seed"])
        confidence_interval = day_block_bootstrap_ci(
            day_returns,
            samples=int(protocol["bootstrap_samples"]),
            seed=seed,
        )
        continuation_difference_ci = day_block_bootstrap_ci(
            vs_continuation,
            samples=int(protocol["bootstrap_samples"]),
            seed=seed ^ 0xC01A,
        )
        reversal_difference_ci = day_block_bootstrap_ci(
            vs_reversal,
            samples=int(protocol["bootstrap_samples"]),
            seed=seed ^ 0xAE71,
        )
    else:
        confidence_interval = (float("nan"), float("nan"))
        continuation_difference_ci = (float("nan"), float("nan"))
        reversal_difference_ci = (float("nan"), float("nan"))
    continuation_mean = float(
        np.mean([gross - highest_cost / 100.0 for _, _, gross, _ in trades])
    )
    reversal_mean = float(
        np.mean([gross - highest_cost / 100.0 for _, _, _, gross in trades])
    )
    summary = {
        "rule_id": spec.rule_id,
        "spec": {
            "scope": spec.scope,
            "trade_filter": spec.trade_filter,
            "segment": spec.segment,
            "lookback_sec": spec.lookback_sec,
            "feature": spec.feature,
            "context": spec.context,
            "horizon_sec": spec.horizon_sec,
            "mode": spec.mode,
        },
        "trades": len(trades),
        "test_days": len(day_returns),
        "mean_gross_pct": float(np.mean([gross for _, gross, _, _ in trades])),
        "mean_net_pct_by_cost": mean_net,
        "positive_day_ratio_15bps": positive_ratio,
        "ci_95_15bps": list(confidence_interval),
        "price_only_controls_15bps": {
            "continuation_mean_net_pct": continuation_mean,
            "reversal_mean_net_pct": reversal_mean,
            "best_mean_net_pct": max(continuation_mean, reversal_mean),
        },
        "ci_95_vs_price_continuation": list(continuation_difference_ci),
        "ci_95_vs_price_reversal": list(reversal_difference_ci),
        "raw_daily_p": raw_p,
        "holm_p": 1.0,
        "max_t_p": 1.0,
        "exchange_sign_confirmation": False,
    }
    return summary, dict(day_returns)


def evaluate_direction_rules(
    feature_points: Iterable[FlowFeaturePoint],
    outcomes: Mapping[tuple[float, int], FuturesOutcome],
    healthy_timestamps: set[float],
    protocol: Mapping[str, Any],
    decision_contexts: Mapping[float, DecisionContext] | None = None,
) -> list[dict[str, Any]]:
    """Run every frozen direction rule with prior-day thresholds and no overlap."""
    grouped: dict[tuple[str, str, str, int, str], list[tuple[float, float]]] = defaultdict(list)
    direction_features = set(protocol["direction_features"])
    minimum_greek_match = float(protocol.get("minimum_greek_match_ratio", 0.0))
    interval_sec = int(protocol["decision_interval_sec"])
    for point in feature_points:
        if not _healthy_lookback_window(
            point.timestamp_utc,
            point.lookback_sec,
            healthy_timestamps,
            interval_sec=interval_sec,
        ):
            continue
        for feature in direction_features:
            value = _feature_value(point, feature)
            if value is None:
                continue
            if feature in {
                "signed_delta_imbalance",
                "delta_contract_consensus",
                "delta_gamma_interaction",
            } and point.greek_match_ratio < minimum_greek_match:
                continue
            grouped[
                (
                    point.scope,
                    point.trade_filter,
                    point.segment,
                    point.lookback_sec,
                    feature,
                )
            ].append((point.timestamp_utc, value))
    summaries: dict[str, dict[str, Any]] = {}
    daily_returns: dict[str, dict[str, list[float]]] = {}
    specs: dict[str, DirectionRuleSpec] = {}
    for (scope, trade_filter, segment, lookback, feature), observations in grouped.items():
        selected = prior_day_quantile_observations(
            observations,
            training_days=int(protocol["training_days"]),
            quantile=float(protocol["signal_quantile"]),
        )
        for context in protocol["contexts"]:
            for horizon in protocol["horizons_sec"]:
                for mode in protocol["direction_modes"]:
                    spec = DirectionRuleSpec(
                        scope=scope,
                        trade_filter=trade_filter,
                        segment=segment,
                        lookback_sec=int(lookback),
                        feature=feature,
                        context=str(context),
                        horizon_sec=int(horizon),
                        mode=str(mode),
                    )
                    next_allowed = -math.inf
                    trades: list[tuple[float, float, float, float]] = []
                    for observation in selected:
                        timestamp = observation.timestamp_utc
                        if timestamp < next_allowed:
                            continue
                        outcome = outcomes.get((timestamp, int(horizon)))
                        if outcome is None:
                            continue
                        if not _context_matches(
                            str(context),
                            outcome,
                            (decision_contexts or {}).get(timestamp),
                        ):
                            continue
                        signal = 1 if observation.value > 0 else -1
                        if mode == "inverse":
                            signal *= -1
                        trailing_return = outcome.trailing_returns_pct.get(int(lookback))
                        if trailing_return is None or trailing_return == 0:
                            continue
                        price_signal = 1 if trailing_return > 0 else -1
                        trades.append(
                            (
                                timestamp,
                                signal * outcome.future_return_pct,
                                price_signal * outcome.future_return_pct,
                                -price_signal * outcome.future_return_pct,
                            )
                        )
                        next_allowed = timestamp + int(horizon)
                    if not trades:
                        continue
                    summary, day_values = _summarize_direction_rule(spec, trades, protocol)
                    summaries[spec.rule_id] = summary
                    daily_returns[spec.rule_id] = day_values
                    specs[spec.rule_id] = spec
    if not summaries:
        return []
    holm = holm_adjust(
        {rule_id: float(summary["raw_daily_p"]) for rule_id, summary in summaries.items()}
    )
    highest_cost = max(float(value) for value in protocol["futures_round_trip_cost_bps"])
    max_t_input = {
        rule_id: {
            day: float(np.mean(values))
            for day, values in day_values.items()
            if values
        }
        for rule_id, day_values in daily_returns.items()
    }
    max_t = shared_day_max_t_p_values(
        max_t_input,
        samples=int(protocol["permutation_samples"]),
        seed=int(protocol["random_seed"]),
    )
    by_signature: dict[tuple[Any, ...], dict[str, dict[str, Any]]] = defaultdict(dict)
    for rule_id, spec in specs.items():
        by_signature[spec.cross_exchange_key][spec.scope] = summaries[rule_id]
    for rule_id, summary in summaries.items():
        spec = specs[rule_id]
        summary["holm_p"] = holm[rule_id]
        summary["max_t_p"] = max_t[rule_id]
        peers = by_signature[spec.cross_exchange_key]
        if spec.scope == "combined" and {"bybit", "deribit"} <= set(peers):
            combined_mean = float(summary["mean_gross_pct"])
            exchange_means = [
                float(peers[exchange]["mean_gross_pct"])
                for exchange in ("bybit", "deribit")
            ]
            summary["exchange_sign_confirmation"] = (
                combined_mean != 0
                and all(value != 0 and value * combined_mean > 0 for value in exchange_means)
            )
        summary["promotion"] = promotion_decision(summary, protocol)
        summary["highest_cost_bps"] = highest_cost
    cost_key = str(int(highest_cost) if highest_cost.is_integer() else highest_cost)
    return sorted(
        summaries.values(),
        key=lambda item: float(item["mean_net_pct_by_cost"][cost_key]),
        reverse=True,
    )


def evaluate_range_rules(
    feature_points: Iterable[FlowFeaturePoint],
    outcomes: Mapping[tuple[float, int], FuturesOutcome],
    healthy_timestamps: set[float],
    protocol: Mapping[str, Any],
    decision_contexts: Mapping[float, DecisionContext] | None = None,
) -> list[dict[str, Any]]:
    """Test whether frozen high-flow states predict a larger future ETH range."""
    grouped: dict[tuple[str, str, str, int, str], list[tuple[float, float]]] = defaultdict(list)
    range_features = set(protocol["range_features"])
    minimum_greek_match = float(protocol.get("minimum_greek_match_ratio", 0.0))
    interval_sec = int(protocol["decision_interval_sec"])
    for point in feature_points:
        if not _healthy_lookback_window(
            point.timestamp_utc,
            point.lookback_sec,
            healthy_timestamps,
            interval_sec=interval_sec,
        ):
            continue
        for feature in range_features:
            value = _feature_value(point, feature)
            if value is None:
                continue
            if feature in {
                "absolute_signed_gamma_imbalance",
                "absolute_signed_vega_imbalance",
                "gamma_vega_joint",
                "delta_activity_interaction",
            } and point.greek_match_ratio < minimum_greek_match:
                continue
            grouped[
                (
                    point.scope,
                    point.trade_filter,
                    point.segment,
                    point.lookback_sec,
                    feature,
                )
            ].append((point.timestamp_utc, value))
    summaries: dict[str, dict[str, Any]] = {}
    day_lifts_by_rule: dict[str, dict[str, list[float]]] = {}
    specs: dict[str, RangeRuleSpec] = {}
    for (scope, trade_filter, segment, lookback, feature), observations in grouped.items():
        _, by_day, days, thresholds = prior_day_quantile_thresholds(
            observations,
            training_days=int(protocol["training_days"]),
            quantile=float(protocol["signal_quantile"]),
        )
        for context in protocol["contexts"]:
            for horizon in protocol["horizons_sec"]:
                spec = RangeRuleSpec(
                    scope=scope,
                    trade_filter=trade_filter,
                    segment=segment,
                    lookback_sec=int(lookback),
                    feature=feature,
                    context=str(context),
                    horizon_sec=int(horizon),
                )
                high_by_day: dict[str, list[float]] = defaultdict(list)
                low_by_day: dict[str, list[float]] = defaultdict(list)
                next_allowed = -math.inf
                for day in days:
                    threshold = thresholds.get(day)
                    if threshold is None:
                        continue
                    for timestamp, value in by_day[day]:
                        if timestamp < next_allowed:
                            continue
                        outcome = outcomes.get((timestamp, int(horizon)))
                        if outcome is None:
                            continue
                        if not _context_matches(
                            str(context),
                            outcome,
                            (decision_contexts or {}).get(timestamp),
                        ):
                            continue
                        target = high_by_day if abs(value) >= threshold else low_by_day
                        target[day].append(outcome.future_range_pct)
                        next_allowed = timestamp + int(horizon)
                common_days = sorted(set(high_by_day) & set(low_by_day))
                if not common_days:
                    continue
                day_lifts = {
                    day: [
                        float(np.mean(high_by_day[day]) - np.mean(low_by_day[day]))
                    ]
                    for day in common_days
                }
                high_values = [value for values in high_by_day.values() for value in values]
                low_values = [value for values in low_by_day.values() for value in values]
                if not high_values or not low_values:
                    continue
                eligible = (
                    len(high_values) >= int(protocol["minimum_trades"])
                    and len(day_lifts) >= int(protocol["minimum_test_days"])
                    and float(np.mean([values[0] for values in day_lifts.values()])) > 0
                    and one_sided_daily_p_value(day_lifts) < 0.05
                )
                if eligible:
                    seed_material = hashlib.sha256(spec.rule_id.encode("utf-8")).digest()[:8]
                    seed = int.from_bytes(seed_material, "big") ^ int(protocol["random_seed"])
                    confidence_interval = day_block_bootstrap_ci(
                        day_lifts,
                        samples=int(protocol["bootstrap_samples"]),
                        seed=seed,
                    )
                else:
                    confidence_interval = (float("nan"), float("nan"))
                summary = {
                    "rule_id": spec.rule_id,
                    "spec": {
                        "scope": spec.scope,
                        "trade_filter": spec.trade_filter,
                        "segment": spec.segment,
                        "lookback_sec": spec.lookback_sec,
                        "feature": spec.feature,
                        "context": spec.context,
                        "horizon_sec": spec.horizon_sec,
                    },
                    "high_observations": len(high_values),
                    "low_observations": len(low_values),
                    "paired_test_days": len(day_lifts),
                    "mean_high_range_pct": float(np.mean(high_values)),
                    "mean_low_range_pct": float(np.mean(low_values)),
                    "mean_range_lift_pct_points": float(
                        np.mean([values[0] for values in day_lifts.values()])
                    ),
                    "ci_95_range_lift": list(confidence_interval),
                    "raw_daily_p": one_sided_daily_p_value(day_lifts),
                    "holm_p": 1.0,
                    "max_t_p": 1.0,
                    "exchange_sign_confirmation": False,
                    "statistically_confirmed": False,
                    "live_entry_changes_allowed": False,
                }
                summaries[spec.rule_id] = summary
                day_lifts_by_rule[spec.rule_id] = day_lifts
                specs[spec.rule_id] = spec
    if not summaries:
        return []
    holm = holm_adjust(
        {rule_id: float(summary["raw_daily_p"]) for rule_id, summary in summaries.items()}
    )
    max_t = shared_day_max_t_p_values(
        {
            rule_id: {day: values[0] for day, values in lifts.items()}
            for rule_id, lifts in day_lifts_by_rule.items()
        },
        samples=int(protocol["permutation_samples"]),
        seed=int(protocol["random_seed"]),
    )
    by_signature: dict[tuple[Any, ...], dict[str, dict[str, Any]]] = defaultdict(dict)
    for rule_id, spec in specs.items():
        by_signature[spec.cross_exchange_key][spec.scope] = summaries[rule_id]
    for rule_id, summary in summaries.items():
        spec = specs[rule_id]
        summary["holm_p"] = holm[rule_id]
        summary["max_t_p"] = max_t[rule_id]
        peers = by_signature[spec.cross_exchange_key]
        if spec.scope == "combined" and {"bybit", "deribit"} <= set(peers):
            combined_lift = float(summary["mean_range_lift_pct_points"])
            exchange_lifts = [
                float(peers[exchange]["mean_range_lift_pct_points"])
                for exchange in ("bybit", "deribit")
            ]
            summary["exchange_sign_confirmation"] = (
                combined_lift > 0 and all(value > 0 for value in exchange_lifts)
            )
        ci_lower = float(summary["ci_95_range_lift"][0])
        range_alpha = float(protocol["range_multiple_testing"]["holm_alpha"])
        max_t_alpha = float(
            protocol["range_multiple_testing"]["shared_day_sign_flip_max_t_alpha"]
        )
        summary["statistically_confirmed"] = (
            int(summary["high_observations"]) >= int(protocol["minimum_trades"])
            and int(summary["paired_test_days"]) >= int(protocol["minimum_test_days"])
            and math.isfinite(ci_lower)
            and ci_lower > 0
            and float(summary["holm_p"]) < range_alpha
            and float(summary["max_t_p"]) < max_t_alpha
            and bool(summary["exchange_sign_confirmation"])
        )
    return sorted(
        summaries.values(),
        key=lambda item: float(item["mean_range_lift_pct_points"]),
        reverse=True,
    )


def holm_adjust(p_values: Mapping[str, float]) -> dict[str, float]:
    """Holm step-down family-wise error correction."""
    finite = sorted(
        ((key, min(1.0, max(0.0, float(value)))) for key, value in p_values.items()),
        key=lambda item: item[1],
    )
    adjusted: dict[str, float] = {}
    running = 0.0
    total = len(finite)
    for rank, (key, value) in enumerate(finite):
        running = max(running, (total - rank) * value)
        adjusted[key] = min(1.0, running)
    return adjusted


def one_sided_daily_p_value(day_returns: Mapping[str, Sequence[float]]) -> float:
    day_means = np.asarray(
        [np.mean(values) for values in day_returns.values() if len(values)], dtype=float
    )
    if day_means.size < 2 or np.allclose(day_means, day_means[0]):
        return 0.0 if day_means.size and day_means[0] > 0 else 1.0
    result = stats.ttest_1samp(day_means, popmean=0.0, alternative="greater")
    return float(result.pvalue) if math.isfinite(float(result.pvalue)) else 1.0


def day_block_bootstrap_ci(
    day_returns: Mapping[str, Sequence[float]],
    *,
    samples: int,
    seed: int,
) -> tuple[float, float]:
    """Bootstrap complete UTC days while preserving within-day dependence."""
    blocks = [np.asarray(values, dtype=float) for values in day_returns.values() if len(values)]
    if not blocks:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    estimates = np.empty(samples, dtype=float)
    for index in range(samples):
        chosen = rng.integers(0, len(blocks), size=len(blocks))
        estimates[index] = float(np.mean(np.concatenate([blocks[item] for item in chosen])))
    low, high = np.quantile(estimates, [0.025, 0.975])
    return float(low), float(high)


def _t_statistic(values: np.ndarray) -> float:
    finite = values[np.isfinite(values)]
    if finite.size < 2:
        return 0.0
    deviation = float(np.std(finite, ddof=1))
    if deviation == 0:
        return math.inf if float(np.mean(finite)) > 0 else 0.0
    return float(np.mean(finite) / (deviation / math.sqrt(finite.size)))


def shared_day_max_t_p_values(
    rule_day_means: Mapping[str, Mapping[str, float]],
    *,
    samples: int,
    seed: int,
) -> dict[str, float]:
    """Westfall-style max-T sign flip sharing the same sign per day across rules."""
    if not rule_day_means:
        return {}
    days = sorted({day for values in rule_day_means.values() for day in values})
    rules = list(rule_day_means)
    matrix = np.full((len(rules), len(days)), np.nan, dtype=float)
    day_index = {day: index for index, day in enumerate(days)}
    for row_index, rule in enumerate(rules):
        for day, value in rule_day_means[rule].items():
            matrix[row_index, day_index[day]] = float(value)
    observed = np.asarray([_t_statistic(row) for row in matrix])
    filled = np.nan_to_num(matrix, nan=0.0)
    counts = np.sum(np.isfinite(matrix), axis=1).astype(float)
    sum_squares = np.sum(filled * filled, axis=1)
    rng = np.random.default_rng(seed)
    exceedances = np.zeros(len(rules), dtype=int)
    completed = 0
    while completed < samples:
        batch_size = min(128, samples - completed)
        signs = rng.choice(
            np.asarray([-1.0, 1.0]), size=(batch_size, len(days))
        )
        signed_sums = filled @ signs.T
        means = np.divide(
            signed_sums,
            counts[:, None],
            out=np.zeros_like(signed_sums),
            where=counts[:, None] > 0,
        )
        variance_numerators = sum_squares[:, None] - np.divide(
            signed_sums * signed_sums,
            counts[:, None],
            out=np.zeros_like(signed_sums),
            where=counts[:, None] > 0,
        )
        variances = np.divide(
            variance_numerators,
            (counts - 1)[:, None],
            out=np.zeros_like(signed_sums),
            where=(counts - 1)[:, None] > 0,
        )
        standard_errors = np.sqrt(np.maximum(variances, 0.0)) / np.sqrt(
            np.maximum(counts[:, None], 1.0)
        )
        t_values = np.divide(
            means,
            standard_errors,
            out=np.zeros_like(means),
            where=standard_errors > 0,
        )
        t_values[(standard_errors == 0) & (means > 0)] = math.inf
        maxima = np.max(t_values, axis=0)
        exceedances += np.sum(maxima[None, :] >= observed[:, None], axis=1)
        completed += batch_size
    return {
        rule: float((exceedances[index] + 1) / (samples + 1))
        for index, rule in enumerate(rules)
    }


def promotion_decision(summary: Mapping[str, Any], protocol: Mapping[str, Any]) -> dict[str, Any]:
    """Apply every frozen economic and statistical gate without exceptions."""
    gate = protocol["promotion_gate"]
    costs = [str(value) for value in protocol["futures_round_trip_cost_bps"]]
    checks = {
        "minimum_trades": int(summary.get("trades", 0)) >= int(protocol["minimum_trades"]),
        "minimum_test_days": int(summary.get("test_days", 0)) >= int(protocol["minimum_test_days"]),
        "positive_day_ratio": float(summary.get("positive_day_ratio_15bps", 0.0))
        >= float(protocol["minimum_positive_day_ratio"]),
        "positive_every_cost": all(
            float(summary.get("mean_net_pct_by_cost", {}).get(cost, -math.inf)) > 0
            for cost in costs
        ),
        "ci_lower_positive_15bps": float(summary.get("ci_95_15bps", [-math.inf])[0]) > 0,
        "beats_price_continuation": float(
            summary.get("ci_95_vs_price_continuation", [-math.inf])[0]
        )
        > 0,
        "beats_price_reversal": float(
            summary.get("ci_95_vs_price_reversal", [-math.inf])[0]
        )
        > 0,
        "holm": float(summary.get("holm_p", 1.0)) < float(gate["holm_adjusted_p_below"]),
        "max_t": float(summary.get("max_t_p", 1.0))
        < float(gate["max_t_adjusted_p_below"]),
        "exchange_sign_confirmation": bool(summary.get("exchange_sign_confirmation", False)),
    }
    return {
        "promote": all(checks.values()) and bool(gate.get("live_entry_changes_allowed", False)),
        "statistically_confirmed": all(checks.values()),
        "live_entry_changes_allowed": bool(gate.get("live_entry_changes_allowed", False)),
        "checks": checks,
    }


def analyze_readiness(input_path: Path | str, protocol_path: Path | str = DEFAULT_PROTOCOL_PATH) -> dict[str, Any]:
    protocol = load_protocol(protocol_path)
    with dataset_files(input_path) as paths:
        return audit_dataset(paths, protocol)


def run_frozen_analysis(
    input_path: Path | str,
    protocol_path: Path | str = DEFAULT_PROTOCOL_PATH,
) -> dict[str, Any]:
    protocol = load_protocol(protocol_path)
    with dataset_files(input_path) as paths:
        audit = audit_dataset(paths, protocol)
        if audit["status"] != "ready":
            return {
                "status": "not_ready",
                "audit": audit,
                "direction_rules": [],
                "range_rules": [],
            }
        trades = load_enriched_option_trades(
            paths, greek_max_age_sec=float(protocol["greek_max_age_sec"])
        )
        buckets = aggregate_trade_flow_buckets(
            trades, interval_sec=int(protocol["decision_interval_sec"])
        )
        feature_points = rolling_flow_feature_points(
            buckets,
            lookbacks_sec=protocol["lookbacks_sec"],
            interval_sec=int(protocol["decision_interval_sec"]),
        )
        healthy = healthy_decision_timestamps(
            paths,
            interval_sec=int(protocol["decision_interval_sec"]),
            minimum_samples_per_exchange=int(
                protocol["minimum_status_samples_per_5m_per_exchange"]
            ),
        )
        decision_times = {
            point.timestamp_utc for point in feature_points if point.timestamp_utc in healthy
        }
        outcomes = build_futures_outcomes(
            load_futures_candles(paths),
            decision_times,
            horizons_sec=protocol["horizons_sec"],
            max_alignment_sec=float(protocol["ohlcv_max_alignment_sec"]),
            false_sweep_break_bps=float(protocol["false_sweep"]["minimum_break_bps"]),
            false_sweep_reference_window_sec=int(
                protocol["false_sweep"]["reference_window_sec"]
            ),
            false_sweep_probe_window_sec=int(
                protocol["false_sweep"]["probe_window_sec"]
            ),
            false_sweep_requires_close_back_inside=bool(
                protocol["false_sweep"]["requires_close_back_inside"]
            ),
            trailing_lookbacks_sec=protocol["lookbacks_sec"],
            minimum_path_coverage_ratio=float(
                protocol["minimum_ohlcv_path_coverage_ratio"]
            ),
        )
        decision_contexts = load_decision_contexts(
            paths,
            decision_times,
            max_age_sec=float(protocol["mos_context_max_age_sec"]),
        )
        direction_rules = evaluate_direction_rules(
            feature_points, outcomes, healthy, protocol, decision_contexts
        )
        range_rules = evaluate_range_rules(
            feature_points, outcomes, healthy, protocol, decision_contexts
        )
        confirmed = [
            rule for rule in direction_rules if rule["promotion"]["statistically_confirmed"]
        ]
        confirmed_range = [
            rule for rule in range_rules if rule["statistically_confirmed"]
        ]
        return {
            "status": "complete",
            "research_version": OPTION_FLOW_RESEARCH_VERSION,
            "audit": audit,
            "protocol": protocol["protocol"],
            "protocol_sha256": protocol_content_sha256(protocol),
            "enriched_trades": len(trades),
            "greek_matched_trades": sum(trade.delta is not None for trade in trades),
            "feature_points": len(feature_points),
            "healthy_decision_timestamps": len(healthy),
            "outcomes": len(outcomes),
            "decision_contexts": len(decision_contexts),
            "direction_rule_count": len(direction_rules),
            "statistically_confirmed_rule_count": len(confirmed),
            "range_rule_count": len(range_rules),
            "statistically_confirmed_range_rule_count": len(confirmed_range),
            "live_entry_change_count": sum(
                rule["promotion"]["promote"] for rule in direction_rules
            ),
            "direction_rules": direction_rules,
            "range_rules": range_rules,
        }


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def render_human_summary(result: Mapping[str, Any]) -> str:
    if result.get("status") == "error":
        return "\n".join(
            (
                "MOS OPTION FLOW RESEARCH",
                "Status: DATA NOT AVAILABLE",
                f"Reason: {result.get('message', 'unknown error')}",
                "Start the v68/v64 collector and run this check again later.",
            )
        )
    audit = result.get("audit", result)
    ready = audit.get("status") == "ready"
    lines = [
        "MOS OPTION FLOW RESEARCH",
        f"Status: {'READY FOR FROZEN ANALYSIS' if ready else 'COLLECTING CLEAN DATA'}",
        f"Trades: {int(audit.get('trades', 0))}",
        f"Calendar overlap: {float(audit.get('common_overlap_days', 0.0)):.2f} days",
        f"Clean full-lookback coverage: {float(audit.get('healthy_full_lookback_days', 0.0)):.2f} days",
        f"Clean days remaining: {float(audit.get('healthy_days_remaining', 0.0)):.2f}",
        f"Progress: {100.0 * float(audit.get('healthy_progress_ratio', 0.0)):.1f}%",
        f"Historical queue drops: {int(audit.get('historical_dropped_trades', 0))}",
    ]
    blockers = list(audit.get("blockers", []))
    if blockers:
        lines.append("Waiting for: " + ", ".join(blockers))
    if ready:
        lines.append("The frozen analysis may now be run; live entries remain unchanged.")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit MOS option-flow research readiness")
    parser.add_argument("input", type=Path, help="Dataset directory or MOS exporter ZIP")
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL_PATH)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--human", action="store_true", help="Print a short collector-facing summary"
    )
    parser.add_argument(
        "--run-analysis",
        "--run-direction-analysis",
        dest="run_analysis",
        action="store_true",
        help="Run the frozen direction and range families after readiness passes",
    )
    args = parser.parse_args(argv)
    try:
        result = (
            run_frozen_analysis(args.input, args.protocol)
            if args.run_analysis
            else analyze_readiness(args.input, args.protocol)
        )
    except (ResearchInputError, sqlite3.DatabaseError, OSError, json.JSONDecodeError) as exc:
        result = {
            "status": "error",
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
    rendered = json.dumps(_json_safe(result), ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(render_human_summary(result) if args.human else rendered)
    return 0 if result["status"] in {"ready", "complete"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
