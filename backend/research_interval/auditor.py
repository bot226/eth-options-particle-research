"""Fail-closed, read-only audit of ETH MOS baseline and interval archives."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Iterable, Iterator, Mapping
import zipfile

import numpy as np

from backend.research_interval.exporter import (
    EXPECTED_PROTOCOL_SHA256,
    EXTRA_PARENT_RELATIONS,
    REQUIRED_DATABASES,
    _columns,
    _canonical_protocol_sha256,
    _eth_asset_identity,
    _relation_orphan_filter_sql,
    _readonly,
    _tables,
    iso_utc,
    parse_utc,
)
from backend.scripts.option_flow_research import (
    FuturesCandle,
    aggregate_trade_flow_buckets,
    audit_dataset as audit_option_flow,
    build_futures_outcomes,
    healthy_decision_timestamps,
    load_enriched_option_trades,
    load_futures_candles,
    load_protocol as load_option_flow_protocol,
    rolling_flow_feature_points,
    run_frozen_analysis,
)
from research.particle_shadow.replay import run_replay


ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_DIR = ROOT / "docs" / "mos"
ALL_HORIZONS_MINUTES = (5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 300, 360, 480, 600, 720)
H7_PROTOCOL = PROTOCOL_DIR / "MOS_TREND_BEFORE_COMPRESSION_PREREG_V1.json"
H7_HASH = EXPECTED_PROTOCOL_SHA256[H7_PROTOCOL.name]


class IntervalAuditError(RuntimeError):
    """Raised when archive trust or ETH identity cannot be established."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


@contextmanager
def open_archive(source: str | Path) -> Iterator[tuple[Path, dict, dict]]:
    """Extract known data members into scratch and never touch the source."""

    source = Path(source).resolve()
    if source.is_dir():
        manifest_path = source / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig")) if manifest_path.is_file() else {}
        yield source, manifest, {"source_type": "directory", "source_sha256": None}
        return
    if not source.is_file() or source.suffix.lower() != ".zip":
        raise IntervalAuditError("input_must_be_ETH_MOS_zip_or_directory")
    source_hash = _sha256(source)
    with tempfile.TemporaryDirectory(prefix="eth_mos_interval_audit_") as temporary:
        target = Path(temporary)
        with zipfile.ZipFile(source) as archive:
            broken = archive.testzip()
            if broken:
                raise IntervalAuditError(f"zip_crc_failed:{broken}")
            for member in archive.infolist():
                path = Path(member.filename.replace("\\", "/"))
                allowed = (
                    path.name in {"manifest.json", "quality.json"}
                    or path.suffix.lower() == ".db"
                    or (len(path.parts) == 2 and path.parts[0] == "protocols" and path.suffix.lower() == ".json")
                )
                if member.is_dir() or not allowed:
                    continue
                if path.is_absolute() or ".." in path.parts or len(path.parts) > 2:
                    raise IntervalAuditError(f"unsafe_archive_member:{member.filename}")
                destination = target / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as incoming, destination.open("wb") as outgoing:
                    while chunk := incoming.read(1024 * 1024):
                        outgoing.write(chunk)
        manifest_path = target / "manifest.json"
        if not manifest_path.is_file():
            raise IntervalAuditError("manifest_missing")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        yield target, manifest, {"source_type": "zip", "source_sha256": source_hash}
    if _sha256(source) != source_hash:
        raise IntervalAuditError("source_archive_modified_during_audit")


def _manifest_file_checks(dataset: Path, manifest: Mapping[str, Any]) -> dict:
    expected: dict[str, dict] = {}
    if isinstance(manifest.get("files"), dict):
        expected.update(manifest["files"])
    for name, item in manifest.get("databases", {}).items():
        backup = item.get("backup", {})
        if name not in expected and backup.get("sha256"):
            expected[name] = {
                "sha256": backup["sha256"],
                "bytes": backup.get("size_bytes"),
            }
    checks = []
    errors = []
    for name, metadata in sorted(expected.items()):
        path = dataset / name
        if not path.is_file():
            errors.append(f"manifest_file_missing:{name}")
            continue
        actual_hash = _sha256(path)
        actual_bytes = path.stat().st_size
        expected_hash = str(metadata.get("sha256", "")).upper()
        expected_bytes = metadata.get("bytes")
        match = actual_hash == expected_hash and (
            expected_bytes is None or actual_bytes == int(expected_bytes)
        )
        checks.append({"file": name, "sha256": actual_hash, "bytes": actual_bytes, "match": match})
        if not match:
            errors.append(f"manifest_hash_or_size_mismatch:{name}")
    return {"checks": checks, "errors": errors}


