"""S76: results reconciliation, set_result, settlement after a hand-written result, and the verified wording.

Fakes and fixtures only: no database is opened (a test that would touch one fails loudly instead).
"""

from __future__ import annotations

import asyncio
import json
import operator
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList, TextClause

from backend.models.paper_bets import PaperBet
from backend.models.results import MatchResultRecord
from scraper.lotus_result_detector import parse_result_comment as detector_parse
from scraper.parsers import lotusbook_json as lj
from scraper.parsers import lotusbook_score as ls
from scraper.parsers.lotusbook_scoreboard import parse_result_comment as scoreboard_parse
from scripts import data_quality_monitor as dqm
from scripts import paper_report as rep
from scripts import results_reconciliation as rr
from scripts import set_result as sr
from strategy.base import bet_gross_pnl
from virtual_trading import paper_ledger as pl

UTC = timezone.utc
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "s76"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- verified wording (fixtures)


def test_finished_text_is_parsed_as_a_win_by_both_parsers():
    fx = _fixture("finished_livescore_v2_lb_36267578.json")
    text_value = fx["result_text"]
    assert text_value == "Biratnagar Kings won by 2 runs \U0001F3C6"
    assert scoreboard_parse(text_value) == ("Biratnagar Kings", "runs", 2)
    assert detector_parse(text_value) == ("win", "Biratnagar Kings", "2 runs")
    assert fx["parsed_as"] == {"result_type": "win", "winner": "Biratnagar Kings", "margin": "2 runs"}


def _score_body(result_text: str, overs: str = "20.0") -> dict[str, Any]:
    """A score/v3 body built from the final stored context row of the fixture (the raw body was not logged)."""
    return {
        "success": True,
        "result": {
            "scorePipeline": "livescore-v2",
            "stats": {
                "battingTeam": {"name": "Janakpur Bolts", "score": 172, "wickets": 7, "overs": overs,
                                "balls": 120, "runRate": 8.6},
                "currentInning": 2,
                "remainingBalls": 0,
                "totalBalls": 240,
                "oversPerInning": 20,
                "matchFormat": "T20",
                "result": result_text,
                "teams": [{"n": "Biratnagar Kings"}, {"n": "Janakpur Bolts"}],
                "updatedAt": "2026-11-03T10:20:55.4349150Z",
            },
        },
    }


def test_finished_livescore_v2_body_is_finished_and_maps_to_completed_context():
    fx = _fixture("finished_livescore_v2_lb_36267578.json")
    state = ls.parse_score_v3(_score_body(fx["result_text"]))
    assert state is not None and state.pipeline == "livescore-v2"
    assert state.is_finished
    row = ls.to_context_row(state, fx["match_id"], fx["team_home"], fx["team_away"])
    final = fx["final_context_row"]
    assert row["status"] == "completed" and row["is_live"] is False
    assert row["score"] == final["score"] == 172 and row["wickets"] == final["wickets"] == 7
    assert row["bowling_team"] == "Biratnagar Kings"
    assert row["balls_remaining"] == final["balls_remaining"] == 0


def test_live_body_without_result_text_is_not_finished():
    state = ls.parse_score_v3(_score_body("", overs="19.5"))
    assert state is not None and not state.is_finished
    row = ls.to_context_row(state, "lb_36267578", "Biratnagar Kings", "Janakpur Bolts")
    assert row["status"] == "live" and row["is_live"] is True


def test_result_wording_variants_parse_as_tie_or_no_result_in_both_parsers():
    for wording in ("Match tied", "No result"):
        assert detector_parse(wording) is not None
        assert scoreboard_parse(wording) is not None
    assert detector_parse("Match tied")[0] == "tie"
    assert detector_parse("No result")[0] == "no_result"


def _market(name: str, status: str, runners: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "id": "1.264887422",
        "name": name,
        "status": status,
        "runners": [{"name": n, "status": s, "back": [], "lay": []} for n, s in runners],
    }


def test_open_winner_market_at_the_end_of_a_real_match_is_not_settled():
    fx = _fixture("winner_market_end_of_real_match_lb_36267578.json")
    runners = [(r["selection"], r["runner_status"].upper()) for r in fx["rows"][:2]]
    assert all(status == "ACTIVE" for _n, status in runners)
    payload = {"result": [_market("Winner", "OPEN", runners)]}
    assert lj.settlement_of(payload) is None


