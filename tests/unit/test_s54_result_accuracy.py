"""S54: accurate match results from recorded prices and the official scoreboard.

Fakes only: the store/publish path is replaced, and the clock is monkeypatched.
No database or Redis is touched.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from scraper import lotus_result_detector as ld
from scraper.lotus_result_detector import LotusResultDetector, parse_result_comment

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
MID = "lb_36255271"
HOME = "Afghanistan"
AWAY = "Zimbabwe"


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


@pytest.fixture
def clock(monkeypatch):
    c = Clock(T0)
    monkeypatch.setattr(ld, "_utcnow", lambda: c.now)
    return c


@pytest.fixture
def stored(monkeypatch):
    """Replace the DB/Redis path with a recorder; returns the list of stored results."""
    calls: list[dict] = []

    async def fake_store(self, result):
        calls.append(dict(result))

    monkeypatch.setattr(LotusResultDetector, "_store_and_publish", fake_store)
    return calls


def _tick(det, *, bh=None, ba=None, lh=None, la=None, live=True, home=HOME, away=AWAY, mid=MID):
    det.update_tick(
        match_id=mid,
        team_home=home,
        team_away=away,
        competition="Test Cup",
        is_live=live,
        back_home=bh,
        back_away=ba,
        lay_home=lh,
        lay_away=la,
    )


def _absent(det, clock, seconds=ld.UNRESOLVED_ABSENCE_SECONDS + 60, mid=MID):
    """Advance the clock past the absence thresholds (incl. the 45-min unresolved wait) and run the batch check."""
    clock.advance(seconds)
    return asyncio.run(det.check_completed(set()))


# --- price inference -------------------------------------------------------

def test_decisive_home_win_from_back_price(clock, stored):
    det = LotusResultDetector()
    _tick(det, bh=1.04, ba=20.0)
    results = _absent(det, clock)

    assert len(results) == 1
    r = results[0]
    assert r["result_type"] == "win"
    assert r["winner"] == HOME
    assert r["loser"] == AWAY
    assert r["source"] == "lotusbook_odds"
    assert r["margin"] == ""
    assert r["closing_back_home"] == 1.04
    assert [c["winner"] for c in stored] == [HOME]


def test_decisive_away_win_from_lay_price(clock, stored):
    det = LotusResultDetector()
    _tick(det, bh=2.5, ba=1.6, la=1.05)
    results = _absent(det, clock)

    assert len(results) == 1
    assert results[0]["result_type"] == "win"
    assert results[0]["winner"] == AWAY
    assert results[0]["loser"] == HOME
    assert results[0]["closing_lay_away"] == 1.05


def test_disappearance_mid_match_is_unresolved_not_no_result(clock, stored):
    det = LotusResultDetector()
    _tick(det, bh=1.8, ba=2.1)
    _tick(det, bh=1.9, ba=2.0)
    results = _absent(det, clock)

    assert len(results) == 1
    r = results[0]
    assert r["result_type"] == "unresolved"
    assert r["winner"] == ""
    assert r["loser"] == ""
    assert r["closing_back_home"] == 1.9
    assert [c["result_type"] for c in stored] == ["unresolved"]


def test_no_prices_at_all_is_unresolved(clock, stored):
    det = LotusResultDetector()
    _tick(det, live=True)  # no quotes recorded at all
    results = _absent(det, clock)

    assert len(results) == 1
    assert results[0]["result_type"] == "unresolved"
    assert results[0]["winner"] == ""


def test_no_result_is_never_written_from_price_disappearance(clock, stored):
    det = LotusResultDetector()
    _tick(det, bh=1.5, ba=1.5)
    _tick(det, bh=2.0, ba=1.9)
    results = _absent(det, clock)

    assert all(r["result_type"] != "no_result" for r in results)
    assert all(c["result_type"] != "no_result" for c in stored)


def test_decisive_tick_outside_window_does_not_count(clock, stored):
    det = LotusResultDetector()
    _tick(det, bh=1.02, ba=30.0)            # decisive, but 30 minutes before disappearance
    clock.advance(30 * 60)
    _tick(det, bh=1.9, ba=2.0)              # last sighting, not decisive
    results = _absent(det, clock)

    assert results[0]["result_type"] == "unresolved"


def test_unresolved_stays_eligible_for_scoreboard(clock, stored):
    det = LotusResultDetector()
    _tick(det, bh=1.8, ba=2.1)
    _absent(det, clock)

    # Not blocked from later updates, and not re-reported on the next batch
    _tick(det, bh=1.8, ba=2.1)
    assert asyncio.run(det.check_completed(set())) == []

    r = asyncio.run(det.update_scoreboard(MID, f"{HOME} won by 25 runs", finished=True))
    assert r is not None
    assert r["source"] == "lotusbook_scoreboard"
    assert r["winner"] == HOME
    assert [c["source"] for c in stored] == ["lotusbook_odds", "lotusbook_scoreboard"]


# --- scoreboard ------------------------------------------------------------

def test_scoreboard_comment_overrides_price_inference(clock, stored):
    det = LotusResultDetector()
    _tick(det, bh=1.04, ba=20.0)            # price says home
    _absent(det, clock)

    r = asyncio.run(det.update_scoreboard(MID, f"{AWAY} won by 25 runs", finished=True))

    assert r["winner"] == AWAY
    assert r["loser"] == HOME
    assert r["result_type"] == "win"
    assert r["margin"] == "25 runs"
    assert r["source"] == "lotusbook_scoreboard"
    assert [c["winner"] for c in stored] == [HOME, AWAY]


def test_price_never_overrides_scoreboard(clock, stored):
    det = LotusResultDetector()
    _tick(det, bh=1.04, ba=20.0)
    asyncio.run(det.update_scoreboard(MID, f"{AWAY} won by 6 wickets", finished=True))

    # A later price-based pass over the same match must not write anything
    _tick(det, bh=1.02, ba=40.0)
    assert _absent(det, clock) == []
    assert asyncio.run(det.update_scoreboard(MID, f"{HOME} won by 1 run", finished=True)) is None

    assert [c["winner"] for c in stored] == [AWAY]
    assert stored[0]["source"] == "lotusbook_scoreboard"


def test_scoreboard_before_disappearance_blocks_price_write(clock, stored):
    det = LotusResultDetector()
    _tick(det, bh=1.04, ba=20.0)
    asyncio.run(det.update_scoreboard(MID, f"{HOME} won by 10 runs", finished=True))

    assert _absent(det, clock) == []
    assert len(stored) == 1


def test_scoreboard_ignored_when_not_finished(clock, stored):
    det = LotusResultDetector()
    _tick(det, bh=1.04, ba=20.0)

    assert asyncio.run(det.update_scoreboard(MID, f"{HOME} won by 10 runs", finished=False)) is None
    assert stored == []
    # The match is still eligible for the price inference afterwards
    assert _absent(det, clock)[0]["winner"] == HOME


def test_scoreboard_winner_uses_exact_tick_team_string(clock, stored):
    det = LotusResultDetector()
    _tick(det, bh=1.5, ba=2.5, home="Melbourne Stars W", away="Perth Scorchers W", mid="lb_36262294")

    r = asyncio.run(
        det.update_scoreboard("lb_36262294", "melbourne stars w  won by 4 wickets", finished=True)
    )
    assert r["winner"] == "Melbourne Stars W"
    assert r["team_home"] == "Melbourne Stars W"


def test_scoreboard_unknown_winner_writes_nothing(clock, stored):
    det = LotusResultDetector()
    _tick(det, bh=1.5, ba=2.5)

    assert asyncio.run(det.update_scoreboard(MID, "Someone Else won by 3 runs", finished=True)) is None
    assert stored == []


def test_scoreboard_untracked_match_writes_nothing(stored):
    det = LotusResultDetector()
    assert asyncio.run(det.update_scoreboard("lb_missing", f"{HOME} won by 3 runs", finished=True)) is None
    assert stored == []


def test_scoreboard_tie_no_result_and_abandoned(clock, stored):
    det = LotusResultDetector()
    _tick(det, bh=1.5, ba=2.5)

    tie = asyncio.run(det.update_scoreboard(MID, "Match tied", finished=True))
    assert tie["result_type"] == "tie"
    assert tie["winner"] == "" and tie["loser"] == ""
    assert tie["source"] == "lotusbook_scoreboard"
    # Once the scoreboard has written a result, later comments are ignored
    assert asyncio.run(det.update_scoreboard(MID, "No result", finished=True)) is None
    assert len(stored) == 1


def test_scoreboard_no_result_comment(clock, stored):
    det = LotusResultDetector()
    _tick(det, bh=1.5, ba=2.5)

    r = asyncio.run(det.update_scoreboard(MID, "No result", finished=True))
    assert r["result_type"] == "no_result"
    assert r["winner"] == ""
    assert r["margin"] == ""


@pytest.mark.parametrize(
    "comment, expected",
    [
        ("Afghanistan won by 25 runs", ("win", "Afghanistan", "25 runs")),
        ("Zimbabwe won by 6 wickets.", ("win", "Zimbabwe", "6 wickets")),
        ("Oman won by 1 run", ("win", "Oman", "1 run")),
        ("Dubai Capitals won", ("win", "Dubai Capitals", "")),
        ("Match tied", ("tie", "", "")),
        ("Match Tied", ("tie", "", "")),
        ("No result", ("no_result", "", "")),
        ("No Result.", ("no_result", "", "")),
        ("Match abandoned", ("abandoned", "", "")),
        ("Match drawn", ("draw", "", "")),
        ("Innings break", None),
        ("", None),
    ],
)
def test_parse_result_comment_forms(comment, expected):
    assert parse_result_comment(comment) == expected


# --- storage path ----------------------------------------------------------

class _FakeSession:
    def __init__(self, merged: list):
        self._merged = merged

    async def merge(self, record):
        self._merged.append(record)

    async def get(self, model, key):
        rows = [r for r in self._merged if r.match_id == key]
        return rows[-1] if rows else None


def test_store_upserts_and_skips_publish_for_unresolved(monkeypatch):
    merged: list = []
    published: list = []

    class _CM:
        async def __aenter__(self):
            return _FakeSession(merged)

        async def __aexit__(self, *exc):
            return False

    class _Redis:
        async def publish_event(self, channel, payload):
            published.append((channel, payload))

    async def fake_get_redis():
        return _Redis()

    monkeypatch.setattr(ld, "get_session", lambda: _CM())
    monkeypatch.setattr(ld, "get_redis", fake_get_redis)

    det = LotusResultDetector()
    base = {
        "match_id": MID,
        "completed_at": T0.isoformat(),
        "winner": "",
        "loser": "",
        "margin": "",
        "team_home": HOME,
        "team_away": AWAY,
        "source": "lotusbook_odds",
        "closing_back_home": 1.8,
        "closing_back_away": 2.1,
        "closing_lay_home": None,
        "closing_lay_away": None,
    }

    asyncio.run(det._store_and_publish({**base, "result_type": "unresolved"}))
    assert len(merged) == 1
    assert merged[0].result_type == "unresolved"
    assert published == []

    asyncio.run(det._store_and_publish({**base, "result_type": "tie", "source": "lotusbook_scoreboard"}))
    assert len(merged) == 2
    assert [p[1]["result_type"] for p in published] == ["tie"]


def test_simulated_result_is_stored_but_never_published(monkeypatch):
    """S50: SRL results are recorded for later study but never reach settlement or live consumers."""
    merged: list = []
    published: list = []

    class _CM:
        async def __aenter__(self):
            return _FakeSession(merged)

        async def __aexit__(self, *exc):
            return False

    class _Redis:
        async def publish_event(self, channel, payload):
            published.append((channel, payload))

    async def fake_get_redis():
        return _Redis()

    monkeypatch.setattr(ld, "get_session", lambda: _CM())
    monkeypatch.setattr(ld, "get_redis", fake_get_redis)

    asyncio.run(LotusResultDetector()._store_and_publish({
        "match_id": "lb_srl", "completed_at": T0.isoformat(), "winner": "Auckland SRL", "loser": "Wellington SRL",
        "margin": "", "team_home": "Auckland SRL", "team_away": "Wellington SRL", "source": "lotusbook_odds",
        "result_type": "win", "closing_back_home": 1.01, "closing_back_away": 16.5,
        "closing_lay_home": None, "closing_lay_away": None,
    }))
    assert len(merged) == 1 and merged[0].winner == "Auckland SRL"
    assert published == []


def test_unresolved_match_that_reappears_is_judged_again(clock, stored):
    det = LotusResultDetector()
    det.update_tick(match_id=MID, team_home=HOME, team_away=AWAY, competition="", is_live=True,
                    back_home=1.8, back_away=2.1)
    clock.advance(seconds=10_000)
    asyncio.run(det.check_completed(set()))
    assert stored[-1]["result_type"] == "unresolved"

    det.update_tick(match_id=MID, team_home=HOME, team_away=AWAY, competition="", is_live=True,
                    back_home=1.02, back_away=30.0)
    clock.advance(seconds=10_000)
    asyncio.run(det.check_completed(set()))
    assert stored[-1]["result_type"] == "win" and stored[-1]["winner"] == HOME


def test_vanished_match_without_decisive_prices_waits_45_minutes(clock, stored):
    det = LotusResultDetector()
    _tick(det, bh=1.9, ba=2.0)
    clock.advance(seconds=600)  # off the listing 10 min (an innings break)
    asyncio.run(det.check_completed(set()))
    assert stored == []
    clock.advance(seconds=ld.UNRESOLVED_ABSENCE_SECONDS)
    asyncio.run(det.check_completed(set()))
    assert stored[-1]["result_type"] == "unresolved"


def test_manual_result_is_never_replaced_by_the_detector(monkeypatch):
    """S76: a result entered from a public scorecard stays; a later detector write is skipped and not published."""
    from backend.models.results import MatchResultRecord

    merged: list = [MatchResultRecord(match_id=MID, completed_at=T0, winner=HOME, loser=AWAY, result_type="win",
                                      margin="2 runs", team_home=HOME, team_away=AWAY, source="public_scorecard")]
    published: list = []

    class _CM:
        async def __aenter__(self):
            return _FakeSession(merged)

        async def __aexit__(self, *exc):
            return False

    class _Redis:
        async def publish_event(self, channel, payload):
            published.append((channel, payload))

    async def fake_get_redis():
        return _Redis()

    monkeypatch.setattr(ld, "get_session", lambda: _CM())
    monkeypatch.setattr(ld, "get_redis", fake_get_redis)

    asyncio.run(LotusResultDetector()._store_and_publish({
        "match_id": MID, "completed_at": T0.isoformat(), "winner": AWAY, "loser": HOME, "margin": "",
        "team_home": HOME, "team_away": AWAY, "source": "lotusbook_odds", "result_type": "win",
        "closing_back_home": 9.0, "closing_back_away": 1.05, "closing_lay_home": None, "closing_lay_away": None,
    }))
    assert len(merged) == 1 and merged[0].winner == HOME
    assert published == []
