"""S36: conservative Cricsheet <-> real price alignment (synthetic fixtures with known ball times)."""

from __future__ import annotations

import gzip
import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from scripts import align_ball_by_ball as aln
from scripts.align_ball_by_ball import (
    FALLBACK_LAG_S,
    AlignmentError,
    CricketMatch,
    Delivery,
    RecordedMatch,
    SafetyLag,
    UnsupportedFormat,
    align_corpus,
    align_deliveries,
    enrich,
    estimate_ball_times,
    index_cricsheet,
    load_deliveries,
    match_markets,
    parse_cricket_deliveries,
    select_safety_lag,
    truth_from_observations,
    validate_alignment,
)

BASE = datetime(2026, 10, 1, 14, 0, tzinfo=timezone.utc)
FALLBACK = SafetyLag(FALLBACK_LAG_S, "fallback_test")


# ================================================================
# Synthetic fixture: a T20 match with known true delivery times
# ================================================================


def _innings_script(rng: random.Random, n_overs: int, p_wicket: float) -> tuple[list[dict], list[float], float]:
    """Cricsheet overs plus the true start offset of every delivery and the innings end offset."""
    overs: list[dict] = []
    offsets: list[float] = []
    t = 0.0
    wickets = 0
    for over_no in range(n_overs):
        deliveries: list[dict] = []
        legal_in_over = 0
        while legal_in_over < 6:
            wide = rng.random() < 0.03
            if wide:
                runs, legal, wicket = 1, False, False
            else:
                runs = rng.choice([0, 0, 1, 1, 1, 2, 4, 6])
                legal = True
                wicket = wickets < 9 and rng.random() < p_wicket
            offsets.append(t)
            t += rng.uniform(25, 40)
            if wicket:
                t += rng.uniform(60, 120)
            delivery: dict[str, Any] = {
                "batter": "bat", "bowler": "bowl",
                "runs": {"batter": runs if legal else 0, "extras": 0 if legal else runs, "total": runs},
            }
            if wide:
                delivery["extras"] = {"wides": 1}
            if wicket:
                wickets += 1
                delivery["wickets"] = [{"player_out": "p", "kind": "bowled"}]
            deliveries.append(delivery)
            if legal:
                legal_in_over += 1
        t += rng.uniform(15, 30)
        overs.append({"over": over_no, "deliveries": deliveries})
    return overs, offsets, t


def make_fixture(seed: int, *, with_break: bool = True, n_overs: int = 20) -> dict[str, Any]:
    rng = random.Random(seed)
    overs1, off1, t_end1 = _innings_script(rng, n_overs, 0.04)
    overs2, off2, t_end2 = _innings_script(rng, n_overs, 0.04)
    first_ball = BASE
    in_play_start = first_ball - timedelta(seconds=rng.uniform(0, 60))
    end1 = first_ball + timedelta(seconds=t_end1)
    inn2_start = end1 + timedelta(seconds=rng.uniform(18 * 60, 22 * 60))
    end2 = inn2_start + timedelta(seconds=t_end2)
    truth_inn1 = [first_ball + timedelta(seconds=o) for o in off1]
    truth_inn2 = [inn2_start + timedelta(seconds=o) for o in off2]
    truth_times = truth_inn1 + truth_inn2

    data = {
        "info": {"match_type": "T20", "dates": ["2026-10-01"], "teams": ["India", "Australia"]},
        "innings": [{"team": "India", "overs": overs1}, {"team": "Australia", "overs": overs2}],
    }
    deliveries = parse_cricket_deliveries(data)
    wicket_times = [t for t, d in zip(truth_times, deliveries) if d.wicket]

    ticks: list[dict[str, Any]] = []
    x = 0.0
    t = in_play_start
    pending = list(wicket_times)
    while t <= end2 + timedelta(seconds=120):
        in_break = with_break and end1 <= t < inn2_start
        if not in_break:
            x += rng.gauss(0.0, 0.02)
        while pending and pending[0] <= t:
            x += rng.gauss(0.0, 0.15)
            pending.pop(0)
        price = 1.0 + math.exp(-x)  # decimal odds for implied probability sigmoid(x)
        ticks.append({"time": t.isoformat(), "in_play": True, "ltp_home": price, "back_home": price})
        t += timedelta(seconds=3)
    return {
        "data": data,
        "deliveries": deliveries,
        "ticks": ticks,
        "truth": list(enumerate(truth_times)),
        "first_ball": first_ball,
        "end1": end1,
        "inn2_start": inn2_start,
    }


def _key(d: Delivery) -> tuple[int, int, int, int]:
    return (d.innings, d.legal_balls, d.score, d.wickets)


