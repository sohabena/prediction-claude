"""S24: in-house chronological Cricket Elo over raw Cricsheet files.

Reads ``data/raw/{ipl,odis,t20s,tests}/*.json`` and ``data/incoming_cricsheet/*.json``
(the synthetic ``data/specialists`` corpora are never read), orders the matches by
``info.dates[0]`` and runs :class:`shared.elo.CricketEloTracker` over them.

Outputs (default ``data/ratings/``, gitignored):
  * ``team_elo_latest.json``      current rating per (team, family, gender)
  * ``team_elo_history.json.gz``  pre/post rating per team per match (for backtests)

``--to-db`` additionally writes to the TimescaleDB table ``team_elo_ratings``. It is off
by default, and ``--dry-run`` computes everything without writing files.

Usage (from the repository root)::

    python -m scripts.compute_cricsheet_elo --limit 100 --dry-run
    python -m scripts.compute_cricsheet_elo
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

from scripts.cricsheet_harmonizer import canonicalize_team
from shared.elo import GENDERS, CricketEloTracker, family_for_match_type
from shared.logging import setup_logging

logger = setup_logging("compute_cricsheet_elo")

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW_DIR = REPO_ROOT / "data" / "raw"
DEFAULT_INCOMING_DIR = REPO_ROOT / "data" / "incoming_cricsheet"
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "ratings"
RAW_SUBDIRS: tuple[str, ...] = ("ipl", "odis", "t20s", "tests")
TOP_N = 5

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS team_elo_ratings (
    team_name VARCHAR(100) NOT NULL,
    format_category VARCHAR(20) NOT NULL,
    match_date TIMESTAMPTZ NOT NULL,
    match_id VARCHAR(50) NOT NULL,
    pre_match_elo DOUBLE PRECISION NOT NULL,
    post_match_elo DOUBLE PRECISION NOT NULL,
    opponent VARCHAR(100) NOT NULL,
    is_winner BOOLEAN,
    PRIMARY KEY (team_name, format_category, match_date, match_id)
)
"""

INSERT_SQL = """
INSERT INTO team_elo_ratings
    (team_name, format_category, match_date, match_id, pre_match_elo, post_match_elo, opponent, is_winner)
VALUES
    (:team_name, :format_category, :match_date, :match_id, :pre, :post, :opponent, :is_winner)
ON CONFLICT (team_name, format_category, match_date, match_id) DO NOTHING
"""


def normalize_team(name: Any) -> str:
    """Canonical team name; unknown names are kept (stripped), never dropped."""
    return canonicalize_team(str(name)) or str(name).strip()


@dataclass
class MatchRecord:
    """One Cricsheet file ready for the tracker, or the reason it was skipped."""

    match_id: str
    date: str
    source: str
    match_info: Optional[dict[str, Any]] = None
    skip_reason: Optional[str] = None


@dataclass
class RunResult:
    tracker: CricketEloTracker
    files_seen: int = 0
    selected: int = 0
    processed: int = 0
    skipped: Counter = field(default_factory=Counter)
    processed_by_group: Counter = field(default_factory=Counter)


def discover_files(raw_dir: Path, incoming_dir: Path) -> list[Path]:
    """Raw subdirectories first, then incoming. Order is stable for duplicate handling."""
    paths: list[Path] = []
    for sub in RAW_SUBDIRS:
        paths.extend(sorted((raw_dir / sub).glob("*.json")))
    if incoming_dir.is_dir():
        paths.extend(sorted(incoming_dir.glob("*.json")))
    return paths


def classify_info(match_id: str, info: dict[str, Any], source: str) -> MatchRecord:
    """Turn a Cricsheet ``info`` block into a tracker input, or a skip reason."""
    dates = info.get("dates") or []
    if not dates:
        return MatchRecord(match_id, "", source, skip_reason="no_date")
    match_date = str(dates[0])[:10]

    if family_for_match_type(info.get("match_type")) is None:
        return MatchRecord(match_id, match_date, source, skip_reason="unsupported_match_type")

    gender = str(info.get("gender") or "").strip().lower()
    if gender not in GENDERS:
        return MatchRecord(match_id, match_date, source, skip_reason="unknown_gender")

    teams = [normalize_team(t) for t in (info.get("teams") or [])]
    if len(teams) != 2 or teams[0] == teams[1]:
        return MatchRecord(match_id, match_date, source, skip_reason="bad_teams")

    outcome = info.get("outcome") or {}
    winner = outcome.get("winner")
    if winner and normalize_team(winner) not in teams:
        return MatchRecord(match_id, match_date, source, skip_reason="winner_not_in_teams")
    if not winner and outcome.get("result") not in ("tie", "draw"):
        return MatchRecord(match_id, match_date, source, skip_reason="no_result")

    match_info = {
        "match_id": match_id,
        "dates": dates,
        "teams": list(info.get("teams") or []),
        "match_type": info.get("match_type"),
        "gender": gender,
        "outcome": outcome,
    }
    return MatchRecord(match_id, match_date, source, match_info=match_info)


