"""
Unit tests for ModelZoo and FormatRouter.
Tests all 12 specialist formats, gender differentiation, and league routing.
"""

from pathlib import Path
import pytest

from rl.model_zoo import ModelZoo, get_model_zoo


def test_model_zoo_key_resolution():
    zoo = ModelZoo(models_dir=Path("tests/temp_models"))
    
    # 1. IPL matches
    assert zoo.resolve_specialist_key(event_name="Indian Premier League") == "ipl"
    assert zoo.resolve_specialist_key(match_name="Chennai Super Kings vs Mumbai Indians") == "ipl"
    assert zoo.resolve_specialist_key(format_name="IPL") == "ipl"

    # 2. BBL (Men) vs WBBL (Women)
    assert zoo.resolve_specialist_key(event_name="Big Bash League") == "bbl_men"
    assert zoo.resolve_specialist_key(event_name="KFC Big Bash League") == "bbl_men"
    assert zoo.resolve_specialist_key(event_name="Women's Big Bash League") == "wbbl_women"
    assert zoo.resolve_specialist_key(match_name="Sydney Sixers Women vs Perth Scorchers Women") == "wbbl_women"

    # 3. T20I (Men) vs T20I (Women)
    assert zoo.resolve_specialist_key(event_name="ICC Men's T20 World Cup") == "t20i_men"
    assert zoo.resolve_specialist_key(match_name="India vs Pakistan T20") == "t20i_men"
    assert zoo.resolve_specialist_key(event_name="ICC Women's T20 World Cup") == "t20i_women"
    assert zoo.resolve_specialist_key(match_name="Australia Women vs England Women T20I") == "t20i_women"

    # 4. ODI (Men) vs ODI (Women)
    assert zoo.resolve_specialist_key(event_name="ICC Men's Cricket World Cup") == "odi_men"
    assert zoo.resolve_specialist_key(match_name="India vs Australia 2nd ODI") == "odi_men"
    assert zoo.resolve_specialist_key(event_name="ICC Women's Cricket World Cup") == "odi_women"
    assert zoo.resolve_specialist_key(match_name="England Women vs South Africa Women ODI") == "odi_women"

    # 5. Test (Men) vs First-Class
    assert zoo.resolve_specialist_key(format_name="Test") == "test_men"
    assert zoo.resolve_specialist_key(event_name="ICC World Test Championship") == "test_men"
    assert zoo.resolve_specialist_key(match_name="England vs Australia 1st Test") == "test_men"
    assert zoo.resolve_specialist_key(event_name="County Championship Division One") == "first_class"
    assert zoo.resolve_specialist_key(event_name="Sheffield Shield") == "first_class"

    # 6. T20 Blast & Domestic T20
    assert zoo.resolve_specialist_key(event_name="Vitality Blast") == "t20_blast"
    assert zoo.resolve_specialist_key(event_name="Syed Mushtaq Ali Trophy") == "t20_blast"

    # 7. List A (Domestic 50-over)
    assert zoo.resolve_specialist_key(event_name="Royal London One-Day Cup") == "list_a"
    assert zoo.resolve_specialist_key(event_name="Vijay Hazare Trophy") == "list_a"

    # 8. Franchise T20
    assert zoo.resolve_specialist_key(event_name="Pakistan Super League") == "franchise_t20"
    assert zoo.resolve_specialist_key(event_name="Caribbean Premier League") == "franchise_t20"
    assert zoo.resolve_specialist_key(event_name="Bangladesh Premier League") == "franchise_t20"


def test_model_zoo_fallback_to_default():
    zoo = ModelZoo(models_dir=Path("tests/temp_models"))
    agent, key = zoo.get_agent_for_match(format_name="UnknownFormat")
    assert key in ("default", "none")


def test_model_zoo_list_specialists():
    zoo = ModelZoo(models_dir=Path("tests/temp_models"))
    specialists = zoo.list_specialists()
    
    # Check all 12 formats exist
    assert "ipl" in specialists
    assert "bbl_men" in specialists
    assert "wbbl_women" in specialists
    assert "t20i_men" in specialists
    assert "t20i_women" in specialists
    assert "odi_men" in specialists
    assert "odi_women" in specialists
    assert "test_men" in specialists
    assert "t20_blast" in specialists
    assert "franchise_t20" in specialists
    assert "first_class" in specialists
    assert "list_a" in specialists

    # Check backward compatibility aliases
    assert "t20i" in specialists
    assert "odi" in specialists
    assert "test" in specialists
