"""S85: placebo controls. Reproducible random and favourite decisions at the reference's times and stakes, guards and
the cap, exclusion from primary and ranking, the excess-CLV computation (match-clustered) and the plain sentence.

Fakes only (the S72 cycle fakes and synthetic ledgers). No database, no network, no real clock.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from shared.metrics import cluster_bootstrap
from strategy import model_edge
from strategy.model_edge import Book, Variant
from tests.unit import test_s72_paper_trader as s72
from virtual_trading import paper_review as rv
from virtual_trading import paper_trader as pt

UTC = timezone.utc
T0 = s72.T0
MID = s72.MID
MIN = s72.MIN
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
BANK = 5_000_000.0
PLACEBO_NAMES = ("placebo_random", "placebo_favourite")


async def _run(session, feed, variants, p_model=0.6, reference="w05_e03", now=T0):
    return await pt.run_cycle(
        session, feed, s72.FakeProbability(p_model=p_model), variants, now=now, reference=reference
    )


def _rows(session, name):
    return [r for r in session.rows if r.variant == name]


def _clv_bet(variant, match_id, clv, status="settled"):
    return {"variant": variant, "match_id": match_id, "status": status, "clv": clv, "stake": 1_000.0, "pnl": 0.0}


def _bet(variant, match_id="m1", clv=0.01, status="settled", decided_at=NOW - timedelta(days=1), in_play=False):
    """A row shaped like paper_bets, as S73's helper, for the review."""
    return {
        "variant": variant, "match_id": match_id, "status": status, "side": "BACK", "selection": "home",
        "price_filled": 2.0, "stake": 1_000.0, "clv": clv,
        "pnl": 0.0 if status in ("settled", "void") else None,
        "result": "home" if status in ("settled", "void") else None,
        "p_used": 0.5, "settled_at": decided_at + timedelta(hours=1) if status == "settled" else None,
        "decided_at": decided_at, "team": "Alpha", "in_play": in_play,
    }


# ----------------------------------------------------------------------------- the choice (pure)
def test_placebo_random_choice_is_reproducible_and_hash_based():
    first = pt.placebo_random_choice(MID, T0, "placebo_random")
    assert first == pt.placebo_random_choice(MID, T0, "placebo_random")
    # the bits are those of a SHA-256 over the match, the decision time and the name: not Python's salted hash()
    digest = hashlib.sha256(f"{MID}|{T0.isoformat()}|placebo_random".encode("utf-8")).digest()
    side = "BACK" if digest[0] & 1 == 0 else "LAY"
    selection = "home" if digest[0] & 2 == 0 else "away"
    assert first == (side, selection)


def test_placebo_random_choice_varies_over_matches_times_and_names():
    seen = {
        pt.placebo_random_choice(f"m{i}", T0 + timedelta(seconds=15 * k), "placebo_random")
        for i in range(10) for k in range(10)
    }
    assert seen == {("BACK", "home"), ("BACK", "away"), ("LAY", "home"), ("LAY", "away")}
    assert any(
        pt.placebo_random_choice(MID, T0 + timedelta(seconds=15 * k), "placebo_random")
        != pt.placebo_random_choice(MID, T0 + timedelta(seconds=15 * k), "placebo_other")
        for k in range(20)
    )


def test_favourite_is_the_lower_back_price_and_home_on_a_tie():
    assert model_edge.favourite({"back_home": 2.2, "back_away": 1.8}) == "away"
    assert model_edge.favourite({"back_home": 1.8, "back_away": 2.2}) == "home"
    assert model_edge.favourite({"back_home": 2.0, "back_away": 2.0}) == "home"
    assert model_edge.favourite({"back_home": 2.0}) is None


def test_variant_rejects_an_unknown_placebo_kind():
    with pytest.raises(ValueError):
        Variant("bad", w=0.0, min_edge=0.03, placebo="coin")


