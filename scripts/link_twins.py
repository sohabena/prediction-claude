"""S96 one fixture, one match: link twin event ids and copy a decided result between them.

LotusBook can list one real fixture under two ``lb_`` event ids (2026-11-03: Otago Volts v Canterbury Kings, lb_36266991
on the livescore-v2 score feed and lb_75330686 on the legacy one). A **twin** is a match whose ``canonical_match_id``
names the canonical match of its fixture; the canonical match and every ordinary match keep it NULL. Consumers skip
the twins: paper trading, the training dataset, the missing-results list and the monitor's results check.

Twin rule (both ids ``lb_``, the same simulated flag, the same teams after the name normalisation, home and away may be
swapped, the same competition when both are known, starts within 30 min; the actual start when known, else the
scheduled one). Canonical: the twin with the score feed (``score_pipeline = 'livescore-v2'``), else the one with more
match-page rows, else the lower id.

Result copy: a decided result (win, tie, no_result, abandoned) of one twin is copied to the other when the other has no
result or only ``unresolved``, and never over a decided or hand-entered result. The copy's source is ``twin:<match_id>``.
Two decided results that disagree are reported as a conflict and never touched.

The pure core (``build_plan``) takes registry rows and returns a plan; it does no I/O. The database layer runs
SELECTs, and ``apply_plan`` writes only ``match_training_status.canonical_match_id`` and ``match_results`` rows for
copies. Writes go through ``--apply`` (or the monitor's ``--quarantine`` harmonise step), never through the dry run.

Usage::

    python scripts/link_twins.py            # dry run (default): prints pairs, canonical choices and copies
    python scripts/link_twins.py --apply    # write canonical_match_id and copied results (needs the S96 migration)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper.match_filter import is_simulated_name  # noqa: E402
from scripts.results_reconciliation import has_decided_result  # noqa: E402
from shared.naming import canonical_team_name  # noqa: E402

ID_PREFIX = "lb_"
TWIN_WINDOW = timedelta(minutes=30)
PIPELINE_V2 = "livescore-v2"
UNRESOLVED = "unresolved"
# Hand-entered result sources: never overwritten by a copy. Same values as scraper.lotus_result_detector.MANUAL_SOURCES
# (a test keeps them equal; this module does not import the detector, which pulls in Redis).
HAND_ENTERED_SOURCES = ("public_scorecard", "manual_fix")
COPY_SOURCE_PREFIX = "twin:"

# Registry rows of the lb_ matches, with the match-page row count and the stored result. Optional columns are read
# through to_jsonb, so the query runs on a database without the S86 or S96 migration (the value is NULL there).
# Read-only; no parameters.
TWIN_ROWS_SQL = """
    SELECT s.match_id, s.team_home, s.team_away, s.competition, s.is_simulated,
           s.match_start_time,
           NULLIF(to_jsonb(s) ->> 'actual_start_time', '')::timestamptz AS actual_start_time,
           s.score_pipeline,
           NULLIF(to_jsonb(s) ->> 'canonical_match_id', '') AS canonical_match_id,
           COALESCE(pages.n, 0) AS match_page_rows,
           r.result_type, r.winner, r.loser, r.margin, r.completed_at, r.source AS result_source
    FROM match_training_status s
    LEFT JOIN match_results r ON r.match_id = s.match_id
    LEFT JOIN (SELECT match_id, COUNT(*) AS n FROM market_ticks GROUP BY match_id) pages
           ON pages.match_id = s.match_id
    WHERE left(s.match_id, 3) = 'lb_'
"""

CANONICAL_COLUMN_SQL = """
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'match_training_status' AND column_name = 'canonical_match_id'
    ) AS present
"""

UPDATE_CANONICAL_SQL = """
    UPDATE match_training_status
       SET canonical_match_id = :canonical
     WHERE match_id = :match_id AND canonical_match_id IS DISTINCT FROM :canonical
"""

# A copy fills a missing row or replaces an unresolved one that no hand entry owns. The WHERE makes the write refuse a
# decided or hand-entered result even if the plan was built on older data.
COPY_RESULT_SQL = """
    INSERT INTO match_results (match_id, completed_at, winner, loser, result_type, margin, team_home, team_away, source)
    VALUES (:match_id, :completed_at, :winner, :loser, :result_type, :margin, :team_home, :team_away, :source)
    ON CONFLICT (match_id) DO UPDATE SET
        completed_at = EXCLUDED.completed_at,
        winner = EXCLUDED.winner,
        loser = EXCLUDED.loser,
        result_type = EXCLUDED.result_type,
        margin = EXCLUDED.margin,
        source = EXCLUDED.source
    WHERE match_results.result_type = 'unresolved'
      AND COALESCE(match_results.source, '') NOT IN ('public_scorecard', 'manual_fix')
