"""Create a causal interval archive for all active frozen ETH MOS hypotheses."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.research_interval.exporter import (
    DEFAULT_CONTEXT_HOURS,
    DEFAULT_DATA_DIR,
    DEFAULT_OUTPUT_DIR,
    IntervalExportError,
    export_interval,
)
from backend.scripts.mos_dataset_exporter import _sha256


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from",
        dest="start",
        required=True,
        help="UTC start, for example 2026-09-01T00:00:00Z",
    )
    parser.add_argument(
        "--to",
        dest="end",
        help="UTC end; default is the latest fully closed ETHUSDT minute",
    )
    parser.add_argument("--context-hours", type=float, default=DEFAULT_CONTEXT_HOURS)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        path, manifest = export_interval(
            args.start,
            end=args.end,
            context_hours=args.context_hours,
            data_dir=args.data_dir,
            output_dir=args.output_dir,
            progress=lambda message: print(message, flush=True),
        )
    except (IntervalExportError, ValueError) as exc:
        print(f"ETH MOS Interval Exporter error: {exc}", file=sys.stderr)
        return 1
    print(f"ETH MOS interval archive: {path}")
    print(f"SHA256: {_sha256(path)}")
    print(json.dumps(manifest["window"], ensure_ascii=False, indent=2))
    print(f"Quality: {json.dumps(manifest['quality_summary'], ensure_ascii=False)}")
    if manifest["missing_optional_databases"]:
        print(
            "Optional databases not present: "
            + ", ".join(manifest["missing_optional_databases"])
            + ". ETH-H7 lineage will be rebuilt causally during audit."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
