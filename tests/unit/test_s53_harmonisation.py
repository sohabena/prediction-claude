"""S53: harmonised match registry. Competition, team names, site-time parsing, and view SQL text.

No database is touched: the view SQL is checked structurally.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

import pytest
from lxml import html as lxml_html

from backend import db_init
from scraper.parsers.lotusbook_parser import (
    COMPETITION_BY_TEAM,
    LotusBookParser,
    SITE_TZ,
    competition_for_teams,
    parse_site_time,
)
from shared.naming import canonical_team_name

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "lotusbook" / "listing_page_20261103.html"

# Reference clock for the time tests: 2026-11-03 12:00 UTC, i.e. 17:30 on 03 Nov in India.
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- team names

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Rawalpindiz Srl", "Rawalpindiz SRL"),
        ("  Lahore   Qalandars  SRL ", "Lahore Qalandars SRL"),
        ("Hyderabad Kingsmen Srl", "Hyderabad Kingsmen SRL"),
        ("Hyderabad Kingsmen SRL", "Hyderabad Kingsmen SRL"),
        ("Hobart Hurricanes W", "Hobart Hurricanes W"),
        ("Srl Hunters", "Srl Hunters"),  # only a trailing token is normalised
        ("", ""),
        (None, ""),
    ],
)
def test_canonical_team_name(raw, expected):
    assert canonical_team_name(raw) == expected


def test_parser_cleans_team_names_with_canonical_rule():
    parser = LotusBookParser()
    assert parser._clean_team_name("Rawalpindiz  Srl ") == "Rawalpindiz SRL"


# ---------------------------------------------------------------- site time -> UTC

@pytest.mark.parametrize(
    "text, expected_utc",
    [
        # Listing text is India Standard Time. 10:00 PM IST on 03 Nov is 16:30 UTC.
        ("Today at 10:00 PM", datetime(2026, 11, 3, 16, 30, tzinfo=timezone.utc)),
        ("today 6:45 am", datetime(2026, 11, 3, 1, 15, tzinfo=timezone.utc)),
        # Tomorrow 12:15 PM IST is 06:45 UTC on 04 Nov.
        ("Tomorrow 12:15 PM", datetime(2026, 11, 4, 6, 45, tzinfo=timezone.utc)),
        # Match-page status: 04:30 IST on 03 Nov is 23:00 UTC on 02 Nov.
        ("03 NOV 04:30", datetime(2026, 11, 2, 23, 0, tzinfo=timezone.utc)),
        ("03 Nov | 04:30", datetime(2026, 11, 2, 23, 0, tzinfo=timezone.utc)),
        # Day-first listing date: 3:30 AM IST on 05 Nov is 22:00 UTC on 04 Nov.
        ("05-11-2026 3:30 AM", datetime(2026, 11, 4, 22, 0, tzinfo=timezone.utc)),
        # Naive ISO text is read in the site clock too.
        ("2026-11-03T04:30", datetime(2026, 11, 2, 23, 0, tzinfo=timezone.utc)),
    ],
)
def test_parse_site_time_returns_utc(text, expected_utc):
    parsed = parse_site_time(text, now=NOW)
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed == expected_utc
    assert parsed.utcoffset().total_seconds() == 0


def test_parse_site_time_unparseable_returns_none():
    assert parse_site_time("LIVE", now=NOW) is None
    assert parse_site_time("", now=NOW) is None
    assert parse_site_time(None, now=NOW) is None


def test_site_timezone_is_india():
    assert SITE_TZ.utcoffset(None).total_seconds() == 5.5 * 3600


def test_aware_datetime_is_converted_to_utc_and_naive_read_as_site_clock():
    parser = LotusBookParser()
    aware = datetime(2026, 11, 3, 4, 30, tzinfo=SITE_TZ)
    assert parser._parse_time(aware) == datetime(2026, 11, 2, 23, 0, tzinfo=timezone.utc)
    naive = datetime(2026, 11, 3, 4, 30)
    assert parser._parse_time(naive) == datetime(2026, 11, 2, 23, 0, tzinfo=timezone.utc)


def test_year_is_chosen_nearest_to_reference_clock():
    # Late December text with no year, read in early January, belongs to the previous year.
    jan = datetime(2027, 1, 2, 0, 0, tzinfo=timezone.utc)
    parsed = parse_site_time("30 DEC 10:00 PM", now=jan)
    assert parsed == datetime(2026, 12, 30, 16, 30, tzinfo=timezone.utc)


# ---------------------------------------------------------------- competition

def _fixture_rows() -> list[dict]:
    """Listing rows built from the saved listing fixture the way the scraper's extractor reads them.

    Team names come from the href (the extractor's fallback), odds from the first button of each row's
    odds block, and the scheduled text from the row's spans. One dict per distinct LotusBook match id.
    """
    doc = lxml_html.parse(str(FIXTURE)).getroot()
    rows: dict[str, dict] = {}
    for link in doc.xpath('//a[contains(@href,"/cricket/") and contains(@href,"vs")]'):
        href = link.get("href")
        lotus_id = href.rstrip("/").split("/")[-1]
        if lotus_id in rows:
            continue
        seg = unquote(href.split("/cricket/", 1)[1].split("/", 1)[0]).replace("+", " ")
        home, away = re.split(r"\s+vs\s+", seg, maxsplit=1)
        odds_div = link.getnext()
        buttons = odds_div.xpath('.//button[@data-odd-type]') if odds_div is not None else []
        # Same rule as the extractor's parseBtn: a price must be > 0, otherwise the market is suspended.
        prices = []
        for b in buttons[:6]:
            try:
                value = float(b.get("data-price") or 0)
            except ValueError:
                value = 0.0
            prices.append(value if value > 0 else None)
        prices += [None] * (6 - len(prices))
        scheduled = ""
        for span in link.xpath(".//span"):
            text = (span.text_content() or "").strip()
            if re.search(r"today|tomorrow|\d{1,2}[-/]\d{1,2}", text, re.I):
                scheduled = text
                break
        rows[lotus_id] = {
            "team_home": home,
            "team_away": away,
            "competition": "",
            "is_live": "LIVE" in (link.text_content() or ""),
            "lotus_id": lotus_id,
            "href": href,
            "scheduled_time": scheduled,
            "back_home": prices[0],
            "back_draw": prices[1],
            "back_away": prices[2],
            "lay_home": prices[3],
            "lay_draw": prices[4],
            "lay_away": prices[5],
        }
    return list(rows.values())


def _competitions_from_fixture() -> dict[str, str]:
    events = LotusBookParser().parse_dom_data(_fixture_rows())
    return {e.match_id: e.competition for e in events}


def test_fixture_has_eighteen_listing_rows_with_fifteen_matches():
    rows = _fixture_rows()
    assert len(rows) == 15
    assert all(r["team_home"] and r["team_away"] for r in rows)


def test_competition_for_three_fixture_matches():
    comp = _competitions_from_fixture()
    # Listing row "Today at 6:45 AM", Upcoming Events
    assert comp["lb_36267578"] == "Nepal Premier League"
    # Listing row "Tomorrow 8:10 AM", Upcoming Events (women's league, team names end " W")
    assert comp["lb_36266944"] == "Big Bash League, Women"
    # In Play row (SRL simulated listing name). Its prices are all 0 in the capture (suspended), so the
    # parser drops it as before; the mapping itself is checked directly.
    assert "lb_76110448" not in comp
    assert competition_for_teams("St Lucia Kings SRL", "Trinbago Knight Riders SRL") == "Caribbean Premier League SRL"


def test_unmapped_fixture_match_keeps_empty_competition():
    # Toronto Kings v Montreal Warriors (lb_76321652): neither team is in a known competition, so the
    # registry keeps "" rather than guessing. The listing and match page do not name it.
    comp = _competitions_from_fixture()
    assert comp["lb_76321652"] == ""


def test_every_fixture_match_gets_a_competition_string_or_empty():
    comp = _competitions_from_fixture()
    # 15 distinct matches in the fixture; the suspended St Lucia row (no prices, no start time) is dropped.
    assert len(comp) == 14
    assert all(isinstance(v, str) for v in comp.values())


def test_competition_needs_both_teams_in_the_same_competition():
    assert competition_for_teams("Biratnagar Kings", "Janakpur Bolts") == "Nepal Premier League"
    assert competition_for_teams("Biratnagar Kings", "Hobart Hurricanes W") == ""
    assert competition_for_teams("Toronto Kings", "Montreal Warriors") == ""


def test_listing_competition_attribute_wins_over_team_mapping():
    parser = LotusBookParser()
    row = _fixture_rows()[0]
    row["competition"] = "Explicit Label"
    events = parser.parse_dom_data([row])
    assert events and events[0].competition == "Explicit Label"


def test_competition_table_keys_are_canonical_lowercase():
    for key in COMPETITION_BY_TEAM:
        assert key == canonical_team_name(key).lower()


# ---------------------------------------------------------------- view SQL (structural)

def _view(name: str) -> str:
    for stmt in db_init.VIEW_STATEMENTS:
        if re.match(rf"\s*CREATE OR REPLACE VIEW {name}\s+AS", stmt):
            return stmt
    raise AssertionError(f"view {name} missing from VIEW_STATEMENTS")


def test_two_views_are_defined():
    assert len(db_init.VIEW_STATEMENTS) == 2
    _view("v_match_odds")
    _view("v_matches")


@pytest.mark.parametrize("name", ["v_match_odds", "v_matches"])
def test_view_sql_is_read_only_and_balanced(name):
    sql = _view(name)
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE TABLE)\b", sql, re.I)
    assert sql.count("(") == sql.count(")")
    assert sql.count("'") % 2 == 0


def test_v_match_odds_columns_and_sources():
    sql = _view("v_match_odds")
    for col in ("back_home", "lay_home", "back_away", "lay_away", "source"):
        assert re.search(rf"\b{col}\b", sql)
    assert "'listing'" in sql and "'match_page'" in sql
    assert "source = 'lotusbook'" in sql
    assert "market_type = 'match_odds'" in sql
    assert "DISTINCT ON (match_id, time)" in sql
    assert "UNION ALL" in sql
    assert sql.rstrip().endswith("ORDER BY match_id, time")


def test_v_match_odds_pivots_runners_by_normalised_team_name():
    sql = _view("v_match_odds")
    assert "r.home_key" in sql and "r.away_key" in sql
    assert sql.count("regexp_replace") >= 4
    assert "lower(btrim(" in sql


def test_v_matches_has_registry_and_tick_summary_columns():
    sql = _view("v_matches")
    for col in ("competition", "match_start_time", "is_simulated", "listing_ticks",
                "match_page_ticks", "first_tick_at", "last_tick_at", "result_winner",
                "result_completed_at"):
        assert col in sql, col
    assert "FROM match_training_status" in sql
    assert "LEFT JOIN match_results" in sql


def test_views_run_after_indexes_in_init():
    src = Path(db_init.__file__).read_text(encoding="utf-8")
    assert src.index('(s, "view")') > src.index('(s, "index")')
    assert "VIEW_STATEMENTS" in src