def test_closed_winner_market_observed_has_no_winner_runner():
    fx = _fixture("winner_market_closed_observed.json")
    real = [(r["selection"], r["runner_status"].upper()) for r in fx["rows"] if r["match_id"] == "lb_76227860"]
    settled = lj.settlement_of({"result": [_market("Winner", "CLOSED", real)]})
    assert settled is not None
    assert settled.winner is None  # nothing says WINNER, so the settled path does not write a winner
    assert {status for _n, status in settled.runner_statuses} == {"SUSPENDED"}


def test_closed_market_with_a_winner_runner_names_the_winner_unverified_wording():
    # UNVERIFIED wording (no stored row has it): the parser accepts WINNER or WON for exactly one runner.
    payload = {"result": [_market("Winner", "CLOSED", [("Alpha", "WINNER"), ("Beta", "LOSER")])]}
    assert lj.settlement_of(payload).winner == "Alpha"


# ---------------------------------------------------------------- reconciliation selection (3 h rule)


def _res_row(match_id: str = "m1", hours: float = 4.0, **overrides: Any) -> dict[str, Any]:
    row = {
        "match_id": match_id,
        "team_home": "Alpha",
        "team_away": "Beta",
        "competition": "ICC T20",
        "is_simulated": False,
        "match_start_time": NOW - timedelta(hours=hours + 2),
        "last_time": NOW - timedelta(hours=hours),
        "age_s": hours * 3600,
        "has_result": False,
        "winner": None,
        "result_type": None,
    }
    row.update(overrides)
    return row


def test_three_hour_rule_boundary():
    rows = [
        _res_row("fresh", hours=2.9),
        _res_row("exactly", hours=3.0),
        _res_row("stale", hours=3.1),
    ]
    missing = rr.select_missing(rows, NOW)
    assert [m["match_id"] for m in missing] == ["stale"]
    assert missing[0]["age_hours"] == pytest.approx(3.1)


def test_srl_is_excluded_by_registry_flag_and_by_name():
    rows = [
        _res_row("flag", is_simulated=True),
        _res_row("name", competition="T20 International SRL", team_home="Australia SRL", team_away="West Indies SRL",
                 is_simulated=None),
        _res_row("real"),
    ]
    assert [m["match_id"] for m in rr.select_missing(rows, NOW)] == ["real"]


@pytest.mark.parametrize("result_type,winner", [
    ("win", "Alpha"), ("tie", ""), ("no_result", ""), ("abandoned", ""),
])
def test_decided_results_are_excluded(result_type, winner):
    row = _res_row("done", has_result=True, result_type=result_type, winner=winner)
    assert rr.select_missing([row], NOW) == []


@pytest.mark.parametrize("result_type,winner", [
    ("unresolved", ""), ("win", ""), (None, None),
])
def test_undecided_rows_are_listed(result_type, winner):
    row = _res_row("open", has_result=result_type is not None, result_type=result_type, winner=winner)
    assert [m["match_id"] for m in rr.select_missing([row], NOW)] == ["open"]


def test_missing_list_is_oldest_first_and_carries_teams_competition_and_start():
    rows = [_res_row("newer", hours=5.0), _res_row("older", hours=8.0, team_home="Gamma", team_away="Delta")]
    missing = rr.select_missing(rows, NOW)
    assert [m["match_id"] for m in missing] == ["older", "newer"]
    first = missing[0]
    assert (first["team_home"], first["team_away"], first["competition"]) == ("Gamma", "Delta", "ICC T20")
    assert first["match_start_time"] == NOW - timedelta(hours=10)
    line = rr.format_line(first)
    assert "Gamma v Delta (older)" in line and "ICC T20" in line
    assert "start 2026-11-03 02:00 UTC" in line and "last tick 2026-11-03 04:00 UTC" in line


class _MappingResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)


class _RecordingSession:
    def __init__(self, rows):
        self.rows = rows
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    async def execute(self, stmt, params=None):
        self.calls.append((stmt, params or {}))
        return _MappingResult(self.rows)


def test_missing_results_runs_one_select_over_the_lookback_window():
    session = _RecordingSession([_res_row("m1", hours=4.0)])
    missing = asyncio.run(rr.missing_results(session, NOW))
    assert [m["match_id"] for m in missing] == ["m1"]
    (stmt, params), = session.calls
    assert isinstance(stmt, TextClause)
    assert params == {"since": NOW - timedelta(days=7), "now": NOW}
    sql = rr.RESULTS_SQL.upper()
    assert "SELECT" in sql and not any(word in sql for word in ("INSERT ", "UPDATE ", "DELETE ", "MERGE "))


