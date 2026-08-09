"""Frozen statistical foundation for public BTC option trade-flow research.

This module is offline-only. It audits a MOS Dataset Exporter directory or ZIP,
freezes the protocol hash, and provides leakage-safe walk-forward and
multiple-testing primitives. It never imports or changes live MOS scoring.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import sqlite3
import tempfile
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

import numpy as np
from scipy import stats


REQUIRED_FILES = ("mos_research.db", "history.db", "option_trade_flow.db")
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
        schema_version = (
            _scalar(flow, "SELECT value FROM metadata WHERE key='schema_version'")
            if "metadata" in flow_tables
            else None
        )
        integrity = _scalar(flow, "PRAGMA quick_check")
        trade_rows = []
        if "option_trades" in flow_tables:
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
        if "collector_status_history" in flow_tables:
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
        # At a five-second heartbeat cadence, 30 samples prove at least 50%
        # observed health within the five-minute decision bucket.
        healthy_buckets = {
            exchange: {
                bucket for bucket, samples in counts.items() if samples >= 30
            }
            for exchange, counts in healthy_bucket_samples.items()
        }
        exchanges = {str(row["exchange"]) for row in trade_rows}
        common_healthy = set.intersection(
            *(healthy_buckets.get(exchange, set()) for exchange in ("bybit", "deribit"))
        ) if healthy_buckets else set()
        healthy_days = len(common_healthy) * 300.0 / 86400.0

        research_tables = _table_names(research)
        if "ohlcv_candles" in research_tables:
            candle_range = dict(
                research.execute(
                    """
                    SELECT COUNT(*) AS candles,
                           MIN(timestamp_utc) AS first_candle_utc,
                           MAX(timestamp_utc) AS last_candle_utc
                    FROM ohlcv_candles
                    WHERE symbol='BTCUSDT' AND timeframe='1m'
                    """
                ).fetchone()
            )
        else:
            candle_range = {"candles": 0, "first_candle_utc": None, "last_candle_utc": None}
        history_tables = _table_names(history)
        contract_range = {"contract_snapshots": 0, "first_snapshot_utc": None, "last_snapshot_utc": None}
        if "option_contract_snapshots" in history_tables:
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
        if schema_version != "1.1":
            blockers.append("quality_history_schema_not_v1_1")
        if exchanges != {"bybit", "deribit"}:
            blockers.append("both_exchanges_not_present")
        if historical_drops:
            blockers.append("sessions_with_queue_drops_present")
        if overlap_days < minimum_days:
            blockers.append("insufficient_common_calendar_days")
        if healthy_days < minimum_days:
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
            "ohlcv": candle_range,
            "contract_snapshots": contract_range,
        }
    finally:
        flow.close()
        research.close()
        history.close()


def prior_day_quantile_observations(
    observations: Iterable[tuple[float, float]],
    *,
    training_days: int,
    quantile: float,
) -> list[ThresholdObservation]:
    """Apply thresholds learned only from complete prior UTC days."""
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
    selected: list[ThresholdObservation] = []
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
        threshold = float(np.quantile(np.asarray(training_values), quantile))
        for timestamp, value in by_day[day]:
            if abs(value) >= threshold:
                selected.append(ThresholdObservation(timestamp, value, threshold))
    return selected


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
    rng = np.random.default_rng(seed)
    exceedances = np.zeros(len(rules), dtype=int)
    for _ in range(samples):
        signs = rng.choice(np.asarray([-1.0, 1.0]), size=len(days))
        maximum = max(_t_statistic(row * signs) for row in matrix)
        exceedances += maximum >= observed
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit MOS option-flow research readiness")
    parser.add_argument("input", type=Path, help="Dataset directory or MOS exporter ZIP")
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL_PATH)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = analyze_readiness(args.input, args.protocol)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
