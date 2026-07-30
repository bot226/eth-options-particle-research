import sqlite3
import os
import time
import json
import logging
from typing import Dict, Any
from pathlib import Path

from engine.version import CODE_VERSION, ENGINE_PATCH_VERSION

log = logging.getLogger(__name__)

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'mos_manual.db'))
MANUAL_LOGGER_VERSION = "1.1"

def get_db_connection(db_path=DB_PATH):
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

class ManualLogger:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
            
        self.received_snapshot_post_count = 0
        self.rejected_stale_count = 0
        self.last_snapshot_error = None
        self.last_snapshot_error_ts = None
        
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()
        from engine.paper_trade_engine import PaperTradeEngine
        PaperTradeEngine.get_instance(self.db_path)

    def _init_db(self):
        conn = get_db_connection(self.db_path)
        cursor = conn.cursor()

        # 1. manual_trading_snapshots
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS manual_trading_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                frontend_session_id TEXT,
                frontend_build_version TEXT,
                source TEXT DEFAULT 'frontend',
                code_version TEXT,
                manual_logger_version TEXT,
                price REAL,
                manual_status TEXT,
                setup_type TEXT,
                manual_bias TEXT,
                setup_quality TEXT,
                actionability TEXT,
                current_state TEXT,
                execution_timing_state TEXT,
                short_term_flow_direction TEXT,
                event_type TEXT,
                level_result TEXT,
                nearest_level REAL,
                invalidation_level REAL,
                manual_reason TEXT,
                confirmation_needed TEXT,
                invalidation_condition TEXT,
                missing_conditions TEXT,
                chart_status TEXT,
                latest_candle_ts TEXT,
                last_fetch_ts TEXT,
                seconds_since_last_fetch REAL,
                candles_count INTEGER,
                source_snapshot_id INTEGER,
                source_event_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 2. manual_decision_events
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS manual_decision_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                frontend_session_id TEXT,
                source TEXT DEFAULT 'frontend',
                previous_status TEXT,
                new_status TEXT,
                previous_setup_type TEXT,
                new_setup_type TEXT,
                previous_bias TEXT,
                new_bias TEXT,
                previous_execution TEXT,
                new_execution TEXT,
                previous_flow TEXT,
                new_flow TEXT,
                price REAL,
                nearest_level REAL,
                invalidation_level REAL,
                reason TEXT,
                source_snapshot_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 3. manual_chart_health
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS manual_chart_health (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                frontend_session_id TEXT,
                source TEXT DEFAULT 'frontend',
                frontend_tab_visible INTEGER,
                chart_status TEXT,
                fetch_status TEXT,
                last_successful_fetch_ts TEXT,
                latest_candle_ts TEXT,
                latest_candle_close REAL,
                candles_count INTEGER,
                seconds_since_last_fetch REAL,
                seconds_since_latest_candle REAL,
                error_message TEXT,
                update_mode TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Indexes
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_manual_snapshots_ts ON manual_trading_snapshots(ts)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_manual_events_ts ON manual_decision_events(ts)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_manual_health_ts ON manual_chart_health(ts)')
        # Unique constraint to prevent duplicate (ts, frontend_session_id) rows
        cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_manual_snapshots_ts_session ON manual_trading_snapshots(ts, frontend_session_id)')

        # Log startup
        cursor.execute("SELECT count(*) FROM manual_trading_snapshots")
        snap_count = cursor.fetchone()[0]
        cursor.execute("SELECT count(*) FROM manual_decision_events")
        ev_count = cursor.fetchone()[0]
        cursor.execute("SELECT count(*) FROM manual_chart_health")
        health_count = cursor.fetchone()[0]

        # Idempotent column migrations — safe for existing databases
        # Use PRAGMA table_info to check before ALTER TABLE
        def _add_column_if_missing(cursor, table, column, col_def):
            cursor.execute(f"PRAGMA table_info({table})")
            existing = {row[1] for row in cursor.fetchall()}
            if column not in existing:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")

        migrations_snapshots = [
            ("frontend_build_version",     "TEXT"),
            ("engine_patch_version",       "TEXT"),
            ("level_context_ttl_sec",     "REAL"),
            ("level_context_age_sec",      "REAL"),
            ("level_context_stale",        "INTEGER"),
            ("level_context_used",         "INTEGER"),
            ("source_level_reaction_id",   "INTEGER"),
            ("source_level_reaction_ts",   "TEXT"),
            ("source_event_ts",            "TEXT"),
            ("raw_level_result",           "TEXT"),
            ("raw_nearest_level",          "REAL"),
            ("raw_level_side",             "TEXT"),
            ("manual_ui_version",          "TEXT"),
                        # Live-safe context columns
            ("live_level_result",          "TEXT"),
            ("live_nearest_level",         "REAL"),
            ("live_level_side",            "TEXT"),
            ("live_level_type",            "TEXT"),
            ("live_distance_pct",          "REAL"),
            ("live_context_used",          "INTEGER"),
            ("live_context_source",        "TEXT"),
            ("live_context_ts",            "TEXT"),
            ("live_source_event_id",       "TEXT"),
            ("live_source_snapshot_id",    "TEXT"),
            ("live_source_snapshot_sequence_id", "TEXT"),
            
            ("live_support_level", "REAL"),
            ("live_support_distance_pct", "REAL"),
            ("live_support_source", "TEXT"),
            ("live_support_result", "TEXT"),
            ("live_support_type", "TEXT"),
            
            ("live_resistance_level", "REAL"),
            ("live_resistance_distance_pct", "REAL"),
            ("live_resistance_source", "TEXT"),
            ("live_resistance_result", "TEXT"),
            ("live_resistance_type", "TEXT"),
            
            ("primary_live_level", "REAL"),
            ("primary_live_side", "TEXT"),
            ("primary_live_distance_pct", "REAL"),
            ("primary_live_source", "TEXT"),
            ("primary_live_result", "TEXT"),
            
            ("selected_setup_level", "REAL"),
            ("selected_setup_side", "TEXT"),
            ("selected_setup_level_source", "TEXT"),
            ("selected_setup_level_distance_pct", "REAL"),
            ("selected_setup_level_result", "TEXT"),
            ("selected_setup_level_type", "TEXT"),
            ("setup_side_required", "TEXT"),
            ("decision_blocker", "TEXT"),
            ("raw_synthetic_flow_pressure", "REAL"),
            ("raw_short_term_flow_direction", "TEXT"),
            ("flow_direction_source", "TEXT"),
            ("flow_threshold_used", "TEXT"),
            ("derived_short_term_flow_direction", "TEXT"),
            ("derived_flow_strength", "TEXT"),
            ("candidate_key", "TEXT"),
            ("candidate_is_new", "INTEGER"),
            ("candidate_cooldown_active", "INTEGER"),
            ("candidate_first_seen_ts", "TEXT"),
            ("candidate_last_seen_ts", "TEXT"),
            ("candidate_cooldown_sec", "INTEGER"),
            ("candidate_age_sec", "INTEGER"),
        ]
        for col, col_def in migrations_snapshots:
            _add_column_if_missing(cursor, "manual_trading_snapshots", col, col_def)

        migrations_health = [
            ("error_source", "TEXT"),
            ("endpoint_name", "TEXT"),
            ("http_status", "INTEGER"),
            ("fetch_duration_ms", "INTEGER"),
            ("recovered_on_next_fetch", "INTEGER DEFAULT 0")
        ]
        for col, col_def in migrations_health:
            _add_column_if_missing(cursor, "manual_chart_health", col, col_def)
            
        migrations_events = [
            ("candidate_key", "TEXT"),
            ("candidate_is_new", "INTEGER"),
        ]
        for col, col_def in migrations_events:
            _add_column_if_missing(cursor, "manual_decision_events", col, col_def)
            
        migrations_impulse = [
            ("recent_return_3m", "REAL"),
            ("recent_return_5m", "REAL"),
            ("latest_close", "REAL"),
            ("latest_low", "REAL"),
            ("latest_high", "REAL"),
            ("close_vs_selected_level", "REAL"),
            ("fresh_lower_low_after_touch", "INTEGER"),
            ("fresh_higher_high_after_touch", "INTEGER"),
            ("price_confirmation_status", "TEXT"),
            ("falling_knife_guard", "INTEGER"),
            ("impulse_guard_reason", "TEXT"),
            ("decision_blocker", "TEXT"),
        ]
        for col, col_def in migrations_impulse:
            _add_column_if_missing(cursor, "manual_trading_snapshots", col, col_def)
            
        migrations_context = [
            ("price_context_source", "TEXT"),
            ("price_context_candle_count", "INTEGER"),
            ("price_context_latest_ts", "REAL"),
        ]
        for col, col_def in migrations_context:
            _add_column_if_missing(cursor, "manual_trading_snapshots", col, col_def)
            
        migrations_forming = [
            ("forming_key", "TEXT"),
            ("forming_is_new", "INTEGER"),
            ("forming_cooldown_active", "INTEGER"),
            ("forming_first_seen_ts", "TEXT"),
            ("forming_age_sec", "INTEGER"),
        ]
        for col, col_def in migrations_forming:
            _add_column_if_missing(cursor, "manual_trading_snapshots", col, col_def)
            
        migrations_ladder_guard = [
            ("previous_candidate_key", "TEXT"),
            ("previous_candidate_ts", "REAL"),
            ("previous_candidate_entry_price", "REAL"),
            ("previous_candidate_level", "REAL"),
            ("previous_candidate_invalidation", "REAL"),
            ("previous_candidate_mfe_r", "REAL"),
            ("previous_candidate_reached_1r", "INTEGER"),
            ("level_ladder_guard_active", "INTEGER"),
        ]
        for col, col_def in migrations_ladder_guard:
            _add_column_if_missing(cursor, "manual_trading_snapshots", col, col_def)
            
        migrations_main_context = [
            ("main_context_conflict_active",            "INTEGER"),
            ("main_context_conflict_side",              "TEXT"),
            ("main_context_conflict_reason",            "TEXT"),
            ("main_context_conflict_reaction_label",    "TEXT"),
            ("main_context_conflict_level_side",        "TEXT"),
            ("main_context_conflict_level_price",       "REAL"),
            ("main_context_conflict_ts",                "TEXT"),
            ("main_context_conflict_age_sec",           "REAL"),
            ("main_context_conflict_stale",             "INTEGER"),
            ("main_context_conflict_source",            "TEXT"),
            ("latest_main_context_direction",           "TEXT"),
            ("latest_main_context_reaction_label",      "TEXT"),
            ("latest_main_context_level_side",          "TEXT"),
            ("latest_main_context_level_price",         "REAL"),
            ("latest_main_context_ts",                  "TEXT"),
            ("latest_main_context_age_sec",             "REAL"),
            ("checked_main_context_count",              "INTEGER"),
            
            ("main_context_none_reason",                "TEXT"),
            ("main_context_lookup_from_ts",             "TEXT"),
            ("main_context_lookup_to_ts",               "TEXT"),
            ("main_context_candidate_count",            "INTEGER"),
            ("main_context_directional_candidate_count", "INTEGER"),
            ("latest_rejected_context_ts",              "TEXT"),
            ("latest_rejected_context_label",           "TEXT"),
            ("latest_rejected_context_level_side",      "TEXT"),
            ("latest_rejected_context_direction",       "TEXT"),
            ("latest_rejected_context_reject_reason",   "TEXT"),
            ("latest_main_context_source",              "TEXT"),
            ("latest_main_context_confidence",          "TEXT"),
            ("latest_main_context_deribit_status",      "TEXT"),
        ]
        for col, col_def in migrations_main_context:
            _add_column_if_missing(cursor, "manual_trading_snapshots", col, col_def)

        migrations_price_source = [
            ("reference_price",                    "REAL"),
            ("execution_price",                    "REAL"),
            ("basis",                              "REAL"),
            ("basis_pct",                          "REAL"),
            ("execution_venue",                    "TEXT"),
            ("execution_symbol",                   "TEXT"),
            ("reference_venue",                    "TEXT"),
            ("reference_symbol",                   "TEXT"),
            ("ohlcv_source",                       "TEXT"),
            # v52: execution instrument metadata
            ("ohlcv_exchange",                     "TEXT"),
            ("ohlcv_market_type",                  "TEXT"),
            ("ohlcv_symbol",                       "TEXT"),
            ("ohlcv_timeframe",                    "TEXT"),
            ("candle_source_verified",             "INTEGER"),
            # v52: OHLCV sync check
            ("latest_execution_ohlcv_close",       "REAL"),
            ("execution_price_ohlcv_diff",         "REAL"),
            ("execution_price_ohlcv_diff_pct",     "REAL"),
            ("execution_price_ohlcv_sync_status",  "TEXT"),
            # price source usage labels
            ("price_source_for_entry",             "TEXT"),
            ("price_source_for_levels",            "TEXT"),
            ("price_source_for_outcome",           "TEXT"),
            # v1.2: reference sanity fields
            ("reference_price_status",             "TEXT"),
            ("basis_valid",                        "INTEGER"),
            ("reference_price_source",             "TEXT"),
            ("reference_candidate_count",          "INTEGER"),
            # Outcome/replay execution fields
            ("entry_execution_price",              "REAL"),
            ("protective_stop_execution_price",    "REAL"),
            ("stop_execution_price",               "REAL"),   # legacy alias kept
            ("tp1_execution_price",                "REAL"),
            ("tp2_execution_price",                "REAL"),
            ("tp3_execution_price",                "REAL"),
            ("mfe_r_execution",                   "REAL"),
            ("mae_r_execution",                   "REAL"),
            ("execution_risk_abs",                 "REAL"),
            ("execution_risk_pct",                 "REAL"),
            ("protective_stop_source",             "TEXT"),
            ("tp_source",                          "TEXT"),
            ("entry_block_stage",                       "TEXT"),
            ("entry_block_reason",                      "TEXT"),
        ]
        for col, col_def in migrations_price_source:
            _add_column_if_missing(cursor, "manual_trading_snapshots", col, col_def)
            
        migrations_paper_trade = [
            ("paper_trade_open_attempted", "INTEGER"),
            ("paper_trade_open_result", "TEXT"),
            ("paper_trade_skipped_reason", "TEXT"),
            ("paper_trade_open_error", "TEXT"),
            ("paper_trade_candidate_key", "TEXT"),
            ("paper_trade_candidate_is_new", "INTEGER"),
        ]
        for col, col_def in migrations_paper_trade:
            _add_column_if_missing(cursor, "manual_trading_snapshots", col, col_def)
        migrations_diagnostics = [
            ("entry_block_reason", "TEXT"),
            ("entry_block_stage", "TEXT"),
            ("entry_missing_conditions_json", "TEXT"),
            ("entry_quality_score", "REAL"),
            ("entry_quality_components_json", "TEXT"),
            ("entry_required_score", "REAL"),
            ("entry_setup_side", "TEXT"),
            ("entry_candidate_side", "TEXT"),
            ("entry_distance_to_level_pct", "REAL"),
            ("entry_distance_to_trigger_pct", "REAL"),
            ("entry_distance_to_invalidation_pct", "REAL"),
            ("entry_main_context_direction", "TEXT"),
            ("entry_main_context_alignment", "TEXT"),
            ("entry_main_context_confidence", "TEXT"),
            ("entry_flow_alignment", "TEXT"),
            ("entry_candle_confirmation", "TEXT"),
            ("entry_level_confirmation", "TEXT"),
            ("entry_cooldown_active", "INTEGER"),
            ("entry_same_zone_guard_active", "INTEGER"),
            ("entry_previous_candidate_guard_active", "INTEGER"),
            ("entry_guard_reasons_json", "TEXT"),
            ("entry_debug_json", "TEXT"),
        ]
        for col, col_def in migrations_diagnostics:
            _add_column_if_missing(cursor, "manual_trading_snapshots", col, col_def)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_manual_snapshots_candidate_ts ON manual_trading_snapshots(candidate_key, ts)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_manual_events_candidate_ts ON manual_decision_events(candidate_key, ts)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_manual_snapshots_forming_ts ON manual_trading_snapshots(forming_key, ts)")

        conn.commit()
        conn.close()

        log.info(f"ManualLogger initialized")
        log.info(f"db_path: {self.db_path}")
        log.info(f"tables_ok: True")
        log.info(f"latest_counts: snapshots={snap_count}, events={ev_count}, health={health_count}")

    def log_snapshot(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Insert manual trading snapshot.

        Returns dict with:
          {"status": "ok"}                       — inserted
          {"status": "rejected_stale", ...}      — code_version mismatch
          {"status": "duplicate", ...}           — (ts, session_id) already exists
          {"status": "error", ...}               — unexpected exception
        """
        self.received_snapshot_post_count += 1

        # ── Stale payload guard ────────────────────────────────────────────────
        payload_version = payload.get('code_version') or 'unknown'
        if payload_version != CODE_VERSION:
            self.rejected_stale_count += 1
            log.warning(
                f"[ManualLogger] REJECTED stale snapshot: payload code_version={payload_version!r} "
                f"!= backend CODE_VERSION={CODE_VERSION!r}. "
                f"frontend_session_id={payload.get('frontend_session_id')!r} "
                f"ts={payload.get('ts')!r}. Total rejected: {self.rejected_stale_count}"
            )
            return {
                "status": "rejected_stale",
                "reason": "payload_code_version_mismatch",
                "payload_code_version": payload_version,
                "expected_code_version": CODE_VERSION,
            }
        # ──────────────────────────────────────────────────────────────────────

        conn = get_db_connection(self.db_path)
        cursor = conn.cursor()
        try:
            missing_conditions = payload.get('missing_conditions')
            if isinstance(missing_conditions, (dict, list)):
                missing_conditions = json.dumps(missing_conditions)
                
            fields = {
                'ts': payload.get('ts'),
                'frontend_session_id': payload.get('frontend_session_id'),
                'frontend_build_version': payload.get('frontend_build_version'),
                'source': payload.get('source', 'frontend'),
                'code_version': payload.get('code_version'),
                'engine_patch_version': payload.get('engine_patch_version', ENGINE_PATCH_VERSION),
                'manual_logger_version': MANUAL_LOGGER_VERSION,
                'manual_ui_version': payload.get('manual_ui_version'),
                'price': payload.get('price'),
                'manual_status': payload.get('manual_status'),
                'setup_type': payload.get('setup_type'),
                'manual_bias': payload.get('manual_bias'),
                'setup_quality': payload.get('setup_quality'),
                'actionability': payload.get('actionability'),
                'current_state': payload.get('current_state'),
                'execution_timing_state': payload.get('execution_timing_state'),
                'short_term_flow_direction': payload.get('short_term_flow_direction'),
                'raw_synthetic_flow_pressure': payload.get('raw_synthetic_flow_pressure'),
                'raw_short_term_flow_direction': payload.get('raw_short_term_flow_direction'),
                'flow_direction_source': payload.get('flow_direction_source'),
                'flow_threshold_used': payload.get('flow_threshold_used'),
                'derived_short_term_flow_direction': payload.get('derived_short_term_flow_direction'),
                'derived_flow_strength': payload.get('derived_flow_strength'),
                'event_type': payload.get('event_type'),
                'level_result': payload.get('level_result'),
                'nearest_level': payload.get('nearest_level'),
                'invalidation_level': payload.get('invalidation_level'),
                'manual_reason': payload.get('manual_reason'),
                'confirmation_needed': payload.get('confirmation_needed'),
                'invalidation_condition': payload.get('invalidation_condition'),
                'missing_conditions': missing_conditions,
                'chart_status': payload.get('chart_status'),
                'latest_candle_ts': payload.get('latest_candle_ts'),
                'last_fetch_ts': payload.get('last_fetch_ts'),
                'seconds_since_last_fetch': payload.get('seconds_since_last_fetch'),
                'candles_count': payload.get('candles_count'),
                'source_snapshot_id': payload.get('source_snapshot_id'),
                'source_event_id': payload.get('source_event_id'),
                'level_context_ttl_sec': payload.get('level_context_ttl_sec'),
                'level_context_age_sec': payload.get('level_context_age_sec'),
                'level_context_stale': 1 if payload.get('level_context_stale') else 0,
                'level_context_used': 1 if payload.get('level_context_used') else 0,
                'source_level_reaction_id': payload.get('source_level_reaction_id'),
                'source_level_reaction_ts': payload.get('source_level_reaction_ts'),
                'source_event_ts': payload.get('source_event_ts'),
                'raw_level_result': payload.get('raw_level_result'),
                'raw_nearest_level': payload.get('raw_nearest_level'),
                'raw_level_side': payload.get('raw_level_side'),
                'live_level_result': payload.get('live_level_result'),
                'live_nearest_level': payload.get('live_nearest_level'),
                'live_level_side': payload.get('live_level_side'),
                'live_level_type': payload.get('live_level_type'),
                'live_distance_pct': payload.get('live_distance_pct'),
                'live_context_used': 1 if payload.get('live_context_used') else 0,
                'live_context_source': payload.get('live_context_source'),
                'live_context_ts': payload.get('live_context_ts'),
                'live_source_event_id': payload.get('live_source_event_id'),
                'live_source_snapshot_id': payload.get('live_source_snapshot_id'),
                'live_source_snapshot_sequence_id': payload.get('live_source_snapshot_sequence_id'),
                
                'live_support_level': payload.get('live_support_level'),
                'live_support_distance_pct': payload.get('live_support_distance_pct'),
                'live_support_source': payload.get('live_support_source'),
                'live_support_result': payload.get('live_support_result'),
                'live_support_type': payload.get('live_support_type'),
                
                'live_resistance_level': payload.get('live_resistance_level'),
                'live_resistance_distance_pct': payload.get('live_resistance_distance_pct'),
                'live_resistance_source': payload.get('live_resistance_source'),
                'live_resistance_result': payload.get('live_resistance_result'),
                'live_resistance_type': payload.get('live_resistance_type'),
                
                'primary_live_level': payload.get('primary_live_level'),
                'primary_live_side': payload.get('primary_live_side'),
                'primary_live_distance_pct': payload.get('primary_live_distance_pct'),
                'primary_live_source': payload.get('primary_live_source'),
                'primary_live_result': payload.get('primary_live_result'),
                
                'selected_setup_level': payload.get('selected_setup_level'),
                'selected_setup_side': payload.get('selected_setup_side'),
                'selected_setup_level_source': payload.get('selected_setup_level_source'),
                'selected_setup_level_distance_pct': payload.get('selected_setup_level_distance_pct'),
                'selected_setup_level_result': payload.get('selected_setup_level_result'),
                'selected_setup_level_type': payload.get('selected_setup_level_type'),
                'setup_side_required': payload.get('setup_side_required'),
                
                'raw_synthetic_flow_pressure': payload.get('raw_synthetic_flow_pressure'),
                'raw_short_term_flow_direction': payload.get('raw_short_term_flow_direction'),
                'flow_direction_source': payload.get('flow_direction_source'),
                'flow_threshold_used': payload.get('flow_threshold_used'),
                'derived_short_term_flow_direction': payload.get('derived_short_term_flow_direction'),
                'derived_flow_strength': payload.get('derived_flow_strength'),
                'decision_blocker': payload.get('decision_blocker'),
                'candidate_key': payload.get('candidate_key'),
                'candidate_is_new': payload.get('candidate_is_new'),
                'candidate_cooldown_active': payload.get('candidate_cooldown_active'),
                'candidate_first_seen_ts': payload.get('candidate_first_seen_ts'),
                'candidate_last_seen_ts': payload.get('candidate_last_seen_ts'),
                'candidate_cooldown_sec': payload.get('candidate_cooldown_sec'),
                'candidate_age_sec': payload.get('candidate_age_sec'),
                'recent_return_3m': payload.get('recent_return_3m'),
                'recent_return_5m': payload.get('recent_return_5m'),
                'latest_close': payload.get('latest_close'),
                'latest_low': payload.get('latest_low'),
                'latest_high': payload.get('latest_high'),
                'close_vs_selected_level': payload.get('close_vs_selected_level'),
                'fresh_lower_low_after_touch': payload.get('fresh_lower_low_after_touch'),
                'fresh_higher_high_after_touch': payload.get('fresh_higher_high_after_touch'),
                'price_confirmation_status': payload.get('price_confirmation_status'),
                'falling_knife_guard': payload.get('falling_knife_guard'),
                'impulse_guard_reason': payload.get('impulse_guard_reason'),
                'price_context_source': payload.get('price_context_source'),
                'price_context_candle_count': payload.get('price_context_candle_count'),
                'price_context_latest_ts': payload.get('price_context_latest_ts'),
                'forming_key': payload.get('forming_key'),
                'forming_is_new': payload.get('forming_is_new'),
                'forming_cooldown_active': payload.get('forming_cooldown_active'),
                'forming_first_seen_ts': payload.get('forming_first_seen_ts'),
                'forming_age_sec': payload.get('forming_age_sec'),
                'previous_candidate_key': payload.get('previous_candidate_key'),
                'previous_candidate_ts': payload.get('previous_candidate_ts'),
                'previous_candidate_entry_price': payload.get('previous_candidate_entry_price'),
                'previous_candidate_level': payload.get('previous_candidate_level'),
                'previous_candidate_invalidation': payload.get('previous_candidate_invalidation'),
                'previous_candidate_mfe_r': payload.get('previous_candidate_mfe_r'),
                'previous_candidate_reached_1r': payload.get('previous_candidate_reached_1r'),
                'level_ladder_guard_active': payload.get('level_ladder_guard_active'),
                'main_context_conflict_active':          payload.get('main_context_conflict_active'),
                'main_context_conflict_side':            payload.get('main_context_conflict_side'),
                'main_context_conflict_reason':          payload.get('main_context_conflict_reason'),
                'main_context_conflict_reaction_label':  payload.get('main_context_conflict_reaction_label'),
                'main_context_conflict_level_side':      payload.get('main_context_conflict_level_side'),
                'main_context_conflict_level_price':     payload.get('main_context_conflict_level_price'),
                'main_context_conflict_ts':              payload.get('main_context_conflict_ts'),
                'main_context_conflict_age_sec':         payload.get('main_context_conflict_age_sec'),
                'main_context_conflict_stale':           payload.get('main_context_conflict_stale'),
                'main_context_conflict_source':          payload.get('main_context_conflict_source'),
                'latest_main_context_direction':         payload.get('latest_main_context_direction'),
                'latest_main_context_reaction_label':    payload.get('latest_main_context_reaction_label'),
                'latest_main_context_level_side':        payload.get('latest_main_context_level_side'),
                'latest_main_context_level_price':       payload.get('latest_main_context_level_price'),
                'latest_main_context_ts':                payload.get('latest_main_context_ts'),
                'latest_main_context_age_sec':           payload.get('latest_main_context_age_sec'),
                'checked_main_context_count':            payload.get('checked_main_context_count'),
                
                'main_context_none_reason':              payload.get('main_context_none_reason'),
                'main_context_lookup_from_ts':           payload.get('main_context_lookup_from_ts'),
                'main_context_lookup_to_ts':             payload.get('main_context_lookup_to_ts'),
                'main_context_candidate_count':          payload.get('main_context_candidate_count'),
                'main_context_directional_candidate_count': payload.get('main_context_directional_candidate_count'),
                'latest_rejected_context_ts':            payload.get('latest_rejected_context_ts'),
                'latest_rejected_context_label':         payload.get('latest_rejected_context_label'),
                'latest_rejected_context_level_side':    payload.get('latest_rejected_context_level_side'),
                'latest_rejected_context_direction':     payload.get('latest_rejected_context_direction'),
                'latest_rejected_context_reject_reason': payload.get('latest_rejected_context_reject_reason'),
                'latest_main_context_source':            payload.get('latest_main_context_source'),
                'latest_main_context_confidence':        payload.get('latest_main_context_confidence'),
                'latest_main_context_deribit_status':    payload.get('latest_main_context_deribit_status'),
                
                # v34/v52: price source separation + execution data integrity
                'reference_price':                  payload.get('reference_price'),
                'execution_price':                  payload.get('execution_price'),
                'basis':                            payload.get('basis'),
                'basis_pct':                        payload.get('basis_pct'),
                'execution_venue':                  payload.get('execution_venue'),
                'execution_symbol':                 payload.get('execution_symbol'),
                'reference_venue':                  payload.get('reference_venue'),
                'reference_symbol':                 payload.get('reference_symbol'),
                'ohlcv_source':                     payload.get('ohlcv_source'),
                'ohlcv_exchange':                   payload.get('ohlcv_exchange'),
                'ohlcv_market_type':                payload.get('ohlcv_market_type'),
                'ohlcv_symbol':                     payload.get('ohlcv_symbol'),
                'ohlcv_timeframe':                  payload.get('ohlcv_timeframe'),
                'candle_source_verified':            payload.get('candle_source_verified'),
                'latest_execution_ohlcv_close':     payload.get('latest_execution_ohlcv_close'),
                'execution_price_ohlcv_diff':       payload.get('execution_price_ohlcv_diff'),
                'execution_price_ohlcv_diff_pct':   payload.get('execution_price_ohlcv_diff_pct'),
                'execution_price_ohlcv_sync_status': payload.get('execution_price_ohlcv_sync_status'),
                'price_source_for_entry':           payload.get('price_source_for_entry'),
                'price_source_for_levels':          payload.get('price_source_for_levels'),
                'price_source_for_outcome':         payload.get('price_source_for_outcome'),
                # v1.2: reference sanity fields
                'reference_price_status':           payload.get('reference_price_status'),
                'basis_valid':                      payload.get('basis_valid'),
                'reference_price_source':           payload.get('reference_price_source'),
                'reference_candidate_count':        payload.get('reference_candidate_count'),
                # Outcome/replay execution fields
                'entry_execution_price':            payload.get('entry_execution_price'),
                'protective_stop_execution_price':  payload.get('protective_stop_execution_price'),
                'stop_execution_price':             payload.get('stop_execution_price'),
                'tp1_execution_price':              payload.get('tp1_execution_price'),
                'tp2_execution_price':              payload.get('tp2_execution_price'),
                'tp3_execution_price':              payload.get('tp3_execution_price'),
                'mfe_r_execution':                  payload.get('mfe_r_execution'),
                'mae_r_execution':                  payload.get('mae_r_execution'),
                'execution_risk_abs':               payload.get('execution_risk_abs'),
                'execution_risk_pct':               payload.get('execution_risk_pct'),
                'protective_stop_source':           payload.get('protective_stop_source'),
                'tp_source':                        payload.get('tp_source'),
                
                # Diagnostics
                'entry_block_reason': payload.get('entry_block_reason'),
                'entry_block_stage': payload.get('entry_block_stage'),
                'entry_missing_conditions_json': json.dumps(payload.get('entry_missing_conditions_json')) if payload.get('entry_missing_conditions_json') is not None else None,
                'entry_quality_score': payload.get('entry_quality_score'),
                'entry_quality_components_json': json.dumps(payload.get('entry_quality_components_json')) if payload.get('entry_quality_components_json') is not None else None,
                'entry_required_score': payload.get('entry_required_score'),
                'entry_setup_side': payload.get('entry_setup_side'),
                'entry_candidate_side': payload.get('entry_candidate_side'),
                'entry_distance_to_level_pct': payload.get('entry_distance_to_level_pct'),
                'entry_distance_to_trigger_pct': payload.get('entry_distance_to_trigger_pct'),
                'entry_distance_to_invalidation_pct': payload.get('entry_distance_to_invalidation_pct'),
                'entry_main_context_direction': payload.get('entry_main_context_direction'),
                'entry_main_context_alignment': payload.get('entry_main_context_alignment'),
                'entry_main_context_confidence': payload.get('entry_main_context_confidence'),
                'entry_flow_alignment': payload.get('entry_flow_alignment'),
                'entry_candle_confirmation': payload.get('entry_candle_confirmation'),
                'entry_level_confirmation': payload.get('entry_level_confirmation'),
                'entry_cooldown_active': payload.get('entry_cooldown_active'),
                'entry_same_zone_guard_active': payload.get('entry_same_zone_guard_active'),
                'entry_previous_candidate_guard_active': payload.get('entry_previous_candidate_guard_active'),
                'entry_guard_reasons_json': json.dumps(payload.get('entry_guard_reasons_json')) if payload.get('entry_guard_reasons_json') is not None else None,
                'entry_debug_json': json.dumps(payload.get('entry_debug_json')) if payload.get('entry_debug_json') is not None else None,
            }
            
            # Replace NaN and Inf with None
            import math
            for k, v in fields.items():
                if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                    fields[k] = None

            cols = ", ".join(fields.keys())
            places = ", ".join("?" for _ in fields)
            # INSERT OR IGNORE prevents duplicate (ts, frontend_session_id) rows
            query = f"INSERT OR IGNORE INTO manual_trading_snapshots ({cols}) VALUES ({places})"
            
            cursor.execute(query, tuple(fields.values()))
            rows_inserted = cursor.rowcount
            conn.commit()
            
            self.last_snapshot_error = None
            self.last_snapshot_error_ts = None

            if rows_inserted == 0:
                log.debug(
                    f"[ManualLogger] DUPLICATE snapshot skipped: "
                    f"ts={payload.get('ts')!r} session={payload.get('frontend_session_id')!r}"
                )
                return {"status": "duplicate", "ts": payload.get('ts')}

            try:
                cursor.execute("SELECT id FROM manual_trading_snapshots WHERE ts = ? AND frontend_session_id = ?", (payload.get('ts'), payload.get('frontend_session_id')))
                row = cursor.fetchone()
                if row:
                    from engine.paper_trade_engine import PaperTradeEngine
                    pte = PaperTradeEngine.get_instance(self.db_path)
                    pte.process_snapshot(row[0], payload)
            except Exception as e:
                log.error(f"[ManualLogger] PaperTradeEngine error: {e}")

            return {"status": "ok"}
        except Exception as e:
            import traceback
            log.error(
                f"[ManualLogger] CRITICAL log_snapshot failed!\n"
                f"  Type: {type(e).__name__}\n"
                f"  Message: {e}\n"
                f"  DB Path: {self.db_path}\n"
                f"  Payload Keys: {list(payload.keys())}\n"
                f"  Fields keys: {list(fields.keys()) if 'fields' in locals() else 'N/A'}\n"
                f"  Traceback: {traceback.format_exc()}"
            )
            self.last_snapshot_error = str(e)
            from datetime import datetime
            self.last_snapshot_error_ts = datetime.utcnow().isoformat() + "Z"
            raise
        finally:
            conn.close()

    def log_event(self, payload: Dict[str, Any]):
        conn = get_db_connection(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO manual_decision_events (
                    ts, frontend_session_id, source,
                    previous_status, new_status,
                    previous_setup_type, new_setup_type,
                    previous_bias, new_bias,
                    previous_execution, new_execution,
                    previous_flow, new_flow,
                    price, nearest_level, invalidation_level,
                    reason, source_snapshot_id,
                    candidate_key, candidate_is_new
                ) VALUES (
                    ?, ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?
                )
            ''', (
                payload.get('ts'),
                payload.get('frontend_session_id'),
                payload.get('source', 'frontend'),
                payload.get('previous_status'),
                payload.get('new_status'),
                payload.get('previous_setup_type'),
                payload.get('new_setup_type'),
                payload.get('previous_bias'),
                payload.get('new_bias'),
                payload.get('previous_execution'),
                payload.get('new_execution'),
                payload.get('previous_flow'),
                payload.get('new_flow'),
                payload.get('price'),
                payload.get('nearest_level'),
                payload.get('invalidation_level'),
                payload.get('reason'),
                payload.get('source_snapshot_id'),
                payload.get('candidate_key'),
                payload.get('candidate_is_new')
            ))
            conn.commit()
        finally:
            conn.close()

    def log_health(self, payload: Dict[str, Any]):
        conn = get_db_connection(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO manual_chart_health (
                    ts, frontend_session_id, source, frontend_tab_visible,
                    chart_status, fetch_status, last_successful_fetch_ts,
                    latest_candle_ts, latest_candle_close, candles_count,
                    seconds_since_last_fetch, seconds_since_latest_candle,
                    error_message, update_mode,
                    error_source, endpoint_name, http_status,
                    fetch_duration_ms, recovered_on_next_fetch
                ) VALUES (
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?
                )
            ''', (
                payload.get('ts'),
                payload.get('frontend_session_id'),
                payload.get('source', 'frontend'),
                payload.get('frontend_tab_visible', 1),
                payload.get('chart_status'),
                payload.get('fetch_status'),
                payload.get('last_successful_fetch_ts'),
                payload.get('latest_candle_ts'),
                payload.get('latest_candle_close'),
                payload.get('candles_count'),
                payload.get('seconds_since_last_fetch'),
                payload.get('seconds_since_latest_candle'),
                payload.get('error_message'),
                payload.get('update_mode'),
                payload.get('error_source'),
                payload.get('endpoint_name'),
                payload.get('http_status'),
                payload.get('fetch_duration_ms'),
                payload.get('recovered_on_next_fetch', 0)
            ))
            conn.commit()
        finally:
            conn.close()

    def get_logging_status(self):
        conn = get_db_connection(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT count(*), MAX(ts) FROM manual_trading_snapshots")
            snap_res = cursor.fetchone()
            latest_snap_ts_str = snap_res[1]
            
            cursor.execute("SELECT count(*), MAX(ts) FROM manual_decision_events")
            ev_res = cursor.fetchone()
            
            cursor.execute("SELECT count(*), MAX(ts) FROM manual_chart_health")
            health_res = cursor.fetchone()
            
            cursor.execute("PRAGMA table_info(manual_trading_snapshots)")
            columns_info = cursor.fetchall()
            
            # Fetch latest health statuses for specific endpoints
            current_endpoint_status = "UNKNOWN"
            last_current_error = None
            cursor.execute("SELECT fetch_status, error_message FROM manual_chart_health WHERE error_source = 'current' ORDER BY id DESC LIMIT 1")
            cur_health = cursor.fetchone()
            if cur_health:
                current_endpoint_status = cur_health[0]
                if cur_health[0] != "OK":
                    last_current_error = cur_health[1]
                    
            kline_endpoint_status = "UNKNOWN"
            last_kline_error = None
            cursor.execute("SELECT fetch_status, error_message FROM manual_chart_health WHERE error_source = 'candles' OR error_source = 'kline' ORDER BY id DESC LIMIT 1")
            kline_health = cursor.fetchone()
            if kline_health:
                kline_endpoint_status = kline_health[0]
                if kline_health[0] != "OK":
                    last_kline_error = kline_health[1]
            
            import time
            from datetime import datetime
            seconds_since_latest_manual_snapshot = None
            if latest_snap_ts_str:
                try:
                    latest_dt = datetime.fromisoformat(latest_snap_ts_str.replace("Z", "+00:00"))
                    seconds_since_latest_manual_snapshot = time.time() - latest_dt.timestamp()
                except Exception:
                    pass
                    
            seconds_since_latest_chart_health = None
            latest_chart_health_ts_str = health_res[1]
            if latest_chart_health_ts_str:
                try:
                    latest_dt = datetime.fromisoformat(latest_chart_health_ts_str.replace("Z", "+00:00"))
                    seconds_since_latest_chart_health = time.time() - latest_dt.timestamp()
                except Exception:
                    pass

            last_overlay_error = None
            cursor.execute("SELECT error_message FROM manual_chart_health WHERE error_source = 'overlay' ORDER BY id DESC LIMIT 1")
            overlay_health = cursor.fetchone()
            if overlay_health:
                last_overlay_error = overlay_health[0]
            
            return {
                "manual_db_path": self.db_path,
                "manual_logging_enabled": True,
                "snapshot_logging_enabled": True,
                "expected_snapshot_columns_count": len(columns_info),
                "received_snapshot_post_count": self.received_snapshot_post_count,
                "rejected_stale_count": self.rejected_stale_count,
                "last_snapshot_error": self.last_snapshot_error,
                "last_snapshot_error_ts": self.last_snapshot_error_ts,
                "manual_trading_snapshots_count": snap_res[0] or 0,
                "manual_decision_events_count": ev_res[0] or 0,
                "manual_chart_health_count": health_res[0] or 0,
                "latest_manual_snapshot_ts": latest_snap_ts_str,
                "seconds_since_latest_manual_snapshot": seconds_since_latest_manual_snapshot,
                "latest_manual_decision_event_ts": ev_res[1],
                "latest_chart_health_ts": health_res[1],
                "seconds_since_latest_chart_health": seconds_since_latest_chart_health,
                "current_endpoint_status": current_endpoint_status,
                "kline_endpoint_status": kline_endpoint_status,
                "last_current_error": last_current_error,
                "last_kline_error": last_kline_error,
                "last_overlay_error": last_overlay_error,
                "manual_logger_version": MANUAL_LOGGER_VERSION,
                "code_version": CODE_VERSION,
                "engine_patch_version": ENGINE_PATCH_VERSION,
            }
        finally:
            conn.close()
