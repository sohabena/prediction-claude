"""S107 part A: the players on the field, read from the live score feed (``match-center/score/v3``).

Covers the parser on the real livescore-v2 fixture and on edited copies (no batsmen, one batsman, bad numbers,
missing parts), the ``players`` field of the match_context row, the nullable JSONB column on the ORM model and the
ADD COLUMN IF NOT EXISTS migration in ``backend/db_init.py``.

Fakes and pure functions only: no database, Redis, browser or network.
"""

from __future__ import annotations

import copy
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.dialects.postgresql import JSONB

from backend import db_init
from backend.models.match import MatchContextRecord
from scraper.parsers.lotusbook_score import ScoreState, parse_score_v3, to_context_row

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "lotusbook" / "score_v3_real_live_36267578_20261103.json"
REAL_PAYLOAD = json.loads(FIXTURE.read_text(encoding="utf-8"))
REAL_MID = "lb_36267578"
BATTING = "Biratnagar Kings"
BOWLING = "Janakpur Bolts"
NOW = datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc)

REAL_PLAYERS = {
    "batsmen": [
        {"name": "Imran Sheikh", "runs": 0, "balls": 2, "strike_rate": 0.0, "on_strike": False},
        {"name": "Lokesh Bahadur Bam", "runs": 1, "balls": 1, "strike_rate": 100.0, "on_strike": True},
    ],
    "bowler": {"name": "Moeen Ali", "overs": 1.0, "wickets": 1, "runs": 2},
    "partnership": {"runs": 0, "balls": 2},
    "last_out": {"name": "Shehan Jayasuriya", "runs": 1, "balls": 3},
}


def _stats(payload: dict) -> dict:
    return payload["result"]["stats"]


def _edited(mutate) -> dict:
    payload = copy.deepcopy(REAL_PAYLOAD)
    mutate(_stats(payload))
    return payload


def _parse(mutate) -> ScoreState:
    state = parse_score_v3(_edited(mutate))
    assert state is not None, "the core score must still parse when only player fields change"
    return state


# ── 1. the real fixture ───────────────────────────────────────────────────────────


class TestRealFixture:
    def test_players_match_the_fixture_field_by_field(self):
        state = parse_score_v3(REAL_PAYLOAD)
        assert state.players == REAL_PLAYERS

    def test_the_core_score_is_unchanged_by_the_player_fields(self):
        state = parse_score_v3(REAL_PAYLOAD)
        assert (state.runs, state.wickets, state.overs, state.balls, state.innings) == (2, 1, 1.0, 6, 1)
        assert state.batting_team == BATTING

    def test_players_are_json_serialisable_for_jsonb(self):
        state = parse_score_v3(REAL_PAYLOAD)
        assert json.loads(json.dumps(state.players)) == REAL_PLAYERS

    def test_the_parser_does_not_change_the_payload(self):
        before = copy.deepcopy(REAL_PAYLOAD)
        parse_score_v3(REAL_PAYLOAD)
        assert REAL_PAYLOAD == before


# ── 2. missing parts are None, never estimated ─────────────────────────────────────


class TestMissingParts:
    def test_no_batsmen_key_gives_batsmen_none_and_keeps_the_rest(self):
        state = _parse(lambda s: s.pop("batsmen"))
        assert state.players["batsmen"] is None
        assert state.players["bowler"] == REAL_PLAYERS["bowler"]
        assert state.players["partnership"] == REAL_PLAYERS["partnership"]
        assert state.players["last_out"] == REAL_PLAYERS["last_out"]

    def test_batsmen_null_gives_none(self):
        state = _parse(lambda s: s.__setitem__("batsmen", None))
        assert state.players["batsmen"] is None

    def test_empty_batsmen_list_is_the_feeds_own_empty_list_not_none(self):
        state = _parse(lambda s: s.__setitem__("batsmen", []))
        assert state.players["batsmen"] == []

    def test_one_batsman_is_one_entry_and_is_not_padded(self):
        state = _parse(lambda s: s.__setitem__("batsmen", s["batsmen"][:1]))
        assert state.players["batsmen"] == [REAL_PLAYERS["batsmen"][0]]

    def test_no_bowler_gives_bowler_none(self):
        state = _parse(lambda s: s.pop("bowler"))
        assert state.players["bowler"] is None
        assert state.players["batsmen"] == REAL_PLAYERS["batsmen"]

    def test_no_partnership_fields_give_partnership_none(self):
        def drop(s):
            del s["battingTeam"]["partnerShipScore"]
        state = _parse(drop)
        assert state.players["partnership"] is None

    def test_null_last_batsman_out_gives_none(self):
        state = _parse(lambda s: s.__setitem__("lastBatsmanOut", None))
        assert state.players["last_out"] is None

    def test_no_stats_players_at_all_still_parses_the_score(self):
        def strip(s):
            for key in ("batsmen", "bowler", "lastBatsmanOut"):
                s.pop(key, None)
            del s["battingTeam"]["partnerShipScore"]
            del s["battingTeam"]["partnerShipBalls"]
        state = _parse(strip)
        assert state.players == {"batsmen": None, "bowler": None, "partnership": None, "last_out": None}
        assert state.runs == 2

    def test_legacy_body_has_no_players_because_it_has_no_score(self):
        assert parse_score_v3({"result": {"stats": {}, "scorePipeline": "legacy"}}) is None


# ── 3. bad numbers and names refuse the part that holds them ───────────────────────


