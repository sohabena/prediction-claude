"""
Unit tests for ModelZoo and FormatRouter.
"""

import numpy as np
import pytest
from pathlib import Path

from rl.model_zoo import ModelZoo, get_model_zoo


def test_model_zoo_key_resolution():
    zoo = ModelZoo(models_dir=Path("tests/temp_models"))
    
    # IPL matches
    assert zoo.resolve_specialist_key(event_name="Indian Premier League") == "ipl"
    assert zoo.resolve_specialist_key(match_name="Chennai Super Kings vs Mumbai Indians") == "ipl"
    assert zoo.resolve_specialist_key(format_name="IPL") == "ipl"

    # Test matches
    assert zoo.resolve_specialist_key(format_name="Test") == "test"
    assert zoo.resolve_specialist_key(event_name="ICC World Test Championship") == "test"
    assert zoo.resolve_specialist_key(match_name="England vs Australia 1st Test") == "test"
    assert zoo.resolve_specialist_key(event_name="County Championship Division One") == "test"

    # ODI matches
    assert zoo.resolve_specialist_key(format_name="ODI") == "odi"
    assert zoo.resolve_specialist_key(event_name="ICC Men's Cricket World Cup") == "odi"
    assert zoo.resolve_specialist_key(match_name="India vs Australia 2nd ODI") == "odi"

    # T20 matches
    assert zoo.resolve_specialist_key(format_name="T20") == "t20i"
    assert zoo.resolve_specialist_key(format_name="T20I") == "t20i"
    assert zoo.resolve_specialist_key(event_name="ICC Men's T20 World Cup") == "t20i"
    assert zoo.resolve_specialist_key(match_name="India vs Pakistan T20") == "t20i"


def test_model_zoo_fallback_to_default():
    zoo = ModelZoo(models_dir=Path("tests/temp_models"))
    agent, key = zoo.get_agent_for_match(format_name="UnknownFormat")
    # If specialists not yet on disk, it falls back to the default champion or None
    assert key in ("default", "none")


def test_model_zoo_list_specialists():
    zoo = ModelZoo(models_dir=Path("tests/temp_models"))
    specialists = zoo.list_specialists()
    assert "t20i" in specialists
    assert "odi" in specialists
    assert "test" in specialists
    assert "ipl" in specialists
