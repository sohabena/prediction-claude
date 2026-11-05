"""S62: bet selection and uncertainty-aware staking (strategy.staking) and its optional hook in ValueStrategy."""
from __future__ import annotations

import pytest

from strategy.staking import edge, edge_lower, select, shrink_factor, stake
from strategy.value_policy import ValueStrategy

MIN_EDGE = {"pre_match": 0.02, "women": 0.04}


# ----------------------------------------------------------------------------- edge maths
def test_back_edge_hand_computed():
    # back 2.10 at p = 0.50: 0.50 * 2.10 - 1 = 0.05 per unit stake
    assert edge("BACK", 0.50, 2.10) == pytest.approx(0.05)
    # fair price gives no edge
    assert edge("BACK", 0.50, 2.00) == pytest.approx(0.0)


def test_lay_edge_worked_example():
    # Lay at 2.20 when p = 0.40 (the selection wins 40% of the time).
    # Selection loses (0.60): layer keeps the backer's stake, +1.00.
    # Selection wins (0.40): layer pays (2.20 - 1) = 1.20, -1.20.
    # edge = 0.60 * 1.00 - 0.40 * 1.20 = 0.12 per unit of backer's stake.
    assert edge("LAY", 0.40, 2.20) == pytest.approx(0.12)
    outcome_table = 0.60 * 1.00 + 0.40 * (-1.20)
    assert edge("LAY", 0.40, 2.20) == pytest.approx(outcome_table)


def test_lay_edge_zero_at_fair_and_sign_flips_at_break_even():
    assert edge("LAY", 0.50, 2.00) == pytest.approx(0.0)
    break_even = 1.0 / 2.20
    assert edge("LAY", break_even - 1e-6, 2.20) > 0.0
    assert edge("LAY", break_even + 1e-6, 2.20) < 0.0


def test_lay_and_back_are_mirror_images_at_zero_commission():
    # Backing the selection at o and laying it at o have opposite expected values only when prices are equal and
    # there is no spread: back at o gives p*o - 1, lay at o gives 1 - p*o.
    for p, o in [(0.3, 3.5), (0.55, 1.9), (0.8, 1.25)]:
        assert edge("BACK", p, o) == pytest.approx(-edge("LAY", p, o))


def test_edge_rejects_bad_inputs():
    with pytest.raises(ValueError):
        edge("BACK", 0.5, 1.0)  # price must be > 1
    with pytest.raises(ValueError):
        edge("BACK", 1.2, 2.0)  # probability out of range
    with pytest.raises(ValueError):
        edge("HOLD", 0.5, 2.0)


def test_edge_lower_evaluates_pessimistic_probability():
    # BACK: pessimistic p = 0.50 - 0.05 = 0.45 -> 0.45 * 2.10 - 1 = -0.055
    assert edge_lower("BACK", 0.50, 0.05, 2.10) == pytest.approx(-0.055)
    # LAY: pessimistic p = 0.40 + 0.05 = 0.45 -> (1 - 0.45) - 0.45 * 1.20 = 0.01
    assert edge_lower("LAY", 0.40, 0.05, 2.20) == pytest.approx(0.01)
    # z = 0 gives the mean edge
    assert edge_lower("LAY", 0.40, 0.05, 2.20, z=0.0) == pytest.approx(0.12)


# ----------------------------------------------------------------------------- selection
def _book(**prices):
    return dict(prices)


def test_no_edge_no_bet():
    # Overround book: back 1.90 / lay 2.10 on a 50/50 selection has negative edge on both sides.
    book = _book(back_home=1.90, lay_home=2.10, back_away=1.90, lay_away=2.10)
    assert select(0.50, 0.01, book, segment="pre_match", min_edge_by_segment=MIN_EDGE) is None


