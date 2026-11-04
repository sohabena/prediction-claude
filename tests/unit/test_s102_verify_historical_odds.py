"""S102: the historical-odds verifier. Each check passes on clean synthetic data and fires on its fault. Fake Cricsheet
entries and synthetic records only; nothing is read from data/raw and nothing touches the database or the network.
"""

from __future__ import annotations

import importlib.util
import json
import math
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from training import historical_odds as ho
from training.historical_odds_verify import load_cricsheet_matches, render_markdown, verify

ROOT = Path(__file__).resolve().parent.parent.parent
START0 = datetime(2025, 1, 1, 6, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 11, 4, tzinfo=timezone.utc)
SOURCES = ho.SOURCES


def _start(i: int) -> datetime:
    return START0 + timedelta(hours=4 * i)


def _p(i: int) -> float:
    """A spread of pre-match home probabilities, 0.2 to 0.8."""
    return 0.2 + 0.6 * ((i * 37) % 101) / 100.0


def _u(i: int) -> float:
    """A low-discrepancy draw in [0, 1): outcomes follow the probability without random luck."""
    return (i * 0.6180339887498949) % 1.0


def _names(i: int) -> tuple[str, str]:
    return f"Club {i:04d} North", f"Club {i:04d} South"


def _cs(i: int, home_won: bool, *, match_type: str = "T20", gender: str = "male") -> dict:
    home, away = _names(i)
    return {
        "match_id": f"ipl/{i:05d}",
        "teams": [home, away],
        "dates": [_start(i).date().isoformat()],
        "match_type": match_type,
        "gender": gender,
        "event": "Synthetic Cup",
        "winner": home if home_won else away,
    }


def _rec(
    i: int,
    *,
    source: str = "aussportsbetting",
    bookmaker: str = "bookA",
    kind: str = "close",
    winner=None,
    price=None,
    names=None,
    snapshot=None,
    start=None,
    competition: str = "Synthetic Cup",
    fmt: str = "t20",
    gender: str = "male",
) -> ho.OddsRecord:
    """A record whose close is the market at probability ``_p(i)`` with a 5% margin, unless ``price`` is given."""
    home, away = names or _names(i)
    if price is None:
        p = _p(i)
        price = (1.0 / (p * 1.05), 1.0 / ((1.0 - p) * 1.05))
    return ho.OddsRecord(
        source=source,
        source_event_id=f"ev-{i:05d}",
        competition=competition,
        format=fmt,
        gender=gender,
        start_time_utc=start or _start(i),
        home=home,
        away=away,
        bookmaker=bookmaker,
        kind=kind,
        price_home=price[0],
        price_away=price[1],
        snapshot_time_utc=snapshot,
        winner=winner,
    )


def _world(n: int, *, miscalibrated: bool = False, source: str = "aussportsbetting"):
    """n events with a close, a Cricsheet match and a stated winner that agrees with Cricsheet.

    Clean: the home side wins with the market's own de-vigged probability, so the market is calibrated.
    Miscalibrated: favourites win more often than the price says (the probability is pushed towards 0 or 1).
    """
    records, cs = [], []
    for i in range(n):
        record = _rec(i, source=source)
        q = ho.p_home_fair(record)
        if miscalibrated:
            q = 1.0 / (1.0 + math.exp(-2.0 * math.log(q / (1.0 - q))))
        won = _u(i) < q
        records.append(replace(record, winner="home" if won else "away"))
        cs.append(_cs(i, won))
    return records, cs


def _item(result: dict, check: str, label: str) -> dict:
    matches = [item for item in result["checks"][check]["items"] if item["label"] == label]
    assert matches, f"no {label} item in {check}"
    return matches[0]


