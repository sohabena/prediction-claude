"""S99: the aussportsbetting importer. Small workbooks are built with openpyxl in tmp_path; the script's dry run and
refusal paths are checked; nothing touches the network, the database or the repo's data folder.
"""

from __future__ import annotations

import csv
from datetime import date, datetime, time, timezone
from pathlib import Path

import pandas
import pytest
from openpyxl import Workbook

from scraper.importers import aussportsbetting as abb
from scripts import import_aussportsbetting as script
from training import historical_odds as ho

HEADER = [
    "Date", "Kick-off (local)", "Home Team", "Away Team", "Home Score", "Away Score",
    "Home Odds", "Away Odds", "Home Odds Open", "Away Odds Open", "Home Odds Min", "Away Odds Min",
    "Home Odds Max", "Away Odds Max", "Home Odds Close", "Away Odds Close", "Bookmakers Surveyed", "Notes",
]
NOTE_ROWS = [["Odds from aussportsbetting.com, several bookmakers."], ["Big Bash League 2025-26"], []]


def _match(**overrides):
    base = {
        "Date": date(2025, 12, 14), "Kick-off (local)": "19:40", "Home Team": "Perth Scorchers",
        "Away Team": "Sydney Sixers", "Home Score": 170, "Away Score": 150,
        "Home Odds": 1.85, "Away Odds": 2.05, "Home Odds Open": 1.90, "Away Odds Open": 2.00,
        "Home Odds Min": 1.75, "Away Odds Min": 1.95, "Home Odds Max": 1.95, "Away Odds Max": 2.15,
        "Home Odds Close": 1.80, "Away Odds Close": 2.10, "Bookmakers Surveyed": 12, "Notes": None,
    }
    base.update(overrides)
    return base


def _workbook(path: Path, rows, title: str = "Sheet1") -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title
    for row in rows:
        sheet.append(list(row))
    workbook.save(path)
    return path


def _file(path: Path, matches, header=HEADER, notes=NOTE_ROWS) -> Path:
    body = [list(header)] + [[match.get(name) for name in header] for match in matches]
    return _workbook(path, list(notes) + body)


def _parse(tmp_path: Path, matches=None, name="bbl_2025-26.xlsx", **kwargs) -> abb.ParseResult:
    matches = [_match()] if matches is None else matches
    return abb.parse_workbook(_file(tmp_path / name, matches, **kwargs))


def _by_kind(records):
    return {record.kind: record for record in records}


def test_note_rows_above_the_header_are_kept_and_the_odds_kinds_read(tmp_path):
    result = _parse(tmp_path)
    assert result.notes == ["Odds from aussportsbetting.com, several bookmakers.", "Big Bash League 2025-26"]
    assert {record.kind for record in result.records} == {"average", "open", "min", "max", "close"}
    assert result.events == 1 and len(result.records) == 5
    close = _by_kind(result.records)["close"]
    assert (close.price_home, close.price_away) == (1.80, 2.10)
    assert close.source == "aussportsbetting" and close.bookmaker == "aussportsbetting"
    assert close.competition == "Big Bash League" and close.gender == "male" and close.format == "t20"


def test_columns_are_found_by_name_not_by_position(tmp_path):
    expected = _parse(tmp_path, name="a.xlsx").records
    reversed_header = list(reversed(HEADER))
    shuffled = abb.parse_workbook(_file(tmp_path / "b.xlsx", [_match()], header=reversed_header))
    assert sorted(shuffled.records, key=lambda r: r.kind) == sorted(expected, key=lambda r: r.kind)


def test_missing_open_min_max_columns_record_only_what_is_present(tmp_path):
    header = [name for name in HEADER if not any(word in name for word in ("Open", "Min", "Max"))]
    result = abb.parse_workbook(_file(tmp_path / "no_extras.xlsx", [_match()], header=header))
    assert {record.kind for record in result.records} == {"average", "close"}
    assert result.counts.get("records") == 2


def test_excel_serial_dates_and_datetime_cells_both_read(tmp_path):
    serial = (date(2025, 12, 14) - date(1899, 12, 30)).days
    result = _parse(
        tmp_path,
        [
            _match(Date=serial, **{"Home Team": "Perth Scorchers", "Away Team": "Sydney Sixers"}),
            _match(Date=datetime(2025, 12, 15, 0, 0), **{"Home Team": "Hobart Hurricanes", "Away Team": "Brisbane Heat"}),
        ],
    )
    events = {record.source_event_id for record in result.records}
    assert events == {
        "2025-12-14_perth-scorchers_sydney-sixers",
        "2025-12-15_hobart-hurricanes_brisbane-heat",
    }


