"""Unit tests for match filter, SRL/cyber blacklist, and international verification."""

from datetime import datetime, timezone
import pytest

from scraper.match_filter import MatchClassifier
from shared.schemas import OddsEvent


def _make_event(team_home: str, team_away: str, competition: str = "") -> OddsEvent:
    return OddsEvent(
        event_id="test_event_1",
        match_id="test_match_1",
        team_home=team_home,
        team_away=team_away,
        competition=competition,
        timestamp=datetime.now(timezone.utc),
        is_live=True,
    )


def test_srl_team_name_is_rejected():
    classifier = MatchClassifier(enabled=True)
    
    # SRL in home team
    ev1 = _make_event("Hyderabad Kingsmen Srl", "Rawalpindiz")
    assert not classifier.qualifies(ev1)
    assert not classifier.is_international_or_major(ev1)

    # SRL in away team
    ev2 = _make_event("Lucknow Super Giants", "Mumbai Indians SRL")
    assert not classifier.qualifies(ev2)
    assert not classifier.is_international_or_major(ev2)

    # Simulated in competition
    ev3 = _make_event("India", "Australia", competition="Simulated Reality League")
    assert not classifier.qualifies(ev3)
    assert not classifier.is_international_or_major(ev3)


def test_tier1_match_qualifies():
    classifier = MatchClassifier(enabled=True)

    # International bilateral
    ev = _make_event("India", "Australia", competition="bilateral series")
    assert classifier.qualifies(ev)
    assert classifier.is_international_or_major(ev)

    # Major franchise (IPL)
    ev_ipl = _make_event("Chennai Super Kings", "Mumbai Indians", competition="Indian Premier League")
    assert classifier.qualifies(ev_ipl)
    assert classifier.is_international_or_major(ev_ipl)


def test_disabled_filter_allows_discovery_but_blocks_auto_approval():
    classifier = MatchClassifier(enabled=False)

    ev_srl = _make_event("Hyderabad Kingsmen Srl", "Rawalpindiz Srl")
    # Qualifies for discovery because filter is disabled (manual control mode)
    assert classifier.qualifies(ev_srl)
    # But is_international_or_major MUST STILL BE FALSE to prevent auto-approval
    assert not classifier.is_international_or_major(ev_srl)
