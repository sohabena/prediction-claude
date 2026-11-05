"""LotusBook live score feed (S68): the match page's own ``match-center/score/v3`` response -> ``ScoreState``.

The match page polls ``GET .../match-center/score/v3/4/<event_id>`` every few seconds. This module is a pure
parser: it reads the body it is given and performs no I/O. The page makes the request; the worker only
reads the response (see scraper/market_pages.py).

Shape (verified 2026-11-03, fixture ``tests/fixtures/lotusbook/score_v3_real_live_36267578_20261103.json``):
- real matches on the ``livescore-v2`` pipeline: ``result.stats`` carries ``battingTeam``, ``currentInning``,
  ``tossWinner``, ``lastOver``, ``remainingBalls``, ``totalBalls``, ``oversPerInning``, ``updatedAt`` and more.
- SRL matches and some real matches: ``{"result": {"stats": {}, "scorePipeline": "legacy"}}`` (no score).

Cricket overs are written ``"16.1"`` = 16 overs and 1 ball; the ball digit must be 0 to 5.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from typing import Any, Optional

from scraper.parsers.lotusbook_scoreboard import parse_result_comment
from shared.schemas import MatchStatus

SCORE_URL_RE = re.compile(r"/match-center/score/v3/4/(\d+)(?:[/?#]|$)")
PIPELINE_V2 = "livescore-v2"
PIPELINE_LEGACY = "legacy"

_FRACTION_RE = re.compile(r"(\d{2}:\d{2}:\d{2})\.(\d+)")
_OVERS_RE = re.compile(r"^\s*(\d+)(?:\.(\d))?\s*$")


@dataclass(frozen=True)
class ScoreState:
    pipeline: str
    batting_team: str
    runs: int
    wickets: int
    overs: float  # cricket notation, e.g. 1.0 = 1 over 0 balls
    balls: int  # balls bowled in the current innings
    innings: int
    target: Optional[int] = None
    run_rate: float = 0.0
    required_run_rate: float = 0.0
    required_runs: Optional[int] = None
    remaining_balls: Optional[int] = None
    total_balls: Optional[int] = None
    max_overs: Optional[float] = None
    match_format: str = ""
    is_powerplay: bool = False
    session: str = ""
    toss_winner: Optional[str] = None
    toss_decision: Optional[str] = None
    updated_at: Optional[datetime] = None  # the feed's own time, UTC
    result_text: str = ""  # the feed's result or status text, "" when none
    last_over_balls: tuple[str, ...] = ()
    teams: tuple[str, ...] = ()
    # S107 part A: the players on the field, read from the same body. Shape:
    #   {"batsmen": [{"name", "runs", "balls", "strike_rate", "on_strike"}] | None,
    #    "bowler": {"name", "overs", "wickets", "runs"} | None,
    #    "partnership": {"runs", "balls"} | None,
    #    "last_out": {"name", "runs", "balls"} | None}
    # A part the feed does not send, or sends unreadably, is None. Nothing is estimated.
    players: Optional[dict] = None

    @property
    def is_finished(self) -> bool:
        """True when the result text reads as a finished match (same wording as the scoreboard comment)."""
        return parse_result_comment(self.result_text) is not None

    def content_key(self) -> tuple:
        """Every field except ``updated_at``: two states with the same key show the same score."""
        return tuple(getattr(self, f.name) for f in fields(self) if f.name != "updated_at")


def score_event_id_from_url(url: str) -> Optional[str]:
    """LotusBook event id of a ``match-center/score/v3`` URL, or None for any other URL."""
    match = SCORE_URL_RE.search(url or "")
    return match.group(1) if match else None


def _stats(payload: Any) -> Optional[dict]:
    if not isinstance(payload, dict) or payload.get("success") is False:
        return None
    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    stats = result.get("stats")
    return stats if isinstance(stats, dict) and stats else None


def score_pipeline_of(payload: Any) -> Optional[str]:
    """The feed's ``scorePipeline`` ("livescore-v2", "legacy", ...), or None when absent."""
    result = payload.get("result") if isinstance(payload, dict) else None
    value = result.get("scorePipeline") if isinstance(result, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _overs(value: Any) -> Optional[tuple[float, int]]:
    """``"16.1"`` -> ``(16.1, 97)``. None when the text is not cricket notation (ball digit above 5)."""
    match = _OVERS_RE.match(value) if isinstance(value, str) else None
    if not match:
        return None
    whole = int(match.group(1))
    ball = int(match.group(2)) if match.group(2) is not None else 0
    if ball > 5:
        return None
    return float(f"{whole}.{ball}"), whole * 6 + ball


def _overs_number(value: Any) -> Optional[float]:
    """A bowler's overs as the feed sends them: a number (``1.0``) or text (``"3.4"``). Ball digit must be 0 to 5."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    parsed = _overs(str(value))
    return parsed[0] if parsed else None


def _batsman(entry: Any) -> Optional[dict[str, Any]]:
    """One batter at the crease. None when the name, runs or balls are unreadable."""
    if not isinstance(entry, dict):
        return None
    name = _text(entry.get("name"))
    runs = _int(entry.get("batsmanRuns"))
    balls = _int(entry.get("balls"))
    if not name or runs is None or balls is None:
        return None
    strike_rate = _float(entry.get("strikeRate"))
    if strike_rate is not None and not math.isfinite(strike_rate):
        strike_rate = None
    on_strike = entry.get("isOnStrike")
    return {
        "name": name,
        "runs": runs,
        "balls": balls,
        "strike_rate": strike_rate,
        "on_strike": on_strike if isinstance(on_strike, bool) else None,
    }


def _batsmen(value: Any) -> Optional[list[dict[str, Any]]]:
    """The batters at the crease. One unreadable entry refuses the whole list: a partial list could hide a batter."""
    if not isinstance(value, list):
        return None
    out: list[dict[str, Any]] = []
    for entry in value:
        parsed = _batsman(entry)
        if parsed is None:
            return None
        out.append(parsed)
    return out


def _bowler(value: Any) -> Optional[dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    name = _text(value.get("name"))
    overs = _overs_number(value.get("overs"))
    wickets = _int(value.get("wickets"))
    runs = _int(value.get("bowlerRuns"))
    if not name or overs is None or wickets is None or runs is None:
        return None
    return {"name": name, "overs": overs, "wickets": wickets, "runs": runs}


def _partnership(batting: dict) -> Optional[dict[str, int]]:
    runs = _int(batting.get("partnerShipScore"))
    balls = _int(batting.get("partnerShipBalls"))
    if runs is None or balls is None:
        return None
    return {"runs": runs, "balls": balls}


def _last_out(value: Any) -> Optional[dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    name = _text(value.get("name"))
    runs = _int(value.get("batsManRuns"))
    balls = _int(value.get("balls"))
    if not name or runs is None or balls is None:
        return None
    return {"name": name, "runs": runs, "balls": balls}


def _players(stats: dict, batting: dict) -> dict[str, Any]:
    """The four player parts of a score body (S107 part A). Each part is None when the feed does not give it."""
    return {
        "batsmen": _batsmen(stats.get("batsmen")),
        "bowler": _bowler(stats.get("bowler")),
        "partnership": _partnership(batting),
        "last_out": _last_out(stats.get("lastBatsmanOut")),
    }


def _parse_utc(value: Any) -> Optional[datetime]:
    """ISO text with a 'Z' or an offset, any number of fraction digits, as an aware UTC datetime."""
    text = _text(value)
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    text = _FRACTION_RE.sub(lambda m: f"{m.group(1)}.{(m.group(2) + '000000')[:6]}", text, count=1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def parse_score_v3(payload: Any) -> Optional[ScoreState]:
    """Score feed body -> ScoreState, or None for legacy, empty or unreadable bodies.

    Nothing is estimated: runs, wickets, overs and innings must all be present, or the body is refused.
    """
    stats = _stats(payload)
    if stats is None:
        return None
    pipeline = score_pipeline_of(payload) or PIPELINE_V2
    if pipeline == PIPELINE_LEGACY:
        return None

    batting = stats.get("battingTeam")
    if not isinstance(batting, dict):
        return None
    batting_team = _text(batting.get("name"))
    runs = _int(batting.get("score"))
    wickets = _int(batting.get("wickets"))
    overs = _overs(batting.get("overs"))
    innings = _int(stats.get("currentInning"))
    if not batting_team or runs is None or wickets is None or overs is None or innings is None:
        return None
    overs_value, overs_balls = overs
    balls = _int(batting.get("balls"))

    toss = stats.get("tossWinner")
    toss_name = _text(toss.get("name")) if isinstance(toss, dict) else ""
    toss_decision = _text(toss.get("decision")) if isinstance(toss, dict) else ""

    last_over = stats.get("lastOver")
    last_balls: tuple[str, ...] = ()
    if isinstance(last_over, dict) and isinstance(last_over.get("balls"), list):
        last_balls = tuple(str(b.get("value")) for b in last_over["balls"] if isinstance(b, dict))

    result_text = _text(stats.get("result")) or _text(stats.get("status"))
    target = _int(batting.get("target"))

    teams = stats.get("teams")
    team_names: tuple[str, ...] = ()
    if isinstance(teams, list):
        team_names = tuple(_text(t.get("n")) for t in teams if isinstance(t, dict) and _text(t.get("n")))

    return ScoreState(
        pipeline=pipeline,
        batting_team=batting_team,
        runs=runs,
        wickets=wickets,
        overs=overs_value,
        balls=balls if balls is not None else overs_balls,
        innings=innings,
        target=target if target else None,
        run_rate=_float(batting.get("runRate")) or 0.0,
        required_run_rate=_float(batting.get("requiredRunRate")) or 0.0,
        required_runs=_int(batting.get("requiredRuns")),
        remaining_balls=_int(stats.get("remainingBalls")),
        total_balls=_int(stats.get("totalBalls")),
        max_overs=_float(stats.get("oversPerInning")),
        match_format=_text(stats.get("matchFormat")),
        is_powerplay=stats.get("isPowerplay") is True,
        session=_text(stats.get("session")),
        toss_winner=toss_name or None,
        toss_decision=(toss_decision or None) if toss_name else None,
        updated_at=_parse_utc(stats.get("updatedAt")),
        result_text=result_text,
        last_over_balls=last_balls,
        teams=team_names,
        players=_players(stats, batting),
    )


def _norm(name: Optional[str]) -> str:
    return " ".join((name or "").split()).casefold()


def _bowling_team(state: ScoreState, team_home: str, team_away: str) -> str:
    """The side not batting. Matched on the registry names; falls back to the feed's own team list."""
    batting = _norm(state.batting_team)
    if batting and batting == _norm(team_home):
        return team_away
    if batting and batting == _norm(team_away):
        return team_home
    others = [name for name in state.teams if _norm(name) != batting]
    return others[0] if others else ""


def to_context_row(
    state: ScoreState,
    match_id: str,
    team_home: str,
    team_away: str,
    observed_at: Optional[datetime] = None,
) -> Optional[dict[str, Any]]:
    """Map a ScoreState to ``match_context`` column values. ``time`` is ``observed_at`` or the feed's own time."""
    when = observed_at or state.updated_at
    if when is None:
        return None
    max_balls = int(state.max_overs * 6) if state.max_overs else None
    remaining = state.remaining_balls
    if remaining is None and max_balls is not None:
        remaining = max(max_balls - state.balls, 0)
    finished = state.is_finished
    return {
        "time": when,
        "match_id": match_id,
        "is_live": not finished,
        "score": state.runs,
        "wickets": state.wickets,
        "overs": state.overs,
        "run_rate": state.run_rate,
        "req_run_rate": state.required_run_rate,
        "innings": state.innings,
        "balls_remaining": remaining or 0,
        "batting_team": state.batting_team,
        "bowling_team": _bowling_team(state, team_home, team_away),
        "status": (MatchStatus.COMPLETED if finished else MatchStatus.LIVE).value,
        "match_format": state.match_format or "T20",
        "players": state.players,
    }
