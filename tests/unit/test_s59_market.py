"""S59: market probability module. Pure numerics; no database, network or fixtures."""

from __future__ import annotations

import math

import numpy as np
import pytest

from training import market as mk


# ---------------------------------------------------------------------------
# implied / takeable / overround
# ---------------------------------------------------------------------------

def test_implied_mid_is_mean_of_back_and_lay_implied():
    assert mk.implied(2.0, 2.2) == pytest.approx(0.5 * (0.5 + 1 / 2.2))


def test_implied_missing_side_uses_the_other():
    assert mk.implied(None, 4.0) == pytest.approx(0.25)
    assert mk.implied(2.5, None) == pytest.approx(0.4)


def test_implied_both_missing_is_none():
    assert mk.implied(None, None) is None


@pytest.mark.parametrize("bad", [1.0, 0.5, 0.0, -2.0, float("nan"), float("inf")])
def test_implied_treats_unusable_prices_as_missing(bad):
    assert mk.implied(bad, 4.0) == pytest.approx(0.25)
    assert mk.implied(bad, bad) is None


def test_takeable_back_fills_at_back_and_lay_fills_at_lay():
    # LotusBook: the Back column is what a backer gets (the lower price), the Lay column what a layer gets
    assert mk.takeable("BACK", 2.0, 2.4) == pytest.approx(1 / 2.0)
    assert mk.takeable("LAY", 2.0, 2.4) == pytest.approx(1 / 2.4)


def test_takeable_has_no_fallback_when_the_needed_side_is_missing():
    assert mk.takeable("BACK", None, 2.4) is None
    assert mk.takeable("LAY", 2.0, None) is None


def test_overround_is_booksum_minus_one():
    assert mk.overround(0.52, 0.5) == pytest.approx(0.02)
    assert mk.overround(0.5, 0.5) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# de-vig methods
# ---------------------------------------------------------------------------

FAIR_BOOKS = [(0.5, 0.5), (0.2, 0.8), (0.7, 0.3), (0.9, 0.1), (0.33, 0.67)]


@pytest.mark.parametrize("ph,pa", FAIR_BOOKS)
def test_three_methods_agree_exactly_on_a_fair_book(ph, pa):
    for method in (mk.devig_multiplicative, mk.devig_power, mk.devig_shin):
        out_h, out_a = method(ph, pa)
        assert out_h == pytest.approx(ph, abs=1e-12)
        assert out_a == pytest.approx(pa, abs=1e-12)


@pytest.mark.parametrize("ph,pa", [(0.5025, 0.5), (0.7, 0.305), (0.32, 0.6805)])
def test_three_methods_agree_closely_on_a_near_fair_book(ph, pa):
    results = [m(ph, pa)[0] for m in (mk.devig_multiplicative, mk.devig_power, mk.devig_shin)]
    assert max(results) - min(results) < 2e-3


@pytest.mark.parametrize("method", [mk.devig_multiplicative, mk.devig_power, mk.devig_shin])
def test_every_method_sums_to_one_on_a_grid(method):
    grid = np.linspace(0.02, 0.98, 25)
    for ph in grid:
        for pa in grid:
            if ph + pa < 1.0:  # the grid covers books with a margin (or fair); a sub-unit booksum is not a real book
                continue
            h, a = method(float(ph), float(pa))
            assert h + a == pytest.approx(1.0, abs=1e-9)
            assert 0.0 < h < 1.0 and 0.0 < a < 1.0


def test_shin_known_example():
    """Raw book (1/1.2 + 0.01, 1/4.5 + 0.01). Closed form gives 29/36 for the favourite (1.2 shorter than 4.5)."""
    ph, pa = 1 / 1.2 + 0.01, 1 / 4.5 + 0.01
    h, a = mk.devig_shin(ph, pa)
    assert h == pytest.approx(29 / 36, abs=1e-9)
    assert a == pytest.approx(7 / 36, abs=1e-9)