def test_start_time_is_sydney_local_converted_to_utc(tmp_path):
    result = _parse(
        tmp_path,
        [
            _match(**{"Kick-off (local)": "19:40", "Home Team": "Perth Scorchers", "Away Team": "Sydney Sixers"}),
            _match(**{"Kick-off (local)": time(13, 15), "Home Team": "Hobart Hurricanes", "Away Team": "Brisbane Heat"}),
            _match(**{"Kick-off (local)": None, "Home Team": "Adelaide Strikers", "Away Team": "Melbourne Stars"}),
        ],
    )
    starts = {record.source_event_id: record.start_time_utc for record in result.records}
    # December is AEDT (UTC+11): 19:40 local is 08:40 UTC the same day.
    assert starts["2025-12-14_perth-scorchers_sydney-sixers"] == datetime(2025, 12, 14, 8, 40, tzinfo=timezone.utc)
    assert starts["2025-12-14_hobart-hurricanes_brisbane-heat"] == datetime(2025, 12, 14, 2, 15, tzinfo=timezone.utc)
    # A date with no time becomes 09:00 UTC on that date.
    assert starts["2025-12-14_adelaide-strikers_melbourne-stars"] == datetime(2025, 12, 14, 9, 0, tzinfo=timezone.utc)
    assert all(record.start_time_utc.tzinfo is not None for record in result.records)


def test_invalid_prices_are_skipped_with_a_reason_and_other_kinds_kept(tmp_path):
    match = _match(**{"Home Odds Close": 0.9, "Home Odds Open": "n/a"})
    result = _parse(tmp_path, [match])
    assert {record.kind for record in result.records} == {"average", "min", "max"}
    reasons = [row["reason"] for row in result.skipped]
    assert "bad_price: close home 0.9" in reasons
    assert "bad_price: open home n/a" in reasons
    assert result.counts["bad_price"] == 2
    assert all(row["row"] == 5 for row in result.skipped)  # three note rows, then the header on row 4


def test_a_blank_price_is_counted_not_recorded(tmp_path):
    result = _parse(tmp_path, [_match(**{"Away Odds Max": None})])
    assert "max" not in {record.kind for record in result.records}
    assert result.counts["missing_price"] == 1


def test_winner_from_scores_and_from_a_result_column(tmp_path):
    header = HEADER + ["Winner"]
    matches = [
        _match(**{"Home Team": "Perth Scorchers", "Away Team": "Sydney Sixers", "Home Score": 170, "Away Score": 150,
                  "Winner": None}),
        _match(**{"Home Team": "Adelaide Strikers", "Away Team": "Brisbane Heat", "Home Score": 150,
                  "Away Score": 150, "Winner": None}),
        _match(**{"Date": date(2025, 12, 16), "Home Team": "Hobart Hurricanes", "Away Team": "Melbourne Stars",
                  "Home Score": 170, "Away Score": 150, "Winner": "Melbourne Stars"}),
        _match(**{"Date": date(2025, 12, 17), "Home Team": "Melbourne Renegades", "Away Team": "Sydney Thunder",
                  "Winner": "tie"}),
        _match(**{"Date": date(2025, 12, 18), "Home Team": "Brisbane Heat", "Away Team": "Hobart Hurricanes",
                  "Winner": "Sunrisers"}),
    ]
    result = abb.parse_workbook(_file(tmp_path / "winners.xlsx", matches, header=header))
    winners = {record.source_event_id: record.winner for record in result.records}
    assert winners["2025-12-14_perth-scorchers_sydney-sixers"] == "home"
    assert winners["2025-12-14_adelaide-strikers_brisbane-heat"] is None
    assert winners["2025-12-16_hobart-hurricanes_melbourne-stars"] == "away"
    assert winners["2025-12-17_melbourne-renegades_sydney-thunder"] == "tie"
    assert winners["2025-12-18_brisbane-heat_hobart-hurricanes"] is None
    assert result.counts["winner_from_scores"] == 1
    assert result.counts["winner_from_column"] == 2
    assert result.counts["winner_unrecognised"] == 1


def test_event_ids_are_stable_across_runs_and_start_time_changes(tmp_path):
    first = _parse(tmp_path, name="first.xlsx")
    second = _parse(tmp_path, [_match(**{"Kick-off (local)": "13:10"})], name="second.xlsx")
    assert first.records[0].source_event_id == "2025-12-14_perth-scorchers_sydney-sixers"
    assert {r.source_event_id for r in first.records} == {r.source_event_id for r in second.records}