def test_edge_gone_at_the_60_second_price_gives_no_bet():
    p_mean, p_sd = 0.50, 0.02
    at_signal = _book(back_home=2.30, lay_home=2.40)
    # signal time: back home at 2.30 -> edge_lower = 0.48 * 2.30 - 1 = 0.104, clears 0.02
    assert select(p_mean, p_sd, at_signal, segment="pre_match", min_edge_by_segment=MIN_EDGE) is not None
    # 60 s later the price has moved to 2.05: edge_lower = 0.48 * 2.05 - 1 = -0.016, no bet
    after_latency = _book(back_home=2.05, lay_home=2.15)
    assert select(p_mean, p_sd, after_latency, segment="pre_match", min_edge_by_segment=MIN_EDGE) is None


def test_select_returns_lay_when_lay_has_the_best_lower_bound():
    # p_home = 0.40 so away wins 0.60; lay home at 2.20 has edge 0.12 at the mean (lower bound 0.076).
    # Away prices are set so that neither away side has an edge.
    book = _book(back_home=2.00, lay_home=2.20, back_away=1.50, lay_away=3.00)
    sel = select(0.40, 0.02, book, segment="pre_match", min_edge_by_segment=MIN_EDGE)
    assert sel is not None
    assert (sel.side, sel.selection, sel.price) == ("LAY", "home", 2.20)
    assert sel.edge == pytest.approx(0.12)
    assert sel.edge_lower == pytest.approx(edge_lower("LAY", 0.40, 0.02, 2.20))


def test_segment_minimum_is_applied_strictly():
    # back home at 2.20 with p 0.50 and p_sd 0: edge = 0.10; a minimum of 0.11 rejects it, 0.09 accepts it
    book = _book(back_home=2.20)
    assert select(0.50, 0.0, book, segment="pre_match", min_edge_by_segment={"pre_match": 0.11}) is None
    assert select(0.50, 0.0, book, segment="pre_match", min_edge_by_segment={"pre_match": 0.09}) is not None


def test_stricter_segment_rejects_what_looser_accepts():
    book = _book(back_home=2.00)  # edge at p 0.515 is 0.03: above the 0.02 minimum, below the 0.04 minimum
    assert select(0.515, 0.0, book, segment="pre_match", min_edge_by_segment=MIN_EDGE) is not None
    assert select(0.515, 0.0, book, segment="women", min_edge_by_segment=MIN_EDGE) is None


def test_select_rejects_unknown_segment_and_invalid_prices_are_skipped():
    with pytest.raises(ValueError):
        select(0.5, 0.01, {}, segment="unknown", min_edge_by_segment=MIN_EDGE)
    # an invalid (<= 1.0 or missing) price is ignored rather than raising
    book = {"back_home": 1.0, "lay_home": None, "back_away": 2.6}
    sel = select(0.30, 0.0, book, segment="pre_match", min_edge_by_segment=MIN_EDGE)
    assert sel is not None and (sel.side, sel.selection) == ("BACK", "away")


# ----------------------------------------------------------------------------- uncertainty
def test_shrink_factor_values():
    # edge_prob = 0.60 - 0.50 = 0.10; p_sd = 0.05 -> 1 / (1 + 0.25) = 0.8
    assert shrink_factor(0.60, 0.05, 2.0, "BACK") == pytest.approx(0.8)
    assert shrink_factor(0.60, 0.0, 2.0, "BACK") == pytest.approx(1.0)
    # no edge on the mean -> no stake
    assert shrink_factor(0.40, 0.05, 2.0, "BACK") == 0.0


def test_larger_uncertainty_gives_smaller_stake():
    kwargs = dict(kelly_fraction=0.25, max_match_frac=1.0, max_day_frac=1.0)
    tight = stake(0.60, 0.01, 2.0, "BACK", 1_000_000, **kwargs)
    loose = stake(0.60, 0.10, 2.0, "BACK", 1_000_000, **kwargs)
    assert tight > loose > 0.0