# --------------------------------------------------------------------------- clean data
def test_clean_two_sources_pass_every_check():
    records, cs = _world(1200)
    other = [replace(r, source="the_odds_api") for r in records]
    result = verify({"aussportsbetting": records, "the_odds_api": other}, cs, aliases={}, now=NOW)
    # The third store (oddspapi) is absent: an absent store is WARN by design, so only the two supplied stores count.
    not_ok = [
        (name, item["label"], item["note"])
        for name, check in result["checks"].items()
        for item in check["items"]
        if "oddspapi" not in item["label"] and item["status"] != "OK"
    ]
    assert not_ok == []
    assert _item(result, "calibration", "aussportsbetting")["numbers"]["matches"] == 1200
    slope = _item(result, "calibration", "aussportsbetting")["numbers"]["slope"]
    assert 0.8 <= slope <= 1.2


def test_clean_world_is_calibrated_and_its_slope_is_near_one():
    records, cs = _world(1200)
    result = verify({"aussportsbetting": records}, cs, aliases={}, now=NOW)
    item = _item(result, "calibration", "aussportsbetting")
    assert item["status"] == "OK"
    assert item["numbers"]["slope"] == pytest.approx(1.0, abs=0.2)
    assert len(item["rows"]) == 10


# --------------------------------------------------------------------------- schema
def test_betfair_record_is_a_schema_failure():
    records, cs = _world(20)
    records.append(_rec(99, bookmaker="betfair_ex_uk"))
    result = verify({"aussportsbetting": records}, cs + [_cs(99, True)], aliases={}, now=NOW)
    item = _item(result, "schema", "aussportsbetting")
    assert item["status"] == "FAIL"
    assert item["numbers"]["forbidden_bookmaker"] == 1
    assert result["checks"]["schema"]["status"] == "FAIL"
    assert result["status"] == "FAIL"


def test_two_percent_bad_prices_fail_the_schema_check():
    records, cs = _world(100)
    records[0] = replace(records[0], price_home=0.9)
    records[1] = replace(records[1], price_home=0.9)
    item = _item(verify({"aussportsbetting": records}, cs, aliases={}, now=NOW), "schema", "aussportsbetting")
    assert item["status"] == "FAIL"
    assert item["numbers"]["bad_price_share"] == pytest.approx(0.02)


def test_half_a_percent_bad_prices_only_warn():
    records, cs = _world(200)
    records[0] = replace(records[0], price_home=0.9)
    item = _item(verify({"aussportsbetting": records}, cs, aliases={}, now=NOW), "schema", "aussportsbetting")
    assert item["status"] == "WARN"


# --------------------------------------------------------------------------- duplicates
def test_duplicate_record_fails():
    records, cs = _world(10)
    records.append(records[3])
    item = _item(verify({"aussportsbetting": records}, cs, aliases={}, now=NOW), "duplicates", "aussportsbetting")
    assert item["status"] == "FAIL"
    assert item["numbers"]["duplicate_records"] == 1


# --------------------------------------------------------------------------- range consistency
def test_close_above_max_warns():
    close = _rec(0, kind="close", price=(2.0, 2.0))
    high = _rec(0, kind="max", price=(1.9, 2.5))
    result = verify({"aussportsbetting": [close, high]}, [_cs(0, True)], aliases={}, now=NOW)
    item = _item(result, "range_consistency", "aussportsbetting")
    assert item["status"] == "WARN"
    assert item["numbers"]["violations"] == 1
    assert item["examples"][0]["rule"] == "close <= max"


def test_min_above_open_warns():
    opening = _rec(0, kind="open", price=(2.0, 2.0))
    low = _rec(0, kind="min", price=(2.2, 2.0))
    result = verify({"aussportsbetting": [opening, low]}, [_cs(0, True)], aliases={}, now=NOW)
    item = _item(result, "range_consistency", "aussportsbetting")
    assert item["status"] == "WARN"
    assert item["examples"][0]["rule"] == "min <= open"


