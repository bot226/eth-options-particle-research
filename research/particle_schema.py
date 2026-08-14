"""SQLite schema for the autonomous ETH options particle collector.

This module owns only the collector database. It does not import MOS engines,
write MOS databases, emit trading signals, or change dashboard state.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


COLLECTOR_VERSION = "options_particle_collector_v1"
SCHEMA_VERSION = "options_particle_schema_v1"
PARTICLE_DEFINITION_VERSION = "options_particle_definitions_v1"


SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS option_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    timestamp_utc TEXT NOT NULL,
    exchange TEXT NOT NULL,
    underlying TEXT NOT NULL,
    spot_price REAL NOT NULL CHECK (spot_price > 0),
    index_price REAL CHECK (index_price IS NULL OR index_price > 0),
    source_status TEXT NOT NULL CHECK (
        source_status IN ('ONLINE', 'DEGRADED', 'OFFLINE')
    ),
    data_quality TEXT NOT NULL CHECK (
        data_quality IN ('GOOD', 'DEGRADED', 'PARTIAL', 'CRITICAL')
    ),
    data_quality_reason TEXT,
    source_age_sec REAL CHECK (source_age_sec IS NULL OR source_age_sec >= 0),
    collector_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    particle_definition_version TEXT NOT NULL,
    exclude_from_analysis INTEGER NOT NULL DEFAULT 0 CHECK (
        exclude_from_analysis IN (0, 1)
    ),
    exclude_reason TEXT,
    raw_payload_json TEXT NOT NULL DEFAULT '{{}}'
);

CREATE TABLE IF NOT EXISTS option_contracts (
    contract_record_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES option_snapshots(snapshot_id),
    exchange TEXT NOT NULL,
    instrument_name TEXT NOT NULL,
    source_timestamp_utc TEXT,
    expiry_utc TEXT NOT NULL,
    strike REAL NOT NULL CHECK (strike > 0),
    option_type TEXT NOT NULL CHECK (option_type IN ('C', 'P')),
    underlying_price REAL CHECK (underlying_price IS NULL OR underlying_price > 0),
    mark_price REAL,
    bid_price REAL,
    ask_price REAL,
    mark_iv REAL,
    bid_iv REAL,
    ask_iv REAL,
    iv_scale TEXT NOT NULL CHECK (iv_scale IN ('DECIMAL', 'PERCENT')),
    delta REAL,
    gamma REAL,
    vega REAL,
    theta REAL,
    open_interest REAL CHECK (open_interest IS NULL OR open_interest >= 0),
    open_interest_unit TEXT NOT NULL,
    volume REAL CHECK (volume IS NULL OR volume >= 0),
    volume_unit TEXT NOT NULL,
    source_age_sec REAL CHECK (source_age_sec IS NULL OR source_age_sec >= 0),
    raw_features_json TEXT NOT NULL DEFAULT '{{}}',
    UNIQUE (snapshot_id, exchange, instrument_name)
);

CREATE TABLE IF NOT EXISTS ohlcv_candles (
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    open_time_utc TEXT NOT NULL,
    open REAL NOT NULL CHECK (open > 0),
    high REAL NOT NULL CHECK (high > 0),
    low REAL NOT NULL CHECK (low > 0),
    close REAL NOT NULL CHECK (close > 0),
    volume REAL NOT NULL CHECK (volume >= 0),
    data_source TEXT NOT NULL,
    collector_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    raw_payload_json TEXT NOT NULL DEFAULT '{{}}',
    PRIMARY KEY (exchange, symbol, timeframe, open_time_utc),
    CHECK (high >= low),
    CHECK (high >= open AND high >= close),
    CHECK (low <= open AND low <= close)
);

CREATE TABLE IF NOT EXISTS particles (
    particle_id TEXT PRIMARY KEY,
    timestamp_utc TEXT NOT NULL,
    snapshot_id TEXT REFERENCES option_snapshots(snapshot_id),
    symbol TEXT NOT NULL,
    timeframe TEXT,
    particle_type TEXT NOT NULL,
    side TEXT,
    price REAL CHECK (price IS NULL OR price > 0),
    level_price REAL CHECK (level_price IS NULL OR level_price > 0),
    level_type TEXT,
    distance_to_level REAL,
    volatility_proxy REAL,
    impulse_score REAL,
    compression_score REAL,
    data_source TEXT NOT NULL,
    calculation_method TEXT NOT NULL,
    collector_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    particle_definition_version TEXT NOT NULL,
    exclude_from_analysis INTEGER NOT NULL DEFAULT 0 CHECK (
        exclude_from_analysis IN (0, 1)
    ),
    exclude_reason TEXT,
    raw_features_json TEXT NOT NULL DEFAULT '{{}}'
);

CREATE TABLE IF NOT EXISTS source_health (
    health_id TEXT PRIMARY KEY,
    timestamp_utc TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ONLINE', 'DEGRADED', 'OFFLINE')),
    records_received INTEGER NOT NULL DEFAULT 0 CHECK (records_received >= 0),
    valid_gamma_count INTEGER NOT NULL DEFAULT 0 CHECK (valid_gamma_count >= 0),
    valid_iv_count INTEGER NOT NULL DEFAULT 0 CHECK (valid_iv_count >= 0),
    valid_oi_count INTEGER NOT NULL DEFAULT 0 CHECK (valid_oi_count >= 0),
    max_source_age_sec REAL CHECK (
        max_source_age_sec IS NULL OR max_source_age_sec >= 0
    ),
    data_quality TEXT NOT NULL CHECK (
        data_quality IN ('GOOD', 'DEGRADED', 'PARTIAL', 'CRITICAL')
    ),
    reason TEXT,
    collector_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{{}}'
);

CREATE INDEX IF NOT EXISTS idx_option_snapshots_timestamp
    ON option_snapshots(timestamp_utc);
CREATE INDEX IF NOT EXISTS idx_option_contracts_snapshot
    ON option_contracts(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_particles_timestamp
    ON particles(timestamp_utc);
CREATE INDEX IF NOT EXISTS idx_particles_type_timestamp
    ON particles(particle_type, timestamp_utc);
CREATE INDEX IF NOT EXISTS idx_source_health_source_timestamp
    ON source_health(source, timestamp_utc);
"""


def connect_database(db_path: str | Path) -> sqlite3.Connection:
    """Open the standalone particle database with its own SQLite settings."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def initialize_database(db_path: str | Path) -> sqlite3.Connection:
    """Create the v1 schema and return an open database connection."""
    connection = connect_database(db_path)
    connection.executescript(SCHEMA_SQL)

    recorded_at_utc = datetime.now(timezone.utc).isoformat()
    versions = {
        "collector_version": COLLECTOR_VERSION,
        "schema_version": SCHEMA_VERSION,
        "particle_definition_version": PARTICLE_DEFINITION_VERSION,
    }
    connection.executemany(
        """
        INSERT OR IGNORE INTO schema_metadata (key, value, recorded_at_utc)
        VALUES (?, ?, ?)
        """,
        [(key, value, recorded_at_utc) for key, value in versions.items()],
    )
    connection.commit()
    return connection