def _database_checks(dataset: Path) -> tuple[dict, list[str]]:
    reports: dict[str, dict] = {}
    errors: list[str] = []
    missing = [name for name in REQUIRED_DATABASES if not (dataset / name).is_file()]
    if missing:
        errors.extend(f"missing_required_database:{name}" for name in missing)
    for path in sorted(dataset.glob("*.db")):
        db = _readonly(path)
        try:
            quick = str(db.execute("PRAGMA quick_check").fetchone()[0])
            integrity = str(db.execute("PRAGMA integrity_check").fetchone()[0])
            foreign_keys = len(db.execute("PRAGMA foreign_key_check").fetchall())
            counts = {table: int(db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]) for table in _tables(db)}
            orphans = []
            for child, child_key, parent, parent_key in EXTRA_PARENT_RELATIONS.get(path.name, []):
                child_columns = _columns(db, child) if child in counts else set()
                if child not in counts or parent not in counts:
                    continue
                if child_key not in child_columns or parent_key not in _columns(db, parent):
                    continue
                orphan_filter = _relation_orphan_filter_sql(
                    path.name,
                    child,
                    child_key,
                    parent,
                    parent_key,
                    child_columns,
                    "c",
                )
                count = int(db.execute(
                    f'SELECT COUNT(*) FROM "{child}" c LEFT JOIN "{parent}" p '
                    f'ON c."{child_key}"=p."{parent_key}" '
                    f'WHERE c."{child_key}" IS NOT NULL AND p."{parent_key}" IS NULL'
                    f'{orphan_filter}'
                ).fetchone()[0])
                orphans.append({"relation": f"{child}.{child_key}->{parent}.{parent_key}", "count": count})
            reports[path.name] = {
                "quick_check": quick,
                "integrity_check": integrity,
                "foreign_key_violations": foreign_keys,
                "relational_orphans": orphans,
                "table_counts": counts,
            }
            if quick.lower() != "ok" or integrity.lower() != "ok":
                errors.append(f"sqlite_integrity_failed:{path.name}")
            if foreign_keys or any(item["count"] for item in orphans):
                errors.append(f"relational_integrity_failed:{path.name}")
        finally:
            db.close()
    return reports, errors


def _protocol_checks(dataset: Path) -> tuple[dict, list[str]]:
    reports = {}
    errors = []
    for name, expected in EXPECTED_PROTOCOL_SHA256.items():
        candidates = (dataset / "protocols" / name, dataset / name)
        path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if path is None:
            errors.append(f"required_protocol_missing:{name}")
            continue
        # Frozen protocol identity is newline-independent.  The raw digest is
        # retained for forensics, while the canonical digest is the gate.
        actual = _canonical_protocol_sha256(path.read_bytes())
        raw = _sha256(path)
        source = "archive" if dataset in path.parents else "project"
        reports[name] = {
            "sha256": actual,
            "canonical_sha256": actual,
            "raw_sha256": raw,
            "expected_sha256": expected,
            "source": source,
            "match": actual == expected,
        }
        if actual != expected:
            errors.append(f"required_protocol_hash_mismatch:{name}")
    return reports, errors


def _collector_quality(dataset: Path) -> dict:
    path = dataset / "option_trade_flow.db"
    if not path.is_file():
        return {"status": "fail", "errors": ["option_trade_flow_missing"]}
    db = _readonly(path)
    try:
        tables = set(_tables(db))
        if "collector_status_history" not in tables:
            return {"status": "fail", "errors": ["collector_status_history_missing"]}
        rows = db.execute(
            "SELECT exchange,session_id,connection_state,dropped_trade_count,updated_at_utc "
            "FROM collector_status_history ORDER BY exchange,session_id,updated_at_utc"
        ).fetchall()
        sessions: dict[tuple[str, str], dict] = {}
        previous: dict[tuple[str, str], float] = {}
        negative_or_duplicate_gaps = 0
        outage_or_gap_intervals: list[dict[str, Any]] = []
        for row in rows:
            key = (str(row[0]).lower(), str(row[1]))
            item = sessions.setdefault(key, {"samples": 0, "subscribed": 0, "unhealthy": 0, "drops": 0, "max_gap_sec": 0.0})
            item["samples"] += 1
            item["subscribed"] += int(row[2] == "subscribed")
            item["unhealthy"] += int(row[2] != "subscribed")
            item["drops"] = max(item["drops"], int(row[3] or 0))
            timestamp = float(row[4])
            if key in previous:
                gap = timestamp - previous[key]
                if gap <= 0:
                    negative_or_duplicate_gaps += 1
                item["max_gap_sec"] = max(item["max_gap_sec"], gap)
                if gap > 15.0:
                    outage_or_gap_intervals.append({"exchange": key[0], "session_id": key[1], "after_utc": iso_utc(previous[key]), "before_utc": iso_utc(timestamp), "gap_sec": gap})
            if row[2] != "subscribed":
                outage_or_gap_intervals.append({"exchange": key[0], "session_id": key[1], "at_utc": iso_utc(timestamp), "state": row[2]})
            previous[key] = timestamp
        serialized = [
            {"exchange": key[0], "session_id": key[1], **value}
            for key, value in sorted(sessions.items())
        ]
        exchanges = {item["exchange"] for item in serialized}
        drops = sum(item["drops"] for item in serialized)
        errors = []
        if exchanges != {"bybit", "deribit"}:
            errors.append("both_ETH_collectors_not_present")
        if drops:
            errors.append("historical_dropped_trades")
        if negative_or_duplicate_gaps:
            errors.append("non_monotonic_collector_clock")
        return {
            "status": "fail" if errors else ("degraded" if outage_or_gap_intervals else "pass"),
            "sessions": serialized,
            "historical_dropped_trades": drops,
            "non_positive_sample_gaps": negative_or_duplicate_gaps,
            "excluded_outage_or_gap_intervals": outage_or_gap_intervals,
            "errors": errors,
        }
    finally:
        db.close()


