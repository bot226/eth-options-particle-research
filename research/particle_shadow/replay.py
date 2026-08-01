"""Offline replay orchestrator for MOS Particle Logic shadow research."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .common import stable_id
from .engine import build_constellations
from .extractor import ExtractionConfig, extract_particles
from .outcomes import attach_outcomes
from .schema import (
    PARTICLE_LOGIC_VERSION,
    PARTICLE_SHADOW_SCHEMA_VERSION,
    finalize_database,
    initialize_database,
)


REQUIRED_INPUTS = ("history.db", "mos_research.db")


class ParticleReplayError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_database(path: Path) -> None:
    connection = sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=30.0
    )
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        quick = connection.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        connection.close()
    if str(integrity).lower() != "ok" or str(quick).lower() != "ok":
        raise ParticleReplayError(
            f"Input database check failed for {path.name}: "
            f"integrity={integrity}, quick={quick}"
        )


@contextmanager
def open_dataset(source: str | Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    source_path = Path(source).resolve()
    if source_path.is_dir():
        dataset_dir = source_path
        manifest_path = dataset_dir / "manifest.json"
        manifest = (
            json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.is_file()
            else {}
        )
        missing = [name for name in REQUIRED_INPUTS if not (dataset_dir / name).is_file()]
        if missing:
            raise ParticleReplayError("Missing dataset databases: " + ", ".join(missing))
        yield dataset_dir, manifest
        return

    if not source_path.is_file() or source_path.suffix.lower() != ".zip":
        raise ParticleReplayError("Dataset source must be a MOS ZIP or extracted directory")
    with tempfile.TemporaryDirectory(prefix="mos_particle_shadow_") as temporary:
        dataset_dir = Path(temporary)
        with zipfile.ZipFile(source_path) as archive:
            members = set(archive.namelist())
            missing = [name for name in REQUIRED_INPUTS if name not in members]
            if missing:
                raise ParticleReplayError(
                    "MOS archive is missing: " + ", ".join(missing)
                )
            for name in (*REQUIRED_INPUTS, "manifest.json"):
                if name in members:
                    archive.extract(name, dataset_dir)
        manifest_path = dataset_dir / "manifest.json"
        manifest = (
            json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.is_file()
            else {}
        )
        yield dataset_dir, manifest


def _summary(connection: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    counts = {}
    for table in (
        "source_snapshots",
        "particle_observations",
        "particle_constellations",
        "shadow_candidates",
        "shadow_outcomes",
    ):
        counts[table] = int(
            connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE "
                + ("run_id = ?" if table != "shadow_outcomes" else
                   "candidate_id IN (SELECT candidate_id FROM shadow_candidates WHERE run_id = ?)"),
                (run_id,),
            ).fetchone()[0]
        )
    return {
        "run_id": run_id,
        "logic_version": PARTICLE_LOGIC_VERSION,
        "schema_version": PARTICLE_SHADOW_SCHEMA_VERSION,
        "counts": counts,
        "excluded_sources": [
            dict(row)
            for row in connection.execute(
                """
                SELECT exclude_reason, COUNT(*) AS rows
                FROM source_snapshots
                WHERE run_id = ? AND exclude_from_analysis = 1
                GROUP BY exclude_reason
                ORDER BY rows DESC, exclude_reason
                """,
                (run_id,),
            )
        ],
        "particle_types": [
            dict(row)
            for row in connection.execute(
                """
                SELECT particle_type, COUNT(*) AS rows
                FROM particle_observations
                WHERE run_id = ?
                GROUP BY particle_type
                ORDER BY rows DESC, particle_type
                """,
                (run_id,),
            )
        ],
        "constellations": [
            dict(row)
            for row in connection.execute(
                """
                SELECT structure_label, COUNT(*) AS rows,
                       ROUND(AVG(movement_score), 4) AS avg_movement_score,
                       ROUND(AVG(trust_score), 4) AS avg_trust_score
                FROM particle_constellations
                WHERE run_id = ?
                GROUP BY structure_label
                ORDER BY rows DESC, structure_label
                """,
                (run_id,),
            )
        ],
        "candidate_distribution": [
            dict(row)
            for row in connection.execute(
                """
                SELECT candidate_status, setup_family, direction, COUNT(*) AS rows,
                       COUNT(DISTINCT candidate_key) AS episodes
                FROM shadow_candidates
                WHERE run_id = ?
                GROUP BY candidate_status, setup_family, direction
                ORDER BY rows DESC, candidate_status, setup_family, direction
                """,
                (run_id,),
            )
        ],
        "candidate_outcomes": [
            dict(row)
            for row in connection.execute(
                """
                SELECT c.candidate_status, c.setup_family, c.direction,
                       COUNT(*) AS rows,
                       ROUND(AVG(o.return_60m), 6) AS avg_return_60m,
                       ROUND(AVG(o.mfe_60m_pct), 6) AS avg_mfe_60m_pct,
                       ROUND(AVG(o.mae_60m_pct), 6) AS avg_mae_60m_pct,
                       SUM(o.outcome_complete_240m) AS complete_240m
                FROM shadow_candidates c
                JOIN shadow_outcomes o USING (candidate_id)
                WHERE c.run_id = ?
                GROUP BY c.candidate_status, c.setup_family, c.direction
                ORDER BY rows DESC, c.candidate_status, c.setup_family, c.direction
                """,
                (run_id,),
            )
        ],
        "episode_outcomes": [
            dict(row)
            for row in connection.execute(
                """
                SELECT c.candidate_status, c.setup_family, c.direction,
                       COUNT(*) AS episodes,
                       ROUND(AVG(o.return_60m), 6) AS avg_return_60m,
                       ROUND(AVG(o.mfe_60m_pct), 6) AS avg_mfe_60m_pct,
                       ROUND(AVG(o.mae_60m_pct), 6) AS avg_mae_60m_pct,
                       SUM(o.outcome_complete_240m) AS complete_240m
                FROM shadow_candidates c
                JOIN shadow_outcomes o USING (candidate_id)
                WHERE c.run_id = ? AND c.candidate_is_new = 1
                GROUP BY c.candidate_status, c.setup_family, c.direction
                ORDER BY episodes DESC, c.candidate_status, c.setup_family, c.direction
                """,
                (run_id,),
            )
        ],
    }


def run_replay(
    dataset_source: str | Path,
    output_db: str | Path,
    *,
    replace: bool = False,
    config: ExtractionConfig | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Run shadow replay atomically and leave source databases unchanged."""

    output_path = Path(output_db).resolve()
    if output_path.exists() and not replace:
        raise ParticleReplayError(
            f"Output already exists: {output_path}. Use --replace explicitly."
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output_path.with_suffix(output_path.suffix + ".tmp")
    if temporary_output.exists():
        temporary_output.unlink()

    with open_dataset(dataset_source) as (dataset_dir, manifest):
        history_path = dataset_dir / "history.db"
        research_path = dataset_dir / "mos_research.db"
        for path in (history_path, research_path):
            _check_database(path)
        history_hash_before = _sha256(history_path)
        research_hash_before = _sha256(research_path)
        run_id = stable_id(
            history_hash_before,
            research_hash_before,
            PARTICLE_LOGIC_VERSION,
        )
        effective_config = config or ExtractionConfig()
        config_json = json.dumps(effective_config.__dict__, sort_keys=True)

        connection = initialize_database(temporary_output)
        try:
            created_at = datetime.now(timezone.utc).isoformat()
            connection.execute(
                """
                INSERT INTO shadow_runs (
                    run_id, created_at_utc, source_history_sha256,
                    source_research_sha256, source_manifest_json, logic_version,
                    schema_version, status, config_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'RUNNING', ?)
                """,
                (
                    run_id,
                    created_at,
                    history_hash_before,
                    research_hash_before,
                    json.dumps(manifest, ensure_ascii=False, sort_keys=True),
                    PARTICLE_LOGIC_VERSION,
                    PARTICLE_SHADOW_SCHEMA_VERSION,
                    config_json,
                ),
            )
            connection.commit()

            counts = {}
            counts.update(
                extract_particles(
                    history_path,
                    research_path,
                    connection,
                    run_id,
                    effective_config,
                )
            )
            counts.update(build_constellations(connection, run_id))
            counts.update(attach_outcomes(connection, run_id, research_path))

            if _sha256(history_path) != history_hash_before:
                raise ParticleReplayError("history.db changed during read-only replay")
            if _sha256(research_path) != research_hash_before:
                raise ParticleReplayError("mos_research.db changed during read-only replay")

            completed_at = datetime.now(timezone.utc).isoformat()
            connection.execute(
                """
                UPDATE shadow_runs
                SET completed_at_utc = ?, status = 'COMPLETE', counts_json = ?
                WHERE run_id = ?
                """,
                (completed_at, json.dumps(counts, sort_keys=True), run_id),
            )
            connection.commit()
            summary = _summary(connection, run_id)
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ParticleReplayError("Generated shadow database failed integrity_check")
            finalize_database(connection)
        finally:
            connection.close()

    os.replace(temporary_output, output_path)
    summary_path = output_path.with_suffix(".summary.json")
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output_path, summary
