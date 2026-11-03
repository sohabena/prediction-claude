"""S88: the value-first scorecard. Headline order, the tradable-book count, R-progress counted in matches (not bets), the
in-play gate rendering, Tier-C absent from the report, and the dashboard rendering without any network.

Synthetic inputs only: plain dicts shaped like paper_bets rows and match facts. No database, no network, no real clock.
"""

from __future__ import annotations

import json
import socket
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts import paper_dashboard as dash
from scripts import paper_report as rep
from tests.unit.test_s73_paper_review import _bet
from virtual_trading import paper_review as rv
from virtual_trading import scorecard as sc

UTC = timezone.utc
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
BANK = 5_000_000.0


def _fact(match_id, *, result="win", winner="Alpha", completed=NOW - timedelta(days=1), tradable=True,
          prematch=True, start=NOW - timedelta(days=2), simulated=False, competition="Nepal Premier League"):
    return {
        "match_id": match_id, "team_home": "Alpha", "team_away": "Beta", "competition": competition,
        "is_simulated": simulated, "start_at": start, "result_type": result, "winner": winner,
        "completed_at": completed, "tradable": tradable, "prematch_price": prematch,
        "last_price_at": NOW - timedelta(minutes=5),
    }


def _state(rows, variants=("w05_e03", "w03_e03")):
    return rv.review(rows, {v: BANK for v in variants}, now=NOW, n_resamples=200, draws=200)


def _rows():
    rows = [_bet(variant="w05_e03", match_id=f"m{i % 3}", clv=0.02, in_play=(i % 2 == 0), status="settled")
            for i in range(40)]
    rows += [_bet(variant="w03_e03", match_id="m0", clv=0.01, in_play=False, status="filled")]
    return rows


# ----------------------------------------------------------------------------- headline order
def test_headline_sections_come_in_the_specified_order_and_before_tier_b():
    rows = _rows()
    state = _state(rows)
    counts = sc.match_counts([_fact("m1"), _fact("m2", result="tie", tradable=False, prematch=False)], NOW,
                             date(2026, 10, 25))
    gate = {"t20": {"inplay_model_allowed": False, "matches": 1, "logloss_diff": -0.01}}
    missing = [{"match_id": "gone", "team_home": "A", "team_away": "B", "competition": "C",
                "match_start_time": None, "last_tick": NOW - timedelta(hours=5), "age_hours": 5.0,
                "result_type": None, "has_result": False}]
    tier = sc.tier_b(rows, state, now=NOW, refusals=(3, Counter({"spread_too_wide": 2})))
    text = rep.render(state, rows, NOW, missing, counts=counts, gate=gate, tier=tier)

    order = [
        "## Headline (Tier A",
        "### Settled real matches with a tradable book",
        "### CLV excess over placebo_random, match-clustered",
        "### Results missing",
        "### Fill realism",
        "### Progress to R3",
        "### In-play model gate (S89)",
        "## Tier B: diagnostics",
        "## Supporting detail",
    ]
    positions = [text.index(h) for h in order]
    assert positions == sorted(positions), "headline sections must come in the S88 order, then Tier B"


def test_results_section_says_not_checked_when_the_list_is_absent():
    state = _state(_rows())
    text = rep.render(state, _rows(), NOW)
    assert "## Results missing" not in text
    assert "Result coverage (not checked in this render)" in text


# ----------------------------------------------------------------------------- tradable-book count
def test_tradable_count_counts_settled_real_matches_with_both_sides_and_the_last_week():
    facts = [
        _fact("m1"),                                   # settled, tradable, settled a day ago
        _fact("m2", result="tie", tradable=False, prematch=False),  # settled, no tradable book
        _fact("m3", simulated=True),                   # simulated: not a real match at all
        _fact("m4", result=None),                      # not settled
        _fact("m5", completed=NOW - timedelta(days=10), start=NOW - timedelta(days=11)),  # settled, old
    ]
    counts = sc.match_counts(facts, NOW, date(2026, 10, 25))
    assert counts["real_matches"] == 4
    assert counts["settled_real"] == 3
    assert counts["tradable_settled"] == 2
    assert counts["tradable_settled_recent"] == 1
    assert counts["recent_days"] == 7


