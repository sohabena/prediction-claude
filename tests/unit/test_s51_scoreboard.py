"""S51: Sportradar scoreboard parser (score state from LotusBook match pages). Pure parser; no network."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from scraper.parsers.lotusbook_scoreboard import (
    parse_result_comment,
    parse_scoreboard_html,
    to_match_context,
)
from shared.schemas import MatchContext, MatchStatus

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "lotusbook"
LIVE_HTML = (FIX / "scoreboard_srl_live_20261103.html").read_text(encoding="utf-8")
PRESTART_HTML = (FIX / "scoreboard_real_prestart_20261103.html").read_text(encoding="utf-8")
NOW = datetime(2026, 11, 3, 2, 50, tzinfo=timezone.utc)
MID = "lb_76110448"


def _scoreboard(result_home: str, result_away: str, status: str, comment: str, extra_home: str = "",
                extra_away: str = "") -> str:
    """Minimal synthetic scoreboard with the same class names as the captured markup."""
    return (
        '<div class="sr-bb sr-simcrick-scb"><div class="sr-simcrick-scb__wrapper">'
        '<div class="sr-simcrick-scb__team-wrapper srm-left"><div class="sr-simcrick-scb__team">'
        '<div class="sr-simcrick-scb__team-name">Alpha</div></div>'
        f'<div class="sr-simcrick-scb__result">{result_home}</div>'
        f'<div class="sr-simcrick-scb__extra">{extra_home}</div></div>'
        '<div class="sr-simcrick-scb__result-sep">:</div>'
        '<div class="sr-simcrick-scb__team-wrapper srm-right"><div class="sr-simcrick-scb__team">'
        '<div class="sr-simcrick-scb__team-name">Beta</div></div>'
        f'<div class="sr-simcrick-scb__result">{result_away}</div>'
        f'<div class="sr-simcrick-scb__extra">{extra_away}</div></div>'
        f'<div class="sr-simcrick-scb__status"><span>{status}</span></div>'
        f'<div class="sr-simcrick-scb__comment">{comment}</div>'
        "</div></div>"
    )


# ── captured fixtures ───────────────────────────────────────────────────────────


def test_live_fixture_teams_and_order():
    state = parse_scoreboard_html(LIVE_HTML)
    assert state is not None
    assert state.home.team == "St Lucia Kings SRL"
    assert state.away.team == "Trinbago Knight Riders SRL"


def test_live_fixture_scores_and_wickets():
    state = parse_scoreboard_html(LIVE_HTML)
    assert (state.home.runs, state.home.wickets) == (183, 5)
    assert (state.away.runs, state.away.wickets) == (74, 7)


def test_live_fixture_run_rates_parsed_per_label():
    state = parse_scoreboard_html(LIVE_HTML)
    assert state.home.run_rate == pytest.approx(9.15)
    assert state.home.crr is None and state.home.rrr is None
    assert state.away.crr == pytest.approx(4.58)
    assert state.away.rrr == pytest.approx(28.70)
    assert state.away.run_rate is None


def test_live_fixture_innings_overs_and_balls():
    state = parse_scoreboard_html(LIVE_HTML)
    assert state.started is True
    assert state.is_simulated is True
    assert state.innings == 2
    assert state.overs == pytest.approx(16.1)
    assert state.max_overs == 20.0
    # "16.1" is 16 overs and 1 ball: 97 bowled, 23 of 120 remaining.
    assert state.balls_bowled == 97
    assert state.balls_remaining == 23


def test_live_fixture_batting_team_and_target():
    state = parse_scoreboard_html(LIVE_HTML)
    assert state.batting_team == "Trinbago Knight Riders SRL"
    assert state.batting_side is state.away
    assert state.target == 183
    assert "74/7 after 16.1 overs" in state.comment


def test_live_fixture_match_context():
    state = parse_scoreboard_html(LIVE_HTML)
    ctx = to_match_context(state, MID, NOW)
    assert isinstance(ctx, MatchContext)
    assert ctx.match_id == MID
    assert ctx.timestamp == NOW
    assert ctx.score == 74
    assert ctx.wickets == 7
    assert ctx.overs == pytest.approx(16.1)
    assert ctx.run_rate == pytest.approx(4.58)
    assert ctx.required_run_rate == pytest.approx(28.70)
    assert ctx.innings == 2
    assert ctx.balls_remaining == 23
    assert ctx.max_overs == 20.0
    assert ctx.max_balls == 120
    assert ctx.batting_team == "Trinbago Knight Riders SRL"
    assert ctx.bowling_team == "St Lucia Kings SRL"
    assert ctx.status == MatchStatus.LIVE
    assert ctx.is_live is True
    assert ctx.match_format == "T20"


def test_prestart_fixture_not_started():
    state = parse_scoreboard_html(PRESTART_HTML)
    assert state is not None
    assert state.started is False
    assert state.home.team == "Toronto Kings"
    assert state.away.team == "Montreal Warriors"
    assert state.home.runs is None and state.away.runs is None
    assert state.innings is None and state.overs is None
    assert state.batting_team is None
    assert "03 Nov" in state.status_text


def test_prestart_fixture_has_no_match_context():
    state = parse_scoreboard_html(PRESTART_HTML)
    assert to_match_context(state, MID, NOW) is None


# ── edge cases ────────────────────────────────────────────────────────────────


def test_non_scoreboard_html_returns_none():
    assert parse_scoreboard_html("<html><body><p>no widget</p></body></html>") is None
    assert parse_scoreboard_html("") is None


def test_result_without_wicket_count_gives_unknown_wickets():
    html = _scoreboard("183", "74/7", "INN 2 | 16.1/20 OV", "")
    state = parse_scoreboard_html(html)
    assert state.home.runs == 183
    assert state.home.wickets is None
    assert to_match_context(state, MID, NOW) is None


def test_batting_side_without_wicket_count_blocks_context():
    html = _scoreboard("183/5", "74", "INN 2 | 16.1/20 OV", "chasing 183",
                       extra_home="<span>9.15</span><span>RR</span>",
                       extra_away='<span class="sr-simcrick-scb__extra-value">4.58</span><span>CRR</span>')
    state = parse_scoreboard_html(html)
    assert state.batting_team == "Beta"
    assert state.away.runs == 74 and state.away.wickets is None
    assert to_match_context(state, MID, NOW) is None


def test_batting_side_inferred_from_completed_side_rr_label():
    html = _scoreboard("183/5", "74/7", "INN 2 | 16.1/20 OV", "chasing 183",
                       extra_home='<span class="sr-simcrick-scb__extra-value">9.15</span><span>RR</span>',
                       extra_away="")
    state = parse_scoreboard_html(html)
    assert state.batting_team == "Beta"
    assert to_match_context(state, MID, NOW).score == 74


def test_overs_with_invalid_ball_digit_are_rejected():
    html = _scoreboard("10/1", "0/0", "INN 1 | 16.7/20 OV", "")
    state = parse_scoreboard_html(html)
    assert state.overs is None
    assert state.balls_remaining is None


def test_overs_without_max_leave_max_unknown():
    html = _scoreboard("50/2", "0/0", "INN 1 | 8.3 OV", "")
    state = parse_scoreboard_html(html)
    assert state.overs == pytest.approx(8.3)
    assert state.balls_bowled == 51
    assert state.max_overs is None
    assert state.balls_remaining is None


def test_match_context_only_when_started_and_complete_fields_present():
    state = parse_scoreboard_html(LIVE_HTML)
    assert to_match_context(state, MID, NOW).overs == pytest.approx(16.1)
    no_innings = parse_scoreboard_html(_scoreboard("1/0", "0/0", "16.1/20 OV", ""))
    assert to_match_context(no_innings, MID, NOW) is None


def test_completed_comment_marks_match_completed():
    html = _scoreboard("183/5", "150/9", "INN 2 | 20/20 OV",
                       "Beta won by 33 runs",
                       extra_home="<span>9.15</span><span>RR</span>",
                       extra_away='<span class="sr-simcrick-scb__extra-value">7.50</span><span>CRR</span>')
    state = parse_scoreboard_html(html)
    ctx = to_match_context(state, MID, NOW)
    assert ctx.status == MatchStatus.COMPLETED
    assert ctx.is_live is False


# ── result comment forms (UNVERIFIED wording, see module) ─────────────────────


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Trinbago Knight Riders SRL won by 25 runs", ("Trinbago Knight Riders SRL", "runs", 25)),
        ("St Lucia Kings SRL won by 1 run", ("St Lucia Kings SRL", "runs", 1)),
        ("Montreal Warriors won by 6 wickets", ("Montreal Warriors", "wickets", 6)),
        ("Match tied", (None, "tie", None)),
        ("No result", (None, "no_result", None)),
    ],
)
def test_result_comment_forms(text, expected):
    assert parse_result_comment(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Trinbago Knight Riders SRL are 74/7 after 16.1 overs chasing 183",
        "",
        "03 Nov 04:30",
    ],
)
def test_non_result_comment_returns_none(text):
    assert parse_result_comment(text) is None
