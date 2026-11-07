"""S119: Strict Tier-1 real cricket gatekeeper tests.

Ensures the trading engine and signals stream exclusively trade premier international
and major franchise matches (WBBL, BBL, IPL, Tests, ODIs, T20Is) and strictly reject:
- All Simulated Reality Leagues (SRL, virtual, e-cricket, cyber)
- Minor/low-tier/obscure regional tournaments (Nepal Premier League, Victoria T20, etc.)
"""

from datetime import datetime, timedelta, timezone
import pytest

from scraper.match_filter import is_tier1_match
from virtual_trading import paper_trader as pt

NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)
START = NOW + timedelta(hours=1)


def _reg(mid: str, comp: str, home: str, away: str, is_sim: bool = False):
    return {
        "match_id": mid,
        "team_home": home,
        "team_away": away,
        "competition": comp,
        "match_start_time": START,
        "actual_start_time": None,
        "is_simulated": is_sim,
        "toss_winner": None,
        "toss_decision": None,
        "settled": False,
        "canonical_match_id": None,
        "bookmaker_only": False,
    }


def test_is_tier1_match_accepts_major_cricket():
    # Major Franchises
    assert is_tier1_match("WBBL", "Melbourne Stars W", "Sydney Sixers W")
    assert is_tier1_match("Women's Big Bash League", "Perth Scorchers W", "Adelaide Strikers W")
    assert is_tier1_match("Indian Premier League", "Chennai Super Kings", "Mumbai Indians")
    assert is_tier1_match("Tata IPL", "Royal Challengers Bangalore", "Kolkata Knight Riders")
    assert is_tier1_match("Big Bash League", "Sydney Sixers", "Melbourne Stars")
    assert is_tier1_match("Pakistan Super League", "Lahore Qalandars", "Karachi Kings")
    assert is_tier1_match("Caribbean Premier League", "Barbados Royals", "Trinbago Knight Riders")
    assert is_tier1_match("The Hundred", "Oval Invincibles", "Manchester Originals")
    assert is_tier1_match("SA20", "MI Cape Town", "Paarl Royals")

    # Major Internationals
    assert is_tier1_match("One Day Internationals", "New Zealand", "India")
    assert is_tier1_match("Test Matches", "Bangladesh", "West Indies")
    assert is_tier1_match("Womens International Twenty20 Matches", "Pakistan W", "Bangladesh W")
    assert is_tier1_match("ICC Men's T20 World Cup", "Australia", "England")
    assert is_tier1_match("ICC Cricket World Cup", "India", "South Africa")


def test_is_tier1_match_rejects_srl_and_obscure_leagues():
    # All SRL leagues must fail
    assert not is_tier1_match("Big Bash League SRL", "Adelaide Strikers SRL", "Brisbane Heat SRL")
    assert not is_tier1_match("Pakistan Super League SRL", "Peshawar Zalmi SRL", "Rawalpindiz SRL")
    assert not is_tier1_match("Premier League SRL", "Gujarat Titans SRL", "Royal Challengers Bangalore SRL")
    assert not is_tier1_match("T20 International SRL", "Australia SRL", "New Zealand SRL")
    assert not is_tier1_match("Super Smash SRL", "Central Districts SRL", "Canterbury SRL")

    # Minor / regional leagues must fail
    assert not is_tier1_match("Nepal Premier League", "Chitwan Rhinos", "Pokhara Avengers")
    assert not is_tier1_match("Victoria T20 Series, Women", "Uganda", "Rwanda")
    assert not is_tier1_match("Abu Dhabi T10", "UAE Bulls", "Yas Lions")
    assert not is_tier1_match("U19 Asia Cup", "Pakistan", "United Arab Emirates")
    assert not is_tier1_match("Ford Trophy", "Central Stags", "Canterbury Kings")
    assert not is_tier1_match("Pro50 Championship", "Tuskers", "MID West Rhinos")


def test_select_candidates_tier1_enforcement():
    registry = [
        # Tier-1: Accepted
        _reg("m1", "WBBL", "Melbourne Stars W", "Sydney Sixers W"),
        _reg("m2", "One Day Internationals", "New Zealand", "India"),
        # Non-Tier-1: Rejected
        _reg("m3", "Nepal Premier League", "Chitwan Rhinos", "Pokhara Avengers"),
        _reg("m4", "Big Bash League SRL", "Adelaide Strikers SRL", "Brisbane Heat SRL"),
        _reg("m5", "Victoria T20 Series, Women", "Uganda", "Rwanda"),
    ]
    last_prices = {f"m{i}": NOW for i in range(1, 6)}

    # When tier1_only=True (default in trading engine)
    candidates, skipped = pt.select_candidates(registry, last_prices, NOW, tier1_only=True)
    assert len(candidates) == 2
    assert [c.match_id for c in candidates] == ["m1", "m2"]
    assert skipped["not_tier1"] == 3
