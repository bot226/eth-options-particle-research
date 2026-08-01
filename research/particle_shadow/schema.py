"""Standalone SQLite schema for MOS Particle Logic shadow replay.

The shadow database is deliberately separate from every live MOS database.
It records derived observations and outcomes but never feeds live state,
execution, events, or manual-trading decisions.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from backend.engine.version import PARTICLE_LOGIC_VERSION

PARTICLE_SHADOW_SCHEMA_VERSION = "particle_shadow_schema_v1"


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shadow_runs (
    run_id TEXT PRIMARY KEY,
    created_at_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    source_history_sha256 TEXT NOT NULL,
    source_research_sha256 TEXT NOT NULL,
    source_manifest_json TEXT NOT NULL DEFAULT '{}',
    logic_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'COMPLETE', 'FAILED')),
    config_json TEXT NOT NULL,
    counts_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS source_snapshots (
    run_id TEXT NOT NULL REFERENCES shadow_runs(run_id),
    history_snapshot_id INTEGER NOT NULL,
    timestamp_utc REAL NOT NULL,
    mos_snapshot_id TEXT,
    mos_snapshot_sequence_id INTEGER,
    mos_snapshot_lag_sec REAL,
    spot_price REAL,
    current_state TEXT,
    gamma_regime TEXT,
    oi_total REAL,
    net_gex REAL,
    atm_iv REAL,
    iv_velocity REAL,
    expansion_probability REAL,
    synthetic_flow_pressure REAL,
    execution_timing_state TEXT,
    call_wall REAL,
    put_wall REAL,
    data_quality TEXT,
    active_sources_json TEXT NOT NULL DEFAULT '[]',
    exclude_from_analysis INTEGER NOT NULL DEFAULT 0 CHECK (
        exclude_from_analysis IN (0, 1)
    ),
    exclude_reason TEXT,
    chain_contract_count INTEGER NOT NULL DEFAULT 0,
    chain_overlap_ratio REAL,
    raw_context_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (run_id, history_snapshot_id)
);

CREATE TABLE IF NOT EXISTS particle_observations (
    particle_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    history_snapshot_id INTEGER NOT NULL,
    timestamp_utc REAL NOT NULL,
    particle_type TEXT NOT NULL,
    option_type TEXT,
    expiry TEXT,
    strike REAL,
    side TEXT NOT NULL,
    source_key TEXT NOT NULL,
    previous_value REAL,
    current_value REAL,
    delta_value REAL NOT NULL,
    relative_change REAL,
    strength_score REAL NOT NULL CHECK (
        strength_score >= 0 AND strength_score <= 100
    ),
    distance_from_spot_pct REAL,
    persistence_count INTEGER NOT NULL DEFAULT 1 CHECK (persistence_count >= 1),
    source_quality TEXT NOT NULL,
    features_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (run_id, history_snapshot_id)
        REFERENCES source_snapshots(run_id, history_snapshot_id)
);

CREATE TABLE IF NOT EXISTS particle_constellations (
    constellation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    history_snapshot_id INTEGER NOT NULL,
    timestamp_utc REAL NOT NULL,
    structure_label TEXT NOT NULL,
    movement_score REAL NOT NULL CHECK (movement_score BETWEEN 0 AND 100),
    direction_score REAL NOT NULL CHECK (direction_score BETWEEN -100 AND 100),
    direction_label TEXT NOT NULL,
    trust_score REAL NOT NULL CHECK (trust_score BETWEEN 0 AND 100),
    oi_activity_score REAL NOT NULL,
    gex_activity_score REAL NOT NULL,
    term_activity_score REAL NOT NULL,
    wall_activity_score REAL NOT NULL,
    options_evidence_count INTEGER NOT NULL,
    reasons_json TEXT NOT NULL,
    FOREIGN KEY (run_id, history_snapshot_id)
        REFERENCES source_snapshots(run_id, history_snapshot_id)
);

CREATE TABLE IF NOT EXISTS shadow_candidates (
    candidate_id TEXT PRIMARY KEY,
    candidate_key TEXT NOT NULL,
    candidate_is_new INTEGER NOT NULL CHECK (candidate_is_new IN (0, 1)),
    candidate_episode_age_sec REAL NOT NULL DEFAULT 0,
    run_id TEXT NOT NULL,
    constellation_id TEXT NOT NULL REFERENCES particle_constellations(constellation_id),
    history_snapshot_id INTEGER NOT NULL,
    timestamp_utc REAL NOT NULL,
    candidate_status TEXT NOT NULL CHECK (
        candidate_status IN (
            'VOLATILITY_WATCH',
            'DIRECTIONAL_WATCH',
            'SHADOW_ENTRY_CANDIDATE'
        )
    ),
    setup_family TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT', 'NEUTRAL')),
    readiness_score REAL NOT NULL CHECK (readiness_score BETWEEN 0 AND 100),
    movement_score REAL NOT NULL,
    direction_score REAL NOT NULL,
    trust_score REAL NOT NULL,
    entry_price REAL,
    reference_level REAL,
    invalidation_level REAL,
    blockers_json TEXT NOT NULL,
    features_json TEXT NOT NULL,
    FOREIGN KEY (run_id, history_snapshot_id)
        REFERENCES source_snapshots(run_id, history_snapshot_id)
);

CREATE TABLE IF NOT EXISTS shadow_outcomes (
    candidate_id TEXT PRIMARY KEY REFERENCES shadow_candidates(candidate_id),
    entry_timestamp_utc REAL NOT NULL,
    entry_price REAL NOT NULL,
    return_5m REAL,
    return_15m REAL,
    return_30m REAL,
    return_60m REAL,
    return_120m REAL,
    return_240m REAL,
    mfe_30m_pct REAL,
    mae_30m_pct REAL,
    mfe_60m_pct REAL,
    mae_60m_pct REAL,
    mfe_120m_pct REAL,
    mae_120m_pct REAL,
    mfe_240m_pct REAL,
    mae_240m_pct REAL,
    candles_available INTEGER NOT NULL DEFAULT 0,
    outcome_complete_240m INTEGER NOT NULL DEFAULT 0 CHECK (
        outcome_complete_240m IN (0, 1)
    ),
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_source_snapshots_timestamp
    ON source_snapshots(run_id, timestamp_utc);
CREATE INDEX IF NOT EXISTS idx_particle_observations_snapshot
    ON particle_observations(run_id, history_snapshot_id);
CREATE INDEX IF NOT EXISTS idx_particle_observations_type_timestamp
    ON particle_observations(run_id, particle_type, timestamp_utc);
CREATE INDEX IF NOT EXISTS idx_constellations_timestamp
    ON particle_constellations(run_id, timestamp_utc);
CREATE INDEX IF NOT EXISTS idx_shadow_candidates_timestamp
    ON shadow_candidates(run_id, timestamp_utc);
CREATE INDEX IF NOT EXISTS idx_shadow_candidates_status
    ON shadow_candidates(run_id, candidate_status, setup_family);
CREATE INDEX IF NOT EXISTS idx_shadow_candidates_episode
    ON shadow_candidates(run_id, candidate_key, timestamp_utc);
"""


def connect_database(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def initialize_database(db_path: str | Path) -> sqlite3.Connection:
    connection = connect_database(db_path)
    connection.executescript(SCHEMA_SQL)
    now = datetime.now(timezone.utc).isoformat()
    connection.executemany(
        """
        INSERT OR REPLACE INTO schema_metadata (key, value, recorded_at_utc)
        VALUES (?, ?, ?)
        """,
        (
            ("particle_logic_version", PARTICLE_LOGIC_VERSION, now),
            ("schema_version", PARTICLE_SHADOW_SCHEMA_VERSION, now),
            ("mode", "offline_read_only_shadow", now),
        ),
    )
    connection.commit()
    return connection


def finalize_database(connection: sqlite3.Connection) -> None:
    """Make the generated research DB portable as one standalone file."""
    connection.commit()
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.execute("PRAGMA journal_mode = DELETE")
    connection.commit()
