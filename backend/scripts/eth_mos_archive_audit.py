"""Run the complete read-only ETH MOS archive audit and write JSON/Markdown."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from backend.research_interval.auditor import (
    IntervalAuditError,
    audit_archive,
    render_markdown,
    update_governance_pointers,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--previous", action="append", type=Path, default=[])
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--update-governance",
        action="store_true",
        help="Update machine-delimited audit pointers in the three ETH governance documents",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = audit_archive(args.archive, previous_sources=args.previous)
    except (IntervalAuditError, OSError, sqlite3.DatabaseError, ValueError, json.JSONDecodeError) as exc:
        print(f"ETH MOS archive audit failed: {exc}", file=sys.stderr)
        return 1
    output_dir = (args.output_dir or args.archive.resolve().parent / "eth_mos_audits").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = args.archive.stem
    json_path = output_dir / f"{stem}.audit.json"
    markdown_path = output_dir / f"{stem}.audit.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(result), encoding="utf-8")
    if args.update_governance:
        update_governance_pointers(result)
    print(f"ETH MOS audit JSON: {json_path}")
    print(f"ETH MOS audit Markdown: {markdown_path}")
    print(f"Quality: {result['status']}")
    print("Entry logic unchanged; source archive and source databases were not modified.")
    return 0 if result["status"] != "FAIL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
