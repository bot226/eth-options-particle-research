import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.engine.history_db import HistoryDB
from research.particle_shadow.common import parse_contract
from research.particle_shadow.extractor import ExtractionConfig
from research.particle_shadow.replay import ParticleReplayError, run_replay


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ParticleShadowReplayTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.dataset = self.root / "dataset"
        self.dataset.mkdir()
        self.history_db = self.dataset / "history.db"
        self.research_db = self.dataset / "mos_research.db"
        self._create_history_db()
        self._create_research_db()
        (self.dataset / "manifest.json").write_text(
            json.dumps({"dataset": {"label": "synthetic_test"}}),
            encoding="utf-8",
        )

    def _create_history_db(self):
        connection = sqlite3.connect(self.history_db)
        try:
            connection.execute(
                """
                CREATE TABLE snapshots (
                    id INTEGER PRIMARY KEY,
                    ts REAL NOT NULL,
                    oi_json TEXT,
                    pdf_json TEXT,
                    gex_json TEXT,
                    term_structure_json TEXT,
                    exchange_data_json TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE option_contract_snapshots (
                    snapshot_id INTEGER NOT NULL,
                    ts REAL NOT NULL,
                    exchange TEXT NOT NULL,
                    contract_id TEXT NOT NULL,
                    source_symbol TEXT,
                    expiry TEXT,
                    strike REAL,
                    option_type TEXT,
                    oi REAL,
                    volume_24h REAL,
                    mark_iv REAL,
                    bid_iv REAL,
                    ask_iv REAL,
                    delta REAL,
                    gamma REAL,
                    vega REAL,
                    theta REAL,
                    mark_price REAL,
                    underlying_price REAL,
                    exchange_sources_json TEXT
                )
                """
            )
            for index, timestamp in enumerate((1_000.0, 1_300.0, 1_600.0, 1_900.0), start=1):
                call_oi = 100.0 + index * 10.0
                put_oi = 80.0 + index * 2.0
                oi = {} if index == 4 else {
                    "BTC-20260814-100-C": call_oi,
                    "BTC-20260814-90-P": put_oi,
                }
                gex = {
                    "data": [
                        {
                            "strike": 100,
                            "call_gex": 20.0 + index,
                            "put_gex": -10.0 - index,
                            "net_gex": 10.0,
                        },
                        {
                            "strike": 90,
                            "call_gex": 5.0,
                            "put_gex": -20.0 - index,
                            "net_gex": -15.0 - index,
                        },
                    ],
                    "metrics": {
                        "total_net_gex": 50.0,
                        "gamma_wall_above": 100.3,
                        "gamma_wall_below": 90.0,
                        "gamma_flip": 96.0,
                    },
                }
                term = {
                    "data": [
                        {"expiry": "20260814", "dte": 13, "atm_iv": 0.30 + index * 0.001}
                    ],
                    "metrics": {"regime": "COMPRESSION"},
                }
                connection.execute(
                    "INSERT INTO snapshots VALUES (?, ?, ?, '{}', ?, ?, '{}')",
                    (index, timestamp, json.dumps(oi), json.dumps(gex), json.dumps(term)),
                )
                for contract_id, strike, option_type, contract_oi in (
                    ("BTC-20260814-100-C", 100.0, "C", call_oi),
                    ("BTC-20260814-90-P", 90.0, "P", put_oi),
                ):
                    connection.execute(
                        """
                        INSERT INTO option_contract_snapshots VALUES (
                            ?, ?, 'bybit', ?, ?, '20260814', ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, '["bybit"]'
                        )
                        """,
                        (
                            index, timestamp, contract_id, contract_id, strike,
                            option_type, contract_oi, 10.0 + index,
                            0.30 + index * 0.002, 0.29 + index * 0.002,
                            0.31 + index * 0.002,
                            (0.50 if option_type == "C" else -0.50) + index * 0.01,
                            0.01 + index * 0.001, 0.20 + index * 0.01,
                            -0.10 - index * 0.01, 0.02, 100.0,
                        ),
                    )
            connection.commit()
        finally:
            connection.close()

    def _create_research_db(self):
        connection = sqlite3.connect(self.research_db)
        try:
            connection.execute(
                """
                CREATE TABLE snapshots (
                    timestamp_utc REAL,
                    snapshot_id TEXT,
                    snapshot_sequence_id INTEGER,
                    spot_price REAL,
                    current_state TEXT,
                    gamma_regime TEXT,
                    oi_total REAL,
                    net_gex REAL,
                    atm_iv REAL,
                    iv_velocity REAL,
                    expansion_probability REAL,
                    synthetic_flow_pressure REAL,
                    execution_timing_state TEXT,
                    call_wall REAL,
                    put_wall REAL,
                    data_quality TEXT,
                    active_sources TEXT,
                    exclude_from_analysis INTEGER,
                    exclude_reason TEXT
                )
                """
            )
            for index, timestamp in enumerate((999.0, 1_299.0, 1_599.0, 1_899.0), start=1):
                connection.execute(
                    "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        timestamp,
                        f"snapshot-{index}",
                        index,
                        100.0,
                        "PINNING",
                        "POSITIVE_GAMMA",
                        200.0,
                        50.0,
                        30.0,
                        0.0,
                        20.0,
                        0.0,
                        "WAIT",
                        100.3,
                        90.0,
                        "GOOD",
                        '["bybit"]',
                        0,
                        "",
                    ),
                )
            connection.execute(
                """
                CREATE TABLE ohlcv_candles (
                    timestamp_utc REAL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    exchange TEXT,
                    symbol TEXT,
                    timeframe TEXT,
                    ohlcv_source TEXT,
                    candle_source_verified INTEGER
                )
                """
            )
            for timestamp in range(900, 2_000, 60):
                close = 100.0 + (timestamp - 900) / 10_000.0
                connection.execute(
                    "INSERT INTO ohlcv_candles VALUES (?, ?, ?, ?, ?, 'bybit', 'BTCUSDT', '1m', 'test', 1)",
                    (timestamp, close, close + 0.1, close - 0.1, close),
                )
            connection.commit()
        finally:
            connection.close()

    def test_parses_bybit_and_deribit_contract_names(self):
        bybit = parse_contract("BTC-20260814-62500-C")
        deribit = parse_contract("BTC-31JUL26-120000-P")
        self.assertEqual((bybit.expiry, bybit.strike, bybit.option_type), ("20260814", 62500.0, "C"))
        self.assertEqual((deribit.expiry, deribit.strike, deribit.option_type), ("31JUL26", 120000.0, "P"))
        self.assertIsNone(parse_contract("invalid"))

    def test_materiality_config_rejects_non_positive_cap(self):
        with self.assertRaises(ValueError):
            ExtractionConfig(max_contract_particles_per_metric_per_snapshot=0)

    def test_replay_is_read_only_and_produces_explainable_shadow_database(self):
        before = (file_hash(self.history_db), file_hash(self.research_db))
        output = self.root / "particle_shadow.db"
        output_path, summary = run_replay(self.dataset, output)
        after = (file_hash(self.history_db), file_hash(self.research_db))

        self.assertEqual(before, after)
        self.assertEqual(output_path, output.resolve())
        self.assertEqual(summary["counts"]["source_snapshots"], 4)
        self.assertEqual(summary["counts"]["contract_observations"], 8)
        self.assertGreater(summary["counts"]["particle_observations"], 0)
        self.assertGreater(summary["counts"]["particle_contract_links"], 0)
        self.assertGreater(summary["counts"]["particle_filter_audit"], 0)
        self.assertGreater(summary["counts"]["particle_constellations"], 0)
        self.assertGreater(summary["counts"]["shadow_candidates"], 0)
        self.assertTrue(output.with_suffix(".summary.json").is_file())

        connection = sqlite3.connect(output)
        try:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(
                connection.execute(
                    "SELECT value FROM schema_metadata WHERE key='mode'"
                ).fetchone()[0],
                "offline_read_only_shadow",
            )
            self.assertEqual(
                connection.execute("SELECT status FROM shadow_runs").fetchone()[0],
                "COMPLETE",
            )
            self.assertEqual(
                connection.execute(
                    "SELECT exclude_from_analysis FROM source_snapshots "
                    "WHERE history_snapshot_id=4"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM particle_observations "
                    "WHERE history_snapshot_id=4"
                ).fetchone()[0],
                0,
            )
            self.assertGreater(
                connection.execute(
                    "SELECT COUNT(*) FROM particle_observations WHERE features_json != '{}'"
                ).fetchone()[0],
                0,
            )
            self.assertGreater(
                connection.execute(
                    "SELECT COUNT(*) FROM particle_observations "
                    "WHERE particle_type LIKE 'CONTRACT_%'"
                ).fetchone()[0],
                0,
            )
            self.assertGreater(
                connection.execute(
                    "SELECT COUNT(*) FROM candidate_particle_lineage"
                ).fetchone()[0],
                0,
            )
            self.assertGreater(
                connection.execute(
                    "SELECT SUM(suppressed_below_threshold) "
                    "FROM particle_filter_audit"
                ).fetchone()[0],
                0,
            )
            self.assertLessEqual(
                connection.execute(
                    "SELECT MAX(emitted_changes) FROM particle_filter_audit"
                ).fetchone()[0],
                48,
            )
            self.assertGreater(
                connection.execute(
                    "SELECT COUNT(*) FROM particle_observations "
                    "WHERE particle_type LIKE 'CONTRACT_%' "
                    "AND features_json LIKE '%materiality_filter%'"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM shadow_candidates c
                    WHERE NOT EXISTS (
                        SELECT 1 FROM candidate_particle_lineage l
                        WHERE l.candidate_id = c.candidate_id
                    )
                    """
                ).fetchone()[0],
                0,
            )
            candidate_rows = connection.execute(
                "SELECT COUNT(*), COUNT(DISTINCT candidate_key), SUM(candidate_is_new) "
                "FROM shadow_candidates"
            ).fetchone()
            self.assertGreater(candidate_rows[0], 1)
            self.assertEqual(candidate_rows[1], 1)
            self.assertEqual(candidate_rows[2], 1)
        finally:
            connection.close()

        with self.assertRaises(ParticleReplayError):
            run_replay(self.dataset, output)

    def test_replay_remains_compatible_with_v1_archives_without_contract_table(self):
        connection = sqlite3.connect(self.history_db)
        try:
            connection.execute("DROP TABLE option_contract_snapshots")
            connection.commit()
        finally:
            connection.close()
        output = self.root / "legacy_particle_shadow.db"
        _, summary = run_replay(self.dataset, output)
        self.assertEqual(summary["counts"]["contract_observations"], 0)
        self.assertEqual(summary["counts"]["particle_contract_links"], 0)
        self.assertEqual(summary["counts"]["particle_filter_audit"], 0)

    def test_materiality_config_is_part_of_run_identity_and_enforces_cap(self):
        default_output = self.root / "default_filter.db"
        custom_output = self.root / "custom_filter.db"
        _, default_summary = run_replay(self.dataset, default_output)
        _, custom_summary = run_replay(
            self.dataset,
            custom_output,
            config=ExtractionConfig(
                max_contract_particles_per_metric_per_snapshot=1
            ),
        )
        self.assertNotEqual(default_summary["run_id"], custom_summary["run_id"])
        connection = sqlite3.connect(custom_output)
        try:
            self.assertLessEqual(
                connection.execute(
                    "SELECT MAX(emitted_changes) FROM particle_filter_audit"
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()


class HistoryContractSnapshotTest(unittest.TestCase):
    def test_history_db_persists_contract_metrics_atomically(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        db_path = Path(temporary.name) / "data" / "history.db"
        history = HistoryDB(str(db_path))
        self.addCleanup(history.close)
        snapshot_id = history.save_snapshot(
            ts=1_000.0,
            oi_data={"BTC-20260814-100-C": 12.0},
            contract_data={
                "BTC-20260814-100-C": {
                    "symbol": "BTC-20260814-100-C",
                    "expiry": "20260814",
                    "strike": 100,
                    "type": "C",
                    "oi": 12,
                    "volume": 7,
                    "markIv": 0.31,
                    "bidIv": 0.30,
                    "askIv": 0.32,
                    "delta": 0.51,
                    "gamma": 0.01,
                    "vega": 0.20,
                    "theta": -0.10,
                }
            },
        )
        self.assertIsNotNone(snapshot_id)
        connection = sqlite3.connect(db_path)
        try:
            row = connection.execute(
                """
                SELECT exchange, contract_id, oi, volume_24h, mark_iv,
                       delta, gamma, vega, theta
                FROM option_contract_snapshots
                """
            ).fetchone()
            self.assertEqual(row[:2], ("bybit", "BTC-20260814-100-C"))
            self.assertEqual(row[2:], (12.0, 7.0, 0.31, 0.51, 0.01, 0.2, -0.1))
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
