"""S99: import the aussportsbetting historical file(s) saved by hand into the S98 odds store.

Usage::

    python scripts/import_aussportsbetting.py                  # reads data/historical_odds/incoming/aussportsbetting
    python scripts/import_aussportsbetting.py --dry-run        # parse and print; writes nothing
    python scripts/import_aussportsbetting.py --incoming DIR --root DIR

Every .xlsx, .xls or .csv in the incoming folder is parsed by ``scraper.importers.aussportsbetting``. Nothing is
downloaded: the operator saves the file by hand (the site is behind a Cloudflare check). The store is rewritten from
all the files in the folder on each run, so keep every file there. The store and manifest go to
``<root>/aussportsbetting/`` through ``training.historical_odds.write_store``.

Exit codes: 0 written (or a dry run that parsed), 1 nothing to import, a file could not be read, or two files
share a match, 2 refused because a note names Betfair as the odds source (Golden Rulebook rule 3). Nothing is
written on any non-zero exit.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper.importers.aussportsbetting import (  # noqa: E402
    SOURCE,
    SOURCE_PAGE,
    SPREADSHEET_SUFFIXES,
    ParseResult,
    parse_workbook,
)
from training import historical_odds as ho  # noqa: E402

DEFAULT_INCOMING = ROOT / "data" / "historical_odds" / "incoming" / SOURCE
DEFAULT_ROOT = ROOT / "data" / "historical_odds"
SHOW_SKIPPED = 20


def _spreadsheets(folder: Path) -> list[Path]:
    """Spreadsheet files in the folder, skipping Excel's ``~$`` lock files."""
    return sorted(
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in SPREADSHEET_SUFFIXES and not path.name.startswith("~$")
    )


def _provenance(parsed: list[ParseResult]) -> str:
    """Where the prices come from, in the file's own words: the page, and the notes above each header."""
    notes = [f"{item.path.name}: {note}" for item in parsed for note in item.notes]
    listed = " | ".join(notes) if notes else "none"
    return f"aussportsbetting.com historical file(s) saved by hand from {SOURCE_PAGE}. Notes in the file: {listed}"


def _totals(parsed: list[ParseResult]) -> Counter:
    totals: Counter = Counter()
    for item in parsed:
        totals.update(item.counts)
    return totals


def _manifest_extra(parsed: list[ParseResult]) -> dict[str, Any]:
    return {
        "import": {
            "script": "scripts/import_aussportsbetting.py",
            "page": SOURCE_PAGE,
            "files": [
                {
                    "name": item.path.name,
                    "competition": item.competition,
                    "gender": item.gender,
                    "records": len(item.records),
                    "events": item.events,
                    "skipped": len(item.skipped),
                    "plain_odds_kind": item.plain_odds_kind,
                }
                for item in parsed
            ],
            "counts": dict(sorted(_totals(parsed).items())),
            "unknown_columns": sorted({name for item in parsed for name in item.unknown_columns}),
            "teams": sorted({name for item in parsed for name in item.teams}),
            "warnings": [f"{item.path.name}: {text}" for item in parsed for text in item.warnings],
        }
    }


def _print_report(parsed: list[ParseResult]) -> None:
    for item in parsed:
        print(f"\n== {item.path.name}: {item.competition} ({item.gender})")
        print(f"   records {len(item.records)} for {item.events} events; skipped {len(item.skipped)}")
        print(f"   counts: {dict(sorted(item.counts.items()))}")
        print(f"   plain Home/Away Odds recorded as: {item.plain_odds_kind}")
        if item.unknown_columns:
            print(f"   unknown columns: {', '.join(item.unknown_columns)}")
        if item.notes:
            print("   notes above the header:")
            for note in item.notes:
                print(f"     - {note}")
        for text in item.warnings:
            print(f"   WARNING: {text}")
        for row in item.skipped[:SHOW_SKIPPED]:
            print(f"   skipped sheet {row['sheet']!r} row {row['row']}: {row['reason']}")
        if len(item.skipped) > SHOW_SKIPPED:
            print(f"   ... and {len(item.skipped) - SHOW_SKIPPED} more skipped rows")
        print(f"   team names: {', '.join(item.teams) if item.teams else 'none'}")


def _betfair_notes(parsed: list[ParseResult]) -> list[str]:
    return [f"{item.path.name}: {note}" for item in parsed for note in item.notes if ho.is_betfair(note)]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Import the aussportsbetting historical odds file(s) (S99).")
    parser.add_argument("--incoming", default=str(DEFAULT_INCOMING), help="folder with the saved file(s)")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="odds store root (data/historical_odds)")
    parser.add_argument("--dry-run", action="store_true", help="parse and print only; write nothing")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    incoming = Path(args.incoming)
    root = Path(args.root)
    if not incoming.is_dir():
        print(f"no folder {incoming}; create it and save the aussportsbetting file there")
        return 1
    files = _spreadsheets(incoming)
    if not files:
        print(f"no .xlsx, .xls or .csv file in {incoming}; save the aussportsbetting file there first")
        return 1

    parsed: list[ParseResult] = []
    for path in files:
        try:
            parsed.append(parse_workbook(path))
        except Exception as exc:  # any unreadable file stops the import; nothing is written
            print(f"cannot read {path.name}: {type(exc).__name__}: {exc}")
            return 1
    _print_report(parsed)

    owner: dict[str, str] = {}
    records: list[ho.OddsRecord] = []
    for item in parsed:
        for event in sorted({record.source_event_id for record in item.records}):
            if event in owner and owner[event] != item.path.name:
                print(f"\nevent {event} is in both {owner[event]} and {item.path.name}; fix the files and re-run")
                return 1
            owner[event] = item.path.name
        records.extend(item.records)
    if not records:
        print("\nno usable records; nothing written")
        return 1

    provenance = _provenance(parsed)
    if ho.is_betfair(provenance) or any(ho.is_betfair(record.bookmaker) for record in records):
        print("\nREFUSED: a note in the file names Betfair as the odds source. Betfair data is never used")
        print("(Golden Rulebook rule 3). Nothing was written. The note(s):")
        for note in _betfair_notes(parsed) or [provenance]:
            print(f"  - {note}")
        return 2

    events = len({record.source_event_id for record in records})
    folder = root / SOURCE
    if args.dry_run:
        print(f"\ndry run: would write {len(records)} records for {events} events to {folder}; nothing written")
        return 0

    try:
        ho.write_store(
            SOURCE,
            records,
            provenance=provenance,
            inputs=files,
            extra=_manifest_extra(parsed),
            root=root,
        )
    except ho.ForbiddenSourceError as exc:
        print(f"\nREFUSED by the store: {exc}. Nothing was written.")
        return 2
    print(f"\nwrote {len(records)} records for {events} events to {folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
