"""S46: recorded LotusBook corpus as real-tick episodes, expanding-window folds, backtest driver on a fixture."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from backtesting.real_backtester import WalkForwardLeakError
from scripts import run_real_backtest as rrb
from scripts.export_recorded_corpus import (
    build_episode_checked,
    export_real_matches,
    load_recorded_real,
    to_real_episode,
)
from strategy.fair_value import family_of

T0 = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
REAL_TICK_KEYS = {
    "time", "market_id", "match_id", "team_home", "team_away", "competition", "match_format", "family",
    "in_play", "ltp_home", "ltp_away", "ltp_draw", "back_home", "lay_home", "back_away", "lay_away",
    "traded_volume_home", "traded_volume_away", "source", "data_tier", "market_model",
}


# ================================================================
# Builders: fake recorder rows (odds_ticks, match_context, match_results)
# ================================================================


def _odds(mid: str, i: int, day: int, fmt_comp: str = "T20 League") -> dict[str, Any]:
    p = 1.9 + (i % 5) * 0.02
    return {
        "time": T0 + timedelta(days=day, seconds=30 * i), "match_id": mid,
        "team_home": "Alpha", "team_away": "Beta", "competition": fmt_comp,
        "back_home": round(p, 2), "lay_home": round(p + 0.05, 2),
        "back_away": 2.0, "lay_away": 2.05, "is_live": True, "source": "lotusbook",
    }


def _ctx(mid: str, k: int, day: int, score: int, fmt: str = "T20") -> dict[str, Any]:
    return {
        "time": T0 + timedelta(days=day, seconds=60 * k), "match_id": mid, "is_live": True,
        "score": score, "wickets": 0, "overs": float(k), "run_rate": 6.0, "req_run_rate": 0.0,
        "innings": 1, "balls_remaining": 120 - k, "batting_team": "Alpha", "bowling_team": "Beta",
        "status": "live", "match_format": fmt,
    }


def _result(mid: str, home_wins: bool) -> dict[str, Any]:
    return {
        "match_id": mid, "team_home": "Alpha", "team_away": "Beta",
        "winner": "Alpha" if home_wins else "Beta", "loser": "Beta" if home_wins else "Alpha",
        "result_type": "win",
    }


def _match(mid: str, day: int, home_wins: bool, *, n_odds: int = 60, fmt: str = "T20") -> dict[str, Any]:
    odds = [_odds(mid, i, day) for i in range(n_odds)]
    context = [_ctx(mid, k, day, score=4 * k, fmt=fmt) for k in range(n_odds // 2 + 1)]
    return {"match_id": mid, "result": _result(mid, home_wins), "odds": odds, "context": context}


def _six_matches() -> list[dict[str, Any]]:
    # Six matches on six different days, both outcomes present.
    return [_match(f"lb_{d}", day=d * 3, home_wins=(d % 2 == 0)) for d in range(6)]


def _export(tmp_path: Path, matches: list[dict[str, Any]]) -> Path:
    out = tmp_path / "recorded_real"
    export_real_matches(matches, out_dir=out, cadence="raw", min_ticks=5, min_coverage=0.0)
    return out


# ================================================================
# Conversion to the real-tick contract
# ================================================================


class TestToRealEpisode:
    def _episode(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        m = _match("lb_x", day=0, home_wins=True, n_odds=20)
        episode, reason = build_episode_checked(m["odds"], m["context"], m["result"], cadence="raw", min_ticks=1)
        assert reason == "ok" and episode is not None
        return episode, m

    def test_converts_to_real_tick_contract(self):
        raw, _ = self._episode()
        ep = to_real_episode(raw)
        assert ep["family"] == "t20"
        assert ep["data_tier"] == "ladder_top"
        assert ep["ticks"], "episode has ticks"
        for tick in ep["ticks"]:
            assert REAL_TICK_KEYS <= set(tick), set(REAL_TICK_KEYS) - set(tick)
            assert tick["source"] == "recorded_lotusbook"
            assert tick["market_model"] == "real"
            assert tick["ltp_home"] is None and tick["ltp_away"] is None and tick["ltp_draw"] is None
            assert tick["traded_volume_home"] is None and tick["traded_volume_away"] is None
            assert tick["in_play"] is True
            assert tick["family"] == "t20"
            assert tick["back_home"] > 1.0 and tick["lay_home"] > 1.0

    def test_result_only_on_first_tick(self):
        raw, _ = self._episode()
        ep = to_real_episode(raw)
        first, rest = ep["ticks"][0], ep["ticks"][1:]
        assert first["_meta"]["winner"] == "Alpha"
        assert first["_meta"]["winner_is_home"] is True
        assert all("_meta" not in t for t in rest)

    def test_state_is_never_taken_from_a_later_context_row(self):
        # Context says score 0 at t=0 and score 100 at t=60 s. The tick at t=30 s must carry score 0.
        mid = "lb_asof"
        odds = [_odds(mid, i, 0) for i in range(4)]  # ticks at 0, 30, 60, 90 s
        ctx = [
            {**_ctx(mid, 0, 0, score=0), "time": T0},
            {**_ctx(mid, 1, 0, score=100), "time": T0 + timedelta(seconds=60)},
        ]
        raw, _ = build_episode_checked(odds, ctx, _result(mid, True), cadence="raw", min_ticks=1)
        ep = to_real_episode(raw)
        by_time = {t["time"]: t for t in ep["ticks"]}
        t30 = by_time[(T0 + timedelta(seconds=30)).isoformat()]
        t60 = by_time[(T0 + timedelta(seconds=60)).isoformat()]
        assert t30["score"] == 0, "a tick must not see a context row recorded after it"
        assert t60["score"] == 100, "a tick at or after a context row sees that row"

    def test_family_from_match_format(self):
        assert family_of("T20") == "t20"
        assert family_of("ODI") == "odi"
        assert family_of("Test") == "multiday"
        m = _match("lb_odi", day=0, home_wins=False, n_odds=10, fmt="ODI")
        raw, _ = build_episode_checked(m["odds"], m["context"], m["result"], cadence="raw", min_ticks=1)
        assert to_real_episode(raw)["family"] == "odi"

    def test_rejects_episode_without_result(self):
        raw, _ = self._episode()
        raw[0].pop("_meta")
        with pytest.raises(ValueError):
            to_real_episode(raw)


# ================================================================
# Export and load
# ================================================================


class TestExportAndLoad:
    def test_round_trip_and_manifest_shape(self, tmp_path):
        out = _export(tmp_path, _six_matches())
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["counts"]["accepted"] == 6
        assert manifest["counts"]["accepted_by_family"] == {"t20": 6}
        entry = manifest["accepted"][0]
        assert entry["file"].startswith("t20/") and entry["file"].endswith(".json.gz")
        assert {"match_id", "family", "competition", "team_home", "team_away", "data_tier", "start", "sha1"} <= set(entry)

        episodes = load_recorded_real("t20", base_dir=out)
        assert len(episodes) == 6
        assert all(ep["family"] == "t20" and ep["ticks"][0]["_meta"] for ep in episodes)

    def test_sha1_mismatch_is_refused(self, tmp_path):
        out = _export(tmp_path, _six_matches()[:1])
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        (out / manifest["accepted"][0]["file"]).write_bytes(b"tampered")
        with pytest.raises(ValueError):
            load_recorded_real("t20", base_dir=out)

    def test_missing_manifest_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_recorded_real("t20", base_dir=tmp_path / "nothing")


# ================================================================
# Expanding-window folds
# ================================================================


def _dated_episodes(days: list[int]) -> list[dict[str, Any]]:
    out = []
    for n, d in enumerate(days):
        start = (T0 + timedelta(days=d)).isoformat()
        out.append({"match_id": f"m{n}", "start": start, "ticks": [{"time": start}]})
    return out


class TestExpandingFolds:
    def test_never_trains_on_a_match_dated_after_a_test_match(self):
        episodes = _dated_episodes([9, 0, 3, 6, 12, 15, 1, 18, 21, 24, 27, 30])
        folds = rrb.expanding_folds(episodes, min_train=3, block=4)
        assert folds, "fixture should produce folds"
        for fold in folds:
            rrb.assert_no_future_training(fold, fitted_match_ids=[ep["match_id"] for ep in fold.train])
            latest_train = max(rrb._start(ep) for ep in fold.train)
            earliest_test = min(rrb._start(ep) for ep in fold.test)
            assert latest_train <= earliest_test

    def test_train_is_all_earlier_and_test_blocks_do_not_overlap(self):
        episodes = _dated_episodes([0, 1, 2, 3, 4, 5, 6, 7])
        folds = rrb.expanding_folds(episodes, min_train=2, block=3)
        seen_test: list[str] = []
        for fold in folds:
            assert len(fold.train) >= 2
            seen_test.extend(ep["match_id"] for ep in fold.test)
        assert len(seen_test) == len(set(seen_test)) == 6  # matches 2..7 each tested once

    def test_leak_is_detected(self):
        episodes = _dated_episodes([0, 1, 2, 3])
        leaky = rrb.DatedFold(
            label="x", train=(episodes[3],), test=(episodes[0],),  # trains on day 3, tests on day 0
        )
        with pytest.raises(WalkForwardLeakError):
            rrb.assert_no_future_training(leaky)

    def test_fitted_match_in_test_is_detected(self):
        episodes = _dated_episodes([0, 1, 2])
        fold = rrb.DatedFold(label="x", train=(episodes[0], episodes[1]), test=(episodes[2],))
        with pytest.raises(WalkForwardLeakError):
            rrb.assert_no_future_training(fold, fitted_match_ids=["m0", "m2"])

    def test_make_folds_falls_back_to_expanding_for_one_season(self):
        episodes = _dated_episodes([0, 3, 6, 9, 12, 15])  # all in 2026, one season
        folds = rrb.make_folds(episodes, min_train=2)
        assert folds and all(isinstance(f, rrb.DatedFold) for f in folds)


# ================================================================
# Backtest driver on a 6-match recorded fixture
# ================================================================


class TestBacktestDriver:
    def test_hold_random_value_run_end_to_end_on_six_matches(self, tmp_path):
        out = _export(tmp_path, _six_matches())
        episodes = load_recorded_real("t20", base_dir=out)
        execution = rrb.ExecutionModel.preset("manual")

        for strategy in ("hold", "random", "value"):
            report = rrb.run_walk_forward(
                episodes, strategy, "t20", execution,
                n_bootstrap=100, model_version="v1", scheme="auto", min_train=2,
            )
            assert report["n_episodes"] == 6
            assert report["scheme"] == "auto"
            assert report["r0"]["scope"] in ("out_of_sample_folds", "all_matches_no_fold")
            assert report["r0"]["n_matches"] > 0
            assert report["folds"], f"{strategy}: expanding folds expected for one season"
            assert all(f["scheme"] == "expanding" for f in report["folds"])

    def test_cli_writes_report_with_n_and_date_range(self, tmp_path):
        out = _export(tmp_path, _six_matches())
        reports = tmp_path / "reports"
        rc = rrb.main([
            "--strategy", "hold", "--family", "t20", "--corpus", "recorded",
            "--recorded-dir", str(out), "--report-dir", str(reports),
            "--min-train", "2", "--n-bootstrap", "100",
        ])
        assert rc == 0
        report = json.loads((reports / "real_backtest_hold_t20_recorded.json").read_text(encoding="utf-8"))
        corpus = report["corpus"]
        assert corpus["n_matches"] == 6
        assert corpus["n_matches_by_family"] == {"t20": 6}
        assert corpus["date_range"]["first"] == "2026-03-01"
        assert corpus["date_range"]["last"] == "2026-03-16"
        assert corpus["share_ticks_with_score_state"] == 1.0

    def test_too_small_for_any_fold_still_runs_r0(self, tmp_path):
        out = _export(tmp_path, _six_matches()[:3])
        episodes = load_recorded_real("t20", base_dir=out)
        report = rrb.run_walk_forward(
            episodes, "hold", "t20", rrb.ExecutionModel.preset("manual"),
            n_bootstrap=100, scheme="auto", min_train=30,
        )
        assert report["folds"] == []
        assert report["r0"]["scope"] == "all_matches_no_fold"
        assert report["r0"]["n_matches"] == 3
        assert any("no walk-forward fold" in w for w in report["warnings"])

    def test_missing_recorded_corpus_returns_error_code(self, tmp_path):
        rc = rrb.main([
            "--strategy", "hold", "--family", "t20", "--recorded-dir", str(tmp_path / "none"),
            "--report-dir", str(tmp_path / "r"),
        ])
        assert rc == 1

    def test_corpus_default_is_recorded(self):
        args = rrb.parse_args(["--strategy", "hold", "--family", "t20"])
        assert args.corpus == "recorded"
