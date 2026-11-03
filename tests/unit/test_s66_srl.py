"""S66: SRL feasibility. Synthetic S58-shaped datasets with and without a planted bias. No database or network."""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

from scripts import srl_feasibility as sf
from training import market as mk

T0 = datetime(2026, 11, 3, 10, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# synthetic S58-shaped dataset
# ---------------------------------------------------------------------------

def _book(p_fair: float, margin: float, source: str = "listing") -> dict:
    """Nested S58 book with back == lay on each runner, so the mid implied is 1/price and booksum is 1 + margin."""
    back_home = 1.0 / (p_fair * (1.0 + margin))
    back_away = 1.0 / ((1.0 - p_fair) * (1.0 + margin))
    leg_home = {"back": back_home, "lay": back_home, "stale_s_back": 30, "stale_s_lay": 30,
                "source_back": source, "source_lay": source}
    leg_away = {"back": back_away, "lay": back_away, "stale_s_back": 30, "stale_s_lay": 30,
                "source_back": source, "source_lay": source}
    return {"home": leg_home, "away": leg_away}


def _empty_book() -> dict:
    empty = {"back": None, "lay": None, "stale_s_back": None, "stale_s_lay": None,
             "source_back": None, "source_lay": None}
    return {"home": dict(empty), "away": dict(empty)}


def _snap(match_id: str, label: str, t: datetime, start: datetime, book: dict, in_play: bool) -> dict:
    return {
        "snapshot_id": f"{match_id}:{label}",
        "match_id": match_id,
        "label": label,
        "t": t.isoformat(),
        "time_to_start_s": int(round((start - t).total_seconds())),
        "in_play": in_play,
        "has_prices": book["home"]["back"] is not None,
        "book": book,
        "book_60s": book,
        "book_10m": book,
    }


def synthetic_run(n_settled: int, *, bias: float = 0.0, margin: float = 0.04, seed: int = 1,
                  n_unsettled: int = 0, inplay: bool = True) -> dict:
    """``n_settled`` settled simulated matches whose outcomes follow ``p_fair + bias``.

    ``p_fair`` is the market's own probability, so any gap between the outcomes and the de-vigged closing price is
    the planted bias. ``n_unsettled`` simulated matches have no result and must be ignored by the calibration.
    """
    rng = np.random.default_rng(seed)
    matches, snapshots = [], []
    for i in range(n_settled + n_unsettled):
        mid = f"lb_sim_{i}"
        p_fair = float(rng.uniform(0.3, 0.7))
        settled = i < n_settled
        y = int(rng.random() < min(max(p_fair + bias, 0.01), 0.99)) if settled else None
        start = T0 + timedelta(hours=i)
        matches.append({"match_id": mid, "is_simulated": True, "home_won": y, "family": "t20",
                        "match_start_time": start.isoformat()})
        t60 = start - timedelta(hours=1)
        t0 = start - timedelta(microseconds=1)
        p_t60 = min(max(p_fair + 0.01, 0.05), 0.95)
        snapshots.append(_snap(mid, "T-60m", t60, start, _book(p_t60, margin), False))
        snapshots.append(_snap(mid, "T-0", t0, start, _book(p_fair, margin), False))
        if inplay:
            for k, src in enumerate(["listing", "match_page", "match_page", "listing"]):
                t = start + timedelta(minutes=2 * (k + 1))
                shift = 0.02 * (1 if k % 2 == 0 else -1)
                snapshots.append(_snap(mid, f"IP+{2 * (k + 1)}m", t, start,
                                       _book(min(max(p_fair + shift, 0.05), 0.95), margin, source=src), True))
    return {"matches": matches, "snapshots": snapshots, "manifest": {}, "run_dir": "synthetic"}


def _analyse(run: dict) -> dict:
    return sf.analyse(run["matches"], run["snapshots"])


# ---------------------------------------------------------------------------
# verdict rule (pure)
# ---------------------------------------------------------------------------

def test_verdict_small_sample_is_not_feasible_even_with_big_bias():
    v = sf.verdict(n_settled=40, bias=0.30, abs_bias_ci_low=0.25, mean_p=0.5, median_margin=0.04)
    assert v["verdict"] == "not feasible yet"
    assert v["further_matches_needed"] >= 260


def test_verdict_at_floor_with_bias_clearing_margin_is_feasible():
    v = sf.verdict(n_settled=300, bias=0.10, abs_bias_ci_low=0.06, mean_p=0.5, median_margin=0.04)
    assert v["verdict"] == "feasible"
    assert v["further_matches_needed"] == 0


def test_verdict_bias_ci_touching_margin_is_not_feasible():
    v = sf.verdict(n_settled=1000, bias=0.05, abs_bias_ci_low=0.04, mean_p=0.5, median_margin=0.04)
    assert v["verdict"] == "not feasible yet"


def test_verdict_no_estimate_when_bias_inside_margin_and_sample_large():
    v = sf.verdict(n_settled=500, bias=0.01, abs_bias_ci_low=0.0, mean_p=0.5, median_margin=0.04)
    assert v["verdict"] == "not feasible yet"
    assert v["effect_based_need"] is None
    assert v["further_matches_needed"] is None


def test_verdict_effect_based_need_grows_as_effect_shrinks():
    big = sf.verdict(n_settled=10, bias=0.20, abs_bias_ci_low=0.0, mean_p=0.5, median_margin=0.04)
    small = sf.verdict(n_settled=10, bias=0.06, abs_bias_ci_low=0.0, mean_p=0.5, median_margin=0.04)
    assert small["effect_based_need"] > big["effect_based_need"]


def test_verdict_missing_inputs_are_not_feasible():
    v = sf.verdict(n_settled=500, bias=None, abs_bias_ci_low=None, mean_p=None, median_margin=None)
    assert v["verdict"] == "not feasible yet"


# ---------------------------------------------------------------------------
# market maths on the nested book
# ---------------------------------------------------------------------------

def test_flat_book_maps_nested_legs():
    book = _book(0.6, 0.04)
    flat = sf.flat_book(book)
    assert flat["back_home"] == pytest.approx(book["home"]["back"])
    assert flat["lay_away"] == pytest.approx(book["away"]["lay"])


def test_raw_booksum_equals_one_plus_margin():
    assert sf.raw_booksum(_book(0.55, 0.07)) == pytest.approx(1.07)


def test_closing_market_prob_recovers_fair_probability_on_fair_book():
    # Multiplicative method is exact on a fair book; power is exact when the booksum is one.
    book = _book(0.55, 0.0)
    assert sf.closing_market_prob(book) == pytest.approx(0.55, abs=1e-9)


def test_unpriced_runner_gives_no_probability_or_booksum():
    book = _empty_book()
    assert sf.closing_market_prob(book) is None
    assert sf.raw_booksum(book) is None


# ---------------------------------------------------------------------------
# closing calibration
# ---------------------------------------------------------------------------

def test_unsettled_and_real_matches_are_excluded_from_calibration():
    run = synthetic_run(50, n_unsettled=20, seed=3)
    real = {"match_id": "lb_real", "is_simulated": False, "home_won": 1}
    run["matches"].append(real)
    result = _analyse(run)
    assert result["simulated_matches"] == 70
    assert result["settled_simulated"] == 50
    assert result["closing_sample"] == 50


def test_closing_row_without_price_is_counted_not_used():
    run = synthetic_run(10, seed=4)
    for snap in run["snapshots"]:
        if snap["match_id"] == "lb_sim_0" and snap["label"] == "T-0":
            snap["book"] = _empty_book()
    result = _analyse(run)
    assert result["settled_without_closing_price"] == 1
    assert result["closing_sample"] == 9


def test_wilson_bias_interval_contains_bias_point_estimate():
    run = synthetic_run(400, bias=0.05, seed=5)
    cal = _analyse(run)["calibration"]
    assert cal["bias_ci"][0] <= cal["bias"] <= cal["bias_ci"][1]
    assert cal["observed_ci"][0] <= cal["observed_rate"] <= cal["observed_ci"][1]


def test_fav_longshot_fit_is_reported_on_synthetic_sample():
    cal = _analyse(synthetic_run(300, seed=6))["calibration"]
    assert cal["fav_longshot"] is not None
    assert len(cal["fav_longshot"]["bins"]) == sf.N_BINS
    assert cal["fav_longshot"]["n"] == 300


def test_fav_longshot_failure_is_reported_not_raised():
    # A single settled match cannot be fitted; the report must say so rather than crash.
    cal = _analyse(synthetic_run(1, seed=7))["calibration"]
    assert cal["fav_longshot"] is None
    assert "not fitted" in cal["fav_longshot_note"]


# ---------------------------------------------------------------------------
# planted bias versus none, at and above the floor
# ---------------------------------------------------------------------------

def test_planted_bias_above_margin_on_enough_matches_is_feasible():
    run = synthetic_run(800, bias=0.10, margin=0.04, seed=11)
    result = _analyse(run)
    assert result["calibration"]["bias"] > 0.05
    assert result["verdict"]["verdict"] == "feasible"
    assert result["verdict"]["further_matches_needed"] == 0


def test_no_planted_bias_is_not_feasible_even_on_enough_matches():
    run = synthetic_run(800, bias=0.0, margin=0.005, seed=12)
    result = _analyse(run)
    assert result["verdict"]["abs_bias_ci_low"] == 0.0
    assert result["verdict"]["verdict"] == "not feasible yet"


def test_planted_bias_below_floor_is_not_feasible_and_names_shortfall():
    run = synthetic_run(120, bias=0.20, margin=0.04, seed=13)
    result = _analyse(run)
    assert result["verdict"]["verdict"] == "not feasible yet"
    assert result["verdict"]["further_matches_needed"] >= 300 - 120


def test_bias_inside_margin_on_large_sample_is_not_feasible():
    run = synthetic_run(800, bias=0.02, margin=0.08, seed=14)
    assert _analyse(run)["verdict"]["verdict"] == "not feasible yet"


# ---------------------------------------------------------------------------
# margin, movement and in-play source
# ---------------------------------------------------------------------------

def test_margin_distribution_matches_planted_margin():
    run = synthetic_run(100, margin=0.05, seed=15)
    q = _analyse(run)["margin_settled_closing"]
    assert q["median"] == pytest.approx(0.05, abs=1e-9)
    assert q["min"] == pytest.approx(0.05, abs=1e-9)


def test_movement_between_t60_and_t0_is_measured():
    run = synthetic_run(60, seed=16)
    mv = _analyse(run)["move_t60_to_t0"]
    assert mv["pairs"] == 60
    assert mv["abs_move"]["median"] > 0.0


def test_inplay_source_switches_are_detected():
    run = synthetic_run(30, seed=17)
    ip = _analyse(run)["inplay_source_gap"]
    assert set(ip["source_mix"]) == {"listing", "match_page"}
    assert ip["switch_jump"]["n"] > 0
    assert ip["steady_move"]["n"] > 0


def test_no_inplay_rows_gives_empty_source_gap():
    run = synthetic_run(20, seed=18, inplay=False)
    ip = _analyse(run)["inplay_source_gap"]
    assert ip["in_play_snapshots"] == 0
    assert ip["switch_jump"]["n"] == 0


# ---------------------------------------------------------------------------
# report and CLI
# ---------------------------------------------------------------------------

def test_report_states_the_verdict_rule_and_verdict():
    run = synthetic_run(800, bias=0.10, margin=0.04, seed=19)
    text = sf.render_report(_analyse(run), run, date(2026, 11, 3))
    assert "## Verdict" in text
    assert "**feasible**" in text
    assert "Verdict rule" in text
    assert "at least 300 settled SRL matches" in text


def test_cli_writes_report_to_reports_research(tmp_path, monkeypatch, capsys):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    run = synthetic_run(40, seed=20)
    with (run_dir / "matches.jsonl").open("w", encoding="utf-8") as fh:
        for m in run["matches"]:
            fh.write(json.dumps(m) + "\n")
    with (run_dir / "snapshots.jsonl").open("w", encoding="utf-8") as fh:
        for s in run["snapshots"]:
            fh.write(json.dumps(s) + "\n")
    (run_dir / "manifest.json").write_text(json.dumps({"git_sha": "abc"}), encoding="utf-8")
    monkeypatch.setattr(sf, "REPORT_DIR", tmp_path / "reports" / "research")

    assert sf.main(["--run-dir", str(run_dir), "--date", "20261103"]) == 0
    out = tmp_path / "reports" / "research" / "srl_20261103.md"
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    assert "not feasible yet" in text
    assert "SRL feasibility verdict: not feasible yet" in capsys.readouterr().out


def test_cli_no_report_writes_nothing(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    run = synthetic_run(5, seed=21)
    (run_dir / "matches.jsonl").write_text("\n".join(json.dumps(m) for m in run["matches"]), encoding="utf-8")
    (run_dir / "snapshots.jsonl").write_text("\n".join(json.dumps(s) for s in run["snapshots"]), encoding="utf-8")
    target = tmp_path / "reports" / "research"
    monkeypatch.setattr(sf, "REPORT_DIR", target)
    assert sf.main(["--run-dir", str(run_dir), "--no-report"]) == 0
    assert not target.exists()


def test_latest_run_dir_picks_newest_with_snapshots(tmp_path):
    for name in ("20261101-0100", "20261103-0522", "20261102-0200"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "snapshots.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "20261104-0000").mkdir()  # no snapshots file: skipped
    assert sf.latest_run_dir(tmp_path).name == "20261103-0522"


def test_real_dataset_run_is_not_feasible_yet(tmp_path, monkeypatch):
    real = sf.TRAINING_DIR / "20261103-0522"
    if not (real / "snapshots.jsonl").is_file():
        pytest.skip("real S58 dataset not present")
    monkeypatch.setattr(sf, "REPORT_DIR", tmp_path)
    assert sf.main(["--run-dir", str(real), "--date", "20261103"]) == 0
    text = (tmp_path / "srl_20261103.md").read_text(encoding="utf-8")
    assert "not feasible yet" in text
    assert "Settled simulated matches with a closing (T-0) price: 1" in text