# ---------------------------------------------------------------- monitor check and wiring


def _monitor_fetch(rows):
    async def fetch(name, params):
        assert name == "results", name
        return [dict(r) for r in rows]

    return fetch


def test_results_missing_warns_for_a_real_match_three_hours_silent():
    rows = [_res_row("silent", hours=4.0), _res_row("srl", hours=4.0, is_simulated=True)]
    check = asyncio.run(dqm.check_results_missing(_monitor_fetch(rows), {"now": NOW}))
    assert check["name"] == "results_missing" and check["status"] == "warn"
    assert check["value"] == 1
    assert "Alpha v Beta (silent)" in check["detail"] and "srl" not in check["detail"]


def test_results_missing_is_ok_when_nothing_is_missing():
    rows = [_res_row("recent", hours=1.0), _res_row("done", hours=5.0, has_result=True, result_type="tie")]
    check = asyncio.run(dqm.check_results_missing(_monitor_fetch(rows), {"now": NOW}))
    assert check["status"] == "ok" and check["value"] == 0


def test_results_missing_is_registered_appended_and_reads_the_results_query():
    names = [fn.__name__ for fn in dqm.CHECKS]
    assert "check_results_missing" in names
    assert names.index("check_results_missing") > names.index("check_results")  # appended, existing order kept
    assert dqm.SQL["results"] is rr.RESULTS_SQL


# ---------------------------------------------------------------- set_result: validation (no database)


def _args(**overrides: Any) -> Any:
    base = dict(match="m1", winner=None, tie=False, no_result=False, margin=None,
                source="public_scorecard", url=None, force=False, dry_run=False)
    base.update(overrides)
    return type("Args", (), base)


def test_canonical_winner_accepts_case_and_spacing_and_returns_registry_spelling():
    assert sr.canonical_winner("melbourne   stars w", "Melbourne Stars W", "Perth Scorchers W") == "Melbourne Stars W"
    assert sr.canonical_winner(" PERTH scorchers w ", "Melbourne Stars W", "Perth Scorchers W") == "Perth Scorchers W"


@pytest.mark.parametrize("winner", ["Sydney Sixers W", "", "   "])
def test_canonical_winner_refuses_a_name_that_is_not_a_registry_team(winner):
    with pytest.raises(sr.ResultError):
        sr.canonical_winner(winner, "Melbourne Stars W", "Perth Scorchers W")


def test_build_request_needs_exactly_one_outcome():
    with pytest.raises(sr.ResultError, match="exactly one"):
        sr.build_request(_args())
    with pytest.raises(sr.ResultError, match="exactly one"):
        sr.build_request(_args(winner="Alpha", tie=True))


def test_build_request_rejects_margin_on_a_tie_bad_source_and_bad_url():
    with pytest.raises(sr.ResultError, match="margin"):
        sr.build_request(_args(tie=True, margin="15 runs"))
    with pytest.raises(sr.ResultError, match="source"):
        sr.build_request(_args(winner="Alpha", source="twitter"))
    with pytest.raises(sr.ResultError, match="url"):
        sr.build_request(_args(winner="Alpha", url="ftp://x"))


def test_build_request_accepts_a_win_with_margin():
    req = sr.build_request(_args(winner=" Alpha ", margin="15 runs", url="https://example.com/sc"))
    assert (req.result_type, req.winner, req.margin, req.source) == ("win", "Alpha", "15 runs", "public_scorecard")


def test_main_refuses_bad_input_without_touching_the_database(monkeypatch, capsys):
    import shared.db as shared_db

    def no_db(*_a, **_k):
        raise AssertionError("the database must not be opened on a refused request")

    monkeypatch.setattr(shared_db, "get_session_factory", no_db)
    assert sr.main(["--match", "m1", "--tie", "--winner", "Alpha"]) == 1
    assert sr.main(["--match", "m1"]) == 1
    assert "refused" in capsys.readouterr().err


# ---------------------------------------------------------------- set_result: fake session