def test_a_settled_match_with_no_time_at_all_is_counted_but_not_as_recent():
    fact = _fact("m1", completed=None, tradable=True)
    fact["last_price_at"] = None
    counts = sc.match_counts([fact], NOW, None)
    assert counts["tradable_settled"] == 1
    assert counts["tradable_settled_recent"] == 0  # no time to place it in the last 7 days


# ----------------------------------------------------------------------------- R-progress counts matches, not bets
def test_r_progress_counts_matches_and_not_bets():
    rows = _rows()  # 41 bets on 3 matches
    state = _state(rows)
    facts = [_fact("m1"), _fact("m5", completed=NOW - timedelta(days=10), start=NOW - timedelta(days=11))]
    counts = sc.match_counts(facts, NOW, date(2026, 10, 25))
    assert counts["r1_matches"] == 2 and counts["r1_target"] == sc.R1_MATCHES
    assert counts["r2_matches"] == 1 and counts["r2_target"] == sc.R2_MATCHES  # only m1 starts after the cut-off
    text = rep.render(state, rows, NOW, [], counts=counts, gate={}, tier=None)
    assert "R1 (model against the market, log loss): 2 of 200 matches" in text
    assert "R2 (strategy on real prices): 1 of 150 held-out matches" in text
    # the R3 table: settled MATCHES (3) against 30, and settled BETS (40) against 300
    assert "| w05_e03 | 3 of 30 | 40 of 300 |" in text


def test_r2_is_unknown_without_a_readable_model_cut_off():
    counts = sc.match_counts([_fact("m1")], NOW, None)
    assert counts["r2_matches"] is None
    text = rep.render(_state(_rows()), _rows(), NOW, [], counts=counts, gate={}, tier=None)
    assert "held-out count not available" in text


def test_held_out_cutoff_reads_the_newest_production_model_date(tmp_path):
    for name, day in (("t20", "2026-10-25"), ("odi", "2026-10-20")):
        path = tmp_path / "models" / "prematch" / f"{name}_v3_walkforward.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"final_model_trained_through": day}), encoding="utf-8")
    assert sc.held_out_cutoff(tmp_path) == date(2026, 10, 25)
    assert sc.held_out_cutoff(tmp_path / "missing") is None


# ----------------------------------------------------------------------------- gate rendering
def test_gate_status_is_rendered_per_format_with_matches():
    gate = {
        "t20": {"inplay_model_allowed": False, "matches": 1, "logloss_diff": -0.0158},
        "odi": {"inplay_model_allowed": True, "matches": 16, "logloss_diff": -0.02},
    }
    text = rep.render(_state(_rows()), _rows(), NOW, [], counts=None, gate=gate, tier=None)
    assert "| t20 | closed | 1 | -0.0158 |" in text
    assert "| odi | open | 16 | -0.0200 |" in text
    assert "does not restrict paper" in text


def test_gate_not_loaded_is_said_plainly_and_a_missing_file_is_closed(tmp_path):
    text = rep.render(_state(_rows()), _rows(), NOW, [], counts=None, gate=None, tier=None)
    assert "Not loaded in this render." in text
    from training.inplay_eval import load_inplay_gate

    gate = load_inplay_gate(tmp_path / "absent.json")
    assert all(not row["open"] for row in sc.gate_rows(gate))


# ----------------------------------------------------------------------------- Tier C is absent
def test_tier_c_measures_are_absent_from_the_report():
    rows = _rows()
    state = _state(rows)
    counts = sc.match_counts([_fact("m1")], NOW, None)
    tier = sc.tier_b(rows, state, now=NOW, refusals=None)
    text = rep.render(state, rows, NOW, [], counts=counts, gate={}, tier=tier)
    for banned in ("## Detail: per-bet intervals", "CLV per bet [95% CI]", "P&L per bet", "per-bet", "Elo",
                   "bet rows", "row count"):
        assert banned not in text, banned
    assert "Tier B" in text and "Trades per match against the 30-ticket budget" in text


def test_tier_b_counts_trades_per_match_against_the_budget():
    rows = [_bet(variant="w05_e03", match_id="busy", status="filled", clv=0.01) for _ in range(31)]
    rows += [_bet(variant="w05_e03", match_id="quiet", status="settled", clv=0.01)]
    state = _state(rows, variants=("w05_e03",))
    tier = sc.tier_b(rows, state, now=NOW, refusals=None)
    row = next(v for v in tier["variants"] if v["variant"] == "w05_e03")
    assert row["trades_max"] == 31 and row["matches_over_budget"] == 1 and row["matches"] == 2


