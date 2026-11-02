"""S18: rebuild_all dry run on a 3-match fixture (all eight steps, < 2 min)."""
from __future__ import annotations

import time
from pathlib import Path

from scripts import rebuild_all


def test_dry_run_completes_every_step() -> None:
    t0 = time.time()
    res = rebuild_all.run_dry(["t20i_men"])
    assert time.time() - t0 < 120
    steps = res["results"]["t20i_men"]["steps"]
    for name in ("backup", "corpus", "audits", "fair", "ppo", "eval", "metadata", "tradeable"):
        assert name in steps
    assert steps["backup"]["backed_up"] is True
    assert steps["metadata"]["active_saved"] is False
    assert steps["eval"]["policies"]["hold"]["status"] == "ok"
    assert not Path(res["tmp"]).samefile(Path("models"))


def test_report_renders_from_json(tmp_path: Path) -> None:
    res = rebuild_all.run_dry(["t20i_men"])
    paths = rebuild_all.Paths(report_dir=Path(res["tmp"]) / "reports")
    out = rebuild_all.write_report(paths, ["t20i_men"], tmp_path / "r.md")
    text = out.read_text(encoding="utf-8")
    assert "Recommendation" in text and "t20i_men" in text