def test_shin_matches_an_independent_bisection():
    ph, pa = 1 / 1.9 + 0.02, 1 / 2.1 + 0.02
    s = ph + pa

    def p(z: float, pi: float) -> float:
        return (math.sqrt(z * z + 4 * (1 - z) * pi * pi / s) - z) / (2 * (1 - z))

    lo, hi = 0.0, 0.999999
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if p(mid, ph) + p(mid, pa) > 1.0:
            lo = mid
        else:
            hi = mid
    z = 0.5 * (lo + hi)
    h, a = mk.devig_shin(ph, pa)
    assert h == pytest.approx(p(z, ph), abs=1e-9)
    assert a == pytest.approx(p(z, pa), abs=1e-9)


def test_power_on_an_asymmetric_book_solves_the_exponent():
    ph, pa = 1 / 1.2 + 0.01, 1 / 4.5 + 0.01
    h, a = mk.devig_power(ph, pa)
    assert h + a == pytest.approx(1.0, abs=1e-9)
    # The exponent k satisfies ph**k + pa**k == 1 on the raw probabilities.
    k = math.log(h) / math.log(ph)
    assert ph ** k + pa ** k == pytest.approx(1.0, abs=1e-9)
    assert math.log(a) / math.log(pa) == pytest.approx(k, abs=1e-9)


def test_power_takes_more_from_the_longshot_than_multiplicative():
    """The favourite-longshot shape: power leaves the favourite with more than proportional de-vig does."""
    ph, pa = 1 / 1.2 + 0.01, 1 / 4.5 + 0.01
    h_pow, _ = mk.devig_power(ph, pa)
    h_mul, _ = mk.devig_multiplicative(ph, pa)
    assert h_pow > h_mul
    assert mk.DEFAULT_DEVIG == "power"


def test_multiplicative_is_proportional():
    h, a = mk.devig_multiplicative(0.6, 0.5)
    assert h / a == pytest.approx(0.6 / 0.5)


@pytest.mark.parametrize("ph,pa", [(0.0, 0.5), (0.5, -0.1), (float("nan"), 0.5)])
def test_devig_rejects_non_positive_or_non_finite_raw_probabilities(ph, pa):
    with pytest.raises(ValueError):
        mk.devig_power(ph, pa)


# ---------------------------------------------------------------------------
# market_prob_home
# ---------------------------------------------------------------------------

BOOK = {"back_home": 1.8, "lay_home": 1.84, "back_away": 2.2, "lay_away": 2.26}


def test_market_prob_home_uses_mid_then_power_by_default():
    ph = 0.5 * (1 / 1.8 + 1 / 1.84)
    pa = 0.5 * (1 / 2.2 + 1 / 2.26)
    assert mk.market_prob_home(BOOK) == pytest.approx(mk.devig_power(ph, pa)[0])


@pytest.mark.parametrize("method", ["multiplicative", "power", "shin"])
def test_market_prob_home_methods_are_selectable(method):
    p = mk.market_prob_home(BOOK, method=method)
    assert 0.0 < p < 1.0


def test_market_prob_home_uses_whatever_prices_are_present():
    partial = {"back_home": 1.8, "lay_home": None, "back_away": 2.2, "lay_away": None}
    expected = mk.market_prob_home({"back_home": 1.8, "lay_home": 1.8, "back_away": 2.2, "lay_away": 2.2})
    assert mk.market_prob_home(partial) == pytest.approx(expected)


def test_market_prob_home_is_none_when_a_runner_has_no_usable_price():
    assert mk.market_prob_home({"back_home": 1.8, "lay_home": 1.84}) is None
    assert mk.market_prob_home({"back_home": None, "lay_home": None,
                                "back_away": 2.2, "lay_away": 2.26}) is None
    assert mk.market_prob_home({"back_home": 1.0, "lay_home": 1.0,
                                "back_away": 2.2, "lay_away": 2.26}) is None


def test_market_prob_home_rejects_unknown_method():
    with pytest.raises(ValueError):
        mk.market_prob_home(BOOK, method="bogus")


# ---------------------------------------------------------------------------
# margin_paid
# ---------------------------------------------------------------------------

def test_margin_is_zero_at_a_fair_price():
    assert mk.margin_paid("BACK", 2.0, 0.5) == pytest.approx(0.0)
    assert mk.margin_paid("LAY", 2.0, 0.5) == pytest.approx(0.0)


