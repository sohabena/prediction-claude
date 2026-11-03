"""S71: paper bet ledger, fills, settlement, CLV and position (fakes only, no database)."""

from __future__ import annotations

import asyncio
import importlib.util
import operator
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList

from backend.models.base import Base
from backend.models.paper_bets import PaperBet
from shared.metrics import clv_prob
from virtual_trading import paper_ledger as pl

ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
NOW = T0 + timedelta(minutes=5)


# ---------------------------------------------------------------- fakes


_OPS = {
    operator.eq: operator.eq,
    operator.ne: operator.ne,
    operator.lt: operator.lt,
    operator.le: operator.le,
    operator.gt: operator.gt,
    operator.ge: operator.ge,
}


def _eval(clause, row) -> bool:
    """Evaluate the subset of SQL WHERE used by the ledger against an in-memory row."""
    if isinstance(clause, BooleanClauseList):
        return all(_eval(c, row) for c in clause.clauses)
    assert isinstance(clause, BinaryExpression), type(clause)
    value = getattr(row, clause.left.key)
    target = clause.right.value
    name = clause.operator.__name__
    if name == "in_op":
        return value in target
    return _OPS[clause.operator](value, target)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class FakeSession:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.flushes = 0

    def add_all(self, rows):
        self.rows.extend(rows)

    async def flush(self):
        self.flushes += 1

    async def execute(self, stmt):
        criteria = stmt.whereclause
        matched = [r for r in self.rows if criteria is None or _eval(criteria, r)]
        return _Result(matched)


def _book(**prices):
    """Book dict in the v_match_odds shape: back_home, lay_home, back_away, lay_away."""
    base = {"back_home": None, "lay_home": None, "back_away": None, "lay_away": None}
    base.update(prices)
    return base


def _row(**overrides):
    fields = dict(
        bet_id=uuid.uuid4(),
        variant="w05_e03",
        match_id="m1",
        decided_at=T0,
        filled_at=None,
        status="pending",
        side="BACK",
        selection="home",
        team="Alpha",
        price_limit=2.0,
        price_filled=None,
        stake=100.0,
        liability=100.0,
        reason="",
        p_model=0.55,
        p_market=0.45,
        p_used=0.5,
        edge=0.05,
        source="prematch",
        in_play=False,
        score_state=None,
        book={},
        pnl=None,
        settled_at=None,
        result=None,
        clv=None,
        closing_price=None,
    )
    fields.update(overrides)
    return PaperBet(**fields)


def _filled(**overrides):
    base = dict(status="filled", filled_at=T0 + timedelta(seconds=60), price_filled=2.0)
    base.update(overrides)
    return _row(**base)


def _ctx(**overrides):
    ctx = dict(
        decided_at=T0,
        team_home="Alpha",
        team_away="Beta",
        p_model={"home": 0.55, "away": 0.45},
        p_market={"home": 0.48, "away": 0.52},
        p_used={"home": 0.5, "away": 0.5},
        edge={"home": 0.02, "away": -0.02},
        source="prematch",
        in_play=False,
        score_state=None,
        book=_book(back_home=2.0, lay_home=2.02, back_away=2.1, lay_away=2.12),
    )
    ctx.update(overrides)
    return ctx


def _intent(side="BACK", selection="home", limit=2.0, stake=100.0, reason="edge"):
    return SimpleNamespace(side=side, selection=selection, price_limit=limit, stake=stake, reason=reason)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- model and migration


def test_model_is_a_plain_table_with_the_required_columns():
    table = PaperBet.__table__
    assert table.name == "paper_bets"
    assert "paper_bets" in Base.metadata.tables
    expected = {
        "bet_id", "variant", "match_id", "decided_at", "filled_at", "status", "side", "selection", "team",
        "price_limit", "price_filled", "stake", "liability", "reason", "p_model", "p_market", "p_used", "edge",
        "source", "in_play", "score_state", "book", "pnl", "settled_at", "result", "clv", "closing_price",
    }
    assert expected <= set(table.columns.keys())
    assert [c.name for c in table.primary_key.columns] == ["bet_id"]
    index_cols = {tuple(c.name for c in ix.columns) for ix in table.indexes}
    assert ("variant", "decided_at") in index_cols
    assert ("match_id",) in index_cols


def test_db_init_registers_the_model():
    text = (ROOT / "backend" / "db_init.py").read_text(encoding="utf-8")
    assert "from backend.models.paper_bets import PaperBet" in text