"""


class MigrationMissing(RuntimeError):
    """--apply needs the S96 column; the dry run does not."""


# --------------------------------------------------------------------------- pure rules
@dataclass(frozen=True)
class TwinLink:
    canonical_id: str
    twin_id: str


@dataclass(frozen=True)
class CanonicalUpdate:
    match_id: str
    canonical_id: Optional[str]  # None: this row must carry no link (a canonical match or an ordinary one)


@dataclass(frozen=True)
class ResultCopy:
    target_id: str
    source_id: str
    row: Mapping[str, Any]  # the match_results values to write on the target


@dataclass(frozen=True)
class ResultConflict:
    canonical_id: str
    twin_id: str
    detail: str


@dataclass
class Plan:
    links: list[TwinLink] = field(default_factory=list)
    desired: dict[str, Optional[str]] = field(default_factory=dict)  # match id -> canonical id (None: canonical)
    updates: list[CanonicalUpdate] = field(default_factory=list)  # rows whose stored canonical differs
    copies: list[ResultCopy] = field(default_factory=list)
    conflicts: list[ResultConflict] = field(default_factory=list)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _team_key(name: Any) -> str:
    """Case-, space- and SRL-insensitive team name (the existing normalisation, then casefold)."""
    return canonical_team_name(name).casefold()


def _team_pair(row: Mapping[str, Any]) -> Optional[tuple[str, str]]:
    """The unordered pair of team keys, so a swapped home and away compare equal; None when a name is empty."""
    home, away = _team_key(row.get("team_home")), _team_key(row.get("team_away"))
    if not home or not away:
        return None
    return tuple(sorted((home, away)))  # type: ignore[return-value]


def _comp_key(row: Mapping[str, Any]) -> str:
    return " ".join(str(row.get("competition") or "").split()).casefold()


def _simulated(row: Mapping[str, Any]) -> bool:
    return bool(row.get("is_simulated")) or is_simulated_name(
        row.get("competition"), row.get("team_home"), row.get("team_away"),
    )


def _start(row: Mapping[str, Any]) -> Optional[datetime]:
    value = row.get("actual_start_time") or row.get("match_start_time")
    return _aware(value) if isinstance(value, datetime) else None


def is_twin(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """The S96 twin rule for two registry rows (symmetric)."""
    id_a, id_b = str(a["match_id"]), str(b["match_id"])
    if id_a == id_b or not (id_a.startswith(ID_PREFIX) and id_b.startswith(ID_PREFIX)):
        return False
    if _simulated(a) != _simulated(b):
        return False  # a simulated (SRL) match is never the twin of a real one
    pair_a, pair_b = _team_pair(a), _team_pair(b)
    if pair_a is None or pair_a != pair_b:
        return False
    comp_a, comp_b = _comp_key(a), _comp_key(b)
    if comp_a and comp_b and comp_a != comp_b:
        return False
    start_a, start_b = _start(a), _start(b)
    if start_a is None or start_b is None:
        return False
    return abs(start_a - start_b) <= TWIN_WINDOW


def _groups(rows: Sequence[Mapping[str, Any]]) -> list[list[Mapping[str, Any]]]:
    """Connected groups of twins (two or more rows). Rows are bucketed by their team pair first, so only plausible
    pairs are compared."""
    buckets: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        pair = _team_pair(row)
        if pair is not None and str(row["match_id"]).startswith(ID_PREFIX):
            buckets[pair].append(row)
    groups: list[list[Mapping[str, Any]]] = []
    for members in buckets.values():
        if len(members) < 2:
            continue
        parent = list(range(len(members)))

        def root(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                if is_twin(members[i], members[j]):
                    parent[root(i)] = root(j)
        by_root: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
        for i, row in enumerate(members):
            by_root[root(i)].append(row)
        groups.extend(g for g in by_root.values() if len(g) > 1)
    return groups


def choose_canonical(group: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """The twin with the most complete record (most match-page rows); then the one with the score feed; then the lower
    match id. The score feed is only a tie-break: on 2026-11-03 the twin with the feed (lb_36266991) left the
    listing mid-match while the other (lb_75330686) kept recording 13 times more rows, and the trader must follow the
    live record."""
    return min(group, key=lambda r: (
        -int(r.get("match_page_rows") or 0),
        str(r.get("score_pipeline") or "") != PIPELINE_V2,
        str(r["match_id"]),
    ))


def _undecided(row: Mapping[str, Any]) -> bool:
    """No result row, or only an unresolved one."""
    result_type = row.get("result_type")
    return result_type is None or str(result_type).strip().lower() == UNRESOLVED


def _hand_entered(row: Mapping[str, Any]) -> bool:
    return str(row.get("result_source") or "") in HAND_ENTERED_SOURCES


def _winner_key(row: Mapping[str, Any]) -> str:
    return _team_key(row.get("winner")) if str(row.get("result_type") or "").lower() == "win" else ""


def _conflict(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """Two decided results disagree: different types, or two wins for different teams."""
    if str(a.get("result_type") or "").strip().lower() != str(b.get("result_type") or "").strip().lower():
        return True
    return _winner_key(a) != _winner_key(b)


def _copy_row(source: Mapping[str, Any], target: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """The match_results row to write on ``target`` from ``source``'s decided result, with the target's names.
    None when the source has no completion time or its winner cannot be matched to a target team."""
    completed = source.get("completed_at")
    if completed is None:
        return None
    home, away = str(target.get("team_home") or ""), str(target.get("team_away") or "")
    result_type = str(source.get("result_type") or "").strip().lower()
    if result_type == "win":
        wanted = _winner_key(source)
        winner = next((name for name in (home, away) if _team_key(name) == wanted), None)
        if winner is None:
            return None
        loser = away if winner == home else home
        margin = str(source.get("margin") or "")
    else:
        winner = loser = margin = ""
    return {
        "match_id": str(target["match_id"]),
        "completed_at": completed,
        "winner": winner,
        "loser": loser,
        "result_type": result_type,
        "margin": margin,
        "team_home": home,
        "team_away": away,
        "source": f"{COPY_SOURCE_PREFIX}{source['match_id']}",
    }


def plan_result_copies(
    links: Iterable[TwinLink], rows: Sequence[Mapping[str, Any]],
) -> tuple[list[ResultCopy], list[ResultConflict]]:
    """Copies and conflicts for the twin links, judged on the rows as they are now. One copy per target."""
    by_id = {str(r["match_id"]): r for r in rows}
    copies: list[ResultCopy] = []
    conflicts: list[ResultConflict] = []
    targeted: set[str] = set()
    for link in links:
        canonical, twin = by_id.get(link.canonical_id), by_id.get(link.twin_id)
        if canonical is None or twin is None:
            continue
        decided_c, decided_t = has_decided_result(canonical), has_decided_result(twin)
        if decided_c and decided_t:
            if _conflict(canonical, twin):
                conflicts.append(ResultConflict(
                    link.canonical_id, link.twin_id,
                    f"{link.canonical_id} {canonical.get('result_type')} {canonical.get('winner') or '-'} vs "
                    f"{link.twin_id} {twin.get('result_type')} {twin.get('winner') or '-'}",
                ))
            continue
        if decided_c:
            source, target = canonical, twin
        elif decided_t:
            source, target = twin, canonical
        else:
            continue
        target_id = str(target["match_id"])
        if target_id in targeted or not _undecided(target) or _hand_entered(target):
            continue
        row = _copy_row(source, target)
        if row is not None:
            targeted.add(target_id)
            copies.append(ResultCopy(target_id, str(source["match_id"]), row))
    return copies, conflicts


def build_plan(rows: Sequence[Mapping[str, Any]]) -> Plan:
    """The whole plan (pure): links, the canonical values to store, copies and conflicts."""
    links: list[TwinLink] = []
    desired: dict[str, Optional[str]] = {}
    for group in _groups(rows):
        canonical_id = str(choose_canonical(group)["match_id"])
        desired[canonical_id] = None
        for member in group:
            member_id = str(member["match_id"])
            if member_id != canonical_id:
                desired[member_id] = canonical_id
                links.append(TwinLink(canonical_id, member_id))
    links.sort(key=lambda link: (link.canonical_id, link.twin_id))
    updates = []
    for row in rows:
        match_id = str(row["match_id"])
        want = desired.get(match_id)
        if want != (row.get("canonical_match_id") or None):
            updates.append(CanonicalUpdate(match_id, want))
    copies, conflicts = plan_result_copies(links, rows)
    return Plan(links=links, desired=desired, updates=updates, copies=copies, conflicts=conflicts)


def unlinked_links(plan: Plan, rows: Sequence[Mapping[str, Any]]) -> list[TwinLink]:
    """Twin links whose stored canonical_match_id on the twin is not yet the canonical id."""
    stored = {str(r["match_id"]): (r.get("canonical_match_id") or None) for r in rows}
    return [link for link in plan.links if stored.get(link.twin_id) != link.canonical_id]


# --------------------------------------------------------------------------- database (SELECT, then one write path)
async def column_present(session: Any) -> bool:
    from sqlalchemy import text

    return bool((await session.execute(text(CANONICAL_COLUMN_SQL))).scalar())


async def load_rows(session: Any) -> list[dict[str, Any]]:
    from sqlalchemy import text

    result = await session.execute(text(TWIN_ROWS_SQL))
    return [dict(r._mapping) for r in result.fetchall()]


async def apply_plan(session: Any, plan: Plan) -> dict[str, int]:
    """Write the canonical values and the copies. The caller commits. Nothing else is written."""
    from sqlalchemy import text

    updated = copied = 0
    for update in plan.updates:
        result = await session.execute(text(UPDATE_CANONICAL_SQL),
                                       {"canonical": update.canonical_id, "match_id": update.match_id})
        updated += int(result.rowcount or 0)
    for copy in plan.copies:
        result = await session.execute(text(COPY_RESULT_SQL), dict(copy.row))
        copied += int(result.rowcount or 0)
    return {"canonical_updated": updated, "results_copied": copied}


async def link_now(session: Any, *, apply: bool) -> dict[str, Any]:
    """Plan from the registry; with ``apply`` also write it. Used by the CLI and the monitor's harmonise step.

    Raises MigrationMissing when ``apply`` is asked on a database without the canonical_match_id column; the dry run
    never needs it (the plan is computed without it).
    """
    has_column = await column_present(session)
    if apply and not has_column:
        raise MigrationMissing("canonical_match_id is missing: apply the S96 migration (backend/db_init.py) first")
    rows = await load_rows(session)
    plan = build_plan(rows)
    applied = await apply_plan(session, plan) if apply else None
    return {"rows": rows, "plan": plan, "column_present": has_column, "applied": applied}


# --------------------------------------------------------------------------- report
def _label(row: Mapping[str, Any]) -> str:
    return f"{row.get('team_home')} v {row.get('team_away')}"


def render(result: Mapping[str, Any], *, apply: bool) -> str:
    rows, plan = result["rows"], result["plan"]
    by_id = {str(r["match_id"]): r for r in rows}
    mode = "APPLIED" if apply else "DRY RUN (nothing written)"
    lines = [f"link_twins: {mode}",
             f"  lb_ registry rows: {len(rows)}; canonical_match_id column "
             f"{'present' if result['column_present'] else 'absent (migration not applied)'}"]
    lines.append(f"  twin pair(s): {len(plan.links)}")
    for link in plan.links:
        canonical, twin = by_id[link.canonical_id], by_id[link.twin_id]
        start = _start(canonical)
        start_text = f"start {start:%Y-%m-%d %H:%M} UTC" if start else "start not recorded"
        competition = canonical.get("competition") or "competition not recorded"
        lines.append(f"    {_label(canonical)} | {competition} | {start_text}")
        lines.append(
            f"      canonical {link.canonical_id} (score {canonical.get('score_pipeline') or '-'}, "
            f"{canonical.get('match_page_rows') or 0} match-page rows)  <-  twin {link.twin_id} "
            f"(score {twin.get('score_pipeline') or '-'}, {twin.get('match_page_rows') or 0} match-page rows)"
        )
    lines.append(f"  canonical_match_id {'written' if apply else 'to write'}: "
                 + (f"{result['applied']['canonical_updated']} row(s)" if apply else f"{len(plan.updates)} row(s)"))
    for update in plan.updates:
        lines.append(f"    {update.match_id} -> {update.canonical_id or 'NULL (canonical or ordinary)'}")
    lines.append(f"  result copies {'written' if apply else 'to write'}: "
                 + (f"{result['applied']['results_copied']} row(s)" if apply else f"{len(plan.copies)} row(s)"))
    for copy in plan.copies:
        row = copy.row
        outcome = (f"{row['result_type']} for {row['winner']}" + (f" by {row['margin']}" if row["margin"] else "")
                   if row["result_type"] == "win" else row["result_type"].replace("_", " "))
        lines.append(f"    {copy.target_id} <- {copy.source_id}: {outcome} (source {row['source']})")
    lines.append(f"  result conflicts (left alone): {len(plan.conflicts)}")
    for conflict in plan.conflicts:
        lines.append(f"    {conflict.canonical_id} / {conflict.twin_id}: {conflict.detail}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- CLI
async def _main(apply: bool) -> int:
    from sqlalchemy import text

    from shared.db import get_engine, get_session

    try:
        async with get_session() as session:
            if not apply:
                await session.execute(text("SET TRANSACTION READ ONLY"))
            result = await link_now(session, apply=apply)
    except MigrationMissing as exc:
        print(f"link_twins: refused: {exc}")
        return 1
    finally:
        await get_engine().dispose()
    print(render(result, apply=apply))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="write canonical_match_id and copied results (default: dry run, read-only)")
    args = parser.parse_args(argv)
    return asyncio.run(_main(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
