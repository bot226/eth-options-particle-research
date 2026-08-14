"""Research Logger — Historical Intelligence Storage for MOS.

Continuously stores snapshots, events, and metrics into a SQLite database
(data/mos_research.db) for historical replay and validation.
Uses an asynchronous queue and a background thread to prevent DB locks.

Features:
- Persistent event cooldown (survives backend restarts)
- Numerical validation (safe_float) to prevent NaN/inf corruption
- Schema v2.0 with full version contract, directional flow scale
- Tiered zero-value handling (CRITICAL vs DEGRADED)
- Schema validation at startup (rejects old schema_version)
"""

import sqlite3
import time
import os
import logging
import threading
import queue
import json
import math
import uuid
from datetime import datetime
from typing import Dict, Any

log = logging.getLogger(__name__)

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'mos_research.db'))

from engine.version import CODE_VERSION, RESEARCH_SCHEMA_VERSION, ENGINE_PATCH_VERSION, FLOW_PRESSURE_SCALE

def safe_float(val, default=0.0):
    """Convert value to float with NaN/inf/corruption protection."""
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def safe_str(val, default="UNKNOWN", allowed=None):
    """Convert value to string with optional allowed-list validation."""
    s = str(val) if val is not None else default
    if allowed and s not in allowed:
        return default
    return s


def get_db_connection(db_path=DB_PATH):
    """Returns a new SQLite connection with performance PRAGMAs applied."""
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def utc_iso(ts=None):
    """Return a stable UTC ISO timestamp for debug rows."""
    if ts is None:
        ts = time.time()
    try:
        return datetime.utcfromtimestamp(float(ts)).isoformat() + "Z"
    except Exception:
        return datetime.utcfromtimestamp(time.time()).isoformat() + "Z"

