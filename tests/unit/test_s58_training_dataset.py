"""S58: as-of price books, match facts, Cricsheet linking and dataset assembly.

Pure functions over plain dicts. No database, Redis or file system is touched.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from training.dataset import (
    STALE_LIMIT_S,
    CricsheetIndex,
    build_dataset,
    build_snapshots,
    group_by_match,
    match_family,
    match_record,
    summarise,
)

UTC = timezone.utc
START = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
MATCH = {
    "match_id": "lb_1",
    "team_home": "Oman",
    "team_away": "Netherlands",
    "competition": "ICC Cricket World Cup League Two",
    "match_start_time": START,
    "is_simulated": False,
    "training_status": "approved",
}


def tick(offset_s: float, source: str = "listing", **prices: float | None) -> dict:
    """A price row at START + offset_s with only the given fields set."""
    row = {"time": START + timedelta(seconds=offset_s), "source": source}
    for field in ("back_home", "lay_home", "back_away", "lay_away"):
        row[field] = prices.get(field)
    return row


# --------------------------------------------------------------------------- as-of books
def test_asof_book_never_reads_a_later_price() -> None:
    rows = [tick(-700, back_home=2.0), tick(-600, back_home=2.2), tick(30, back_home=9.0)]
    snap = build_snapshots(rows, MATCH, snapshot_offsets=[("X", -600)], inplay_every_s=0)[0]
    assert snap["t"] == (START - timedelta(seconds=600)).isoformat()
    assert snap["book"]["home"]["back"] == 2.2
    assert snap["book"]["home"]["stale_s_back"] == 0


def test_pre_match_zero_offset_is_last_instant_before_start() -> None:
    rows = [tick(-60, back_home=2.0), tick(0, back_home=3.0)]
    snap = next(s for s in build_snapshots(rows, MATCH, inplay_every_s=0) if s["label"] == "T-0")
    assert snap["book"]["home"]["back"] == 2.0
    assert snap["time_to_start_s"] == 0
    assert snap["in_play"] is False


def test_listing_back_and_match_page_lay_merge_into_one_book() -> None:
    rows = [
        tick(-600, source="listing", back_home=2.0),
        tick(-300, source="match_page", lay_home=2.2),
    ]
    snap = build_snapshots(rows, MATCH, snapshot_offsets=[("X", -300)], inplay_every_s=0)[0]
    home = snap["book"]["home"]
    assert home["back"] == 2.0 and home["stale_s_back"] == 300 and home["source_back"] == "listing"
    assert home["lay"] == 2.2 and home["stale_s_lay"] == 0 and home["source_lay"] == "match_page"
    assert snap["has_prices"] is True


def test_field_older_than_fifteen_minutes_is_dropped() -> None:
    rows = [
        tick(-1000, back_home=2.0),  # 940 s before the snapshot: stale
        tick(-900, lay_home=2.5),  # exactly 900 s before the snapshot: kept
    ]
    snap = build_snapshots(rows, MATCH, snapshot_offsets=[("X", -60)], inplay_every_s=0)[0]
    assert snap["book"]["home"]["back"] is None
    assert snap["book"]["home"]["stale_s_back"] is None
    assert snap["book"]["home"]["lay"] == 2.5
    assert snap["book"]["home"]["stale_s_lay"] == 840
    assert STALE_LIMIT_S == 900


def test_book_60s_and_10m_are_labels_that_may_look_ahead() -> None:
    rows = [tick(-600, back_home=2.0), tick(30, back_home=2.4), tick(500, back_home=2.8)]
    snap = build_snapshots(rows, MATCH, snapshot_offsets=[("X", -600)], inplay_every_s=0)[0]
    # The snapshot itself sees only the 2.0 price.
    assert snap["book"]["home"]["back"] == 2.0
    # 60 s after the snapshot is START - 540 s: still 2.0. 10 min after is START: 2.0 (the 2.4 row is later).
    assert snap["book_60s"]["home"]["back"] == 2.0
    assert snap["book_10m"]["home"]["back"] == 2.0
    later = build_snapshots(rows, MATCH, snapshot_offsets=[("X", 0)], inplay_every_s=0)[0]
    assert later["book_60s"]["home"]["back"] == 2.4  # row at +30 s is inside the 60 s window


def test_inplay_snapshots_every_two_minutes_until_last_price() -> None:
    rows = [tick(-60, back_home=2.0), tick(600, back_home=3.0)]
    snaps = build_snapshots(rows, MATCH, snapshot_offsets=[], inplay_every_s=120)
    in_play = [s for s in snaps if s["in_play"]]
    assert [s["label"] for s in in_play] == ["IP+0m", "IP+2m", "IP+4m", "IP+6m", "IP+8m", "IP+10m"]
    assert all(s["time_to_start_s"] <= 0 for s in in_play)
    assert in_play[0]["book"]["home"]["back"] == 2.0


def test_no_prices_or_no_start_gives_no_snapshots() -> None:
    assert build_snapshots([], MATCH) == []
    assert build_snapshots([tick(-60, back_home=2.0)], {**MATCH, "match_start_time": None}) == []


# --------------------------------------------------------------------------- match facts
def test_home_won_is_one_for_home_win_and_zero_for_away_win() -> None:
    home = match_record(MATCH, {"result_type": "win", "winner": "Oman", "source": "x"})
    away = match_record(MATCH, {"result_type": "win", "winner": "Netherlands", "source": "x"})
    assert home["home_won"] == 1 and home["winner_matched"] is True
    assert away["home_won"] == 0


@pytest.mark.parametrize(
    "result",
    [
        {"result_type": "tie", "winner": None, "source": "x"},
        {"result_type": "unresolved", "winner": "Oman", "source": "x"},
        {"result_type": "win", "winner": "Some Other Team", "source": "x"},
        None,
    ],
)
def test_home_won_is_none_for_tie_unresolved_unknown_or_missing(result) -> None:
    record = match_record(MATCH, result)
    assert record["home_won"] is None


def test_srl_matches_are_flagged_by_column_or_name() -> None:
    by_column = match_record({**MATCH, "is_simulated": True})
    by_name = match_record(
        {
            **MATCH,
            "team_home": "Adelaide Strikers SRL",
            "team_away": "Perth Scorchers SRL",
            "competition": "Big Bash League SRL",
            "is_simulated": False,
        }
    )
    real = match_record(MATCH)
    assert by_column["is_simulated"] is True
    assert by_name["is_simulated"] is True
    assert real["is_simulated"] is False


@pytest.mark.parametrize(
    ("competition", "expected"),
    [
        ("Test Matches", "test"),
        ("One Day Internationals", "odi"),
        ("Royal London One-Day Cup", "odi"),
        ("T20 International SRL", "t20"),
        ("Pakistan Super League SRL", "t20"),
        ("Ford Trophy", "odi"),  # 50-over List A (S67)
        ("", "unknown"),
    ],
)
def test_family_from_competition(competition: str, expected: str) -> None:
    assert match_family(competition) == expected


def test_conflicting_family_tokens_are_unknown() -> None:
    assert match_family("T20 Test Series") == "unknown"


# --------------------------------------------------------------------------- Cricsheet linking
def _index(teams: list[str], dates: list[str], match_id: str = "t20s/1") -> CricsheetIndex:
    return CricsheetIndex([{"match_id": match_id, "teams": teams, "dates": dates}])


def test_cricsheet_link_accepts_reversed_teams_within_one_day() -> None:
    index = _index(["Mumbai Indians", "Chennai Super Kings"], ["2026-04-10"])
    row = {**MATCH, "team_home": "Chennai Super Kings", "team_away": "Mumbai Indians",
           "match_start_time": datetime(2026, 4, 11, 0, 30, tzinfo=UTC)}
    detail = index.link_detail(row)
    assert detail == {"cricsheet_match_id": "t20s/1", "reversed": True, "day_delta": 1}


def test_cricsheet_link_rejects_more_than_one_day_apart() -> None:
    index = _index(["Oman", "Netherlands"], ["2026-04-10"])
    row = {**MATCH, "match_start_time": datetime(2026, 4, 12, 12, 0, tzinfo=UTC)}
    assert index.link_cricsheet(row) is None


def test_cricsheet_link_uses_team_alias_and_not_reversed_when_same_order() -> None:
    index = CricsheetIndex(
        [{"match_id": "ipl/9", "teams": ["Royal Challengers Bangalore", "Kolkata Knight Riders"], "dates": ["2026-04-10"]}],
        team_alias={"Royal Challengers Bangalore": "Royal Challengers Bengaluru"},
    )
    row = {**MATCH, "team_home": "Royal Challengers Bengaluru", "team_away": "Kolkata Knight Riders",
           "match_start_time": datetime(2026, 4, 10, 14, 0, tzinfo=UTC)}
    assert index.link_detail(row) == {"cricsheet_match_id": "ipl/9", "reversed": False, "day_delta": 0}


def test_cricsheet_link_is_none_when_two_matches_are_equally_close() -> None:
    index = CricsheetIndex(
        [
            {"match_id": "a/1", "teams": ["Oman", "Netherlands"], "dates": ["2026-04-10"]},
            {"match_id": "a/2", "teams": ["Oman", "Netherlands"], "dates": ["2026-04-10"]},
        ]
    )
    row = {**MATCH, "match_start_time": datetime(2026, 4, 10, 12, 0, tzinfo=UTC)}
    assert index.link_cricsheet(row) is None


def test_multi_day_test_match_links_on_any_listed_day() -> None:
    index = _index(["England", "India"], ["2026-07-01", "2026-07-02", "2026-07-03"], match_id="tests/7")
    row = {**MATCH, "team_home": "India", "team_away": "England",
           "match_start_time": datetime(2026, 7, 3, 4, 0, tzinfo=UTC)}
    assert index.link_cricsheet(row) == "tests/7"


# --------------------------------------------------------------------------- dataset assembly
def test_build_dataset_skips_matches_without_prices_and_reports_counts() -> None:
    other = {**MATCH, "match_id": "lb_2"}
    prices = group_by_match([{**tick(-600, back_home=2.0), "match_id": "lb_1"}])
    dataset = build_dataset([MATCH, other], prices, {})
    assert [m["match_id"] for m in dataset["matches"]] == ["lb_1"]
    assert dataset["stats"]["matches_without_prices"] == 1
    summary = summarise(dataset, price_rows_total=1)
    assert summary["matches"] == 1
    assert summary["matches_without_result"] == 1
    assert summary["matches_without_cricsheet_link"] == 1
    assert summary["snapshots_by_label"]["T-0"] == 1


def test_build_dataset_counts_matches_without_start_time() -> None:
    no_start = {**MATCH, "match_start_time": None}
    prices = {"lb_1": [{**tick(-600, back_home=2.0), "match_id": "lb_1"}]}
    dataset = build_dataset([no_start], prices, {})
    assert dataset["stats"]["matches_without_start"] == 1
    assert dataset["snapshots"] == []
    assert len(dataset["matches"]) == 1