def _shown_index(deliveries: tuple[Delivery, ...]) -> dict[tuple[int, int, int, int], int]:
    return {_key(d): i for i, d in enumerate(deliveries)}


# ================================================================
# Parsing
# ================================================================


class TestParsing:
    def test_wides_are_not_legal_and_state_counts_follow_cricsheet(self) -> None:
        data = {"innings": [{"team": "A", "overs": [{"over": 0, "deliveries": [
            {"runs": {"total": 1}, "extras": {"wides": 1}},
            {"runs": {"total": 4}},
            {"runs": {"total": 0}, "wickets": [{"kind": "caught"}]},
        ]}]}]}
        deliveries = parse_cricket_deliveries(data)
        assert [d.legal for d in deliveries] == [False, True, True]
        assert [d.legal_balls for d in deliveries] == [0, 1, 2]
        assert [d.score for d in deliveries] == [1, 5, 5]
        assert [d.wickets for d in deliveries] == [0, 0, 1]
        assert deliveries[2].wicket is True

    def test_super_over_is_dropped_and_second_innings_target_comes_from_first_total(self) -> None:
        def inn(team: str, runs: int, balls: int) -> dict[str, Any]:
            return {"team": team, "overs": [{"over": 0, "deliveries": [{"runs": {"total": runs}}] * balls}]}

        data = {"innings": [inn("A", 3, 6), inn("B", 1, 6), inn("A", 9, 1)]}
        data["innings"][2]["super_over"] = True
        deliveries = parse_cricket_deliveries(data)
        assert {d.innings for d in deliveries} == {1, 2}
        assert deliveries[-1].target == 19  # first innings 6 x 3 = 18, target 19
        assert deliveries[-1].innings == 2


# ================================================================
# Estimation
# ================================================================


class TestEstimation:
    def test_innings_one_anchored_at_in_play_and_break_detected(self) -> None:
        fx = make_fixture(1)
        result = align_deliveries(fx["deliveries"], fx["ticks"], "t20")
        assert result.anchors == 2
        assert result.break_start is not None
        assert abs((result.break_start - fx["end1"]).total_seconds()) < 5 * 60
        assert 0.7 <= result.pace_factor <= 1.5
        assert len(result.times) == len(fx["deliveries"])
        assert all(a <= b for a, b in zip(result.times, result.times[1:]))

    def test_no_innings_break_gives_single_anchor_and_no_residual(self) -> None:
        fx = make_fixture(2, with_break=False)
        result = align_deliveries(fx["deliveries"], fx["ticks"], "t20")
        assert result.anchors == 1
        assert result.pace_residual is None
        assert result.break_start is None

    def test_estimate_ball_times_returns_one_time_per_delivery(self) -> None:
        fx = make_fixture(3)
        times = estimate_ball_times(fx["deliveries"], fx["ticks"], "t20")
        assert len(times) == len(fx["deliveries"])
        assert times[0] <= fx["first_ball"] + timedelta(seconds=60)

    def test_multiday_family_has_no_pace_model(self) -> None:
        fx = make_fixture(4)
        with pytest.raises(UnsupportedFormat):
            align_deliveries(fx["deliveries"], fx["ticks"], "multiday")

    def test_no_in_play_tick_is_an_alignment_error(self) -> None:
        fx = make_fixture(5)
        ticks = [dict(t, in_play=False) for t in fx["ticks"]]
        with pytest.raises(AlignmentError):
            align_deliveries(fx["deliveries"], ticks, "t20")


# ================================================================
# Conservative release (the success criterion)
# ================================================================


