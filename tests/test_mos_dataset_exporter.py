import json
import sqlite3
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from backend.scripts.mos_dataset_exporter import (
    DatasetExportError,
    REQUIRED_DATABASES,
    create_dataset_export,
)


class MosDatasetExporterTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.data_dir = self.root / "data"
        self.output_dir = self.root / "exports"
        self.data_dir.mkdir()
        self.connections = []
        for database_name in REQUIRED_DATABASES:
            connection = sqlite3.connect(self.data_dir / database_name)
            self.connections.append(connection)
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE records (
                    id INTEGER PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO records (value) VALUES (?)",
                (database_name,),
            )
            connection.commit()
        self.addCleanup(self._close_connections)

    def _close_connections(self):
        for connection in self.connections:
            connection.close()

    def test_exports_consistent_wal_databases_and_manifest(self):
        archive_path, manifest = create_dataset_export(
            self.data_dir,
            self.output_dir,
            "mos_test",
            project_root=self.root,
            started_at=datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(archive_path.name, "mos_test_2026-07-31T120000Z.zip")
        self.assertTrue(archive_path.is_file())
        self.assertFalse(manifest["exporter"]["source_databases_modified"])
        self.assertEqual(manifest["errors"], [])

        extracted_dir = self.root / "extracted"
        with zipfile.ZipFile(archive_path) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {*REQUIRED_DATABASES, "manifest.json"},
            )
            archive.extractall(extracted_dir)
            archived_manifest = json.loads(archive.read("manifest.json"))

        self.assertEqual(
            archived_manifest["dataset"]["required_databases"],
            list(REQUIRED_DATABASES),
        )
        for database_name in REQUIRED_DATABASES:
            metadata = archived_manifest["databases"][database_name]
            self.assertEqual(metadata["checks"]["integrity_check"], "ok")
            self.assertEqual(metadata["checks"]["quick_check"], "ok")
            self.assertEqual(metadata["table_counts"], {"records": 1})
            self.assertEqual(len(metadata["backup"]["sha256"]), 64)
            connection = sqlite3.connect(extracted_dir / database_name)
            try:
                self.assertEqual(
                    connection.execute("SELECT value FROM records").fetchone()[0],
                    database_name,
                )
                self.assertEqual(
                    connection.execute("PRAGMA integrity_check").fetchone()[0],
                    "ok",
                )
            finally:
                connection.close()

    def test_fails_when_required_database_is_missing(self):
        self.connections[-1].close()
        self.connections.pop()
        (self.data_dir / REQUIRED_DATABASES[-1]).unlink()

        with self.assertRaisesRegex(DatasetExportError, "history.db"):
            create_dataset_export(
                self.data_dir,
                self.output_dir,
                project_root=self.root,
            )

        self.assertFalse(self.output_dir.exists())

    def test_rejects_unsafe_archive_label(self):
        with self.assertRaisesRegex(DatasetExportError, "Label"):
            create_dataset_export(
                self.data_dir,
                self.output_dir,
                "../outside",
                project_root=self.root,
            )


if __name__ == "__main__":
    unittest.main()
