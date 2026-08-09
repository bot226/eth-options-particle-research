import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.workers.option_trade_flow_worker import (
    OptionTradeFlowStore,
    normalize_bybit_trade,
    normalize_deribit_trade,
)


class OptionTradeNormalizationTest(unittest.TestCase):
    def test_normalizes_bybit_public_option_trade(self):
        trade = normalize_bybit_trade(
            {
                "T": 1786300000123,
                "s": "BTC-25SEP26-70000-C-USDT",
                "S": "Buy",
                "v": "1.5",
                "p": "0.025",
                "i": "bybit-trade-1",
                "seq": 12345,
                "BT": False,
                "RPI": False,
                "mP": "0.024",
                "iP": "70010.5",
                "mIv": "0.51",
                "iv": "0.53",
            },
            received_at=1786300001.0,
        )

        self.assertIsNotNone(trade)
        self.assertEqual(trade["exchange"], "bybit")
        self.assertEqual(trade["contract_id"], "BTC-20260925-70000-C")
        self.assertEqual(trade["option_type"], "C")
        self.assertEqual(trade["taker_side"], "BUY")
        self.assertEqual(trade["contracts"], 1.5)
        self.assertEqual(trade["trade_iv_decimal"], 0.53)
        self.assertEqual(json.loads(trade["raw_json"])["i"], "bybit-trade-1")

    def test_normalizes_deribit_public_option_trade(self):
        trade = normalize_deribit_trade(
            {
                "trade_seq": 467,
                "trade_id": "415305279",
                "timestamp": 1786300000456,
                "tick_direction": 2,
                "price": 0.0525,
                "mark_price": 0.05253883,
                "iv": 45.91,
                "instrument_name": "BTC-25SEP26-65000-P",
                "index_price": 66930.31,
                "direction": "sell",
                "amount": 3,
                "contracts": 3,
                "block_trade_id": "154",
            },
            received_at=1786300001.0,
        )

        self.assertIsNotNone(trade)
        self.assertEqual(trade["exchange"], "deribit")
        self.assertEqual(trade["contract_id"], "BTC-20260925-65000-P")
        self.assertEqual(trade["option_type"], "P")
        self.assertEqual(trade["taker_side"], "SELL")
        self.assertAlmostEqual(trade["trade_iv_decimal"], 0.4591)
        self.assertEqual(trade["is_block_trade"], 1)

    def test_rejects_incomplete_trade(self):
        self.assertIsNone(normalize_bybit_trade({"s": "BTC-25SEP26-70000-C"}))
        self.assertIsNone(normalize_deribit_trade({"trade_id": "1"}))


class OptionTradeFlowStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "option_trade_flow.db"
        self.store = OptionTradeFlowStore(self.path)
        self.store.initialize()

    def test_inserts_deduplicates_and_tracks_status(self):
        trade = normalize_deribit_trade(
            {
                "trade_seq": 1,
                "trade_id": "duplicate-id",
                "timestamp": 1786300000456,
                "tick_direction": 0,
                "price": 0.01,
                "mark_price": 0.011,
                "iv": 50,
                "instrument_name": "BTC-25SEP26-70000-C",
                "index_price": 70000,
                "direction": "buy",
                "amount": 2,
            }
        )
        self.assertEqual(self.store.insert_trades([trade, trade]), 1)
        self.assertEqual(self.store.insert_trades([trade]), 0)

        status = {
            exchange: {
                "connection_state": "subscribed",
                "connection_count": 1,
                "reconnect_count": 0,
                "message_count": 1,
                "normalized_trade_count": 1,
                "queued_trade_count": 1,
                "dropped_trade_count": 0,
                "last_message_utc": 1786300001.0,
                "last_trade_utc": 1786300000.456,
                "last_error": "",
            }
            for exchange in ("bybit", "deribit")
        }
        self.store.write_status(status, "session-test", 1786299900.0)

        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM option_trades").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM collector_status").fetchone()[0], 2)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM collector_status_history").fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(DISTINCT session_id) FROM collector_status_history"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0],
                "1.1",
            )
        finally:
            connection.close()

    def test_migrates_v1_current_status_without_clean_database(self):
        legacy_path = Path(self.temp_dir.name) / "legacy_option_trade_flow.db"
        connection = sqlite3.connect(legacy_path)
        try:
            connection.execute(
                """
                CREATE TABLE collector_status (
                    exchange TEXT PRIMARY KEY,
                    connection_state TEXT NOT NULL,
                    connection_count INTEGER NOT NULL,
                    reconnect_count INTEGER NOT NULL,
                    message_count INTEGER NOT NULL,
                    normalized_trade_count INTEGER NOT NULL,
                    queued_trade_count INTEGER NOT NULL,
                    dropped_trade_count INTEGER NOT NULL,
                    last_message_utc REAL,
                    last_trade_utc REAL,
                    last_error TEXT NOT NULL,
                    updated_at_utc REAL NOT NULL
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

        OptionTradeFlowStore(legacy_path).initialize()

        connection = sqlite3.connect(legacy_path)
        try:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(collector_status)")
            }
            self.assertIn("session_id", columns)
            self.assertIn("process_started_at_utc", columns)
            self.assertEqual(
                connection.execute(
                    "SELECT value FROM metadata WHERE key='schema_version'"
                ).fetchone()[0],
                "1.1",
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type='table' AND name='collector_status_history'"
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
