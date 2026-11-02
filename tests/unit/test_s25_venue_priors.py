"""S25: venue par and toss priors. Small in-memory Cricsheet-shaped dicts, no files, no database."""

from __future__ import annotations

import copy
import json
from datetime import date
from typing import Any, Dict, List, Optional

import pytest

from shared.venue_priors import MIN_SAMPLE, VenuePriors, canonical_venue, family_for_match_type


def _innings(team: str, runs: int, legal_balls: int, wickets: int = 0, super_over: bool = False) -> Dict[str, Any]:
    base, extra = divmod(runs, legal_balls)
    deliveries: List[Dict[str, Any]] = []
    for i in range(legal_balls):
        total = base + (1 if i < extra else 0)
        deliveries.append({"batter": "b", "bowler": "w", "runs": {"batter": total, "extras": 0, "total": total}})
    for w in range(wickets):
        deliveries[w]["wickets"] = [{"player_out": f"p{w}", "kind": "bowled"}]
    overs = [{"over": i // 6, "deliveries": deliveries[i : i + 6]} for i in range(0, len(deliveries), 6)]
    innings: Dict[str, Any] = {"team": team, "overs": overs}
    if super_over:
        innings["super_over"] = True
    return innings


def _match(
    day: str,
    *,
    first_team: str = "Alpha",
    second_team: str = "Beta",
    first_runs: int = 160,
    first_balls: int = 120,
    first_wkts: int = 10,
    second_runs: int = 120,
    second_balls: int = 120,
    second_wkts: int = 10,
    winner: Optional[str] = "Alpha",
    toss: str = "Alpha",
    match_type: str = "T20",
    gender: str = "male",
    venue: str = "Test Ground",
    city: Optional[str] = "Testville",
    overs: Optional[int] = None,
) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "dates": [day],
        "match_type": match_type,
        "gender": gender,
        "venue": venue,
        "teams": [first_team, second_team],
        "toss": {"winner": toss, "decision": "bat"},
    }
    if city is not None:
        info["city"] = city
    if overs is not None:
        info["overs"] = overs
    if winner:
        info["outcome"] = {"winner": winner}
    return {
        "info": info,
        "innings": [
            _innings(first_team, first_runs, first_balls, first_wkts),
            _innings(second_team, second_runs, second_balls, second_wkts),
        ],
    }


def _days(n: int, month: int = 1, year: int = 2020) -> List[str]:
    return [f"{year}-{month:02d}-{d:02d}" for d in range(1, n + 1)]


# ---------------------------------------------------------------- canonicalisation


@pytest.mark.parametrize(
    "a,b",
    [
        ("M. Chinnaswamy Stadium", "M Chinnaswamy Stadium, Bengaluru"),
        ("M Chinnaswamy Stadium", "Chinnaswamy Stadium"),
        ("MA Chidambaram Stadium, Chepauk, Chennai", "MA Chidambaram Stadium, Chepauk"),
        ("Gahanga International Cricket Stadium. Rwanda", "Gahanga International Cricket Stadium, Rwanda"),
        ("Feroz Shah Kotla", "Arun Jaitley Stadium, Delhi"),
        ("Sardar Patel Stadium, Motera", "Narendra Modi Stadium, Ahmedabad"),
        ("The Oval", "Kennington Oval"),
        ("Lord's", "Lords"),
        ("Eden Gardens, Kolkata", "eden gardens"),
        ("R. Premadasa Stadium, Colombo", "R Premadasa Stadium"),
    ],
)
def test_alias_pairs_canonicalise_to_one_key(a: str, b: str) -> None:
    assert canonical_venue(a) == canonical_venue(b)
    assert canonical_venue(a) != ""


def test_trailing_city_is_not_part_of_the_key() -> None:
    assert canonical_venue("Wankhede Stadium, Mumbai") == canonical_venue("Wankhede Stadium") == "wankhede stadium"


def test_distinct_grounds_and_turfs_stay_distinct() -> None:
    assert canonical_venue("Al Amerat Cricket Ground Oman Cricket (Ministry Turf 1)") != canonical_venue(
        "Al Amerat Cricket Ground Oman Cricket (Ministry Turf 2)"
    )
    assert canonical_venue("Botswana Cricket Association Oval 1, Gaborone") != canonical_venue(
        "Botswana Cricket Association Oval 2, Gaborone"
    )


def test_blank_name_gives_empty_key() -> None:
    assert canonical_venue(None) == ""
    assert canonical_venue("   ") == ""


@pytest.mark.parametrize(
    "match_type,family",
    [
        ("T20", "t20"),
        ("IT20", "t20"),
        ("ODI", "odi"),
        ("ODM", "odi"),
        ("Test", "multiday"),
        ("MDM", "multiday"),
        ("T10", None),
        (None, None),
    ],
)
def test_match_type_maps_to_family(match_type: Optional[str], family: Optional[str]) -> None:
    assert family_for_match_type(match_type) == family


# ---------------------------------------------------------------- par and fallback


def test_high_scoring_venue_has_higher_par_than_low_scoring_venue() -> None:
    high = [_match(d, venue="High Ground", first_runs=200, first_wkts=5) for d in _days(7)]
    low = [_match(d, venue="Low Ground", first_runs=130, first_wkts=9) for d in _days(7)]
    priors = VenuePriors.build(high + low)

    hi = priors.get("High Ground", "t20", "male", "2021-01-01")
    lo = priors.get("Low Ground", "t20", "male", "2021-01-01")
    assert hi["fallback"] is False and lo["fallback"] is False
    assert hi["par_first_innings"] == pytest.approx(200.0)
    assert lo["par_first_innings"] == pytest.approx(130.0)
    assert hi["par_first_innings"] > lo["par_first_innings"]
    assert hi["par_run_rate"] > lo["par_run_rate"]
    assert hi["par_run_rate"] == pytest.approx(200 / 20.0)


def test_unknown_venue_falls_back_to_family_average_as_of() -> None:
    a = [_match(d, venue="Ground A", first_runs=180) for d in _days(10)]
    b = [_match(d, venue="Ground B", first_runs=140) for d in _days(5, month=2)]
    priors = VenuePriors.build(a + b)

    p = priors.get("Nowhere Ground", "t20", "male", "2021-01-01")
    assert p["fallback"] is True
    assert p["venue_n"] == 0
    assert p["n"] == 15
    assert p["par_first_innings"] == pytest.approx((180 * 10 + 140 * 5) / 15)


def test_small_venue_sample_falls_back_to_family_average() -> None:
    small = [_match(d, venue="Small Ground", first_runs=250) for d in _days(MIN_SAMPLE - 1)]
    other = [_match(d, venue="Other Ground", first_runs=150) for d in _days(5, month=3)]
    priors = VenuePriors.build(small + other)

    p = priors.get("Small Ground", "t20", "male", "2021-01-01")
    assert p["fallback"] is True
    assert p["venue_n"] == MIN_SAMPLE - 1
    assert p["par_first_innings"] == pytest.approx((250 * 4 + 150 * 5) / 9)


def test_empty_build_returns_none_metrics_without_error() -> None:
    p = VenuePriors.build([]).get("Anywhere", "odi", "male", "2021-01-01")
    assert p["fallback"] is True
    assert p["n"] == 0
    assert p["par_first_innings"] is None
    assert p["par_run_rate"] is None
    assert p["chase_win_pct"] is None
    assert p["toss_winner_win_pct"] is None


def test_gender_series_do_not_mix() -> None:
    men = [_match(d, venue="Shared", first_runs=170, gender="male") for d in _days(6)]
    women = [_match(d, venue="Shared", first_runs=110, gender="female") for d in _days(6, month=4)]
    priors = VenuePriors.build(men + women)
    assert priors.get("Shared", "t20", "male", "2021-01-01")["par_first_innings"] == pytest.approx(170.0)
    assert priors.get("Shared", "t20", "female", "2021-01-01")["par_first_innings"] == pytest.approx(110.0)


# ---------------------------------------------------------------- strict as-of


def test_prior_excludes_match_on_or_after_as_of() -> None:
    matches = [_match(d, venue="V", first_runs=100 + 10 * i) for i, d in enumerate(_days(10))]
    priors = VenuePriors.build(matches)

    p = priors.get("V", "t20", "male", "2020-01-06")  # matches dated 01..05 only
    assert p["n"] == 5
    assert p["fallback"] is False
    assert p["par_first_innings"] == pytest.approx(sum(100 + 10 * i for i in range(5)) / 5)

    # date objects are accepted and the match dated exactly as_of is excluded
    p_date = priors.get("V", "t20", "male", date(2020, 1, 6))
    assert p_date["n"] == 5 and p_date["par_first_innings"] == p["par_first_innings"]

    assert priors.get("V", "t20", "male", "2020-01-05")["n"] == 4


def test_changing_match_n_or_later_does_not_change_prior_at_as_of_n() -> None:
    matches = [_match(d, venue="V", first_runs=150, winner="Alpha", toss="Alpha") for d in _days(10)]
    mutated = copy.deepcopy(matches)
    # Rewrite match 8 (index 7) and match 9 completely: score, winner, toss.
    for idx in (7, 8):
        mutated[idx] = _match(_days(10)[idx], venue="V", first_runs=300, winner="Beta", toss="Beta")

    cutoff = _days(10)[7]  # the first mutated match is dated exactly at as_of
    assert VenuePriors.build(matches).get("V", "t20", "male", cutoff) == VenuePriors.build(mutated).get(
        "V", "t20", "male", cutoff
    )
    later = "2020-01-10"
    assert VenuePriors.build(matches).get("V", "t20", "male", later) != VenuePriors.build(mutated).get(
        "V", "t20", "male", later
    )


def test_no_lookahead_from_future_fallback_average() -> None:
    past = [_match(d, venue="A", first_runs=120) for d in _days(5)]
    future = [_match(d, venue="B", first_runs=260) for d in _days(20, month=6)]
    priors = VenuePriors.build(past + future)
    p = priors.get("Unknown", "t20", "male", "2020-02-01")
    assert p["n"] == 5
    assert p["par_first_innings"] == pytest.approx(120.0)


# ---------------------------------------------------------------- innings rules


def test_chase_and_toss_win_percentages() -> None:
    # Alpha bats first in every match. Beta (chasing) wins 4 of 6; Alpha wins the toss in all 6 and wins 2.
    results = ["Beta", "Beta", "Beta", "Beta", "Alpha", "Alpha"]
    matches = [_match(d, venue="Chase Ground", winner=w, toss="Alpha") for d, w in zip(_days(6), results)]
    p = VenuePriors.build(matches).get("Chase Ground", "t20", "male", "2021-01-01")
    assert p["n_chase"] == 6
    assert p["chase_win_pct"] == pytest.approx(100 * 4 / 6, abs=1e-3)
    assert p["n_toss"] == 6
    assert p["toss_winner_win_pct"] == pytest.approx(100 * 2 / 6, abs=1e-3)


def test_no_result_and_tie_do_not_count_for_chase_or_toss() -> None:
    normal = [_match(d, venue="Res", winner="Beta", toss="Beta") for d in _days(5)]
    no_result = _match("2020-02-01", venue="Res", winner=None, toss="Beta")
    tie = _match("2020-02-02", venue="Res", winner="Alpha", toss="Beta")
    tie["info"]["outcome"]["result"] = "tie"
    p = VenuePriors.build(normal + [no_result, tie]).get("Res", "t20", "male", "2021-01-01")
    assert p["n_chase"] == 5 and p["chase_win_pct"] == pytest.approx(100.0)
    assert p["n_toss"] == 5 and p["toss_winner_win_pct"] == pytest.approx(100.0)


def test_rain_shortened_first_innings_is_excluded_but_all_out_counts() -> None:
    full = [_match(d, venue="Rain", first_runs=170, first_wkts=6) for d in _days(5)]
    shortened = _match("2020-02-01", venue="Rain", first_runs=90, first_balls=60, first_wkts=3)
    all_out_early = _match("2020-02-02", venue="Rain", first_runs=100, first_balls=90, first_wkts=10)
    p = VenuePriors.build(full + [shortened, all_out_early]).get("Rain", "t20", "male", "2021-01-01")
    assert p["n"] == 6  # 5 full + 1 all-out; the shortened innings is excluded
    assert p["par_first_innings"] == pytest.approx((170 * 5 + 100) / 6)


def test_super_over_is_ignored_for_innings_and_chase() -> None:
    base = [_match(d, venue="Tie", winner="Beta") for d in _days(5)]
    tied = _match("2020-02-01", venue="Tie", first_runs=150, winner="Alpha", toss="Alpha")
    tied["innings"].append(_innings("Alpha", 12, 6, 0, super_over=True))
    tied["innings"].append(_innings("Beta", 8, 6, 1, super_over=True))
    p = VenuePriors.build(base + [tied]).get("Tie", "t20", "male", "2021-01-01")
    assert p["n"] == 6
    assert p["n_chase"] == 5  # the super-over-decided match is not a chase result
    assert p["chase_win_pct"] == pytest.approx(100.0)


def test_illegal_deliveries_do_not_count_as_balls() -> None:
    # 120 legal balls plus two wides (1 run each): runs = 122, overs = 20.0 (wides are not balls).
    m = _match("2020-01-01", venue="Wide", first_runs=120, first_balls=120, first_wkts=2)
    wide = {"batter": "b", "bowler": "w", "runs": {"batter": 0, "extras": 1, "total": 1}, "extras": {"wides": 1}}
    m["innings"][0]["overs"][0]["deliveries"].extend([dict(wide), dict(wide)])
    plain = _match("2020-01-02", venue="Wide", first_runs=160, first_balls=120, first_wkts=2)
    priors = VenuePriors.build([m] * 5 + [plain]).get("Wide", "t20", "male", "2021-01-01")
    assert priors["n"] == 6
    # sum_runs = 122*5 + 160 = 770 over sum_overs = 20*6 = 120 overs.
    assert priors["par_run_rate"] == pytest.approx(770 / 120, abs=1e-3)
    assert priors["par_first_innings"] == pytest.approx(770 / 6, abs=1e-3)


def test_multiday_first_innings_counts_without_overs_cap() -> None:
    tests = [
        _match(d, venue="Red Ball", match_type="Test", first_runs=400, first_balls=540, first_wkts=10,
               second_runs=300, second_balls=600, second_wkts=10, overs=None)
        for d in _days(5)
    ]
    p = VenuePriors.build(tests).get("Red Ball", "multiday", "male", "2021-01-01")
    assert p["n"] == 5
    assert p["par_first_innings"] == pytest.approx(400.0)
    assert p["par_run_rate"] == pytest.approx(400 / 90.0, abs=1e-3)


# ---------------------------------------------------------------- city tiebreak


def test_ambiguous_ground_name_splits_by_city_only_once_both_cities_have_history() -> None:
    taunton = [_match(d, venue="County Ground", city="Taunton", first_runs=150) for d in _days(8, month=6)]
    bristol = [_match(d, venue="County Ground", city="Bristol", first_runs=190) for d in _days(6, month=6, year=2021)]
    priors = VenuePriors.build(taunton + bristol)

    # Before any Bristol match exists the name is not ambiguous, so the city does not split it.
    before = priors.get("County Ground", "t20", "male", "2021-03-01", city="Bristol")
    assert before["venue_n"] == 8
    assert before["par_first_innings"] == pytest.approx(150.0)

    # Once both cities have history, the city selects its own series.
    after_bristol = priors.get("County Ground", "t20", "male", "2022-01-01", city="Bristol")
    after_taunton = priors.get("County Ground", "t20", "male", "2022-01-01", city="Taunton")
    assert after_bristol["venue_n"] == 6
    assert after_bristol["par_first_innings"] == pytest.approx(190.0)
    assert after_taunton["venue_n"] == 8
    assert after_taunton["par_first_innings"] == pytest.approx(150.0)


def test_city_tiebreak_respects_as_of_for_the_second_city() -> None:
    taunton = [_match(d, venue="County Ground", city="Taunton", first_runs=150) for d in _days(8, month=6)]
    bristol = [_match(d, venue="County Ground", city="Bristol", first_runs=190) for d in _days(6, month=6, year=2021)]
    priors = VenuePriors.build(taunton + bristol)
    # Bristol's first match is 2021-06-01; as of 2021-06-01 it has no history yet (not ambiguous, merged group).
    p = priors.get("County Ground", "t20", "male", "2021-06-01", city="Bristol")
    assert p["venue_n"] == 8


# ---------------------------------------------------------------- snapshot


def test_snapshot_is_json_serialisable_and_lists_venues() -> None:
    matches = [_match(d, venue="M Chinnaswamy Stadium, Bengaluru", first_runs=185) for d in _days(6)]
    priors = VenuePriors.build(matches)
    snap = priors.snapshot()
    json.dumps(snap)  # must serialise
    # Venue-level series plus the city-split series (the fixture carries city "Testville").
    assert set(snap["venues"]) == {
        "v|m chinnaswamy stadium|t20|male",
        "vc|m chinnaswamy stadium|testville|t20|male",
    }
    entry = snap["venues"]["v|m chinnaswamy stadium|t20|male"]
    assert entry["par_score"] == pytest.approx(185.0)
    assert entry["sample_size"] == 6
    assert snap["last_match_date"] == "2020-01-06"


def test_venue_lookup_uses_alias_of_same_ground() -> None:
    matches = [_match(d, venue="M. Chinnaswamy Stadium", first_runs=190) for d in _days(5)]
    priors = VenuePriors.build(matches)
    a = priors.get("M Chinnaswamy Stadium, Bengaluru", "t20", "male", "2021-01-01")
    b = priors.get("Chinnaswamy Stadium", "t20", "male", "2021-01-01")
    assert a["fallback"] is False
    assert a["par_first_innings"] == b["par_first_innings"] == pytest.approx(190.0)
