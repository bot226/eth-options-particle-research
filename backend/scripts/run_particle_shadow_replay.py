"""CLI entry point for offline MOS Particle Logic shadow replay."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from research.particle_shadow.replay import ParticleReplayError, run_replay


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a standalone Particle Logic shadow database from a MOS dataset ZIP. "
            "The source databases are opened read-only."
        )
    )
    parser.add_argument("dataset", type=Path, help="MOS dataset ZIP or extracted directory")
    parser.add_argument(
        "--output",
        type=Path,
        help="Output .db path (default: <dataset>_particle_shadow_v1.db)",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Explicitly replace an existing output database",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = args.output
    if output is None:
        stem = args.dataset.stem if args.dataset.suffix else args.dataset.name
        output = args.dataset.parent / f"{stem}_particle_shadow_v1.db"
    try:
        output_path, summary = run_replay(
            args.dataset,
            output,
            replace=args.replace,
        )
    except ParticleReplayError as exc:
        print(f"Particle shadow replay error: {exc}", file=sys.stderr)
        return 1

    print(f"Particle shadow database: {output_path}")
    print(json.dumps(summary["counts"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
