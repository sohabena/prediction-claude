"""S74: replay of the paper trader over recorded history (fakes only, no database).

Covers: the in-memory session supports the statement shapes of the ledger; the replay feed never returns anything
recorded after its cursor (look-ahead); a decision is the same whether or not later rows exist; two runs are
identical; the replay gives the same intents as a direct run_cycle loop over a naive as-of feed (parity).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, text

from backend.models.paper_bets import PaperBet
from strategy.probability import Estimate
from training.market import band_prob_home
from virtual_trading import paper_ledger as ledger
from virtual_trading import paper_trader as pt
from virtual_trading.memory_session import MemorySession
from virtual_trading.replay_feed import SETTLE_DELAY, ReplayFeed
from scripts import replay_paper as rp

T0 = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
START = T0 + timedelta(hours=2)  # the match starts two hours after T0
END = T0 + timedelta(hours=4)
STEP = timedelta(seconds=15)
M1 = "m1"  # real match
M2 = "srl1"  # simulated (SRL): excluded unless --include-srl


# ---------------------------------------------------------------- synthetic recorded history


def _registry(mid, simulated=False, start=START):
    return {
        "match_id": mid,
        "team_home": f"{mid} home",
        "team_away": f"{mid} away",
        "competition": "Indian Premier League",
        "match_start_time": start,
        "is_simulated": simulated,
        "toss_winner": None,
        "toss_decision": None,
    }


def _tick(mid, t, k):
    """A book that moves: home back cycles 1.8 / 2.0 / 2.2 every 90 s, lay sits 0.1 above back. The away prices keep
    the book consistent (backs sum above 1, lays below 1), so the S59 band exists at every tick."""
    back_home = (1.8, 2.0, 2.2)[(k // 3) % 3]
    return {
        "match_id": mid,
        "time": t,
        "back_home": back_home,
        "lay_home": back_home + 0.1,
        "back_away": 1.8,
        "lay_away": 2.2,
        "source": "listing",
    }


def _result(mid, completed_at, winner="m1 home"):
    return {
        "match_id": mid,
        "winner": winner,
        "loser": "m1 away",
        "result_type": "win",
        "team_home": f"{mid} home",
        "team_away": f"{mid} away",
        "completed_at": completed_at,
    }


def _history(extra_future=False):
    """Recorded rows for two matches. With ``extra_future`` the rows after END are included too."""
    prices, context = [], []
    horizon = END + timedelta(hours=1) if extra_future else END
    for mid in (M1, M2):
        t = T0 - timedelta(minutes=20)
        k = 0
        while t <= horizon:
            prices.append(_tick(mid, t, k))
            t += timedelta(seconds=30)
            k += 1
    for k in range(0, 40):
        t = START + timedelta(minutes=2 * k)
        if t > horizon:
            break
        context.append({
            "match_id": M1, "time": t, "is_live": True, "score": 10 + k, "wickets": 0, "overs": 1.0 + k * 0.3,
            "innings": 1, "balls_remaining": 100, "batting_team": "m1 home", "bowling_team": "m1 away",
            "run_rate": 7.0, "req_run_rate": None, "match_format": "T20", "status": "live",
        })
    registry = [_registry(M1), _registry(M2, simulated=True)]
    results = [_result(M1, START + timedelta(hours=1, minutes=40))]
    if extra_future:
        results.append(_result(M2, END + timedelta(minutes=30)))
    return prices, registry, context, results


class TimedProbability:
    """Stands in for ProbabilityService: the model flips between a strong home and a strong away view every 5 min."""

    def estimate(self, match, book, state=None, *, as_of):
        slot = int((as_of - T0).total_seconds()) // 300
        p_model = 0.7 if slot % 2 == 0 else 0.3
        return Estimate(p_model=p_model, p_market=band_prob_home(book), source="prematch", has_model=True,
                        features={}, reason="")


def _feed(extra_future=False, end=END):
    prices, registry, context, results = _history(extra_future)
    return ReplayFeed(prices=prices, registry=registry, context=context, results=results, end=end)


def _variants():
    return pt.build_variants()


def _intent_key(intent):
    return (intent["match_id"], intent["variant"], intent["side"], intent["selection"],
            intent["price_limit"], intent["stake"], intent["reason"])


# ---------------------------------------------------------------- memory session


def _bet(**overrides):
    base = dict(
        variant="v", match_id=M1, decided_at=T0, side="BACK", selection="home", team="A", stake=1_000.0,
        p_used=0.55, edge=0.1, source="prematch", book={}, price_limit=2.0,
    )
    base.update(overrides)
    return PaperBet(**base)


@pytest.mark.asyncio
async def test_memory_session_supports_every_ledger_shape():
    session = MemorySession()
    book = {"back_home": 2.2, "lay_home": 2.3, "back_away": 1.9, "lay_away": 2.0}

    # record_intents: add_all + flush, defaults applied (pending, not in play, empty reason, uuid key)
    intents = [
        type("I", (), {"side": "BACK", "selection": "home", "price_limit": 2.1, "stake": 5_000.0, "reason": "enter"})(),
    ]
    rows = await ledger.record_intents(
        session, "v", M1, intents,
        {"decided_at": T0, "team_home": "A", "team_away": "B", "source": "prematch", "book": book,
         "p_used": 0.55, "edge": 0.1, "p_model": 0.6, "p_market": 0.5},
    )
    assert len(session.rows) == 1 and rows[0] is session.rows[0]
    assert rows[0].status == "pending" and rows[0].in_play is False and rows[0].bet_id is not None

    # fill_pending: select with two criteria (==, <=)
    counts = await ledger.fill_pending(session, lambda mid, t: book, T0 + timedelta(seconds=60))
    assert counts == {"filled": 1, "unfilled": 0}
    assert rows[0].status == "filled"

    # position: select with three criteria
    pos = await ledger.position(session, "v", M1)
    assert pos["home"] == pytest.approx(5_000 * 1.2) and pos["liability"] == pytest.approx(5_000.0)

    # settle: select with == and in_
    changed = await ledger.settle(
        session, {M1: {"winner": "A", "result_type": "win", "team_home": "A", "team_away": "B"}}, T0 + timedelta(hours=3)
    )
    assert [r.status for r in changed] == ["settled"]

    # _open_rows: select with in_ over a list
    open_rows = await pt._open_rows(session)
    assert open_rows == []


@pytest.mark.asyncio
async def test_memory_session_defaults_are_deterministic_and_db_like():
    first, second = MemorySession(), MemorySession()
    a = _bet()
    b = _bet()
    first.add_all([a])
    second.add_all([b])
    assert a.bet_id == b.bet_id  # the same seeded sequence on every run
    assert a.status == "pending" and a.in_play is False and a.reason == ""
    third = MemorySession()
    third.add_all([_bet(), _bet()])
    assert third.rows[0].bet_id != third.rows[1].bet_id
    explicit = _bet(bet_id=None, status="filled")
    first.add_all([explicit])
    assert explicit.status == "filled" and explicit.bet_id is not None


@pytest.mark.asyncio
async def test_memory_session_refuses_shapes_it_cannot_answer():
    session = MemorySession([_bet()])
    with pytest.raises(NotImplementedError):
        await session.execute(text("SELECT 1"))
    with pytest.raises(NotImplementedError):
        await session.execute(select(PaperBet).where((PaperBet.status == "pending") | (PaperBet.status == "filled")))
    with pytest.raises(NotImplementedError):
        await session.execute(select(PaperBet).order_by(PaperBet.decided_at))


# ---------------------------------------------------------------- look-ahead


@pytest.mark.asyncio
async def test_replay_feed_never_returns_rows_after_the_cursor():
    feed = _feed(extra_future=True, end=END + timedelta(hours=1))
    for k in range(0, int((END - START).total_seconds() // 15), 37):
        t = START + k * STEP
        feed.seek(t)
        last = await feed.last_prices(t)
        assert all(ts <= t for ts in last.values())
        prices = await feed.price_rows([M1, M2], t - timedelta(hours=1))
        assert prices and all(r["time"] <= t for r in prices)
        context = await feed.context_rows([M1], START - timedelta(hours=1), t)
        assert all(r["time"] <= t for r in context)
        registry = {r["match_id"]: r for r in await feed.registry([M1, M2])}
        assert registry[M2]["settled"] is False  # its result completes after END
        results = await feed.results([M1, M2])
        assert all(
            (mid == M1 and t >= START + timedelta(hours=1, minutes=40)) for mid in results
        ), "a result must not be returned before its completed_at"
        assert registry[M1]["settled"] is (M1 in results)


@pytest.mark.asyncio
async def test_a_decision_is_the_same_whether_or_not_future_rows_exist():
    decision_at = START + timedelta(minutes=30)
    reports = []
    for extra in (False, True):
        feed = _feed(extra_future=extra, end=END + timedelta(hours=1) if extra else END)
        session = MemorySession()
        feed.seek(decision_at)
        report = await pt.run_cycle(session, feed, TimedProbability(), _variants(), now=decision_at)
        reports.append((report, [(r.variant, r.match_id, r.side, r.selection, r.price_limit, r.stake)
                                 for r in session.rows]))
    assert reports[0] == reports[1]
    assert reports[0][0]["intents"] > 0


@pytest.mark.asyncio
async def test_replay_intents_do_not_change_when_later_data_is_added():
    cut = START + timedelta(minutes=50)
    short = _feed(end=cut)
    long = _feed(extra_future=True, end=END + timedelta(hours=1))
    a = await rp.replay_window(short, TimedProbability(), _variants(), start=T0, end=cut)
    b = await rp.replay_window(long, TimedProbability(), _variants(), start=T0, end=cut)
    assert [_intent_key(i) for i in a.intents] == [_intent_key(i) for i in b.intents]
    assert a.intents, "the window must contain trades for the check to mean anything"


# ---------------------------------------------------------------- feed rules


@pytest.mark.asyncio
async def test_results_without_completed_at_settle_after_the_silence_delay():
    prices = [_tick(M1, T0 - timedelta(minutes=1), 0)]  # the match's last tick
    results = [{**_result(M1, None), "completed_at": None}]
    feed = ReplayFeed(prices=prices, registry=[_registry(M1)], context=[], results=results, end=T0 + timedelta(hours=1))
    last_tick = T0 - timedelta(minutes=1)
    feed.seek(last_tick + SETTLE_DELAY - timedelta(seconds=1))
    assert await feed.results([M1]) == {}
    feed.seek(last_tick + SETTLE_DELAY)
    assert (await feed.results([M1]))[M1]["winner"] == "m1 home"


@pytest.mark.asyncio
async def test_toss_is_hidden_until_shortly_before_the_start():
    registry = [{**_registry(M1), "toss_winner": "m1 home", "toss_decision": "bat"}]
    feed = ReplayFeed(prices=[], registry=registry, context=[], results=[], end=END)
    feed.seek(START - timedelta(hours=1))
    assert (await feed.registry([M1]))[0]["toss_winner"] is None
    feed.seek(START - timedelta(minutes=20))
    assert (await feed.registry([M1]))[0]["toss_winner"] == "m1 home"


@pytest.mark.asyncio
async def test_the_cursor_must_be_set_and_must_match_run_cycle():
    feed = _feed()
    with pytest.raises(RuntimeError):
        await feed.last_prices(START)
    feed.seek(START)
    with pytest.raises(ValueError):
        await feed.last_prices(START + STEP)


# ---------------------------------------------------------------- determinism


@pytest.mark.asyncio
async def test_two_runs_over_the_same_data_give_identical_output(tmp_path: Path):
    settings = {"include_srl": False, "variants": [v.name for v in _variants()]}
    outputs = []
    for name in ("a", "b"):
        result = await rp.replay_window(_feed(end=END), TimedProbability(), _variants(), start=T0, end=END)
        summary = rp.build_summary(result, _variants(), _feed(end=END), settings=settings)
        rp.write_outputs(summary, result.session, tmp_path / name)
        outputs.append(summary)
    assert outputs[0] == outputs[1]
    for file in ("summary.json", "summary.md", "bets.csv"):
        assert (tmp_path / "a" / file).read_bytes() == (tmp_path / "b" / file).read_bytes(), file
    parsed = json.loads((tmp_path / "a" / "summary.json").read_text(encoding="utf-8"))
    assert set(parsed["variants"]) == {v.name for v in _variants()}
    assert parsed["window"]["cycles_run"] > 0


# ---------------------------------------------------------------- parity


class NaiveFeed:
    """An independent, obviously-correct as-of feed for the parity check. ``now`` is set by the caller."""

    def __init__(self, prices, registry, context, results):
        self.prices, self.registry_rows, self.context_rows_all, self.results_all = prices, registry, context, results
        self.now = None

    async def last_prices(self, now):
        out = {}
        for r in self.prices:
            if now - pt.FRESH_PRICE <= r["time"] <= now:
                out[r["match_id"]] = max(out.get(r["match_id"], r["time"]), r["time"])
        return out

    async def registry(self, match_ids):
        out = []
        for r in self.registry_rows:
            if r["match_id"] in match_ids:
                done = any(x["result_type"] == "win" and x["completed_at"] <= self.now
                           for x in self.results_all if x["match_id"] == r["match_id"])
                out.append({**r, "settled": done})
        return out

    async def price_rows(self, match_ids, since):
        return [dict(r) for r in self.prices if r["match_id"] in match_ids and since <= r["time"] <= self.now]

    async def context_rows(self, match_ids, since, now):
        return [dict(r) for r in self.context_rows_all
                if r["match_id"] in match_ids and since <= r["time"] <= now]

    async def results(self, match_ids):
        out = {}
        for r in self.results_all:
            if r["match_id"] in match_ids and r["completed_at"] <= self.now:
                out[r["match_id"]] = dict(r)
        return out


@pytest.mark.asyncio
async def test_replay_matches_a_direct_run_cycle_loop_over_a_naive_feed():
    prices, registry, context, results = _history()
    naive = NaiveFeed(prices, registry, context, results)
    session = MemorySession()
    direct = []
    variants = _variants()
    now = T0
    while now <= END:
        naive.now = now
        report = await pt.run_cycle(session, naive, TimedProbability(), variants, now=now)
        direct.extend({**i, "decided_at": now.isoformat()} for i in report["intent_detail"])
        now += STEP

    replay = await rp.replay_window(_feed(end=END), TimedProbability(), variants, start=T0, end=END)
    assert direct, "the window must contain trades for the parity check to mean anything"
    assert [(i["decided_at"], _intent_key(i)) for i in replay.intents] == \
        [(i["decided_at"], _intent_key(i)) for i in direct]
    assert all(r.match_id != M2 for r in replay.session.rows), "SRL is excluded by default"
    key = lambda r: (r.variant, r.match_id, r.decided_at, r.side, r.selection, r.status, r.price_filled, r.stake)  # noqa: E731
    assert sorted(map(key, replay.session.rows)) == sorted(map(key, session.rows))
