import sqlite3
import tempfile
import unittest
from pathlib import Path

from research.particle_schema import (
    COLLECTOR_VERSION,
    PARTICLE_DEFINITION_VERSION,
    SCHEMA_VERSION,
    initialize_database,
)


class ParticleSchemaTest(unittest.TestCase):
    def test_initializes_and_writes_synthetic_option_snapshot(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        db_path = Path(temp_dir.name) / "particles_live.db"
        connection = initialize_database(db_path)
        self.addCleanup(connection.close)

        connection.execute(
            """
            INSERT INTO option_snapshots (
                snapshot_id,
                timestamp_utc,
                exchange,
                underlying,
                spot_price,
                index_price,
                source_status,
                data_quality,
                source_age_sec,
                collector_version,
                schema_version,
                particle_definition_version,
                raw_payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "snapshot-test-1",
                "2026-07-30T20:00:00+00:00",
                "deribit",
                "BTC",
                118_000.0,
                118_010.0,
                "ONLINE",
                "GOOD",
                0.5,
                COLLECTOR_VERSION,
                SCHEMA_VERSION,
                PARTICLE_DEFINITION_VERSION,
                '{"synthetic": true}',
            ),
        )
        connection.execute(
            """
            INSERT INTO option_contracts (
                contract_record_id,
                snapshot_id,
                exchange,
                instrument_name,
                source_timestamp_utc,
                expiry_utc,
                strike,
                option_type,
                underlying_price,
                mark_iv,
                iv_scale,
                delta,
                gamma,
                open_interest,
                open_interest_unit,
                volume,
                volume_unit,
                source_age_sec,
                raw_features_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "contract-test-1",
                "snapshot-test-1",
                "deribit",
                "BTC-31JUL26-120000-C",
                "2026-07-30T19:59:59.500000+00:00",
                "2026-07-31T08:00:00+00:00",
                120_000.0,
                "C",
                118_010.0,
                52.4,
                "PERCENT",
                0.41,
                0.00008,
                125.0,
                "CONTRACTS",
                10.0,
                "CONTRACTS",
                0.5,
                '{"synthetic": true}',
            ),
        )
        connection.commit()

        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertTrue(
            {
                "schema_metadata",
                "option_snapshots",
                "option_contracts",
                "ohlcv_candles",
                "particles",
                "source_health",
            }.issubset(tables)
        )
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM option_snapshots").fetchone()[0],
            1,
        )
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM option_contracts").fetchone()[0],
            1,
        )
        self.assertEqual(
            connection.execute("PRAGMA integrity_check").fetchone()[0],
            "ok",
        )
        self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")

        metadata = dict(
            connection.execute("SELECT key, value FROM schema_metadata").fetchall()
        )
        self.assertEqual(metadata["collector_version"], COLLECTOR_VERSION)
        self.assertEqual(metadata["schema_version"], SCHEMA_VERSION)
        self.assertEqual(
            metadata["particle_definition_version"],
            PARTICLE_DEFINITION_VERSION,
        )

        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO option_contracts (
                    contract_record_id,
                    snapshot_id,
                    exchange,
                    instrument_name,
                    expiry_utc,
                    strike,
                    option_type,
                    iv_scale,
                    open_interest_unit,
                    volume_unit
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "orphan-contract",
                    "missing-snapshot",
                    "deribit",
                    "BTC-31JUL26-100000-P",
                    "2026-07-31T08:00:00+00:00",
                    100_000.0,
                    "P",
                    "PERCENT",
                    "CONTRACTS",
                    "CONTRACTS",
                ),
            )


if __name__ == "__main__":
    unittest.main()