def test_back_margin_when_priced_below_fair():
    assert mk.margin_paid("BACK", 1.9, 0.5) == pytest.approx(0.05)


def test_lay_margin_when_priced_above_fair():
    assert mk.margin_paid("LAY", 2.1, 0.5) == pytest.approx(0.05)


def test_margin_sign_flips_when_the_price_is_favourable():
    assert mk.margin_paid("BACK", 2.1, 0.5) < 0
    assert mk.margin_paid("LAY", 1.9, 0.5) < 0


@pytest.mark.parametrize("p,price", [(0.0, 2.0), (1.0, 2.0), (0.5, 1.0)])
def test_margin_rejects_invalid_inputs(p, price):
    with pytest.raises(ValueError):
        mk.margin_paid("BACK", price, p)


# ---------------------------------------------------------------------------
# wilson and favourite-longshot calibration
# ---------------------------------------------------------------------------

def test_wilson_interval_known_value():
    lo, hi = mk.wilson_interval(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-4)
    assert hi == pytest.approx(0.7634, abs=1e-4)


def test_wilson_interval_empty_is_nan():
    lo, hi = mk.wilson_interval(0, 0)
    assert math.isnan(lo) and math.isnan(hi)


def _seeded_market(n: int, a0: float, b0: float, seed: int = 59):
    rng = np.random.default_rng(seed)
    p_mkt = rng.uniform(0.05, 0.95, size=n)
    logit = a0 + b0 * np.log(p_mkt / (1 - p_mkt))
    p_true = 1 / (1 + np.exp(-logit))
    y = (rng.uniform(size=n) < p_true).astype(float)
    return p_mkt, y


def test_fitted_line_recovers_a_and_b_from_seeded_data():
    a0, b0 = 0.3, 1.15
    p_mkt, y = _seeded_market(20000, a0, b0)
    fit = mk.fit_fav_longshot(p_mkt, y)
    assert fit.n == 20000
    assert abs(fit.a - a0) < 4 * fit.se_a
    assert abs(fit.b - b0) < 4 * fit.se_b
    assert fit.se_a > 0 and fit.se_b > 0
    assert abs(fit.b - b0) < 0.1


def test_calibrated_market_fits_identity():
    p_mkt, y = _seeded_market(20000, 0.0, 1.0, seed=7)
    fit = mk.fit_fav_longshot(p_mkt, y)
    assert abs(fit.a) < 4 * fit.se_a
    assert abs(fit.b - 1.0) < 4 * fit.se_b


def test_apply_fav_longshot_identity_and_scalar_and_array():
    identity = mk.FavLongshotFit(a=0.0, b=1.0, se_a=0.0, se_b=0.0, n=2)
    out = mk.apply_fav_longshot(np.array([0.2, 0.7]), identity)
    assert out == pytest.approx([0.2, 0.7])
    scalar = mk.apply_fav_longshot(0.4, identity)
    assert np.shape(scalar) == () or np.size(scalar) == 1
    assert float(np.ravel(scalar)[0]) == pytest.approx(0.4)


def test_bins_report_counts_and_contain_the_observed_rate():
    p_mkt, y = _seeded_market(5000, 0.0, 1.0, seed=11)
    fit = mk.fit_fav_longshot(p_mkt, y, n_bins=10)
    assert len(fit.bins) == 10
    assert sum(b.n for b in fit.bins) == 5000
    for b in fit.bins:
        if b.n == 0:
            continue
        assert b.lo <= b.mean_p_market <= b.hi
        assert b.ci_low <= b.observed_rate <= b.ci_high


def test_fit_rejects_bad_inputs():
    with pytest.raises(ValueError):
        mk.fit_fav_longshot([0.5, 0.6], [1, 0, 1])
    with pytest.raises(ValueError):
        mk.fit_fav_longshot([0.5, 1.0], [1, 0])
    with pytest.raises(ValueError):
        mk.fit_fav_longshot([0.5, 0.6], [1, 2])
    with pytest.raises(ValueError):
        mk.fit_fav_longshot([0.5, 0.6, 0.7], [1, 1, 1])