def test_team_names_are_whitespace_normalised(tmp_path):
    result = _parse(tmp_path, [_match(**{"Home Team": "  Perth   Scorchers ", "Away Team": "Sydney Sixers"})])
    assert result.records[0].home == "Perth Scorchers"
    assert result.teams == ["Perth Scorchers", "Sydney Sixers"]


def test_unknown_columns_are_listed(tmp_path):
    header = HEADER[:-1] + ["Venue"]
    matches = [_match(Venue="Perth Stadium")]
    result = abb.parse_workbook(_file(tmp_path / "venue.xlsx", matches, header=header))
    assert result.unknown_columns == ["Venue"]


def test_womens_file_name_sets_the_competition_and_gender(tmp_path):
    result = _parse(tmp_path, name="wbbl_2025-26.xlsx")
    assert result.competition == "Women's Big Bash League"
    assert result.gender == "female"
    assert {record.gender for record in result.records} == {"female"}
    assert result.warnings == []


def test_unnamed_file_is_assumed_bbl_with_a_warning(tmp_path):
    result = _parse(tmp_path, name="odds_export.xlsx")
    assert (result.competition, result.gender) == ("Big Bash League", "male")
    assert any("assumed Big Bash League" in text for text in result.warnings)


def test_a_note_can_define_plain_odds_as_closing_prices(tmp_path):
    header = [name for name in HEADER if "Close" not in name]
    notes = [["Home Odds and Away Odds are closing prices."]]
    result = abb.parse_workbook(_file(tmp_path / "plain.xlsx", [_match()], header=header, notes=notes))
    assert result.plain_odds_kind == "close"
    assert "close" in {record.kind for record in result.records}
    assert "average" not in {record.kind for record in result.records}


def test_a_note_about_the_close_column_does_not_change_plain_odds(tmp_path):
    notes = [["Home Odds Close is the closing price."]]
    result = _parse(tmp_path, notes=notes)
    assert result.plain_odds_kind == "average"
    assert "average" in {record.kind for record in result.records}


def test_rows_without_a_team_or_a_readable_date_are_skipped(tmp_path):
    matches = [
        _match(**{"Away Team": None}),
        _match(**{"Date": "soon, maybe", "Home Team": "Hobart Hurricanes", "Away Team": "Brisbane Heat"}),
        _match(**{"Date": None, "Home Team": "Adelaide Strikers", "Away Team": "Melbourne Stars"}),
    ]
    result = _parse(tmp_path, matches)
    assert result.records == []
    reasons = [row["reason"].split(":")[0] for row in result.skipped]
    assert reasons == ["missing_team", "bad_date", "missing_date"]


def test_the_same_match_twice_in_one_file_is_skipped_once(tmp_path):
    result = _parse(tmp_path, [_match(), _match()])
    assert result.events == 1
    assert result.counts["duplicate_event"] == 1
    assert len(result.records) == 5


def test_a_bad_start_time_skips_the_row(tmp_path):
    result = _parse(tmp_path, [_match(**{"Kick-off (local)": "after lunch"})])
    assert result.records == []
    assert result.skipped[0]["reason"].startswith("bad_time")


def test_a_csv_file_with_text_numbers_is_read(tmp_path):
    path = tmp_path / "bbl.csv"
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER[:8])
        writer.writerow(["2025-12-14", "19:40", "Perth Scorchers", "Sydney Sixers", "170", "150", "1.85", "2.05"])
    result = abb.parse_workbook(path)
    assert {record.kind for record in result.records} == {"average"}
    assert result.records[0].price_home == 1.85
    assert result.records[0].winner == "home"


def test_a_workbook_without_a_home_away_header_is_an_error(tmp_path):
    path = _workbook(tmp_path / "other.xlsx", [["Team", "Score"], ["Perth Scorchers", 170]])
    with pytest.raises(ValueError, match="Home Team"):
        abb.parse_workbook(path)


def test_a_sheet_without_the_header_is_ignored_with_a_warning(tmp_path):
    workbook = Workbook()
    notes_sheet = workbook.active
    notes_sheet.title = "About"
    notes_sheet.append(["Just a cover sheet"])
    data = workbook.create_sheet("Odds")
    data.append(HEADER)
    data.append([_match()[name] for name in HEADER])
    path = tmp_path / "two_sheets.xlsx"
    workbook.save(path)
    result = abb.parse_workbook(path)
    assert result.events == 1
    assert any("'About'" in text for text in result.warnings)


def test_xls_without_xlrd_gives_a_clear_error(tmp_path, monkeypatch):
    path = tmp_path / "old.xls"
    path.write_bytes(b"not really an xls")

    def no_xlrd(*args, **kwargs):
        raise ImportError("No module named 'xlrd'")

    monkeypatch.setattr(pandas, "read_excel", no_xlrd)
    with pytest.raises(ValueError, match="xlrd"):
        abb.parse_workbook(path)


