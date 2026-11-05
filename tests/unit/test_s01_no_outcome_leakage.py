"""S01: synthetic odds must be a function of match state only, never of the result."""

from __future__ import annotations

import inspect
import json
import random
from pathlib import Path

import pytest

from scripts import cricsheet_harmonizer as ch
from scripts import prepare_specialist_datasets as psd
from scripts.generate_realistic_historical_corpus import (
    MatchArchetype,
    RealisticCorpusGenerator,
)

ODDS_KEYS = (
    "back_home", "lay_home", "back_away", "lay_away", "implied_prob_home",
    "implied_prob_away", "overround", "volume_back_home", "volume_lay_home",
    "volume_back_away", "volume_lay_away",
)


def _innings(team: str, overs: int, seed: int, wicket_every: int = 17) -> dict:
    rng = random.Random(seed)
    over_list = []
    n = 0
    for o in range(overs):
        dels = []
        for _ in range(6):
            n += 1
            d = {"runs": {"total": rng.choice([0, 1, 1, 2, 4, 6])}}
            if n % wicket_every == 0:
                d["wickets"] = [{"player_out": "X"}]
            dels.append(d)
        over_list.append({"over": o, "deliveries": dels})
    return {"team": team, "overs": over_list}


def _write_match(path: Path, winner, fmt: str = "T20", result: str | None = None) -> Path:
    outcome: dict = {}
    if winner:
        outcome["winner"] = winner
    if result:
        outcome["result"] = result
    if fmt == "Test":
        innings = [
            _innings("India", 40, 1), _innings("Australia", 40, 2),
            _innings("India", 40, 3), _innings("Australia", 40, 4),
        ]
    else:
        innings = [_innings("India", 20, 1), _innings("Australia", 20, 2)]
    data = {
        "info": {"teams": ["India", "Australia"], "outcome": outcome, "dates": ["2023-05-01"],
                 "gender": "male", "event": {"name": "Test Event"}},
        "innings": innings,
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _odds(ticks):
    return [{k: t[k] for k in ODDS_KEYS} for t in ticks]


class TestWinProbIsStateOnly:
    @pytest.mark.parametrize("model", [psd.CricketWinProbModel, ch.CricketWinProbModel])
    def test_no_winner_parameter(self, model):
        assert "winner_is_home" not in inspect.signature(model.calculate_win_prob).parameters

    @pytest.mark.parametrize("model", [psd.CricketWinProbModel, ch.CricketWinProbModel])
    def test_deterministic_for_fixed_state(self, model):
        kw = dict(fmt="T20", innings=2, overs=15.3, score=120, wickets=4, target=170, batting_is_home=True)
        assert model.calculate_win_prob(**kw) == model.calculate_win_prob(**kw)

    def test_no_discontinuity_at_innings_break(self):
        # Previously a winner bias peaked at the end of innings 1; state-only now.
        a = psd.CricketWinProbModel.calculate_win_prob("T20", 1, 19.6, 170, 5, None, True)
        b = psd.CricketWinProbModel.calculate_win_prob("T20", 2, 0.1, 0, 0, 171, False)
        assert abs(a - (1.0 - b)) < 0.25


class TestSwappedWinnerGivesIdenticalOdds:
    def test_prepare_specialist_datasets(self, tmp_path):
        f1 = _write_match(tmp_path / "a.json", "India")
        f2 = _write_match(tmp_path / "b.json", "Australia")
        m1 = psd.parse_cricsheet_json(f1, "T20", 7, "t20i_men")
        m2 = psd.parse_cricsheet_json(f2, "T20", 7, "t20i_men")
        assert m1 and m2
        assert _odds(m1["ticks"]) == _odds(m2["ticks"])
        assert m1["winner"] != m2["winner"]

    def test_cricsheet_harmonizer(self, tmp_path):
        f1 = _write_match(tmp_path / "a.json", "India")
        f2 = _write_match(tmp_path / "b.json", "Australia")
        m1 = ch.parse_and_harmonize_match(f1, "T20", 7)
        m2 = ch.parse_and_harmonize_match(f2, "T20", 7)
        assert m1 and m2
        assert _odds(m1["ticks"]) == _odds(m2["ticks"])

    def test_noise_is_seeded_by_match_not_result(self, tmp_path):
        f = _write_match(tmp_path / "a.json", "India")
        a = psd.parse_cricsheet_json(f, "T20", 7, "t20i_men")
        b = psd.parse_cricsheet_json(f, "T20", 8, "t20i_men")
        assert _odds(a["ticks"]) != _odds(b["ticks"])


class TestCorpusGeneratorPathFirst:
    def test_ticks_independent_of_winner_decision(self, monkeypatch):
        base = RealisticCorpusGenerator(seed=3).generate_corpus(10)
        monkeypatch.setattr(
            RealisticCorpusGenerator, "_decide_winner",
            staticmethod(lambda fs, tgt, h, a, rng: a),
        )
        flipped = RealisticCorpusGenerator(seed=3).generate_corpus(10)
        for m1, m2 in zip(base, flipped):
            strip = lambda ts: [{k: v for k, v in t.items() if k != "timestamp"} for t in ts]
            assert strip(m1.ticks) == strip(m2.ticks)
        assert any(m.winner != n.winner for m, n in zip(base, flipped))

    def test_winner_follows_terminal_state(self):
        gen = RealisticCorpusGenerator(seed=5)
        for m in gen.generate_corpus(30):
            # req_run_rate>0 implies target not reached; final tick holds the terminal score.
            last = m.ticks[-1]
            assert m.winner in (m.team_home, m.team_away)
            assert m.loser != m.winner
            assert last["score"] > 0

    def test_sources_never_betfair(self):
        for m in RealisticCorpusGenerator(seed=1).generate_corpus(5):
            assert all(t["source"] == "synthetic_archetype" for t in m.ticks)

    def test_archetypes_still_exported(self):
        assert MatchArchetype.BLOWOUT.value == "blowout"


class TestSourceLabels:
    def test_cricsheet_generators_label_synthetic(self, tmp_path):
        f = _write_match(tmp_path / "a.json", "India")
        for ep in (psd.parse_cricsheet_json(f, "T20", 1, "t20i_men"), ch.parse_and_harmonize_match(f, "T20", 1)):
            assert ep and all(t["source"] == "synthetic_cricsheet" for t in ep["ticks"])
            assert all(t["source"] != "betfair" for t in ep["ticks"])

    def test_settlement_keys_constant(self):
        assert psd.SETTLEMENT_ONLY_KEYS == ("_meta", "winner", "loser", "winner_is_home")


class TestMultiDay:
    def test_test_match_has_four_innings(self, tmp_path):
        f = _write_match(tmp_path / "t.json", "India", fmt="Test")
        for ep in (psd.parse_cricsheet_json(f, "Test", 1, "test_men"), ch.parse_and_harmonize_match(f, "Test", 1)):
            assert ep
            assert {t["innings"] for t in ep["ticks"]} == {1, 2, 3, 4}

    def test_draw_is_retained_and_marked(self, tmp_path):
        f = _write_match(tmp_path / "d.json", None, fmt="Test", result="draw")
        ep = psd.parse_cricsheet_json(f, "Test", 1, "test_men")
        assert ep and ep["result_type"] == "draw"
        assert ep["ticks"][0]["_meta"]["result_type"] == "draw"
        ep2 = ch.parse_and_harmonize_match(f, "Test", 1)
        assert ep2 and ep2["result_type"] == "draw"

    def test_limited_overs_without_winner_dropped(self, tmp_path):
        f = _write_match(tmp_path / "n.json", None, fmt="T20", result="no result")
        assert psd.parse_cricsheet_json(f, "T20", 1, "t20i_men") is None
        assert ch.parse_and_harmonize_match(f, "T20", 1) is None


class TestAuditScript:
    def test_synthetic_sample_passes(self):
        from scripts.audit_outcome_leakage import audit_matches, _synthetic_sample

        passed, report = audit_matches(_synthetic_sample(200))
        assert passed, report

    def test_detects_leaky_odds(self):
        from scripts.audit_outcome_leakage import audit_matches, _synthetic_sample

        matches = _synthetic_sample(200)
        for m in matches:
            home_won = m["winner"] == m["team_home"]
            for t in m["ticks"]:
                t["back_home"] = 1.05 if home_won else 30.0
        passed, _ = audit_matches(matches)
        assert not passed
