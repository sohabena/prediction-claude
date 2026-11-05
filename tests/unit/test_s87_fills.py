"""S87: liquidity-aware fills (fakes only, no database).

Covers the size units (BACK and LAY), full, partial and unknown-size fills, the zero-size and no-price cases, each fill
delay (30, 60, 120 s), the as-of rule for sizes (no look-ahead, staleness), the in-memory feed, the trader cycle with and
without the size cap, and the fill metrics the review and the reports read.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.models.paper_bets import PaperBet
from tests.unit import test_s71_paper_ledger as s71
from tests.unit import test_s72_paper_trader as s72
from tests.unit import test_s74_replay as s74
from virtual_trading import paper_ledger as pl
from virtual_trading import paper_review as review
from virtual_trading import paper_trader as pt
from virtual_trading import replay_feed as rf
from virtual_trading import memory_session as ms

T0 = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
MIN = timedelta(minutes=1)
SEC = timedelta(seconds=1)
MID = "m1"


def _run(coro):
    return asyncio.run(coro)


def _size_row(t, *, selection="home", back=2.2, back_size=100.0, lay=2.3, lay_size=90.0, market="match_odds", mid=MID):
    return {
        "match_id": mid, "time": t, "selection": selection, "market_name": market,
        "back": back, "back_size": back_size, "lay": lay, "lay_size": lay_size,
    }


def _sized(size_map):
    """A size_at callback over a fixed size per (side, selection), ignoring time (the as-of test is separate)."""
    def size_at(match_id, side, selection, t):
        return size_map.get((side, selection))
    return size_at


def _pending(side="BACK", selection="home", limit=2.0, stake=100.0, decided=T0, asked=None):
    row = s71._row(side=side, selection=selection, price_limit=limit, stake=stake, decided_at=decided,
                   liability=stake, status="pending")
    row.stake_asked = stake if asked is None else asked
    return row


BOOK_BACK_HOME = s71._book(back_home=2.2, lay_home=2.3, back_away=1.8, lay_away=1.9)


def _book_at(_mid, _t):
    return BOOK_BACK_HOME


# ---------------------------------------------------------------- units


def test_back_size_is_the_stake():
    assert pl.max_stake("BACK", 2.2, 3000.0) == 3000.0


def test_lay_size_is_the_stake_by_default():
    assert pl.LAY_SIZE_UNIT == "stake"
    assert pl.max_stake("LAY", 3.0, 200.0) == 200.0


def test_lay_size_as_liability_when_the_reading_is_changed(monkeypatch):
    monkeypatch.setattr(pl, "LAY_SIZE_UNIT", "liability")
    assert pl.max_stake("LAY", 3.0, 200.0) == pytest.approx(100.0)  # liability 200 at price 3 is a stake of 100
    assert pl.max_stake("BACK", 3.0, 200.0) == 200.0  # BACK is always the stake


def test_no_size_gives_no_maximum_stake():
    assert pl.max_stake("BACK", 2.2, None) is None
    assert pl.max_stake("LAY", 3.0, None) is None


# ---------------------------------------------------------------- fill_pending: full, partial, unknown, zero


def test_full_fill_takes_the_asked_stake_when_the_size_covers_it():
    row = _pending(stake=100.0)
    session = s71.FakeSession([row])
    counts = _run(pl.fill_pending(session, _book_at, now=T0 + MIN, size_at=_sized({("BACK", "home"): 1000.0})))
    assert counts == {"filled": 1, "unfilled": 0, "size_capped": 0, "size_unknown": 0, "size_zero": 0}
    assert row.status == "filled" and row.stake == 100.0 and row.stake_asked == 100.0
    assert row.fill_note is None and row.liability == pytest.approx(100.0)


def test_partial_fill_cuts_to_the_size_shown_and_keeps_the_asked_stake():
    row = _pending(stake=25_000.0)
    session = s71.FakeSession([row])
    counts = _run(pl.fill_pending(session, _book_at, now=T0 + MIN, size_at=_sized({("BACK", "home"): 3000.0})))
    assert counts["filled"] == 1 and counts["size_capped"] == 1
    assert row.status == "filled"
    assert row.stake == 3000.0 and row.stake_asked == 25_000.0  # the remainder lapses
    assert row.fill_note == "size_capped"
    assert row.liability == pytest.approx(3000.0)  # BACK liability is the filled stake
    assert row.price_filled == 2.2


def test_lay_partial_fill_cuts_the_lay_stake_and_the_liability_follows():
    row = _pending(side="LAY", selection="home", limit=3.5, stake=1000.0)
    session = s71.FakeSession([row])
    book = s71._book(back_home=2.9, lay_home=3.0, back_away=1.8, lay_away=1.9)
    counts = _run(pl.fill_pending(session, lambda m, t: book, now=T0 + MIN,
                                  size_at=_sized({("LAY", "home"): 200.0})))
    assert counts["size_capped"] == 1
    assert row.stake == 200.0 and row.stake_asked == 1000.0
    assert row.liability == pytest.approx(200.0 * (3.0 - 1.0))  # stake * (price - 1)


def test_lay_size_read_as_liability_gives_a_smaller_lay_stake(monkeypatch):
    monkeypatch.setattr(pl, "LAY_SIZE_UNIT", "liability")
    row = _pending(side="LAY", selection="home", limit=3.5, stake=1000.0)
    book = s71._book(back_home=2.9, lay_home=3.0, back_away=1.8, lay_away=1.9)
    _run(pl.fill_pending(s71.FakeSession([row]), lambda m, t: book, now=T0 + MIN,
                         size_at=_sized({("LAY", "home"): 200.0})))
    assert row.stake == pytest.approx(100.0)
    assert row.liability == pytest.approx(200.0)


def test_unknown_size_fills_at_most_the_default_and_is_marked():
    row = _pending(stake=25_000.0)
    counts = _run(pl.fill_pending(s71.FakeSession([row]), _book_at, now=T0 + MIN, size_at=lambda *a: None))
    assert counts["filled"] == 1 and counts["size_unknown"] == 1 and counts["size_capped"] == 0
    assert row.stake == pl.UNKNOWN_SIZE_CAP == 10_000.0
    assert row.stake_asked == 25_000.0
    assert row.fill_note == "size_unknown"
    assert row.liability == pytest.approx(10_000.0)


def test_unknown_size_below_the_default_is_filled_whole_but_still_marked():
    row = _pending(stake=500.0)
    _run(pl.fill_pending(s71.FakeSession([row]), _book_at, now=T0 + MIN, size_at=lambda *a: None))
    assert row.stake == 500.0 and row.stake_asked == 500.0
    assert row.fill_note == "size_unknown"


def test_unknown_size_cap_is_configurable():
    row = _pending(stake=25_000.0)
    _run(pl.fill_pending(s71.FakeSession([row]), _book_at, now=T0 + MIN, size_at=lambda *a: None,
                         unknown_size_cap=2_500.0))
    assert row.stake == 2_500.0


def test_zero_size_is_unfilled_with_a_note():
    row = _pending(stake=100.0)
    counts = _run(pl.fill_pending(s71.FakeSession([row]), _book_at, now=T0 + MIN,
                                  size_at=_sized({("BACK", "home"): 0.0})))
    assert counts == {"filled": 0, "unfilled": 1, "size_capped": 0, "size_unknown": 0, "size_zero": 1}
    assert row.status == "unfilled" and row.fill_note == "size_zero" and row.filled_at is None


def test_no_price_is_unfilled_and_the_size_is_not_read():
    row = _pending(stake=100.0)
    asked = []

    def size_at(*args):
        asked.append(args)
        return 1000.0

    counts = _run(pl.fill_pending(s71.FakeSession([row]), lambda m, t: s71._book(), now=T0 + MIN, size_at=size_at))
    assert counts["unfilled"] == 1 and row.status == "unfilled"
    assert asked == []


def test_price_outside_the_limit_is_unfilled_whatever_the_size():
    row = _pending(limit=2.5, stake=100.0)  # BACK needs 2.5 or better; the book offers 2.2
    counts = _run(pl.fill_pending(s71.FakeSession([row]), _book_at, now=T0 + MIN, size_at=lambda *a: 1e9))
    assert counts["unfilled"] == 1 and row.status == "unfilled" and row.fill_note is None


def test_without_size_at_the_fill_is_the_pre_s87_one():
    row = _pending(stake=25_000.0)
    counts = _run(pl.fill_pending(s71.FakeSession([row]), _book_at, now=T0 + MIN))
    assert counts == {"filled": 1, "unfilled": 0}  # no size keys when no size callback is given
    assert row.stake == 25_000.0 and row.fill_note is None and row.liability == pytest.approx(25_000.0)
    assert row.stake_asked == 25_000.0


def test_a_row_without_stake_asked_gets_its_stake_as_the_asked_stake():
    row = _pending(stake=3000.0)
    row.stake_asked = None  # a row written before S87
    _run(pl.fill_pending(s71.FakeSession([row]), _book_at, now=T0 + MIN, size_at=_sized({("BACK", "home"): 500.0})))
    assert row.stake == 500.0 and row.stake_asked == 3000.0


def test_record_intents_keeps_the_asked_stake():
    session = s71.FakeSession()
    intents = [SimpleNamespace(side="BACK", selection="home", price_limit=2.0, stake=250.0, reason="edge")]
    rows = _run(pl.record_intents(session, "w05_e03", MID, intents, s71._ctx()))
    assert rows[0].stake == 250.0 and rows[0].stake_asked == 250.0 and rows[0].fill_note is None


# ---------------------------------------------------------------- each delay


@pytest.mark.parametrize("delay_s", [30, 60, 120])
def test_each_delay_fills_at_decided_plus_delay_and_not_before(delay_s):
    delay = timedelta(seconds=delay_s)
    row = _pending(stake=100.0, decided=T0)
    session = s71.FakeSession([row])
    seen = []

    def book_at(mid, t):
        seen.append(("book", t))
        return BOOK_BACK_HOME

    def size_at(mid, side, sel, t):
        seen.append(("size", t))
        return 1000.0

    # one second before the delay: too early, nothing is read and nothing fills
    before = _run(pl.fill_pending(session, book_at, now=T0 + delay - SEC, delay=delay, size_at=size_at))
    assert before["filled"] == 0 and row.status == "pending" and seen == []
    # at the delay: filled at decided + delay, with the book and the size as of that time
    counts = _run(pl.fill_pending(session, book_at, now=T0 + delay, delay=delay, size_at=size_at))
    assert counts["filled"] == 1
    assert row.filled_at == T0 + delay
    assert seen == [("book", T0 + delay), ("size", T0 + delay)]


def test_a_late_cycle_still_reads_the_book_and_size_at_the_fill_time():
    row = _pending(stake=100.0, decided=T0)
    seen = []

    def book_at(mid, t):
        seen.append(t)
        return BOOK_BACK_HOME

    def size_at(mid, side, sel, t):
        seen.append(t)
        return 1000.0

    _run(pl.fill_pending(s71.FakeSession([row]), book_at, now=T0 + timedelta(minutes=5), delay=timedelta(seconds=120),
                         size_at=size_at))
    assert row.filled_at == T0 + timedelta(seconds=120)
    assert seen == [T0 + timedelta(seconds=120)] * 2


# ---------------------------------------------------------------- sizes as of a time (no look-ahead, staleness)


def test_size_cache_never_uses_a_row_after_the_time_asked():
    cache = pt.SizeCache([
        _size_row(T0 + timedelta(seconds=30), back_size=800.0),
        _size_row(T0 + timedelta(seconds=60), back_size=100.0),
        _size_row(T0 + timedelta(seconds=61), back_size=5000.0),  # after the fill time of a 60 s bet
    ])
    assert cache.size_at(MID, "BACK", "home", T0 + timedelta(seconds=59)) == 800.0
    assert cache.size_at(MID, "BACK", "home", T0 + timedelta(seconds=60)) == 100.0
    assert cache.size_at(MID, "BACK", "home", T0 + timedelta(seconds=61)) == 5000.0


def test_size_cache_before_the_first_row_is_unknown():
    cache = pt.SizeCache([_size_row(T0 + timedelta(seconds=30))])
    assert cache.size_at(MID, "BACK", "home", T0 + timedelta(seconds=29)) is None


def test_size_cache_row_older_than_the_staleness_limit_is_unknown():
    cache = pt.SizeCache([_size_row(T0)])
    assert cache.size_at(MID, "BACK", "home", T0 + pt.SIZE_STALE) == 100.0  # at the limit: still known
    assert cache.size_at(MID, "BACK", "home", T0 + pt.SIZE_STALE + SEC) is None


def test_size_cache_takes_the_size_of_the_price_the_book_shows_across_markets():
    cache = pt.SizeCache([
        _size_row(T0, market="match_odds", back=2.2, back_size=100.0, lay=2.4, lay_size=300.0),
        _size_row(T0, market="match_odds#1.2", back=2.3, back_size=50.0, lay=2.1, lay_size=900.0),
    ])
    # BACK: the highest back price (2.3, size 50) is the one v_match_odds shows
    assert cache.size_at(MID, "BACK", "home", T0) == 50.0
    # LAY: the highest lay price (2.4, size 300), as v_match_odds takes max(lay)
    assert cache.size_at(MID, "LAY", "home", T0) == 300.0


def test_size_cache_is_per_selection_and_unknown_without_a_price_or_a_size():
    cache = pt.SizeCache([
        _size_row(T0, selection="home", back_size=100.0),
        _size_row(T0, selection="away", back=None, back_size=None),  # no price, no size
        _size_row(T0, selection="draw", back_size=7.0),
    ])
    assert cache.size_at(MID, "BACK", "home", T0) == 100.0
    assert cache.size_at(MID, "BACK", "away", T0) is None
    assert cache.size_at("other", "BACK", "home", T0) is None


def test_size_cache_with_a_dom_row_that_has_no_size_is_unknown():
    cache = pt.SizeCache([_size_row(T0, back_size=None, lay_size=None)])
    assert cache.size_at(MID, "BACK", "home", T0) is None
    assert cache.size_at(MID, "LAY", "home", T0) is None


def test_size_sql_reads_only_the_match_page_match_odds_rows_and_is_read_only():
    sql = pt.SIZE_ROWS_SQL
    assert "market_type = 'match_odds'" in sql and "source = 'lotusbook'" in sql
    assert ":until" in sql and ":since" in sql
    lowered = sql.lower()
    for word in ("insert ", "update ", "delete ", "alter ", "drop "):
        assert word not in lowered


def test_replay_feed_returns_sizes_only_up_to_the_cursor():
    rows = [_size_row(T0 + timedelta(seconds=s), back_size=float(s)) for s in (30, 60, 120, 400)]
    feed = rf.ReplayFeed(prices=[], registry=[], context=[], results=[], end=T0 + timedelta(seconds=300), sizes=rows)
    feed.seek(T0 + timedelta(seconds=60))
    got = _run(feed.size_rows([MID], T0 - timedelta(hours=1), T0 + timedelta(seconds=60)))
    assert [r["back_size"] for r in got] == [30.0, 60.0]
    assert all(r["time"] <= T0 + timedelta(seconds=60) for r in got)
    # the row at 400 s is after the end of the window, so the feed never held it
    feed.seek(T0 + timedelta(seconds=300))
    later = _run(feed.size_rows([MID], T0 - timedelta(hours=1), T0 + timedelta(seconds=300)))
    assert [r["back_size"] for r in later] == [30.0, 60.0, 120.0]


def test_replay_feed_size_rows_refuse_a_different_clock():
    feed = rf.ReplayFeed(prices=[], registry=[], context=[], results=[], end=T0, sizes=[_size_row(T0)])
    feed.seek(T0)
    with pytest.raises(ValueError):
        _run(feed.size_rows([MID], T0, T0 + SEC))


# ---------------------------------------------------------------- trader cycle


class SizedFeed(s72.FakeFeed):
    """The S72 fake feed with the match-page size rows (as DbFeed.size_rows returns them)."""

    def __init__(self, sizes=(), **kwargs):
        super().__init__(**kwargs)
        self.sizes = list(sizes)

    async def size_rows(self, match_ids, since, now):
        return [dict(r) for r in self.sizes if r["match_id"] in set(match_ids) and since <= r["time"] <= now]


def _two_cycle_scenario(feed, *, fill_delay=timedelta(seconds=60), size_cap=True, second_at=T0 + timedelta(seconds=61)):
    """Decide at T0 (BACK home at 2.2 for every variant with an edge), then run a cycle at ``second_at``."""
    session = ms.MemorySession()
    feed.registry_rows = [s72._registry()]
    feed.prices = [s72._price(T0 - MIN)]
    feed.last = {MID: T0}
    prob = s72.FakeProbability(p_model=0.6)
    variants = s72._variants()
    _run(pt.run_cycle(session, feed, prob, variants, now=T0, fill_delay=fill_delay, size_cap=size_cap))
    feed.prices.append(s72._price(T0 + timedelta(seconds=30)))
    feed.last = {MID: second_at}
    report = _run(pt.run_cycle(session, feed, prob, variants, now=second_at, fill_delay=fill_delay, size_cap=size_cap))
    return session, report


def test_cycle_cuts_each_fill_to_the_size_shown_at_the_fill_time(monkeypatch):
    monkeypatch.setattr(pt, "PREMATCH_NEW_RISK", True)  # S106: pre-match entry is off by default
    feed = SizedFeed(sizes=[_size_row(T0 + timedelta(seconds=30), selection="home", back=2.2, back_size=150.0)])
    session, report = _two_cycle_scenario(feed)
    filled = [r for r in session.rows if r.status == "filled" and not review.is_placebo(r.variant)]  # S85: model rows
    assert filled, "the scenario must fill something"
    for row in filled:
        assert row.stake == pytest.approx(150.0)
        assert row.stake_asked > 150.0
        assert row.fill_note == "size_capped"
    assert report["fills"]["size_capped"] == len(filled)


def test_cycle_does_not_use_a_size_shown_after_the_fill_time(monkeypatch):
    monkeypatch.setattr(pt, "PREMATCH_NEW_RISK", True)  # S106: pre-match entry is off by default
    # the size at 61 s is huge, but a bet decided at 0 s fills at 60 s: the 30 s size of 150 is the one that counts
    feed = SizedFeed(sizes=[
        _size_row(T0 + timedelta(seconds=30), selection="home", back=2.2, back_size=150.0),
        _size_row(T0 + timedelta(seconds=61), selection="home", back=2.2, back_size=1_000_000.0),
    ])
    session, _report = _two_cycle_scenario(feed)
    filled = [r for r in session.rows if r.status == "filled" and not review.is_placebo(r.variant)]  # S85: model rows
    assert filled and all(r.stake == pytest.approx(150.0) for r in filled)


def test_cycle_with_size_cap_off_fills_the_asked_stake(monkeypatch):
    monkeypatch.setattr(pt, "PREMATCH_NEW_RISK", True)  # S106: pre-match entry is off by default
    feed = SizedFeed(sizes=[_size_row(T0 + timedelta(seconds=30), selection="home", back=2.2, back_size=150.0)])
    session, report = _two_cycle_scenario(feed, size_cap=False)
    filled = [r for r in session.rows if r.status == "filled"]
    assert filled and all(r.fill_note is None and r.stake == r.stake_asked for r in filled)
    assert "size_capped" not in report["fills"]


def test_feed_without_size_rows_keeps_the_pre_s87_fill(monkeypatch):
    monkeypatch.setattr(pt, "PREMATCH_NEW_RISK", True)  # S106: pre-match entry is off by default
    feed = s72.FakeFeed()
    session, report = _two_cycle_scenario(feed)
    filled = [r for r in session.rows if r.status == "filled"]
    assert filled and all(r.fill_note is None and r.stake == r.stake_asked for r in filled)
    assert report["fills"] == {"filled": len(filled), "unfilled": 0}


def test_cycle_with_no_size_known_caps_at_the_default_and_marks_it(monkeypatch):
    monkeypatch.setattr(pt, "PREMATCH_NEW_RISK", True)  # S106: pre-match entry is off by default
    feed = SizedFeed(sizes=[])  # the match page has no size for the runner
    session, _report = _two_cycle_scenario(feed)
    filled = [r for r in session.rows if r.status == "filled"]
    assert filled
    for row in filled:
        assert row.stake <= pl.UNKNOWN_SIZE_CAP
        assert row.fill_note == "size_unknown"


def test_cycle_honours_a_120_second_delay(monkeypatch):
    monkeypatch.setattr(pt, "PREMATCH_NEW_RISK", True)  # S106: pre-match entry is off by default
    feed = SizedFeed(sizes=[_size_row(T0 + timedelta(seconds=30), selection="home", back=2.2, back_size=150.0)])
    session, report = _two_cycle_scenario(feed, fill_delay=timedelta(seconds=120))
    assert report["fills"]["filled"] == 0 and session.rows and all(r.status == "pending" for r in session.rows)
    feed.prices.append(s72._price(T0 + timedelta(seconds=90)))
    feed.last = {MID: T0 + timedelta(seconds=121)}
    report = _run(pt.run_cycle(session, feed, s72.FakeProbability(p_model=0.6), s72._variants(),
                               now=T0 + timedelta(seconds=121), fill_delay=timedelta(seconds=120)))
    filled = [r for r in session.rows if r.status == "filled"]
    assert filled and all(r.filled_at == T0 + timedelta(seconds=120) for r in filled)


# ---------------------------------------------------------------- replay: sizes from the recorded window


def test_replay_caps_every_fill_to_the_recorded_size_and_size_cap_off_does_not():
    prices, registry, context, results = s74._history()
    sizes = []
    for mid in (s74.M1, s74.M2):
        t = s74.T0 - timedelta(minutes=20)
        while t <= s74.END:
            for sel in ("home", "away"):
                sizes.append(_size_row(t, selection=sel, back=2.0, back_size=150.0, lay=2.1, lay_size=150.0, mid=mid))
            t += timedelta(seconds=30)

    def feed():
        return rf.ReplayFeed(prices=prices, registry=registry, context=context, results=results, end=s74.END,
                             sizes=sizes)

    capped = _run(s74.rp.replay_window(feed(), s74.TimedProbability(), s74._variants(), start=s74.T0, end=s74.END,
                                       size_cap=True))
    fills = [r for r in capped.session.rows if r.status in ("filled", "settled", "void")]
    assert fills and all(r.stake <= 150.0 + 1e-9 for r in fills)
    assert any(r.fill_note == "size_capped" for r in fills)

    uncapped = _run(s74.rp.replay_window(feed(), s74.TimedProbability(), s74._variants(), start=s74.T0, end=s74.END,
                                         size_cap=False))
    uncapped_fills = [r for r in uncapped.session.rows if r.status in ("filled", "settled", "void")]
    assert any(r.stake > 150.0 for r in uncapped_fills)
    assert all(r.fill_note is None for r in uncapped_fills)


# ---------------------------------------------------------------- review metrics


def _fill_row(**kw):
    base = dict(status="filled", side="BACK", selection="home", price_filled=2.2, stake=100.0, stake_asked=100.0,
                fill_note=None, book={"back_home": 2.2, "lay_home": 2.3})
    base.update(kw)
    return base


def test_fill_metrics_stake_fill_rate_and_capped_share():
    rows = [
        _fill_row(stake=200.0, stake_asked=1000.0, fill_note="size_capped"),  # capped
        _fill_row(stake=100.0, stake_asked=100.0),  # whole
        dict(status="unfilled", side="BACK", selection="home", stake=300.0, stake_asked=300.0, fill_note=None,
             book={"back_home": 2.2}, price_filled=None),
        dict(status="unfilled", side="BACK", selection="home", stake=50.0, stake_asked=50.0, fill_note="size_zero",
             book={"back_home": 2.2}, price_filled=None),
    ]
    m = review.fill_metrics(rows)
    assert m["decided_bets"] == 4
    assert m["asked_stake"] == pytest.approx(1000.0 + 100.0 + 300.0 + 50.0)
    assert m["filled_stake"] == pytest.approx(300.0)
    assert m["stake_fill_rate"] == pytest.approx(300.0 / 1450.0)
    assert m["size_capped_fills"] == 1 and m["size_capped_share"] == pytest.approx(0.5)
    assert m["size_capped_stake_lapsed"] == pytest.approx(800.0)
    assert m["size_zero_unfilled"] == 1


def test_fill_metrics_unknown_share_and_slippage_sign():
    rows = [
        _fill_row(price_filled=2.0, fill_note="size_unknown"),  # BACK: decided 2.2, filled 2.0 -> 0.2 worse
        _fill_row(side="LAY", selection="home", price_filled=3.2, stake=50.0, stake_asked=50.0,
                  book={"lay_home": 3.0}),  # LAY: decided 3.0, filled 3.2 -> 0.2 worse
    ]
    m = review.fill_metrics(rows)
    assert m["size_unknown_fills"] == 1 and m["size_unknown_share"] == pytest.approx(0.5)
    assert m["slippage_n"] == 2
    assert m["mean_adverse_slippage"] == pytest.approx(0.2)
    assert m["mean_adverse_slippage_pct"] == pytest.approx((0.2 / 2.2 + 0.2 / 3.0) / 2)


def test_fill_metrics_are_nan_without_fills():
    m = review.fill_metrics([])
    assert m["decided_bets"] == 0
    assert m["stake_fill_rate"] != m["stake_fill_rate"]  # NaN
    assert m["size_capped_share"] != m["size_capped_share"]


def test_summarise_variant_carries_the_sizing_block():
    rows = [_fill_row(stake=200.0, stake_asked=1000.0, fill_note="size_capped", match_id="m1", pnl=None,
                      status="filled", variant="w05_e03", decided_at=T0, settled_at=None, result=None, clv=None,
                      in_play=False, p_used=0.5, team="A", filled_at=T0 + MIN)]
    s = review.summarise_variant("w05_e03", rows, bankroll=100_000.0, review_day=T0.date())
    assert s["sizing"]["size_capped_fills"] == 1
    assert s["sizing"]["stake_fill_rate"] == pytest.approx(200.0 / 1000.0)


def test_paper_bets_model_has_the_s87_columns():
    cols = PaperBet.__table__.columns
    assert "stake_asked" in cols and "fill_note" in cols
    assert cols["stake_asked"].nullable and cols["fill_note"].nullable


def test_db_init_adds_the_s87_columns_with_if_not_exists():
    from backend import db_init

    statements = "\n".join(db_init.MIGRATION_STATEMENTS)
    assert "ALTER TABLE paper_bets" in statements
    assert "ADD COLUMN IF NOT EXISTS stake_asked DOUBLE PRECISION" in statements
    assert "ADD COLUMN IF NOT EXISTS fill_note TEXT" in statements


def test_daily_report_renders_the_fills_and_sizes_table(monkeypatch):
    monkeypatch.setattr(pt, "PREMATCH_NEW_RISK", True)  # S106: pre-match entry is off by default
    from backend.models.paper_bets import PaperBet as Bet
    from scripts import paper_report as pr

    feed = SizedFeed(sizes=[_size_row(T0 + timedelta(seconds=30), selection="home", back=2.2, back_size=150.0)])
    session, _report = _two_cycle_scenario(feed)
    rows = [{c.name: getattr(r, c.name) for c in Bet.__table__.columns} for r in session.rows]
    now = T0 + timedelta(hours=1)
    state = review.review(rows, {v.name: v.bankroll for v in pt.build_variants()}, now=now)
    text = pr.render(state, rows, now)
    assert "## Fills and sizes (S87)" in text
    assert "| w05_e03 |" in text
    capped = [v for v in state["variants"] if v["sizing"]["size_capped_fills"] > 0]
    assert capped, "the scenario caps fills, so the report must show a size-capped share"


# ----------------------------------------------------------------------------- hard loss limit (operator, 2026-11-03)
def test_a_fill_beyond_the_hard_loss_limit_is_refused_and_a_reducing_fill_passes():
    from datetime import datetime, timezone

    from backend.models.paper_bets import PaperBet
    from virtual_trading import paper_ledger as pl
    from virtual_trading.memory_session import MemorySession

    t0 = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)

    def bet(status, side, stake, price=2.0, minutes=0):
        return PaperBet(bet_id=None, variant="v", match_id="m", decided_at=t0 + timedelta(minutes=minutes),
                        filled_at=t0 if status == "filled" else None, status=status, side=side, selection="home",
                        team="A", price_limit=price, price_filled=price if status == "filled" else None,
                        stake=stake, liability=stake, reason="add", p_model=0.6, p_market=0.5, p_used=0.55,
                        edge=0.05, source="inplay", in_play=True, book={})

    book = {"back_home": 2.0, "lay_home": 2.0, "back_away": 2.0, "lay_away": 2.0}
    held = bet("filled", "BACK", 100_000.0)                       # away outcome already at -100,000
    too_far = bet("pending", "BACK", 30_000.0, minutes=1)         # would take it to -130,000
    reducing = bet("pending", "LAY", 20_000.0, minutes=2)         # brings away back towards zero
    session = MemorySession([held, too_far, reducing])
    counts = _run(pl.fill_pending(session, lambda mid, t: book, t0 + timedelta(minutes=10),
                                  hard_loss_limit=120_000.0))
    assert too_far.status == "unfilled" and too_far.fill_note == "over_loss_limit"
    assert reducing.status == "filled"
    assert counts["over_loss_limit"] == 1