class TestConservativeRelease:
    def test_no_delivery_state_is_released_before_its_true_time(self) -> None:
        for seed in range(10, 16):
            fx = make_fixture(seed)
            times = align_deliveries(fx["deliveries"], fx["ticks"], "t20").times
            truth = dict(fx["truth"])
            early = [i for i, est in enumerate(times) if est + timedelta(seconds=FALLBACK_LAG_S) < truth[i]]
            assert early == [], f"seed {seed}: early release at deliveries {early[:5]}"

    def test_no_tick_shows_a_state_before_the_true_time(self) -> None:
        for seed in range(20, 24):
            fx = make_fixture(seed)
            cricket = CricketMatch(
                name=f"fx{seed}", rel=f"fx{seed}.json", file=Path(f"fx{seed}.json"), family="t20",
                match_format="T20", match_date=BASE.date(), teams=frozenset({"India", "Australia"}),
                deliveries=fx["deliveries"],
            )
            episode = {"match_id": f"bf_{seed}", "market_id": f"1.{seed}", "family": "t20",
                       "team_home": "India", "team_away": "Australia", "start": fx["ticks"][0]["time"],
                       "ticks": fx["ticks"]}
            out = enrich(episode, cricket, safety_lag=FALLBACK)
            index = _shown_index(fx["deliveries"])
            truth = dict(fx["truth"])
            shown_any = False
            for tick in out["ticks"]:
                if tick["innings"] is None:
                    continue
                shown_any = True
                k = index[(tick["innings"], aln._legal_from_overs(tick["overs"]), tick["score"], tick["wickets"])]
                assert truth[k] <= datetime.fromisoformat(tick["time"]), f"seed {seed}: early state at {tick['time']}"
            assert shown_any

    def test_validation_on_fewer_than_five_matches_falls_back_to_300s(self) -> None:
        matches = []
        for seed in (30, 31):
            fx = make_fixture(seed)
            matches.append(RecordedMatch("m", "t20", tuple(fx["ticks"]), fx["deliveries"], tuple(fx["truth"])))
        report = validate_alignment(matches)
        assert report.safety_lag.seconds == FALLBACK_LAG_S == 300.0
        assert report.safety_lag.source.startswith("fallback_too_few_matches")
        assert report.matches == 2
        assert report.states > 0

    def test_p95_lag_is_used_when_errors_are_mostly_late(self) -> None:
        errors = [-50.0] * 40 + [10.0] * 5
        lag = select_safety_lag(errors, 10)
        assert lag.source == "p95_validated"
        assert lag.seconds == 60.0

    def test_fallback_when_too_many_states_would_release_early(self) -> None:
        errors = [0.0] * 95 + [200.0] * 5
        lag = select_safety_lag(errors, 10)
        assert lag.seconds == FALLBACK_LAG_S
        assert lag.source.startswith("fallback_early_fraction")

    def test_truth_mapping_keeps_earliest_observation_per_state(self) -> None:
        deliveries = parse_cricket_deliveries({"innings": [{"team": "A", "overs": [{"over": 0, "deliveries": [
            {"runs": {"total": 0}}, {"runs": {"total": 2}}]}]}]})
        obs = [
            (BASE + timedelta(seconds=90), 1, 0.2, 2, 0),
            (BASE + timedelta(seconds=30), 1, 0.2, 2, 0),
            (BASE + timedelta(seconds=10), 1, 0.1, 0, 0),
            (BASE + timedelta(seconds=10), 1, None, 0, 0),
        ]
        truth = truth_from_observations(deliveries, obs)
        assert truth == ((0, BASE + timedelta(seconds=10)), (1, BASE + timedelta(seconds=30)))

    def test_legal_balls_from_cricket_overs_notation(self) -> None:
        assert aln._legal_from_overs(12.3) == 75
        assert aln._legal_from_overs(0.0) == 0
        assert aln._legal_from_overs(19.6) == 120


# ================================================================
# Matching
# ================================================================


def _entry(name: str, day: str = "2026-10-01", teams: tuple[str, str] = ("India", "Australia")) -> CricketMatch:
    return CricketMatch(
        name=name, rel=f"{name}.json", file=Path(f"{name}.json"), family="t20", match_format="T20",
        match_date=datetime.fromisoformat(day).date(), teams=frozenset(teams),
    )


def _episode(market: str, day: str = "2026-10-01T16:00:00+00:00", teams: tuple[str, str] = ("India", "Australia")) -> dict[str, Any]:
    return {"market_id": market, "match_id": f"bf_{market}", "family": "t20",
            "team_home": teams[0], "team_away": teams[1], "start": day, "ticks": []}


class TestMatching:
    def test_unique_candidate_within_one_day_matches(self) -> None:
        result = match_markets([_episode("1.1", "2026-10-02T03:00:00+00:00")], [_entry("a", "2026-10-01")])
        assert len(result.matched) == 1
        assert result.rejected == ()

    def test_two_candidate_files_reject_the_market(self) -> None:
        result = match_markets([_episode("1.1")], [_entry("a"), _entry("b")])
        assert result.matched == ()
        assert result.rejected[0]["reason"] == "ambiguous_cricsheet"

    def test_one_file_claimed_by_two_markets_rejects_both(self) -> None:
        result = match_markets([_episode("1.1"), _episode("1.2")], [_entry("a")])
        assert result.matched == ()
        assert sorted(r["reason"] for r in result.rejected) == ["ambiguous_market", "ambiguous_market"]

    def test_no_candidate_is_unmatched_and_wrong_teams_do_not_match(self) -> None:
        result = match_markets([_episode("1.1", teams=("India", "England"))], [_entry("a")])
        assert result.matched == ()
        assert result.unmatched[0]["reason"] == "no_cricsheet_match"

    def test_multiday_market_is_rejected_as_unsupported(self) -> None:
        ep = dict(_episode("1.1"), family="multiday")
        result = match_markets([ep], [_entry("a")])
        assert result.rejected[0]["reason"] == "unsupported_format"