def test_build_variants_adds_two_placebos_that_are_controls_not_strategies():
    variants = {v.name: v for v in pt.build_variants()}
    for name, kind in (("placebo_random", "random"), ("placebo_favourite", "favourite")):
        assert variants[name].placebo == kind and variants[name].market_only is True
    # the model grid and the market-only control are unchanged
    assert all(variants[n].placebo == "" for n in ("w03_e03", "w07_e06", "market_only"))


# ----------------------------------------------------------------------------- placebo_ticket: sizing and guards
def test_placebo_enter_is_capped_per_ticket_by_max_trade_risk():
    variant = Variant("placebo_random", w=0.0, min_edge=0.03, market_only=True, placebo="random")
    intent = model_edge.placebo_ticket(
        variant, 0.5, {"back_home": 2.2, "lay_home": 2.3, "back_away": 1.8, "lay_away": 1.9}, Book(),
        side="BACK", selection="home", stake=30_000.0, in_play=False,
    )
    assert intent.reason == "enter" and intent.stake == pytest.approx(25_000.0)  # max_trade_risk for a BACK
    lay = model_edge.placebo_ticket(
        variant, 0.5, {"back_home": 2.2, "lay_home": 2.3, "back_away": 1.8, "lay_away": 1.9}, Book(),
        side="LAY", selection="home", stake=30_000.0, in_play=False,
    )
    assert lay.stake == pytest.approx(25_000.0 / 1.3)  # a LAY's risk is its liability at the limit (price 2.3)


def test_placebo_cap_shrinks_the_stake_to_the_room_left():
    variant = Variant("placebo_random", w=0.0, min_edge=0.03, market_only=True, placebo="random")
    book = {"back_home": 2.2, "lay_home": 2.3, "back_away": 1.8, "lay_away": 1.9}
    near_cap = Book(home_pnl=-95_000.0, away_pnl=0.0)  # BACK away moves home by -1 per unit: room 5,000
    # in play the cap is the full 100,000 (before the off it is the pre-match share, 50,000)
    intent = model_edge.placebo_ticket(
        variant, 0.5, book, near_cap, side="BACK", selection="away", stake=20_000.0, in_play=True,
    )
    assert intent.stake == pytest.approx(5_000.0)
    assert near_cap.home_pnl - intent.stake >= -variant.match_cap(True) - 1e-6
    no_room = Book(home_pnl=-99_500.0, away_pnl=0.0)  # room 500: below the minimum trade, so no ticket
    assert model_edge.placebo_ticket(
        variant, 0.5, book, no_room, side="BACK", selection="away", stake=20_000.0, in_play=True,
    ) is None


def test_suspended_placebo_opens_no_new_risk_but_reduces():
    variant = Variant("placebo_random", w=0.0, min_edge=0.03, market_only=True, placebo="random")
    book = {"back_home": 2.2, "lay_home": 2.3, "back_away": 1.8, "lay_away": 1.9}
    assert model_edge.placebo_ticket(
        variant, 0.5, book, Book(), side="BACK", selection="home", stake=5_000.0, in_play=False, new_risk=False,
    ) is None
    long_home = Book(home_pnl=5_000.0, away_pnl=-5_000.0)  # balance +10,000: a BACK away reduces it
    reduce = model_edge.placebo_ticket(
        variant, 0.5, book, long_home, side="BACK", selection="away", stake=9_000.0, in_play=False, new_risk=False,
    )
    assert reduce.reason == "reduce"
    assert reduce.stake == pytest.approx(10_000.0 / 1.8)  # cut to the close-out part: never a flip


# ----------------------------------------------------------------------------- run_cycle: timing, stakes, guards
@pytest.mark.asyncio
async def test_placebo_random_has_the_reference_time_and_stake(monkeypatch):
    monkeypatch.setattr(pt, "PREMATCH_NEW_RISK", True)  # S106: pre-match entry is off by default
    session = s72.FakeSession()
    feed = s72.FakeFeed(registry=[s72._registry()], prices=[s72._price(T0 - MIN)], last={MID: T0})
    report = await _run(session, feed, pt.build_variants())
    (ref,) = _rows(session, "w05_e03")
    (rnd,) = _rows(session, "placebo_random")
    assert rnd.decided_at == ref.decided_at == T0
    assert rnd.match_id == ref.match_id == MID
    assert rnd.stake == pytest.approx(ref.stake)
    assert rnd.source == "market_only" and rnd.p_model is None and rnd.in_play is False
    assert (rnd.side, rnd.selection) == pt.placebo_random_choice(MID, T0, "placebo_random")
    assert report["reference"] == "w05_e03"
    assert any(i.get("placebo") == "random" for i in report["intent_detail"])


