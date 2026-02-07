"""Unit tests for LotusBook parser."""

from __future__ import annotations

import pytest

from scraper.parsers.lotusbook_parser import LotusBookParser


class TestLotusBookParser:
    """Tests for the LotusBookParser class."""

    def setup_method(self) -> None:
        self.parser = LotusBookParser()

    def test_parse_valid_dom_data(self) -> None:
        """Parser produces valid OddsEvent from well-formed DOM data."""
        raw = [
            {
                "team_home": "India",
                "team_away": "Australia",
                "competition": "ICC T20",
                "is_live": True,
                "back_home": 1.85,
                "lay_home": 1.90,
                "back_away": 2.10,
                "lay_away": 2.15,
                "back_draw": 15.0,
                "lay_draw": 18.0,
            }
        ]
        events = self.parser.parse_dom_data(raw)
        assert len(events) == 1
        event = events[0]
        assert event.team_home == "India"
        assert event.team_away == "Australia"
        assert event.back_home == 1.85
        assert event.is_live is True
        assert event.match_id  # non-empty

    def test_parse_missing_teams_skipped(self) -> None:
        """Entries without team names are skipped."""
        raw = [{"back_home": 1.85, "back_away": 2.10}]
        events = self.parser.parse_dom_data(raw)
        assert len(events) == 0

    def test_parse_invalid_odds_skipped(self) -> None:
        """Entries with out-of-range odds are skipped."""
        raw = [
            {
                "team_home": "India",
                "team_away": "Australia",
                "back_home": 0.5,  # Invalid: below 1.01
                "back_away": 2.10,
            }
        ]
        events = self.parser.parse_dom_data(raw)
        assert len(events) == 0

    def test_parse_spread_violation_skipped(self) -> None:
        """Entries where back > lay are skipped."""
        raw = [
            {
                "team_home": "India",
                "team_away": "Australia",
                "back_home": 2.00,
                "lay_home": 1.90,  # Invalid: back > lay
                "back_away": 2.10,
                "lay_away": 2.15,
            }
        ]
        events = self.parser.parse_dom_data(raw)
        assert len(events) == 0

    def test_dedup_identical_events(self) -> None:
        """Duplicate events for the same match are deduplicated."""
        raw = [
            {
                "team_home": "India",
                "team_away": "Australia",
                "back_home": 1.85,
                "lay_home": 1.90,
                "back_away": 2.10,
                "lay_away": 2.15,
            }
        ]
        events1 = self.parser.parse_dom_data(raw)
        events2 = self.parser.parse_dom_data(raw)  # Same data again
        assert len(events1) == 1
        assert len(events2) == 0  # Deduped

    def test_match_id_deterministic(self) -> None:
        """Same teams/competition always produce the same match_id."""
        raw = [
            {
                "team_home": "India",
                "team_away": "Australia",
                "competition": "ICC",
                "back_home": 1.85,
                "back_away": 2.10,
            }
        ]
        # Reset parser dedup for this test
        parser2 = LotusBookParser()
        events = parser2.parse_dom_data(raw)
        parser3 = LotusBookParser()
        events2 = parser3.parse_dom_data(raw)
        assert events[0].match_id == events2[0].match_id

    def test_validate_event_valid(self) -> None:
        """validate_event returns True for valid event."""
        from shared.schemas import OddsEvent
        from datetime import datetime, timezone

        event = OddsEvent(
            match_id="test",
            timestamp=datetime.now(timezone.utc),
            team_home="A",
            team_away="B",
            back_home=1.85,
            lay_home=1.90,
            back_away=2.10,
            lay_away=2.15,
        )
        assert self.parser.validate_event(event) is True

    def test_parse_odds_string_input(self) -> None:
        """_parse_odds handles string inputs correctly."""
        assert self.parser._parse_odds("1.85") == 1.85
        assert self.parser._parse_odds("2,500.00") is None  # Too high after comma removal
        assert self.parser._parse_odds(None) is None
        assert self.parser._parse_odds("abc") is None