def _bet(**overrides: Any) -> PaperBet:
    fields = dict(
        bet_id=uuid.uuid4(), variant="w05_e03", match_id="m1", decided_at=NOW - timedelta(hours=5),
        filled_at=NOW - timedelta(hours=5) + timedelta(seconds=60), status="filled", side="BACK",
        selection="home", team="Alpha", price_limit=2.0, price_filled=2.0, stake=100.0, liability=100.0,
        reason="", p_model=0.55, p_market=0.45, p_used=0.5, edge=0.05, source="prematch", in_play=False,
        score_state=None, book={}, pnl=None, settled_at=None, result=None, clv=None, closing_price=None,
    )
    fields.update(overrides)
    return PaperBet(**fields)


_OPS = {operator.eq: operator.eq, operator.ne: operator.ne, operator.lt: operator.lt, operator.le: operator.le,
        operator.gt: operator.gt, operator.ge: operator.ge}


def _eval(clause, row) -> bool:
    if isinstance(clause, BooleanClauseList):
        return all(_eval(c, row) for c in clause.clauses)
    assert isinstance(clause, BinaryExpression), type(clause)
    value = getattr(row, clause.left.key)
    target = clause.right.value
    if clause.operator.__name__ == "in_op":
        return value in target
    return _OPS[clause.operator](value, target)


class _Res:
    def __init__(self, mapping_rows=(), scalar_rows=()):
        self._mapping_rows = list(mapping_rows)
        self._scalar_rows = list(scalar_rows)

    def mappings(self):
        return self

    def first(self):
        return self._mapping_rows[0] if self._mapping_rows else None

    def scalars(self):
        return self

    def all(self):
        return list(self._scalar_rows) if self._scalar_rows else list(self._mapping_rows)


RESULT_COLUMNS = [c.name for c in MatchResultRecord.__table__.columns]


class FakeSession:
    """In-memory stand-in for the async session: registry, match_results, odds_ticks (latest row) and paper_bets."""

    def __init__(self, registry=(), results=(), bets=(), odds=()):
        self.registry = {r["match_id"]: dict(r) for r in registry}
        self.results = {r["match_id"]: dict(r) for r in results}
        self.bets = list(bets)
        self.odds = list(odds)
        self.merged: list[MatchResultRecord] = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, stmt, params=None):
        params = params or {}
        if isinstance(stmt, TextClause):
            sql = stmt.text
            mid = params.get("match_id")
            if "FROM match_training_status" in sql:
                return _Res(mapping_rows=[self.registry[mid]] if mid in self.registry else [])
            if "FROM match_results" in sql:
                return _Res(mapping_rows=[self.results[mid]] if mid in self.results else [])
            if "FROM odds_ticks" in sql:
                rows = sorted((o for o in self.odds if o["match_id"] == mid),
                              key=lambda o: (o["time"], o.get("tick_seq", 0)), reverse=True)
                return _Res(mapping_rows=rows[:1])
            raise AssertionError(f"unexpected text query: {sql}")
        criteria = stmt.whereclause
        return _Res(scalar_rows=[b for b in self.bets if criteria is None or _eval(criteria, b)])

    async def merge(self, record):
        self.merged.append(record)
        self.results[record.match_id] = {c: getattr(record, c, None) for c in RESULT_COLUMNS}
        return record

    async def flush(self):
        return None

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    def add_all(self, rows):
        self.bets.extend(rows)


REGISTRY = [{"match_id": "m1", "team_home": "Alpha", "team_away": "Beta", "competition": "ICC T20",
             "is_simulated": False}]
ODDS = [{"match_id": "m1", "time": NOW - timedelta(hours=5), "tick_seq": 1, "back_home": 1.9, "lay_home": 2.0,
         "back_away": 2.1, "lay_away": 2.2},
        {"match_id": "m1", "time": NOW - timedelta(hours=4), "tick_seq": 2, "back_home": 1.5, "lay_home": 1.6,
         "back_away": 2.8, "lay_away": 3.0}]


def _request(**overrides: Any) -> sr.Request:
    base = dict(match_id="m1", result_type="win", winner="Alpha", margin="15 runs", source="public_scorecard",
                url=None, force=False, dry_run=False)
    base.update(overrides)
    return sr.Request(**base)


