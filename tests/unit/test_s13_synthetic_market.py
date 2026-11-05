"""S13: synthetic market carries no free edge; null-market test and edge audit."""

from __future__ import annotations

import json
import random
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import cricsheet_harmonizer, prepare_specialist_datasets  # noqa: E402
from scripts.audit_synthetic_edge import audit_matches, main as audit_main  # noqa: E402
from scripts.prepare_specialist_datasets import parse_cricsheet_json  # noqa: E402
from scripts.synthetic_market import MarketNoise  # noqa: E402

TEAMS = ("Alpha XI", "Beta XI")


def _fake_cricsheet(seed: int) -> Dict[str, Any]:
    """A plausible T20 ball-by-ball file; winner follows from the simulated scores."""
    rng = random.Random(seed)
    strength = rng.gauss(0.0, 0.1)

    def innings(team: str, target: int | None) -> tuple[Dict[str, Any], int]:
        overs: List[Dict[str, Any]] = []
        runs = wk = 0
        for o in range(20):
            dels = []
            for _ in range(6):
                r = rng.choices([0, 1, 2, 4, 6], weights=[30, 38, 8, 14, 10])[0]
                dd: Dict[str, Any] = {"runs": {"total": r}}
                if rng.random() < 0.05 - strength * 0.1:
                    dd["wickets"] = [{"player_out": "x"}]
                    wk += 1
                runs += r
                dels.append(dd)
                if wk >= 10 or (target is not None and runs >= target):
                    break
            overs.append({"over": o, "deliveries": dels})
            if wk >= 10 or (target is not None and runs >= target):
                break
        return {"team": team, "overs": overs}, runs

    inn1, r1 = innings(TEAMS[0], None)
    inn2, r2 = innings(TEAMS[1], r1 + 1)
    winner = TEAMS[1] if r2 > r1 else TEAMS[0]
    return {
        "info": {
            "teams": list(TEAMS), "outcome": {"winner": winner}, "gender": "male",
            "dates": ["2024-01-01"], "event": {"name": "Test Cup"},
        },
        "innings": [inn1, inn2],
    }


def _make_corpus(tmp_path: Path, n: int, noise_mode: str, **noise_kw: float) -> List[Dict[str, Any]]:
    matches = []
    for i in range(1, n + 1):
        fp = tmp_path / f"m{i}.json"
        if not fp.exists():
            fp.write_text(json.dumps(_fake_cricsheet(i)), encoding="utf-8")
        m = parse_cricsheet_json(fp, "T20", i, "t20i_men", noise_mode=noise_mode, **noise_kw)
        if m is not None:
            matches.append(m)
    return matches


class TestSingleDefinition:
    def test_one_market_noise_class(self):
        hits = [
            p for p in (ROOT / "scripts").glob("*.py")
            if "class MarketNoise" in p.read_text(encoding="utf-8", errors="ignore")
        ]
        assert [h.name for h in hits] == ["synthetic_market.py"]

    def test_reexports_are_same_objects(self):
        assert prepare_specialist_datasets.MarketNoise is MarketNoise
        assert cricsheet_harmonizer.MarketNoise is MarketNoise
        assert prepare_specialist_datasets.CricketWinProbModel is cricsheet_harmonizer.CricketWinProbModel
        assert prepare_specialist_datasets.synthesize_exchange_ticks is cricsheet_harmonizer.synthesize_exchange_ticks


class TestMarketNoise:
    def test_modes(self):
        mart = MarketNoise(1, noise_mode="martingale", latent_sigma=0.0)
        ou = MarketNoise(1, noise_mode="ou", latent_sigma=0.0)
        none = MarketNoise(1, noise_mode="none", latent_sigma=0.0)
        assert [none.step() for _ in range(5)] == [0.0] * 5
        # martingale increments are independent with mean ~0
        xs = np.array([mart.step() for _ in range(4000)])
        d = np.diff(xs)
        assert abs(d.mean()) < 0.01
        assert abs(np.corrcoef(d[:-1], d[1:])[0, 1]) < 0.06
        # ou is stationary (mean reverting): bounded spread compared to the random walk
        ox = np.array([ou.step() for _ in range(4000)])
        assert ox.std() < 0.3 < xs.std()

    def test_latent_is_fixed_per_match_and_nonzero(self):
        n = MarketNoise(7, noise_mode="none")
        vals = {n.step() for _ in range(10)}
        assert len(vals) == 1 and n.latent != 0.0
        assert MarketNoise(8, noise_mode="none").latent != n.latent

    def test_bad_mode(self):
        with pytest.raises(ValueError):
            MarketNoise(1, noise_mode="bogus")  # type: ignore[arg-type]


class TestCorpusTags:
    def test_tags_and_volume(self, tmp_path):
        matches = _make_corpus(tmp_path, 3, "martingale")
        m = matches[0]
        assert m["market_model"] == "martingale:v2" and m["corpus_format_version"] >= 2
        assert all(t["market_model"] == "martingale:v2" for t in m["ticks"])
        # volume is deterministic (no i.i.d. jitter) and reproducible
        again = _make_corpus(tmp_path, 1, "martingale")[0]
        assert [t["volume_back_home"] for t in again["ticks"]] == [t["volume_back_home"] for t in m["ticks"]]
        # and it responds to price moves
        assert len({t["volume_back_home"] for t in m["ticks"]}) > 5
        assert _make_corpus(tmp_path, 1, "ou")[0]["market_model"] == "ou:v2"


@pytest.fixture(scope="module")
def corpora(tmp_path_factory):
    d = tmp_path_factory.mktemp("s13")
    # Positive control: a strongly mean-reverting ("exploitable") OU price. The audit cannot see a very
    # weak reversion (the default theta=0.1), so the control uses theta=0.5, sigma=0.15.
    return {
        "martingale": _make_corpus(d, 60, "martingale"),
        "ou": _make_corpus(d, 60, "ou", noise_sigma=0.15, noise_theta=0.5),
    }


class TestAudit:
    def test_passes_martingale_fails_ou(self, corpora, tmp_path):
        ok, rep_m = audit_matches(corpora["martingale"])
        bad, rep_o = audit_matches(corpora["ou"])
        assert ok, rep_m
        assert not bad, rep_o
        assert rep_o["r2"] > rep_m["r2"]

    def test_cli_exit_codes(self, corpora, tmp_path):
        paths = {}
        for mode, matches in corpora.items():
            paths[mode] = tmp_path / f"{mode}.json"
            paths[mode].write_text(json.dumps(matches), encoding="utf-8")
        assert audit_main(["--corpus", str(paths["martingale"])]) == 0
        assert audit_main(["--corpus", str(paths["ou"])]) == 1


class TestNullMarket:
    @pytest.mark.parametrize("policy", ["random", "heuristic"])
    def test_baselines_not_significantly_positive(self, tmp_path, policy):
        from scripts.train_format_specialists import evaluate_specialist_holdout

        matches = _make_corpus(tmp_path, 30, "martingale")
        episodes = [m["ticks"] for m in matches]
        metrics, _ = evaluate_specialist_holdout(
            None, "t20i_men", "T20", episodes, policy=policy, include_baselines=False, seed=1,
        )
        assert metrics.n_matches == len(episodes)
        # bootstrap 95% CI of mean per-match P&L must include or lie below 0
        assert metrics.pnl_ci95_low <= 0.0, (policy, metrics.mean_match_pnl, metrics.pnl_ci95_low)