def test_guard_refusals_count_cycles_in_the_last_24_hours_only():
    lines = [
        json.dumps({"cycle_at": (NOW - timedelta(hours=1)).isoformat(), "guard_refusals": {"spread_too_wide": 2}}),
        json.dumps({"cycle_at": (NOW - timedelta(hours=30)).isoformat(), "guard_refusals": {"spread_too_wide": 9}}),
        json.dumps({"cycle_at": (NOW - timedelta(hours=2)).isoformat(), "error": "X"}),
        "not json",
    ]
    cycles, counts = sc.read_refusals_from_lines(lines, NOW)
    assert cycles == 1 and counts == {"spread_too_wide": 2}


# ----------------------------------------------------------------------------- the dashboard
def _live_inputs():
    registry = [{
        "match_id": "m1", "team_home": "Alpha", "team_away": "Beta", "competition": "Nepal Premier League",
        "match_start_time": NOW - timedelta(hours=1), "actual_start_time": None, "is_simulated": False,
        "settled": False,
    }]
    price_rows = [{"match_id": "m1", "time": NOW - timedelta(seconds=30), "back_home": 2.0, "lay_home": 2.1,
                   "back_away": 2.0, "lay_away": 2.1, "source": "listing"}]
    ledger = [{
        "variant": "w05_e03", "match_id": "m1", "status": "filled", "side": "BACK", "selection": "home",
        "price_limit": 2.0, "price_filled": 2.0, "stake": 1_000.0, "decided_at": NOW - timedelta(minutes=5),
        "filled_at": NOW - timedelta(minutes=4), "p_model": 0.6, "p_used": 0.55, "status_reason": "",
    }]
    return registry, {"m1": NOW - timedelta(seconds=30)}, price_rows, ledger


def test_live_positions_show_home_and_away_outcomes_and_the_worst_case():
    registry, last, price_rows, ledger = _live_inputs()
    live = sc.live_matches_from(registry, last, price_rows, ledger, NOW)
    assert len(live) == 1 and live[0]["p_model"] == 0.6 and live[0]["p_market"] is not None
    pos = next(p for p in live[0]["positions"] if p["variant"] == "w05_e03")
    assert pos["home"] == pytest.approx(1_000.0)   # back at 2.0 wins 1,000 when home wins
    assert pos["away"] == pytest.approx(-1_000.0)  # and loses the stake when away wins
    assert pos["worst"] == pytest.approx(1_000.0) and pos["worst_share"] == pytest.approx(0.01)


def test_dashboard_renders_without_network_and_has_no_script(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("the dashboard must not open a network connection")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)

    rows = _rows()
    state = _state(rows)
    registry, last, price_rows, ledger = _live_inputs()
    data = {
        "rows": rows,
        "state": state,
        "missing": [],
        "counts": sc.match_counts([_fact("m1")], NOW, date(2026, 10, 25)),
        "gate": {"t20": {"inplay_model_allowed": False, "matches": 1, "logloss_diff": None}},
        "tier": sc.tier_b(rows, state, now=NOW, refusals=None),
        "live": sc.live_matches_from(registry, last, price_rows, ledger, NOW),
    }
    html = dash.render_html(data, NOW)
    assert "<script" not in html and "<script src=" not in html
    assert "<!doctype html>" in html and "prefers-color-scheme: dark" in html
    assert "PHOENIX paper dashboard" in html and "Live now" in html and "Alpha v Beta" in html
    assert "Settled real matches with a tradable book" in html
    assert "http://" not in html and "https://" not in html  # no external resources at all
    assert 'src="' not in html


def test_dashboard_says_when_no_real_match_is_live():
    data = {"rows": [], "state": _state([]), "missing": [], "counts": None, "gate": None, "tier": None, "live": []}
    html = dash.render_html(data, NOW)
    assert "No real match is being traded right now" in html
    assert "<script" not in html


def test_write_dashboard_path_is_the_reports_paper_folder():
    assert dash.REPORT_PATH == Path(dash.ROOT) / "reports" / "paper" / "dashboard.html"