def _existing(result_type="win", winner="Alpha", **overrides: Any) -> dict[str, Any]:
    row = {"match_id": "m1", "winner": winner, "loser": "Beta" if winner == "Alpha" else "Alpha",
           "result_type": result_type, "margin": "", "source": "lotusbook_scoreboard", "completed_at": NOW,
           "team_home": "Alpha", "team_away": "Beta", "closing_back_home": None, "closing_back_away": None,
           "closing_lay_home": None, "closing_lay_away": None}
    row.update(overrides)
    return row


def _apply(session, request, now=NOW):
    return asyncio.run(sr.apply_result(session, request, now=now))


def test_row_has_the_detector_shape_and_closing_prices_from_the_latest_tick():
    session = FakeSession(registry=REGISTRY, odds=ODDS)
    summary = _apply(session, _request(winner="alpha"))
    (record,) = session.merged
    assert set(sr.build_row(_request(), REGISTRY[0], NOW, None)) == set(RESULT_COLUMNS)
    assert (record.winner, record.loser, record.result_type, record.margin) == ("Alpha", "Beta", "win", "15 runs")
    assert (record.team_home, record.team_away, record.source) == ("Alpha", "Beta", "public_scorecard")
    assert record.completed_at == NOW
    assert (record.closing_back_home, record.closing_lay_home) == (1.5, 1.6)  # the newer tick, not the older
    assert summary["written"]["winner"] == "Alpha" and summary["replaced"] is None


@pytest.mark.parametrize("result_type", ["tie", "no_result"])
def test_tie_and_no_result_write_empty_winner_and_loser(result_type):
    session = FakeSession(registry=REGISTRY, odds=ODDS)
    _apply(session, _request(result_type=result_type, winner=None, margin=""))
    record = session.merged[0]
    assert (record.result_type, record.winner, record.loser) == (result_type, "", "")


def test_refuses_to_overwrite_a_trusted_result_without_force():
    for trusted in ("win", "tie", "no_result"):
        existing = _existing(result_type=trusted, winner="Alpha" if trusted == "win" else "")
        session = FakeSession(registry=REGISTRY, results=[existing], odds=ODDS)
        with pytest.raises(sr.ResultError, match="--force"):
            _apply(session, _request(winner="Beta"))
        assert session.merged == []  # nothing written


def test_an_unresolved_row_can_be_replaced_without_force():
    existing = _existing(result_type="unresolved", winner="")
    session = FakeSession(registry=REGISTRY, results=[existing], odds=ODDS)
    summary = _apply(session, _request(winner="Beta"))
    assert session.merged[0].winner == "Beta"
    assert summary["replaced"] == "result_type unresolved, winner -, source lotusbook_scoreboard"


def test_force_replaces_a_trusted_result_when_no_paper_bet_is_settled():
    session = FakeSession(registry=REGISTRY, results=[_existing()], odds=ODDS)
    _apply(session, _request(winner="Beta", force=True))
    assert session.merged[0].winner == "Beta"


def test_force_is_still_refused_when_paper_bets_are_already_settled_on_the_old_result():
    settled = _bet(status="settled", result="home", pnl=100.0, settled_at=NOW)
    session = FakeSession(registry=REGISTRY, results=[_existing()], bets=[settled], odds=ODDS)
    with pytest.raises(sr.ResultError, match="already settled"):
        _apply(session, _request(winner="Beta", force=True))
    assert session.merged == []


def test_a_win_without_a_winner_is_not_a_trusted_result():
    existing = _existing(result_type="win", winner="")
    session = FakeSession(registry=REGISTRY, results=[existing], odds=ODDS)
    _apply(session, _request(winner="Alpha"))
    assert session.merged[0].winner == "Alpha"


def test_unknown_match_and_unknown_winner_are_refused():
    with pytest.raises(sr.ResultError, match="no row in match_training_status"):
        _apply(FakeSession(registry=REGISTRY, odds=ODDS), _request(match_id="m404"))
    with pytest.raises(sr.ResultError, match="neither"):
        _apply(FakeSession(registry=REGISTRY, odds=ODDS), _request(winner="Gamma"))


# ---------------------------------------------------------------- settlement after set_result


