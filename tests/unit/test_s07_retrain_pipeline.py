"""S07: chronological corpus, frozen splits, promotion gate, rollback, DB sync, KL guard."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from types import SimpleNamespace

from scripts import weekly_cricsheet_sync_and_retrain as w
from scripts.rollback_specialist import rollback_specialist


def _match(i: int, day: int | None = None) -> dict:
    d = day if day is not None else i
    return {
        "match_id": f"m{i:03d}", "team_home": f"H{i}", "team_away": f"A{i}",
        "start_time": f"2025-{1 + d // 28:02d}-{1 + d % 28:02d}T00:00:00",
        "ticks": [{"match_id": f"m{i:03d}", "time": i}],
    }


def _write_corpus(d: Path, key: str, matches: list) -> None:
    d.mkdir(parents=True, exist_ok=True)
    with gzip.open(d / f"corpus_{key}.json.gz", "wt", encoding="utf-8") as f:
        json.dump(matches, f)


def _metrics(mean=10.0, ci_low=1.0, dd_pct=5.0, edge=2.0):
    return SimpleNamespace(mean_match_pnl=mean, pnl_ci95_low=ci_low, max_drawdown_pct=dd_pct, edge_vs_random=edge)


def test_merge_sorts_chronologically_and_dedupes() -> None:
    old = [_match(5), _match(1)]
    dup = _match(9)
    dup.update(team_home="H1", team_away="A1", start_time=old[1]["start_time"])
    out = w.merge_corpus(old, [_match(3), dup])
    assert [m["match_id"] for m in out] == ["m001", "m003", "m005"]


def test_split_frozen_when_matches_added() -> None:
    base = [_match(i) for i in range(40)]
    m1 = w.build_split_manifest(base)
    assert m1["holdout_ids"] == [f"m{i:03d}" for i in range(34, 40)]
    assert not set(m1["val_ids"]) & set(m1["holdout_ids"])
    more = base + [_match(i) for i in range(40, 50)]
    m2 = w.build_split_manifest(more, existing=m1)
    assert m2["holdout_ids"] == m1["holdout_ids"] and m2["val_ids"] == m1["val_ids"]
    m3 = w.build_split_manifest(more, existing=m2, holdout_refresh=3)
    assert m3["holdout_ids"][: len(m1["holdout_ids"])] == m1["holdout_ids"]
    assert len(m3["holdout_generations"]) == 2 and len(m3["holdout_ids"]) == len(m1["holdout_ids"]) + 3


def test_new_matches_in_finetune_mix_and_holdout_excluded() -> None:
    base = [_match(i) for i in range(40)]
    manifest = w.build_split_manifest(base)
    more = base + [_match(i) for i in range(40, 50)]
    manifest = w.build_split_manifest(more, existing=manifest)
    new_ids = {f"m{i:03d}" for i in range(40, 50)}
    mix = w.select_finetune_matches(more, manifest, new_ids)
    ids = {m["match_id"] for m in mix}
    assert new_ids <= ids
    assert not ids & set(manifest["holdout_ids"])
    assert not ids & set(manifest["val_ids"])


def test_replay_sample_size() -> None:
    matches = [_match(i, day=i % 300) for i in range(600)]
    manifest = w.build_split_manifest(matches)
    mix = w.select_finetune_matches(matches, manifest, {"m000"})
    assert len(mix) == 1 + 200


def test_decide_promotion_gate() -> None:
    assert w.decide_promotion(_metrics(), _metrics(mean=5.0))[0]
    assert "candidate_mean_pnl_below_champion" in w.decide_promotion(_metrics(mean=1), _metrics(mean=5))[1]
    assert "pnl_ci95_low_not_positive" in w.decide_promotion(_metrics(ci_low=-0.1), None)[1]
    assert "max_drawdown_above_limit" in w.decide_promotion(_metrics(dd_pct=20.0), None)[1]
    assert "edge_vs_random_not_positive" in w.decide_promotion(_metrics(edge=None), None)[1]


def _setup_pipeline(tmp_path: Path):
    corpus, models = tmp_path / "data", tmp_path / "models"
    models.mkdir()
    _write_corpus(corpus, "ipl", [_match(i) for i in range(30)])
    (models / "ipl_v1.zip").write_bytes(b"v1")
    (models / "metadata.json").write_text(json.dumps(
        {"ipl": {"key": "ipl", "path": str(models / "ipl_v1.zip"), "metrics": {"mean_match_pnl": 5.0}}}))

    def train_fn(key, mix, steps, champion, cand_path, bets):
        cand_path.parent.mkdir(parents=True, exist_ok=True)
        cand_path.write_bytes(b"cand")
        return cand_path, {"aborted": False}

    return corpus, models, train_fn


def test_worse_candidate_not_promoted(tmp_path: Path) -> None:
    corpus, models, train_fn = _setup_pipeline(tmp_path)
    before = (models / "metadata.json").read_text()

    def eval_fn(path, *a):
        return (_metrics(mean=1.0) if "candidates" in str(path) else _metrics(mean=5.0)), []

    res = w.retrain_specialist_model("ipl", 100, corpus_dir=corpus, models_dir=models,
                                     train_fn=train_fn, evaluate_fn=eval_fn)
    assert not res["promoted"]
    assert (models / "metadata.json").read_text() == before
    assert len(list((models / "candidates").glob("ipl_*.zip"))) == 1
    assert not (models / "ipl_v2.zip").exists()


def test_better_candidate_promoted_then_rollback(tmp_path: Path) -> None:
    corpus, models, train_fn = _setup_pipeline(tmp_path)

    def eval_fn(path, *a):
        return (_metrics(mean=9.0) if "candidates" in str(path) else _metrics(mean=5.0)), []

    res = w.retrain_specialist_model("ipl", 100, corpus_dir=corpus, models_dir=models,
                                     train_fn=train_fn, evaluate_fn=eval_fn)
    assert res["promoted"]
    meta = json.loads((models / "metadata.json").read_text())["ipl"]
    assert meta["version"] == 2 and meta["champion_path"].endswith("ipl_v2.zip")
    assert meta["path"] == meta["champion_path"]
    assert meta["history"][-1]["path"].endswith("ipl_v1.zip")
    assert (models / "ipl_v1.zip").read_bytes() == b"v1"

    change = rollback_specialist("ipl", models_dir=models)
    assert change["to_version"] == 1
    meta = json.loads((models / "metadata.json").read_text())["ipl"]
    assert meta["version"] == 1 and meta["path"].endswith("ipl_v1.zip")
    assert (models / "ipl_v2.zip").exists()


def test_dry_run_does_not_promote(tmp_path: Path) -> None:
    corpus, models, train_fn = _setup_pipeline(tmp_path)

    def eval_fn(path, *a):
        return (_metrics(mean=9.0) if "candidates" in str(path) else _metrics(mean=5.0)), []

    res = w.retrain_specialist_model("ipl", 100, corpus_dir=corpus, models_dir=models, dry_run=True,
                                     train_fn=train_fn, evaluate_fn=eval_fn)
    assert not res["promoted"] and not (models / "ipl_v2.zip").exists()


async def test_db_sync_is_awaited(tmp_path: Path) -> None:
    calls = []

    async def fake_sync(new_by_spec):
        calls.append(new_by_spec)
        return 1

    status: dict = {}
    updated = await w.append_to_corpora({"ipl": [_match(1)]}, tmp_path, db_sync_fn=fake_sync, status=status)
    assert updated == ["ipl"] and len(calls) == 1 and status["db_sync"] == "ok"


async def test_db_sync_failure_reported(tmp_path: Path) -> None:
    async def bad_sync(_):
        raise RuntimeError("db down")

    status: dict = {}
    await w.append_to_corpora({"ipl": [_match(1)]}, tmp_path, db_sync_fn=bad_sync, status=status)
    assert status["db_sync"] == "failed"


def test_kl_guard_aborts() -> None:
    cb = w.UpdateHealthCallback()
    cb.record({"train/approx_kl": 0.2, "train/clip_fraction": 0.3, "train/value_loss": 1.0})
    assert cb._on_step() is False and cb.aborted
    cb2 = w.UpdateHealthCallback()
    cb2.record({"train/approx_kl": 0.01, "train/value_loss": float("nan")})
    assert cb2.aborted
    cb3 = w.UpdateHealthCallback()
    cb3.record({"train/approx_kl": 0.01, "train/value_loss": 1.0})
    assert cb3._on_step() is True