def _window_checks(dataset: Path, manifest: Mapping[str, Any]) -> dict:
    window = manifest.get("window")
    if not isinstance(window, Mapping):
        return {
            "status": "fail",
            "errors": ["interval_window_metadata_missing"],
            "analysis": None,
            "support": None,
            "carryover": None,
        }
    required = (
        "analysis_start_utc",
        "package_end_latest_complete_candle_utc",
        "support_context_start_utc",
        "carryover_outcome_start_utc",
        "fully_mature_720m_event_end_utc",
    )
    missing = [name for name in required if not window.get(name)]
    if missing:
        return {"status": "fail", "errors": ["interval_window_fields_missing:" + ",".join(missing)]}
    start = parse_utc(window["analysis_start_utc"])
    end = parse_utc(window["package_end_latest_complete_candle_utc"])
    support = parse_utc(window["support_context_start_utc"])
    carryover = parse_utc(window["carryover_outcome_start_utc"])
    mature = parse_utc(window["fully_mature_720m_event_end_utc"])
    errors = []
    if not support < carryover <= start < end:
        errors.append("invalid_support_analysis_carryover_order")
    if abs((start - carryover) - 720 * 60) > 0.001:
        errors.append("carryover_boundary_not_720m")
    if abs((end - mature) - 720 * 60) > 0.001:
        errors.append("maturity_boundary_not_720m")
    if start - support < 7 * 86400:
        errors.append("support_context_shorter_than_7d")
    research = _readonly(dataset / "mos_research.db")
    try:
        max_open = research.execute(
            "SELECT MAX(timestamp_utc) FROM ohlcv_candles WHERE exchange='bybit' AND symbol='ETHUSDT' AND timeframe='1m'"
        ).fetchone()[0]
        leaking = int(research.execute(
            "SELECT COUNT(*) FROM ohlcv_candles WHERE exchange='bybit' AND symbol='ETHUSDT' AND timeframe='1m' AND timestamp_utc>=?",
            (end,),
        ).fetchone()[0])
    finally:
        research.close()
    if max_open is None or float(max_open) + 60.0 < end:
        errors.append("end_boundary_candle_missing")
    if leaking:
        errors.append("current_or_end_minute_leakage")
    return {
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "analysis": {"start_utc": iso_utc(start), "end_utc": iso_utc(end), "half_open": True},
        "support": {"start_utc": iso_utc(support), "hours": (start - support) / 3600.0, "counts_as_new_evidence": False},
        "carryover": {"start_utc": iso_utc(carryover), "fully_mature_event_end_utc": iso_utc(mature), "counts_only_prior_pending_outcomes": True},
    }


def _causal_greek_quality(dataset: Path) -> dict:
    scratch = sqlite3.connect(":memory:")
    try:
        scratch.execute("ATTACH DATABASE ? AS flow", (str(dataset / "option_trade_flow.db"),))
        scratch.execute("ATTACH DATABASE ? AS history", (str(dataset / "history.db"),))
        rows = scratch.execute(
            """WITH starts AS (
                 SELECT exchange,MIN(updated_at_utc) AS live_start
                 FROM flow.collector_status_history GROUP BY exchange
               )
               SELECT t.exchange,COUNT(*) AS trades,
               SUM(EXISTS(SELECT 1 FROM history.option_contract_snapshots s
                   WHERE s.exchange=t.exchange AND s.contract_id=t.contract_id
                     AND s.ts<=t.trade_timestamp_utc
                     AND t.trade_timestamp_utc-s.ts BETWEEN 0 AND 600
                     AND s.delta IS NOT NULL AND s.gamma IS NOT NULL
                     AND s.vega IS NOT NULL AND s.theta IS NOT NULL)) AS matched,
               SUM(t.trade_timestamp_utc>=starts.live_start) AS live_trades,
               SUM(CASE WHEN t.trade_timestamp_utc>=starts.live_start THEN
                 EXISTS(SELECT 1 FROM history.option_contract_snapshots s
                   WHERE s.exchange=t.exchange AND s.contract_id=t.contract_id
                     AND s.ts<=t.trade_timestamp_utc
                     AND t.trade_timestamp_utc-s.ts BETWEEN 0 AND 600
                     AND s.delta IS NOT NULL AND s.gamma IS NOT NULL
                     AND s.vega IS NOT NULL AND s.theta IS NOT NULL)
                 ELSE 0 END) AS live_matched,
               SUM(CASE WHEN
                 EXISTS(SELECT 1 FROM history.option_contract_snapshots sf
                   WHERE sf.exchange=t.exchange AND sf.contract_id=t.contract_id
                     AND sf.ts>t.trade_timestamp_utc
                     AND sf.ts-t.trade_timestamp_utc<=600)
                 AND NOT EXISTS(SELECT 1 FROM history.option_contract_snapshots sp
                   WHERE sp.exchange=t.exchange AND sp.contract_id=t.contract_id
                     AND sp.ts<=t.trade_timestamp_utc
                     AND t.trade_timestamp_utc-sp.ts BETWEEN 0 AND 600)
                 THEN 1 ELSE 0 END) AS rejected_future_only
               FROM flow.option_trades t JOIN starts USING(exchange)
               GROUP BY t.exchange"""
        ).fetchall()
        result = []
        for exchange, trades, matched, live_trades, live_matched, rejected_future_only in rows:
            ratio = int(matched or 0) / int(trades) if trades else 0.0
            live_ratio = int(live_matched or 0) / int(live_trades) if live_trades else 0.0
            result.append({"exchange": exchange, "all_trades": int(trades), "all_matched": int(matched or 0), "all_ratio": ratio, "live_trades": int(live_trades or 0), "live_matched": int(live_matched or 0), "live_ratio": live_ratio, "rejected_future_only_snapshots": int(rejected_future_only or 0), "negative_join_ages_used": 0, "passes_0_80": live_ratio >= 0.8})
        return {"max_age_sec": 600, "backfill_and_live_reported_separately": True, "exchanges": result, "all_pass": bool(result) and all(item["passes_0_80"] for item in result)}
    finally:
        scratch.close()


