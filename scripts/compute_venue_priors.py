"""S25: compute venue par and toss priors from raw Cricsheet files (as-of, no lookahead).

Input is raw Cricsheet only: ``data/raw/*/*.json`` and ``data/incoming_cricsheet/*.json``. The synthetic
``data/specialists/corpus_*.json.gz`` files are never read.

Default output is ``data/ratings/venue_priors.json``, a full-period convenience snapshot. Backtests must query
``shared.venue_priors.VenuePriors.get(..., as_of)`` instead. ``--to-db`` additionally upserts into TimescaleDB
table ``venue_priors``; it is off by default.

Usage::

    python -m scripts.compute_venue_priors --limit 100 --dry-run
    python -m scripts.compute_venue_priors --limit 100 --out <scratch>/sample.json
    python -m scripts.compute_venue_priors                      # full run, writes data/ratings/venue_priors.json
    python -m scripts.compute_venue_priors --to-db              # also writes the venue_priors table
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from shared.logging import setup_logging
from shared.venue_priors import MIN_SAMPLE, VenuePriors

logger = setup_logging("compute_venue_priors")

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "ratings" / "venue_priors.json"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS venue_priors (
    venue_name VARCHAR(150) NOT NULL,
    format_category VARCHAR(20) NOT NULL,
    sample_matches INT NOT NULL,
    avg_first_innings_score DOUBLE PRECISION NOT NULL,
    avg_par_run_rate DOUBLE PRECISION NOT NULL,
    chase_win_pct DOUBLE PRECISION NOT NULL,
    toss_win_match_win_pct DOUBLE PRECISION NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (venue_name, format_category)
)
"""

_UPSERT = """
INSERT INTO venue_priors (
    venue_name, format_category, sample_matches, avg_first_innings_score,
    avg_par_run_rate, chase_win_pct, toss_win_match_win_pct, updated_at
) VALUES (
    :venue_name, :format_category, :sample_matches, :par, :par_rr, :chase, :toss, :updated_at
)
ON CONFLICT (venue_name, format_category) DO UPDATE SET
    sample_matches = EXCLUDED.sample_matches,
    avg_first_innings_score = EXCLUDED.avg_first_innings_score,
    avg_par_run_rate = EXCLUDED.avg_par_run_rate,
    chase_win_pct = EXCLUDED.chase_win_pct,
    toss_win_match_win_pct = EXCLUDED.toss_win_match_win_pct,
    updated_at = EXCLUDED.updated_at
"""


def _collect_files(limit: Optional[int]) -> List[Path]:
    """Sorted raw files. With ``limit`` an evenly spaced sample is taken, so it spans all formats."""
    files = sorted((ROOT / "data" / "raw").glob("*/*.json"))
    files += sorted((ROOT / "data" / "incoming_cricsheet").glob("*.json"))
    if limit and limit < len(files):
        step = max(1, len(files) // limit)
        files = files[::step][:limit]
    return files


def _load(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        logger.warning("venue_priors_bad_file", file=path.name, error=str(exc))
        return None


def _db_rows(snapshot: Dict[str, Any], updated_at: datetime) -> List[Dict[str, Any]]:
    """Rows for the table: venue-level entries with complete metrics. City-split entries are named ``venue, city``."""
    rows: List[Dict[str, Any]] = []
    for entry in snapshot["venues"].values():
        metrics = (entry["par_score"], entry["par_rr"], entry["chase_win_pct"], entry["toss_winner_win_pct"])
        if any(m is None for m in metrics):
            continue
        name = entry["venue"] if not entry["city"] else f"{entry['venue']}, {entry['city']}"
        rows.append(
            {
                "venue_name": name[:150],
                "format_category": f"{entry['family']}-{entry['gender']}"[:20],
                "sample_matches": int(entry["sample_size"]),
                "par": float(entry["par_score"]),
                "par_rr": float(entry["par_rr"]),
                "chase": float(entry["chase_win_pct"]),
                "toss": float(entry["toss_winner_win_pct"]),
                "updated_at": updated_at,
            }
        )
    return rows


async def _write_db(rows: List[Dict[str, Any]]) -> int:
    from sqlalchemy import text

    from shared.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text(_CREATE_TABLE))
        for row in rows:
            await session.execute(text(_UPSERT), row)
        await session.commit()
    return len(rows)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None, help="use an evenly spaced sample of N raw files")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="snapshot JSON path")
    parser.add_argument("--dry-run", action="store_true", help="compute and summarise; write nothing")
    parser.add_argument("--to-db", action="store_true", help="also upsert into TimescaleDB venue_priors")
    args = parser.parse_args(argv)

    started = time.perf_counter()
    files = _collect_files(args.limit)
    matches = [m for m in (_load(p) for p in files) if m is not None]
    priors = VenuePriors.build(matches)
    snapshot = priors.snapshot()
    elapsed = round(time.perf_counter() - started, 2)

    venues = list(snapshot["venues"].values())
    met = sum(1 for v in venues if v["min_sample_met"])
    logger.info(
        "venue_priors_built",
        files=len(files),
        matches_seen=priors.matches_seen,
        matches_used=priors.matches_used,
        venue_groups=len(venues),
        venue_groups_min_sample=met,
        last_match_date=priors.last_match_date,
        min_sample=MIN_SAMPLE,
        seconds=elapsed,
    )

    if args.dry_run:
        logger.info("venue_priors_dry_run", wrote=False, out=str(args.out))
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(snapshot, indent=1, sort_keys=True), encoding="utf-8")
    logger.info("venue_priors_written", out=str(args.out), venue_groups=len(venues))

    if args.to_db:
        rows = _db_rows(snapshot, datetime.now(timezone.utc))
        written = asyncio.run(_write_db(rows))
        logger.info("venue_priors_db_upserted", rows=written)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
