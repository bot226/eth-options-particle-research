"""Create a consistent, transferable MOS dataset archive.

The exporter is intentionally read-only with respect to the live MOS databases.
It uses SQLite's online backup API so committed WAL records are included without
stopping the collector.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import runpy
import socket
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


EXPORTER_VERSION = "1.0.0"
MANIFEST_VERSION = "1.0"
REQUIRED_DATABASES = (
    "mos_research.db",
    "mos_manual.db",
    "history.db",
)
DEFAULT_LABEL = "mos_baseline"
LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_ROOT / "backend" / "data"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "backend" / "exports"

TIME_COLUMNS: dict[str, dict[str, str]] = {
    "mos_research.db": {
        "snapshots": "timestamp_utc",
        "debug_snapshots": "timestamp_utc",
        "events": "timestamp_utc",
        "future_labels": "timestamp_utc",
        "ohlcv_candles": "timestamp_utc",
        "event_outcomes": "event_timestamp_utc",
        "event_level_reactions": "event_timestamp_utc",
    },
    "mos_manual.db": {
        "manual_trading_snapshots": "created_at",
        "manual_decision_events": "created_at",
        "paper_trades": "created_at",
        "paper_trade_events": "ts",
    },
    "history.db": {
        "snapshots": "ts",
        "oi_history": "ts",
    },
}


class DatasetExportError(RuntimeError):
    """Raised when a complete, verified MOS archive cannot be created."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _file_mtime_utc(path: Path) -> str:
    return _iso_utc(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _readonly_connection(path: Path) -> sqlite3.Connection:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=30.0)


def _table_names(connection: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
    ]


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    quoted_table = _quote_identifier(table)
    return {
        row[1]
        for row in connection.execute(f"PRAGMA table_info({quoted_table})").fetchall()
    }


def _table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in _table_names(connection):
        quoted_table = _quote_identifier(table)
        counts[table] = int(
            connection.execute(f"SELECT COUNT(*) FROM {quoted_table}").fetchone()[0]
        )
    return counts


def _time_ranges(
    connection: sqlite3.Connection,
    database_name: str,
) -> dict[str, dict[str, Any]]:
    ranges: dict[str, dict[str, Any]] = {}
    available_tables = set(_table_names(connection))
    for table, column in TIME_COLUMNS.get(database_name, {}).items():
        if table not in available_tables:
            continue
        columns = _column_names(connection, table)
        if column not in columns:
            continue
        quoted_table = _quote_identifier(table)
        quoted_column = _quote_identifier(column)
        row = connection.execute(
            f"""
            SELECT COUNT(*), MIN({quoted_column}), MAX({quoted_column})
            FROM {quoted_table}
            """
        ).fetchone()
        ranges[table] = {
            "column": column,
            "rows": int(row[0]),
            "min": row[1],
            "max": row[2],
        }
    return ranges