def _surface_quality(dataset: Path) -> dict:
    db = _readonly(dataset / "history.db")
    try:
        tables = set(_tables(db))
        if not {"option_surface_snapshots", "option_surface_contract_snapshots"} <= tables:
            return {"status": "blocked", "reason": "stable_surface_tables_missing"}
        snapshots = db.execute(
            "SELECT id,exchange,ts,universe_id,snapshot_valid,coverage_ratio FROM option_surface_snapshots ORDER BY exchange,ts"
        ).fetchall()
        by_exchange: dict[str, list] = {}
        for row in snapshots:
            by_exchange.setdefault(str(row[1]).lower(), []).append(row)
        summaries = {}
        for exchange, items in by_exchange.items():
            strict = adjacent = 0
            for left, right in zip(items, items[1:]):
                gap = float(right[2]) - float(left[2])
                if gap <= 0 or gap > 600:
                    continue
                adjacent += 1
                left_ids = {r[0] for r in db.execute("SELECT contract_id FROM option_surface_contract_snapshots WHERE surface_snapshot_id=?", (left[0],))}
                right_ids = {r[0] for r in db.execute("SELECT contract_id FROM option_surface_contract_snapshots WHERE surface_snapshot_id=?", (right[0],))}
                union = left_ids | right_ids
                overlap = len(left_ids & right_ids) / len(union) if union else 0.0
                if int(left[4]) and int(right[4]) and overlap >= 0.95:
                    strict += 1
            summaries[exchange] = {"snapshots": len(items), "adjacent_pairs_le_600s": adjacent, "strict_pairs_ge_0_95": strict}
        return {"status": "pass" if {"bybit", "deribit"} <= set(summaries) else "blocked", "exchanges": summaries, "both_exchanges_present": {"bybit", "deribit"} <= set(summaries)}
    finally:
        db.close()


def _event_profile(candles: list[FuturesCandle], events: Iterable[tuple[float, int]], registered: set[int]) -> dict:
    event_map = {float(timestamp): int(direction) for timestamp, direction in events}
    outcomes = build_futures_outcomes(
        candles,
        event_map,
        horizons_sec=[minutes * 60 for minutes in ALL_HORIZONS_MINUTES],
        max_alignment_sec=90,
        minimum_path_coverage_ratio=0.95,
    )
    profile = {}
    for minutes in ALL_HORIZONS_MINUTES:
        horizon = minutes * 60
        selected_timestamps = []
        next_allowed = -math.inf
        for timestamp in sorted(event_map):
            if timestamp < next_allowed:
                continue
            selected_timestamps.append(timestamp)
            next_allowed = timestamp + horizon
        retained = [
            outcomes[(timestamp, horizon)]
            for timestamp in selected_timestamps
            if (timestamp, horizon) in outcomes
        ]
        ranges = [item.future_range_pct for item in retained]
        directional = [item.future_return_pct * event_map[item.decision_timestamp_utc] for item in retained if event_map[item.decision_timestamp_utc]]
        profile[str(minutes)] = {
            "role": "registered" if minutes in registered else "diagnostic_only_not_selection",
            "independent_complete_events": len(retained),
            "independent_pending_events": len(selected_timestamps) - len(retained),
            "utc_days": len({datetime.fromtimestamp(item.decision_timestamp_utc, timezone.utc).date().isoformat() for item in retained}),
            "mean_future_range_pct": float(np.mean(ranges)) if ranges else None,
            "mean_directional_gross_pct": float(np.mean(directional)) if directional else None,
            "mean_directional_net_pct_by_cost_bps": {
                str(cost): float(np.mean(directional) - cost / 100.0) if directional else None
                for cost in (6, 10, 15)
            },
        }
    return profile


def _particle_lineage(dataset: Path, scratch: Path) -> tuple[Path, dict]:
    generated = scratch / "particle_shadow_v3_rebuilt.db"
    _, summary = run_replay(dataset, generated)
    archived = dataset / "particle_shadow_v3.db"
    parity = {"required": archived.is_file(), "status": "not_applicable", "overlap_rows": 0, "mismatches": 0}
    if archived.is_file():
        left = _readonly(archived)
        right = _readonly(generated)
        try:
            run_id = str(right.execute("SELECT run_id FROM shadow_runs WHERE status='COMPLETE' ORDER BY completed_at_utc DESC LIMIT 1").fetchone()[0])
            candidate_query = "SELECT candidate_id,candidate_key,candidate_is_new,history_snapshot_id,ROUND(timestamp_utc,3),candidate_status,setup_family,direction,ROUND(readiness_score,6),ROUND(movement_score,6),ROUND(trust_score,6) FROM shadow_candidates WHERE run_id=? ORDER BY candidate_id"
            a_candidates = set(tuple(row) for row in left.execute(candidate_query, (run_id,)))
            b_candidates = set(tuple(row) for row in right.execute(candidate_query, (run_id,)))
            lineage_query = "SELECT candidate_id,particle_id,constellation_id,evidence_rank,evidence_role,component,ROUND(movement_contribution,6),ROUND(direction_contribution,6),included_reason FROM candidate_particle_lineage WHERE candidate_id IN (SELECT candidate_id FROM shadow_candidates WHERE run_id=?) ORDER BY candidate_id,evidence_rank,particle_id"
            a_lineage = set(tuple(row) for row in left.execute(lineage_query, (run_id,)))
            b_lineage = set(tuple(row) for row in right.execute(lineage_query, (run_id,)))
            candidate_mismatches = len(a_candidates ^ b_candidates)
            lineage_mismatches = len(a_lineage ^ b_lineage)
            overlap_rows = len(a_candidates & b_candidates)
            parity = {
                "required": True,
                "status": "pass" if overlap_rows and candidate_mismatches == 0 and lineage_mismatches == 0 else "fail",
                "run_id": run_id,
                "overlap_rows": overlap_rows,
                "candidate_mismatches": candidate_mismatches,
                "lineage_mismatches": lineage_mismatches,
                "mismatches": candidate_mismatches + lineage_mismatches,
            }
        finally:
            left.close()
            right.close()
    return generated, {"source": "causal_replay_from_history_and_mos", "summary": summary, "parity": parity}