@pytest.mark.asyncio
async def test_placebo_favourite_backs_the_favourite_with_the_reference_stake(monkeypatch):
    monkeypatch.setattr(pt, "PREMATCH_NEW_RISK", True)  # S106: pre-match entry is off by default
    session = s72.FakeSession()
    feed = s72.FakeFeed(registry=[s72._registry()], prices=[s72._price(T0 - MIN)], last={MID: T0})
    await _run(session, feed, pt.build_variants())
    (ref,) = _rows(session, "w05_e03")
    (fav,) = _rows(session, "placebo_favourite")
    assert (fav.side, fav.selection) == ("BACK", "away")  # back away 1.8 is shorter than back home 2.2
    assert fav.decided_at == ref.decided_at == T0
    assert fav.stake == pytest.approx(ref.stake)
    assert fav.price_limit <= 1.8


@pytest.mark.asyncio
async def test_placebos_place_no_trade_when_the_reference_places_none():
    # min_edge 0.9 is never met on this book, so the reference places nothing and the placebos must not either
    idle = Variant("idle_ref", w=0.5, min_edge=0.9, max_match_loss=100_000.0)
    variants = [idle] + [v for v in pt.build_variants() if v.placebo]
    session = s72.FakeSession()
    feed = s72.FakeFeed(registry=[s72._registry()], prices=[s72._price(T0 - MIN)], last={MID: T0})
    report = await _run(session, feed, variants, reference="idle_ref")
    assert session.rows == []
    assert report["intents_by_variant"] == {}


@pytest.mark.asyncio
async def test_placebos_do_nothing_when_the_reference_is_not_in_the_run():
    variants = [v for v in pt.build_variants() if v.name in ("w03_e03", "placebo_random", "placebo_favourite")]
    session = s72.FakeSession()
    feed = s72.FakeFeed(registry=[s72._registry()], prices=[s72._price(T0 - MIN)], last={MID: T0})
    await _run(session, feed, variants, reference="w07_e06")
    assert _rows(session, "placebo_random") == [] and _rows(session, "placebo_favourite") == []


@pytest.mark.asyncio
async def test_placebo_decisions_reproduce_across_runs(monkeypatch):
    monkeypatch.setattr(pt, "PREMATCH_NEW_RISK", True)  # S106: pre-match entry is off by default
    placed = []
    for _ in range(2):
        session = s72.FakeSession()
        feed = s72.FakeFeed(registry=[s72._registry()], prices=[s72._price(T0 - MIN)], last={MID: T0})
        await _run(session, feed, pt.build_variants())
        placed.append(sorted(
            (r.variant, r.side, r.selection, round(r.stake, 6), r.price_limit, r.decided_at)
            for r in session.rows if r.variant in PLACEBO_NAMES
        ))
    assert placed[0] == placed[1] and len(placed[0]) == 2


@pytest.mark.asyncio
async def test_placebos_fill_and_settle_like_other_variants(monkeypatch):
    monkeypatch.setattr(pt, "PREMATCH_NEW_RISK", True)  # S106: pre-match entry is off by default
    variants = pt.build_variants()
    session = s72.FakeSession()
    feed = s72.FakeFeed(registry=[s72._registry()], prices=[s72._price(T0 - MIN)], last={MID: T0})
    await _run(session, feed, variants)
    feed.prices.append(s72._price(T0 + timedelta(seconds=30)))
    feed.last = {MID: T0 + timedelta(seconds=60)}
    await _run(session, feed, variants, now=T0 + timedelta(seconds=61))
    for name in PLACEBO_NAMES:
        (row,) = _rows(session, name)
        assert row.status == "filled" and row.filled_at == T0 + timedelta(seconds=60)
    feed.result_map = {MID: {"winner": "Alpha", "result_type": "win", "team_home": "Alpha", "team_away": "Beta"}}
    feed.registry_rows = [s72._registry() | {"settled": True}]
    feed.last = {}
    await _run(session, feed, variants, now=T0 + timedelta(hours=4))
    assert all(r.status == "settled" for name in PLACEBO_NAMES for r in _rows(session, name))


