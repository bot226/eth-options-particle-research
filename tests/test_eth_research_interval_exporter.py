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
