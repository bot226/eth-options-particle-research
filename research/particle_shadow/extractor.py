"""Extract deterministic option particles from existing MOS databases."""

from __future__ import annotations

import json
import sqlite3
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .common import (
    clamp,
    finite_float,
    parse_contract,
    percentile_strength,
    safe_json_loads,
    stable_id,
)


@dataclass(frozen=True)
class ExtractionConfig:
    max_research_lag_sec: float = 120.0
    max_history_gap_sec: float = 900.0
    minimum_chain_overlap: float = 0.70


def _readonly_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=30.0,
    )
    connection.row_factory = sqlite3.Row
    return connection


def _load_research_contexts(path: Path) -> tuple[list[float], list[dict[str, Any]]]:
    connection = _readonly_connection(path)
    try:
        records = [
            dict(row)
            for row in connection.execute(
                """
                SELECT timestamp_utc, snapshot_id, snapshot_sequence_id, spot_price,
                       current_state, gamma_regime, oi_total, net_gex, atm_iv, iv_velocity,
                       expansion_probability, synthetic_flow_pressure,
                       execution_timing_state, call_wall, put_wall, data_quality,
                       active_sources, exclude_from_analysis, exclude_reason
                FROM snapshots
                ORDER BY timestamp_utc
                """
            )
        ]
    finally:
        connection.close()
    return [float(record["timestamp_utc"]) for record in records], records


def _nearest_prior_context(
    timestamp: float,
    timestamps: list[float],
    records: list[dict[str, Any]],
    max_lag_sec: float,
) -> tuple[dict[str, Any] | None, float | None]:
    index = bisect_right(timestamps, timestamp) - 1
    if index < 0:
        return None, None
    lag = timestamp - timestamps[index]
    if lag < 0 or lag > max_lag_sec:
        return None, lag
    return records[index], lag


def _gex_by_strike(payload: dict[str, Any]) -> dict[float, dict[str, float]]:
    result: dict[float, dict[str, float]] = {}
    for row in payload.get("data", []):
        if not isinstance(row, dict):
            continue
        strike = finite_float(row.get("strike"))
        if strike is None or strike <= 0:
            continue
        result[strike] = {
            "call_gex": finite_float(row.get("call_gex")) or 0.0,
            "put_gex": finite_float(row.get("put_gex")) or 0.0,
            "net_gex": finite_float(row.get("net_gex")) or 0.0,
        }
    return result


def _front_iv(payload: dict[str, Any]) -> tuple[str | None, float | None]:
    rows = [row for row in payload.get("data", []) if isinstance(row, dict)]
    rows.sort(key=lambda row: finite_float(row.get("dte")) or float("inf"))
    for row in rows:
        atm_iv = finite_float(row.get("atm_iv"))
        if atm_iv is not None and atm_iv > 0:
            return str(row.get("expiry") or ""), atm_iv
    return None, None


def _source_quality(context: dict[str, Any] | None) -> str:
    if context is None:
        return "NO_RESEARCH_CONTEXT"
    if int(context.get("exclude_from_analysis") or 0):
        return "SOURCE_CRITICAL"
    sources = safe_json_loads(context.get("active_sources"), [])
    if len(sources) > 1:
        return "OPTIONS_MULTI_SOURCE"
    if sources == ["bybit"]:
        return "OPTIONS_BYBIT_ONLY"
    return "SOURCE_UNKNOWN"


def _persist(
    memory: dict[str, tuple[str, int, int]],
    key: str,
    particle_type: str,
    snapshot_id: int,
) -> int:
    previous = memory.get(key)
    if previous and previous[0] == particle_type and previous[2] == snapshot_id - 1:
        count = previous[1] + 1
    else:
        count = 1
    memory[key] = (particle_type, count, snapshot_id)
    return count