class ResearchLogger:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

        # Write queue and thread
        self._queue = queue.Queue()
        self._stop_event = threading.Event()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    def _init_db(self):
        conn = get_db_connection(self.db_path)
        cursor = conn.cursor()

        # SNAPSHOTS TABLE
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS snapshots (
                timestamp_utc REAL PRIMARY KEY,
                snapshot_id TEXT,
                snapshot_sequence_id INTEGER,
                schema_version TEXT DEFAULT '2.0',
                spot_price REAL,
                
                current_state TEXT,
                previous_state TEXT,
                candidate_state TEXT,
                transition_state TEXT,
                regime_duration_sec REAL,
                
                global_confidence REAL,
                data_quality TEXT,
                active_sources TEXT,
                
                volume_total REAL,
                oi_total REAL,
                
                gamma_regime TEXT,
                net_gex REAL,
                gamma_slope REAL,
                gamma_slope_state TEXT,
                gamma_acceleration REAL,
                gamma_acceleration_state TEXT,
                
                liquidity_void_score REAL,
                dealer_hedging_pressure TEXT,
                
                expansion_probability REAL,
                compression_failure_risk REAL,
                
                atm_iv REAL,
                iv_velocity REAL,
                term_structure_state TEXT,
                
                synthetic_flow_pressure REAL,
                synthetic_flow_pressure_scale TEXT,
                
                breakout_window TEXT,
                execution_timing_state TEXT,
                signal_cluster_score REAL,
                market_phase_hash TEXT,
                
                call_wall REAL,
                put_wall REAL,
                
                exclude_from_analysis BOOLEAN DEFAULT 0,
                exclude_reason TEXT,
                data_quality_reason TEXT DEFAULT '',
                
                deribit_status TEXT,
                deribit_age_sec REAL,
                deribit_records_used INTEGER,
                deribit_error TEXT,
                option_tickers_count INTEGER,
                valid_greeks_count INTEGER,
                valid_iv_count INTEGER,
                valid_gamma_count INTEGER,
                calls_count INTEGER,
                puts_count INTEGER,
                expiries_count INTEGER,
                strikes_count INTEGER,
                
                raw_payload TEXT,
                
                code_version TEXT,
                research_schema_version TEXT,
                engine_patch_version TEXT
            )
        ''')

        # EVENTS TABLE
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_utc REAL,
                snapshot_sequence_id INTEGER,
                event_type TEXT,
                severity TEXT,
                message TEXT,
                spot_price REAL,
                current_state TEXT,
                gamma_regime TEXT,
                atm_iv REAL,
                synthetic_flow_pressure REAL,
                execution_timing_state TEXT,
                event_payload_json TEXT,
                previous_state TEXT,
                previous_execution_timing_state TEXT,
                current_execution_timing_state TEXT
            )
        ''')

        # FUTURE LABELS TABLE
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS future_labels (
                timestamp_utc REAL PRIMARY KEY,
                snapshot_id TEXT,
                snapshot_sequence_id INTEGER,
                future_return_5m REAL,
                future_return_15m REAL,
                future_return_30m REAL,
                future_max_up_30m REAL,
                future_max_down_30m REAL,
                future_realized_vol_30m REAL,
                future_range_30m REAL,
                future_breakout_strength REAL
            )
        ''')

        # BAD SNAPSHOTS TABLE
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bad_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_utc REAL,
                failure_reason TEXT,
                raw_payload TEXT
            )
        ''')

        # BOOKMARKS TABLE
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bookmarks (
                bookmark_id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_utc REAL,
                bookmark_type TEXT,
                description TEXT
            )
        ''')

        # EVENT FINGERPRINT CACHE — persistent deduplication
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS event_fingerprint_cache (
                fingerprint TEXT PRIMARY KEY,
                event_type TEXT,
                last_emitted_ts REAL
            )
        ''')

        # OHLCV CANDLES TABLE — read-only validation layer for replay.
        # This table does not affect snapshots schema or MOS state/scoring logic.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ohlcv_candles (
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                timestamp_utc REAL NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                quote_volume REAL,
                trade_count INTEGER,
                source_latency_ms REAL,
                created_at_utc REAL,
                UNIQUE(exchange, symbol, timeframe, timestamp_utc)
            )
        ''')

        # DEBUG SNAPSHOTS TABLE - persistent diagnostic breakdown per snapshot.
        # This is a read-only analysis layer and does not affect MOS scoring.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS debug_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id TEXT,
                snapshot_sequence_id INTEGER,
                timestamp_utc TEXT NOT NULL,
                code_version TEXT,
                research_schema_version TEXT,
                engine_patch_version TEXT,
                current_state TEXT,
                execution_timing_state TEXT,
                flow_breakdown_json TEXT,
                void_breakdown_json TEXT,
                signal_breakdown_json TEXT,
                transition_breakdown_json TEXT,
                execution_breakdown_json TEXT,
                state_breakdown_json TEXT,
                gamma_breakdown_json TEXT,
                replay_alignment_json TEXT,
                short_term_flow_breakdown_json TEXT,
                created_at_utc TEXT NOT NULL,
                UNIQUE(snapshot_id)
            )
        ''')

        # EVENT OUTCOMES TABLE - replay-only validation of event consequences.
        # This table is derived from persisted events/snapshots/OHLCV and does not affect live MOS logic.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS event_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER,
                snapshot_id TEXT,
                snapshot_sequence_id INTEGER,
                event_type TEXT NOT NULL,
                event_timestamp_utc TEXT NOT NULL,
                spot_price REAL,
                future_return_5m REAL,
                future_return_15m REAL,
                future_return_30m REAL,
                future_max_up_5m REAL,
                future_max_down_5m REAL,
                future_max_up_15m REAL,
                future_max_down_15m REAL,
                future_max_up_30m REAL,
                future_max_down_30m REAL,
                future_range_5m REAL,
                future_range_15m REAL,
                future_range_30m REAL,
                future_realized_vol_15m REAL,
                future_realized_vol_30m REAL,
                did_expand_15m INTEGER,
                did_expand_30m INTEGER,
                did_continue_direction_15m INTEGER,
                did_reverse_15m INTEGER,
                outcome_label TEXT,
                created_at_utc TEXT NOT NULL,
                UNIQUE(event_id)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS event_level_reactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER,
                outcome_id INTEGER,
                snapshot_id TEXT,
                snapshot_sequence_id INTEGER,
                event_type TEXT NOT NULL,
                event_timestamp_utc TEXT NOT NULL,
                spot_price REAL,
                level_type TEXT,
                level_price REAL,
                distance_pct REAL,
                level_side TEXT,
                reaction_label TEXT,
                price_zone_25 REAL,
                price_zone_50 REAL,
                price_zone_100 REAL,
                range_high_15m REAL,
                range_low_15m REAL,
                range_high_1h REAL,
                range_low_1h REAL,
                range_position_15m REAL,
                range_position_1h REAL,
                near_range_high INTEGER,
                near_range_low INTEGER,
                range_1h_partial INTEGER,
                reaction_context TEXT,
                level_result TEXT,
                nearest_level REAL,
                nearest_level_type TEXT,
                distance_to_level_pct REAL,
                future_return_5m REAL,
                future_return_15m REAL,
                future_return_30m REAL,
                future_max_up_15m REAL,
                future_max_down_15m REAL,
                future_range_15m REAL,
                future_range_30m REAL,
                max_high_30m REAL,
                min_low_30m REAL,
                last_close_30m REAL,
                context_notes_json TEXT,
                classification_reason TEXT,
                source TEXT,
                confidence TEXT,
                deribit_status TEXT,
                created_at_utc TEXT NOT NULL,
                UNIQUE(event_id)
            )
        ''')

        # MIGRATIONS
        cursor.execute("PRAGMA table_info(snapshots)")
        snapshot_cols = [info[1] for info in cursor.fetchall()]
        if "snapshot_sequence_id" not in snapshot_cols:
            cursor.execute("ALTER TABLE snapshots ADD COLUMN snapshot_sequence_id INTEGER")
        if "exclude_from_analysis" not in snapshot_cols:
            cursor.execute("ALTER TABLE snapshots ADD COLUMN exclude_from_analysis BOOLEAN DEFAULT 0")
        if "exclude_reason" not in snapshot_cols:
            cursor.execute("ALTER TABLE snapshots ADD COLUMN exclude_reason TEXT")
        if "data_quality_reason" not in snapshot_cols:
            cursor.execute("ALTER TABLE snapshots ADD COLUMN data_quality_reason TEXT DEFAULT ''")
        if "code_version" not in snapshot_cols:
            cursor.execute(f"ALTER TABLE snapshots ADD COLUMN code_version TEXT DEFAULT '{CODE_VERSION}'")
        if "research_schema_version" not in snapshot_cols:
            cursor.execute(f"ALTER TABLE snapshots ADD COLUMN research_schema_version TEXT DEFAULT '{RESEARCH_SCHEMA_VERSION}'")
        if "engine_patch_version" not in snapshot_cols:
            cursor.execute(f"ALTER TABLE snapshots ADD COLUMN engine_patch_version TEXT DEFAULT '{ENGINE_PATCH_VERSION}'")
        if "snapshot_id" not in snapshot_cols:
            cursor.execute("ALTER TABLE snapshots ADD COLUMN snapshot_id TEXT")
        if "synthetic_flow_pressure_scale" not in snapshot_cols:
            cursor.execute(f"ALTER TABLE snapshots ADD COLUMN synthetic_flow_pressure_scale TEXT DEFAULT '{FLOW_PRESSURE_SCALE}'")
        
        # Schema version warning
        if "schema_version" in snapshot_cols:
            cursor.execute("SELECT DISTINCT schema_version FROM snapshots LIMIT 5")
            versions = [r[0] for r in cursor.fetchall()]
            old_versions = [v for v in versions if v and v != RESEARCH_SCHEMA_VERSION]
            if old_versions:
                log.warning(f"Database contains old schema versions: {old_versions}. Current: {RESEARCH_SCHEMA_VERSION}. Consider clearing DB.")
            
        cursor.execute("PRAGMA table_info(events)")
        event_cols = [info[1] for info in cursor.fetchall()]
        if "snapshot_sequence_id" not in event_cols:
            cursor.execute("ALTER TABLE events ADD COLUMN snapshot_sequence_id INTEGER")
        # Phase 5 migrations — event payload and state context
        if "event_payload_json" not in event_cols:
            cursor.execute("ALTER TABLE events ADD COLUMN event_payload_json TEXT")
        if "previous_state" not in event_cols:
            cursor.execute("ALTER TABLE events ADD COLUMN previous_state TEXT")
        if "current_execution_timing_state" not in event_cols:
            cursor.execute("ALTER TABLE events ADD COLUMN current_execution_timing_state TEXT")
        if "previous_execution_timing_state" not in event_cols:
            cursor.execute("ALTER TABLE events ADD COLUMN previous_execution_timing_state TEXT")
        # snapshots migrations
        cursor.execute("PRAGMA table_info(snapshots)")
        snap_cols = [info[1] for info in cursor.fetchall()]
        snap_migrations = {
            "deribit_status": "TEXT",
            "deribit_age_sec": "REAL",
            "deribit_records_used": "INTEGER",
            "deribit_error": "TEXT",
            "option_tickers_count": "INTEGER",
            "valid_greeks_count": "INTEGER",
            "valid_iv_count": "INTEGER",
            "valid_gamma_count": "INTEGER",
            "calls_count": "INTEGER",
            "puts_count": "INTEGER",
            "expiries_count": "INTEGER",
            "strikes_count": "INTEGER",
            "raw_payload": "TEXT"
        }
        for col, col_type in snap_migrations.items():
            if col not in snap_cols:
                cursor.execute(f"ALTER TABLE snapshots ADD COLUMN {col} {col_type}")

        # future_labels migrations
        cursor.execute("PRAGMA table_info(future_labels)")
        fl_cols = [info[1] for info in cursor.fetchall()]
        if "snapshot_id" not in fl_cols:
            cursor.execute("ALTER TABLE future_labels ADD COLUMN snapshot_id TEXT")
        if "snapshot_sequence_id" not in fl_cols:
            cursor.execute("ALTER TABLE future_labels ADD COLUMN snapshot_sequence_id INTEGER")

        # debug_snapshots migrations
        cursor.execute("PRAGMA table_info(debug_snapshots)")
        debug_cols = [info[1] for info in cursor.fetchall()]
        if "short_term_flow_breakdown_json" not in debug_cols:
            cursor.execute("ALTER TABLE debug_snapshots ADD COLUMN short_term_flow_breakdown_json TEXT")

        # event_level_reactions migrations
        cursor.execute("PRAGMA table_info(event_level_reactions)")
        reaction_cols = [info[1] for info in cursor.fetchall()]
        reaction_migrations = {
            "price_zone_25": "REAL",
            "price_zone_50": "REAL",
            "price_zone_100": "REAL",
            "range_high_15m": "REAL",
            "range_low_15m": "REAL",
            "range_high_1h": "REAL",
            "range_low_1h": "REAL",
            "range_position_15m": "REAL",
            "range_position_1h": "REAL",
            "near_range_high": "INTEGER",
            "near_range_low": "INTEGER",
            "range_1h_partial": "INTEGER",
            "reaction_context": "TEXT",
            "level_result": "TEXT",
            "nearest_level": "REAL",
            "nearest_level_type": "TEXT",
            "distance_to_level_pct": "REAL",
            "future_return_5m": "REAL",
            "future_return_15m": "REAL",
            "future_return_30m": "REAL",
            "future_max_up_15m": "REAL",
            "future_max_down_15m": "REAL",
            "future_range_15m": "REAL",
            "future_range_30m": "REAL",
            "max_high_30m": "REAL",
            "min_low_30m": "REAL",
            "last_close_30m": "REAL",
            "context_notes_json": "TEXT",
            "classification_reason": "TEXT",
            "source": "TEXT",
            "confidence": "TEXT",
            "deribit_status": "TEXT",
        }
        for col, col_type in reaction_migrations.items():
            if col not in reaction_cols:
                cursor.execute(f"ALTER TABLE event_level_reactions ADD COLUMN {col} {col_type}")

        # INDEXES
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_snapshots_time ON snapshots(timestamp_utc)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_events_time ON events(timestamp_utc)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_future_labels_time ON future_labels(timestamp_utc)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_snapshots_seq ON snapshots(snapshot_sequence_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_snapshots_exclude ON snapshots(exclude_from_analysis)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_ohlcv_candles_lookup ON ohlcv_candles(symbol, timeframe, timestamp_utc)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_debug_snapshots_seq ON debug_snapshots(snapshot_sequence_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_debug_snapshots_time ON debug_snapshots(timestamp_utc)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_event_outcomes_type ON event_outcomes(event_type)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_event_outcomes_seq ON event_outcomes(snapshot_sequence_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_event_outcomes_label ON event_outcomes(outcome_label)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_event_level_reactions_type ON event_level_reactions(event_type)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_event_level_reactions_label ON event_level_reactions(reaction_label)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_event_level_reactions_seq ON event_level_reactions(snapshot_sequence_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_event_level_reactions_zone_50 ON event_level_reactions(price_zone_50)')

        # Restore sequence ID
        try:
            cursor.execute("SELECT MAX(snapshot_sequence_id) FROM snapshots")
            max_seq = cursor.fetchone()[0]
            self._sequence_id = max_seq if max_seq is not None else 0
        except Exception:
            self._sequence_id = 0

        # Load persistent event cooldowns into RAM cache
        self._event_cooldowns = {}
        try:
            cursor.execute("SELECT fingerprint, last_emitted_ts FROM event_fingerprint_cache")
            now = time.time()
            for fp, last_ts in cursor.fetchall():
                # Only load non-expired entries (max cooldown = 600s)
                if now - last_ts < 600:
                    self._event_cooldowns[fp] = last_ts
        except Exception:
            pass

        conn.commit()
        conn.close()

        # Schema validation — refuse to write into old schema silently
        self._schema_valid = True
        self._missing_columns = []
        required_cols = [
            "snapshot_id", "code_version", "research_schema_version",
            "engine_patch_version", "synthetic_flow_pressure_scale",
            "data_quality_reason", "exclude_from_analysis", "exclude_reason",
            "schema_version", "market_phase_hash", "signal_cluster_score",
            "execution_timing_state", "dealer_hedging_pressure",
        ]
        conn2 = get_db_connection(self.db_path)
        c2 = conn2.cursor()
        c2.execute("PRAGMA table_info(snapshots)")
        existing = [info[1] for info in c2.fetchall()]
        conn2.close()
        self._missing_columns = [c for c in required_cols if c not in existing]
        if self._missing_columns:
            self._schema_valid = False
            log.error(f"SCHEMA INVALID: missing columns in snapshots: {self._missing_columns}")
        else:
            log.info(f"Schema validation OK — all {len(required_cols)} required columns present")

        log.info(f"ResearchLogger initialized at {self.db_path} with sequence ID {self._sequence_id}")
        log.info(f"  CODE_VERSION={CODE_VERSION}")
        log.info(f"  SCHEMA_VERSION={RESEARCH_SCHEMA_VERSION}")
        log.info(f"  ENGINE_PATCH={ENGINE_PATCH_VERSION}")
        log.info(f"  FLOW_SCALE={FLOW_PRESSURE_SCALE}")
        log.info(f"  SCHEMA_VALID={self._schema_valid}")
        if self._missing_columns:
            log.info(f"  MISSING_COLUMNS={self._missing_columns}")

    def _worker_loop(self):
        """Background thread that pops items from queue and inserts into DB."""
        conn = get_db_connection(self.db_path)
        cursor = conn.cursor()
        
        while not self._stop_event.is_set() or not self._queue.empty():
            try:
                # Wait for an item, timeout helps check stop_event
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            try:
                action, data = item
                if action == "snapshot":
                    self._insert_snapshot(cursor, data)
                elif action == "debug_snapshot":
                    self._insert_debug_snapshot(cursor, data)
                elif action == "bad_snapshot":
                    self._insert_bad_snapshot(cursor, data)
                elif action == "event":
                    self._insert_event(cursor, data)
                elif action == "cooldown_sync":
                    self._sync_cooldowns(cursor, data)
                elif action == "flush_sync":
                    pass # Just to wake up and process everything
                
                conn.commit()
            except Exception as e:
                log.error(f"Error executing DB write: {e}")
                conn.rollback()
            finally:
                self._queue.task_done()
        
        conn.close()
        log.info("ResearchLogger background thread shut down.")

    def _insert_snapshot(self, cursor, data):
        cursor.execute('''
            INSERT INTO snapshots (
                timestamp_utc, snapshot_id, snapshot_sequence_id, schema_version, spot_price,
                current_state, previous_state, candidate_state, transition_state, regime_duration_sec,
                global_confidence, data_quality, active_sources,
                volume_total, oi_total,
                gamma_regime, net_gex, gamma_slope, gamma_slope_state, gamma_acceleration, gamma_acceleration_state,
                liquidity_void_score, dealer_hedging_pressure,
                expansion_probability, compression_failure_risk,
                atm_iv, iv_velocity, term_structure_state,
                synthetic_flow_pressure, synthetic_flow_pressure_scale,
                breakout_window, execution_timing_state,
                signal_cluster_score, market_phase_hash,
                call_wall, put_wall,
                exclude_from_analysis, exclude_reason, data_quality_reason,
                deribit_status, deribit_age_sec, deribit_records_used, deribit_error,
                option_tickers_count, valid_greeks_count, valid_iv_count, valid_gamma_count,
                calls_count, puts_count, expiries_count, strikes_count,
                raw_payload,
                code_version, research_schema_version, engine_patch_version
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?,
                ?, ?, ?
            )
        ''', data)

    def _insert_debug_snapshot(self, cursor, data):
        cursor.execute('''
            INSERT OR REPLACE INTO debug_snapshots (
                snapshot_id, snapshot_sequence_id, timestamp_utc,
                code_version, research_schema_version, engine_patch_version,
                current_state, execution_timing_state,
                flow_breakdown_json, void_breakdown_json,
                signal_breakdown_json, transition_breakdown_json,
                execution_breakdown_json, state_breakdown_json,
                gamma_breakdown_json, replay_alignment_json,
                short_term_flow_breakdown_json,
                created_at_utc
            ) VALUES (
                ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?,
                ?
            )
        ''', data)

    def _insert_bad_snapshot(self, cursor, data):
        cursor.execute('''
            INSERT INTO bad_snapshots (timestamp_utc, failure_reason, raw_payload)
            VALUES (?, ?, ?)
        ''', data)

    def _insert_event(self, cursor, data):
        cursor.execute('''
            INSERT INTO events (
                timestamp_utc, snapshot_sequence_id, event_type, severity, message,
                spot_price, current_state, gamma_regime, atm_iv,
                synthetic_flow_pressure, execution_timing_state,
                event_payload_json, previous_state,
                previous_execution_timing_state, current_execution_timing_state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', data)

    def _sync_cooldowns(self, cursor, cooldowns_dict):
        """Persist event cooldowns to SQLite."""
        for fp, ts in cooldowns_dict.items():
            # event_type is extracted from fingerprint if possible, else just use fp
            event_type = fp.split(":")[0] if ":" in fp else fp.split("_")[0]
            cursor.execute(
                "INSERT OR REPLACE INTO event_fingerprint_cache (fingerprint, event_type, last_emitted_ts) VALUES (?, ?, ?)",
                (fp, event_type, ts)
            )
        # Cleanup expired entries (older than 10 minutes)
        cutoff = time.time() - 600
        cursor.execute("DELETE FROM event_fingerprint_cache WHERE last_emitted_ts < ?", (cutoff,))

    def _json_dump(self, payload):
        try:
            return json.dumps(payload or {}, default=str, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"debug_serialization_error": str(exc)}, ensure_ascii=False)

    def _build_replay_alignment_debug(self, timestamp_utc, spot_price, synthetic_flow_pressure,
                                      signal_cluster_score, expansion_probability,
                                      liquidity_void_score):
        """Build read-only OHLCV alignment context for the debug snapshot row."""
        short_term_flow_context = {
            "status": "experimental",
            "available": False,
            "reason": "not_computed",
            "note": "Experimental debug-only context; not used by live MOS scoring or event generation.",
        }
        debug = {
            "available": False,
            "reason": "not_computed",
            "note": "Replay alignment is diagnostic only and does not affect live MOS scoring.",
            "spot_price": round(spot_price, 2),
            "synthetic_flow_pressure": round(synthetic_flow_pressure, 2),
            "flow_intensity": round(abs(synthetic_flow_pressure), 2),
            "short_term_flow_context": short_term_flow_context,
            "signal_cluster_score": round(signal_cluster_score, 2),
            "expansion_probability": round(expansion_probability, 2),
            "liquidity_void_score": round(liquidity_void_score, 2),
            "short_term_flow_pressure": 0.0,
            "flow_layer_comparison": {
                "synthetic_flow_pressure": round(synthetic_flow_pressure, 2),
                "short_term_flow_pressure": 0.0,
                "short_term_minus_synthetic": round(-synthetic_flow_pressure, 2),
                "alignment_flag": "both_flow_layers_neutral" if abs(synthetic_flow_pressure) < 10 else "legacy_flow_active_but_short_term_flow_neutral",
            },
            "alignment_flags": [
                "both_flow_layers_neutral" if abs(synthetic_flow_pressure) < 10 else "legacy_flow_active_but_short_term_flow_neutral"
            ],
        }

        try:
            conn = get_db_connection(self.db_path)
            cursor = conn.cursor()
            from_ts = float(timestamp_utc) - 30 * 60
            cursor.execute('''
                SELECT timestamp_utc, open, high, low, close, volume
                FROM ohlcv_candles
                WHERE symbol = 'ETHUSDT'
                  AND timeframe = '1m'
                  AND timestamp_utc BETWEEN ? AND ?
                ORDER BY timestamp_utc ASC
            ''', (from_ts, float(timestamp_utc)))
            rows = cursor.fetchall()
            conn.close()

            if not rows:
                debug["reason"] = "no_ohlcv_candles_in_30m_window"
                return debug

            try:
                from engine.short_term_flow_context_engine import ShortTermFlowContextEngine
                candle_dicts = [
                    {
                        "timestamp_utc": row[0],
                        "open": row[1],
                        "high": row[2],
                        "low": row[3],
                        "close": row[4],
                        "volume": row[5],
                    }
                    for row in rows
                ]
                short_term_flow_context = ShortTermFlowContextEngine.calculate(
                    timestamp_utc=timestamp_utc,
                    spot_price=spot_price,
                    candles=candle_dicts,
                )
                debug["short_term_flow_context"] = short_term_flow_context
                st_pressure = safe_float(short_term_flow_context.get("short_term_flow_pressure"))
                st_direction = short_term_flow_context.get("direction", "NEUTRAL")
                comparison = self._build_flow_layer_comparison(synthetic_flow_pressure, st_pressure, st_direction)
                debug["short_term_flow_pressure"] = round(st_pressure, 2)
                debug["short_term_vs_legacy_flow_delta"] = comparison["short_term_minus_synthetic"]
                debug["flow_layer_comparison"] = comparison
            except Exception as exc:
                debug["short_term_flow_context"] = {
                    "status": "experimental",
                    "available": False,
                    "reason": f"short_term_flow_failed:{exc}",
                    "note": "Experimental debug-only context; not used by live MOS scoring or event generation.",
                }

            first_open = safe_float(rows[0][1])
            last_close = safe_float(rows[-1][4])
            high = max(safe_float(row[2]) for row in rows)
            low = min(safe_float(row[3]) for row in rows)

            def _window_return(minutes):
                subset = rows[-minutes:] if len(rows) >= minutes else rows
                if len(subset) < 2:
                    return 0.0
                start_open = safe_float(subset[0][1])
                end_close = safe_float(subset[-1][4])
                return round((end_close - start_open) / start_open * 100, 3) if start_open > 0 else 0.0

            debug.update({
                "available": True,
                "reason": "ok",
                "ohlcv_window_candles": len(rows),
                "ohlcv_from": utc_iso(rows[0][0]),
                "ohlcv_to": utc_iso(rows[-1][0]),
                "return_5m": _window_return(5),
                "return_15m": _window_return(15),
                "return_30m": _window_return(30),
                "range_30m": round((high - low) / first_open * 100, 3) if first_open > 0 else 0.0,
                "alignment_notes": [],
            })

            if abs(debug["return_15m"]) >= 0.5 and abs(synthetic_flow_pressure) < 20:
                debug["alignment_notes"].append("ohlcv_moved_but_flow_remained_weak")
            if (
                short_term_flow_context.get("available")
                and safe_float(short_term_flow_context.get("short_term_flow_intensity")) >= 20
                and abs(synthetic_flow_pressure) < 10
            ):
                debug["alignment_notes"].append("short_term_flow_detected_legacy_flow_neutral")
            if abs(synthetic_flow_pressure) < 10:
                debug["alignment_notes"].append("flow_neutral_zone")
            if expansion_probability < 50:
                debug["alignment_notes"].append("expansion_probability_below_50")
            debug["alignment_flags"] = [debug.get("flow_layer_comparison", {}).get("alignment_flag", "both_flow_layers_neutral")]
            return debug
        except Exception as exc:
            debug["reason"] = f"replay_alignment_failed:{exc}"
            return debug

    def _build_flow_layer_comparison(self, synthetic_flow_pressure, short_term_flow_pressure, short_term_direction):
        legacy = safe_float(synthetic_flow_pressure)
        short_term = safe_float(short_term_flow_pressure)
        legacy_active = abs(legacy) >= 10
        short_active = abs(short_term) >= 8
        legacy_side = "buy" if legacy > 0 else "sell" if legacy < 0 else "neutral"
        short_side = "buy" if short_term > 0 else "sell" if short_term < 0 else "neutral"

        if short_active and not legacy_active:
            flag = "short_term_flow_active_but_legacy_flow_neutral"
        elif legacy_active and not short_active:
            flag = "legacy_flow_active_but_short_term_flow_neutral"
        elif not legacy_active and not short_active:
            flag = "both_flow_layers_neutral"
        elif legacy_side == short_side == "buy":
            flag = "both_flow_layers_aligned_buy"
        elif legacy_side == short_side == "sell":
            flag = "both_flow_layers_aligned_sell"
        else:
            flag = "flow_layers_divergent"

        return {
            "synthetic_flow_pressure": round(legacy, 2),
            "short_term_flow_pressure": round(short_term, 2),
            "short_term_minus_synthetic": round(short_term - legacy, 2),
            "short_term_direction": short_term_direction or "NEUTRAL",
            "legacy_flow_active": legacy_active,
            "short_term_flow_active": short_active,
            "alignment_flag": flag,
        }

    def _build_short_term_flow_breakdown(self, short_term_flow_context, synthetic_flow_pressure):
        """Build the persisted v21 short-term flow debug payload."""
        payload = dict(short_term_flow_context or {})
        st_pressure = safe_float(payload.get("short_term_flow_pressure"))
        direction = payload.get("direction", "NEUTRAL")
        comparison = self._build_flow_layer_comparison(synthetic_flow_pressure, st_pressure, direction)
        payload["comparison"] = {
            "synthetic_flow_pressure": comparison["synthetic_flow_pressure"],
            "short_term_minus_synthetic": comparison["short_term_minus_synthetic"],
            "old_flow_reason": "weak_directional_components" if abs(safe_float(synthetic_flow_pressure)) < 10 else "legacy_flow_active",
        }
        payload["flow_layer_comparison"] = comparison
        payload.setdefault("status", "experimental")
        payload.setdefault("flow_scale", FLOW_PRESSURE_SCALE)
        payload.setdefault("short_term_flow_pressure", 0.0)
        payload.setdefault("short_term_flow_intensity", 0.0)
        payload.setdefault("direction", "NEUTRAL")
        payload.setdefault("inputs", {
            "return_1m": 0.0,
            "return_5m": 0.0,
            "return_15m": 0.0,
            "range_5m": 0.0,
            "range_15m": 0.0,
            "volume_change_5m": 0.0,
            "volume_change_15m": 0.0,
        })
        payload.setdefault("components", {
            "return_1m_component": 0.0,
            "return_5m_component": 0.0,
            "return_15m_component": 0.0,
            "range_expansion_component": 0.0,
            "volume_acceleration_component": 0.0,
            "breakout_component": 0.0,
            "reversal_component": 0.0,
        })
        payload.setdefault("reason", "neutral_short_term_flow")
        return payload

    def _build_debug_snapshot_row(self, snapshot_id, seq_id, timestamp_utc,
                                  current_state, execution_timing_state,
                                  gamma, gamma_surface, synthetic_flow_pressure,
                                  signal_cluster_score, expansion_probability,
                                  liquidity_void_score, spot_price):
        """Collect current engine debug breakdowns for persistent diagnostics."""
        try:
            from engine.synthetic_orderflow_engine import SyntheticOrderflowEngine
            from engine.liquidity_void_engine import LiquidityVoidEngine
            from engine.regime_transition_engine import RegimeTransitionEngine
            from engine.execution_timing_engine import ExecutionTimingEngine
            from engine.state_engine import StateEngine

            flow_debug = SyntheticOrderflowEngine.get_debug()
            void_debug = LiquidityVoidEngine.get_debug()
            signal_debug = StateEngine.get_cluster_debug()
            transition_debug = RegimeTransitionEngine.get_debug()
            execution_debug = ExecutionTimingEngine.get_debug()
            state_debug = StateEngine.get_state_debug()
        except Exception as exc:
            flow_debug = {"debug_collect_error": str(exc)}
            void_debug = {}
            signal_debug = {}
            transition_debug = {}
            execution_debug = {}
            state_debug = {}

        gamma_debug = {
            "gamma_metrics": gamma.get("metrics", {}) if isinstance(gamma, dict) else {},
            "gamma_signals": gamma.get("signals", {}) if isinstance(gamma, dict) else {},
            "gamma_surface_metrics": gamma_surface.get("metrics", {}) if isinstance(gamma_surface, dict) else {},
            "gamma_surface_features": gamma_surface.get("features", {}) if isinstance(gamma_surface, dict) else {},
        }
        replay_debug = self._build_replay_alignment_debug(
            timestamp_utc,
            spot_price,
            synthetic_flow_pressure,
            signal_cluster_score,
            expansion_probability,
            liquidity_void_score,
        )
        short_term_flow_debug = self._build_short_term_flow_breakdown(
            replay_debug.get("short_term_flow_context", {}),
            synthetic_flow_pressure,
        )
        try:
            flow_debug = dict(flow_debug or {})
            flow_debug["short_term_flow_context"] = replay_debug.get("short_term_flow_context", {})
            flow_debug["short_term_flow_note"] = (
                "Experimental OHLCV 1m context only; live synthetic_flow_pressure and FLOW_SURGE are unchanged."
            )
        except Exception:
            pass

        return (
            snapshot_id,
            seq_id,
            utc_iso(timestamp_utc),
            CODE_VERSION,
            RESEARCH_SCHEMA_VERSION,
            ENGINE_PATCH_VERSION,
            current_state,
            execution_timing_state,
            self._json_dump(flow_debug),
            self._json_dump(void_debug),
            self._json_dump(signal_debug),
            self._json_dump(transition_debug),
            self._json_dump(execution_debug),
            self._json_dump(state_debug),
            self._json_dump(gamma_debug),
            self._json_dump(replay_debug),
            self._json_dump(short_term_flow_debug),
            utc_iso(),
        )

    def log_snapshot(self, state: Dict[str, Any]):
        """Extracts market state, validates numerically, and queues for DB insertion.
        
        Implements tiered zero-value handling:
        - CRITICAL: spot_price <= 0, oi_total <= 0, NaN/inf, corrupted gamma → exclude_from_analysis = 1
        - DEGRADED: atm_iv <= 0, volume_total <= 0 → snapshot included but quality marked
        """
        timestamp_utc = time.time()
        
        try:
            spot_price = safe_float(state.get("spot", 0))
            state_machine = state.get("state_machine", {})
            current_state = state_machine.get("current_state", "TRANSITION")
            
            # 1. Basic validation — if completely broken, send to bad_snapshots
            if spot_price <= 0 or current_state is None:
                raise ValueError(f"Invalid state: spot={spot_price}, state={current_state}")
                
            # Base Extraction with numerical safety
            previous_state = safe_str(state_machine.get("previous_state", "TRANSITION"))
            candidate_state = safe_str(state_machine.get("candidate_state", "TRANSITION"))
            transition_state = safe_str(state_machine.get("transition_state", "NONE"))
            regime_duration_sec = safe_float(state_machine.get("state_persistence_sec", 0.0))
            
            global_confidence = safe_float(state.get("global_confidence", 0.0))
            
            # data_quality must be categorical
            dq_allowed = {"GOOD", "DEGRADED", "PARTIAL", "CRITICAL"}
            data_quality = safe_str(state.get("data_quality", "DEGRADED"), "DEGRADED", dq_allowed)
            
            active_sources = json.dumps(state.get("active_sources", []))
            
            volume_total = safe_float(state.get("volume_total", 0.0))
            oi_total = safe_float(state.get("oi_total", 0.0))

            gamma = state.get("gamma", {})
            gamma_regime = gamma.get("signals", {}).get("gamma_regime", "UNKNOWN")
            net_gex = safe_float(gamma.get("metrics", {}).get("net_gex", 0))
            call_wall = safe_float(gamma.get("metrics", {}).get("call_wall", 0))
            put_wall = safe_float(gamma.get("metrics", {}).get("put_wall", 0))
            
            vol = state.get("volatility", {})
            atm_iv = safe_float(vol.get("metrics", {}).get("atm_iv", 0))
            iv_velocity = safe_float(vol.get("metrics", {}).get("iv_velocity", 0))
            
            # Validate IV is non-negative
            if atm_iv < 0:
                atm_iv = 0.0
            
            # Advanced Intelligence
            adv = state.get("advanced_intelligence", {})
            p1 = adv.get("phase_1", {})
            p2 = adv.get("phase_2", {})
            
            gamma_surface = p1.get("gamma_surface", {})
            gamma_slope = safe_float(gamma_surface.get("metrics", {}).get("gamma_slope", 0))
            gamma_slope_state = safe_str(gamma_surface.get("metrics", {}).get("gamma_slope_state", "neutral"))
            gamma_acceleration = safe_float(gamma_surface.get("metrics", {}).get("gamma_acceleration", 0))
            gamma_acceleration_state = safe_str(gamma_surface.get("metrics", {}).get("gamma_acceleration_state", "neutral"))
            
            lv = p1.get("liquidity_voids", {})
            # Use composite void_score instead of simple count
            liquidity_void_score = safe_float(lv.get("metrics", {}).get("void_score", 0))
            
            dh = p1.get("dealer_hedging", {})
            dealer_hedging_pressure = safe_str(dh.get("metrics", {}).get("dealer_hedging_pressure", "LOW"))
            
            rt = p1.get("regime_transition", {})
            expansion_probability = safe_float(rt.get("metrics", {}).get("expansion_probability", 0))
            compression_failure_risk = safe_float(rt.get("metrics", {}).get("compression_failure_risk", 0))
            
            ts_state = p2.get("term_structure", {})
            term_structure_state = safe_str(ts_state.get("features", {}).get("term_structure_state", "UNKNOWN"))
            
            so = p2.get("synthetic_orderflow", {})
            synthetic_flow_pressure = safe_float(so.get("metrics", {}).get("flow_momentum_score", 0))
            
            bt = p2.get("breakout_timing", {})
            breakout_window = safe_str(bt.get("features", {}).get("estimated_breakout_window", "UNKNOWN"))
            
            ex = p2.get("execution_timing", {})
            execution_timing_state = safe_str(ex.get("features", {}).get("execution_state", "WAIT"))
            
            signal_cluster_score = safe_float(adv.get("signal_cluster_score", 0))
            market_phase_hash = safe_str(adv.get("market_phase_hash", ""))
            
            # data_quality_reason formation
            dq_reasons = []
            if not active_sources or active_sources == "[]":
                dq_reasons.append("no_active_sources")
            
            deribit_status = state.get("deribit_status", "UNKNOWN")
            if deribit_status == "STALE":
                dq_reasons.append("deribit_stale_data")
            elif deribit_status == "MISSING":
                dq_reasons.append("bybit_ok_deribit_missing")
            elif deribit_status == "ERROR":
                dq_reasons.append("deribit_fetch_error")
            elif deribit_status == "OFFLINE":
                reason = state.get("deribit_disabled_reason")
                if reason:
                    if reason.startswith("deribit_"):
                        dq_reasons.append(reason)
                    else:
                        dq_reasons.append(f"deribit_disabled_{reason}")
                else:
                    dq_reasons.append("deribit_disabled")
            elif deribit_status == "EMPTY":
                dq_reasons.append("deribit_empty_response")
            elif deribit_status == "PARSE_ERROR":
                dq_reasons.append("deribit_parse_error")
                
            if atm_iv <= 0:
                dq_reasons.append("atm_iv_missing")
            if volume_total <= 0:
                dq_reasons.append("volume_total_missing")
            
            data_quality_reason = ",".join(dq_reasons) if dq_reasons else "ok"
            
            # ── Tiered Zero-Value Handling ─────────────────────────────
            exclude_from_analysis = 0
            exclude_reasons = []
            
            # CRITICAL exclusions (invalidate entire snapshot for analysis)
            if oi_total <= 0:
                exclude_reasons.append("oi_total_zero")
            if gamma_slope == 0 and gamma_acceleration == 0 and oi_total > 0:
                # Check if gamma values are suspiciously zeroed while OI exists
                if net_gex == 0:
                    exclude_reasons.append("gamma_values_invalid")
            
            # Check for NaN/inf that slipped through safe_float
            _check_fields = [spot_price, atm_iv, oi_total, volume_total, 
                           gamma_slope, gamma_acceleration, net_gex,
                           expansion_probability, liquidity_void_score]
            for f in _check_fields:
                if math.isnan(f) or math.isinf(f):
                    exclude_reasons.append("nan_or_inf_detected")
                    break
            
            if exclude_reasons:
                data_quality = "CRITICAL"
                exclude_from_analysis = 1
            else:
                if dq_reasons and data_quality == "GOOD":
                    data_quality = "DEGRADED"
            
            exclude_reason = ";".join(exclude_reasons) if exclude_reasons else ""
            
            self._sequence_id += 1
            seq_id = self._sequence_id
            
            snapshot_id = str(uuid.uuid4())
            
            row = (
                timestamp_utc, snapshot_id, seq_id, RESEARCH_SCHEMA_VERSION, spot_price,
                current_state, previous_state, candidate_state, transition_state, regime_duration_sec,
                global_confidence, data_quality, active_sources,
                volume_total, oi_total,
                gamma_regime, net_gex, gamma_slope, gamma_slope_state, gamma_acceleration, gamma_acceleration_state,
                liquidity_void_score, dealer_hedging_pressure,
                expansion_probability, compression_failure_risk,
                atm_iv, iv_velocity, term_structure_state,
                synthetic_flow_pressure, FLOW_PRESSURE_SCALE,
                breakout_window, execution_timing_state,
                signal_cluster_score, market_phase_hash,
                call_wall, put_wall,
                exclude_from_analysis, exclude_reason, data_quality_reason,
                state.get("deribit_status", "UNKNOWN"),
                state.get("deribit_age_sec", 0.0),
                state.get("deribit_records_used", 0),
                state.get("deribit_error", ""),
                state.get("option_tickers_count", 0),
                state.get("valid_greeks_count", 0),
                state.get("valid_iv_count", 0),
                state.get("valid_gamma_count", 0),
                state.get("calls_count", 0),
                state.get("puts_count", 0),
                state.get("expiries_count", 0),
                state.get("strikes_count", 0),
                json.dumps(state),
                CODE_VERSION, RESEARCH_SCHEMA_VERSION, ENGINE_PATCH_VERSION
            )
            
            self._queue.put(("snapshot", row))
            debug_row = self._build_debug_snapshot_row(
                snapshot_id=snapshot_id,
                seq_id=seq_id,
                timestamp_utc=timestamp_utc,
                current_state=current_state,
                execution_timing_state=execution_timing_state,
                gamma=gamma,
                gamma_surface=gamma_surface,
                synthetic_flow_pressure=synthetic_flow_pressure,
                signal_cluster_score=signal_cluster_score,
                expansion_probability=expansion_probability,
                liquidity_void_score=liquidity_void_score,
                spot_price=spot_price,
            )
            self._queue.put(("debug_snapshot", debug_row))
            
            # Log significant events automatically
            # Events from state_engine are passed through state["events"]
            current_exec_state = execution_timing_state
            previous_exec_state = state_machine.get("previous_execution_timing_state", "")
            for ev in state.get("events", []):
                ev_type = ev.get("type", "SYSTEM_EVENT")
                ev_sev = ev.get("severity", "INFO").upper()
                ev_msg = ev.get("message", "Unknown event")
                ev_payload = ev.get("payload", None)
                # Для execution-related events передаём правильный previous_exec_state из payload
                _prev_exec = ev_payload.get("previous_execution_timing_state", previous_exec_state) if ev_payload else previous_exec_state
                _curr_exec = ev_payload.get("current_execution_timing_state", current_exec_state) if ev_payload else current_exec_state
                self.log_event(
                    ev_type, ev_sev, ev_msg, state, seq_id,
                    payload=ev_payload,
                    previous_exec_state=_prev_exec,
                    current_exec_state=_curr_exec,
                )
            
            # NOTE: EXECUTION_WINDOW_OPEN and EXPANSION_RISK events
            # are generated by StateEngine — do NOT duplicate here

        except Exception as e:
            log.warning(f"Failed to extract/validate snapshot: {e}. Writing to bad_snapshots.")
            try:
                bad_payload = json.dumps(state, default=str)
            except Exception:
                bad_payload = str(state)[:5000]
            bad_row = (timestamp_utc, str(e), bad_payload)
            self._queue.put(("bad_snapshot", bad_row))

    def log_event(self, event_type: str, severity: str, message: str,
                  state: Dict[str, Any] = None, seq_id: int = 0,
                  payload: dict = None,
                  previous_exec_state: str = "",
                  current_exec_state: str = ""):
        """Queues an event to be written into the database with persistent cooldown."""
        if not seq_id or seq_id <= 0:
            log.warning(f"Event {event_type} dropped: missing snapshot_sequence_id")
            return
            
        from engine.state_engine import StateEngine
        warmup_guarded_events = {
            "FLOW_SURGE",
            "VOLATILITY_EXPANSION",
            "VOID_INTENSIFYING",
            "VOID_DETECTED",
            "VOID_CLEARED",
            "EXECUTION_WINDOW_OPEN",
            "STRUCTURE_UNSTABLE",
        }
        if event_type in warmup_guarded_events and seq_id < StateEngine.WARMUP_MIN_SNAPSHOTS:
            log.info(
                "Event %s dropped by DB sequence warmup guard: seq_id=%s < %s",
                event_type, seq_id, StateEngine.WARMUP_MIN_SNAPSHOTS
            )
            return

        timestamp_utc = time.time()
        
        # State values for fingerprint
        current_state = "TRANSITION"
        if state:
            current_state = state.get("state_machine", {}).get("current_state", "TRANSITION")
            
        # Check persistent cooldown
        cooldown_secs = StateEngine.EVENT_COOLDOWN.get(event_type, 60)
        
        # Determine fingerprint — specialized per event type
        EXECUTION_TRANSITION_EVENTS = {"STRUCTURE_UNSTABLE", "EXECUTION_WINDOW_OPEN"}
        
        if event_type == "REGIME_CHANGE" and payload and "previous_state" in payload:
            # REGIME_CHANGE: fingerprint per direction pair to allow repeated transitions
            fp = f"REGIME_CHANGE:{payload['previous_state']}->{payload.get('current_state', current_state)}"
        elif event_type in EXECUTION_TRANSITION_EVENTS and payload:
            # Execution transition events: fingerprint per prev→curr execution state pair
            # This allows the same transition to re-fire after cooldown expires
            # and different transitions (e.g., WAIT→STRUCTURE_UNSTABLE vs EXPANSION_CONFIRMING→STRUCTURE_UNSTABLE)
            # to each generate their own events
            prev_exec = payload.get("previous_execution_timing_state", previous_exec_state or "UNKNOWN")
            curr_exec = payload.get("current_execution_timing_state", event_type)
            # Add seq_id bucket (per 5-min window) to allow re-entry after sufficient time
            seq_bucket = int(timestamp_utc / cooldown_secs)
            fp = f"{event_type}:{prev_exec}->{curr_exec}:{seq_bucket}"
        else:
            fp = f"{event_type}:{severity}:{current_state}"
        
        last_emitted = self._event_cooldowns.get(fp, 0)
        if timestamp_utc - last_emitted < cooldown_secs:
            return  # Still in cooldown
        
        # Update cooldown
        self._event_cooldowns[fp] = timestamp_utc
        self._queue.put(("cooldown_sync", dict(self._event_cooldowns)))
        
        spot = 0.0
        current_state = "TRANSITION"
        gamma_regime = "UNKNOWN"
        atm_iv = 0.0
        synthetic_flow_pressure = 0.0
        execution_timing_state = "WAIT"
        previous_state_val = ""
        
        if state:
            try:
                spot = safe_float(state.get("spot", 0))
                current_state = state.get("state_machine", {}).get("current_state", "TRANSITION")
                previous_state_val = state.get("state_machine", {}).get("previous_state", "")
                gamma_regime = state.get("gamma", {}).get("signals", {}).get("gamma_regime", "UNKNOWN")
                atm_iv = safe_float(state.get("volatility", {}).get("metrics", {}).get("atm_iv", 0))
                synthetic_flow_pressure = safe_float(
                    state.get("advanced_intelligence", {}).get("phase_2", {})
                    .get("synthetic_orderflow", {}).get("metrics", {}).get("flow_momentum_score", 0)
                )
                execution_timing_state = (
                    state.get("advanced_intelligence", {}).get("phase_2", {})
                    .get("execution_timing", {}).get("features", {}).get("execution_state", "WAIT")
                )
            except Exception:
                pass

        # Serialize payload
        payload_json = None
        if payload:
            try:
                payload_json = json.dumps(payload)
            except Exception:
                payload_json = str(payload)

        row = (
            timestamp_utc, seq_id, event_type, severity, message,
            spot, current_state, gamma_regime, atm_iv,
            synthetic_flow_pressure, execution_timing_state,
            payload_json, previous_state_val,
            previous_exec_state or "", current_exec_state or ""
        )
        self._queue.put(("event", row))

    def flush(self):
        """Blocks until all queued items have been processed."""
        self._queue.join()

    def stop(self):
        """Signals the worker thread to stop and flushes the queue."""
        self._stop_event.set()
        self.flush()

    def close(self):
        """Alias for stop() for graceful shutdown."""
        self.stop()
        if self._worker_thread.is_alive():
            self._worker_thread.join(timeout=5.0)