# --------------------------------------------------------------------------- overround
def test_overround_outside_the_band_on_many_records_warns():
    records = [_rec(i, price=(1.2, 1.2)) if i < 4 else _rec(i) for i in range(20)]
    cs = [_cs(i, True) for i in range(20)]
    item = _item(verify({"aussportsbetting": records}, cs, aliases={}, now=NOW), "overround", "aussportsbetting")
    assert item["status"] == "WARN"
    assert len(item["examples"]) == 4


# --------------------------------------------------------------------------- times
def test_snapshot_after_the_start_is_look_ahead_and_fails():
    record = _rec(0, snapshot=_start(0) + timedelta(hours=1))
    item = _item(verify({"aussportsbetting": [record]}, [_cs(0, True)], aliases={}, now=NOW),
                 "times", "aussportsbetting")
    assert item["status"] == "FAIL"
    assert item["numbers"]["snapshots_after_start"] == 1


def test_start_in_the_future_warns():
    record = _rec(0, start=datetime(2030, 1, 1, 10, 0, tzinfo=timezone.utc))
    item = _item(verify({"aussportsbetting": [record]}, [_cs(0, True)], aliases={}, now=NOW),
                 "times", "aussportsbetting")
    assert item["status"] == "WARN"
    assert item["numbers"]["starts_in_future"] == 1


# --------------------------------------------------------------------------- Cricsheet link
def test_low_link_rate_lists_the_unlinked_names():
    records = [_rec(i, names=(f"Zeta {i} Lions", f"Omega {i} Kings")) for i in range(5)]
    cs = [_cs(i, True) for i in range(5)]
    item = _item(verify({"aussportsbetting": records}, cs, aliases={}, now=NOW), "cricsheet_link", "aussportsbetting")
    assert item["status"] == "WARN"
    assert item["numbers"]["link_rate"] == 0
    names = {example["team"]: example["unlinked_events"] for example in item["examples"]}
    assert names["Zeta 0 Lions"] == 1
    assert len(item["rows"]) == 1 and item["rows"][0]["link_rate"] == 0


def test_team_alias_makes_the_link():
    names = ("Royal Challengers Bangalore", "Chennai Super Kings")
    days = [START0 + timedelta(days=i) for i in range(3)]  # one fixture per day: the same pair must not be ambiguous
    records = [_rec(i, names=names, start=days[i]) for i in range(3)]
    cs = [
        {**_cs(i, True), "teams": ["Chennai Super Kings", "Royal Challengers Bengaluru"],
         "dates": [days[i].date().isoformat()]}
        for i in range(3)
    ]
    aliases = {"Royal Challengers Bangalore": "Royal Challengers Bengaluru"}
    linked = _item(verify({"aussportsbetting": records}, cs, aliases=aliases, now=NOW),
                   "cricsheet_link", "aussportsbetting")
    assert linked["numbers"]["link_rate"] == 1.0
    unlinked = _item(verify({"aussportsbetting": records}, cs, aliases={}, now=NOW),
                     "cricsheet_link", "aussportsbetting")
    assert unlinked["numbers"]["link_rate"] == 0


# --------------------------------------------------------------------------- results
def test_a_wrong_winner_fails_the_result_check():
    records, cs = _world(20)
    records[5] = replace(records[5], winner="away" if records[5].winner == "home" else "home")
    item = _item(verify({"aussportsbetting": records}, cs, aliases={}, now=NOW), "results", "aussportsbetting")
    assert item["status"] == "FAIL"
    assert item["numbers"]["agreement_rate"] == pytest.approx(0.95)
    assert [example["event"] for example in item["examples"]] == ["ev-00005"]


def test_reversed_cricsheet_order_is_oriented_correctly():
    records, cs = [], []
    for i in range(20):
        home, away = _names(i)
        records.append(_rec(i, names=(away, home), winner="away"))  # Cricsheet lists `home` first and it won
        cs.append(_cs(i, True))
    item = _item(verify({"aussportsbetting": records}, cs, aliases={}, now=NOW), "results", "aussportsbetting")
    assert item["status"] == "OK"
    assert item["numbers"]["agree"] == 20


