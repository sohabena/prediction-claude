"""S91: read-only data health (prefix ``/api/data``) and the one validated write, the manual result.

- ``GET /api/data/health``: the last run of the quality monitor (``logs/data_quality.jsonl``): each check with status,
  value and detail.
- ``GET /api/data/matches?days=3``: the recorded matches with their state (live, pre-match, finished), whether the
  match page was seen, the score pipeline, the start and actual start, and the result.
- ``GET /api/data/missing-results``: ``scripts.results_reconciliation.missing_results``.
- ``POST /api/data/results``: ``scripts.set_result`` (validation, refusal to overwrite a trusted result, settlement of the
  match's paper bets). No ``force`` from the API: the body does not accept it.

Reads run in a READ ONLY transaction. The POST is the only write, and it goes through ``set_result.run``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, Mapping, Optional, Sequence

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from backend.routers.readonly import age_seconds, jsonable, last_json_line, logs_dir, read_only_session, utc
from scripts import results_reconciliation as rec
from scripts import set_result
from shared.logging import setup_logging

router = APIRouter()
logger = setup_logging("router_data")

LIVE_FRESH = timedelta(minutes=15)  # a live match with no tick this long is not live (the monitor's live freshness)
STATE_RANK = {"live": 0, "pre-match": 1, "stumps": 2, "finished": 3}

MATCHES_SQL = text(
    """
    WITH window_ticks AS (
        SELECT match_id, MAX(time) AS last_tick, MIN(time) FILTER (WHERE is_live) AS first_live_tick,
               COUNT(*) AS odds_rows, MAX(team_home) AS tick_home, MAX(team_away) AS tick_away,
               MAX(competition) AS tick_competition
        FROM odds_ticks
        WHERE time >= :since
        GROUP BY match_id
    ),
    latest AS (
        SELECT DISTINCT ON (match_id) match_id, is_live AS live_now
        FROM odds_ticks
        WHERE time >= :since
        ORDER BY match_id, time DESC, tick_seq DESC
    )
    SELECT w.match_id,
           COALESCE(s.team_home, w.tick_home) AS team_home,
           COALESCE(s.team_away, w.tick_away) AS team_away,
           COALESCE(NULLIF(s.competition, ''), w.tick_competition) AS competition,
           s.is_simulated, s.match_start_time, s.actual_start_time, s.actual_start_source, s.score_pipeline,
           w.last_tick, w.first_live_tick, w.odds_rows, COALESCE(l.live_now, false) AS live_now,
           EXISTS (SELECT 1 FROM market_ticks m WHERE m.match_id = w.match_id AND m.time >= :since) AS match_page_seen,
           res.result_type, res.winner, res.completed_at
    FROM window_ticks w
    LEFT JOIN latest l ON l.match_id = w.match_id
    LEFT JOIN match_training_status s ON s.match_id = w.match_id
    LEFT JOIN match_results res ON res.match_id = w.match_id
    """
)


# ----------------------------------------------------------------------------- health
def quality_record(path: Optional[Path] = None) -> Optional[dict[str, Any]]:
    """The last quality-monitor record (one JSON line per run), or None."""
    return last_json_line(path or logs_dir() / "data_quality.jsonl", accept=lambda rec_: "checks" in rec_)


def build_health(record: Optional[Mapping[str, Any]], now: datetime) -> dict[str, Any]:
    if record is None:
        return {"available": False, "ts": None, "age_seconds": None, "window_hours": None, "overall": None,
                "exit_code": None, "checks": []}
    return {
        "available": True,
        "ts": record.get("ts"),
        "age_seconds": age_seconds(record.get("ts"), now),
        "window_hours": record.get("window_hours"),
        "overall": record.get("overall"),
        "exit_code": record.get("exit_code"),
        "checks": [
            {"name": c.get("name"), "status": c.get("status"), "value": c.get("value"), "detail": c.get("detail")}
            for c in record.get("checks") or []
        ],
    }


@router.get("/health")
async def get_health() -> dict[str, Any]:
    """The last data quality monitor run: each check with its status, value and detail."""
    now = datetime.now(timezone.utc)
    return jsonable(build_health(quality_record(), now))


# ----------------------------------------------------------------------------- matches
def match_state(row: Mapping[str, Any], now: datetime) -> str:
    """live: the newest tick says in play and is fresh.
    stumps: multi-day match started, not currently live, within the multiday window and without a decided result.
    finished: started (or first seen live) and not live now.
    pre-match: not started yet, or never seen live with no start recorded."""
    last = utc(row.get("last_tick"))
    if row.get("live_now") and last is not None and now - last <= LIVE_FRESH:
        return "live"
    start = utc(row.get("actual_start_time")) or utc(row.get("match_start_time"))
    if start is not None:
        if start > now:
            return "pre-match"
        if rec.is_multiday(row.get("competition")) and not rec.has_decided_result(row) and now - start <= rec.MULTIDAY_WINDOW:
            return "stumps"
        return "finished"
    if row.get("first_live_tick") is None:
        return "pre-match"
    if rec.is_multiday(row.get("competition")) and not rec.has_decided_result(row):
        first = utc(row.get("first_live_tick"))
        if first is not None and now - first <= rec.MULTIDAY_WINDOW:
            return "stumps"
    return "finished"


def result_status(row: Mapping[str, Any], state: str, decided: bool, now: datetime) -> str:
    """decided: a stored win, tie, no_result or abandoned result. not_started: before the off. missing: no decided result
    and the last tick is over 3 h old (the S76 rule, as in the results list). awaiting: no decided result yet, but the
    match was recently in play or is still in play."""
    if decided:
        return "decided"
    if state == "pre-match":
        return "not_started"
    last = utc(row.get("last_tick"))
    if rec.is_multiday(row.get("competition")):
        start = utc(row.get("actual_start_time")) or utc(row.get("match_start_time")) or last
        if start is not None and now - start <= rec.MULTIDAY_WINDOW:
            return "awaiting"
    if last is None or now - last > rec.STALE_AFTER:
        return "missing"
    return "awaiting"


def build_matches(rows: Sequence[Mapping[str, Any]], now: datetime, days: int, include_simulated: bool) -> dict[str, Any]:
    """The /api/data/matches payload. Pure: ``rows`` are the rows of ``MATCHES_SQL``."""
    items = []
    for row in rows:
        simulated = not rec.is_real(row)
        if simulated and not include_simulated:
            continue
        state = match_state(row, now)
        decided = rec.has_decided_result(row)
        status = result_status(row, state, decided, now)
        items.append({
            "match_id": str(row["match_id"]),
            "team_home": row.get("team_home") or "",
            "team_away": row.get("team_away") or "",
            "competition": row.get("competition") or "",
            "simulated": simulated,
            "state": state,
            "start": row.get("match_start_time"),
            "actual_start": row.get("actual_start_time"),
            "actual_start_source": row.get("actual_start_source"),
            "first_live_tick": row.get("first_live_tick"),
            "last_tick": row.get("last_tick"),
            "odds_rows": row.get("odds_rows"),
            "match_page_seen": bool(row.get("match_page_seen")),
            "score_pipeline": row.get("score_pipeline"),
            "result": {
                "result_type": row.get("result_type"),
                "winner": row.get("winner"),
                "completed_at": row.get("completed_at"),
                "decided": decided,
            },
            "result_status": status,
        })
    # newest tick first within each state; live, then pre-match, then finished
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    items.sort(key=lambda i: utc(i["last_tick"]) or epoch, reverse=True)
    items.sort(key=lambda i: STATE_RANK[i["state"]])
    summary = {"live": 0, "pre-match": 0, "stumps": 0, "finished": 0, "missing_result": 0}
    for i in items:
        summary[i["state"]] += 1
        if i["result_status"] == "missing":
            summary["missing_result"] += 1
    return {
        "generated_at": now,
        "days": days,
        "include_simulated": include_simulated,
        "count": len(items),
        "summary": summary,
        "matches": items,
    }


@router.get("/matches")
async def get_matches(
    days: int = Query(3, ge=1, le=30),
    include_simulated: bool = Query(False, description="include simulated (SRL) matches"),
) -> dict[str, Any]:
    """Recorded matches with ticks in the last ``days`` days."""
    now = datetime.now(timezone.utc)
    rows = []
    try:
        async with read_only_session() as session:
            rows = [dict(r) for r in (await session.execute(MATCHES_SQL, {"since": now - timedelta(days=days)})).mappings().all()]
    except Exception as exc:
        logger.warning("Database unavailable for matches: %s", exc)
    return jsonable(build_matches(rows, now, days, include_simulated))


# ----------------------------------------------------------------------------- missing results
@router.get("/missing-results")
async def get_missing_results() -> dict[str, Any]:
    """Real matches with a recent tick, silent for over 3 h, and no decided result (the S76 list)."""
    now = datetime.now(timezone.utc)
    items = []
    try:
        async with read_only_session() as session:
            items = await rec.missing_results(session, now)
    except Exception as exc:
        logger.warning("Database unavailable for missing results: %s", exc)
    return jsonable({
        "generated_at": now,
        "count": len(items),
        "lookback_days": rec.LOOKBACK.days,
        "stale_after_hours": rec.STALE_AFTER.total_seconds() / 3600.0,
        "items": items,
    })


# ----------------------------------------------------------------------------- the manual result (the one write)
class ResultIn(BaseModel):
    """The body of POST /api/data/results. ``force`` is not a field: an extra key is refused."""

    model_config = ConfigDict(extra="forbid")

    match_id: str = Field(min_length=1, max_length=64)
    outcome: Literal["winner", "tie", "no_result"]
    winner: Optional[str] = Field(default=None, max_length=120)
    margin: Optional[str] = Field(default=None, max_length=60)
    url: str = Field(min_length=8, max_length=2048)


def request_args(body: ResultIn) -> SimpleNamespace:
    """The argparse-shaped namespace ``set_result.build_request`` validates. Source is always public_scorecard."""
    return SimpleNamespace(
        match=body.match_id,
        winner=body.winner if body.outcome == "winner" else None,
        tie=body.outcome == "tie",
        no_result=body.outcome == "no_result",
        margin=body.margin,
        source="public_scorecard",
        url=body.url,
        force=False,
        dry_run=False,
    )


@router.post("/results")
async def post_result(body: ResultIn) -> dict[str, Any]:
    """Write a result from a public scorecard and settle the match's paper bets. 409 when a trusted result exists."""
    if body.outcome == "winner" and not (body.winner or "").strip():
        raise HTTPException(status_code=422, detail="winner is required when outcome is winner")
    if body.outcome != "winner" and body.winner is not None:
        raise HTTPException(status_code=422, detail="winner applies only when outcome is winner")
    try:
        request = set_result.build_request(request_args(body))
    except set_result.ResultError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    now = datetime.now(timezone.utc)
    try:
        summary = await set_result.run(request, now=now)
    except set_result.OverwriteRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except set_result.ResultError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:  # the write failed: say so, without the driver's text
        logger.error("manual_result_write_failed", match_id=body.match_id, error=type(exc).__name__)
        raise HTTPException(status_code=500, detail="the result could not be written")
    set_result._audit(summary, request, now)  # one line in logs/set_result.jsonl; a failure only prints a warning
    settled = {str(k): int(v) for k, v in dict(summary["bets_settled"]).items()}
    logger.info("manual_result_written", match_id=body.match_id, result_type=summary["written"]["result_type"])
    return jsonable({
        "written": True,
        "match_id": summary["match_id"],
        "team_home": summary["team_home"],
        "team_away": summary["team_away"],
        "competition": summary["competition"],
        "is_simulated": summary["is_simulated"],
        "result": summary["written"],
        "replaced": summary["replaced"],
        "paper_bets_settled": sum(settled.values()),
        "bets_settled": settled,
        "url": request.url,
        "checked_at": now,
    })
