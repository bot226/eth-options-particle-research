"""ServerSnapshotWorker — server-side manual trading snapshot writer.

v52: Replaces the 60s frontend-driven snapshot interval with a
deterministic 20-30 second server-side loop.

Rules:
- Target interval: 20-30 sec (configured via interval_sec, default 25)
- Duplicate timestamp prevention: uses (ts_rounded, session_id) UNIQUE index
- One active writer: if the payload is identical to the last, skip
- Stale frontend payloads: ManualLogger already rejects them via code_version check
- Runs only if the data manager is available and warmed up (has expiries)
- Does NOT compute outcome/future fields
- Does NOT signal-based exit
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

# Import lazily inside methods to avoid circular imports at module level
_market_module = None

# Session ID for this backend process (single writer per process)
import uuid
_SERVER_SESSION_ID = f"server_{uuid.uuid4().hex[:12]}"


def _get_market_module():
    global _market_module
    if _market_module is None:
        from routes import market as _m
        _market_module = _m
    return _market_module


class ServerSnapshotWorker:
    """Async worker that writes manual trading snapshots every ~25 seconds.

    Architecture:
    - Calls _build_manual_trading_payload() internally (same as /api/manual-trading/current)
    - Writes directly to ManualLogger.log_snapshot() with source='server'
    - Unique session_id per backend process to prevent duplicate (ts, session) rows
    - Uses rounded ISO ts (to nearest second) for deterministic dedup
    """

    def __init__(self, interval_sec: int = 25):
        self.interval_sec = max(20, min(interval_sec, 30))  # clamp 20-30
        self._task: asyncio.Task | None = None
        self._last_snapshot_ts: float = 0.0
        self._snapshots_written: int = 0
        self._snapshots_skipped: int = 0
        self._last_error: str = ""
        self._consecutive_errors: int = 0
        self._session_id: str = _SERVER_SESSION_ID
        
        # Debug counters for immediate entry snapshots
        self.immediate_entry_snapshot_written: int = 0
        self.immediate_entry_snapshot_skipped_duplicate: int = 0
        self.immediate_entry_paper_open_result: str = None
        self.last_immediate_entry_candidate_key: str = None

    async def start(self):
        self._task = asyncio.create_task(self._loop())
        m = _get_market_module()
        logger = m.get_manual_logger()
        import os
        log.info(
            "SERVER_SNAPSHOT_WORKER_STARTED: db_path=%s cwd=%s session=%s",
            logger.db_path, os.getcwd(), self._session_id
        )

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info(
            "ServerSnapshotWorker stopped: written=%d skipped=%d",
            self._snapshots_written, self._snapshots_skipped,
        )

    async def _loop(self):
        # Brief startup delay to let DM warm up
        await asyncio.sleep(5)
        while True:
            try:
                m = _get_market_module()
                logger = m.get_manual_logger()
                dm = getattr(m, "_dm", None)
                import os
                
                # Check market time instead of real time
                if dm:
                    current_market_ts = getattr(dm, "last_update_ts", 0)
                    if current_market_ts <= 0:
                        current_market_ts = time.time()
                else:
                    current_market_ts = time.time()

                if current_market_ts - self._last_snapshot_ts >= self.interval_sec:
                    log.info(
                        "SERVER_SNAPSHOT_WORKER_TICK: db_path=%s cwd=%s latest_snapshot_ts=%s manual_snapshot_count=%d",
                        logger.db_path, os.getcwd(), self._last_snapshot_ts, self._snapshots_written
                    )
                    await self._write_snapshot_once()
                    self._last_snapshot_ts = current_market_ts
                    self._consecutive_errors = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = str(exc)
                self._consecutive_errors += 1
                m = _get_market_module()
                logger = m.get_manual_logger()
                import os
                log.error(
                    "SERVER_SNAPSHOT_WORKER_ERROR: error_message='%s' db_path=%s cwd=%s latest_snapshot_ts=%s manual_snapshot_count=%d",
                    exc, logger.db_path, os.getcwd(), self._last_snapshot_ts, self._snapshots_written
                )
            # Sleep 1 second real time to allow fast replay but low CPU
            await asyncio.sleep(1)

    async def _write_snapshot_once(self, payload_override=None):
        """Build and write one manual trading snapshot."""
        m = _get_market_module()

        is_immediate = payload_override is not None

        if not is_immediate:
            # Guard: dm must be alive and warmed up
            dm = getattr(m, "_dm", None)
            if dm is None:
                self._snapshots_skipped += 1
                return None
            if getattr(dm, "spot_price", 0) <= 0 and not getattr(dm, "klines", []):
                self._snapshots_skipped += 1
                return None

            # Build payload (same as /api/manual-trading/current)
            try:
                from engine.state_engine import StateEngine
                market_state = StateEngine.build_market_state(dm)
                payload = m._build_manual_trading_payload(market_state)
            except Exception as exc:
                log.debug("ServerSnapshotWorker: payload build failed: %s", exc)
                self._last_error = str(exc)
                return None
        else:
            payload = payload_override

        if payload.get("status") not in ("ok", "OK"):
            if not is_immediate:
                self._snapshots_skipped += 1
            return None

        # Build the snapshot record
        manual_setup = payload.get("manual_setup") or {}
        source_fields = payload.get("source_fields") or {}
        ps_info = payload.get("price_source_info") or {}
        live_ctx = payload.get("live_level_context") or {}
        level_meta = payload.get("level_context_meta") or {}
        # Rounded ISO timestamp (to second) using market time for correct replay
        snapshot_ts = payload.get("timestamp")
        try:
            import datetime as dt
            from datetime import timezone
            if isinstance(snapshot_ts, (int, float)):
                ts_sec = snapshot_ts / 1000.0 if snapshot_ts > 1e11 else snapshot_ts
                now_iso = dt.datetime.fromtimestamp(ts_sec, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            elif isinstance(snapshot_ts, str):
                now_iso = snapshot_ts
            else:
                now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


        # Flatten source fields into the record
        from engine.version import CODE_VERSION, ENGINE_PATCH_VERSION

        record: Dict[str, Any] = {
            "ts":                       now_iso,
            "frontend_session_id":      self._session_id,
            "source":                   "server",
            "code_version":             CODE_VERSION,
            "engine_patch_version":     ENGINE_PATCH_VERSION,
            # Core manual status fields from manual_setup
            "manual_status":            manual_setup.get("manual_status"),
            "setup_type":               manual_setup.get("manual_setup_type"),
            "manual_bias":              manual_setup.get("manual_bias"),
            "setup_quality":            manual_setup.get("setup_quality"),
            "actionability":            manual_setup.get("actionability"),
            # Context
            "current_state":            source_fields.get("current_state"),
            "execution_timing_state":   source_fields.get("execution_timing_state"),
            "short_term_flow_direction": source_fields.get("short_term_flow_direction"),
            "event_type":               source_fields.get("event_type"),
            "level_result":             source_fields.get("level_result"),
            "nearest_level":            source_fields.get("nearest_level"),
            "invalidation_level":       manual_setup.get("invalidation_level"),
            "manual_reason":            manual_setup.get("manual_reason"),
            "price":                    source_fields.get("price"),
            # Price source integrity
            "reference_price":          manual_setup.get("reference_price"),
            "execution_price":          manual_setup.get("execution_price"),
            "basis":                    manual_setup.get("basis"),
            "basis_pct":                manual_setup.get("basis_pct"),
            "execution_venue":          manual_setup.get("execution_venue"),
            "execution_symbol":         manual_setup.get("execution_symbol"),
            "reference_venue":          manual_setup.get("reference_venue"),
            "reference_symbol":         manual_setup.get("reference_symbol"),
            "ohlcv_source":             manual_setup.get("ohlcv_source"),
            "ohlcv_exchange":           manual_setup.get("ohlcv_exchange"),
            "ohlcv_market_type":        manual_setup.get("ohlcv_market_type"),
            "ohlcv_symbol":             manual_setup.get("ohlcv_symbol"),
            "ohlcv_timeframe":          manual_setup.get("ohlcv_timeframe"),
            "candle_source_verified":   manual_setup.get("candle_source_verified"),
            # OHLCV sync check
            "latest_execution_ohlcv_close":     manual_setup.get("latest_execution_ohlcv_close"),
            "execution_price_ohlcv_diff":       manual_setup.get("execution_price_ohlcv_diff"),
            "execution_price_ohlcv_diff_pct":   manual_setup.get("execution_price_ohlcv_diff_pct"),
            "execution_price_ohlcv_sync_status": manual_setup.get("execution_price_ohlcv_sync_status"),
            "price_source_for_entry":            manual_setup.get("price_source_for_entry"),
            "price_source_for_levels":           manual_setup.get("price_source_for_levels"),
            "price_source_for_outcome":          manual_setup.get("price_source_for_outcome"),
            # Reference sanity diagnostics
            "reference_price_status":            manual_setup.get("reference_price_status"),
            "basis_valid":                       manual_setup.get("basis_valid"),
            "reference_price_source":            manual_setup.get("reference_price_source"),
            "reference_candidate_count":         manual_setup.get("reference_candidate_count"),
            # Execution SL/TP
            "entry_execution_price":             manual_setup.get("entry_execution_price"),
            "protective_stop_execution_price":   manual_setup.get("protective_stop_execution_price"),
            "stop_execution_price":              manual_setup.get("stop_execution_price"),
            "tp1_execution_price":               manual_setup.get("tp1_execution_price"),
            "tp2_execution_price":               manual_setup.get("tp2_execution_price"),
            "tp3_execution_price":               manual_setup.get("tp3_execution_price"),
            "mfe_r_execution":                   manual_setup.get("mfe_r_execution"),
            "mae_r_execution":                   manual_setup.get("mae_r_execution"),
            "execution_risk_abs":                manual_setup.get("execution_risk_abs"),
            "execution_risk_pct":                manual_setup.get("execution_risk_pct"),
            "protective_stop_source":            manual_setup.get("protective_stop_source"),
            "tp_source":                         manual_setup.get("tp_source"),
            # Main context diagnostics
            "main_context_conflict_active":         manual_setup.get("main_context_conflict_active"),
            "main_context_conflict_reason":         manual_setup.get("main_context_conflict_reason"),
            "latest_main_context_direction":        manual_setup.get("latest_main_context_direction"),
            "latest_main_context_reaction_label":   manual_setup.get("latest_main_context_reaction_label"),
            "latest_main_context_level_price":      manual_setup.get("latest_main_context_level_price"),
            "latest_main_context_ts":               manual_setup.get("latest_main_context_ts"),
            "latest_main_context_age_sec":          manual_setup.get("latest_main_context_age_sec"),
            "checked_main_context_count":           manual_setup.get("checked_main_context_count"),
            "latest_main_context_source":           manual_setup.get("latest_main_context_source"),
            "latest_main_context_confidence":       manual_setup.get("latest_main_context_confidence"),
            "latest_main_context_deribit_status":   manual_setup.get("latest_main_context_deribit_status"),
            "latest_main_context_level_side":       manual_setup.get("latest_main_context_level_side"),
            # Candidate
            "candidate_key":                manual_setup.get("candidate_key"),
            "candidate_is_new":             manual_setup.get("candidate_is_new"),
            "candidate_cooldown_active":    manual_setup.get("candidate_cooldown_active"),
            "candidate_first_seen_ts":      manual_setup.get("candidate_first_seen_ts"),
            "candidate_age_sec":            manual_setup.get("candidate_age_sec"),
            # Setup details
            "selected_setup_level":                 manual_setup.get("selected_setup_level"),
            "selected_setup_side":                  manual_setup.get("selected_setup_side"),
            "selected_setup_level_source":          manual_setup.get("selected_setup_level_source"),
            "selected_setup_level_distance_pct":    manual_setup.get("selected_setup_level_distance_pct"),
            "selected_setup_level_result":          manual_setup.get("selected_setup_level_result"),
            "selected_setup_level_type":            manual_setup.get("selected_setup_level_type"),
            "setup_side_required":                  manual_setup.get("setup_side_required"),
            "falling_knife_guard":                  manual_setup.get("falling_knife_guard"),
            "decision_blocker":                     manual_setup.get("decision_blocker"),
            "price_confirmation_status":            manual_setup.get("price_confirmation_status"),
            "latest_close":                         manual_setup.get("latest_close"),
            # Live Level Context (Canonical Server Side)
            "live_level_result":            live_ctx.get("live_level_result"),
            "live_nearest_level":           live_ctx.get("live_nearest_level"),
            "live_level_side":              live_ctx.get("live_level_side"),
            "live_level_type":              live_ctx.get("live_level_type"),
            "live_distance_pct":            live_ctx.get("live_distance_pct"),
            "live_context_used":            live_ctx.get("live_context_used"),
            "live_context_source":          live_ctx.get("live_context_source"),
            "live_context_ts":              live_ctx.get("live_context_ts"),
            "live_source_event_id":         live_ctx.get("live_source_event_id"),
            "live_source_snapshot_id":      live_ctx.get("live_source_snapshot_id"),
            "live_source_snapshot_sequence_id": live_ctx.get("live_source_snapshot_sequence_id"),
            
            "live_support_level":           live_ctx.get("live_support_level"),
            "live_support_distance_pct":    live_ctx.get("live_support_distance_pct"),
            "live_support_source":          live_ctx.get("live_support_source"),
            "live_support_result":          live_ctx.get("live_support_result"),
            "live_support_type":            live_ctx.get("live_support_type"),
            
            "live_resistance_level":        live_ctx.get("live_resistance_level"),
            "live_resistance_distance_pct": live_ctx.get("live_resistance_distance_pct"),
            "live_resistance_source":       live_ctx.get("live_resistance_source"),
            "live_resistance_result":       live_ctx.get("live_resistance_result"),
            "live_resistance_type":         live_ctx.get("live_resistance_type"),
            
            "primary_live_level":           live_ctx.get("primary_live_level"),
            "primary_live_side":            live_ctx.get("primary_live_side"),
            "primary_live_distance_pct":    live_ctx.get("primary_live_distance_pct"),
            "primary_live_source":          live_ctx.get("primary_live_source"),
            "primary_live_result":          live_ctx.get("primary_live_result"),
            # Level Context Meta (Replay/Old Contexts)
            "level_context_ttl_sec":        level_meta.get("ttl_sec"),
            "level_context_age_sec":        level_meta.get("age_sec"),
            "level_context_stale":          level_meta.get("is_stale"),
            "level_context_used":           level_meta.get("is_used"),
            "source_level_reaction_id":     level_meta.get("reaction_id"),
            "source_level_reaction_ts":     level_meta.get("reaction_ts"),
            "raw_level_result":             level_meta.get("raw_level_result"),
            "raw_nearest_level":            level_meta.get("raw_nearest_level"),
            "raw_level_side":               level_meta.get("raw_level_side"),
            # NEW: Diagnostics Fields
            "entry_block_reason":                   manual_setup.get("entry_block_reason"),
            "entry_block_stage":                    manual_setup.get("entry_block_stage"),
            "entry_missing_conditions_json":        manual_setup.get("entry_missing_conditions_json"),
            "entry_quality_score":                  manual_setup.get("entry_quality_score"),
            "entry_quality_components_json":        manual_setup.get("entry_quality_components_json"),
            "entry_required_score":                 manual_setup.get("entry_required_score"),
            "entry_setup_side":                     manual_setup.get("entry_setup_side"),
            "entry_candidate_side":                 manual_setup.get("entry_candidate_side"),
            "entry_distance_to_level_pct":          manual_setup.get("entry_distance_to_level_pct"),
            "entry_distance_to_trigger_pct":        manual_setup.get("entry_distance_to_trigger_pct"),
            "entry_distance_to_invalidation_pct":   manual_setup.get("entry_distance_to_invalidation_pct"),
            "entry_main_context_direction":         manual_setup.get("entry_main_context_direction"),
            "entry_main_context_alignment":         manual_setup.get("entry_main_context_alignment"),
            "entry_main_context_confidence":        manual_setup.get("entry_main_context_confidence"),
            "entry_flow_alignment":                 manual_setup.get("entry_flow_alignment"),
            "entry_candle_confirmation":            manual_setup.get("entry_candle_confirmation"),
            "entry_level_confirmation":             manual_setup.get("entry_level_confirmation"),
            "entry_cooldown_active":                manual_setup.get("entry_cooldown_active"),
            "entry_same_zone_guard_active":         manual_setup.get("entry_same_zone_guard_active"),
            "entry_previous_candidate_guard_active":manual_setup.get("entry_previous_candidate_guard_active"),
            "entry_guard_reasons_json":             manual_setup.get("entry_guard_reasons_json"),
            "entry_debug_json":                     manual_setup.get("entry_debug_json"),
        }

        # Runtime self-check
        if record.get("manual_status") == "WATCH" and record.get("entry_block_reason") is None:
            import json
            log.warning(
                "ENTRY_DIAGNOSTICS_MISSING_FOR_WATCH: "
                "setup_type=%s, actionability=%s, level_result=%s, selected_setup_level=%s, latest_main_context_direction=%s",
                record.get("setup_type"),
                record.get("actionability"),
                record.get("level_result"),
                record.get("selected_setup_level"),
                record.get("latest_main_context_direction")
            )


        # Write via ManualLogger
        try:
            logger = m.get_manual_logger()
            result = await asyncio.to_thread(logger.log_snapshot, record)
            
            # Fetch the actual open result if PaperTradeEngine was triggered
            paper_open_result = None
            if result.get("status") == "ok":
                try:
                    import sqlite3
                    conn = sqlite3.connect(logger.db_path)
                    cursor = conn.cursor()
                    cursor.execute("SELECT paper_trade_open_result FROM manual_trading_snapshots WHERE ts = ? AND frontend_session_id = ?", (now_iso, self._session_id))
                    r = cursor.fetchone()
                    if r:
                        paper_open_result = r[0]
                    conn.close()
                except Exception:
                    pass

            if is_immediate:
                self.last_immediate_entry_candidate_key = record.get("candidate_key")
                self.immediate_entry_paper_open_result = paper_open_result

            if result.get("status") == "ok":
                if is_immediate:
                    self.immediate_entry_snapshot_written += 1
                else:
                    self._snapshots_written += 1
                    self._last_snapshot_ts = time.time()
                import os
                log.info(
                    "SERVER_SNAPSHOT_WORKER_SNAPSHOT_WRITTEN: db_path=%s cwd=%s latest_snapshot_ts=%s manual_snapshot_count=%d ts=%s exec_price=%s sync=%s immediate=%s paper_open=%s",
                    logger.db_path, os.getcwd(), self._last_snapshot_ts, self._snapshots_written,
                    now_iso, record.get("execution_price"), record.get("execution_price_ohlcv_sync_status"), is_immediate, paper_open_result
                )
                return {"status": "ok", "paper_open_result": paper_open_result}
            elif result.get("status") == "duplicate":
                if is_immediate:
                    self.immediate_entry_snapshot_skipped_duplicate += 1
                    log.info("Immediate entry snapshot skipped as duplicate")
                else:
                    self._snapshots_skipped += 1
                return {"status": "duplicate"}
            elif result.get("status") == "rejected_stale":
                log.warning("ServerSnapshotWorker: snapshot rejected (stale code_version)")
                return {"status": "rejected_stale"}
            else:
                if not is_immediate:
                    self._snapshots_skipped += 1
                return {"status": "error"}
        except Exception as exc:
            log.warning("ServerSnapshotWorker: log_snapshot failed: %s", exc)
            raise

    def get_status(self) -> Dict[str, Any]:
        return {
            "worker":             "ServerSnapshotWorker",
            "interval_sec":       self.interval_sec,
            "session_id":         self._session_id,
            "snapshots_written":  self._snapshots_written,
            "snapshots_skipped":  self._snapshots_skipped,
            "last_snapshot_ts":   self._last_snapshot_ts,
            "consecutive_errors": self._consecutive_errors,
            "last_error":         self._last_error,
            "immediate_entry_snapshot_written": self.immediate_entry_snapshot_written,
            "immediate_entry_snapshot_skipped_duplicate": self.immediate_entry_snapshot_skipped_duplicate,
            "last_immediate_entry_candidate_key": self.last_immediate_entry_candidate_key,
            "immediate_entry_paper_open_result": self.immediate_entry_paper_open_result,
        }