def test_a_filled_bet_settles_after_set_result_with_the_win_pnl():
    bet = _bet(side="BACK", selection="home", price_filled=2.0, stake=100.0)
    other = _bet(match_id="m2", selection="away")
    session = FakeSession(registry=REGISTRY + [{"match_id": "m2", "team_home": "Gamma", "team_away": "Delta",
                                                  "competition": "ICC T20", "is_simulated": False}],
                          bets=[bet, other], odds=ODDS)
    summary = _apply(session, _request(winner="Alpha"))
    assert (bet.status, bet.result) == ("settled", "home")
    assert bet.pnl == pytest.approx(bet_gross_pnl("BACK", "home", 2.0, 100.0, "home"))
    assert other.status == "filled"  # another match's bet is untouched
    assert summary["bets_settled"] == {"settled": 1}


@pytest.mark.parametrize("result_type", ["tie", "no_result"])
def test_tie_and_no_result_void_the_filled_bets(result_type):
    bet = _bet(side="LAY", selection="away", price_filled=3.0, stake=100.0)
    session = FakeSession(registry=REGISTRY, bets=[bet], odds=ODDS)
    summary = _apply(session, _request(result_type=result_type, winner=None, margin=""))
    assert (bet.status, bet.result, bet.pnl) == ("void", "void", 0.0)
    assert summary["bets_settled"] == {"void": 1}


def test_settlement_rule_for_void_types_in_the_ledger():
    for result_type in ("tie", "no_result", "abandoned"):
        assert pl.resolve_outcome({"winner": "", "result_type": result_type, "team_home": "A", "team_away": "B"}) == "void"
    assert pl.resolve_outcome({"winner": "", "result_type": "unresolved", "team_home": "A", "team_away": "B"}) is None
    assert pl.resolve_outcome({"winner": "A", "result_type": "win", "team_home": "A", "team_away": "B"}) == "home"


# ---------------------------------------------------------------- dry run and commit


class _Ctx:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_exc):
        return False


def test_dry_run_rolls_back_and_a_real_run_commits():
    session = FakeSession(registry=REGISTRY, bets=[_bet()], odds=ODDS)
    summary = asyncio.run(sr.run(_request(dry_run=True), now=NOW, session_factory=lambda: _Ctx(session)))
    assert summary["dry_run"] is True and session.rolled_back and not session.committed
    assert "dry run" in sr.render_summary(summary)

    session2 = FakeSession(registry=REGISTRY, bets=[_bet()], odds=ODDS)
    summary2 = asyncio.run(sr.run(_request(), now=NOW, session_factory=lambda: _Ctx(session2)))
    assert summary2["dry_run"] is False and session2.committed and not session2.rolled_back
    assert "dry run" not in sr.render_summary(summary2)


def test_render_summary_names_the_outcome_and_the_settled_counts():
    session = FakeSession(registry=REGISTRY, bets=[_bet()], odds=ODDS)
    summary = _apply(session, _request(url="https://example.com/sc"))
    text_out = sr.render_summary(summary)
    assert "Alpha v Beta (m1, ICC T20)" in text_out
    assert "win for Alpha by 15 runs" in text_out
    assert "paper bets settled now: 1 settled" in text_out
    assert "https://example.com/sc" in text_out


# ---------------------------------------------------------------- paper report section


def test_report_lists_missing_matches_and_says_none_when_empty():
    missing = rr.select_missing([_res_row("gone", hours=6.0)], NOW)
    block = "\n".join(rep._missing_block(missing))
    assert "## Results missing" in block and "gone" in block and "set_result.py" in block
    assert "None:" in "\n".join(rep._missing_block([]))


def test_report_render_without_missing_is_unchanged():
    state = rep.review.review([], {"w05_e03": 1_000_000.0}, now=NOW, n_resamples=50, draws=50)
    assert "## Results missing" not in rep.render(state, [], NOW)
    assert "## Results missing" in rep.render(state, [], NOW, [])


def test_a_listed_match_that_never_went_live_is_not_missing_a_result():
    """2026-11-03: Sudurpaschim Royals v Karnali Yaks (starts 6 Nov) was listed as missing a result."""
    from datetime import datetime, timedelta, timezone

    from scripts.results_reconciliation import select_missing

    now = datetime(2026, 11, 3, 21, 0, tzinfo=timezone.utc)
    base = {"match_id": "lb_1", "team_home": "A", "team_away": "B", "competition": "Nepal Premier League",
            "is_simulated": False, "match_start_time": now + timedelta(days=2), "last_time": now - timedelta(hours=5),
            "has_result": False, "winner": None, "result_type": None}
    assert select_missing([{**base, "went_live": False}], now) == []
    assert len(select_missing([{**base, "went_live": True}], now)) == 1
