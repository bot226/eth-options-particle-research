import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from backend.research_interval.auditor import (
    _accounting,
    _collector_quality,
    _event_profile,
    _manifest_file_checks,
    _sha256,
    open_archive,
)
from backend.scripts.option_flow_research import FuturesCandle


class EthIntervalAuditorTest(unittest.TestCase):
    def test_archive_reader_never_modifies_source_zip(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "eth.zip"
            with zipfile.ZipFile(archive_path, "w") as package:
                package.writestr("manifest.json", json.dumps({"asset": "ETH"}))
            before = _sha256(archive_path)
            with open_archive(archive_path) as (_, manifest, provenance):
                self.assertEqual(manifest["asset"], "ETH")
                self.assertEqual(provenance["source_sha256"], before)
            self.assertEqual(_sha256(archive_path), before)

    def test_manifest_hash_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "history.db").write_bytes(b"changed")
            result = _manifest_file_checks(
                root,
                {"files": {"history.db": {"sha256": "00", "bytes": 7}}},
            )
            self.assertIn("manifest_hash_or_size_mismatch:history.db", result["errors"])

    def test_overlap_accounting_removes_repeated_events(self):
        result = _accounting([100.0, 200.0, 300.0], [50.0, 100.0, 200.0])
        self.assertEqual(result["overlap_events_removed"], 2)
        self.assertEqual(result["new"]["independent_events"], 1)
        self.assertEqual(result["cumulative"]["independent_events"], 4)

    def test_incomplete_horizons_are_pending_not_zero(self):
        candles = [
            FuturesCandle(float(timestamp), 101.0, 99.0, 100.0)
            for timestamp in range(0, 16 * 60, 60)
        ]
        profile = _event_profile(candles, [(0.0, 1)], {5, 15})
        self.assertEqual(profile["5"]["independent_complete_events"], 1)
        self.assertEqual(profile["30"]["independent_complete_events"], 0)
        self.assertEqual(profile["30"]["independent_pending_events"], 1)
        self.assertIsNone(profile["30"]["mean_future_range_pct"])
        self.assertEqual(profile["30"]["role"], "diagnostic_only_not_selection")

    def test_non_monotonic_observer_health_clock_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = sqlite3.connect(root / "option_trade_flow.db")
            db.execute(
                "CREATE TABLE collector_status_history(exchange TEXT,session_id TEXT,connection_state TEXT,dropped_trade_count INTEGER,updated_at_utc REAL)"
            )
            for exchange in ("bybit", "deribit"):
                db.execute("INSERT INTO collector_status_history VALUES(?, 's', 'subscribed', 0, 1)", (exchange,))
                db.execute("INSERT INTO collector_status_history VALUES(?, 's', 'subscribed', 0, 1)", (exchange,))
            db.commit()
            db.close()
            result = _collector_quality(root)
            self.assertEqual(result["status"], "fail")
            self.assertIn("non_monotonic_collector_clock", result["errors"])

    def test_collector_gap_is_explicitly_degraded_and_excluded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = sqlite3.connect(root / "option_trade_flow.db")
            db.execute(
                "CREATE TABLE collector_status_history(exchange TEXT,session_id TEXT,connection_state TEXT,dropped_trade_count INTEGER,updated_at_utc REAL)"
            )
            for exchange in ("bybit", "deribit"):
                db.execute("INSERT INTO collector_status_history VALUES(?, 's', 'subscribed', 0, 1)", (exchange,))
                db.execute("INSERT INTO collector_status_history VALUES(?, 's', 'subscribed', 0, 31)", (exchange,))
            db.commit()
            db.close()
            result = _collector_quality(root)
            self.assertEqual(result["status"], "degraded")
            self.assertEqual(len(result["excluded_outage_or_gap_intervals"]), 2)


if __name__ == "__main__":
    unittest.main()
