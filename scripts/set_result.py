"""S76: write a match result by hand (from a public scorecard), then settle that match's paper bets.

Usage::

    python scripts/set_result.py --match lb_36262294 --winner "Melbourne Stars W" --margin "15 runs" \
        --source public_scorecard --url https://example.com/scorecard
    python scripts/set_result.py --match lb_76109046 --tie
    python scripts/set_result.py --match lb_76109046 --no-result
    add --dry-run to see what would be written and settled; the transaction is rolled back and nothing is kept
    add --force to replace a trusted result (win, tie, no_result or abandoned); refused when paper bets on the match
    are already settled against the old result

The winner must be one of the registry's team_home or team_away (case and spacing are ignored; the registry spelling
is written). The row has the shape the result detector writes to ``match_results``. Settlement uses
``virtual_trading.paper_ledger.settle``, the same function the trader runs every cycle, so the trader would settle
the same bets on its next cycle. Each run appends one line to ``logs/set_result.jsonl`` (not on a dry run).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select, text  # noqa: E402

from backend.models.paper_bets import PaperBet  # noqa: E402
from backend.models.results import MatchResultRecord  # noqa: E402
from scripts.results_reconciliation import has_decided_result  # noqa: E402
from virtual_trading import paper_ledger as ledger  # noqa: E402

PUBLIC_SOURCES = ("public_scorecard", "manual_fix")
SETTLED_BET_STATUSES = ("settled", "void")
AUDIT_PATH = ROOT / "logs" / "set_result.jsonl"

REGISTRY_SQL = text(
    "SELECT match_id, team_home, team_away, competition, is_simulated "
    "FROM match_training_status WHERE match_id = :match_id"
)
EXISTING_SQL = text(
    "SELECT match_id, winner, loser, result_type, margin, source, completed_at "
    "FROM match_results WHERE match_id = :match_id"
)
CLOSING_SQL = text(
    "SELECT back_home, lay_home, back_away, lay_away FROM odds_ticks "
    "WHERE match_id = :match_id ORDER BY time DESC, tick_seq DESC LIMIT 1"
)


class ResultError(ValueError):
    """A refusal. Nothing is written when this is raised."""


class OverwriteRefused(ResultError):
    """A refusal to replace a trusted result (S91: the API answers 409 for it, and 422 for other refusals)."""


@dataclass(frozen=True)
class Request:
    match_id: str
    result_type: str  # win, tie or no_result
    winner: Optional[str]
    margin: str
    source: str
    url: Optional[str]
    force: bool
    dry_run: bool


def _norm(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def build_request(args: argparse.Namespace) -> Request:
    """Validate the command line (no database). Raises ResultError."""
    match_id = (args.match or "").strip()
    if not match_id:
        raise ResultError("--match is required")
    chosen = [args.winner is not None, bool(args.tie), bool(args.no_result)]
    if sum(chosen) != 1:
        raise ResultError("give exactly one of --winner, --tie or --no-result")
    if args.winner is not None and not args.winner.strip():
        raise ResultError("--winner is empty")
    if (args.tie or args.no_result) and args.margin:
        raise ResultError("--margin applies only to --winner")
    if args.source not in PUBLIC_SOURCES:
        raise ResultError(f"--source must be one of {', '.join(PUBLIC_SOURCES)}")
    if args.url and not args.url.startswith(("http://", "https://")):
        raise ResultError("--url must start with http:// or https://")
    if args.winner is not None:
        result_type = "win"
    else:
        result_type = "tie" if args.tie else "no_result"
    return Request(
        match_id=match_id,
        result_type=result_type,
        winner=args.winner.strip() if args.winner is not None else None,
        margin=(args.margin or "").strip() if result_type == "win" else "",
        source=args.source,
        url=args.url,
        force=bool(args.force),
        dry_run=bool(args.dry_run),
    )


def canonical_winner(winner: Optional[str], team_home: str, team_away: str) -> str:
    """The registry spelling of the winning team. Case and runs of spaces are ignored; anything else is refused."""
    key = _norm(winner)
    hits = [name for name in (team_home, team_away) if name and _norm(name) == key]
    if len(hits) != 1:
        raise ResultError(f"winner {winner!r} is neither {team_home!r} nor {team_away!r} in the registry")
    return hits[0]


def describe(existing: Mapping[str, Any]) -> str:
    return (f"result_type {existing.get('result_type')}, winner {existing.get('winner') or '-'}, "
            f"source {existing.get('source')}")


def check_overwrite(existing: Optional[Mapping[str, Any]], force: bool, settled_bets: int, match_id: str) -> None:
    """Refuse to replace a trusted result (win, tie, no_result, abandoned) without --force, and never when paper
    bets on the match are already settled against it: a replacement would leave them on the old outcome."""
    if existing is None or not has_decided_result(existing):
        return
    if not force:
        raise OverwriteRefused(f"{match_id} already has a trusted result ({describe(existing)}); pass --force to replace it")
    if settled_bets:
        raise OverwriteRefused(
            f"{settled_bets} paper bet(s) on {match_id} are already settled against the existing result; a "
            "replacement would not re-settle them. Correct those bets by hand first."
        )


def build_row(
    request: Request, registry: Mapping[str, Any], now: datetime, closing: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    """The match_results row, in the shape scraper/lotus_result_detector.py writes."""
    home = registry["team_home"] or ""
    away = registry["team_away"] or ""
    if request.result_type == "win":
        winner = canonical_winner(request.winner, home, away)
        loser = away if winner == home else home
    else:
        winner = loser = ""
    closing = closing or {}
    return {
        "match_id": request.match_id,
        "completed_at": now,
        "winner": winner,
        "loser": loser,
        "result_type": request.result_type,
        "margin": request.margin,
        "team_home": home,
        "team_away": away,
        "source": request.source,
        "closing_back_home": closing.get("back_home"),
        "closing_back_away": closing.get("back_away"),
        "closing_lay_home": closing.get("lay_home"),
        "closing_lay_away": closing.get("lay_away"),
    }


async def _settled_bet_count(session: Any, match_id: str) -> int:
    stmt = select(PaperBet).where(PaperBet.match_id == match_id, PaperBet.status.in_(SETTLED_BET_STATUSES))
    return len((await session.execute(stmt)).scalars().all())


async def apply_result(session: Any, request: Request, *, now: datetime) -> dict[str, Any]:
    """Validate, write the match_results row and settle the match's filled paper bets. The caller commits or rolls
    back. Returns a summary for printing and the audit log."""
    registry_row = (await session.execute(REGISTRY_SQL, {"match_id": request.match_id})).mappings().first()
    if registry_row is None:
        raise ResultError(f"{request.match_id} has no row in match_training_status; the winner cannot be validated")
    registry = dict(registry_row)

    existing_row = (await session.execute(EXISTING_SQL, {"match_id": request.match_id})).mappings().first()
    existing = dict(existing_row) if existing_row is not None else None
    settled_bets = 0
    if existing is not None and has_decided_result(existing):
        settled_bets = await _settled_bet_count(session, request.match_id)
    check_overwrite(existing, request.force, settled_bets, request.match_id)

    closing_row = (await session.execute(CLOSING_SQL, {"match_id": request.match_id})).mappings().first()
    row = build_row(request, registry, now, dict(closing_row) if closing_row is not None else None)

    await session.merge(MatchResultRecord(**row))
    await session.flush()
    changed = await ledger.settle(session, {request.match_id: row}, now)
    counts = Counter(bet.status for bet in changed)
    return {
        "match_id": request.match_id,
        "team_home": row["team_home"],
        "team_away": row["team_away"],
        "competition": registry.get("competition") or "",
        "is_simulated": bool(registry.get("is_simulated")),
        "written": {
            "result_type": row["result_type"],
            "winner": row["winner"],
            "loser": row["loser"],
            "margin": row["margin"],
            "source": row["source"],
        },
        "replaced": describe(existing) if existing is not None else None,
        "bets_settled": dict(counts),
        "url": request.url,
        "dry_run": request.dry_run,
    }


async def run(request: Request, *, now: datetime, session_factory: Any = None) -> dict[str, Any]:
    """Open a session, apply, then commit (or roll back on a dry run). ``session_factory`` is injectable for tests."""
    if session_factory is None:
        from shared.db import get_session_factory

        session_factory = get_session_factory()
    async with session_factory() as session:
        summary = await apply_result(session, request, now=now)
        if request.dry_run:
            await session.rollback()
        else:
            await session.commit()
    return summary


def render_summary(summary: Mapping[str, Any]) -> str:
    written = summary["written"]
    if written["result_type"] == "win":
        outcome = f"win for {written['winner']}" + (f" by {written['margin']}" if written["margin"] else "")
    else:
        outcome = written["result_type"].replace("_", " ")
    settled = summary["bets_settled"]
    settled_text = ", ".join(f"{count} {status}" for status, count in sorted(settled.items())) or "none"
    lines = [
        f"{summary['team_home']} v {summary['team_away']} ({summary['match_id']}"
        f"{', ' + summary['competition'] if summary['competition'] else ''})",
        f"result: {outcome}; source {written['source']}" + (f"; url {summary['url']}" if summary["url"] else ""),
        f"previous: {summary['replaced'] or 'none'}",
        f"paper bets settled now: {settled_text}",
    ]
    if summary["is_simulated"]:
        lines.append("note: this match is simulated (SRL); the result is recorded but no live consumer uses it")
    if summary["dry_run"]:
        lines.append("dry run: the transaction was rolled back; nothing was written")
    return "\n".join(lines)


def _audit(summary: Mapping[str, Any], request: Request, now: datetime) -> None:
    record = {
        "ts": now.isoformat(),
        "match_id": request.match_id,
        "written": summary["written"],
        "replaced": summary["replaced"],
        "force": request.force,
        "url": request.url,
        "bets_settled": summary["bets_settled"],
    }
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except OSError as exc:
        print(f"could not write {AUDIT_PATH}: {exc}", file=sys.stderr)


async def _main(argv: Optional[list[str]]) -> int:
    parser = _parser()
    request = build_request(parser.parse_args(argv))
    now = datetime.now(timezone.utc)
    from shared.db import close_database

    try:
        summary = await run(request, now=now)
    finally:
        await close_database()
    if not request.dry_run:
        _audit(summary, request, now)
    print(render_summary(summary))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="S76 write a match result by hand and settle its paper bets.")
    parser.add_argument("--match", required=True, help="match id, e.g. lb_36262294")
    parser.add_argument("--winner", help="winning team, as in the registry (case-insensitive)")
    parser.add_argument("--tie", action="store_true", help="the match tied (bets void)")
    parser.add_argument("--no-result", dest="no_result", action="store_true", help="no result (bets void)")
    parser.add_argument("--margin", help='margin text, e.g. "15 runs" or "6 wickets" (with --winner)')
    parser.add_argument("--source", default="public_scorecard", help="public_scorecard (default) or manual_fix")
    parser.add_argument("--url", help="the scorecard the result came from (logged, not stored in match_results)")
    parser.add_argument("--force", action="store_true", help="replace a trusted existing result")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="show what would be written and settled; roll back")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    try:
        return asyncio.run(_main(argv))
    except ResultError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
