"""Interpretable Particle Logic v1 constellation and shadow-candidate engine."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Any

from .common import clamp, safe_json_loads, stable_id


ACTIVE_EXECUTION_STATES = {
    "EXPANSION_CONFIRMING",
    "HEDGE_CHASE_STARTING",
    "EXECUTION_WINDOW_OPEN",
}


def _quality_score(snapshot: sqlite3.Row) -> float:
    base = {
        "GOOD": 100.0,
        "PARTIAL": 85.0,
        "DEGRADED": 70.0,
        "CRITICAL": 0.0,
    }.get(snapshot["data_quality"], 45.0)
    overlap = snapshot["chain_overlap_ratio"]
    if overlap is not None:
        base *= 0.60 + 0.40 * clamp(float(overlap), 0.0, 1.0)
    sources = safe_json_loads(snapshot["active_sources_json"], [])
    if len(sources) > 1:
        base += 5.0
    return round(clamp(base, 0.0, 100.0), 4)


def _direction_label(score: float) -> str:
    if score >= 25.0:
        return "UP"
    if score <= -25.0:
        return "DOWN"
    return "NEUTRAL"


def _score_snapshot(
    snapshot: sqlite3.Row,
    particles: list[sqlite3.Row],
) -> dict[str, Any]:
    oi_particles = [p for p in particles if p["particle_type"].startswith("OI_")]
    gex_particles = [p for p in particles if p["particle_type"].startswith("GEX_")]
    term_particles = [p for p in particles if p["particle_type"].startswith("FRONT_IV_")]
    wall_particles = [
        p
        for p in particles
        if p["particle_type"]
        in {"CALL_WALL_MIGRATION", "PUT_WALL_MIGRATION", "GAMMA_FLIP_MIGRATION"}
    ]

    total_oi_delta = sum(abs(float(p["delta_value"])) for p in oi_particles)
    oi_total = abs(float(snapshot["oi_total"] or 0.0))
    oi_activity = clamp(
        total_oi_delta / max(oi_total, 1.0) * 2500.0,
        0.0,
        100.0,
    )

    total_gex_delta = sum(abs(float(p["delta_value"])) for p in gex_particles)
    total_gex_current = sum(abs(float(p["current_value"] or 0.0)) for p in gex_particles)
    gex_activity = clamp(
        total_gex_delta / max(total_gex_current, 1.0) * 300.0,
        0.0,
        100.0,
    )
    term_activity = max(
        (float(p["strength_score"]) for p in term_particles),
        default=0.0,
    )
    wall_activity = max(
        (float(p["strength_score"]) for p in wall_particles),
        default=0.0,
    )

    negative_gamma_bonus = 10.0 if snapshot["gamma_regime"] == "NEGATIVE_GAMMA" else 0.0
    movement_score = clamp(
        0.25 * oi_activity
        + 0.35 * gex_activity
        + 0.20 * term_activity
        + 0.20 * wall_activity
        + negative_gamma_bonus,
        0.0,
        100.0,
    )

    near_oi = [
        p
        for p in oi_particles
        if p["distance_from_spot_pct"] is not None
        and abs(float(p["distance_from_spot_pct"])) <= 10.0
    ]
    call_delta = sum(float(p["delta_value"]) for p in near_oi if p["side"] == "CALL")
    put_delta = sum(float(p["delta_value"]) for p in near_oi if p["side"] == "PUT")
    oi_denominator = sum(abs(float(p["delta_value"])) for p in near_oi)
    direction_score = (
        (call_delta - put_delta) / oi_denominator * 15.0
        if oi_denominator > 0
        else 0.0
    )
    structural_direction = []
    spot = float(snapshot["spot_price"] or 0.0)
    for particle in wall_particles:
        delta = float(particle["delta_value"])
        magnitude = clamp(abs(delta) / max(spot, 1.0) * 2000.0, 0.0, 25.0)
        contribution = magnitude if delta > 0 else -magnitude
        if particle["particle_type"] == "GAMMA_FLIP_MIGRATION":
            contribution *= 1.2
        direction_score += contribution
        structural_direction.append(
            {
                "particle_type": particle["particle_type"],
                "delta": delta,
                "direction_contribution": round(contribution, 4),
            }
        )
    direction_score = clamp(direction_score, -100.0, 100.0)

    trust_score = _quality_score(snapshot)
    context = safe_json_loads(snapshot["raw_context_json"], {})
    gex_metrics = context.get("gex_metrics", {})
    call_wall = snapshot["call_wall"] or gex_metrics.get("gamma_wall_above")
    put_wall = snapshot["put_wall"] or gex_metrics.get("gamma_wall_below")
    call_distance = (
        abs(float(call_wall) - spot) / spot * 100.0 if call_wall and spot else None
    )
    put_distance = (
        abs(float(put_wall) - spot) / spot * 100.0 if put_wall and spot else None
    )
    expansion_probability = float(snapshot["expansion_probability"] or 0.0)

    structure_label = "NO_CLEAR_CONSTELLATION"
    reasons: list[str] = []
    if expansion_probability >= 45.0 and movement_score < 40.0:
        structure_label = "UNCONFIRMED_MOS_EXPANSION"
        reasons.append("mos_expansion_not_confirmed_by_option_particle_activity")
    elif snapshot["gamma_regime"] == "NEGATIVE_GAMMA" and movement_score >= 55.0:
        if abs(direction_score) >= 25.0:
            structure_label = "NEGATIVE_GAMMA_BREAKOUT"
            reasons.append("negative_gamma_with_directional_particle_redistribution")
        else:
            structure_label = "VOLATILITY_WITHOUT_DIRECTION"
            reasons.append("negative_gamma_activity_has_no_reliable_direction")
    elif snapshot["gamma_regime"] == "POSITIVE_GAMMA" and movement_score <= 45.0:
        nearest_wall_distance = min(
            value for value in (call_distance, put_distance) if value is not None
        ) if any(value is not None for value in (call_distance, put_distance)) else None
        if nearest_wall_distance is not None and nearest_wall_distance <= 0.50:
            structure_label = "PINNING_REVERSION"
            if call_distance is not None and (
                put_distance is None or call_distance <= put_distance
            ):
                direction_score = min(direction_score, -35.0)
                reasons.append("spot_near_stable_call_wall_in_positive_gamma")
            else:
                direction_score = max(direction_score, 35.0)
                reasons.append("spot_near_stable_put_wall_in_positive_gamma")
    elif movement_score >= 55.0:
        structure_label = "VOLATILITY_WITHOUT_DIRECTION"
        reasons.append("high_particle_activity_without_directional_constellation")

    direction_label = _direction_label(direction_score)
    if not reasons:
        reasons.append("particle_activity_below_shadow_thresholds")
    return {
        "structure_label": structure_label,
        "movement_score": round(movement_score, 4),
        "direction_score": round(direction_score, 4),
        "direction_label": direction_label,
        "trust_score": trust_score,
        "oi_activity_score": round(oi_activity, 4),
        "gex_activity_score": round(gex_activity, 4),
        "term_activity_score": round(term_activity, 4),
        "wall_activity_score": round(wall_activity, 4),
        "options_evidence_count": len(particles),
        "reasons": reasons,
        "diagnostics": {
            "near_spot_call_oi_delta": call_delta,
            "near_spot_put_oi_delta": put_delta,
            "structural_direction": structural_direction,
            "call_wall_distance_pct": call_distance,
            "put_wall_distance_pct": put_distance,
            "negative_gamma_bonus": negative_gamma_bonus,
        },
    }


def _candidate_from_constellation(
    run_id: str,
    snapshot: sqlite3.Row,
    constellation_id: str,
    scores: dict[str, Any],
    episode_memory: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    structure = scores["structure_label"]
    if structure == "NO_CLEAR_CONSTELLATION":
        return None

    direction = {
        "UP": "LONG",
        "DOWN": "SHORT",
    }.get(scores["direction_label"], "NEUTRAL")
    movement = scores["movement_score"]
    direction_confidence = abs(scores["direction_score"])
    trust = scores["trust_score"]
    readiness = clamp(
        0.45 * movement + 0.25 * direction_confidence + 0.30 * trust,
        0.0,
        100.0,
    )
    blockers: list[str] = []
    status = "VOLATILITY_WATCH"
    if structure in {"NEGATIVE_GAMMA_BREAKOUT", "PINNING_REVERSION"}:
        status = "DIRECTIONAL_WATCH"
        if trust < 55.0:
            blockers.append("particle_source_trust_below_55")
        if direction == "NEUTRAL":
            blockers.append("option_direction_not_confirmed")

        flow = float(snapshot["synthetic_flow_pressure"] or 0.0)
        flow_aligned = (
            (direction == "LONG" and flow >= 15.0)
            or (direction == "SHORT" and flow <= -15.0)
        )
        execution_active = snapshot["execution_timing_state"] in ACTIVE_EXECUTION_STATES
        if not flow_aligned:
            blockers.append("confirmation_flow_not_aligned")
        if not execution_active:
            blockers.append("execution_window_not_active")
        if structure == "PINNING_REVERSION":
            blockers.append("fresh_price_rejection_not_available_in_shadow_v1")
        elif movement < 60.0:
            blockers.append("movement_score_below_60")
        elif direction_confidence < 30.0:
            blockers.append("direction_score_below_30")

        if not blockers:
            status = "SHADOW_ENTRY_CANDIDATE"

    context = safe_json_loads(snapshot["raw_context_json"], {})
    gex_metrics = context.get("gex_metrics", {})
    call_wall = snapshot["call_wall"] or gex_metrics.get("gamma_wall_above")
    put_wall = snapshot["put_wall"] or gex_metrics.get("gamma_wall_below")
    gamma_flip = gex_metrics.get("gamma_flip")
    if structure == "PINNING_REVERSION":
        reference_level = call_wall if direction == "SHORT" else put_wall
    elif direction == "LONG":
        reference_level = gamma_flip or call_wall
    elif direction == "SHORT":
        reference_level = gamma_flip or put_wall
    else:
        reference_level = gamma_flip
    invalidation = None
    if reference_level and direction == "LONG":
        invalidation = round(float(reference_level) * 0.998, 4)
    elif reference_level and direction == "SHORT":
        invalidation = round(float(reference_level) * 1.002, 4)

    history_id = int(snapshot["history_snapshot_id"])
    timestamp = float(snapshot["timestamp_utc"])
    level_bucket = round(float(reference_level), 0) if reference_level else 0
    episode_base = f"{structure}:{direction}:{level_bucket:g}"
    episode = episode_memory.get(episode_base)
    if episode is None or timestamp - float(episode["last_timestamp"]) > 1800.0:
        candidate_key = stable_id(run_id, episode_base, timestamp)
        first_timestamp = timestamp
        candidate_is_new = 1
    else:
        candidate_key = str(episode["candidate_key"])
        first_timestamp = float(episode["first_timestamp"])
        candidate_is_new = 0
    episode_memory[episode_base] = {
        "candidate_key": candidate_key,
        "first_timestamp": first_timestamp,
        "last_timestamp": timestamp,
    }
    return {
        "candidate_id": stable_id(run_id, history_id, structure, direction),
        "candidate_key": candidate_key,
        "candidate_is_new": candidate_is_new,
        "candidate_episode_age_sec": round(timestamp - first_timestamp, 4),
        "run_id": run_id,
        "constellation_id": constellation_id,
        "history_snapshot_id": history_id,
        "timestamp_utc": timestamp,
        "candidate_status": status,
        "setup_family": structure,
        "direction": direction,
        "readiness_score": round(readiness, 4),
        "movement_score": movement,
        "direction_score": scores["direction_score"],
        "trust_score": trust,
        "entry_price": snapshot["spot_price"],
        "reference_level": reference_level,
        "invalidation_level": invalidation,
        "blockers_json": json.dumps(blockers, sort_keys=True),
        "features_json": json.dumps(
            {
                "current_state": snapshot["current_state"],
                "gamma_regime": snapshot["gamma_regime"],
                "expansion_probability": snapshot["expansion_probability"],
                "synthetic_flow_pressure": snapshot["synthetic_flow_pressure"],
                "execution_timing_state": snapshot["execution_timing_state"],
                "options_first": True,
                "price_and_flow_role": "confirmation_only",
            },
            sort_keys=True,
        ),
    }


def build_constellations(
    connection: sqlite3.Connection,
    run_id: str,
) -> dict[str, int]:
    particles_by_snapshot: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for particle in connection.execute(
        """
        SELECT * FROM particle_observations
        WHERE run_id = ?
        ORDER BY history_snapshot_id, particle_id
        """,
        (run_id,),
    ):
        particles_by_snapshot[int(particle["history_snapshot_id"])].append(particle)

    constellation_count = 0
    candidate_count = 0
    episode_memory: dict[str, dict[str, Any]] = {}
    for snapshot in connection.execute(
        """
        SELECT * FROM source_snapshots
        WHERE run_id = ? AND exclude_from_analysis = 0
        ORDER BY history_snapshot_id
        """,
        (run_id,),
    ):
        history_id = int(snapshot["history_snapshot_id"])
        particles = particles_by_snapshot.get(history_id, [])
        scores = _score_snapshot(snapshot, particles)
        constellation_id = stable_id(run_id, history_id, "constellation")
        connection.execute(
            """
            INSERT INTO particle_constellations (
                constellation_id, run_id, history_snapshot_id, timestamp_utc,
                structure_label, movement_score, direction_score, direction_label,
                trust_score, oi_activity_score, gex_activity_score,
                term_activity_score, wall_activity_score, options_evidence_count,
                reasons_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                constellation_id, run_id, history_id, snapshot["timestamp_utc"],
                scores["structure_label"], scores["movement_score"],
                scores["direction_score"], scores["direction_label"],
                scores["trust_score"], scores["oi_activity_score"],
                scores["gex_activity_score"], scores["term_activity_score"],
                scores["wall_activity_score"], scores["options_evidence_count"],
                json.dumps(
                    {
                        "reasons": scores["reasons"],
                        "diagnostics": scores["diagnostics"],
                    },
                    sort_keys=True,
                ),
            ),
        )
        constellation_count += 1

        candidate = _candidate_from_constellation(
            run_id,
            snapshot,
            constellation_id,
            scores,
            episode_memory,
        )
        if candidate is None:
            continue
        connection.execute(
            """
            INSERT INTO shadow_candidates (
                candidate_id, candidate_key, candidate_is_new,
                candidate_episode_age_sec, run_id, constellation_id, history_snapshot_id,
                timestamp_utc, candidate_status, setup_family, direction,
                readiness_score, movement_score, direction_score, trust_score,
                entry_price, reference_level, invalidation_level, blockers_json,
                features_json
            ) VALUES (
                :candidate_id, :candidate_key, :candidate_is_new,
                :candidate_episode_age_sec, :run_id, :constellation_id, :history_snapshot_id,
                :timestamp_utc, :candidate_status, :setup_family, :direction,
                :readiness_score, :movement_score, :direction_score, :trust_score,
                :entry_price, :reference_level, :invalidation_level, :blockers_json,
                :features_json
            )
            """,
            candidate,
        )
        candidate_count += 1

    connection.commit()
    return {
        "constellations": constellation_count,
        "shadow_candidates": candidate_count,
    }
