"""S98: the historical-odds record contract. Validation, the Betfair refusal, the store round trip (tmp_path only),
de-vig and the Cricsheet link. No database or network.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from training import historical_odds as ho
from training.dataset import CricsheetIndex

START = datetime(2025, 12, 14, 8, 15, tzinfo=timezone.utc)


def _record(**overrides) -> ho.OddsRecord:
    base = dict(
        source="aussportsbetting", source_event_id="bbl-2025-01", competition="Big Bash League", format="t20",
        gender="male", start_time_utc=START, home="Perth Scorchers", away="Sydney Sixers",
        bookmaker="aussportsbetting", kind="close", price_home=1.80, price_away=2.10, winner="home",
    )
    base.update(overrides)
    return ho.OddsRecord(**base)


def test_a_good_record_has_no_problems():
    assert ho.validate(_record()) == []


@pytest.mark.parametrize(
    "overrides, code",
    [
        ({"price_home": 1.0}, "bad_price"),
        ({"price_away": float("nan")}, "bad_price"),
        ({"start_time_utc": START.replace(tzinfo=None)}, "start_not_utc"),
        ({"away": " perth  scorchers "}, "same_team"),
        ({"home": ""}, "missing_team"),
        ({"kind": "closing"}, "unknown_kind"),
        ({"format": "t10"}, "unknown_format"),
        ({"winner": "draw"}, "unknown_winner"),
        ({"source": "oddsportal"}, "unknown_source"),
        ({"bookmaker": "betfair_ex_uk"}, "forbidden_bookmaker"),
    ],
)
def test_each_problem_has_its_code(overrides, code):
    assert code in ho.validate(_record(**overrides))


@pytest.mark.parametrize("name", ["betfair_ex_uk", "Betfair Exchange", "BETFAIR-SB", "betfair"])
def test_betfair_is_recognised_in_any_spelling(name):
    assert ho.is_betfair(name)


def test_pinnacle_is_not_betfair():
    assert not ho.is_betfair("pinnacle")


def test_store_round_trip(tmp_path):
    incoming = tmp_path / "bbl.xlsx"
    incoming.write_bytes(b"fake")
    records = [_record(), _record(source_event_id="bbl-2025-02", kind="open", winner=None, price_draw=None)]
    folder = ho.write_store("aussportsbetting", records, provenance="test file", inputs=[incoming], root=tmp_path)
    back, manifest = ho.read_store("aussportsbetting", root=tmp_path)
    assert back == records
    assert manifest["records"] == 2 and manifest["events"] == 2
    assert manifest["inputs"][0]["sha256"] == ho.sha256_of(incoming)
    assert (folder / ho.MANIFEST_FILE).exists()


def test_store_counts_problems_in_the_manifest(tmp_path):
    ho.write_store("aussportsbetting", [_record(price_home=0.9)], provenance="x", root=tmp_path)
    manifest = json.loads((tmp_path / "aussportsbetting" / ho.MANIFEST_FILE).read_text(encoding="utf-8"))
    assert manifest["problems"] == {"bad_price": 1}


def test_store_refuses_betfair_records(tmp_path):
    with pytest.raises(ho.ForbiddenSourceError):
        ho.write_store("oddspapi", [_record(source="oddspapi", bookmaker="betfair-ex")], provenance="x", root=tmp_path)
    assert not (tmp_path / "oddspapi").exists()


def test_store_refuses_a_betfair_provenance(tmp_path):
    with pytest.raises(ho.ForbiddenSourceError):
        ho.write_store("aussportsbetting", [_record()], provenance="Odds from Betfair", root=tmp_path)


def test_store_refuses_records_of_another_source(tmp_path):
    with pytest.raises(ValueError):
        ho.write_store("oddspapi", [_record()], provenance="x", root=tmp_path)


def test_missing_store_reads_empty(tmp_path):
    assert ho.read_store("the_odds_api", root=tmp_path) == ([], {})


def test_fair_probability_removes_the_margin():
    record = _record(price_home=1.90, price_away=1.90)
    assert ho.overround(record) == pytest.approx(2 / 1.9)
    assert ho.p_home_fair(record) == pytest.approx(0.5)
    assert ho.p_home_fair(replace(record, price_home=1.5, price_away=2.6)) > 0.6


def test_link_to_cricsheet_within_a_day_and_reversed():
    index = CricsheetIndex([
        {"match_id": "1410001", "teams": ["Sydney Sixers", "Perth Scorchers"], "dates": ["2025-12-15"],
         "match_type": "T20"},
    ])
    detail = ho.link_detail(_record(), index)
    assert detail == {"cricsheet_match_id": "1410001", "reversed": True, "day_delta": 1}
    assert ho.link_detail(_record(home="Hobart Hurricanes"), index) is None
