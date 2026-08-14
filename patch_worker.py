import os
import re

file_path = r"C:\Users\User\Desktop\Project\eth-gpt-codex-v11-targeted-fix\backend\workers\server_snapshot_worker.py"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Update _loop to poll faster and use market time
loop_pattern = r'    async def _loop\(self\):.*?async def _write_snapshot_once\(self\):'

new_loop = '''    async def _loop(self):
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

    async def _write_snapshot_once(self):'''

content = re.sub(loop_pattern, new_loop, content, flags=re.DOTALL)

# 2. Update ts to use payload timestamp
ts_pattern = r'\s+# Rounded ISO timestamp \(to second\) for deterministic dedup\n\s+now_iso = datetime\.now\(timezone\.utc\)\.strftime\("%Y-%m-%dT%H:%M:%SZ"\)'

new_ts = '''
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
'''
content = re.sub(ts_pattern, new_ts, content, count=1)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)

print("Patch applied to server_snapshot_worker.py")
