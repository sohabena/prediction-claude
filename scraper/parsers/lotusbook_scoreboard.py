"""Sportradar scoreboard (``sr-simcrick-scb``) -> live score state for LotusBook match pages.

Markup source: ``tests/fixtures/lotusbook/scoreboard_*_20261103.html``, captured read-only on 2026-11-03.
Verified classes: ``sr-simcrick-scb__team-wrapper`` (srm-left / srm-right), ``__team-name``, ``__result``
("183/5", or "183" with no wicket count), ``__extra`` with ``__extra-value`` followed by an RR / CRR / RRR label,
``__status`` ("Simulated match - INN 2 | 16.1/20 OV"), ``__simulated-info``, ``__comment``.

Pre-start pages have no ``__result`` nodes and only a date/time in the status; they parse with ``started=False``.

This module is a pure parser. It reads only the markup it is given and performs no network I/O.
Cricket overs are written ``"16.1"`` = 16 overs and 1 ball; ``balls_bowled = 16 * 6 + 1 = 97``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from scraper.parsers.lotusbook_markets import _Node, _build, _text, _walk
from shared.schemas import MatchContext, MatchStatus

_PREFIX = "sr-simcrick-scb"
_RESULT_RE = re.compile(r"^\s*(\d+)\s*(?:/\s*(\d+))?\s*$")
_INN_RE = re.compile(r"\bINN\s*(\d+)\b", re.IGNORECASE)
_OVERS_RE = re.compile(r"(\d+)(?:\.(\d))?\s*(?:/\s*(\d+))?\s*OV\b", re.IGNORECASE)
_CHASING_RE = re.compile(r"\bchasing\s+(\d+)\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

# UNVERIFIED: no finished-match markup has been captured yet. These forms are inferred from the usual wording
# of result comments ("X won by 25 runs", "X won by 6 wickets", "Match tied", "No result").
_WON_RE = re.compile(r"^\s*(?P<winner>.+?)\s+won\s+by\s+(?P<margin>\d+)\s+(?P<kind>runs?|wickets?)\b", re.IGNORECASE)
_TIE_RE = re.compile(r"^\s*(match\s+tied|tied\s+match)\b", re.IGNORECASE)
_NO_RESULT_RE = re.compile(r"^\s*(match\s+)?no\s+result\b", re.IGNORECASE)


@dataclass(frozen=True)
class SideScore:
    team: str
    runs: Optional[int] = None
    wickets: Optional[int] = None  # None when the result has no "/N" (all-out unknown)
    run_rate: Optional[float] = None  # the "RR" label (completed innings)
    crr: Optional[float] = None  # the "CRR" label (side currently batting)
    rrr: Optional[float] = None  # the "RRR" label (required run rate)


@dataclass(frozen=True)
class ScoreboardState:
    home: SideScore  # left side in the markup
    away: SideScore  # right side in the markup
    started: bool
    innings: Optional[int] = None
    overs: Optional[float] = None  # as written, e.g. 16.1
    balls_bowled: Optional[int] = None
    max_overs: Optional[float] = None
    balls_remaining: Optional[int] = None
    target: Optional[int] = None
    batting_team: Optional[str] = None
    is_simulated: bool = False
    comment: str = ""
    status_text: str = ""

    @property
    def batting_side(self) -> Optional[SideScore]:
        if self.batting_team is None:
            return None
        if self.home.team == self.batting_team:
            return self.home
        if self.away.team == self.batting_team:
            return self.away
        return None


def _has_class(node: _Node, name: str) -> bool:
    return name in node.attrs.get("class", "").split()


def _find_class(root: _Node, name: str) -> list[_Node]:
    return [n for n in _walk(root) if _has_class(n, name)]


def _first_text(root: _Node, name: str) -> Optional[str]:
    nodes = _find_class(root, name)
    return _text(nodes[0]) if nodes else None


def _parse_result(text: str) -> tuple[Optional[int], Optional[int]]:
    match = _RESULT_RE.match(text or "")
    if not match:
        return None, None
    runs = int(match.group(1))
    wickets = int(match.group(2)) if match.group(2) is not None else None
    return runs, wickets


def _parse_extra(extra: _Node) -> dict[str, float]:
    """Pair each ``__extra-value`` with the label that follows it (RR, CRR, RRR)."""
    labels: dict[str, float] = {}
    pending: Optional[float] = None

    def visit(node: _Node) -> None:
        nonlocal pending
        for child in node.children:
            if isinstance(child, _Node):
                if _has_class(child, f"{_PREFIX}__extra-value"):
                    match = _NUMBER_RE.search(_text(child))
                    pending = float(match.group(0)) if match else None
                else:
                    visit(child)
            else:
                label = child.strip().upper()
                if label in {"RR", "CRR", "RRR"} and pending is not None:
                    labels[label] = pending
                    pending = None

    visit(extra)
    return labels


def _parse_side(wrapper: _Node) -> SideScore:
    team = _first_text(wrapper, f"{_PREFIX}__team-name") or ""
    runs, wickets = _parse_result(_first_text(wrapper, f"{_PREFIX}__result") or "")
    extras = _find_class(wrapper, f"{_PREFIX}__extra")
    labels = _parse_extra(extras[0]) if extras else {}
    return SideScore(
        team=team,
        runs=runs,
        wickets=wickets,
        run_rate=labels.get("RR"),
        crr=labels.get("CRR"),
        rrr=labels.get("RRR"),
    )


def _parse_overs(status: str) -> tuple[Optional[float], Optional[int], Optional[float]]:
    """Return ``(overs, balls_bowled, max_overs)`` from a status such as ``"INN 2 | 16.1/20 OV"``."""
    match = _OVERS_RE.search(status)
    if not match:
        return None, None, None
    whole = int(match.group(1))
    ball = int(match.group(2)) if match.group(2) is not None else 0
    if ball > 5:
        return None, None, None
    max_overs = float(match.group(3)) if match.group(3) is not None else None
    overs = float(f"{whole}.{ball}")
    return overs, whole * 6 + ball, max_overs


def parse_scoreboard_html(html: str) -> Optional[ScoreboardState]:
    """Parse a Sportradar scoreboard. Returns None when the markup has no two-team scoreboard."""
    root = _build(html)
    scoreboards = [n for n in _walk(root) if _has_class(n, _PREFIX)]
    if not scoreboards:
        return None
    board = scoreboards[0]
    wrappers = _find_class(board, f"{_PREFIX}__team-wrapper")
    if len(wrappers) < 2:
        return None

    home = _parse_side(wrappers[0])
    away = _parse_side(wrappers[1])
    started = bool(_find_class(board, f"{_PREFIX}__result"))

    status_nodes = _find_class(board, f"{_PREFIX}__status")
    status = _text(status_nodes[0]) if status_nodes else ""
    innings_match = _INN_RE.search(status)
    innings = int(innings_match.group(1)) if innings_match else None
    overs, balls_bowled, max_overs = _parse_overs(status)
    balls_remaining = None
    if balls_bowled is not None and max_overs is not None:
        balls_remaining = int(max_overs * 6) - balls_bowled

    comment = _first_text(board, f"{_PREFIX}__comment") or ""
    target_match = _CHASING_RE.search(comment)
    target = int(target_match.group(1)) if target_match else None

    # The side currently batting is the one showing a CRR label. If only the other side shows RR (its innings
    # is complete), the side without RR is inferred to be batting. Both sides showing neither gives None.
    batting_team = None
    if home.crr is not None and away.crr is None:
        batting_team = home.team
    elif away.crr is not None and home.crr is None:
        batting_team = away.team
    elif home.crr is None and away.crr is None:
        if away.run_rate is not None and home.run_rate is None:
            batting_team = home.team or None
        elif home.run_rate is not None and away.run_rate is None:
            batting_team = away.team or None

    return ScoreboardState(
        home=home,
        away=away,
        started=started,
        innings=innings,
        overs=overs,
        balls_bowled=balls_bowled,
        max_overs=max_overs,
        balls_remaining=balls_remaining,
        target=target,
        batting_team=batting_team,
        is_simulated=bool(_find_class(board, f"{_PREFIX}__simulated-info")),
        comment=comment,
        status_text=status,
    )


def parse_result_comment(text: str) -> Optional[tuple[Optional[str], str, Optional[int]]]:
    """Finished-match comment -> ``(winner|None, result_type, margin|None)``, else None.

    ``result_type`` is ``"runs"``, ``"wickets"``, ``"tie"`` or ``"no_result"``.
    UNVERIFIED: no finished-match markup has been captured; the forms are inferred (see the regex constants).
    """
    text = " ".join((text or "").split())
    if not text:
        return None
    won = _WON_RE.match(text)
    if won:
        kind = "runs" if won.group("kind").lower().startswith("run") else "wickets"
        return won.group("winner").strip() or None, kind, int(won.group("margin"))
    if _TIE_RE.match(text):
        return None, "tie", None
    if _NO_RESULT_RE.match(text):
        return None, "no_result", None
    return None


def to_match_context(state: ScoreboardState, match_id: str, observed_at: datetime) -> Optional[MatchContext]:
    """Build a MatchContext from a started scoreboard, or None.

    Returns None when the match has not started, or when the batting side, its runs or wickets, the innings,
    the overs or the max overs cannot be read from the markup. Nothing is estimated.
    """
    if not state.started:
        return None
    batting = state.batting_side
    if batting is None or batting.runs is None or batting.wickets is None:
        return None
    if state.innings is None or state.overs is None or state.max_overs is None or state.balls_remaining is None:
        return None

    bowling = state.away if batting is state.home else state.home
    completed = parse_result_comment(state.comment) is not None
    max_overs = state.max_overs
    if max_overs == 20:
        match_format = "T20"
    elif max_overs == 50:
        match_format = "ODI"
    else:
        match_format = "T20"  # schema default; no other format is recognised from the markup
    run_rate = batting.crr if batting.crr is not None else (batting.run_rate or 0.0)

    return MatchContext(
        match_id=match_id,
        timestamp=observed_at,
        is_live=not completed,
        score=batting.runs,
        wickets=batting.wickets,
        overs=state.overs,
        run_rate=run_rate,
        required_run_rate=batting.rrr or 0.0,
        innings=state.innings,
        balls_remaining=state.balls_remaining,
        max_overs=max_overs,
        max_balls=int(max_overs * 6),
        batting_team=batting.team,
        bowling_team=bowling.team,
        status=MatchStatus.COMPLETED if completed else MatchStatus.LIVE,
        match_format=match_format,
    )
