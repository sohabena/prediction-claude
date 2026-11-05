"""
Command-line utility to import Betfair historical data into PHOENIX.

Usage:
  # Import from a directory of Betfair files:
  python -m scripts.import_betfair_data --path data/betfair_cricket/

  # Dry run (inspect without inserting to database):
  python -m scripts.import_betfair_data --path data/match_123.json.gz --dry-run

  # Generate and import synthetic verified matches to immediately unblock offline training:
  python -m scripts.import_betfair_data --demo --matches 15
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from scraper.parsers.betfair_historical import BetfairHistoricalParser
from shared.logging import setup_logging

logger = setup_logging("import_betfair_cli")


def run_dry_run(args: argparse.Namespace) -> None:
    """Run inspection and validation synchronously without touching the database."""
    parser = BetfairHistoricalParser(
        resample_seconds=args.resample,
        min_ticks=args.min_ticks,
    )
    path = Path(args.path)
    print("\n" + "=" * 60)
    print(" PHOENIX - Betfair Historical Importer (DRY RUN)")
    print("=" * 60)
    print(f" Target path : {args.path}")
    print(f" Resample sec: {args.resample}s")
    print(f" Min ticks   : {args.min_ticks}")
    print("-" * 60)

    if not path.exists():
        print(f"[ERROR] Target path does not exist: {path}")
        sys.exit(1)

    if path.is_file():
        files = [path]
    else:
        files = []
        for ext in ("*.json", "*.json.gz", "*.bz2", "*.csv", "*.csv.gz", "*.jsonl"):
            files.extend(path.rglob(ext))

    total_ticks = 0
    valid_matches = 0

    for f in files:
        try:
            matches = parser.parse_file(f)
            for m in matches:
                valid_matches += 1
                total_ticks += len(m.ticks)
                print(
                    f"  [MATCH] {m.match_id}: {m.team_home} vs {m.team_away} | "
                    f"Ticks: {len(m.ticks)} | Winner: {m.winner or 'Unresolved'} | "
                    f"Duration: {m.completed_at - m.start_time}"
                )
        except Exception as e:
            print(f"  [ERROR] {f.name}: {e}")

    print("\n" + "-" * 60)
    print(f" Total files inspected : {len(files)}")
    print(f" Qualifying matches    : {valid_matches}")
    print(f" Total valid ticks     : {total_ticks}")
    print("=" * 60 + "\n")


async def run_import(args: argparse.Namespace) -> None:
    from scraper.importers.betfair_importer import BetfairHistoricalImporter

    importer = BetfairHistoricalImporter(
        resample_seconds=args.resample,
        min_ticks=args.min_ticks,
    )

    print("\n" + "=" * 60)
    print(" PHOENIX - Betfair Historical Data Importer")
    print("=" * 60)

    if args.demo:
        print(f" Mode: Generating {args.matches} synthetic verified match episodes...")
        summary = await importer.generate_and_import_demo_dataset(
            match_count=args.matches,
            ticks_per_match=args.ticks_per_match,
        )
    else:
        print(f" Target path : {args.path}")
        print(f" Resample sec: {args.resample}s")
        print(f" Min ticks   : {args.min_ticks}")
        print("-" * 60)
        summary = await importer.import_path(args.path, dry_run=False)

    print("\n" + "-" * 60)
    print(" IMPORT RESULTS SUMMARY")
    print("-" * 60)
    print(f" Files Scanned           : {summary.total_files_scanned}")
    print(f" Matches Imported        : {summary.matches_imported}")
    print(f" Total Ticks Persisted   : {summary.ticks_imported}")
    print(f" Match Results Recorded  : {summary.results_recorded}")
    print(f" Training Episodes Ready : {summary.training_episodes_approved}")
    print(f" Skipped / Incomplete    : {summary.skipped_matches}")

    if summary.errors:
        print("\nErrors encountered:")
        for err in summary.errors[:10]:
            print(f"  - {err}")
        if len(summary.errors) > 10:
            print(f"  ... and {len(summary.errors) - 10} more.")

    print("=" * 60 + "\n")


def cli() -> None:
    parser = argparse.ArgumentParser(
        description="Import Betfair historical cricket data into TimescaleDB."
    )
    parser.add_argument(
        "--path",
        type=str,
        default=None,
        help="Path to Betfair JSON/JSONL/BZ2/GZ/CSV file or directory.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Generate and import a synthetic verified dataset of matches (solves cold-start immediately).",
    )
    parser.add_argument(
        "--matches",
        type=int,
        default=15,
        help="Number of demo matches to generate (used with --demo, default 15).",
    )
    parser.add_argument(
        "--ticks-per-match",
        type=int,
        default=120,
        help="Ticks per match for demo dataset (default 120).",
    )
    parser.add_argument(
        "--resample",
        type=float,
        default=3.0,
        help="Minimum seconds between ticks for resampling (default 3.0).",
    )
    parser.add_argument(
        "--min-ticks",
        type=int,
        default=50,
        help="Minimum ticks required for an episode to qualify (default 50).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and validate files without committing to the database.",
    )

    args = parser.parse_args()

    if not args.path and not args.demo:
        print("\n[ERROR] Either --path <file_or_dir> or --demo must be specified.\n")
        parser.print_help()
        sys.exit(1)

    if args.dry_run:
        if not args.path:
            print("[ERROR] --dry-run requires --path <file_or_dir>")
            sys.exit(1)
        run_dry_run(args)
    else:
        asyncio.run(run_import(args))


if __name__ == "__main__":
    cli()
