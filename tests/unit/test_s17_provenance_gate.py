"""S17: model zoo provenance gate."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import rl.model_zoo as mz
from rl.model_zoo import ModelZoo

GOOD = {
    "code_version": "abc123", "edge_vs_random": 12.0, "pnl_ci95_low": 1.5,
    "matches_tested": 80, "max_drawdown_pct": 20.0,
}


def _zoo(tmp_path: Path, meta: dict, weights=()) -> ModelZoo:
    (tmp_path / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    for k in weights:
        (tmp_path / f"{k}_v1.zip").write_bytes(b"x")
    return ModelZoo(tmp_path)


def _entry(key, **over):
    m = {**GOOD, **over.pop("metrics", {})}
    return {"key": key, "path": f"x/{key}_v1.zip", "metrics": m, "active": True, **over}


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv(mz.ALLOW_UNVERIFIED_ENV, raising=False)


def test_current_metadata_not_tradeable(tmp_path):
    src = Path("models/specialists/metadata.json")
    if not src.exists():
        pytest.skip("no metadata.json")
    shutil.copy(src, tmp_path / "metadata.json")
    zoo = ModelZoo(tmp_path)
    results = [zoo.is_tradeable(k) for k in json.loads(src.read_text(encoding="utf-8"))]
    assert results and not any(ok for ok, _ in results)
    assert any("missing code_version" in r for _, r in results)


def test_good_entry_tradeable_and_each_rule_fails(tmp_path):
    zoo = _zoo(tmp_path, {"ipl": _entry("ipl")})
    assert zoo.is_tradeable("ipl") == (True, "ok")
    for over, frag in [
        ({"active": False}, "active"),
        ({"metrics": {"code_version": ""}}, "code_version"),
        ({"metrics": {"edge_vs_random": None}}, "edge_vs_random"),
        ({"metrics": {"pnl_ci95_low": 0.0}}, "ci95"),
        ({"metrics": {"matches_tested": 10}}, "matches_tested"),
        ({"metrics": {"max_drawdown_pct": 90.0}}, "drawdown"),
        ({"metrics": {"market_model": "ou"}}, "market_model"),
    ]:
        z = _zoo(tmp_path, {"ipl": _entry("ipl", **over)})
        ok, reason = z.is_tradeable("ipl")
        assert not ok and frag in reason, (over, reason)
    assert _zoo(tmp_path, {}).is_tradeable("ipl")[0] is False


def test_untradeable_returns_none_and_no_default_fallback(tmp_path, monkeypatch):
    zoo = _zoo(tmp_path, {"ipl": _entry("ipl", metrics={"code_version": ""})}, weights=["ipl"])
    sentinel = object()
    zoo._default_agent = sentinel  # type: ignore[assignment]
    assert zoo.get_agent("ipl", allow_load=False) is None
    agent, key = zoo.get_agent_for_match(format_name="T20", event_name="Indian Premier League", allow_load=False)
    assert agent is None


def test_default_goes_through_gate(tmp_path):
    zoo = _zoo(tmp_path, {})
    zoo._default_agent = object()  # type: ignore[assignment]
    assert zoo.get_agent("default", allow_load=False) is None


def test_tradeable_loads(tmp_path, monkeypatch):
    zoo = _zoo(tmp_path, {"ipl": _entry("ipl")}, weights=["ipl"])
    fake = object()
    monkeypatch.setattr(ModelZoo, "_try_load", lambda self, k, p: (fake, {"key": k, "path": str(p), "mtime": 0, "sha256": "s"}))
    assert zoo.get_agent("ipl") is fake


def test_override_bypasses_gate_with_warning(tmp_path, monkeypatch):
    monkeypatch.setenv(mz.ALLOW_UNVERIFIED_ENV, "1")
    zoo = _zoo(tmp_path, {}, weights=["ipl"])
    fake = object()
    monkeypatch.setattr(ModelZoo, "_try_load", lambda self, k, p: (fake, {"key": k, "path": str(p), "mtime": 0, "sha256": "s"}))
    warned = []
    monkeypatch.setattr(mz.logger, "warning", lambda ev, **kw: warned.append(ev))
    assert zoo.get_agent("ipl") is fake
    assert "unverified_model_allowed" in warned


def test_register_inactive_by_default_and_list_fields(tmp_path):
    zoo = ModelZoo(tmp_path)
    zoo.register_specialist("ipl", "p.zip", "IPL", 10, dict(GOOD))
    assert zoo._metadata["ipl"]["active"] is False
    assert zoo.list_specialists()["ipl"]["tradeable"] is False
    assert zoo.list_specialists()["ipl"]["reason"] == "not active"
    zoo.register_specialist("ipl", "p.zip", "IPL", 10, dict(GOOD), active=True)
    assert zoo.list_specialists()["ipl"]["tradeable"] is True