@pytest.mark.asyncio
async def test_placebos_hold_the_cap_across_cycles(monkeypatch):
    monkeypatch.setattr(pt, "PREMATCH_NEW_RISK", True)  # S106: pre-match entry is off by default
    variants = [
        Variant("ref", w=0.5, min_edge=0.03, max_match_loss=20_000.0),
        Variant("placebo_random", w=0.0, min_edge=0.03, max_match_loss=20_000.0, market_only=True, placebo="random"),
        Variant("placebo_favourite", w=0.0, min_edge=0.03, max_match_loss=20_000.0, market_only=True,
                placebo="favourite"),
    ]
    session = s72.FakeSession()
    feed = s72.FakeFeed(registry=[s72._registry()], last={})
    p_markets = [0.45, 0.5, 0.42, 0.55, 0.4, 0.6, 0.47, 0.38, 0.58, 0.44, 0.52, 0.41]
    p_model_seq = [0.9, 0.2, 0.85, 0.3, 0.9, 0.15, 0.8, 0.25, 0.9, 0.3, 0.7, 0.2]
    prob = s72.FakeProbability(p_model_seq=p_model_seq)
    for k, p_mkt in enumerate(p_markets):
        now = T0 + timedelta(seconds=61 * k)
        feed.prices.append(s72._price(now - timedelta(seconds=30), **s72._book_for(p_mkt)))
        feed.last = {s72.MID: now}
        await pt.run_cycle(session, feed, prob, variants, now=now, reference="ref")
        for variant in variants:
            book = s72._ledger_book(session, variant.name)
            if book is not None:
                assert book.worst() <= variant.max_match_loss + 1e-6, (variant.name, k, book.worst())
    assert _rows(session, "placebo_random") and _rows(session, "placebo_favourite")


# ----------------------------------------------------------------------------- primary, ranking, sentence
def test_placebo_is_never_primary_or_ranked():
    # the placebo has the higher CLV on every bet; without the exclusion it would be primary
    rows = [_bet("w05_e03", match_id=f"m{i % 20}", clv=0.02 if i % 2 else 0.01) for i in range(40)]
    rows += [_bet("placebo_random", match_id=f"m{i % 20}", clv=0.05) for i in range(40)]
    state = rv.review(rows, {"w05_e03": BANK, "placebo_random": BANK}, now=NOW, n_resamples=200, draws=500)
    assert state["primary"] == "w05_e03"
    assert state["ranking"]["eligible"] == ["w05_e03"]
    assert set(state["ranking"]["p_best"]) <= {"w05_e03"}
    flags = {s["variant"]: s["placebo"] for s in state["variants"]}
    assert flags == {"w05_e03": False, "placebo_random": True}


def test_placebo_alone_gives_no_primary():
    rows = [_bet("placebo_random", match_id=f"m{i % 20}", clv=0.05) for i in range(40)]
    state = rv.review(rows, {"placebo_random": BANK}, now=NOW, n_resamples=100, draws=100)
    assert state["primary"] is None and state["ranking"]["eligible"] == []


def test_reference_is_the_primary_in_the_state_else_the_default():
    assert rv.reference_name({"primary": "w07_e06"}) == "w07_e06"
    assert rv.reference_name({"primary": None}) == rv.DEFAULT_REFERENCE == "w05_e03"
    assert rv.reference_name({"primary": "placebo_random"}) == rv.DEFAULT_REFERENCE
    assert rv.reference_name({}) == "w05_e03"


