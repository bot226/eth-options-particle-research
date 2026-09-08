import hashlib
import sqlite3
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from backend.research_interval import exporter
from backend.research_interval.exporter import IntervalExportError, export_interval


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class EthResearchIntervalExporterTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = self.root / "data"
        self.output = self.root / "output"
        self.data.mkdir()
        now_end = int(time.time() // 60) * 60 - 120
        self.start = float(now_end - 3600)
        self.end = float(now_end)
        self._research()
        self._history()
        self._flow()
        manual = sqlite3.connect(self.data / "mos_manual.db")
        manual.execute("CREATE TABLE manual_trading_snapshots(id INTEGER PRIMARY KEY,ts REAL)")
        manual.commit()
        manual.close()

    def _research(self):
        db = sqlite3.connect(self.data / "mos_research.db")
        db.execute(
            "CREATE TABLE ohlcv_candles(exchange TEXT,symbol TEXT,timeframe TEXT,timestamp_utc REAL,high REAL,low REAL,close REAL,candle_source_verified INTEGER,created_at_utc REAL)"
        )
        db.execute("CREATE TABLE snapshots(snapshot_id TEXT PRIMARY KEY,timestamp_utc REAL)")
        db.execute(
            "CREATE TABLE debug_snapshots(id INTEGER PRIMARY KEY,snapshot_id TEXT,timestamp_utc TEXT)"
        )
        support = self.start - 168 * 3600
        for timestamp in range(int(support), int(self.end), 60):
            db.execute(
                "INSERT INTO ohlcv_candles VALUES('bybit','ETHUSDT','1m',?,101,99,100,1,?)",
                (timestamp, timestamp + 60),
            )
        for timestamp in (support, self.start, self.end - 60):
            db.execute("INSERT INTO snapshots VALUES(?,?)", (str(timestamp), timestamp))
        db.commit()
        db.close()

    def test_parent_closure_keeps_snapshot_referenced_by_windowed_child(self):
        parent_id = "snapshot-before-support"
        db = sqlite3.connect(self.data / "mos_research.db")
        db.execute("INSERT INTO snapshots VALUES(?,?)", (parent_id, self.start - 1))
        db.execute(
            "INSERT INTO debug_snapshots(snapshot_id,timestamp_utc) VALUES(?,?)",
            (parent_id, exporter.iso_utc(self.start + 60)),
        )
        db.commit()
        db.close()

        archive, manifest = export_interval(
            self.start,
            end=self.end,
            data_dir=self.data,
            output_dir=self.output,
            project_root=Path(__file__).resolve().parents[1],
        )
        self.assertEqual(manifest["quality_summary"]["relational_orphans"], {})
        extracted = self.root / "parent_closure"
        with zipfile.ZipFile(archive) as package:
            package.extractall(extracted)
        db = sqlite3.connect(extracted / "mos_research.db")
        try:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM snapshots WHERE snapshot_id=?", (parent_id,)).fetchone()[0],
                1,
            )
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM debug_snapshots WHERE snapshot_id=?", (parent_id,)).fetchone()[0],
                1,
            )
        finally:
            db.close()

    def _add_reaction_schema(self):
        db = sqlite3.connect(self.data / "mos_research.db")
        db.execute("CREATE TABLE events(id INTEGER PRIMARY KEY,snapshot_id TEXT,timestamp_utc REAL)")
        db.execute(
            "CREATE TABLE event_level_reactions("
            "id INTEGER PRIMARY KEY,event_id TEXT,outcome_id INTEGER,snapshot_id TEXT,"
            "event_type TEXT,event_timestamp_utc TEXT,source TEXT,is_synthetic INTEGER)"
        )
        db.commit()
        db.close()

    def test_valid_ohlcv_fallback_has_snapshot_lineage_without_event_parent(self):
        self._add_reaction_schema()
        snapshot_id = "synthetic-source-snapshot"
        db = sqlite3.connect(self.data / "mos_research.db")
        db.execute("INSERT INTO snapshots VALUES(?,?)", (snapshot_id, self.start - 1))
        db.execute(
            "INSERT INTO event_level_reactions VALUES(1,?,?,?,?,?,?,?)",
            (
                f"fallback_{snapshot_id}",
                None,
                snapshot_id,
                "OHLCV_PRICE_ACTION",
                exporter.iso_utc(self.start + 60),
                "ohlcv_only",
                1,
            ),
        )
        db.commit()
        db.close()

        archive, manifest = export_interval(
            self.start,
            end=self.end,
            data_dir=self.data,
            output_dir=self.output,
            project_root=Path(__file__).resolve().parents[1],
        )
        self.assertEqual(manifest["quality_summary"]["relational_orphans"], {})
        extracted = self.root / "valid_fallback"
        with zipfile.ZipFile(archive) as package:
            package.extractall(extracted)
        db = sqlite3.connect(extracted / "mos_research.db")
        try:
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM event_level_reactions WHERE event_id=?",
                    (f"fallback_{snapshot_id}",),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM snapshots WHERE snapshot_id=?", (snapshot_id,)
                ).fetchone()[0],
                1,
            )
        finally:
            db.close()

    def test_malformed_fallback_identifier_still_fails_closed(self):
        self._add_reaction_schema()
        snapshot_id = "synthetic-source-snapshot"
        db = sqlite3.connect(self.data / "mos_research.db")
        db.execute("INSERT INTO snapshots VALUES(?,?)", (snapshot_id, self.start))
        db.execute(
            "INSERT INTO event_level_reactions VALUES(1,?,?,?,?,?,?,?)",
            (
                "fallback_wrong-snapshot",
                None,
                snapshot_id,
                "OHLCV_PRICE_ACTION",
                exporter.iso_utc(self.start + 60),
                "ohlcv_only",
                1,
            ),
        )
        db.commit()
        db.close()

        with self.assertRaisesRegex(
            IntervalExportError, "archive_quality_gate_failed:relational_orphans_detected"
        ):
            export_interval(
                self.start,
                end=self.end,
                data_dir=self.data,
                output_dir=self.output,
                project_root=Path(__file__).resolve().parents[1],
            )

    def test_fallback_without_source_snapshot_still_fails_closed(self):
        self._add_reaction_schema()
        snapshot_id = "missing-source-snapshot"
        db = sqlite3.connect(self.data / "mos_research.db")
        db.execute(
            "INSERT INTO event_level_reactions VALUES(1,?,?,?,?,?,?,?)",
            (
                f"fallback_{snapshot_id}",
                None,
                snapshot_id,
                "OHLCV_PRICE_ACTION",
                exporter.iso_utc(self.start + 60),
                "ohlcv_only",
                1,
            ),
        )
        db.commit()
        db.close()

        with self.assertRaisesRegex(
            IntervalExportError, "archive_quality_gate_failed:relational_orphans_detected"
        ):
            export_interval(
                self.start,
                end=self.end,
                data_dir=self.data,
                output_dir=self.output,
                project_root=Path(__file__).resolve().parents[1],
            )

    def _history(self):
        db = sqlite3.connect(self.data / "history.db")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("CREATE TABLE snapshots(id INTEGER PRIMARY KEY,ts REAL)")
        db.execute(
            "CREATE TABLE option_contract_snapshots(snapshot_id INTEGER,ts REAL,exchange TEXT,contract_id TEXT,delta REAL,gamma REAL,vega REAL,theta REAL,mark_iv REAL,underlying_price REAL,FOREIGN KEY(snapshot_id) REFERENCES snapshots(id))"
        )
        db.execute("INSERT INTO snapshots VALUES(1,?)", (self.start,))
        db.execute(
            "INSERT INTO option_contract_snapshots VALUES(1,?,'bybit','ETH-30SEP26-3000-C',.5,.1,.2,-.1,.5,3000)",
            (self.start,),
        )
        db.commit()
        db.close()

    def _flow(self):
        db = sqlite3.connect(self.data / "option_trade_flow.db")
        db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)")
        db.execute("INSERT INTO metadata VALUES('schema_version','1.1')")
        db.execute("CREATE TABLE option_trades(id INTEGER PRIMARY KEY,exchange TEXT,trade_id TEXT,trade_timestamp_utc REAL,contract_id TEXT,source_instrument TEXT)")
        db.execute(
            "INSERT INTO option_trades VALUES(1,'bybit','t1',?,'ETH-30SEP26-3000-C','ETH-30SEP26-3000-C')",
            (self.start,),
        )
        db.execute("CREATE TABLE collector_status(exchange TEXT,updated_at_utc REAL)")
        db.execute("CREATE TABLE collector_status_history(id INTEGER PRIMARY KEY,exchange TEXT,session_id TEXT,connection_state TEXT,dropped_trade_count INTEGER,updated_at_utc REAL)")
        db.execute("INSERT INTO collector_status VALUES('bybit',?)", (self.end - 1,))
        db.execute("INSERT INTO collector_status_history VALUES(1,'bybit','s','subscribed',0,?)", (self.end - 1,))
        db.commit()
        db.close()

    def test_half_open_export_preserves_sources_and_embeds_eth_protocols(self):
        before = {path.name: digest(path) for path in self.data.glob("*.db")}
        archive, manifest = export_interval(
            self.start,
            end=self.end,
            data_dir=self.data,
            output_dir=self.output,
            project_root=Path(__file__).resolve().parents[1],
        )
        self.assertTrue(archive.is_file())
        self.assertEqual(manifest["asset"], "ETH")
        self.assertEqual(manifest["futures_symbol"], "ETHUSDT")
        self.assertTrue(manifest["quality_summary"]["ETHUSDT_1m_coverage_complete"])
        self.assertEqual(before, {path.name: digest(path) for path in self.data.glob("*.db")})
        with zipfile.ZipFile(archive) as package:
            self.assertIsNone(package.testzip())
            self.assertIn("protocols/MOS_TREND_BEFORE_COMPRESSION_PREREG_V1.json", package.namelist())
            extracted = self.root / "extracted"
            package.extractall(extracted)
        db = sqlite3.connect(extracted / "mos_research.db")
        try:
            maximum = db.execute("SELECT MAX(timestamp_utc) FROM ohlcv_candles").fetchone()[0]
        finally:
            db.close()
        self.assertEqual(maximum, self.end - 60)

    def test_refuses_missing_required_database(self):
        (self.data / "mos_manual.db").unlink()
        with self.assertRaisesRegex(IntervalExportError, "mos_manual.db"):
            export_interval(self.start, end=self.end, data_dir=self.data, output_dir=self.output)

    def test_refuses_frozen_protocol_hash_mismatch(self):
        name = "MOS_OPTION_FLOW_PREREG_V1.json"
        with patch.dict(exporter.EXPECTED_PROTOCOL_SHA256, {name: "00"}):
            with self.assertRaisesRegex(IntervalExportError, "frozen_protocol_sha256_mismatch"):
                export_interval(self.start, end=self.end, data_dir=self.data, output_dir=self.output)

    def test_protocol_hash_is_independent_of_windows_line_endings(self):
        raw = b'{"protocol_id":"TEST"}\r\n'
        lf = b'{"protocol_id":"TEST"}\n'
        self.assertEqual(
            exporter._canonical_protocol_sha256(raw),
            exporter._canonical_protocol_sha256(lf),
        )
        self.assertNotEqual(hashlib.sha256(raw).hexdigest(), hashlib.sha256(lf).hexdigest())

    def test_protocol_manifest_retains_raw_and_canonical_digests(self):
        path = self.root / "protocol.json"
        path.write_bytes(b'{"protocol_id":"TEST"}\r\n')
        manifest = exporter._protocol_manifest([path])[path.name]
        self.assertEqual(manifest["sha256"], manifest["canonical_sha256"])
        self.assertNotEqual(manifest["raw_sha256"], manifest["canonical_sha256"])
        self.assertTrue(manifest["line_endings_normalized"])

    def test_refuses_mixed_asset_rows(self):
        db = sqlite3.connect(self.data / "option_trade_flow.db")
        db.execute(
            "INSERT INTO option_trades VALUES(2,'bybit','t2',?,'BTC-30SEP26-3000-C','BTC-30SEP26-3000-C')",
            (self.start + 1,),
        )
        db.commit()
        db.close()
        with self.assertRaisesRegex(IntervalExportError, "ETH_asset_identity_failed"):
            export_interval(self.start, end=self.end, data_dir=self.data, output_dir=self.output)

    def test_verified_but_still_forming_minute_is_never_exported(self):
        forming_open = float(int(time.time() // 60) * 60)
        db = sqlite3.connect(self.data / "mos_research.db")
        db.execute(
            "INSERT INTO ohlcv_candles VALUES('bybit','ETHUSDT','1m',?,101,99,100,1,?)",
            (forming_open, forming_open),
        )
        db.commit()
        db.close()
        archive, _ = export_interval(
            self.start,
            data_dir=self.data,
            output_dir=self.output,
            project_root=Path(__file__).resolve().parents[1],
        )
        extracted = self.root / "forming"
        with zipfile.ZipFile(archive) as package:
            package.extractall(extracted)
        copied = sqlite3.connect(extracted / "mos_research.db")
        try:
            self.assertEqual(
                copied.execute("SELECT COUNT(*) FROM ohlcv_candles WHERE timestamp_utc=?", (forming_open,)).fetchone()[0],
                0,
            )
        finally:
            copied.close()


if __name__ == "__main__":
    unittest.main()