# --------------------------------------------------------------------------- calibration
def test_a_miscalibrated_source_warns_on_the_slope():
    records, cs = _world(1200, miscalibrated=True)
    item = _item(verify({"aussportsbetting": records}, cs, aliases={}, now=NOW), "calibration", "aussportsbetting")
    assert item["status"] == "WARN"
    assert item["numbers"]["slope"] > 1.2


def test_fewer_than_100_matches_warns_even_when_calibrated():
    records, cs = _world(50)
    item = _item(verify({"aussportsbetting": records}, cs, aliases={}, now=NOW), "calibration", "aussportsbetting")
    assert item["status"] == "WARN"
    assert item["numbers"]["matches"] == 50
    assert "fewer than 100" in item["note"]


def test_calibration_uses_one_close_per_event_from_the_busiest_bookmaker():
    records, cs = _world(120)
    extra = [_rec(i, bookmaker="bookB") for i in range(5)]  # fewer events: must not be used
    result = verify({"aussportsbetting": records + extra}, cs, aliases={}, now=NOW)
    assert _item(result, "calibration", "aussportsbetting")["numbers"]["bookmaker"] == "bookA"


# --------------------------------------------------------------------------- cross source
def test_two_sources_ten_points_apart_warn():
    a = [_rec(i, source="aussportsbetting", price=(2.0, 2.0)) for i in range(30)]
    b = [_rec(i, source="the_odds_api", price=(1.0 / 0.6, 1.0 / 0.4)) for i in range(30)]
    cs = [_cs(i, True) for i in range(30)]
    result = verify({"aussportsbetting": a, "the_odds_api": b}, cs, aliases={}, now=NOW)
    item = _item(result, "cross_source", "aussportsbetting vs the_odds_api")
    assert item["status"] == "WARN"
    assert item["numbers"]["median_abs_diff"] == pytest.approx(0.1, abs=0.005)


def test_identical_sources_agree():
    records, cs = _world(60)
    other = [replace(r, source="the_odds_api") for r in records]
    result = verify({"aussportsbetting": records, "the_odds_api": other}, cs, aliases={}, now=NOW)
    item = _item(result, "cross_source", "aussportsbetting vs the_odds_api")
    assert item["status"] == "OK"
    assert item["numbers"]["median_abs_diff"] == 0


# --------------------------------------------------------------------------- coverage
def test_coverage_counts_by_competition_season_format_and_gender():
    records = [
        _rec(0, competition="A League"),
        _rec(1, competition="A League"),
        _rec(2, competition="A League"),
        _rec(3, competition="B Cup", fmt="odi", gender="female"),
    ]
    cs = [_cs(i, True) for i in range(4)]
    item = _item(verify({"aussportsbetting": records}, cs, aliases={}, now=NOW), "coverage", "aussportsbetting")
    rows = {(r["competition"], r["format"], r["gender"]): r for r in item["rows"]}
    assert rows[("A League", "t20", "male")]["events"] == 3
    assert rows[("B Cup", "odi", "female")] == {
        "competition": "B Cup", "season": "2025", "format": "odi", "gender": "female", "events": 1, "records": 1,
    }


# --------------------------------------------------------------------------- empty and unreadable stores
def test_no_stores_at_all_is_a_warning_not_a_failure():
    result = verify({}, [], aliases={}, now=NOW)
    assert result["status"] == "WARN"
    assert set(result["sources"]) == set(SOURCES)
    assert all(check["status"] == "WARN" for check in result["checks"].values())


def test_a_store_that_cannot_be_read_fails():
    result = verify({}, [], aliases={}, now=NOW, read_errors={"oddspapi": "BadGzipFile: not gzipped"})
    assert _item(result, "readable", "oddspapi")["status"] == "FAIL"
    assert result["status"] == "FAIL"