def _particle_events(path: Path, eligible_after: float | None = None) -> list[tuple[float, int]]:
    db = _readonly(path)
    try:
        rows = db.execute(
            "SELECT timestamp_utc,direction FROM shadow_candidates WHERE candidate_is_new=1 AND setup_family='VOLATILITY_WITHOUT_DIRECTION' ORDER BY timestamp_utc"
        ).fetchall()
        directions = {"LONG": 1, "SHORT": -1, "NEUTRAL": 0}
        return [(float(row[0]), directions.get(str(row[1]).upper(), 0)) for row in rows if eligible_after is None or float(row[0]) > eligible_after]
    finally:
        db.close()


def _strict_sweeps(candles: list[FuturesCandle]) -> list[tuple[float, int]]:
    decisions = [candle.timestamp_utc for candle in candles if int(candle.timestamp_utc) % 300 == 0]
    outcomes = build_futures_outcomes(candles, decisions, horizons_sec=(900,), max_alignment_sec=90, minimum_path_coverage_ratio=0.95)
    return [(timestamp, outcome.false_sweep_direction) for (timestamp, _), outcome in outcomes.items() if outcome.false_sweep_direction]


def _overlap(dataset: Path, previous_sources: Iterable[str | Path]) -> dict:
    current = _trade_ids(dataset / "option_trade_flow.db")
    previous: set[tuple[str, str]] = set()
    for source in previous_sources:
        with open_archive(source) as (prior, _, _):
            path = prior / "option_trade_flow.db"
            if path.is_file():
                previous |= _trade_ids(path)
    overlap = current & previous
    return {"current_trade_ids": len(current), "previous_union_trade_ids": len(previous), "overlap_removed": len(overlap), "new_unique_trade_ids": len(current - previous)}


def _trade_ids(path: Path) -> set[tuple[str, str]]:
    db = _readonly(path)
    try:
        columns = _columns(db, "option_trades")
        key = "trade_id" if "trade_id" in columns else "id"
        return {(str(row[0]).lower(), str(row[1])) for row in db.execute(f'SELECT exchange,"{key}" FROM option_trades')}
    finally:
        db.close()


def _rank(values: list[float]) -> np.ndarray:
    order = np.argsort(np.asarray(values, dtype=float), kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    index = 0
    while index < len(values):
        end = index + 1
        while end < len(values) and values[order[end]] == values[order[index]]:
            end += 1
        ranks[order[index:end]] = (index + end - 1) / 2.0
        index = end
    return ranks


def _spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) < 3 or len(set(x)) < 2 or len(set(y)) < 2:
        return None
    return float(np.corrcoef(_rank(x), _rank(y))[0, 1])


