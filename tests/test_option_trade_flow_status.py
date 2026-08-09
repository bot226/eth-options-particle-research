import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from backend.routes import research
from backend.workers.option_trade_flow_worker import OptionTradeFlowStore


def _status_rows(dropped_bybit=0, dropped_deribit=0):
    now = time.time()
    return {
        exchange: {
            "connection_state": "subscribed",
            "connection_count": 1,
            "reconnect_count": 0,
            "message_count": 1,
            "normalized_trade_count": 1,
            "queued_trade_count": 1,
            "dropped_trade_count": (
                dropped_bybit if exchange == "bybit" else dropped_deribit
            ),
            "last_message_utc": now,
            "last_trade_utc": now,
            "last_error": "",
        }
        for exchange in ("bybit", "deribit")
    }


class OptionTradeFlowStatusTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "option_trade_flow.db"
        self.store = OptionTradeFlowStore(self.path)
        self.store.initialize()
        self.original_path = research.OPTION_FLOW_DB_PATH
        research.OPTION_FLOW_DB_PATH = str(self.path)
        self.addCleanup(self._restore_path)

    def _restore_path(self):
        research.OPTION_FLOW_DB_PATH = self.original_path

    def test_reports_ok_only_with_both_current_and_historical_collectors(self):
        self.store.write_status(_status_rows(), "session-1", time.time() - 5)

        result = research.option_trade_flow_status()

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["quality_history_available"])
        self.assertEqual(result["historical_dropped_trades"], 0)
        self.assertEqual(
            {row["exchange"] for row in result["quality_window"]},
            {"bybit", "deribit"},
        )

    def test_missing_exchange_history_is_degraded(self):
        self.store.write_status(_status_rows(), "session-1", time.time() - 5)
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                "DELETE FROM collector_status_history WHERE exchange='deribit'"
            )
            connection.commit()
        finally:
            connection.close()

        result = research.option_trade_flow_status()

        self.assertEqual(result["status"], "degraded")
        self.assertFalse(result["quality_history_available"])

    def test_any_session_queue_drop_remains_visible(self):
        self.store.write_status(
            _status_rows(dropped_bybit=2), "damaged-session", time.time() - 5
        )
        self.store.write_status(_status_rows(), "clean-session", time.time())

        result = research.option_trade_flow_status()

        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["dropped_trades"], 0)
        self.assertEqual(result["historical_dropped_trades"], 2)


if __name__ == "__main__":
    unittest.main()
