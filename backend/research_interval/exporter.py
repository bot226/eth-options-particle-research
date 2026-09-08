"""Export a self-contained causal MOS interval without modifying live databases."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
from typing import Callable
import uuid
import zipfile

from backend.engine.version import CODE_VERSION, ENGINE_PATCH_VERSION, INTERVAL_EXPORTER_VERSION
from backend.scripts.mos_dataset_exporter import _backup_database, _git_metadata, _sha256


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = ROOT / "backend" / "data"
DEFAULT_OUTPUT_DIR = ROOT / "backend" / "exports" / "research_intervals"
PROTOCOL_DIR = ROOT / "docs" / "mos"
REQUIRED_DATABASES = (
    "mos_research.db",
    "history.db",
    "option_trade_flow.db",
    "mos_manual.db",
)
OPTIONAL_DATABASES = ("particle_shadow_v3.db",)
DEFAULT_CONTEXT_HOURS = 168.0
MAX_OUTCOME_MINUTES = 720
REQUIRED_PROTOCOLS = (
    "MOS_OPTION_FLOW_PREREG_V1.json",
    "MOS_TREND_BEFORE_COMPRESSION_PREREG_V1.json",
)
EXPECTED_PROTOCOL_SHA256 = {
    "MOS_OPTION_FLOW_PREREG_V1.json": "57B0D1FA0D988F5BCCEE5B674E8A8461E1667D36D7DFA7204FEA5A7D5D723217",
    "MOS_TREND_BEFORE_COMPRESSION_PREREG_V1.json": "E184B0C6FAD1E7849C9C2EA94A0882C479E9007BF61CD3ED1D52DEF9B23C906A",
}

TIME_COLUMNS: dict[str, dict[str, str]] = {
    "mos_research.db": {
        "snapshots": "timestamp_utc",
        "debug_snapshots": "timestamp_utc",
        "events": "timestamp_utc",
        "future_labels": "timestamp_utc",
        "bad_snapshots": "timestamp_utc",
        "bookmarks": "timestamp_utc",
        "ohlcv_candles": "timestamp_utc",
        "event_outcomes": "event_timestamp_utc",
        "event_level_reactions": "event_timestamp_utc",
    },
    "history.db": {
        "snapshots": "ts",
        "oi_history": "ts",
        "option_contract_snapshots": "ts",
        "option_surface_snapshots": "ts",
        "option_surface_contract_snapshots": "ts",
    },
    "option_trade_flow.db": {
        "option_trades": "trade_timestamp_utc",
        "collector_status": "updated_at_utc",
        "collector_status_history": "updated_at_utc",
    },
    "particle_shadow_v3.db": {
        "source_snapshots": "timestamp_utc",
        "contract_observations": "timestamp_utc",
        "particle_observations": "timestamp_utc",
        "particle_filter_audit": "timestamp_utc",
        "particle_constellations": "timestamp_utc",
        "shadow_candidates": "timestamp_utc",
    },
    "mos_manual.db": {
        "manual_trading_snapshots": "ts",
        "manual_decision_events": "ts",
        "manual_chart_health": "ts",
        "paper_trades": "entry_ts",
        "paper_trade_events": "ts",
    },
}

# `created_at_utc` on OHLCV rows is refreshed when the collector revisits its
# rolling backfill window.  It is therefore a last-write clock, not the first
# time the candle became available.  Filtering it against the requested market
# cutoff removed the newest rolling window (1,000 minutes in the live v72
# database).  Market time and verified-source flags are the causal selectors
# for archived outcome candles; write-lag is audited separately in quality.json.
AVAILABILITY_FILTER_EXEMPT = {("mos_research.db", "ohlcv_candles")}

CHILD_OF_WINDOWED_PARENT = {
    "particle_shadow_v3.db": {
        "shadow_outcomes": ("candidate_id", "shadow_candidates", "candidate_id"),
        "candidate_particle_lineage": (
            "candidate_id", "shadow_candidates", "candidate_id"
        ),
        "constellation_particle_links": (
            "constellation_id", "particle_constellations", "constellation_id"
        ),
        "particle_contract_links": (
            "particle_id", "particle_observations", "particle_id"
        ),
    }
}

EXTRA_PARENT_RELATIONS = {
    "history.db": [
        ("option_surface_snapshots", "universe_id", "option_surface_universes", "universe_id"),
    ],
    "mos_research.db": [
        ("debug_snapshots", "snapshot_id", "snapshots", "snapshot_id"),
        ("events", "snapshot_id", "snapshots", "snapshot_id"),
        ("future_labels", "snapshot_id", "snapshots", "snapshot_id"),
        ("event_outcomes", "event_id", "events", "id"),
        ("event_level_reactions", "event_id", "events", "id"),
        ("event_level_reactions", "outcome_id", "event_outcomes", "id"),
    ],
    "mos_manual.db": [
        ("paper_trade_events", "paper_trade_id", "paper_trades", "id"),
        ("paper_trades", "entry_snapshot_id", "manual_trading_snapshots", "id"),
        ("manual_decision_events", "source_snapshot_id", "manual_trading_snapshots", "id"),
    ],
}

# These parent/child rows carry the same source snapshot clock and both sides
# are independently selected by the identical support window. Re-running
# closure by the unindexed snapshots.snapshot_id would scan multi-gigabyte raw
# payload rows without adding a parent row.
TIME_COINCIDENT_RELATIONS = {
    "mos_research.db": {
        ("debug_snapshots", "snapshot_id", "snapshots", "snapshot_id"),
        ("future_labels", "snapshot_id", "snapshots", "snapshot_id"),
    },
}


class IntervalExportError(RuntimeError):
    pass


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def parse_utc(value: str | float | int | datetime) -> float:
    if isinstance(value, datetime):
        candidate = value
    elif isinstance(value, (float, int)):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("non_finite_UTC_time")
        return result
    else:
        text = str(value).strip()
        try:
            result = float(text)
        except ValueError:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            candidate = datetime.fromisoformat(text)
        else:
            if not math.isfinite(result):
                raise ValueError("non_finite_UTC_time")
            return result
    if candidate.tzinfo is None:
        candidate = candidate.replace(tzinfo=timezone.utc)
    return candidate.astimezone(timezone.utc).timestamp()


def iso_utc(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def _iso_sort_bound(value: float) -> str:
    """Fixed-width TEXT timestamp for half-open SQLite range comparisons."""
    return datetime.fromtimestamp(value, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )


def _safe_epoch(value) -> float | None:
    if value is None:
        return None
    try:
        return parse_utc(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _readonly(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    return db


def _tables(db: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in db.execute(f"PRAGMA table_info({_quote(table)})")}


def _column_types(db: sqlite3.Connection, table: str) -> dict[str, str]:
    return {
        row[1]: str(row[2] or "").upper()
        for row in db.execute(f"PRAGMA table_info({_quote(table)})")
    }


def _clock_access(
    column: str, declared_types: dict[str, str]
) -> tuple[str, Callable[[float], float | str]]:
    declared = declared_types.get(column, "")
    numeric_affinity = ("INT", "REAL", "FLOA", "DOUB", "NUM", "DEC")
    if any(token in declared for token in numeric_affinity):
        return _quote(column), float
    text_affinity = ("CHAR", "CLOB", "TEXT")
    if any(token in declared for token in text_affinity):
        # MOS text clocks are canonical UTC ISO-8601 strings. Comparing the
        # raw column keeps its ordinary timestamp index usable; wrapping the
        # column in a Python UDF forced multi-gigabyte table scans.
        # Use fixed-width microsecond bounds.  A second-only upper bound such
        # as ``...19:11:00Z`` sorts *after* ``...19:11:00.816Z`` in SQLite
        # TEXT order, which leaks rows at the half-open interval boundary.
        return _quote(column), _iso_sort_bound
    return f"utc_epoch({_quote(column)})", float


def _slice_copy(path: Path, database_name: str, support_start: float, end: float,
                analysis_start: float) -> dict:
    """Create a compact slice from an immutable online-backup copy."""
    full_copy = path.with_name(f".{path.name}.full")
    path.replace(full_copy)
    db = _readonly(full_copy)
    db.create_function("utc_epoch", 1, _safe_epoch, deterministic=True)
    tables = _tables(db)
    columns = {table: _columns(db, table) for table in tables}
    declared_types = {table: _column_types(db, table) for table in tables}
    report: dict[str, dict] = {}
    deferred_children: list[str] = []
    try:
        for table in tables:
            db.execute(f"CREATE TEMP TABLE {_quote('keep_' + table)}(id INTEGER PRIMARY KEY)")
        for table in tables:
            target = _quote(table)
            keep = _quote("keep_" + table)
            clock_column = TIME_COLUMNS.get(database_name, {}).get(table)
            if clock_column in columns[table]:
                clock, clock_bound = _clock_access(
                    clock_column, declared_types[table]
                )
                availability = next(
                    (
                        candidate
                        for candidate in ("received_at_utc", "created_at_utc", "observed_at_utc")
                        if candidate in columns[table] and candidate != clock_column
                    ),
                    None,
                )
                condition = f"(({clock} >= ? AND {clock} < ?) OR {clock} IS NULL)"
                params: tuple = (clock_bound(support_start), clock_bound(end))
                availability_filter_applied = bool(
                    availability
                    and (database_name, table) not in AVAILABILITY_FILTER_EXEMPT
                )
                if availability_filter_applied:
                    availability_clock, availability_bound = _clock_access(
                        availability, declared_types[table]
                    )
                    condition += (
                        f" AND ({availability_clock}<? "
                        f"OR {_quote(availability)} IS NULL)"
                    )
                    params += (availability_bound(end),)
                db.execute(f"INSERT INTO {keep} SELECT rowid FROM {target} WHERE {condition}", params)
                analysis_rows = db.execute(
                    f"SELECT COUNT(*) FROM {target} WHERE {clock} >= ? AND {clock} < ?",
                    (clock_bound(analysis_start), clock_bound(end)),
                ).fetchone()[0]
                report[table] = {
                    "policy": "support_and_analysis_window",
                    "time_column": clock_column,
                    "availability_column": availability,
                    "availability_filter_applied": availability_filter_applied,
                    "analysis_rows_before_parent_closure": int(analysis_rows),
                    "invalid_time_rows_retained": int(
                        db.execute(f"SELECT COUNT(*) FROM {target} WHERE {clock} IS NULL").fetchone()[0]
                    ),
                }
            elif table in CHILD_OF_WINDOWED_PARENT.get(database_name, {}):
                # Parent keep-tables may appear later in alphabetical schema
                # order, so resolve every child only after the first pass.
                deferred_children.append(table)
            elif (
                database_name == "history.db"
                and table == "option_surface_universes"
                and "created_ts" in columns[table]
            ):
                db.execute(
                    f"INSERT INTO {keep} SELECT rowid FROM {target} "
                    "WHERE utc_epoch(created_ts)<? OR utc_epoch(created_ts) IS NULL",
                    (end,),
                )
                report[table] = {"policy": "surface_universe_available_by_export_end"}
            else:
                db.execute(f"INSERT INTO {keep} SELECT rowid FROM {target}")
                report[table] = {"policy": "retained_reference_state_or_unmapped_table"}

        for table in deferred_children:
            target = _quote(table)
            keep = _quote("keep_" + table)
            child_key, parent, parent_key = CHILD_OF_WINDOWED_PARENT[database_name][table]
            db.execute(
                f"""INSERT INTO {keep} SELECT child.rowid FROM {target} child
                    WHERE child.{_quote(child_key)} IN (
                      SELECT parent.{_quote(parent_key)} FROM {_quote(parent)} parent
                      WHERE parent.rowid IN (
                        SELECT id FROM {_quote('keep_' + parent)}
                      )
                    )"""
            )
            report[table] = {
                "policy": f"children_of_windowed_{parent}",
                "parent_key": parent_key,
                "child_key": child_key,
            }

        relations: list[tuple[str, str, str, str]] = []
        for child in tables:
            for fk in db.execute(f"PRAGMA foreign_key_list({_quote(child)})"):
                relations.append((child, fk[3], fk[2], fk[4]))
        relations.extend(EXTRA_PARENT_RELATIONS.get(database_name, []))
        for _ in range(len(tables) + 1):
            changed = 0
            for child, child_key, parent, parent_key in relations:
                relation = (child, child_key, parent, parent_key)
                if relation in TIME_COINCIDENT_RELATIONS.get(database_name, set()):
                    continue
                if (
                    child not in columns
                    or parent not in columns
                    or child_key not in columns[child]
                    or parent_key not in columns[parent]
                ):
                    continue
                cursor = db.execute(
                    f"""INSERT OR IGNORE INTO {_quote('keep_' + parent)}
                        SELECT parent.rowid FROM {_quote(parent)} parent
                        JOIN {_quote(child)} child
                          ON parent.{_quote(parent_key)}=child.{_quote(child_key)}
                        WHERE child.rowid IN (SELECT id FROM {_quote('keep_' + child)})"""
                )
                changed += max(cursor.rowcount, 0)
            if not changed:
                break
        db.commit()
        schema_rows = db.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL "
            "AND name NOT LIKE 'sqlite_%' ORDER BY CASE type "
            "WHEN 'table' THEN 0 WHEN 'index' THEN 1 WHEN 'trigger' THEN 2 ELSE 3 END,name"
        ).fetchall()
        table_sql = [row[2] for row in schema_rows if row[0] == "table"]
        trailing_sql = [row[2] for row in schema_rows if row[0] != "table"]
        user_version = int(db.execute("PRAGMA user_version").fetchone()[0])
        application_id = int(db.execute("PRAGMA application_id").fetchone()[0])

        sliced = sqlite3.connect(path)
        try:
            sliced.execute("PRAGMA foreign_keys=OFF")
            for sql in table_sql:
                sliced.execute(sql)
            sliced.execute(f"PRAGMA user_version={user_version}")
            sliced.execute(f"PRAGMA application_id={application_id}")
            sliced.commit()
        finally:
            sliced.close()

        db.execute("ATTACH DATABASE ? AS sliced", (str(path),))
        try:
            db.execute("BEGIN")
            for table in tables:
                insertable = [
                    row[1]
                    for row in db.execute(f"PRAGMA table_xinfo({_quote(table)})")
                    if int(row[6]) == 0
                ]
                quoted_columns = ",".join(_quote(column) for column in insertable)
                db.execute(
                    f"INSERT INTO sliced.{_quote(table)}(rowid,{quoted_columns}) "
                    f"SELECT rowid,{quoted_columns} FROM main.{_quote(table)} "
                    f"WHERE rowid IN (SELECT id FROM temp.{_quote('keep_' + table)})"
                )
                report[table]["exported_rows"] = int(
                    db.execute(
                        f"SELECT COUNT(*) FROM temp.{_quote('keep_' + table)}"
                    ).fetchone()[0]
                )
            db.commit()
        finally:
            db.execute("DETACH DATABASE sliced")

        sliced = sqlite3.connect(path)
        try:
            for sql in trailing_sql:
                sliced.execute(sql)
            sliced.commit()
            integrity = sliced.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = sliced.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            sliced.close()
        if str(integrity).lower() != "ok":
            raise IntervalExportError(f"slice_integrity_failed:{database_name}:{integrity}")
        if foreign_keys:
            raise IntervalExportError(f"slice_foreign_key_failed:{database_name}")
        return report
    finally:
        db.close()
        if full_copy.exists():
            full_copy.unlink()


def _latest_complete_candle(data_dir: Path) -> float:
    path = data_dir / "mos_research.db"
    if not path.is_file():
        raise IntervalExportError("missing_required_database:mos_research.db")
    db = _readonly(path)
    try:
        columns = _columns(db, "ohlcv_candles")
        verified = " AND candle_source_verified=1" if "candle_source_verified" in columns else ""
        # A collector can mark the currently forming minute as verified before
        # that minute has actually closed.  The export boundary is therefore
        # constrained by wall-clock completion, not by the mutable source flag.
        completed_end = math.floor(time.time() / 60.0) * 60.0
        row = db.execute(
            "SELECT MAX(timestamp_utc) FROM ohlcv_candles "
            "WHERE exchange='bybit' AND symbol='ETHUSDT' AND timeframe='1m'"
            " AND timestamp_utc < ?" + verified,
            (completed_end,),
        ).fetchone()
    finally:
        db.close()
    if row is None or row[0] is None:
        raise IntervalExportError("no_verified_ETHUSDT_1m_candles")
    return min(float(row[0]) + 60.0, completed_end)


def _protocol_files() -> list[Path]:
    return sorted(PROTOCOL_DIR.glob("MOS_*.json"))


def _protocol_manifest(protocols: list[Path] | None = None) -> dict[str, dict]:
    result = {}
    for path in protocols if protocols is not None else _protocol_files():
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8-sig"))
        result[path.name] = {
            "sha256": hashlib.sha256(raw).hexdigest().upper(),
            "protocol_id": payload.get("protocol_id") or payload.get("protocol"),
            "hypothesis_id": payload.get("hypothesis_id"),
            "eligible_data_start_utc": payload.get("eligible_data_start_utc")
            or payload.get("eligible_data_after_utc"),
        }
    return result


def _validate_required_protocols(protocols: list[Path]) -> dict[str, dict]:
    by_name = {path.name: path for path in protocols}
    missing = [name for name in REQUIRED_PROTOCOLS if name not in by_name]
    if missing:
        raise IntervalExportError(
            "missing_required_frozen_protocols:" + ",".join(missing)
        )
    manifest = _protocol_manifest([by_name[name] for name in REQUIRED_PROTOCOLS])
    mismatched = [
        name
        for name, expected in EXPECTED_PROTOCOL_SHA256.items()
        if manifest[name]["sha256"] != expected
    ]
    if mismatched:
        raise IntervalExportError(
            "frozen_protocol_sha256_mismatch:" + ",".join(mismatched)
        )
    return manifest


def _database_summary(path: Path, database_name: str) -> dict:
    db = _readonly(path)
    db.create_function("utc_epoch", 1, _safe_epoch, deterministic=True)
    try:
        tables = _tables(db)
        counts = {
            table: int(db.execute(f"SELECT COUNT(*) FROM {_quote(table)}").fetchone()[0])
            for table in tables
        }
        ranges = {}
        for table, time_column in TIME_COLUMNS.get(database_name, {}).items():
            if table not in tables or time_column not in _columns(db, table):
                continue
            clock = f"utc_epoch({_quote(time_column)})"
            row = db.execute(
                f"SELECT COUNT(*),MIN({clock}),MAX({clock}) FROM {_quote(table)}"
            ).fetchone()
            ranges[table] = {
                "time_column": time_column,
                "rows": int(row[0]),
                "min_utc": iso_utc(float(row[1])) if row[1] is not None else None,
                "max_utc": iso_utc(float(row[2])) if row[2] is not None else None,
            }
        orphan_checks = []
        for child, child_key, parent, parent_key in EXTRA_PARENT_RELATIONS.get(
            database_name, []
        ):
            if (
                child not in tables
                or parent not in tables
                or child_key not in _columns(db, child)
                or parent_key not in _columns(db, parent)
            ):
                continue
            orphan_count = int(
                db.execute(
                    f"SELECT COUNT(*) FROM {_quote(child)} child "
                    f"LEFT JOIN {_quote(parent)} parent "
                    f"ON child.{_quote(child_key)}=parent.{_quote(parent_key)} "
                    f"WHERE child.{_quote(child_key)} IS NOT NULL "
                    f"AND parent.{_quote(parent_key)} IS NULL"
                ).fetchone()[0]
            )
            orphan_checks.append(
                {
                    "child": child,
                    "child_key": child_key,
                    "parent": parent,
                    "parent_key": parent_key,
                    "orphans": orphan_count,
                }
            )
        return {
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
            "table_counts": counts,
            "time_ranges": ranges,
            "checks": {
                "quick_check": db.execute("PRAGMA quick_check").fetchone()[0],
                "integrity_check": db.execute("PRAGMA integrity_check").fetchone()[0],
                "foreign_key_violations": len(db.execute("PRAGMA foreign_key_check").fetchall()),
                "relational_orphan_checks": orphan_checks,
                "relational_orphans": sum(item["orphans"] for item in orphan_checks),
            },
        }
    finally:
        db.close()


def _eth_asset_identity(stage: Path) -> dict:
    """Prove that the staged interval contains ETH and no BTC/SOL contamination."""

    checks: list[dict] = []
    contamination: list[dict] = []
    proven_sources: set[str] = set()

    specifications = {
        "mos_research.db": {
            "ohlcv_candles": {"symbol": "symbol"},
        },
        "option_trade_flow.db": {
            "option_trades": {
                "contract_id": "option",
                "source_instrument": "option",
            },
        },
        "history.db": {
            "option_contract_snapshots": {
                "contract_id": "option",
                "instrument_name": "option",
            },
            "option_surface_contract_snapshots": {
                "contract_id": "option",
                "instrument_name": "option",
            },
        },
    }
    for database_name, tables in specifications.items():
        path = stage / database_name
        if not path.is_file():
            continue
        db = _readonly(path)
        try:
            available_tables = set(_tables(db))
            for table, candidates in tables.items():
                if table not in available_tables:
                    continue
                available_columns = _columns(db, table)
                for column, kind in candidates.items():
                    if column not in available_columns:
                        continue
                    rows = db.execute(
                        f"SELECT {_quote(column)},COUNT(*) FROM {_quote(table)} "
                        f"WHERE {_quote(column)} IS NOT NULL "
                        f"GROUP BY {_quote(column)}"
                    ).fetchall()
                    checked = 0
                    eth_rows = 0
                    bad_rows = 0
                    for value, count in rows:
                        text = str(value).upper()
                        count = int(count)
                        checked += count
                        is_eth = text == "ETHUSDT" if kind == "symbol" else text.startswith("ETH-")
                        if is_eth:
                            eth_rows += count
                        else:
                            bad_rows += count
                            if len(contamination) < 20:
                                contamination.append(
                                    {
                                        "database": database_name,
                                        "table": table,
                                        "column": column,
                                        "value": str(value)[:160],
                                        "rows": count,
                                    }
                                )
                    checks.append(
                        {
                            "database": database_name,
                            "table": table,
                            "column": column,
                            "rows_checked": checked,
                            "eth_rows": eth_rows,
                            "non_eth_rows": bad_rows,
                        }
                    )
                    if eth_rows:
                        proven_sources.add(f"{database_name}:{table}:{column}")
        finally:
            db.close()
    required_proof = {
        "mos_research.db:ohlcv_candles:symbol",
        "option_trade_flow.db:option_trades:contract_id",
        "history.db:option_contract_snapshots:contract_id",
    }
    missing_proof = sorted(required_proof - proven_sources)
    return {
        "asset": "ETH",
        "futures_symbol": "ETHUSDT",
        "option_prefix": "ETH-",
        "checks": checks,
        "proof_sources": sorted(proven_sources),
        "missing_required_proof": missing_proof,
        "contamination_examples": contamination,
        "status": "pass" if not missing_proof and not contamination else "fail",
    }


def _quality(
    stage: Path,
    analysis_start: float,
    support_start: float,
    end: float,
) -> dict:
    result: dict = {
        "integrity": {},
        "ETHUSDT_1m": {},
        "hypothesis_eligibility": {},
        "archive_integrity_errors": [],
        "warnings": [],
    }
    for path in sorted(stage.glob("*.db")):
        db = _readonly(path)
        try:
            result["integrity"][path.name] = {
                "quick_check": db.execute("PRAGMA quick_check").fetchone()[0],
                "integrity_check": db.execute("PRAGMA integrity_check").fetchone()[0],
                "foreign_key_violations": len(db.execute("PRAGMA foreign_key_check").fetchall()),
            }
        finally:
            db.close()
    research = _readonly(stage / "mos_research.db")
    try:
        columns = _columns(research, "ohlcv_candles")
        verified = " AND candle_source_verified=1" if "candle_source_verified" in columns else ""
        created_clock = "created_at_utc" if "created_at_utc" in columns else "NULL"
        rows = [
            (float(row[0]), _safe_epoch(row[1]))
            for row in research.execute(
                f"SELECT timestamp_utc,{created_clock} FROM ohlcv_candles WHERE exchange='bybit' "
                "AND symbol='ETHUSDT' AND timeframe='1m' "
                "AND timestamp_utc >= ? AND timestamp_utc < ?" + verified + " ORDER BY timestamp_utc",
                (support_start, end),
            )
        ]
    finally:
        research.close()
    times = [row[0] for row in rows]
    unique_times = sorted(set(times))
    gaps = [
        {"after_utc": iso_utc(left), "before_utc": iso_utc(right), "gap_sec": right - left}
        for left, right in zip(unique_times, unique_times[1:])
        if right - left > 90.0
    ]
    required_first_open = math.ceil(support_start / 60.0) * 60.0
    required_last_open = end - 60.0
    starts_on_time = bool(unique_times and unique_times[0] <= required_first_open)
    ends_on_time = bool(unique_times and unique_times[-1] >= required_last_open)
    expected_rows = max(0, int(round((required_last_open - required_first_open) / 60.0)) + 1)
    duplicate_rows = len(times) - len(unique_times)
    missing_rows = max(0, expected_rows - len(unique_times))
    write_lags = [
        created - (timestamp + 60.0)
        for timestamp, created in rows
        if created is not None and math.isfinite(created)
    ]
    result["ETHUSDT_1m"] = {
        "rows": len(times),
        "unique_rows": len(unique_times),
        "expected_rows": expected_rows,
        "missing_rows": missing_rows,
        "duplicate_rows": duplicate_rows,
        "first_open_utc": iso_utc(unique_times[0]) if unique_times else None,
        "last_open_utc": iso_utc(unique_times[-1]) if unique_times else None,
        "required_first_open_utc": iso_utc(required_first_open),
        "required_last_open_utc": iso_utc(required_last_open),
        "starts_on_time": starts_on_time,
        "ends_on_time": ends_on_time,
        "gaps_over_90_sec": len(gaps),
        "gap_examples": gaps[:50],
        "coverage_complete": (
            starts_on_time
            and ends_on_time
            and not gaps
            and missing_rows == 0
            and duplicate_rows == 0
        ),
        "record_write_lag_sec": {
            "semantics": "last database write minus candle close; not guaranteed first-arrival latency",
            "samples": len(write_lags),
            "median": round(float(sorted(write_lags)[len(write_lags) // 2]), 3)
            if write_lags else None,
            "p95": round(float(sorted(write_lags)[min(len(write_lags) - 1, int(0.95 * len(write_lags)))]), 3)
            if write_lags else None,
            "maximum": round(max(write_lags), 3) if write_lags else None,
        },
    }
    if gaps:
        result["warnings"].append("ETHUSDT_1m_has_gaps_inside_requested_interval")
    if not starts_on_time:
        result["archive_integrity_errors"].append("ETHUSDT_1m_missing_support_start")
    if not ends_on_time:
        result["archive_integrity_errors"].append("ETHUSDT_1m_missing_requested_end")
    if gaps:
        result["archive_integrity_errors"].append("ETHUSDT_1m_internal_gaps")
    if missing_rows:
        result["archive_integrity_errors"].append("ETHUSDT_1m_missing_rows")
    if duplicate_rows:
        result["archive_integrity_errors"].append("ETHUSDT_1m_duplicate_rows")
    eligibility = result["hypothesis_eligibility"]
    result["research_usability"] = {
        "status": "ok"
        if not result["archive_integrity_errors"]
        and all(item.get("eligible", True) for item in eligibility.values())
        else "degraded",
        "complete_for_all_active_frozen_hypotheses": (
            not result["archive_integrity_errors"]
            and all(item.get("eligible", True) for item in eligibility.values())
        ),
    }
    return result


def export_interval(
    start,
    *,
    end=None,
    context_hours: float = DEFAULT_CONTEXT_HOURS,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    project_root: str | Path = ROOT,
    progress: Callable[[str], None] | None = None,
) -> tuple[Path, dict]:
    data_dir = Path(data_dir).resolve()
    output_dir = Path(output_dir).resolve()
    project_root = Path(project_root).resolve()
    analysis_start = parse_utc(start)
    if not math.isfinite(context_hours) or context_hours < 24.0:
        raise IntervalExportError("context_hours_must_be_at_least_24")
    latest_candle_end = _latest_complete_candle(data_dir)
    package_end = latest_candle_end if end is None else min(parse_utc(end), latest_candle_end)
    if analysis_start >= package_end:
        raise IntervalExportError("start_must_precede_latest_complete_candle")
    support_start = analysis_start - context_hours * 3600.0
    carryover_outcome_start = analysis_start - MAX_OUTCOME_MINUTES * 60.0
    complete_720_end = package_end - MAX_OUTCOME_MINUTES * 60.0

    missing = [name for name in REQUIRED_DATABASES if not (data_dir / name).is_file()]
    if missing:
        raise IntervalExportError("missing_required_databases:" + ",".join(missing))
    protocols = _protocol_files()
    required_protocol_manifest = _validate_required_protocols(protocols)
    included = [*REQUIRED_DATABASES]
    included.extend(name for name in OPTIONAL_DATABASES if (data_dir / name).is_file())
    inputs = {name: data_dir / name for name in included}
    if len(set(path.resolve() for path in inputs.values())) != len(inputs):
        raise IntervalExportError("source_database_collision")

    output_dir.mkdir(parents=True, exist_ok=True)
    estimate = sum(
        path.stat().st_size
        + (Path(str(path) + "-wal").stat().st_size if Path(str(path) + "-wal").exists() else 0)
        for path in inputs.values()
    )
    if shutil.disk_usage(output_dir).free < estimate * 2 + 512 * 1024 * 1024:
        raise IntervalExportError("insufficient_staging_space_for_online_backups")
    export_id = uuid.uuid4().hex
    name_start = datetime.fromtimestamp(analysis_start, timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    name_end = datetime.fromtimestamp(package_end, timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    archive_path = output_dir / f"mos_interval_{name_start}_to_{name_end}_{export_id[:8]}.zip"
    missing_optional = [name for name in OPTIONAL_DATABASES if name not in included]
    warnings = [
        "Each database is internally consistent but the set is not one cross-database transaction.",
        "Rows before analysis_start are support context and must never count as new evidence.",
        "Rows in the 720-minute carryover window may only mature prior pending outcomes and must be deduplicated by stable source ID.",
        "Events lacking their complete frozen future horizon remain pending, never zero or negative.",
        "Offline replay and audit outputs are research records and cannot authorize exchange execution.",
    ]
    manifest = {
        "format": "mos_frozen_hypothesis_interval_v1",
        "interval_exporter_version": INTERVAL_EXPORTER_VERSION,
        "export_id": export_id,
        "created_at_utc": iso_utc(time.time()),
        "code_version": CODE_VERSION,
        "engine_patch_version": ENGINE_PATCH_VERSION,
        "git": _git_metadata(project_root),
        "source_databases_modified": False,
        "cross_database_atomic": False,
        "backup_method": "sqlite_online_backup_then_staged_relational_slice",
        "asset": "ETH",
        "futures_symbol": "ETHUSDT",
        "option_instrument_prefix": "ETH-",
        "scope": "all_active_frozen_ETH_MOS_hypotheses_research_only",
        "window": {
            "analysis_start_utc": iso_utc(analysis_start),
            "package_end_latest_complete_candle_utc": iso_utc(package_end),
            "support_context_start_utc": iso_utc(support_start),
            "support_context_hours": context_hours,
            "maximum_outcome_horizon_minutes": MAX_OUTCOME_MINUTES,
            "carryover_outcome_start_utc": iso_utc(carryover_outcome_start),
            "carryover_rule": "events before analysis_start may only complete previously pending outcomes and must be deduplicated by stable source ID",
            "fully_mature_720m_event_end_utc": iso_utc(complete_720_end),
            "new_interval_has_fully_mature_720m_events": complete_720_end >= analysis_start,
            "events_after_fully_mature_end_are_pending": True,
            "analysis_rule": "count new events only from analysis_start; carryover may mature prior pending outcomes; older context never enlarges the test sample",
        },
        "required_databases": list(REQUIRED_DATABASES),
        "optional_databases": list(OPTIONAL_DATABASES),
        "included_databases": included,
        "missing_optional_databases": missing_optional,
        "h7_lineage": {
            "preferred_source": "particle_shadow_v3.db",
            "fallback_source": "causal_replay_from_history.db_and_mos_research.db",
            "fallback_requires_exact_parity_when_both_sources_overlap": True,
            "frozen_gates_may_be_weakened": False,
        },
        "required_frozen_protocols": list(REQUIRED_PROTOCOLS),
        "required_frozen_protocol_sha256": dict(EXPECTED_PROTOCOL_SHA256),
        "required_frozen_protocol_manifest": required_protocol_manifest,
        "frozen_protocols": _protocol_manifest(protocols),
        "databases": {},
        "warnings": warnings,
    }

    with tempfile.TemporaryDirectory(prefix=".mos_interval_export_", dir=output_dir) as temporary:
        stage = Path(temporary)
        total_databases = len(inputs)
        for index, (database_name, source) in enumerate(inputs.items(), 1):
            if progress:
                progress(f"[{index}/{total_databases}] Online backup: {database_name}")
            backup = _backup_database(source, stage / database_name)
            backup["pre_slice_backup"] = {
                "size_bytes": backup["backup"]["size_bytes"],
                "sha256": backup["backup"]["sha256"],
                "table_counts": backup["table_counts"],
                "time_ranges": backup["time_ranges"],
            }
            if progress:
                progress(f"[{index}/{total_databases}] Compact interval copy: {database_name}")
            backup["slice"] = _slice_copy(
                stage / database_name,
                database_name,
                support_start,
                package_end,
                analysis_start,
            )
            summary = _database_summary(stage / database_name, database_name)
            backup["backup"]["size_bytes"] = summary["size_bytes"]
            backup["backup"]["sha256"] = summary["sha256"]
            backup["table_counts"] = summary["table_counts"]
            backup["time_ranges"] = summary["time_ranges"]
            backup["checks"] = summary["checks"]
            manifest["databases"][database_name] = backup
            if progress:
                progress(f"[{index}/{total_databases}] Complete: {database_name}")

        quality = _quality(stage, analysis_start, support_start, package_end)
        quality["asset_identity"] = _eth_asset_identity(stage)
        if quality["asset_identity"]["status"] != "pass":
            quality["archive_integrity_errors"].append("ETH_asset_identity_failed")
        relational_orphans = {
            name: int(item["checks"].get("relational_orphans", 0))
            for name, item in manifest["databases"].items()
            if int(item["checks"].get("relational_orphans", 0)) > 0
        }
        quality["relational_orphans"] = relational_orphans
        if relational_orphans:
            quality["archive_integrity_errors"].append("relational_orphans_detected")
        quality["hypothesis_eligibility"]["ETH-H7-lineage"] = {
            "eligible": True,
            "source": (
                "particle_shadow_v3.db"
                if (stage / "particle_shadow_v3.db").is_file()
                else "causal_replay_from_history.db_and_mos_research.db"
            ),
            "parity_required_when_both_sources_overlap": True,
        }
        quality["research_usability"] = {
            "status": "ok" if not quality["archive_integrity_errors"] else "degraded",
            "complete_for_all_active_frozen_hypotheses": not quality[
                "archive_integrity_errors"
            ],
        }
        (stage / "quality.json").write_text(
            json.dumps(quality, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        protocol_stage = stage / "protocols"
        protocol_stage.mkdir()
        for protocol in protocols:
            shutil.copy2(protocol, protocol_stage / protocol.name)
        manifest["quality_summary"] = {
            "all_integrity_checks_ok": all(
                str(item["integrity_check"]).lower() == "ok"
                and str(item["quick_check"]).lower() == "ok"
                and item["foreign_key_violations"] == 0
                for item in quality["integrity"].values()
            ),
            "ETHUSDT_1m_gaps_over_90_sec": quality["ETHUSDT_1m"]["gaps_over_90_sec"],
            "ETHUSDT_1m_coverage_complete": quality["ETHUSDT_1m"]["coverage_complete"],
            "archive_integrity_errors": quality["archive_integrity_errors"],
            "asset_identity": quality["asset_identity"],
            "relational_orphans": quality["relational_orphans"],
            "hypothesis_eligibility": quality["hypothesis_eligibility"],
            "research_usability": quality["research_usability"],
            "warnings": quality["warnings"],
        }
        if quality["archive_integrity_errors"]:
            raise IntervalExportError(
                "archive_quality_gate_failed:"
                + ",".join(quality["archive_integrity_errors"])
            )
        manifest["files"] = {}
        for path in sorted(item for item in stage.rglob("*") if item.is_file()):
            relative = path.relative_to(stage).as_posix()
            manifest["files"][relative] = {"sha256": _sha256(path), "bytes": path.stat().st_size}
        (stage / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_archive = stage / "dataset.zip"
        if progress:
            progress("Creating final ZIP archive")
        with zipfile.ZipFile(temporary_archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(item for item in stage.rglob("*") if item.is_file()):
                if path != temporary_archive:
                    archive.write(path, path.relative_to(stage).as_posix())
        with zipfile.ZipFile(temporary_archive) as archive:
            if archive.testzip() is not None:
                raise IntervalExportError("archive_crc_failed")
        os.replace(temporary_archive, archive_path)
    return archive_path, manifest