def test_alembic_008_follows_007_and_creates_the_table():
    spec = importlib.util.spec_from_file_location(
        "rev008", ROOT / "alembic" / "versions" / "008_paper_bets.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "008_paper_bets"
    assert mod.down_revision == "007_toss_fields"
    source = (ROOT / "alembic" / "versions" / "008_paper_bets.py").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS paper_bets" in source
    assert "idx_paper_bets_variant_decided" in source


# ---------------------------------------------------------------- record_intents


def test_record_intents_adds_pending_rows_with_context():
    session = FakeSession()
    intents = [_intent("BACK", "home", 2.0, 100.0), _intent("LAY", "away", 2.2, 50.0, reason="hedge")]
    rows = _run(pl.record_intents(session, "w05_e03", "m1", intents, _ctx()))
    assert len(rows) == 2 and session.rows == rows
    back, lay = rows
    assert back.status == "pending" and back.filled_at is None and back.price_filled is None
    assert back.team == "Alpha" and lay.team == "Beta"
    assert back.p_used == 0.5 and back.edge == 0.02
    assert lay.edge == -0.02 and lay.reason == "hedge"
    assert back.liability == 100.0
    assert lay.liability == pytest.approx(50.0 * (2.2 - 1.0))
    assert back.book["back_home"] == 2.0
    assert isinstance(back.bet_id, uuid.UUID) and back.bet_id != lay.bet_id


def test_record_intents_requires_aware_decision_time():
    with pytest.raises(ValueError):
        _run(pl.record_intents(FakeSession(), "v", "m1", [_intent()], _ctx(decided_at=datetime(2026, 1, 1))))


# ---------------------------------------------------------------- fill_pending


def test_fill_happens_at_the_price_60_seconds_after_decision():
    session = FakeSession([_row()])
    asked = []

    def book_at(match_id, t):
        asked.append((match_id, t))
        return _book(back_home=2.1)

    counts = _run(pl.fill_pending(session, book_at, now=NOW))
    row = session.rows[0]
    assert counts == {"filled": 1, "unfilled": 0}
    assert asked == [("m1", T0 + timedelta(seconds=60))]
    assert row.status == "filled"
    assert row.price_filled == 2.1
    assert row.filled_at == T0 + timedelta(seconds=60)
    assert row.liability == 100.0


def test_back_worse_than_limit_is_unfilled():
    session = FakeSession([_row(price_limit=2.0)])
    counts = _run(pl.fill_pending(session, lambda m, t: _book(back_home=1.9), now=NOW))
    assert counts == {"filled": 0, "unfilled": 1}
    assert session.rows[0].status == "unfilled"
    assert session.rows[0].price_filled is None


def test_lay_worse_than_limit_is_unfilled_and_better_fills():
    worse = FakeSession([_row(side="LAY", price_limit=2.0, liability=100.0)])
    _run(pl.fill_pending(worse, lambda m, t: _book(lay_home=2.2), now=NOW))
    assert worse.rows[0].status == "unfilled"

    better = FakeSession([_row(side="LAY", price_limit=2.0, liability=100.0)])
    _run(pl.fill_pending(better, lambda m, t: _book(lay_home=1.9), now=NOW))
    assert better.rows[0].status == "filled"
    assert better.rows[0].price_filled == 1.9
    assert better.rows[0].liability == pytest.approx(100.0 * 0.9)


def test_missing_takeable_price_is_unfilled():
    session = FakeSession([_row()])
    _run(pl.fill_pending(session, lambda m, t: _book(lay_home=2.0), now=NOW))  # back_home missing
    assert session.rows[0].status == "unfilled"


def test_bets_younger_than_60_seconds_stay_pending():
    session = FakeSession([_row(decided_at=NOW - timedelta(seconds=30))])
    counts = _run(pl.fill_pending(session, lambda m, t: _book(back_home=2.5), now=NOW))
    assert counts == {"filled": 0, "unfilled": 0}
    assert session.rows[0].status == "pending"


# ---------------------------------------------------------------- settle


def _res(winner, result_type="win"):
    return {"winner": winner, "result_type": result_type, "team_home": "Alpha", "team_away": "Beta"}


def test_back_bet_wins_and_loses():
    win = FakeSession([_filled(side="BACK", selection="home", price_filled=2.0, stake=100.0)])
    _run(pl.settle(win, {"m1": _res("Alpha")}, now=NOW))
    assert win.rows[0].status == "settled" and win.rows[0].result == "home"
    assert win.rows[0].pnl == pytest.approx(100.0)
    assert win.rows[0].settled_at == NOW

    lose = FakeSession([_filled(side="BACK", selection="home", price_filled=2.0, stake=100.0)])
    _run(pl.settle(lose, {"m1": _res("Beta")}, now=NOW))
    assert lose.rows[0].status == "settled" and lose.rows[0].result == "away"
    assert lose.rows[0].pnl == pytest.approx(-100.0)


def test_lay_bet_wins_and_loses():
    lay_wins = FakeSession([_filled(side="LAY", selection="home", price_filled=3.0, stake=100.0)])
    _run(pl.settle(lay_wins, {"m1": _res("Beta")}, now=NOW))
    assert lay_wins.rows[0].pnl == pytest.approx(100.0)

    lay_loses = FakeSession([_filled(side="LAY", selection="home", price_filled=3.0, stake=100.0)])
    _run(pl.settle(lay_loses, {"m1": _res("Alpha")}, now=NOW))
    assert lay_loses.rows[0].pnl == pytest.approx(-200.0)


def test_tie_voids_the_bet_with_zero_pnl():
    session = FakeSession([_filled(side="BACK", price_filled=2.0)])
    _run(pl.settle(session, {"m1": _res("", "tie")}, now=NOW))
    row = session.rows[0]
    assert row.status == "void" and row.result == "void" and row.pnl == 0.0


@pytest.mark.parametrize("rtype", ["no_result", "abandoned"])
def test_no_result_and_abandoned_void(rtype):
    session = FakeSession([_filled(side="LAY", price_filled=3.0)])
    _run(pl.settle(session, {"m1": _res("", rtype)}, now=NOW))
    assert session.rows[0].status == "void" and session.rows[0].pnl == 0.0


def test_unresolved_result_leaves_the_bet_filled():
    session = FakeSession([_filled(), _filled(match_id="m2")])
    _run(pl.settle(session, {"m1": _res("", "unresolved"), "m2": _res("Gamma")}, now=NOW))
    assert session.rows[0].status == "filled" and session.rows[0].pnl is None
    assert session.rows[1].status == "filled"  # winner matches neither team: waits, not guessed


def test_settle_ignores_bets_that_are_not_filled_and_matches_without_result():
    pending = _row(status="pending")
    filled_other = _filled(match_id="m9")
    session = FakeSession([pending, filled_other])
    changed = _run(pl.settle(session, {"m1": _res("Alpha")}, now=NOW))
    assert changed == []
    assert pending.status == "pending" and filled_other.status == "filled"


def test_resolve_outcome_normalises_names():
    assert pl.resolve_outcome({"winner": "  alpha ", "result_type": "win",
                               "team_home": "Alpha", "team_away": "Beta"}) == "home"
    assert pl.resolve_outcome(None) is None


# ---------------------------------------------------------------- CLV


def test_clv_sign_for_back_bets():
    row = _filled(side="BACK", price_filled=2.0)
    assert pl.clv_for(row, 1.8) > 0  # the price shortened after the bet: we got the better price
    assert pl.clv_for(row, 2.2) < 0
    assert pl.clv_for(row, 2.0) == pytest.approx(0.0)
    assert pl.clv_for(row, 1.8) == pytest.approx(clv_prob("BACK", 2.0, 1.8))


def test_clv_sign_for_lay_bets():
    row = _filled(side="LAY", price_filled=3.0)
    assert pl.clv_for(row, 3.5) > 0  # lay at 3.0, the market then offered 3.5: we laid too short
    assert pl.clv_for(row, 2.5) < 0
    assert pl.clv_for(row, 3.5) == pytest.approx(clv_prob("LAY", 3.0, 3.5))


def test_clv_none_without_prices():
    assert pl.clv_for(_filled(price_filled=None), 2.0) is None
    assert pl.clv_for(_filled(), None) is None


def test_reference_price_prematch_is_last_price_before_off():
    row = _filled(side="BACK", selection="home", in_play=False)
    seen = []

    def book_at(match_id, t):
        seen.append(t)
        return _book(back_home=1.8)

    off = T0 + timedelta(hours=2)
    assert pl.reference_price(row, book_at, off_at=off) == 1.8
    assert seen == [off]


def test_reference_price_inplay_is_same_side_10_minutes_after_fill():
    row = _filled(side="LAY", selection="away", in_play=True, filled_at=T0 + timedelta(minutes=30))
    seen = []

    def book_at(match_id, t):
        seen.append(t)
        return _book(lay_away=2.5, back_away=2.4)

    assert pl.reference_price(row, book_at) == 2.5
    assert seen == [T0 + timedelta(minutes=40)]


def test_record_clv_sets_closing_price_and_clv():
    row = _filled(side="BACK", price_filled=2.0)
    session = FakeSession([row])
    clv = _run(pl.record_clv(session, row, lambda m, t: _book(back_home=1.8), off_at=T0))
    assert row.closing_price == 1.8
    assert row.clv == pytest.approx(clv_prob("BACK", 2.0, 1.8))
    assert clv == row.clv


# ---------------------------------------------------------------- position


def test_position_sums_filled_bets_and_reports_worst_case():
    session = FakeSession([
        _filled(side="BACK", selection="home", price_filled=2.0, stake=100.0),
        _filled(side="LAY", selection="away", price_filled=3.0, stake=50.0),
        _filled(side="BACK", selection="home", price_filled=2.0, stake=10.0, variant="other"),
        _row(side="BACK", selection="home", price_limit=2.0, stake=999.0),  # pending: not exposure
        _filled(side="BACK", selection="away", price_filled=2.0, stake=5.0, match_id="m2"),
    ])
    pos = _run(pl.position(session, "w05_e03", "m1"))
    # BACK home 2.0 x100: home +100, away -100, draw -100. LAY away 3.0 x50: home +50, away -100, draw +50.
    assert pos["home"] == pytest.approx(150.0)
    assert pos["away"] == pytest.approx(-200.0)
    assert pos["draw"] == pytest.approx(-50.0)
    assert pos["liability"] == pytest.approx(200.0)


def test_position_is_zero_with_no_bets():
    pos = _run(pl.position(FakeSession(), "w05_e03", "m1"))
    assert pos == {"home": 0.0, "away": 0.0, "draw": 0.0, "liability": 0.0}