class TestBadValues:
    def test_a_batsman_with_unreadable_runs_refuses_the_whole_batsmen_list(self):
        def bad(s):
            s["batsmen"][1]["batsmanRuns"] = "x"
        state = _parse(bad)
        assert state.players["batsmen"] is None

    def test_a_batsman_without_a_name_refuses_the_whole_batsmen_list(self):
        def bad(s):
            s["batsmen"][0]["name"] = "   "
        state = _parse(bad)
        assert state.players["batsmen"] is None

    def test_a_batsman_with_missing_balls_refuses_the_whole_batsmen_list(self):
        def bad(s):
            del s["batsmen"][0]["balls"]
        state = _parse(bad)
        assert state.players["batsmen"] is None

    def test_a_nan_strike_rate_becomes_none_and_the_batter_is_kept(self):
        def bad(s):
            s["batsmen"][1]["strikeRate"] = float("nan")
        state = _parse(bad)
        assert state.players["batsmen"][1]["strike_rate"] is None
        assert state.players["batsmen"][1]["name"] == "Lokesh Bahadur Bam"
        assert all(not math.isnan(b["strike_rate"]) for b in state.players["batsmen"] if b["strike_rate"] is not None)

    def test_a_non_boolean_on_strike_flag_becomes_none(self):
        def bad(s):
            s["batsmen"][0]["isOnStrike"] = "yes"
        state = _parse(bad)
        assert state.players["batsmen"][0]["on_strike"] is None
        assert state.players["batsmen"][1]["on_strike"] is True

    def test_a_missing_on_strike_flag_becomes_none(self):
        def bad(s):
            del s["batsmen"][1]["isOnStrike"]
        state = _parse(bad)
        assert state.players["batsmen"][1]["on_strike"] is None

    def test_bowler_overs_with_ball_digit_six_refuses_the_bowler(self):
        def bad(s):
            s["bowler"]["overs"] = "3.6"
        state = _parse(bad)
        assert state.players["bowler"] is None

    def test_bowler_overs_as_text_with_a_valid_ball_digit_is_read(self):
        state = _parse(lambda s: s["bowler"].__setitem__("overs", "3.4"))
        assert state.players["bowler"]["overs"] == 3.4

    def test_bowler_overs_as_a_boolean_refuses_the_bowler(self):
        state = _parse(lambda s: s["bowler"].__setitem__("overs", True))
        assert state.players["bowler"] is None

    def test_bowler_without_wickets_refuses_the_bowler_and_never_guesses_zero(self):
        def bad(s):
            del s["bowler"]["wickets"]
        state = _parse(bad)
        assert state.players["bowler"] is None

    def test_partnership_numbers_as_text_are_read(self):
        def text_numbers(s):
            s["battingTeam"]["partnerShipScore"] = "41"
            s["battingTeam"]["partnerShipBalls"] = "30"
        state = _parse(text_numbers)
        assert state.players["partnership"] == {"runs": 41, "balls": 30}

    def test_partnership_with_a_non_number_is_none(self):
        def bad(s):
            s["battingTeam"]["partnerShipBalls"] = "many"
        state = _parse(bad)
        assert state.players["partnership"] is None

    def test_last_out_without_balls_is_none(self):
        def bad(s):
            del s["lastBatsmanOut"]["balls"]
        state = _parse(bad)
        assert state.players["last_out"] is None


# ── 4. the match_context row and the stored field ──────────────────────────────────


class TestContextRow:
    def test_the_row_carries_players(self):
        state = parse_score_v3(REAL_PAYLOAD)
        row = to_context_row(state, REAL_MID, BATTING, BOWLING, observed_at=NOW)
        assert row["players"] == REAL_PLAYERS

    def test_the_row_keys_are_exactly_the_orm_columns(self):
        state = parse_score_v3(REAL_PAYLOAD)
        row = to_context_row(state, REAL_MID, BATTING, BOWLING, observed_at=NOW)
        assert set(row) == {c.name for c in MatchContextRecord.__table__.columns}

    def test_a_row_without_players_info_stores_none_for_each_part_not_a_guess(self):
        state = _parse(lambda s: s.pop("batsmen"))
        row = to_context_row(state, REAL_MID, BATTING, BOWLING, observed_at=NOW)
        assert row["players"]["batsmen"] is None
        assert row["players"]["bowler"] == REAL_PLAYERS["bowler"]

    def test_content_key_changes_when_a_batter_scores(self):
        before = parse_score_v3(REAL_PAYLOAD)

        def one_more(s):
            s["batsmen"][1]["batsmanRuns"] = "2"
            s["batsmen"][1]["balls"] = "2"
        after = _parse(one_more)
        assert before.content_key() != after.content_key()

    def test_content_key_ignores_the_feed_time_as_before(self):
        state = parse_score_v3(REAL_PAYLOAD)
        later = ScoreState(**{**state.__dict__, "updated_at": NOW})
        assert state.content_key() == later.content_key()


# ── 5. the column: nullable JSONB; the migration is additive ───────────────────────


class TestColumnAndMigration:
    def test_orm_column_is_nullable_jsonb(self):
        column = MatchContextRecord.__table__.columns["players"]
        assert column.nullable is True
        assert isinstance(column.type, JSONB)

    def test_a_record_built_without_players_is_still_valid(self):
        record = MatchContextRecord(time=NOW, match_id=REAL_MID)
        assert record.players is None

    def test_migration_adds_the_column_with_if_not_exists_and_no_default(self):
        statements = [s for s in db_init.MIGRATION_STATEMENTS if "match_context" in s and "players" in s]
        assert len(statements) == 1
        sql = " ".join(statements[0].split())
        assert sql == "ALTER TABLE match_context ADD COLUMN IF NOT EXISTS players JSONB"
        assert "NOT NULL" not in sql.upper() and "DEFAULT" not in sql.upper()

    def test_migration_is_listed_after_the_existing_ones(self):
        index = [i for i, s in enumerate(db_init.MIGRATION_STATEMENTS) if "players JSONB" in s]
        assert index == [len(db_init.MIGRATION_STATEMENTS) - 1]
