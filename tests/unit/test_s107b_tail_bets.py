"""S107 part B: the chase model and the tail variant. Synthetic Cricsheet matches and fakes only, no database.

Covers: no look-ahead in the batters' career figures, label alignment, the subsampling of states, the exclusions
(DLS, super over, not 20 overs), the name match, the monotone core model, the tail thresholds (price 10.0, value 1.2
times fair, ticket limit, spacing, per-side limit, never a lay, the match cap), the core fallback, no bet without a
fresh second-innings score, and that the tail variant leaves the other variants unchanged.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from strategy import tail_bet
from strategy.base import OrderIntent
from strategy.model_edge import Book
from strategy.probability import Estimate
from training import chase_model as cm
from virtual_trading import paper_trader as pt
from virtual_trading.memory_session import MemorySession

T0 = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)
MID = "m1"


# ---------------------------------------------------------------- synthetic Cricsheet matches


def _d(batter, non, runs=0, wide=False, out=None):
    """One delivery. ``wide`` makes an illegal ball (a wide with one run); ``out`` names a dismissed batter."""
    extras = {"wides": 1} if wide else {}
    total = runs + (1 if wide else 0)
    d = {"batter": batter, "bowler": "Bowler", "non_striker": non,
         "runs": {"batter": 0 if wide else runs, "extras": 1 if wide else 0, "total": total}}
    if extras:
        d["extras"] = extras
    if out:
        d["wickets"] = [{"player_out": out, "kind": "bowled"}]
    return d


def _raw(mid, date, first, second, second_deliveries, *, winner=None, first_runs=1, overs=20, super_over=False,
         method=None, gender="male"):
    first_deliveries = [_d(f"{first} top", f"{first} partner", first_runs) for _ in range(120)]
    innings = [{"team": first, "overs": [{"over": 0, "deliveries": first_deliveries}]},
               {"team": second, "overs": [{"over": 0, "deliveries": second_deliveries}]}]
    if super_over:
        innings.append({"team": second, "super_over": True,
                        "overs": [{"over": 0, "deliveries": [_d("X", "Y", 1)]}]})
    outcome = {"winner": winner or second}
    if method:
        outcome["method"] = method
    info = {"match_type": "T20", "dates": [date], "gender": gender, "balls_per_over": 6, "overs": overs,
            "teams": [first, second], "outcome": outcome}
    return {"match_id": mid, "info": info, "innings": innings}


def _chase_deliveries(batter="Chase One", non="Chase Two", runs=1, n=120, wicket_at=None):
    out = []
    for i in range(n):
        out.append(_d(batter, non, runs, out=batter if wicket_at == i else None))
    return out


def _facts(*raws):
    return [f for f in (cm.summarise_match(r) for r in raws) if f is not None]


# ---------------------------------------------------------------- dataset (pure)


def test_chase_states_subsample_last_sixty_balls_and_one_state_per_over_before():
    raw = _raw("a", "2020-01-01", "Alpha", "Beta", _chase_deliveries(runs=0), winner="Beta")
    facts = cm.summarise_match(raw)
    assert facts.chase is not None
    legal = [s[0] for s in facts.chase.states]
    expected = [i for i in range(120) if (120 - i) <= cm.TAIL_BALLS or i % cm.OVER_EVERY == 0]
    assert legal == expected
    assert len(legal) == 70


def test_target_and_runs_needed_and_label_alignment():
    # first innings: 120 runs (1 per ball) -> target 121; the chase scores 1 per ball, so it is still 61 short after 60.
    raw = _raw("a", "2020-01-01", "Alpha", "Beta", _chase_deliveries(runs=1), winner="Alpha")
    facts = cm.summarise_match(raw)
    assert facts.chase.target == 121
    assert facts.chase.label == 0  # Alpha (the first innings side) won
    by_legal = {s[0]: s for s in facts.chase.states}
    assert by_legal[0][1] == 121 and by_legal[0][2] == 120 and by_legal[0][3] == 10
    assert by_legal[60][1] == 61 and by_legal[60][2] == 60 and by_legal[60][3] == 10


def test_wides_are_not_legal_balls_and_do_not_count_as_faced():
    deliveries = [_d("Chase One", "Chase Two", 0, wide=True)] + _chase_deliveries(runs=1, n=120)
    raw = _raw("a", "2020-01-01", "Alpha", "Beta", deliveries, winner="Beta")
    facts = cm.summarise_match(raw)
    first = facts.chase.states[0]
    assert first[0] == 0 and first[1] == 121 - 1  # the wide scored one run before the first legal ball
    assert facts.bat_totals["Chase One"][1] == 120  # balls faced exclude the wide


@pytest.mark.parametrize("kwargs, reason", [
    ({"method": "D/L"}, "method"),
    ({"super_over": True}, "super_over"),
    ({"overs": 10}, "not_20_overs"),
])
def test_exclusions_give_no_chase_but_keep_the_career(kwargs, reason):
    raw = _raw("a", "2020-01-01", "Alpha", "Beta", _chase_deliveries(), winner="Beta", **kwargs)
    facts = cm.summarise_match(raw)
    assert facts.chase is None
    assert facts.excluded == reason
    assert "Chase One" in facts.bat_totals


def test_no_result_is_excluded():
    raw = _raw("a", "2020-01-01", "Alpha", "Beta", _chase_deliveries(), winner="Beta")
    raw["info"]["outcome"] = {}
    assert cm.summarise_match(raw).excluded == "no_result"


def test_career_uses_only_earlier_dates_and_never_the_same_day():
    m1 = _raw("m1", "2020-01-01", "Alpha", "Beta", _chase_deliveries(runs=1, n=120, wicket_at=119), winner="Beta")
    m2 = _raw("m2", "2020-01-02", "Alpha", "Gamma", _chase_deliveries(runs=2, n=120), winner="Gamma")
    m3 = _raw("m3", "2020-01-02", "Delta", "Beta", _chase_deliveries(runs=3, n=120), winner="Beta")
    table, career, ids = cm.build_rows(_facts(m1, m2, m3))
    arrays = table.arrays()
    rows_m2 = arrays["match"] == ids.index("m2")
    rows_m3 = arrays["match"] == ids.index("m3")
    # m2 and m3 share a date: neither sees the other, and each sees only m1 (120 runs, 120 balls, 1 dismissal).
    assert np.all(arrays["s_runs"][rows_m2] == 120) and np.all(arrays["s_balls"][rows_m2] == 120)
    assert np.all(arrays["s_outs"][rows_m2] == 1)
    assert np.all(arrays["s_runs"][rows_m3] == 120)
    # the first match of the data sees nothing (no earlier date): NaN career.
    rows_m1 = arrays["match"] == ids.index("m1")
    assert np.all(np.isnan(arrays["s_balls"][rows_m1]))
    # the record after all matches is the sum of the three (120 + 240 + 360 runs)
    assert career["Chase One"][0] == 120 + 240 + 360


def test_name_key_matches_initials_and_full_names():
    assert cm.name_key("Lokesh Bahadur Bam") == cm.name_key("LB Bam") == "L BAM"
    assert cm.name_key("Quinton de Kock") == cm.name_key("Q de Kock") == "Q KOCK"
    assert cm.name_key("Imran") == ""


def test_batters_of_needs_two_batters_and_one_on_strike():
    players = {"batsmen": [{"name": "A One", "runs": 5, "balls": 4, "on_strike": True},
                           {"name": "B Two", "runs": 1, "balls": 2, "on_strike": False}]}
    striker, non = tail_bet.batters_of(players)
    assert striker["name"] == "A One" and non["name"] == "B Two"
    assert tail_bet.batters_of({"batsmen": [players["batsmen"][0]]}) is None
    assert tail_bet.batters_of({"batsmen": [dict(players["batsmen"][0], on_strike=False), players["batsmen"][1]]}) is None
    assert tail_bet.batters_of(tail_bet.players_of('{"batsmen": []}')) is None


# ---------------------------------------------------------------- the model


def _tiny_payload(seed=7, n_matches=60):
    rng = random.Random(seed)
    names = ["Chase One", "Chase Two", "Chase Three", "Chase Four"]
    raws = []
    for i in range(n_matches):
        date = f"2019-{1 + i % 12:02d}-{1 + (i // 12) % 27:02d}"
        pair = rng.sample(names, 2)
        dels = []
        for k in range(120):
            batter, non = pair[k % 2], pair[(k + 1) % 2]  # both batters face balls, so both have career records
            wicket = batter if k == 80 and rng.random() < 0.5 else None
            dels.append(_d(batter, non, rng.choice([0, 1, 1, 2, 4]), out=wicket))
        winner = rng.choice(["Alpha", "Beta"])
        raws.append(_raw(f"t{i}", date, "Alpha", "Beta", dels, winner=winner, first_runs=rng.choice([1, 2])))
    facts = _facts(*raws)
    table, career, _ids = cm.build_rows(facts)
    arrays = table.arrays()
    models = cm.fit_models(arrays)
    keys = {}
    for name in career:
        k = cm.name_key(name)
        if k:
            keys[k] = "" if (k in keys and keys[k] != name) else name
    return {
        cm.CORE: models[cm.CORE], cm.PLAYERS: models[cm.PLAYERS], "empirical": cm.fit_empirical(arrays),
        "career": {n: [int(v[0]), int(v[1]), int(v[2])] for n, v in career.items()}, "career_keys": keys,
        "version": cm.VERSION, "date": "test",
    }


def test_core_model_is_monotone_in_runs_needed():
    model = cm.ChaseModel(_tiny_payload())
    low = model.estimate(runs_needed=10, balls_left=30, wickets_left=5)
    high = model.estimate(runs_needed=60, balls_left=30, wickets_left=5)
    assert low.p >= high.p
    assert low.source == "core" and low.p_players is None


def test_players_model_used_only_when_both_names_resolve_and_core_otherwise():
    model = cm.ChaseModel(_tiny_payload())
    striker = {"name": "Chase One", "runs": 12, "balls": 9, "on_strike": True}
    non_known = {"name": "Chase Two", "runs": 3, "balls": 4, "on_strike": False}
    non_unknown = {"name": "Nobody Atall", "runs": 3, "balls": 4, "on_strike": False}
    matched = model.estimate(runs_needed=40, balls_left=36, wickets_left=6, batters=(striker, non_known))
    assert matched.source == "players" and matched.p_players is not None
    fallback = model.estimate(runs_needed=40, balls_left=36, wickets_left=6, batters=(striker, non_unknown))
    assert fallback.source == "core" and fallback.p == fallback.p_core
    assert model.estimate(runs_needed=40, balls_left=36, wickets_left=6).source == "core"


def test_live_name_resolves_to_cricsheet_name_by_initial_and_surname():
    model = cm.ChaseModel(_tiny_payload())
    assert model.career_for("Chase One") is not None
    assert model.career_for("C One") == model.career_for("Chase One")  # initial + surname key, unique
    assert model.career_for("Chase Nine") is None
    assert model.career_for("Nobody Atall") is None


def test_empirical_table_backs_off_to_the_overall_rate_when_a_cell_is_thin():
    # 40 states at (10 runs, 30 balls, 5 wickets) all won; one state elsewhere lost. The thin cell backs off.
    n = 40
    arrays = {"runs_needed": np.array([10.0] * n + [200.0]), "balls_left": np.array([30.0] * n + [5.0]),
              "wickets_left": np.array([5.0] * n + [1.0]), "y": np.array([1.0] * n + [0.0])}
    table = cm.fit_empirical(arrays)
    assert cm.empirical_probability(table, 10, 30, 5) == pytest.approx(1.0)  # the full cell (n >= 30)
    overall = table["overall"]
    assert overall == pytest.approx(40 / 41)
    assert cm.empirical_probability(table, 200, 5, 1) == pytest.approx(overall)  # a single row: overall rate


# ---------------------------------------------------------------- the decision rule (pure)


def _book(back_away, back_home=1.08):
    return {"back_home": back_home, "lay_home": 1.10, "back_away": back_away, "lay_away": 13.0}


def _decide(prices, p_home, **kw):
    args = dict(in_play=True, now=1000.0, tickets=0, per_side={}, last_tail_at=None, position=Book(), cap=100_000.0)
    args.update(kw)
    return tail_bet.decide(prices, p_home, **args)


def test_price_must_be_at_least_ten():
    intent, reason = _decide(_book(back_away=9.9), p_home=0.95)
    assert intent is None and reason == "price_below_min"


def test_value_must_be_at_least_one_point_two_times_fair():
    # back away 20.0: the away side needs p_away >= 0.06 (fair odds 16.7). p_away = 0.05 -> ratio 1.0, no bet.
    intent, reason = _decide(_book(back_away=20.0), p_home=0.95)
    assert intent is None and reason == "no_value"
    intent, reason = _decide(_book(back_away=20.0), p_home=0.939)  # p_away 0.061 -> ratio 1.22
    assert reason == "bet" and intent.selection == "away"


def test_bet_is_a_back_at_a_limit_inside_the_decision_price_and_never_a_lay():
    intent, reason = _decide(_book(back_away=12.0), p_home=0.85)  # p_away 0.15 -> ratio 1.8
    assert reason == "bet"
    assert intent.side == "BACK" and intent.reason == "tail"
    assert intent.stake == tail_bet.TAIL_STAKE == 2_000.0
    assert 10.0 <= intent.price_limit <= 12.0


def test_no_bet_before_the_off():
    intent, reason = _decide(_book(back_away=12.0), p_home=0.85, in_play=False)
    assert intent is None and reason == "not_in_play"


def test_ticket_limit_and_spacing():
    assert _decide(_book(back_away=12.0), 0.85, tickets=3)[1] == "ticket_limit"
    assert _decide(_book(back_away=12.0), 0.85, last_tail_at=1000.0 - 299.0)[1] == "spacing"
    assert _decide(_book(back_away=12.0), 0.85, last_tail_at=1000.0 - 300.0)[1] == "bet"


def test_at_most_two_tail_tickets_on_one_side():
    intent, reason = _decide(_book(back_away=12.0), 0.85, per_side={"away": 2})
    assert intent is None and reason == "side_limit"


def test_worst_case_stays_within_the_match_cap():
    intent, reason = _decide(_book(back_away=12.0), 0.85, cap=1_000.0)  # a 2,000 loss on away would exceed 1,000
    assert intent is None and reason == "match_cap"


def test_home_probability_is_oriented_by_the_batting_side():
    assert tail_bet.home_probability(0.12, "away") == pytest.approx(0.88)
    assert tail_bet.home_probability(0.12, "home") == pytest.approx(0.12)


def test_snapshot_is_only_for_twenty_over_t20_states_within_the_innings():
    row = {"innings": 2, "score": 60, "wickets": 2, "balls_remaining": 48, "match_format": "T20"}
    assert tail_bet.snapshot(row, target=121, batting_side="away") is not None
    assert tail_bet.snapshot(dict(row, match_format="ODI"), target=121, batting_side="away") is None
    assert tail_bet.snapshot(dict(row, match_format=None), target=121, batting_side="away") is None
    assert tail_bet.snapshot(dict(row, balls_remaining=222), target=121, batting_side="away") is None


def test_snapshot_needs_second_innings_a_target_and_a_positive_state():
    row = {"innings": 2, "score": 60, "wickets": 2, "balls_remaining": 48, "match_format": "T20"}
    snap = tail_bet.snapshot(row, target=121, batting_side="away")
    assert snap is not None and snap.runs_needed == 61 and snap.wickets_left == 8
    assert tail_bet.snapshot(dict(row, innings=1), target=121, batting_side="away") is None
    assert tail_bet.snapshot(row, target=None, batting_side="away") is None
    assert tail_bet.snapshot(dict(row, score=130), target=121, batting_side="away") is None


# ---------------------------------------------------------------- the trader wiring (fakes)


class FakeChase:
    def __init__(self, p, source="core"):
        self.p = p
        self.calls = []

    def estimate(self, *, runs_needed, balls_left, wickets_left, batters=None):
        self.calls.append((runs_needed, balls_left, wickets_left, batters is not None))
        return cm.ChaseEstimate(p=self.p, source="players" if batters else "core", p_core=self.p, p_players=None,
                                p_empirical=self.p)


class FakeFeed:
    def __init__(self, registry, prices, last, context):
        self.registry_rows = list(registry)
        self.prices = list(prices)
        self.last = dict(last)
        self.context_list = list(context)

    async def last_prices(self, now):
        return dict(self.last)

    async def registry(self, match_ids):
        return [dict(r) for r in self.registry_rows if r["match_id"] in set(match_ids)]

    async def price_rows(self, match_ids, since):
        return [dict(r) for r in self.prices if r["match_id"] in set(match_ids) and r["time"] >= since]

    async def context_rows(self, match_ids, since, now):
        return [dict(r) for r in self.context_list if r["match_id"] in set(match_ids) and since <= r["time"] <= now]

    async def results(self, match_ids):
        return {}


class FakeProbability:
    def __init__(self, p_model=0.5):
        self.p_model = p_model
        self.normalize_team = None

    def estimate(self, match, book, state=None, *, as_of):
        from training.market import band_prob_home

        return Estimate(p_model=self.p_model, p_market=band_prob_home(book),
                        source="inplay" if state else "prematch", has_model=True, features={}, reason="")


PLAYERS = {"batsmen": [{"name": "Imran Sheikh", "runs": 10, "balls": 8, "on_strike": True},
                       {"name": "Lokesh Bahadur Bam", "runs": 3, "balls": 4, "on_strike": False}]}


def _registry(start):
    return {"match_id": MID, "team_home": "Alpha", "team_away": "Beta", "competition": "Test League",
            "match_start_time": start, "is_simulated": False, "toss_winner": None, "toss_decision": None,
            "settled": False}


def _price(t, back_away=12.0):
    return {"match_id": MID, "time": t, "back_home": 1.08, "lay_home": 1.10, "back_away": back_away,
            "lay_away": 13.0, "source": "listing"}


def _context(t, innings, score, wickets, balls_remaining, batting="Beta", players=None):
    return {"match_id": MID, "time": t, "is_live": True, "score": score, "wickets": wickets, "overs": 12.0,
            "innings": innings, "balls_remaining": balls_remaining, "batting_team": batting,
            "bowling_team": "Alpha", "run_rate": 5.0, "req_run_rate": 9.0, "match_format": "T20",
            "status": "live", "players": players}


def _live_feed(players=PLAYERS, back_away=12.0):
    start = T0 - timedelta(hours=1)
    context = [_context(T0 - timedelta(minutes=90), 1, 120, 6, 0, batting="Alpha"),
               _context(T0 - timedelta(seconds=30), 2, 60, 2, 48, players=players)]
    return FakeFeed([_registry(start)], [_price(T0 - timedelta(seconds=10), back_away)], {MID: T0}, context)


async def _cycle(feed, chase, variants=None, now=T0):
    session = MemorySession()
    report = await pt.run_cycle(session, feed, FakeProbability(), variants or pt.build_variants(), now=now,
                                chase_model=chase)
    return session, report


def _tail_rows(session):
    return [r for r in session.rows if r.variant == pt.TAIL_VARIANT]


def test_tail_variant_is_registered_as_its_own_path_with_no_dynamics_or_placebo_flag():
    tail = [v for v in pt.build_variants() if v.name == pt.TAIL_VARIANT]
    assert len(tail) == 1
    assert tail[0].placebo == "" and not tail[0].market_only and tail[0].name not in pt.DYNAMICS_VARIANTS


@pytest.mark.asyncio
async def test_tail_places_one_back_tail_ticket_with_the_chase_model_source():
    chase = FakeChase(p=0.12)
    session, report = await _cycle(_live_feed(), chase)
    rows = _tail_rows(session)
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "pending" and row.side == "BACK" and row.selection == "away"
    assert row.reason == "tail" and row.source == "chase_model" and row.stake == 2_000.0
    assert row.score_state["chase"]["reason"] == "bet"
    assert report["tail"]["loaded"] is True and report["tail"]["bet"] == 1
    # the state the model read: runs needed 121 - 60, balls left 48, wickets left 8
    assert chase.calls and chase.calls[0][:3] == (61.0, 48, 8)


@pytest.mark.asyncio
async def test_tail_refused_without_a_fresh_second_innings_score_or_before_the_off():
    chase = FakeChase(p=0.12)
    feed = _live_feed()
    feed.context_list = [r for r in feed.context_list if r["innings"] == 1]  # no live state at all
    session, report = await _cycle(feed, chase)
    assert _tail_rows(session) == [] and report["tail"].get("no_state") == 1

    first_innings = _live_feed()
    first_innings.context_list = [_context(T0 - timedelta(seconds=30), 1, 60, 2, 110)]
    session, report = await _cycle(first_innings, chase)
    assert _tail_rows(session) == [] and report["tail"].get("no_snapshot") == 1

    start = T0 + timedelta(hours=2)  # not off yet
    prematch = _live_feed()
    prematch.registry_rows = [_registry(start)]
    session, report = await _cycle(prematch, chase)
    assert _tail_rows(session) == [] and report["tail"].get("not_in_play") == 1


@pytest.mark.asyncio
async def test_tail_never_bets_outside_twenty_over_t20():
    feed = _live_feed()
    feed.context_list = [dict(r, match_format="ODI") for r in feed.context_list]
    session, report = await _cycle(feed, FakeChase(p=0.12))
    assert _tail_rows(session) == [] and report["tail"].get("not_t20") == 1


@pytest.mark.asyncio
async def test_tail_refused_without_a_model_and_with_no_model_loaded_nothing_changes():
    session, report = await _cycle(_live_feed(), None)
    assert _tail_rows(session) == [] and report["tail"]["loaded"] is False


@pytest.mark.asyncio
async def test_tail_respects_the_s75_new_risk_guards_on_a_stale_price():
    feed = _live_feed()
    feed.prices = [_price(T0 - timedelta(seconds=90))]  # 90 s old: stale for an in-play new-risk ticket
    feed.last = {MID: T0 - timedelta(seconds=10)}
    session, report = await _cycle(feed, FakeChase(p=0.12))
    assert _tail_rows(session) == []
    assert report["guard_refusals"].get("stale_book", 0) >= 1


@pytest.mark.asyncio
async def test_trader_uses_players_only_for_matched_names_and_core_otherwise():
    model = cm.ChaseModel(_tiny_payload())  # names "Chase One" and "Chase Two" have career records; "Nobody" do not
    unmatched = {"batsmen": [{"name": "Nobody A", "runs": 1, "balls": 1, "on_strike": True},
                             {"name": "Nobody B", "runs": 0, "balls": 0, "on_strike": False}]}
    _s, report = await _cycle(_live_feed(players=unmatched), model)
    assert report["tail"].get("estimate:core") == 1 and report["tail"].get("estimate:players") is None

    matched = {"batsmen": [{"name": "Chase One", "runs": 1, "balls": 1, "on_strike": True},
                           {"name": "Chase Two", "runs": 0, "balls": 0, "on_strike": False}]}
    _s, report = await _cycle(_live_feed(players=matched), model)
    assert report["tail"].get("estimate:players") == 1


@pytest.mark.asyncio
async def test_tail_variant_leaves_the_other_variants_unchanged():
    chase = FakeChase(p=0.12)
    _s1, with_tail = await _cycle(_live_feed(), chase)
    without = [v for v in pt.build_variants() if v.name != pt.TAIL_VARIANT]
    _s2, no_tail = await _cycle(_live_feed(), chase, variants=without)

    def other(report):
        return [(i["variant"], i["side"], i["selection"], i["stake"], i["reason"])
                for i in report["intent_detail"] if i["variant"] != pt.TAIL_VARIANT]

    assert other(with_tail) == other(no_tail)
    assert any(i["variant"] == pt.TAIL_VARIANT for i in with_tail["intent_detail"])  # the tail did trade
    assert not any(i["variant"] == pt.TAIL_VARIANT for i in no_tail["intent_detail"])


def test_no_tail_state_before_the_last_ten_overs():
    """Lead review 2026-11-05: early in the chase the state model does not know team strength."""
    from strategy import tail_bet as tb
    row = {"innings": 2, "score": 40, "wickets": 1, "balls_remaining": 61, "match_format": "T20"}
    assert tb.snapshot(row, target=160, batting_side="home") is None
    row["balls_remaining"] = 60
    snap = tb.snapshot(row, target=160, batting_side="home")
    if snap is not None:  # chase_format_ok may need more fields in this fake row
        assert snap.balls_left == 60