def _flow_temporal_diagnostics(
    paths: Mapping[str, Path],
    protocol: Mapping[str, Any],
    candles: list[FuturesCandle],
) -> dict[str, Any]:
    """Describe all horizons without using them to select a confirmatory rule."""

    trades = load_enriched_option_trades(
        paths, greek_max_age_sec=float(protocol["greek_max_age_sec"])
    )
    buckets = aggregate_trade_flow_buckets(
        trades, interval_sec=int(protocol["decision_interval_sec"])
    )
    points = rolling_flow_feature_points(
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
    base = [
        point
        for point in points
        if point.timestamp_utc in healthy
        and point.trade_filter == "all"
        and point.segment == "all"
    ]
    decision_times = {point.timestamp_utc for point in base}
    outcomes = build_futures_outcomes(
        candles,
        decision_times,
        horizons_sec=[minutes * 60 for minutes in ALL_HORIZONS_MINUTES],
        max_alignment_sec=float(protocol["ohlcv_max_alignment_sec"]),
        minimum_path_coverage_ratio=float(
            protocol["minimum_ohlcv_path_coverage_ratio"]
        ),
    )
    registered = {int(value) // 60 for value in protocol["horizons_sec"]}
    correlations: dict[str, list[dict]] = {
        "ETH-H3_trade_intensity": [],
        "ETH-H4_absolute_gamma": [],
        "ETH-H4_absolute_vega": [],
    }
    feature_names = {
        "ETH-H3_trade_intensity": "trade_intensity",
        "ETH-H4_absolute_gamma": "absolute_signed_gamma_imbalance",
        "ETH-H4_absolute_vega": "absolute_signed_vega_imbalance",
    }
    for label, feature in feature_names.items():
        for scope in ("combined", "bybit", "deribit"):
            for lookback in sorted(set(int(value) for value in protocol["lookbacks_sec"])):
                selected = [
                    point
                    for point in base
                    if point.scope == scope and point.lookback_sec == lookback
                ]
                for minutes in ALL_HORIZONS_MINUTES:
                    pairs = [
                        (float(getattr(point, feature)), outcomes[(point.timestamp_utc, minutes * 60)].future_range_pct)
                        for point in selected
                        if getattr(point, feature) is not None
                        and (point.timestamp_utc, minutes * 60) in outcomes
                    ]
                    correlations[label].append(
                        {
                            "scope": scope,
                            "lookback_sec": lookback,
                            "horizon_minutes": minutes,
                            "role": "registered" if minutes in registered else "diagnostic_only_not_selection",
                            "observations": len(pairs),
                            "spearman_rho": _spearman(
                                [pair[0] for pair in pairs], [pair[1] for pair in pairs]
                            ),
                        }
                    )

    paired: dict[tuple[float, int], dict[str, Any]] = {}
    for point in base:
        if point.scope not in {"bybit", "deribit"}:
            continue
        paired.setdefault((point.timestamp_utc, point.lookback_sec), {})[
            point.scope
        ] = point
    h5 = []
    for lookback in sorted(set(int(value) for value in protocol["lookbacks_sec"])):
        for minutes in ALL_HORIZONS_MINUTES:
            agree_ranges: list[float] = []
            disagree_ranges: list[float] = []
            for (timestamp, candidate_lookback), scopes in paired.items():
                if candidate_lookback != lookback or set(scopes) != {"bybit", "deribit"}:
                    continue
                left = scopes["bybit"].signed_delta_imbalance
                right = scopes["deribit"].signed_delta_imbalance
                outcome = outcomes.get((timestamp, minutes * 60))
                if left is None or right is None or outcome is None or left == 0 or right == 0:
                    continue
                (agree_ranges if left * right > 0 else disagree_ranges).append(
                    outcome.future_range_pct
                )
            h5.append(
                {
                    "lookback_sec": lookback,
                    "horizon_minutes": minutes,
                    "role": "registered" if minutes in registered else "diagnostic_only_not_selection",
                    "agree_observations": len(agree_ranges),
                    "disagree_observations": len(disagree_ranges),
                    "mean_range_agree_pct": float(np.mean(agree_ranges)) if agree_ranges else None,
                    "mean_range_disagree_pct": float(np.mean(disagree_ranges)) if disagree_ranges else None,
                    "agree_minus_disagree_pct_points": (
                        float(np.mean(agree_ranges) - np.mean(disagree_ranges))
                        if agree_ranges and disagree_ranges
                        else None
                    ),
                }
            )
    return {
        "enriched_trades": len(trades),
        "feature_points": len(points),
        "healthy_decision_timestamps": len(healthy),
        "correlations": correlations,
        "ETH-H5_agreement": h5,
    }


def _calendar_counts(timestamps: Iterable[float]) -> dict[str, int]:
    values = sorted(set(float(value) for value in timestamps))
    days = {
        datetime.fromtimestamp(value, timezone.utc).date().isoformat()
        for value in values
    }
    weeks = {
        f"{date.isocalendar().year}-W{date.isocalendar().week:02d}"
        for date in (datetime.fromtimestamp(value, timezone.utc).date() for value in values)
    }
    return {"independent_events": len(values), "utc_days": len(days), "utc_weeks": len(weeks)}


def _accounting(new_values: Iterable[float], old_values: Iterable[float]) -> dict:
    new = {round(float(value), 3) for value in new_values}
    old = {round(float(value), 3) for value in old_values}
    untouched_new = new - old
    return {
        "old": _calendar_counts(old),
        "new_before_overlap_removal": _calendar_counts(new),
        "overlap_events_removed": len(new & old),
        "new": _calendar_counts(untouched_new),
        "cumulative": _calendar_counts(old | untouched_new),
    }


def _prior_event_sets(
    sources: Iterable[str | Path], h7_cutoff: float
) -> dict[str, set[float]]:
    result = {hypothesis: set() for hypothesis in ("ETH-H1", "ETH-H2", "ETH-H3", "ETH-H4", "ETH-H5", "ETH-H6", "ETH-H7")}
    for source in sources:
        with open_archive(source) as (dataset, _, _):
            paths = {name: dataset / name for name in ("mos_research.db", "history.db", "option_trade_flow.db")}
            if not all(path.is_file() for path in paths.values()):
                continue
            candles = load_futures_candles(paths)
            sweeps = {timestamp for timestamp, _ in _strict_sweeps(candles)}
            healthy = healthy_decision_timestamps(
                paths,
                interval_sec=300,
                minimum_samples_per_exchange=48,
            )
            result["ETH-H2"] |= sweeps
            result["ETH-H6"] |= sweeps
            for hypothesis in ("ETH-H3", "ETH-H4", "ETH-H5"):
                result[hypothesis] |= healthy
            with tempfile.TemporaryDirectory(prefix="eth_prior_lineage_") as temporary:
                particle, _ = _particle_lineage(dataset, Path(temporary))
                result["ETH-H1"] |= {timestamp for timestamp, _ in _particle_events(particle)}
                result["ETH-H7"] |= {timestamp for timestamp, _ in _particle_events(particle, h7_cutoff)}
    return result


def audit_archive(source: str | Path, *, previous_sources: Iterable[str | Path] = ()) -> dict:
    source = Path(source).resolve()
    with open_archive(source) as (dataset, manifest, provenance):
        source_db_hashes_before = {
            path.name: _sha256(path) for path in sorted(dataset.glob("*.db"))
        }
        manifest_checks = _manifest_file_checks(dataset, manifest)
        database_checks, database_errors = _database_checks(dataset)
        protocol_checks, protocol_errors = _protocol_checks(dataset)
        errors = [*manifest_checks["errors"], *database_errors, *protocol_errors]
        identity = _eth_asset_identity(dataset)
        if identity["status"] != "pass":
            errors.append("ETH_asset_identity_failed")
        collector = _collector_quality(dataset)
        errors.extend(collector.get("errors", []))
        window_checks = _window_checks(dataset, manifest) if (dataset / "mos_research.db").is_file() else {"status": "fail", "errors": ["mos_research_missing"]}
        errors.extend(window_checks.get("errors", []))
        core_inputs_present = all(
            (dataset / name).is_file()
            for name in ("mos_research.db", "history.db", "option_trade_flow.db")
        )
        causal_greeks = _causal_greek_quality(dataset) if core_inputs_present else {"all_pass": False, "exchanges": []}
        surface = _surface_quality(dataset) if (dataset / "history.db").is_file() else {"status": "blocked"}
        option_protocol = load_option_flow_protocol(PROTOCOL_DIR / "MOS_OPTION_FLOW_PREREG_V1.json")
        paths = {name: dataset / name for name in ("mos_research.db", "history.db", "option_trade_flow.db")}
        option_readiness = audit_option_flow(paths, option_protocol) if all(path.is_file() for path in paths.values()) else {"status": "not_ready", "blockers": ["required_input_missing"]}
        h7_protocol = json.loads(H7_PROTOCOL.read_text(encoding="utf-8-sig"))
        h7_cutoff = parse_utc(h7_protocol["eligible_data_after_utc"])
        prior_events = _prior_event_sets(previous_sources, h7_cutoff)
        with tempfile.TemporaryDirectory(prefix="eth_h7_lineage_") as temporary:
            particle, lineage = _particle_lineage(dataset, Path(temporary))
            candles = load_futures_candles(paths)
            h1_events = _particle_events(particle)
            h7_events = _particle_events(particle, h7_cutoff)
            sweeps = _strict_sweeps(candles)
            healthy_grid = healthy_decision_timestamps(
                paths,
                interval_sec=int(option_protocol["decision_interval_sec"]),
                minimum_samples_per_exchange=int(option_protocol["minimum_status_samples_per_5m_per_exchange"]),
            )
            profiles = {
                "ETH-H1": _event_profile(candles, h1_events, {5, 15, 30, 60, 120, 240}),
                "ETH-H2": _event_profile(candles, sweeps, {15, 30, 60}),
                "ETH-H6": _event_profile(candles, sweeps, {60}),
                "ETH-H7": _event_profile(candles, h7_events, {15, 30, 60, 120}),
            }
            profiles.update(_flow_temporal_diagnostics(paths, option_protocol, candles))
            current_events = {
                "ETH-H1": {timestamp for timestamp, _ in h1_events},
                "ETH-H2": {timestamp for timestamp, _ in sweeps},
                "ETH-H3": set(healthy_grid),
                "ETH-H4": set(healthy_grid),
                "ETH-H5": set(healthy_grid),
                "ETH-H6": {timestamp for timestamp, _ in sweeps},
                "ETH-H7": {timestamp for timestamp, _ in h7_events},
            }
            accounting = {
                hypothesis: _accounting(current_events[hypothesis], prior_events[hypothesis])
                for hypothesis in current_events
            }
        clean_days = float(option_readiness.get("healthy_full_lookback_days", 0.0))
        raw_flow_ready = option_readiness.get("status") == "ready"
        frozen_flow = run_frozen_analysis(dataset, PROTOCOL_DIR / "MOS_OPTION_FLOW_PREREG_V1.json") if raw_flow_ready else {"status": "not_ready", "reason": "quality_or_sample_gate_closed"}
        h7_days = len({datetime.fromtimestamp(item[0], timezone.utc).date().isoformat() for item in h7_events})
        confirmed_range_rules = [
            item
            for item in frozen_flow.get("range_rules", [])
            if item.get("statistically_confirmed")
        ]
        h3_confirmed = any(
            item.get("spec", {}).get("feature") == "trade_intensity"
            for item in confirmed_range_rules
        )
        h4_confirmed = any(
            item.get("spec", {}).get("feature")
            in {
                "absolute_signed_gamma_imbalance",
                "absolute_signed_vega_imbalance",
                "gamma_vega_joint",
            }
            for item in confirmed_range_rules
        )
        statuses = {
            "ETH-H1": {"state": "NOT_READY" if clean_days < 14 else "FROZEN_PENDING", "events": len(h1_events), "data_gate_open": clean_days >= 14},
            "ETH-H2": {"state": "NOT_READY" if clean_days < 14 else "FROZEN_PENDING", "events": len(sweeps), "data_gate_open": clean_days >= 14},
            "ETH-H3": {"state": "NOT_READY" if not raw_flow_ready else ("CONFIRMED_RESEARCH" if h3_confirmed else "REJECTED_CURRENT_SAMPLE"), "frozen_result": frozen_flow.get("status")},
            "ETH-H4": {"state": "TECHNICALLY_BLOCKED" if not causal_greeks.get("all_pass") else ("NOT_READY" if clean_days < 14 else ("CONFIRMED_RESEARCH" if h4_confirmed else "REJECTED_CURRENT_SAMPLE")), "causal_greek_gate": causal_greeks.get("all_pass")},
            "ETH-H5": {"state": "TECHNICALLY_BLOCKED" if not causal_greeks.get("all_pass") else ("NOT_READY" if clean_days < 14 else "FROZEN_PENDING")},
            "ETH-H6": {"state": "TECHNICALLY_BLOCKED" if surface.get("status") != "pass" else ("NOT_READY" if clean_days < 28 or len(sweeps) < 100 else "FROZEN_PENDING")},
            "ETH-H7": {"state": "TECHNICALLY_BLOCKED" if not causal_greeks.get("all_pass") or lineage["parity"]["status"] == "fail" else "FROZEN_PENDING", "eligible_events": len(h7_events), "eligible_days": h7_days, "minimum_days": 28, "minimum_events": 100, "quality_gate_pass": causal_greeks.get("all_pass") and lineage["parity"]["status"] != "fail"},
        }
        source_db_hashes_after = {
            path.name: _sha256(path) for path in sorted(dataset.glob("*.db"))
        }
        source_database_hashes_unchanged = (
            source_db_hashes_before == source_db_hashes_after
        )
        if not source_database_hashes_unchanged:
            errors.append("source_database_modified_during_audit")
        result = {
            "status": "FAIL" if errors else ("DEGRADED" if collector.get("status") != "pass" or not raw_flow_ready or not causal_greeks.get("all_pass") or surface.get("status") != "pass" else "PASS"),
            "asset": "ETH",
            "source": str(source),
            "source_sha256": provenance["source_sha256"],
            "manifest_format": manifest.get("format") or manifest.get("manifest_version"),
            "manifest": {"code_version": manifest.get("code_version") or manifest.get("project", {}).get("runtime_versions", {}).get("code_version"), "engine_patch_version": manifest.get("engine_patch_version") or manifest.get("project", {}).get("runtime_versions", {}).get("engine_patch_version"), "git": manifest.get("git") or manifest.get("project", {}).get("git"), "window": manifest.get("window")},
            "integrity": {"manifest": manifest_checks, "databases": database_checks, "errors": errors},
            "identity": identity,
            "collector_quality": collector,
            "interval_windows": window_checks,
            "causal_greek_quality": causal_greeks,
            "stable_surface_quality": surface,
            "option_flow_readiness": option_readiness,
            "overlap": _overlap(dataset, previous_sources),
            "h7_lineage": lineage,
            "hypotheses": statuses,
            "old_new_cumulative_accounting": accounting,
            "temporal_profiles": profiles,
            "frozen_option_flow_analysis": frozen_flow,
            "multiple_testing": {"status": "executed_by_full_frozen_option_flow_family" if raw_flow_ready else "not_estimable_until_frozen_sample_gate_opens", "threshold_selection_from_diagnostic_horizons_prohibited": True},
            "decision": "entry_logic_unchanged",
            "source_databases_modified": not source_database_hashes_unchanged,
        }
        return result


def render_markdown(result: Mapping[str, Any]) -> str:
    lines = [
        "# ETH MOS interval audit",
        "",
        f"Overall quality: **{result['status']}**",
        "",
        f"Source: `{result['source']}`",
        f"SHA-256: `{result.get('source_sha256') or 'directory input'}`",
        "",
        "## Frozen ETH hypotheses",
        "",
        "| ID | State | Evidence count |",
        "|---|---|---:|",
    ]
    for hypothesis, item in result["hypotheses"].items():
        count = item.get("events", item.get("eligible_events", "—"))
        lines.append(f"| {hypothesis} | {item['state']} | {count} |")
    lines.extend([
        "",
        "## Quality gates",
        "",
        f"- ETH identity: {result['identity']['status']}.",
        f"- Collector quality: {result['collector_quality']['status']}.",
        f"- Causal Greek gate: {'pass' if result['causal_greek_quality'].get('all_pass') else 'blocked'}.",
        f"- Stable surface: {result['stable_surface_quality'].get('status')}.",
        f"- Overlap removed: {result['overlap']['overlap_removed']} trade IDs.",
        f"- H7 lineage parity: {result['h7_lineage']['parity']['status']}.",
        "",
        "All non-registered horizons are diagnostic only and cannot select a confirmatory horizon.",
        "Entry logic remains unchanged. Source databases and the source archive were not modified.",
    ])
    return "\n".join(lines) + "\n"


def update_governance_pointers(
    result: Mapping[str, Any], project_root: str | Path = ROOT
) -> list[Path]:
    """Update only machine-delimited audit pointers; never rewrite frozen evidence."""

    project_root = Path(project_root).resolve()
    docs = project_root / "docs" / "mos"
    source_name = Path(str(result["source"])).name
    states = ", ".join(
        f"{key}={value['state']}" for key, value in result["hypotheses"].items()
    )
    block = (
        "<!-- ETH_INTERVAL_AUDIT_LATEST_START -->\n"
        "## Latest automated ETH interval audit pointer\n\n"
        f"Archive: `{source_name}`  \n"
        f"SHA-256: `{result.get('source_sha256') or 'directory input'}`  \n"
        f"Quality: `{result['status']}`  \n"
        f"States: {states}  \n"
        "This pointer does not promote a hypothesis or alter frozen thresholds.\n"
        "<!-- ETH_INTERVAL_AUDIT_LATEST_END -->"
    )
    updated = []
    start_marker = "<!-- ETH_INTERVAL_AUDIT_LATEST_START -->"
    end_marker = "<!-- ETH_INTERVAL_AUDIT_LATEST_END -->"
    for name in ("MOS_HYPOTHESIS_REGISTRY.md", "MOS_CURRENT_STATE.md", "MOS_NEXT_TASK.md"):
        path = docs / name
        text = path.read_text(encoding="utf-8")
        if start_marker in text and end_marker in text:
            before, remainder = text.split(start_marker, 1)
            _, after = remainder.split(end_marker, 1)
            text = before.rstrip() + "\n\n" + block + after
        else:
            text = text.rstrip() + "\n\n" + block + "\n"
        path.write_text(text, encoding="utf-8")
        updated.append(path)
    return updated