# ================================================================
# Enrichment, corpus output and CLI path
# ================================================================


class TestEnrichAndCorpus:
    def test_enrich_copies_ticks_and_records_quality_on_first_tick_only(self) -> None:
        fx = make_fixture(40)
        original = json.dumps(fx["ticks"], sort_keys=True)
        cricket = CricketMatch(name="fx", rel="fx.json", file=Path("fx.json"), family="t20", match_format="T20",
                               match_date=BASE.date(), teams=frozenset({"India", "Australia"}),
                               deliveries=fx["deliveries"])
        episode = {"match_id": "bf_40", "market_id": "1.40", "family": "t20", "team_home": "India",
                   "team_away": "Australia", "start": fx["ticks"][0]["time"], "ticks": fx["ticks"]}
        out = enrich(episode, cricket, safety_lag=SafetyLag(90.0, "p95_validated"))
        assert json.dumps(fx["ticks"], sort_keys=True) == original
        quality = out["ticks"][0]["_meta"]["alignment_quality"]
        assert quality["anchors"] == 2
        assert quality["innings_break_detected"] is True
        assert quality["safety_lag_s"] == 90.0
        assert quality["pace_constants"] == "default_unfitted"
        assert all("_meta" not in t for t in out["ticks"][1:])
        assert out["ticks"][0]["score"] is None  # nothing released at the first in-play tick

    def test_single_anchor_market_is_withheld_not_released_with_a_lag(self, tmp_path: Path) -> None:
        # Without an innings break the pace error is unbounded (default pace), so no lag is safe.
        fx = make_fixture(41, with_break=False)
        cricket = CricketMatch(name="fx", rel="fx.json", file=Path("fx.json"), family="t20", match_format="T20",
                               match_date=BASE.date(), teams=frozenset({"India", "Australia"}),
                               deliveries=fx["deliveries"])
        episode = {"match_id": "bf_41", "market_id": "1.41", "family": "t20", "team_home": "India",
                   "team_away": "Australia", "start": fx["ticks"][0]["time"], "ticks": fx["ticks"]}
        with pytest.raises(aln.SingleAnchorError):
            enrich(episode, cricket, safety_lag=SafetyLag(600.0, "p95_validated"))
        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "41.json").write_text(json.dumps(fx["data"]), encoding="utf-8")
        manifest = align_corpus([episode], index_cricsheet(raw), tmp_path / "out", safety_lag=FALLBACK,
                                validation={"skipped": "test"})
        assert manifest["counts"]["matched"] == 0
        assert manifest["rejected"] == [{"market_id": "1.41", "reason": "no_innings_break", "detail": "41.json"}]

    def test_align_corpus_writes_gz_and_deterministic_manifest(self, tmp_path: Path) -> None:
        fx = make_fixture(42)
        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "1000001.json").write_text(json.dumps(fx["data"]), encoding="utf-8")
        entries = index_cricsheet(raw)
        assert [e.rel for e in entries] == ["1000001.json"]
        episode = {"match_id": "bf_1_300002", "market_id": "1.300002", "family": "t20",
                   "team_home": "India", "team_away": "Australia", "start": fx["ticks"][0]["time"],
                   "ticks": fx["ticks"]}
        out = tmp_path / "aligned"
        manifest = align_corpus([episode], entries, out, safety_lag=FALLBACK, validation={"skipped": "test"})
        first = (out / "manifest.json").read_bytes()
        align_corpus([episode], entries, out, safety_lag=FALLBACK, validation={"skipped": "test"})
        assert (out / "manifest.json").read_bytes() == first
        assert manifest["counts"]["matched"] == 1
        row = manifest["matched"][0]
        assert row["cricsheet_file"] == "1000001.json"
        assert row["cricsheet_match_id"] == "1000001"  # S38 evaluator join key
        ticks = json.loads(gzip.decompress((out / row["file"]).read_bytes()).decode("utf-8"))
        assert len(ticks) == len(fx["ticks"])
        assert ticks[-1]["score"] is not None
        assert "alignment_quality" in ticks[0]["_meta"]

    def test_load_deliveries_round_trip(self, tmp_path: Path) -> None:
        fx = make_fixture(43)
        path = tmp_path / "9.json"
        path.write_text(json.dumps(fx["data"]), encoding="utf-8")
        entry =CricketMatch(name="9", rel="9.json", file=path, family="t20", match_format="T20",
                             match_date=BASE.date(), teams=frozenset({"India", "Australia"}))
        assert load_deliveries(entry).deliveries == fx["deliveries"]