def _version_groups(connection: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for table in ("snapshots", "manual_trading_snapshots"):
        if table not in set(_table_names(connection)):
            continue
        columns = _column_names(connection, table)
        selected = [
            column
            for column in (
                "code_version",
                "research_schema_version",
                "engine_patch_version",
            )
            if column in columns
        ]
        if not selected:
            continue
        quoted_table = _quote_identifier(table)
        quoted_columns = [_quote_identifier(column) for column in selected]
        select_sql = ", ".join(quoted_columns)
        rows = connection.execute(
            f"""
            SELECT {select_sql}, COUNT(*)
            FROM {quoted_table}
            GROUP BY {select_sql}
            ORDER BY COUNT(*) DESC
            """
        ).fetchall()
        result[table] = [
            {
                **dict(zip(selected, row[:-1])),
                "rows": int(row[-1]),
            }
            for row in rows
        ]
    return result


def _database_checks(connection: sqlite3.Connection) -> dict[str, str]:
    integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
    quick_rows = connection.execute("PRAGMA quick_check").fetchall()
    return {
        "integrity_check": "; ".join(str(row[0]) for row in integrity_rows),
        "quick_check": "; ".join(str(row[0]) for row in quick_rows),
    }


def _backup_database(source_path: Path, backup_path: Path) -> dict[str, Any]:
    source_stat = source_path.stat()
    source_connection = _readonly_connection(source_path)
    try:
        source_journal_mode = str(
            source_connection.execute("PRAGMA journal_mode").fetchone()[0]
        )
        target_connection = sqlite3.connect(backup_path)
        try:
            source_connection.backup(target_connection, pages=2048, sleep=0.01)
            target_connection.commit()
            target_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            target_connection.execute("PRAGMA journal_mode = DELETE")
            target_connection.commit()
        finally:
            target_connection.close()
    finally:
        source_connection.close()

    backup_connection = _readonly_connection(backup_path)
    try:
        checks = _database_checks(backup_connection)
        if checks["integrity_check"].lower() != "ok":
            raise DatasetExportError(
                f"Integrity check failed for {source_path.name}: "
                f"{checks['integrity_check']}"
            )
        if checks["quick_check"].lower() != "ok":
            raise DatasetExportError(
                f"Quick check failed for {source_path.name}: {checks['quick_check']}"
            )
        page_count = int(backup_connection.execute("PRAGMA page_count").fetchone()[0])
        page_size = int(backup_connection.execute("PRAGMA page_size").fetchone()[0])
        schema_version = int(
            backup_connection.execute("PRAGMA schema_version").fetchone()[0]
        )
        user_version = int(
            backup_connection.execute("PRAGMA user_version").fetchone()[0]
        )
        table_counts = _table_counts(backup_connection)
        time_ranges = _time_ranges(backup_connection, source_path.name)
        version_groups = _version_groups(backup_connection)
    finally:
        backup_connection.close()

    return {
        "source": {
            "filename": source_path.name,
            "size_bytes": int(source_stat.st_size),
            "modified_at_utc": _file_mtime_utc(source_path),
            "journal_mode": source_journal_mode,
        },
        "backup": {
            "filename": backup_path.name,
            "size_bytes": int(backup_path.stat().st_size),
            "sha256": _sha256(backup_path),
            "page_count": page_count,
            "page_size": page_size,
            "schema_version": schema_version,
            "user_version": user_version,
        },
        "checks": checks,
        "table_counts": table_counts,
        "time_ranges": time_ranges,
        "version_groups": version_groups,
    }


def _run_git(project_root: Path, arguments: Iterable[str]) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(project_root), *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = completed.stdout.strip()
    return value or None


def _git_metadata(project_root: Path) -> dict[str, Any]:
    status = _run_git(project_root, ("status", "--porcelain"))
    return {
        "commit": _run_git(project_root, ("rev-parse", "HEAD")),
        "branch": _run_git(project_root, ("branch", "--show-current")),
        "dirty": None if status is None else bool(status),
    }


def _runtime_versions(project_root: Path) -> dict[str, Any]:
    version_path = project_root / "backend" / "engine" / "version.py"
    if not version_path.exists():
        return {"error": f"Version file not found: {version_path}"}
    try:
        namespace = runpy.run_path(str(version_path))
    except Exception as exc:  # pragma: no cover - defensive metadata fallback
        return {"error": f"Unable to read runtime version: {exc}"}
    return {
        "code_version": namespace.get("CODE_VERSION"),
        "research_schema_version": namespace.get("RESEARCH_SCHEMA_VERSION"),
        "engine_patch_version": namespace.get("ENGINE_PATCH_VERSION"),
        "dataset_exporter_version": namespace.get("DATASET_EXPORTER_VERSION"),
        "particle_logic_version": namespace.get("PARTICLE_LOGIC_VERSION"),
    }


def _available_archive_path(output_dir: Path, filename: str) -> Path:
    candidate = output_dir / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    index = 2
    while True:
        candidate = output_dir / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _write_archive(staging_dir: Path, archive_path: Path) -> None:
    temporary_archive = archive_path.with_suffix(archive_path.suffix + ".tmp")
    try:
        with zipfile.ZipFile(
            temporary_archive,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            archive_members = [
                *(staging_dir / name for name in REQUIRED_DATABASES),
                staging_dir / "manifest.json",
            ]
            for path in sorted(archive_members, key=lambda item: item.name):
                archive.write(path, arcname=path.name)
        os.replace(temporary_archive, archive_path)
    finally:
        if temporary_archive.exists():
            temporary_archive.unlink()


def create_dataset_export(
    data_dir: Path | str = DEFAULT_DATA_DIR,
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
    label: str = DEFAULT_LABEL,
    *,
    project_root: Path | str = PROJECT_ROOT,
    started_at: datetime | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Create and verify a ZIP containing consistent copies of all MOS databases."""

    data_dir = Path(data_dir).resolve()
    output_dir = Path(output_dir).resolve()
    project_root = Path(project_root).resolve()
    if not LABEL_PATTERN.fullmatch(label):
        raise DatasetExportError(
            "Label must contain only letters, numbers, dots, underscores, and hyphens"
        )

    missing = [name for name in REQUIRED_DATABASES if not (data_dir / name).is_file()]
    if missing:
        raise DatasetExportError(
            "Required MOS databases are missing: " + ", ".join(missing)
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    export_started = started_at or _utc_now()
    if export_started.tzinfo is None:
        export_started = export_started.replace(tzinfo=timezone.utc)
    timestamp = export_started.astimezone(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    archive_path = _available_archive_path(output_dir, f"{label}_{timestamp}.zip")

    manifest: dict[str, Any] = {
        "manifest_version": MANIFEST_VERSION,
        "exporter": {
            "name": "MOS Dataset Exporter",
            "version": EXPORTER_VERSION,
            "backup_method": "sqlite_online_backup_api",
            "source_databases_modified": False,
        },
        "dataset": {
            "label": label,
            "started_at_utc": _iso_utc(export_started),
            "completed_at_utc": None,
            "required_databases": list(REQUIRED_DATABASES),
        },
        "collector": {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "timezone": str(datetime.now().astimezone().tzinfo),
        },
        "project": {
            "git": _git_metadata(project_root),
            "runtime_versions": _runtime_versions(project_root),
        },
        "databases": {},
        "errors": [],
    }

    try:
        with tempfile.TemporaryDirectory(
            prefix=".mos_dataset_export_",
            dir=output_dir,
        ) as temporary_directory:
            staging_dir = Path(temporary_directory)
            for database_name in REQUIRED_DATABASES:
                manifest["databases"][database_name] = _backup_database(
                    data_dir / database_name,
                    staging_dir / database_name,
                )

            manifest["dataset"]["completed_at_utc"] = _iso_utc(_utc_now())
            manifest_path = staging_dir / "manifest.json"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            _write_archive(staging_dir, archive_path)
    except Exception as exc:
        if isinstance(exc, DatasetExportError):
            raise
        raise DatasetExportError(f"Dataset export failed: {exc}") from exc

    return archive_path, manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create a consistent ZIP backup of mos_research.db, mos_manual.db, "
            "and history.db while MOS keeps running."
        )
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"MOS database directory (default: {DEFAULT_DATA_DIR})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Archive destination (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--label",
        default=DEFAULT_LABEL,
        help=f"Archive prefix (default: {DEFAULT_LABEL})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        archive_path, manifest = create_dataset_export(
            data_dir=args.data_dir,
            output_dir=args.output_dir,
            label=args.label,
        )
    except DatasetExportError as exc:
        print(f"MOS Dataset Exporter error: {exc}", file=sys.stderr)
        return 1

    print(f"MOS dataset archive: {archive_path}")
    print(f"SHA256: {_sha256(archive_path)}")
    for database_name in REQUIRED_DATABASES:
        metadata = manifest["databases"][database_name]
        row_total = sum(metadata["table_counts"].values())
        print(
            f"  {database_name}: {row_total} rows, "
            f"integrity={metadata['checks']['integrity_check']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
