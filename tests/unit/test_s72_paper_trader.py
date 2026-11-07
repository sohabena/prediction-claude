"""S72: paper trader cycle (fakes only, no database): place, fill, settle, cap, restart, stale and SRL rules."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone

import pytest
from backend.models.paper_bets import PaperBet
from strategy.base import bet_gross_pnl
from strategy.probability import Estimate
from training.market import band_prob_home
from virtual_trading import paper_trader as pt
from virtual_trading.memory_session import MemorySession
from virtual_trading.paper_review import is_placebo

T0 = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
START = T0 + timedelta(hours=2)
MID = "m1"
MIN = timedelta(minutes=1)


# ---------------------------------------------------------------- fakes


# The in-memory session is production code (S74): the same SELECT / add_all / flush shapes as the database.
FakeSession = MemorySession


class FakeFeed:
    """The read side of one cycle. Rows are plain dicts, as DbFeed returns them."""

    def __init__(self, registry=(), prices=(), last=None, context=(), results=None):
        self.registry_rows = list(registry)
        self.prices = list(prices)
        self.last = dict(last or {})
        self.context_list = list(context)
        self.result_map = dict(results or {})

    async def last_prices(self, now):
        return dict(self.last)

    async def registry(self, match_ids):
        return [dict(r) for r in self.registry_rows if r["match_id"] in set(match_ids)]

    async def price_rows(self, match_ids, since):
        return [dict(r) for r in self.prices if r["match_id"] in set(match_ids) and r["time"] >= since]

    async def context_rows(self, match_ids, since, now):
        return [
            dict(r) for r in self.context_list
            if r["match_id"] in set(match_ids) and since <= r["time"] <= now
        ]

    async def results(self, match_ids):
        return {m: dict(v) for m, v in self.result_map.items() if m in set(match_ids)}


class FakeProbability:
    """Stands in for ProbabilityService: a sequence of p_model values, and p_market = the band midpoint of the book
    (as the service computes it, S59), unless a fixed p_market is given."""

    def __init__(self, p_model=0.6, p_market=None, p_model_seq=None):
        self.p_model = p_model
        self.p_market = p_market
        self.seq = list(p_model_seq) if p_model_seq is not None else None
        self.calls = []

    def estimate(self, match, book, state=None, *, as_of):
        self.calls.append((match["match_id"], as_of))
        p_model = self.seq.pop(0) if self.seq else self.p_model
        p_market = self.p_market if self.p_market is not None else band_prob_home(book)
        return Estimate(
            p_model=p_model,
            p_market=p_market,
            source="inplay" if state else "prematch",
            has_model=p_model is not None,
            features={},
            reason="",
        )


def _registry(mid=MID, simulated=False, start=START):
    return {
        "match_id": mid,
        "team_home": "Alpha",
        "team_away": "Beta",
        "competition": "Indian Premier League",
        "match_start_time": start,
        "is_simulated": simulated,
        "toss_winner": None,
        "toss_decision": None,
        "settled": False,
    }


def _book_for(p, d=0.025):
    """A two-runner book whose band midpoint is exactly ``p``: backs sum above 1 and lays below 1, so no contradiction
    (a book with back_away 1.9 and back_home 2.2 has no band, and the S75 band guard refuses it)."""
    return {
        "back_home": 1.0 / (p + d),
        "lay_home": 1.0 / (p - d),
        "back_away": 1.0 / (1.0 - p + d),
        "lay_away": 1.0 / (1.0 - p - d),
    }


def _price(t, back_home=2.2, lay_home=2.3, back_away=1.8, lay_away=1.9, mid=MID, source="listing"):
    return {
        "match_id": mid,
        "time": t,
        "back_home": back_home,
        "lay_home": lay_home,
        "back_away": back_away,
        "lay_away": lay_away,
        "source": source,
    }


def _variants():
    return pt.build_variants()


def _ledger_book(session, variant, mid=MID):
    books, _pending, _last = pt.ledger_state(session.rows)
    return books.get((variant, mid))


# ---------------------------------------------------------------- tests


@pytest.mark.asyncio
async def test_cycle_places_fills_and_settles(monkeypatch):
    monkeypatch.setattr(pt, "PREMATCH_NEW_RISK", True)  # S106: pre-match entry is off by default
    session = FakeSession()
    feed = FakeFeed(
        registry=[_registry()],
        prices=[_price(T0 - MIN)],
        last={MID: T0},
    )
    prob = FakeProbability(p_model=0.6)
    variants = _variants()

    report = await pt.run_cycle(session, feed, prob, variants, now=T0)
    assert report["candidates"] == 1
    assert report["considered"] == 1
    # S97: the two market-dynamics variants trade only with a model (none here), so they are not counted
    assert report["intents"] >= len(variants) - 1 - len(pt.DYNAMICS_VARIANTS) - 2  # S107: not tail, S120: not rl_specialist
    placed = [r for r in session.rows if r.status == "pending"]
    assert placed, "a cycle with a clear edge must place pending bets"
    assert all(r.decided_at == T0 and r.match_id == MID for r in placed)
    # The first-cycle orders are the ones checked below: BACK home at the takeable price 2.2. The control trades
    # only on the market's own edge, which this book does not offer (the market sits at the band midpoint).
    # S85: the placebo controls are placed too, at the reference's decision time; the model checks below use the
    # strategy rows only.
    first = {r.variant: r for r in placed if not is_placebo(r.variant)}
    assert set(first) == {v.name for v in variants if not v.market_only and v.name not in pt.DYNAMICS_VARIANTS
                          and v.name not in (pt.TAIL_VARIANT, pt.RL_SPECIALIST_V1)}  # S107/S120: tail & RL trade in play
    assert all(r.side == "BACK" and r.selection == "home" and r.price_limit <= 2.2 for r in first.values())
    assert {r.variant for r in placed if is_placebo(r.variant)} == {"placebo_random", "placebo_favourite"}

    # One minute later the book still offers 2.2, so the decided orders fill at decided + 60 s.
    feed.prices.append(_price(T0 + timedelta(seconds=30)))
    feed.last = {MID: T0 + timedelta(seconds=60)}
    report = await pt.run_cycle(session, feed, prob, variants, now=T0 + timedelta(seconds=61))
    assert report["fills"] == {"filled": len(placed), "unfilled": 0}
    for row in first.values():
        assert row.status == "filled"
        assert row.price_filled == 2.2
        assert row.filled_at == T0 + timedelta(seconds=60)
    for row in placed:
        assert row.status == "filled" and row.filled_at == T0 + timedelta(seconds=60)

    # The match finishes with home winning: the filled orders settle at their gross P&L.
    feed.result_map = {MID: {"winner": "Alpha", "result_type": "win", "team_home": "Alpha", "team_away": "Beta"}}
    feed.registry_rows = [_registry() | {"settled": True}]
    feed.last = {}
    report = await pt.run_cycle(session, feed, prob, variants, now=T0 + timedelta(hours=4))
    assert report["settled"].get("settled", 0) >= len(first)
    for row in first.values():
        assert row.status == "settled"
        assert row.result == "home"
        assert row.pnl == pytest.approx(bet_gross_pnl("BACK", "home", 2.2, row.stake, "home"))


@pytest.mark.asyncio
async def test_cap_holds_across_cycles(monkeypatch):
    monkeypatch.setattr(pt, "PREMATCH_NEW_RISK", True)  # S106: pre-match entry is off by default
    variants = _variants()
    session = FakeSession()
    feed = FakeFeed(registry=[_registry()], last={})
    # Prices move from cycle to cycle and the model swings between a strong and a weak view, so the strategy
    # adds, reduces, flips and greens up. The worst-case loss per variant must stay within 100,000 throughout.
    p_markets = [0.45, 0.5, 0.42, 0.55, 0.4, 0.6, 0.47, 0.38, 0.58, 0.44, 0.52, 0.41]
    p_model_seq = [0.9, 0.2, 0.85, 0.3, 0.9, 0.15, 0.8, 0.25, 0.9, 0.3, 0.7, 0.2]
    prob = FakeProbability(p_model_seq=p_model_seq)
    for k, p_mkt in enumerate(p_markets):
        now = T0 + timedelta(seconds=61 * k)
        feed.prices.append(_price(now - timedelta(seconds=30), **_book_for(p_mkt)))
        feed.last = {MID: now}
        await pt.run_cycle(session, feed, prob, variants, now=now)
        for variant in variants:
            book = _ledger_book(session, variant.name)
            if book is not None:
                assert book.worst() <= variant.max_match_loss + 1e-6, (variant.name, k, book.worst())
    filled_total = sum(1 for r in session.rows if r.status == "filled")
    assert filled_total > 0


@pytest.mark.asyncio
async def test_restart_reads_positions_from_the_ledger():
    variants = _variants()
    first = FakeSession()
    feed = FakeFeed(registry=[_registry()], prices=[_price(T0 - MIN)], last={MID: T0})
    prob = FakeProbability(p_model=0.9)
    await pt.run_cycle(first, feed, prob, variants, now=T0)
    feed.prices.append(_price(T0 + timedelta(seconds=30)))
    feed.last = {MID: T0 + timedelta(seconds=60)}
    await pt.run_cycle(first, feed, prob, variants, now=T0 + timedelta(seconds=61))

    # A restarted process has no memory: it gets only the table. Its positions must equal the original's.
    restarted = FakeSession(rows=[copy.copy(r) for r in first.rows])
    books_before, pending_before, last_before = pt.ledger_state(first.rows)
    books_after, pending_after, last_after = pt.ledger_state(restarted.rows)
    assert books_after.keys() == books_before.keys()
    for key, book in books_before.items():
        assert books_after[key].home_pnl == pytest.approx(book.home_pnl)
        assert books_after[key].away_pnl == pytest.approx(book.away_pnl)
        assert books_after[key].trades == book.trades
    assert pending_after == pending_before
    assert last_after == last_before


@pytest.mark.asyncio
async def test_restart_respects_cap_from_existing_positions():
    variants = _variants()
    target = next(v for v in variants if v.name == "w05_e03")
    # A filled BACK home bet of 100,000 at 2.2 leaves away P&L at -100,000: the cap is used up, so the
    # variant may not add risk after a restart, even though the model still likes home.
    session = FakeSession()
    feed = FakeFeed(registry=[_registry()], prices=[_price(T0 - MIN)], last={MID: T0})
    session.rows.append(
        PaperBet(
            bet_id=None,
            variant=target.name,
            match_id=MID,
            decided_at=T0 - timedelta(minutes=5),
            filled_at=T0 - timedelta(minutes=4),
            status="filled",
            side="BACK",
            selection="home",
            team="Alpha",
            price_limit=2.2,
            price_filled=2.2,
            stake=100_000.0,
            liability=100_000.0,
            reason="enter",
            p_model=0.6,
            p_market=0.5,
            p_used=0.55,
            edge=0.21,
            source="prematch",
            in_play=False,
            score_state=None,
            book={},
        )
    )
    prob = FakeProbability(p_model=0.9)
    await pt.run_cycle(session, feed, prob, [target], now=T0)
    new_for_variant = [r for r in session.rows if r.variant == target.name and r.status == "pending"]
    assert new_for_variant == []
    book = _ledger_book(session, target.name)
    assert book.worst() <= target.max_match_loss + 1e-6


@pytest.mark.asyncio
async def test_pending_bet_blocks_a_new_decision(monkeypatch):
    monkeypatch.setattr(pt, "PREMATCH_NEW_RISK", True)  # S106: pre-match entry is off by default
    variants = _variants()
    session = FakeSession()
    feed = FakeFeed(registry=[_registry()], prices=[_price(T0 - MIN)], last={MID: T0})
    prob = FakeProbability(p_model=0.6)
    await pt.run_cycle(session, feed, prob, variants, now=T0)
    count_after_first = len(session.rows)
    assert count_after_first > 0

    # 15 s later the orders are still pending (the fill is due at 60 s): no variant may decide again.
    feed.last = {MID: T0 + timedelta(seconds=15)}
    report = await pt.run_cycle(session, feed, prob, variants, now=T0 + timedelta(seconds=15))
    assert len(session.rows) == count_after_first
    assert report["skipped_pending"] == len({r.variant for r in session.rows if not is_placebo(r.variant)})
    assert report["intents"] == 0


@pytest.mark.asyncio
async def test_stale_price_gives_no_candidate_and_no_trade():
    registry = [_registry()]
    last_old = {MID: T0 - timedelta(minutes=10)}
    candidates, skipped = pt.select_candidates(registry, last_old, T0)
    assert candidates == []
    assert skipped == {"stale_price": 1}

    session = FakeSession()
    feed = FakeFeed(registry=registry, prices=[_price(T0 - timedelta(minutes=10))], last={})
    report = await pt.run_cycle(session, feed, FakeProbability(), _variants(), now=T0)
    assert report["candidates"] == 0
    assert report["intents"] == 0
    assert session.rows == []


def test_book_is_none_when_every_field_is_older_than_the_staleness_limit():
    cache = pt.PriceCache([_price(T0 - timedelta(minutes=16))])
    assert cache.book_at(MID, T0) is None
    fresh = pt.PriceCache([_price(T0 - timedelta(minutes=14))])
    assert fresh.book_at(MID, T0)["back_home"] == 2.2


def test_as_of_book_merges_fields_per_s58_rule():
    rows = [
        _price(T0 - timedelta(minutes=10), back_home=2.0, lay_home=2.1, back_away=None, lay_away=None,
               source="match_page"),
        _price(T0 - timedelta(minutes=1), back_home=None, lay_home=None, back_away=1.9, lay_away=2.0,
               source="listing"),
    ]
    book = pt.PriceCache(rows).book_at(MID, T0)
    # Each field comes from its own latest row within 15 min: home prices from the older row, away from the newer.
    assert book == {"back_home": 2.0, "lay_home": 2.1, "back_away": 1.9, "lay_away": 2.0}
    # Nothing after t is used.
    assert pt.PriceCache(rows).book_at(MID, T0 - timedelta(minutes=5))["back_away"] is None


def test_select_candidates_skips_srl_unless_asked():
    now = T0
    last = {MID: now}
    srl = _registry(simulated=True)
    candidates, skipped = pt.select_candidates([srl], last, now)
    assert candidates == []
    assert skipped == {"simulated": 1}
    candidates, _ = pt.select_candidates([srl], last, now, include_simulated=True)
    assert [c.match_id for c in candidates] == [MID]


@pytest.mark.asyncio
async def test_cycle_never_trades_srl_by_default():
    session = FakeSession()
    feed = FakeFeed(registry=[_registry(simulated=True)], prices=[_price(T0 - MIN)], last={MID: T0})
    report = await pt.run_cycle(session, feed, FakeProbability(), _variants(), now=T0)
    assert report["skipped_registry"] == {"simulated": 1}
    assert session.rows == []


def test_select_candidates_window_rules():
    now = T0
    far = _registry(start=T0 + timedelta(hours=7))
    unknown = _registry(start=None)
    in_play = _registry(start=T0 - timedelta(hours=1))
    far["match_id"] = "far"
    unknown["match_id"] = "unknown"
    in_play["match_id"] = "live"
    candidates, skipped = pt.select_candidates([far, unknown, in_play], {"far": now, "unknown": now, "live": now}, now)
    assert [c.match_id for c in candidates] == ["live"]
    assert candidates[0].in_play is True
    assert skipped == {"too_early": 1, "no_start": 1}


def test_build_variants_grid_and_control():
    variants = pt.build_variants()
    names = [v.name for v in variants]
    assert names == [
        "w03_e03", "w03_e06", "w05_e03", "w05_e06", "w07_e03", "w07_e06", "market_only",
        "mkt_dyn", "mkt_dyn_e06",  # S97: the market-dynamics variants (w = 1)
        "w05_e06_green",  # S112: auto green-up variant adopting w05_e06 with profit lock-in
        "bmodel_green_v1", "bmodel_scalp_v1", "bmodel_scalp_fast", "bmodel_swing_v1", "bmodel_deep_v1", "bmodel_fav_lock_v1",  # S113/S114: specialist bmodels
        "rl_specialist_v1",  # S120: format-specialist reinforcement learning
        "tail",  # S107: the chase-model variant (its own decision path)
        "placebo_random", "placebo_favourite",  # S85
    ]
    assert all(v.max_match_loss == 100_000.0 for v in variants)
    control = next(v for v in variants if v.name == "market_only")
    assert control.market_only is True and control.min_edge == 0.03
    assert [v.placebo for v in variants if v.placebo] == ["random", "favourite"]
    dyn = next(v for v in variants if v.name == "mkt_dyn")
    assert dyn.guard_double_negative is True
    assert dyn.auto_green_up is True
    assert dyn.hard_ticket_limit is True
    assert dyn.ticket_budget == 15
    for bname in ["bmodel_green_v1", "bmodel_scalp_v1", "bmodel_scalp_fast", "bmodel_swing_v1", "bmodel_deep_v1", "bmodel_fav_lock_v1"]:
        b = next(v for v in variants if v.name == bname)
        assert b.guard_double_negative is True
        assert b.auto_green_up is True
        assert b.hard_ticket_limit is True


def test_report_and_log_line_are_json(tmp_path):
    report = {"cycle_at": T0.isoformat(), "at": T0, "intent_detail": [], "nested": {"x": float("nan")}}
    line = pt.append_log(report, tmp_path / "paper_trader.log")
    parsed = json.loads(line)
    assert parsed["cycle_at"] == T0.isoformat()
    assert parsed["nested"]["x"] is None
    assert (tmp_path / "paper_trader.log").read_text(encoding="utf-8").count("\n") == 1


def test_run_cycle_rejects_naive_now():
    import asyncio

    with pytest.raises(ValueError):
        asyncio.run(pt.run_cycle(FakeSession(), FakeFeed(), FakeProbability(), _variants(),
                                 now=datetime(2026, 11, 3, 12, 0)))