# --------------------------------------------------------------------------- Cricsheet loader
def test_loader_keeps_outcome_gender_and_event_and_reports_unreadable(tmp_path):
    folder = tmp_path / "all"  # Cricsheet's every-match folder: the only one read (BBL is in no format folder)
    folder.mkdir()
    (folder / "1.json").write_text(json.dumps({"info": {
        "teams": ["A", "B"], "dates": ["2025-01-02"], "match_type": "T20", "gender": "female",
        "event": {"name": "Women's Premier"}, "outcome": {"winner": "B"},
    }}), encoding="utf-8")
    (folder / "2.json").write_text("{not json", encoding="utf-8")
    other = tmp_path / "other"
    other.mkdir()
    (other / "3.json").write_text(json.dumps({"info": {"teams": ["C", "D"]}}), encoding="utf-8")

    matches, unreadable = load_cricsheet_matches(tmp_path)

    assert [m["match_id"] for m in matches] == ["all/1"]
    assert matches[0]["gender"] == "female"
    assert matches[0]["event"] == "Women's Premier"
    assert matches[0]["winner"] == "B"
    assert unreadable == [str(folder / "2.json")]


def test_loader_gives_no_winner_for_a_match_without_a_decided_result(tmp_path):
    folder = tmp_path / "all"
    folder.mkdir()
    (folder / "9.json").write_text(json.dumps({"info": {
        "teams": ["A", "B"], "dates": ["2025-01-02"], "outcome": {"result": "no result"},
    }}), encoding="utf-8")
    matches, _ = load_cricsheet_matches(tmp_path)
    assert matches[0]["winner"] is None


# --------------------------------------------------------------------------- report text
def test_markdown_puts_the_failing_check_first():
    records, cs = _world(20)
    records.append(_rec(99, bookmaker="betfair_ex_uk"))
    result = verify({"aussportsbetting": records}, cs + [_cs(99, True)], aliases={}, now=NOW)
    text = render_markdown(result)
    headings = [line for line in text.splitlines() if line.startswith("## ") and ": " in line]
    first = headings[0]
    assert first.endswith(": FAIL")
    assert "**Overall: FAIL.**" in text


# --------------------------------------------------------------------------- script
def _load_script():
    script = ROOT / "scripts" / "verify_historical_odds.py"
    spec = importlib.util.spec_from_file_location("verify_historical_odds_under_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_store(root: Path, records) -> None:
    ho.write_store("aussportsbetting", records, provenance="synthetic test store", root=root)


def test_script_writes_both_reports_into_a_dated_folder(tmp_path):
    script = _load_script()
    records, _ = _world(5)
    _write_store(tmp_path / "store", records)
    result, folder = script.run(tmp_path / "store", tmp_path / "out", raw_dir=tmp_path / "raw",
                                aliases={}, now=NOW)
    assert folder.parent == tmp_path / "out"
    assert folder.name == "verify-2026-11-04"
    saved = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    assert saved["status"] == result["status"] == "WARN"
    assert saved["manifests"]["aussportsbetting"]["provenance"] == "synthetic test store"
    assert (folder / "report.md").read_text(encoding="utf-8").startswith("# Historical odds verification")


def test_script_exit_code_is_1_on_fail_and_0_otherwise(tmp_path, monkeypatch):
    script = _load_script()
    monkeypatch.setattr(script, "_team_alias", lambda: {})
    records, _ = _world(100)
    records[0] = replace(records[0], price_home=0.9)
    records[1] = replace(records[1], price_home=0.9)
    _write_store(tmp_path / "bad", records)
    args = ["--root", str(tmp_path / "bad"), "--out", str(tmp_path / "out"), "--raw-dir", str(tmp_path / "raw")]
    assert script.main(args) == 1

    clean, _ = _world(5)
    _write_store(tmp_path / "clean", clean)
    args = ["--root", str(tmp_path / "clean"), "--out", str(tmp_path / "out2"), "--raw-dir", str(tmp_path / "raw")]
    assert script.main(args) == 0
