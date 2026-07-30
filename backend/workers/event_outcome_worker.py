"""Event outcome worker for MOS Research Layer.

The worker derives replay-only event outcomes and level reactions from
persisted events, snapshots, and OHLCV. It never feeds live MOS scoring,
event generation, execution timing, or trading logic.
"""

import asyncio
import logging
import sqlite3
import time
from typing import Any, Dict

from engine.research_logger import DB_PATH, get_db_connection
from engine.replay_outcome_engine import ReplayOutcomeEngine

log = logging.getLogger(__name__)

EVENT_OUTCOME_WORKER_ENABLED = True
EVENT_OUTCOME_WORKER_INTERVAL_SEC = 60
EVENT_OUTCOME_WORKER_BATCH_LIMIT = 500

_LAST_STATUS: Dict[str, Any] = {
    "enabled": EVENT_OUTCOME_WORKER_ENABLED,
    "running": False,
    "last_run_ts": 0.0,
    "last_result": {},
    "last_error": "",
}


def get_event_outcome_worker_status() -> Dict[str, Any]:
    return dict(_LAST_STATUS)


class EventOutcomeWorker:
    """Async background worker for replay-only event outcome backfill."""

    def __init__(
        self,
        db_path: str = DB_PATH,
        interval_sec: int = EVENT_OUTCOME_WORKER_INTERVAL_SEC,
        batch_limit: int = EVENT_OUTCOME_WORKER_BATCH_LIMIT,
    ):
        self.db_path = db_path
        self.interval_sec = interval_sec
        self.batch_limit = batch_limit
        self.enabled = EVENT_OUTCOME_WORKER_ENABLED
        self._task: asyncio.Task | None = None

    async def start(self):
        if not self.enabled:
            log.info("Event outcome worker disabled")
            return
        conn = get_db_connection(self.db_path)
        try:
            ReplayOutcomeEngine.ensure_schema(conn)
        finally:
            conn.close()
        _LAST_STATUS.update({
            "enabled": self.enabled,
            "running": True,
            "db_path": self.db_path,
            "interval_sec": self.interval_sec,
            "batch_limit": self.batch_limit,
            "last_error": "",
        })
        self._task = asyncio.create_task(self._loop())
        log.info(
            "Event outcome worker started: interval=%ss batch_limit=%s",
            self.interval_sec,
            self.batch_limit,
        )

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        _LAST_STATUS["running"] = False
        log.info("Event outcome worker stopped")

    async def _loop(self):
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _LAST_STATUS["last_error"] = str(exc)
                log.warning("Event outcome worker iteration failed: %s", exc)
            await asyncio.sleep(self.interval_sec)

    async def run_once(self) -> Dict[str, Any]:
        result = await asyncio.to_thread(self._run_once_sync)
        _LAST_STATUS.update({
            "last_run_ts": time.time(),
            "last_result": result,
            "last_error": "",
        })
        if result.get("inserted") or result.get("level_reactions_inserted") or result.get("skipped"):
            log.info("Event outcome worker result: %s", result)
        return result

    def _run_once_sync(self) -> Dict[str, Any]:
        conn = get_db_connection(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            result = ReplayOutcomeEngine.backfill_event_outcomes(conn, limit=self.batch_limit)
            fallback_result = ReplayOutcomeEngine.backfill_fallback_reactions(conn, limit=self.batch_limit)
            result["fallback_reactions_inserted"] = fallback_result.get("inserted", 0)
            result["fallback_reactions_skipped"] = fallback_result.get("skipped", 0)
            diagnostics = ReplayOutcomeEngine.worker_diagnostics(conn)
            result["diagnostics"] = diagnostics
            return result
        finally:
            conn.close()
