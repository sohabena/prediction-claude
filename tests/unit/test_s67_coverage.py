"""S67: Cricsheet coverage for every competition LotusBook lists.

Covers the family mapping for List A, T20 and first-class competitions, the single gender rule shared by the
training segments and the live ratings, the women's team-name normalisation for rating lookups, the Cricsheet
``raw/all`` dataset (classification, dedupe against the per-format folders), and the downloader's dry run, which
must make HEAD requests only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import scripts.download_cricsheet_data as dl
from scripts import train_fair_value as tfv
from strategy import live_context as lc
from training import segments as seg
from training.dataset import CricsheetIndex, match_family, match_record

ROOT = Path(__file__).resolve().parents[2]

# The 16 distinct competitions in data/training/20261103-0522/matches.jsonl, with their families.
RECORDED_COMPETITIONS = [
    ("Ford Trophy", "odi"),
    ("Emirates D20", "t20"),
    ("Nepal Premier League", "t20"),
    ("T20 Matches", "t20"),
    ("WBBL", "t20"),
    ("Afghanistan v Zimbabwe T20I Series 2026-27", "t20"),
    ("One Day Internationals", "odi"),
    ("NCL GT20", "t20"),
    ("Pakistan Super League SRL", "t20"),
    ("Premier League SRL", "t20"),
    ("Big Bash League SRL", "t20"),
    ("T20 PCB Super-3, Women", "t20"),
    ("T20 MCA Super Premier League", "t20"),
    ("T20 International SRL", "t20"),
    ("ICC Cricket World Cup League Two", "odi"),
]

# Named in the story: one-day, T20 tokens and first-class.
STORY_COMPETITIONS = [
    ("Royal London One-Day Cup", "odi"),
    ("Marsh Cup", "odi"),
    ("Super Smash", "t20"),
    ("Vitality Blast", "t20"),
    ("NCL D20", "t20"),
    ("Plunket Shield", "test"),
    ("Sheffield Shield", "test"),
    ("Ranji Trophy", "test"),
    ("County Championship Division One", "test"),
]


@pytest.mark.parametrize(("competition", "expected"), RECORDED_COMPETITIONS + STORY_COMPETITIONS)
def test_competition_maps_to_a_family(competition: str, expected: str) -> None:
    assert match_family(competition) == expected


def test_every_competition_in_the_recorded_dataset_has_a_family() -> None:
    paths = sorted((ROOT / "data" / "training").glob("*/matches.jsonl"))
    if not paths:
        pytest.skip("no recorded training dataset on this machine")
    unknown = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if match_family(row.get("competition")) == "unknown":
                unknown.append(row.get("competition"))
    assert unknown == []


def test_plunket_is_first_class_not_t20_or_odi() -> None:
    assert match_family("Plunket Shield") == "test"
    assert match_family("Plunket Shield", "Canterbury v Otago") == "test"


def test_conflicting_tokens_stay_unknown_even_with_a_match_type() -> None:
    assert match_family("T20 Test Series") == "unknown"
    assert match_family("T20 Test Series", match_type="ODI") == "unknown"


def test_unknown_label_falls_back_to_cricsheet_match_type() -> None:
    assert match_family("Emirates Cup", match_type="T20") == "t20"
    assert match_family("Emirates Cup", match_type="ODM") == "odi"
    assert match_family("Emirates Cup", match_type="MDM") == "test"
    assert match_family("Emirates Cup", match_type="ODM_UNKNOWN") == "unknown"
    assert match_family("Emirates Cup") == "unknown"


def test_match_record_uses_linked_match_type_only_when_the_label_is_silent() -> None:
    row = {
        "match_id": "lb_9",
        "team_home": "Dubai",
        "team_away": "Sharjah",
        "competition": "Emirates Cup",
        "match_start_time": "2026-11-03T10:00:00+00:00",
    }
    linked = {"cricsheet_match_id": "t20s/7", "reversed": False, "day_delta": 0, "match_type": "T20"}
    assert match_record(row, None, linked)["family"] == "t20"
    assert match_record(row, None, {**linked, "match_type": ""})["family"] == "unknown"
    labelled = {**row, "competition": "Ford Trophy"}
    assert match_record(labelled, None, linked)["family"] == "odi"


def test_cricsheet_index_reports_match_type_without_changing_link_detail() -> None:
    index = CricsheetIndex(
        [{"match_id": "all/42", "teams": ["Oman", "Netherlands"], "dates": ["2026-11-02"], "match_type": "ODI"}]
    )
    assert index.match_type_of("all/42") == "ODI"
    assert index.match_type_of("missing") == ""
    detail = index.link_detail(
        {"team_home": "Oman", "team_away": "Netherlands", "match_start_time": "2026-11-02T05:30:00+00:00"}
    )
    assert detail == {"cricsheet_match_id": "all/42", "reversed": False, "day_delta": 0}


# ------------------------------------------------------------------ gender rule
GENDER_CASES: list[tuple[str, str, str, str]] = [
    # competition, home, away, expected segment gender
    ("WBBL", "Melbourne Stars W", "Perth Scorchers W", "women"),
    ("Big Bash League SRL", "Sydney Thunder", "Brisbane Heat", "men"),
    ("WPL", "Mumbai Indians", "Delhi Capitals", "women"),
    ("Indian Premier League", "Delhi Capitals Women", "Mumbai Indians Women", "women"),
    ("Women's T20 Challenge", "India", "Australia", "women"),
    ("T20 PCB Super-3, Women", "Incredibles", "Warriors", "women"),
    ("Pakistan Super League SRL", "Rawalpindiz SRL", "Lahore Qalandars SRL", "men"),
    ("Ford Trophy", "Otago Volts", "Canterbury Kings", "men"),
    ("ICC Cricket World Cup League Two", "Oman", "Netherlands", "men"),
    ("T20I", "Afghanistan", "Zimbabwe", "men"),
    ("", "Sydney Sixers (W)", "Perth Scorchers W.", "women"),
    ("", "Warriors", "Wellington", "men"),
]


@pytest.mark.parametrize(("competition", "home", "away", "expected"), GENDER_CASES)
def test_one_gender_rule_for_segments_and_live_ratings(competition: str, home: str, away: str, expected: str) -> None:
    assert seg.gender_of(competition, home, away) == expected
    assert lc.gender_of(home, away, competition) == ("female" if expected == "women" else "male")


def test_womens_suffix_is_stripped_only_for_women_sides() -> None:
    assert seg.strip_womens_team("Sydney Thunder W") == "Sydney Thunder"
    assert seg.strip_womens_team("Delhi Capitals Women") == "Delhi Capitals"
    assert seg.strip_womens_team("Sydney Sixers (W)") == "Sydney Sixers"
    assert seg.strip_womens_team("Warriors") == "Warriors"
    assert seg.strip_womens_team("Oman") == "Oman"
    assert lc.ratings_team_name("Melbourne Stars W", "female") == "Melbourne Stars"
    assert lc.ratings_team_name("Melbourne Stars W", "male") == "Melbourne Stars W"


def test_bare_wbbl_resolves_to_female_for_segments_and_live_context() -> None:
    row = {"competition": "WBBL", "team_home": "Melbourne Stars W", "team_away": "Perth Scorchers W", "family": "t20"}
    assert "gender_women" in seg.segments_for(row)
    assert "gender_men" not in seg.segments_for(row)
    assert lc.gender_of("Melbourne Stars W", "Perth Scorchers W", "WBBL") == "female"


class _RecordingRatings:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def pre_match_live(self, home: str, away: str, family: str, gender: str, as_of: str, **_: Any) -> dict[str, float]:
        self.calls.append((home, away, family, gender, as_of))
        return {"pm_elo_home": 1500.0}


def test_women_rating_lookup_uses_the_men_name_with_female_gender() -> None:
    ratings = _RecordingRatings()
    tick = {
        "match_id": "lb_76073798",
        "team_home": "Sydney Thunder W",
        "team_away": "Perth Scorchers W",
        "competition": "WBBL",
        "family": "t20",
        "time": "2026-11-03T05:00:00+00:00",
    }
    fields = lc.prematch_fields(tick, ratings)
    assert fields == {"pm_elo_home": 1500.0}
    assert ratings.calls == [("Sydney Thunder", "Perth Scorchers", "t20", "female", "2026-11-03")]


def test_men_rating_lookup_keeps_the_name_as_recorded() -> None:
    ratings = _RecordingRatings()
    tick = {
        "match_id": "lb_1", "team_home": "India", "team_away": "Australia",
        "competition": "T20 International", "family": "t20", "time": "2026-11-03T05:00:00+00:00",
    }
    lc.prematch_fields(tick, ratings)
    assert ratings.calls == [("India", "Australia", "t20", "male", "2026-11-03")]


# ------------------------------------------------------------------ raw/all dataset
def _write_match(path: Path, match_type: str, gender: str = "male") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"info": {"match_type": match_type, "gender": gender}}), encoding="utf-8")


def test_raw_all_is_a_ratings_source_for_every_family_and_keeps_old_keys() -> None:
    assert "raw/all" in tfv.FAMILY_DIRS["t20"]
    assert "raw/all" in tfv.FAMILY_DIRS["odi"]
    assert "raw/all" in tfv.FAMILY_DIRS["multiday"]
    assert {"raw/ipl", "raw/t20s", "incoming_cricsheet"} <= set(tfv.FAMILY_DIRS["t20"])
    assert {"raw/odis", "incoming_cricsheet"} <= set(tfv.FAMILY_DIRS["odi"])
    assert {"raw/tests", "incoming_cricsheet"} <= set(tfv.FAMILY_DIRS["multiday"])


def test_match_present_in_raw_all_and_a_legacy_folder_is_read_once(tmp_path: Path) -> None:
    _write_match(tmp_path / "raw" / "all" / "1082591.json", "T20")
    _write_match(tmp_path / "raw" / "t20s" / "1082591.json", "T20")
    _write_match(tmp_path / "raw" / "all" / "1500001.json", "T20")
    _write_match(tmp_path / "raw" / "all" / "2000001.json", "ODI")
    dirs = [tmp_path / "raw" / "all", tmp_path / "raw" / "t20s"]

    t20_ids = sorted(m["match_id"] for m in tfv.iter_cricsheet(dirs, "t20"))
    odi_ids = sorted(m["match_id"] for m in tfv.iter_cricsheet(dirs, "odi"))

    assert t20_ids == ["1082591", "1500001"]
    assert odi_ids == ["2000001"]


def test_classify_info_maps_match_type_and_gender() -> None:
    assert dl.classify_info({"match_type": "T20", "gender": "female"}) == ("t20", "female")
    assert dl.classify_info({"match_type": "IT20", "gender": "male"}) == ("t20", "male")
    assert dl.classify_info({"match_type": "ODM"}) == ("odi", "unknown")
    assert dl.classify_info({"match_type": "MDM", "gender": "male"}) == ("test", "male")
    assert dl.classify_info({"match_type": "Hundred", "gender": "other"}) == ("unknown", "unknown")


def test_classify_archive_counts_without_extracting(tmp_path: Path) -> None:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("1.json", json.dumps({"info": {"match_type": "T20", "gender": "female"}}))
        zf.writestr("2.json", json.dumps({"info": {"match_type": "ODI", "gender": "male"}}))
        zf.writestr("README.txt", "not a match")
    counts = dl.classify_archive(buffer.getvalue())
    assert counts == {("t20", "female"): 1, ("odi", "male"): 1}


# ------------------------------------------------------------------ downloader dry run
class _Recorder:
    def __init__(self) -> None:
        self.methods: list[str] = []
        self.urls: list[str] = []

    def __call__(self, request: Any, timeout: float = 0) -> Any:  # urllib.request.urlopen stand-in
        self.methods.append(request.get_method())
        self.urls.append(request.full_url)
        if request.get_method() != "HEAD":
            raise AssertionError(f"non-HEAD request in dry run: {request.get_method()} {request.full_url}")

        class _Response:
            headers = {"Content-Length": "1048576"}

            def __enter__(self) -> "_Response":
                return self

            def __exit__(self, *exc: object) -> bool:
                return False

        return _Response()


def test_dry_run_makes_head_requests_only_and_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(dl.urllib.request, "urlopen", recorder)
    monkeypatch.setattr(dl, "DATA_RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(dl, "download_and_extract_archive", _fail_if_called)

    dl.main(["--dry-run"])

    assert recorder.methods == ["HEAD"]
    assert recorder.urls == ["https://cricsheet.org/downloads/all_json.zip"]
    assert not (tmp_path / "raw").exists()


def test_dry_run_with_legacy_lists_every_archive_by_head(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(dl.urllib.request, "urlopen", recorder)
    monkeypatch.setattr(dl, "DATA_RAW_DIR", tmp_path / "raw")

    dl.main(["--dry-run", "--legacy"])

    assert set(recorder.methods) == {"HEAD"}
    assert len(recorder.urls) == 5
    assert not (tmp_path / "raw").exists()


def _fail_if_called(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("dry run must not download or extract")


def test_all_archive_is_the_primary_dataset_and_legacy_archives_stay_available() -> None:
    assert dl.ARCHIVES["all"]["url"] == "https://cricsheet.org/downloads/all_json.zip"
    for key in ("t20s", "odis", "tests", "ipl"):
        assert key in dl.LEGACY_ARCHIVES
        assert key in dl.ARCHIVES