def extract_particles(
    history_db: str | Path,
    research_db: str | Path,
    output: sqlite3.Connection,
    run_id: str,
    config: ExtractionConfig | None = None,
) -> dict[str, int]:
    """Import slow MOS structure and derive particles without look-ahead."""

    config = config or ExtractionConfig()
    history_path = Path(history_db)
    research_path = Path(research_db)
    research_timestamps, research_records = _load_research_contexts(research_path)

    history = _readonly_connection(history_path)
    try:
        rows = list(
            history.execute(
                """
                SELECT id, ts, oi_json, gex_json, term_structure_json, pdf_json,
                       exchange_data_json
                FROM snapshots
                ORDER BY ts, id
                """
            )
        )
    finally:
        history.close()

    previous: dict[str, Any] | None = None
    persistence: dict[str, tuple[str, int, int]] = {}
    snapshot_count = 0
    particle_count = 0

    for row in rows:
        history_id = int(row["id"])
        timestamp = float(row["ts"])
        oi = safe_json_loads(row["oi_json"], {})
        gex = safe_json_loads(row["gex_json"], {})
        term = safe_json_loads(row["term_structure_json"], {})
        pdf = safe_json_loads(row["pdf_json"], {})
        context, context_lag = _nearest_prior_context(
            timestamp,
            research_timestamps,
            research_records,
            config.max_research_lag_sec,
        )
        source_quality = _source_quality(context)
        spot = finite_float(context.get("spot_price")) if context else None

        previous_oi = previous["oi"] if previous else {}
        union_count = len(set(oi) | set(previous_oi)) if previous else len(oi)
        overlap_count = len(set(oi) & set(previous_oi)) if previous else len(oi)
        overlap_ratio = overlap_count / union_count if union_count else 0.0
        history_gap = timestamp - previous["timestamp"] if previous else None

        exclude_reasons: list[str] = []
        if context is None:
            exclude_reasons.append("research_context_missing_or_stale")
        elif int(context.get("exclude_from_analysis") or 0):
            exclude_reasons.append(context.get("exclude_reason") or "mos_excluded")
        if spot is None or spot <= 0:
            exclude_reasons.append("spot_missing")
        if not isinstance(oi, dict) or not oi:
            exclude_reasons.append("option_chain_missing")
        if previous and overlap_ratio < config.minimum_chain_overlap:
            exclude_reasons.append("chain_overlap_below_threshold")
        if history_gap is not None and history_gap > config.max_history_gap_sec:
            exclude_reasons.append("history_gap_too_large")

        gex_metrics = gex.get("metrics", {}) if isinstance(gex, dict) else {}
        term_metrics = term.get("metrics", {}) if isinstance(term, dict) else {}
        raw_context = {
            "source_quality": source_quality,
            "gex_metrics": gex_metrics,
            "term_metrics": term_metrics,
            "pdf_metrics": pdf.get("metrics", {}) if isinstance(pdf, dict) else {},
            "history_gap_sec": history_gap,
            "chain_union_count": union_count,
            "chain_overlap_count": overlap_count,
        }
        output.execute(
            """
            INSERT INTO source_snapshots (
                run_id, history_snapshot_id, timestamp_utc, mos_snapshot_id,
                mos_snapshot_sequence_id, mos_snapshot_lag_sec, spot_price,
                current_state, gamma_regime, oi_total, net_gex, atm_iv, iv_velocity,
                expansion_probability, synthetic_flow_pressure,
                execution_timing_state, call_wall, put_wall, data_quality,
                active_sources_json, exclude_from_analysis, exclude_reason,
                chain_contract_count, chain_overlap_ratio, raw_context_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                history_id,
                timestamp,
                context.get("snapshot_id") if context else None,
                context.get("snapshot_sequence_id") if context else None,
                context_lag,
                spot,
                context.get("current_state") if context else None,
                context.get("gamma_regime") if context else None,
                finite_float(context.get("oi_total")) if context else None,
                finite_float(context.get("net_gex")) if context else None,
                finite_float(context.get("atm_iv")) if context else None,
                finite_float(context.get("iv_velocity")) if context else None,
                finite_float(context.get("expansion_probability")) if context else None,
                finite_float(context.get("synthetic_flow_pressure")) if context else None,
                context.get("execution_timing_state") if context else None,
                finite_float(context.get("call_wall")) if context else None,
                finite_float(context.get("put_wall")) if context else None,
                context.get("data_quality") if context else None,
                context.get("active_sources") if context else "[]",
                int(bool(exclude_reasons)),
                ",".join(dict.fromkeys(exclude_reasons)),
                len(oi) if isinstance(oi, dict) else 0,
                overlap_ratio,
                json.dumps(raw_context, sort_keys=True),
            ),
        )
        snapshot_count += 1

        can_compare = (
            previous is not None
            and not exclude_reasons
            and not previous["excluded"]
            and history_gap is not None
            and history_gap <= config.max_history_gap_sec
        )
        if not can_compare:
            previous = {
                "history_id": history_id,
                "timestamp": timestamp,
                "oi": oi if isinstance(oi, dict) else {},
                "gex": gex if isinstance(gex, dict) else {},
                "term": term if isinstance(term, dict) else {},
                "excluded": bool(exclude_reasons),
            }
            continue

        particle_rows: list[tuple[Any, ...]] = []

        oi_changes: list[tuple[str, float, float, float]] = []
        for instrument in sorted(set(oi) & set(previous_oi)):
            current_value = finite_float(oi.get(instrument))
            previous_value = finite_float(previous_oi.get(instrument))
            if current_value is None or previous_value is None:
                continue
            delta = current_value - previous_value
            if abs(delta) <= 1e-12:
                continue
            oi_changes.append((instrument, previous_value, current_value, delta))
        oi_population = [item[3] for item in oi_changes]
        for instrument, previous_value, current_value, delta in oi_changes:
            identity = parse_contract(instrument)
            if identity is None:
                continue
            particle_type = "OI_BUILD" if delta > 0 else "OI_UNWIND"
            relative = delta / abs(previous_value) if abs(previous_value) > 1e-12 else None
            distance = (
                (identity.strike - spot) / spot * 100.0
                if spot is not None and spot > 0
                else None
            )
            key = f"OI:{instrument}"
            strength = percentile_strength(delta, oi_population)
            particle_rows.append(
                (
                    stable_id(run_id, history_id, key, particle_type), run_id,
                    history_id, timestamp, particle_type, identity.option_type,
                    identity.expiry, identity.strike,
                    "CALL" if identity.option_type == "C" else "PUT", key,
                    previous_value, current_value, delta, relative, strength,
                    distance, _persist(persistence, key, particle_type, history_id),
                    source_quality,
                    json.dumps({"instrument": instrument}, sort_keys=True),
                )
            )

        previous_gex = _gex_by_strike(previous["gex"])
        current_gex = _gex_by_strike(gex)
        gex_changes: list[tuple[float, str, float, float, float]] = []
        for strike in sorted(set(previous_gex) & set(current_gex)):
            for component, option_type in (
                ("call_gex", "C"), ("put_gex", "P"), ("net_gex", "NET")
            ):
                previous_value = previous_gex[strike][component]
                current_value = current_gex[strike][component]
                delta = current_value - previous_value
                if abs(delta) <= 1e-12:
                    continue
                gex_changes.append(
                    (strike, option_type, previous_value, current_value, delta)
                )
        gex_population = [item[4] for item in gex_changes]
        for strike, option_type, previous_value, current_value, delta in gex_changes:
            if previous_value * current_value < 0:
                particle_type = "GEX_SIGN_FLIP"
            elif abs(current_value) > abs(previous_value):
                particle_type = "GEX_BUILD"
            else:
                particle_type = "GEX_DECAY"
            relative = delta / abs(previous_value) if abs(previous_value) > 1e-12 else None
            distance = (strike - spot) / spot * 100.0 if spot else None
            key = f"GEX:{option_type}:{strike:g}"
            particle_rows.append(
                (
                    stable_id(run_id, history_id, key, particle_type), run_id,
                    history_id, timestamp, particle_type,
                    option_type if option_type in ("C", "P") else None,
                    None, strike,
                    {"C": "CALL", "P": "PUT"}.get(option_type, "NET"), key,
                    previous_value, current_value, delta, relative,
                    percentile_strength(delta, gex_population), distance,
                    _persist(persistence, key, particle_type, history_id),
                    source_quality,
                    json.dumps({"gex_component": option_type}, sort_keys=True),
                )
            )

        previous_metrics = previous["gex"].get("metrics", {})
        current_metrics = gex.get("metrics", {})
        for metric, particle_type, side in (
            ("gamma_wall_above", "CALL_WALL_MIGRATION", "CALL_WALL"),
            ("gamma_wall_below", "PUT_WALL_MIGRATION", "PUT_WALL"),
            ("gamma_flip", "GAMMA_FLIP_MIGRATION", "GAMMA_FLIP"),
        ):
            previous_value = finite_float(previous_metrics.get(metric))
            current_value = finite_float(current_metrics.get(metric))
            if previous_value is None or current_value is None:
                continue
            delta = current_value - previous_value
            if abs(delta) <= 1e-12:
                continue
            key = f"STRUCTURE:{metric}"
            strength = clamp(abs(delta) / spot * 5000.0 if spot else 0.0, 0.0, 100.0)
            particle_rows.append(
                (
                    stable_id(run_id, history_id, key, particle_type), run_id,
                    history_id, timestamp, particle_type, None, None,
                    current_value, side, key, previous_value, current_value,
                    delta, delta / previous_value if previous_value else None,
                    round(strength, 4),
                    (current_value - spot) / spot * 100.0 if spot else None,
                    _persist(persistence, key, particle_type, history_id),
                    source_quality, "{}",
                )
            )

        previous_expiry, previous_front_iv = _front_iv(previous["term"])
        current_expiry, current_front_iv = _front_iv(term)
        if previous_front_iv is not None and current_front_iv is not None:
            delta = current_front_iv - previous_front_iv
            if abs(delta) > 1e-12:
                particle_type = "FRONT_IV_RISE" if delta > 0 else "FRONT_IV_FALL"
                key = "TERM:FRONT_IV"
                particle_rows.append(
                    (
                        stable_id(run_id, history_id, key, particle_type), run_id,
                        history_id, timestamp, particle_type, None, current_expiry,
                        None, "VOLATILITY", key, previous_front_iv,
                        current_front_iv, delta,
                        delta / previous_front_iv if previous_front_iv else None,
                        round(clamp(abs(delta) * 5000.0, 0.0, 100.0), 4),
                        None, _persist(persistence, key, particle_type, history_id),
                        source_quality,
                        json.dumps({"previous_expiry": previous_expiry}, sort_keys=True),
                    )
                )

        if particle_rows:
            output.executemany(
                """
                INSERT INTO particle_observations (
                    particle_id, run_id, history_snapshot_id, timestamp_utc,
                    particle_type, option_type, expiry, strike, side, source_key,
                    previous_value, current_value, delta_value, relative_change,
                    strength_score, distance_from_spot_pct, persistence_count,
                    source_quality, features_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                particle_rows,
            )
            particle_count += len(particle_rows)

        previous = {
            "history_id": history_id,
            "timestamp": timestamp,
            "oi": oi,
            "gex": gex,
            "term": term,
            "excluded": bool(exclude_reasons),
        }

    output.commit()
    return {"source_snapshots": snapshot_count, "particles": particle_count}