def test_dry_run_writes_nothing(tmp_path, capsys):
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    _file(incoming / "bbl.xlsx", [_match()])
    root = tmp_path / "store"
    code = script.main(["--incoming", str(incoming), "--root", str(root), "--dry-run"])
    assert code == 0
    assert not root.exists()
    assert "dry run" in capsys.readouterr().out


def test_the_script_writes_the_store_with_provenance_and_hashes(tmp_path):
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    source = _file(incoming / "bbl.xlsx", [_match(), _match(**{"Home Team": "Hobart Hurricanes",
                                                                "Away Team": "Brisbane Heat"})])
    root = tmp_path / "store"
    code = script.main(["--incoming", str(incoming), "--root", str(root)])
    assert code == 0
    records, manifest = ho.read_store("aussportsbetting", root=root)
    assert len(records) == 10 and manifest["events"] == 2
    assert "aussportsbetting.com" in manifest["provenance"]
    assert "Odds from aussportsbetting.com, several bookmakers." in manifest["provenance"]
    assert manifest["inputs"][0]["sha256"] == ho.sha256_of(source)
    assert manifest["import"]["files"][0]["name"] == "bbl.xlsx"


def test_a_betfair_note_is_refused_in_a_dry_run_and_a_real_run(tmp_path, capsys):
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    notes = NOTE_ROWS + [["Prices taken from Betfair Exchange closing odds."]]
    _file(incoming / "bbl.xlsx", [_match()], notes=notes)
    root = tmp_path / "store"

    assert script.main(["--incoming", str(incoming), "--root", str(root), "--dry-run"]) == 2
    assert script.main(["--incoming", str(incoming), "--root", str(root)]) == 2
    assert not root.exists()
    output = capsys.readouterr().out
    assert "REFUSED" in output and "Betfair" in output


def test_two_files_with_the_same_match_stop_the_import(tmp_path):
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    _file(incoming / "bbl_a.xlsx", [_match()])
    _file(incoming / "bbl_b.xlsx", [_match(**{"Kick-off (local)": "13:10"})])
    root = tmp_path / "store"
    assert script.main(["--incoming", str(incoming), "--root", str(root)]) == 1
    assert not root.exists()


def test_an_empty_incoming_folder_is_reported(tmp_path, capsys):
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    assert script.main(["--incoming", str(incoming), "--root", str(tmp_path / "store")]) == 1
    assert "no .xlsx" in capsys.readouterr().out


def test_the_real_file_layout_side_letters_winner_codes_and_bookmaker_groups(tmp_path):
    """2026-11-04, the operator's file: "Home Team (H)", "Time (Local)", Winner H/A/D/V, and a group row naming
    Odds Portal over the plain odds and bet365 over Open/Min/Max/Close."""
    from openpyxl import Workbook

    header = ["Date", "Time\n(Local)", "Home Team (H)", "Away Team (A)", "Winner", "Home\nOdds", "Away\nOdds",
              "Home Odds Open", "Home Odds Min", "Home Odds Max", "Home Odds Close",
              "Away Odds Open", "Away Odds Min", "Away Odds Max", "Away Odds Close"]
    group = [None] * 5 + ["Odds Portal", None, "bet365 Odds"] + [None] * 7
    wb = Workbook()
    ws = wb.active
    ws.append(group)
    ws.append(header)
    ws.append([datetime(2026, 1, 25), time(16, 15), "Perth Scorchers", "Sydney Sixers", "H", 1.56, 2.31,
               1.66, 1.56, 1.70, 1.56, 2.13, 2.13, 2.45, 2.45])
    ws.append([datetime(2026, 1, 23), time(19, 15), "Sydney Sixers", "Hobart Hurricanes", "V", 1.70, 2.07,
               None, None, None, None, None, None, None, None])
    path = tmp_path / "big_bash_league.xlsx"
    wb.save(path)

    result = abb.parse_workbook(path)
    by_kind = {(r.source_event_id[:10], r.kind): r for r in result.records}
    assert by_kind[("2026-01-25", "average")].bookmaker == "odds_portal"
    assert by_kind[("2026-01-25", "close")].bookmaker == "bet365"
    assert by_kind[("2026-01-25", "close")].price_away == 2.45
    assert by_kind[("2026-01-25", "close")].winner == "home"
    assert by_kind[("2026-01-23", "average")].winner == "no_result"
    assert ("2026-01-23", "close") not in by_kind
    assert result.skipped == []