def test_no_edge_no_stake_and_zero_bankroll():
    assert stake(0.50, 0.01, 1.90, "BACK", 10_000) == 0.0
    assert stake(0.60, 0.01, 2.0, "BACK", 0.0) == 0.0


def test_stake_zero_below_minimum_stake():
    # 0.25 * 0.20 * 100 = 5 risk, below the 10 minimum
    assert stake(0.60, 0.0, 2.0, "BACK", 100, max_match_frac=1.0, max_day_frac=1.0) == 0.0


# ----------------------------------------------------------------------------- caps
def test_match_cap_binds_and_open_exposure_reduces_room():
    kwargs = dict(kelly_fraction=0.25, max_match_frac=0.02, max_day_frac=1.0)
    # p 0.90 at 2.0: kelly = 0.80; uncapped risk 0.25 * 0.80 * 10000 = 2000, match cap 200 -> stake 200
    assert stake(0.90, 0.0, 2.0, "BACK", 10_000, **kwargs) == 200.0
    # 150 already open on this match: room 50 -> stake 50
    assert stake(0.90, 0.0, 2.0, "BACK", 10_000, open_match_exposure=150.0, **kwargs) == 50.0
    # cap already used up -> 0
    assert stake(0.90, 0.0, 2.0, "BACK", 10_000, open_match_exposure=250.0, **kwargs) == 0.0


def test_day_cap_binds():
    kwargs = dict(kelly_fraction=0.25, max_match_frac=1.0, max_day_frac=0.01)
    assert stake(0.90, 0.0, 2.0, "BACK", 10_000, **kwargs) == 100.0  # day cap 100
    assert stake(0.90, 0.0, 2.0, "BACK", 10_000, open_day_exposure=60.0, **kwargs) == 40.0


def test_lay_stake_hand_computed():
    # lay 2.20 at p 0.40: edge 0.12, kelly as liability = 0.12
    # risk = 0.25 * 0.12 * 10000 = 300 liability -> backer's stake 300 / 1.20 = 250 -> round down to 200
    s = stake(0.40, 0.0, 2.20, "LAY", 10_000, kelly_fraction=0.25, max_match_frac=1.0, max_day_frac=1.0)
    assert s == 200.0
    assert s * (2.20 - 1.0) == pytest.approx(240.0)  # liability of the rounded stake, within the 300 risk


def test_lay_liability_cap_applies_to_liability_not_stake():
    # match cap 200 liability: stake = 200 / (3.0 - 1) = 100 backer's stake
    s = stake(0.20, 0.0, 3.0, "LAY", 10_000, kelly_fraction=1.0, max_match_frac=0.02, max_day_frac=1.0)
    assert s == 100.0


# ----------------------------------------------------------------------------- ValueStrategy hook
class _FixedFair:
    version = "stub"

    def __init__(self, p_home: float) -> None:
        self.p_home = p_home

    def predict_home(self, tick):
        return self.p_home


def test_value_strategy_hook_shrinks_stake_only_when_p_sd_supplied():
    tick_free = ValueStrategy(_FixedFair(0.6))
    tick_zero = ValueStrategy(_FixedFair(0.6), p_sd=0.0)
    tick_sd = ValueStrategy(_FixedFair(0.6), p_sd=0.1)
    args = ("BACK", 2.0, 0.0, 0.1, 100_000.0)
    base = tick_free._stake(*args, p=0.6)
    assert tick_zero._stake(*args, p=0.6) == base
    assert tick_sd._stake(*args, p=0.6) < base
    # 0.25 * 0.1 * 100000 = 2500 -> round 2000; shrink 0.5 -> 1250 -> round 1000
    assert base == 2000.0
    assert tick_sd._stake(*args, p=0.6) == 1000.0


def test_value_strategy_rejects_negative_p_sd():
    with pytest.raises(ValueError):
        ValueStrategy(_FixedFair(0.6), p_sd=-0.1)