def load_records(paths: Sequence[Path]) -> tuple[list[MatchRecord], Counter]:
    """Parse every file once. The first file for a match_id wins; later copies are skipped."""
    records: list[MatchRecord] = []
    skipped: Counter = Counter()
    seen: set[str] = set()
    for path in paths:
        match_id = path.stem
        if match_id in seen:
            skipped["duplicate_match_id"] += 1
            continue
        seen.add(match_id)
        try:
            with path.open(encoding="utf-8") as fh:
                info = json.load(fh)["info"]
        except (OSError, ValueError, KeyError, TypeError):
            skipped["unreadable"] += 1
            continue
        records.append(classify_info(match_id, info, str(path)))
    return records, skipped


def run_elo(records: Sequence[MatchRecord], limit: Optional[int] = None) -> RunResult:
    """Run the tracker over matches sorted by (date, match_id). ``limit`` keeps the earliest N."""
    ordered = sorted(records, key=lambda r: (r.date, r.match_id))
    if limit is not None:
        ordered = ordered[:limit]
    result = RunResult(tracker=CricketEloTracker(normalize_team=normalize_team), selected=len(ordered))
    for rec in ordered:
        if rec.skip_reason is not None or rec.match_info is None:
            result.skipped[rec.skip_reason or "unknown"] += 1
            continue
        try:
            changed = result.tracker.update(rec.match_info)
        except ValueError as exc:
            result.skipped["rejected_by_tracker"] += 1
            logger.warning("elo_match_rejected", match_id=rec.match_id, error=str(exc))
            continue
        if not changed:
            result.skipped["no_result"] += 1
            continue
        result.processed += 1
        family = family_for_match_type(rec.match_info["match_type"])
        result.processed_by_group[(family, rec.match_info["gender"])] += 1
    return result


def top_teams(tracker: CricketEloTracker, top_n: int = TOP_N) -> dict[str, list[dict[str, Any]]]:
    """Top teams per ``family|gender`` by current Elo."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in tracker.current_ratings():
        groups[f"{row['family']}|{row['gender']}"].append(row)
    return {key: rows[:top_n] for key, rows in sorted(groups.items())}


def write_outputs(result: RunResult, out_dir: Path) -> dict[str, str]:
    """Write the latest-ratings JSON and the history archive."""
    out_dir.mkdir(parents=True, exist_ok=True)
    latest_path = out_dir / "team_elo_latest.json"
    history_path = out_dir / "team_elo_history.json.gz"
    latest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base": result.tracker.base,
        "k_by_family": result.tracker.k_by_family,
        "matches_processed": result.processed,
        "ratings": result.tracker.current_ratings(),
    }
    latest_path.write_text(json.dumps(latest, indent=2), encoding="utf-8")
    result.tracker.save(history_path)
    return {"latest": str(latest_path), "history": str(history_path)}


async def write_to_db(tracker: CricketEloTracker) -> int:
    """Insert history rows into ``team_elo_ratings``. ``format_category`` is ``<family>_<gender>``."""
    from sqlalchemy import text

    from shared.db import get_session

    rows = [
        {
            "team_name": rec["team"],
            "format_category": f"{rec['family']}_{rec['gender']}",
            "match_date": datetime.fromisoformat(rec["date"]).replace(tzinfo=timezone.utc),
            "match_id": rec["match_id"],
            "pre": rec["pre"],
            "post": rec["post"],
            "opponent": rec["opponent"],
            "is_winner": rec["is_winner"],
        }
        for rec in tracker.history()
    ]
    async with get_session() as session:
        await session.execute(text(CREATE_TABLE_SQL))
        chunk = 5000
        for start in range(0, len(rows), chunk):
            await session.execute(text(INSERT_SQL), rows[start : start + chunk])
        await session.commit()
    return len(rows)


def print_summary(result: RunResult, top: dict[str, list[dict[str, Any]]]) -> None:
    print(f"files_seen={result.files_seen} selected={result.selected} processed={result.processed}")
    print(f"skipped_total={sum(result.skipped.values())} by_reason={dict(sorted(result.skipped.items()))}")
    print("processed_by_group=" + json.dumps({f"{f}|{g}": n for (f, g), n in sorted(result.processed_by_group.items())}))
    for key, rows in top.items():
        formatted = ", ".join(f"{r['team']} {r['elo']:.1f}" for r in rows)
        print(f"top{TOP_N} {key}: {formatted}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Chronological Cricket Elo over raw Cricsheet files (S24).")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--incoming-dir", type=Path, default=DEFAULT_INCOMING_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--limit", type=int, default=None, help="process only the earliest N matches")
    parser.add_argument("--dry-run", action="store_true", help="compute and print, write no files")
    parser.add_argument("--to-db", action="store_true", help="also insert into team_elo_ratings")
    args = parser.parse_args(argv)

    paths = discover_files(args.raw_dir, args.incoming_dir)
    records, load_skips = load_records(paths)
    result = run_elo(records, limit=args.limit)
    result.files_seen = len(paths)
    result.skipped.update(load_skips)

    top = top_teams(result.tracker)
    print_summary(result, top)
    logger.info(
        "elo_run_complete",
        files_seen=result.files_seen,
        processed=result.processed,
        skipped=dict(result.skipped),
        dry_run=args.dry_run,
    )

    if args.dry_run:
        print("dry-run: no files written")
    else:
        paths_written = write_outputs(result, args.out_dir)
        print(f"wrote {paths_written['latest']} and {paths_written['history']}")

    if args.to_db:
        rows = asyncio.run(write_to_db(result.tracker))
        print(f"inserted up to {rows} rows into team_elo_ratings (ON CONFLICT DO NOTHING)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