def test_excess_is_the_per_match_difference_with_a_match_clustered_bootstrap():
    rows, expected, matches = [], [], []
    for i in range(12):
        mid = f"m{i:02d}"
        v_clvs, p_clvs = [0.02 + 0.001 * i, 0.03], [0.01]
        rows += [_clv_bet("w05_e03", mid, c) for c in v_clvs] + [_clv_bet("placebo_random", mid, c) for c in p_clvs]
        expected.append(sum(v_clvs) / len(v_clvs) - sum(p_clvs) / len(p_clvs))
        matches.append(mid)
    rows.append(_clv_bet("w05_e03", "only_variant", 0.5))  # no placebo CLV on this match: left out
    out = rv.excess_over_placebo(rows, ["w05_e03", "placebo_random"], n_resamples=500)
    e = out["w05_e03"]
    boot = cluster_bootstrap(expected, matches, n_boot=500, seed=rv.SEED)
    assert "placebo_random" not in out
    assert e["matches"] == 12 and e["bets"] == 24 and e["control_bets"] == 12
    assert e["estimate"] == pytest.approx(float(np.mean(expected)))
    assert e["ci_lo"] == pytest.approx(boot["ci_lower"]) and e["ci_hi"] == pytest.approx(boot["ci_upper"])
    assert e["above_zero"] is True and e["too_few_matches"] is False


def test_excess_flags_too_few_matches_and_handles_no_control():
    rows = [_clv_bet("w05_e03", f"m{i}", 0.02) for i in range(5)] + [_clv_bet("placebo_random", f"m{i}", 0.0)
                                                                      for i in range(5)]
    few = rv.excess_over_placebo(rows, ["w05_e03"], n_resamples=200)["w05_e03"]
    assert few["matches"] == 5 and few["too_few_matches"] is True
    none = rv.excess_over_placebo([_clv_bet("w05_e03", "m1", 0.02)], ["w05_e03"], n_resamples=200)["w05_e03"]
    assert none["matches"] == 0 and none["above_zero"] is False and np.isnan(none["estimate"])


def test_sentence_says_plainly_when_no_variant_beats_the_placebo():
    excess = {
        "w05_e03": {"above_zero": False, "too_few_matches": False},
        "placebo_favourite": {"above_zero": True, "too_few_matches": False},
    }
    sentence = rv.excess_sentence(excess)
    assert sentence.startswith("No variant's CLV beats placebo_random")
    assert "no excess-CLV confidence interval lies above zero on at least 10 matches" in sentence
    assert sentence.count(".") == 1  # one sentence; the favourite control is not a variant
    assert rv.excess_sentence({}).startswith("No variant's CLV beats placebo_random")


def test_sentence_names_the_variants_whose_excess_is_above_zero():
    excess = {
        "w05_e03": {"above_zero": True, "too_few_matches": False},
        "w07_e06": {"above_zero": False, "too_few_matches": False},
    }
    sentence = rv.excess_sentence(excess)
    assert "w05_e03" in sentence and "w07_e06" not in sentence
    assert "No variant" not in sentence


def test_an_interval_on_too_few_matches_does_not_count_as_above_zero():
    excess = {"w05_e03": {"above_zero": True, "too_few_matches": True}}
    assert rv.excess_sentence(excess).startswith("No variant's CLV beats placebo_random")


def test_review_state_carries_the_excess_block_and_the_plain_sentence():
    rows = [_bet("w05_e03", match_id=f"m{i % 20}", clv=0.02) for i in range(40)]
    rows += [_bet("placebo_random", match_id=f"m{i % 20}", clv=0.02) for i in range(40)]
    state = rv.review(rows, {"w05_e03": BANK, "placebo_random": BANK}, now=NOW, n_resamples=200, draws=200,
                      reference="w07_e06")
    block = state["placebo"]
    assert block["control"] == "placebo_random" and block["reference"] == "w07_e06"
    assert block["excess"]["w05_e03"]["matches"] == 20
    assert block["excess"]["w05_e03"]["estimate"] == pytest.approx(0.0)  # identical CLV to the control
    assert block["sentence"].startswith("No variant's CLV beats placebo_random")
