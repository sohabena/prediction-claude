"""S105 criterion 6: the trader skips a real match whose only match-odds market is the bookmaker's (_BR) one.

Fakes only, no database. Covers the candidate rule (``select_candidates``), the cycle counter (``run_cycle``), the live
registry SQL (``DbFeed.registry``), the replay's as-of rule (``ReplayFeed.registry``) and its one-read load.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone

from virtual_trading import paper_trader as pt
from virtual_trading import replay_feed as rf
from virtual_trading.memory_session import MemorySession

NOW = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
START = NOW + timedelta(hours=1)
MIN = timedelta(minutes=1)


def _reg(mid="a", start=START, actual=None, simulated=False, **extra):
    row = {
        "match_id": mid,
        "team_home": "Alpha",
        "team_away": "Beta",
        "competition": "Karnataka T20",
        "match_start_time": start,
        "actual_start_time": actual,
        "is_simulated": simulated,
        "toss_winner": None,
        "toss_decision": None,
        "settled": False,
        "canonical_match_id": None,
    }
    row.update(extra)
    return row


def _price(t, mid="a"):
    return {"match_id": mid, "time": t, "back_home": 2.2, "lay_home": 2.3, "back_away": 1.8, "lay_away": 1.9,
            "source": "listing"}


# ----------------------------------------------------------------------------- select_candidates (live and replay rows)
def test_a_bookmaker_only_row_is_skipped_with_its_own_reason():
    candidates, skipped = pt.select_candidates([_reg(bookmaker_only=True)], {"a": NOW}, NOW)
    assert candidates == []
    assert skipped == {"bookmaker_only": 1}


def test_a_row_without_the_key_is_not_skipped_for_old_feeds():
    candidates, skipped = pt.select_candidates([_reg()], {"a": NOW}, NOW)
    assert [c.match_id for c in candidates] == ["a"]
    assert "bookmaker_only" not in skipped


def test_a_row_with_the_flag_false_is_not_skipped():
    candidates, skipped = pt.select_candidates([_reg(bookmaker_only=False)], {"a": NOW}, NOW)
    assert [c.match_id for c in candidates] == ["a"]
    assert "bookmaker_only" not in skipped


def test_a_bookmaker_only_match_with_no_recent_price_is_not_counted_at_all():
    # Same rule as every other registry row: no price in the window is not a candidate and not a skip.
    candidates, skipped = pt.select_candidates([_reg(bookmaker_only=True)], {}, NOW)
    assert candidates == [] and skipped == {}


def test_simulated_is_checked_before_bookmaker_only_so_srl_counts_are_unchanged():
    srl = _reg(simulated=True, bookmaker_only=True)
    _, skipped = pt.select_candidates([srl], {"a": NOW}, NOW)
    assert skipped == {"simulated": 1}
    # With SRL included, a bookmaker-only SRL match is still unbettable and is skipped.
    candidates, skipped = pt.select_candidates([srl], {"a": NOW}, NOW, include_simulated=True)
    assert candidates == [] and skipped == {"bookmaker_only": 1}


def test_the_skip_counts_each_bookmaker_only_match_once():
    rows = [_reg("a", bookmaker_only=True), _reg("b", bookmaker_only=True), _reg("c")]
    candidates, skipped = pt.select_candidates(rows, {"a": NOW, "b": NOW, "c": NOW}, NOW)
    assert [c.match_id for c in candidates] == ["c"]
    assert skipped == {"bookmaker_only": 2}


# ----------------------------------------------------------------------------- run_cycle: the counter, no pricing
class _Feed:
    """The read side of one cycle, with no prices, contexts or results: only the registry matters here."""

    def __init__(self, rows, last):
        self.rows = list(rows)
        self.last = dict(last)

    async def last_prices(self, now):
        return dict(self.last)

    async def registry(self, match_ids):
        return [dict(r) for r in self.rows if r["match_id"] in set(match_ids)]

    async def price_rows(self, match_ids, since):
        return []

    async def context_rows(self, match_ids, since, now):
        return []

    async def results(self, match_ids):
        return {}


class _NeverPrice:
    def estimate(self, *args, **kwargs):
        raise AssertionError("a bookmaker-only match must not reach the probability service")


def test_run_cycle_reports_the_bookmaker_only_skip_and_never_prices_the_match():
    session = MemorySession()
    feed = _Feed([_reg(bookmaker_only=True)], {"a": NOW})
    report = asyncio.run(pt.run_cycle(session, feed, _NeverPrice(), pt.build_variants(), now=NOW))
    assert report["candidates"] == 0
    assert report["skipped_registry"] == {"bookmaker_only": 1}
    assert report["intents"] == 0
    assert session.rows == []


# ----------------------------------------------------------------------------- live registry SQL
def test_the_live_registry_sql_carries_the_bookmaker_only_rule():
    class Capture:
        sql = ""
        params = None

        async def execute(self, stmt, params=None):
            Capture.sql = str(stmt)
            Capture.params = params

            class Result:
                def mappings(self):
                    return self

                def all(self):
                    return []

            return Result()

    asyncio.run(pt.DbFeed(Capture()).registry(["a"]))
    sql = Capture.sql
    assert "AS bookmaker_only" in sql
    assert "RIGHT(b.market_id, 3) = '_BR'" in sql
    assert "COALESCE(RIGHT(x.market_id, 3), '') <> '_BR'" in sql
    assert "b.market_type = 'match_odds'" in sql and "x.market_type = 'match_odds'" in sql
    assert "AND NOT COALESCE((SELECT true FROM market_ticks x" in sql and sql.count("LIMIT 1") == 2
    assert ";" not in sql
    assert sql.strip().upper().startswith("SELECT")


def test_the_live_registry_passes_the_flag_through_to_the_candidate_filter():
    class Row(dict):
        pass

    class Result:
        def mappings(self):
            return self

        def all(self):
            return [Row(match_id="a", team_home="A", team_away="B", competition="C", match_start_time=START,
                        actual_start_time=None, is_simulated=False, toss_winner=None, toss_decision=None,
                        canonical_match_id=None, settled=False, bookmaker_only=True)]

    class Session:
        async def execute(self, stmt, params=None):
            return Result()

    rows = asyncio.run(pt.DbFeed(Session()).registry(["a"]))
    assert rows[0]["bookmaker_only"] is True
    candidates, skipped = pt.select_candidates(rows, {"a": NOW}, NOW)
    assert candidates == [] and skipped == {"bookmaker_only": 1}


def test_the_replay_query_is_a_read_of_the_first_times_and_shares_the_filters():
    sql = pt.MATCH_ODDS_FIRST_SQL
    assert sql.strip().upper().startswith("SELECT") and ";" not in sql
    assert "FROM market_ticks" in sql and "market_type = 'match_odds'" in sql and "source = 'lotusbook'" in sql
    assert "min(time) FILTER (WHERE RIGHT(market_id, 3) = '_BR')" in sql
    assert "COALESCE(RIGHT(market_id, 3), '') <> '_BR'" in sql
    assert "time <= :until" in sql and "match_id = ANY(:ids)" in sql


# ----------------------------------------------------------------------------- replay: the as-of rule
def _feed(match_odds_times=None):
    return rf.ReplayFeed(prices=[], registry=[_reg()], context=[], results=[], end=NOW + timedelta(hours=3),
                         match_odds_times=match_odds_times or [])


def _flag(feed, t):
    feed.seek(t)
    return asyncio.run(feed.registry(["a"]))[0]["bookmaker_only"]


def test_replay_is_true_from_the_first_bookmaker_tick_and_not_before():
    first_br = NOW + 10 * MIN
    feed = _feed([{"match_id": "a", "first_bookmaker": first_br, "first_exchange": None}])
    assert _flag(feed, first_br - MIN) is False  # no look-ahead: the tick is not yet visible
    assert _flag(feed, first_br) is True  # the boundary: visible at its own time


def test_replay_turns_false_once_an_exchange_tick_is_visible():
    first_br = NOW + 10 * MIN
    first_ex = NOW + 20 * MIN
    feed = _feed([{"match_id": "a", "first_bookmaker": first_br, "first_exchange": first_ex}])
    assert _flag(feed, NOW + 15 * MIN) is True
    assert _flag(feed, first_ex - MIN) is True
    assert _flag(feed, first_ex) is False  # the exchange tick at or before t settles the question


def test_replay_with_exchange_first_at_the_same_time_as_the_bookmaker_is_not_skipped():
    t = NOW + 10 * MIN
    feed = _feed([{"match_id": "a", "first_bookmaker": t, "first_exchange": t}])
    assert _flag(feed, t) is False


def test_replay_never_skips_a_match_with_no_match_page_rows():
    feed = _feed([])
    assert _flag(feed, NOW + 2 * MIN) is False
    feed_none = _feed([{"match_id": "a", "first_bookmaker": None, "first_exchange": None}])
    assert _flag(feed_none, NOW + 2 * MIN) is False


def test_replay_feed_built_without_the_rows_keeps_old_behaviour():
    feed = rf.ReplayFeed(prices=[], registry=[_reg()], context=[], results=[], end=NOW + timedelta(hours=3))
    assert _flag(feed, NOW + 5 * MIN) is False


def test_replay_skip_follows_the_cursor_through_select_candidates():
    first_br = NOW + 10 * MIN
    feed = _feed([{"match_id": "a", "first_bookmaker": first_br, "first_exchange": None}])
    feed.seek(first_br - MIN)
    before = asyncio.run(feed.registry(["a"]))
    assert pt.select_candidates(before, {"a": first_br - MIN}, first_br - MIN)[0] != []
    feed.seek(first_br)
    after = asyncio.run(feed.registry(["a"]))
    candidates, skipped = pt.select_candidates(after, {"a": first_br}, first_br)
    assert candidates == [] and skipped == {"bookmaker_only": 1}


# ----------------------------------------------------------------------------- replay: one read in load()
class _Res:
    def __init__(self, rows=(), scalar=None):
        self._rows = list(rows)
        self._scalar = scalar

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def scalar(self):
        return self._scalar


class _LoadSession:
    def __init__(self, first_rows):
        self.first_rows = first_rows
        self.seen: list[tuple[str, dict]] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.seen.append((sql, dict(params or {})))
        if "information_schema" in sql:
            return _Res(scalar=True)
        if "first_bookmaker" in sql:
            return _Res(rows=self.first_rows)
        if "FROM market_ticks" in sql:  # the size query
            return _Res()
        if "FROM match_training_status" in sql:
            return _Res(rows=[_reg()])
        if "FROM v_match_odds" in sql:
            return _Res(rows=[_price(NOW - MIN)])
        return _Res()


def test_load_reads_the_first_times_once_up_to_the_end():
    first_br = NOW + 10 * MIN
    session = _LoadSession([{"match_id": "a", "first_bookmaker": first_br, "first_exchange": None}])
    end = NOW + timedelta(hours=1)
    feed = asyncio.run(rf.ReplayFeed.load(session, end=end))
    issued = [(sql, params) for sql, params in session.seen if "first_bookmaker" in sql]
    assert len(issued) == 1
    assert issued[0][1] == {"ids": ["a"], "until": end}
    assert _flag(feed, first_br - MIN) is False
    assert _flag(feed, first_br) is True


def test_load_with_no_price_matches_issues_no_first_time_read():
    class Empty(_LoadSession):
        async def execute(self, stmt, params=None):
            if "FROM v_match_odds" in str(stmt):
                self.seen.append((str(stmt), {}))
                return _Res(rows=[])
            return await super().execute(stmt, params)

    session = Empty([])
    feed = asyncio.run(rf.ReplayFeed.load(session, end=NOW + timedelta(hours=1)))
    assert not any("first_bookmaker" in sql for sql, _ in session.seen)
    assert feed._match_odds_first == {}


def test_the_replay_rule_and_the_live_rule_share_one_definition_of_the_suffix():
    # The replay SQL and the live registry SQL spell the suffix test the same way (a guard against drift).
    live = inspect.getsource(pt.DbFeed.registry)
    assert "RIGHT(b.market_id, 3) = '_BR'" in live
    assert "RIGHT(market_id, 3) = '_BR'" in pt.MATCH_ODDS_FIRST_SQL
