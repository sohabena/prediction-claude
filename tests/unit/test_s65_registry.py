"""S65: model registry, promotion rule, drift statistics, weekly schedule and the retraining orchestration.

Every trainer step is a fake that writes the files the real script would write; nothing touches the database,
the network, Docker or the Cricsheet archive.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from scripts import retrain_weekly as rw
from training import evaluate as ev
from training import registry as reg

UTC = timezone.utc
REPO = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- synthetic S58 dataset
def _leg(p: float) -> dict:
    return {"back": 1.0 / (p * 0.995), "lay": 1.0 / (p * 1.005),
            "stale_s_back": 0, "source_back": "listing", "stale_s_lay": 0, "source_lay": "listing"}


def _dataset(n: int, seed: int = 3) -> dict:
    """``n`` settled matches with a T-10m snapshot priced around a random p (power de-vig is exact on this book)."""
    rng = np.random.default_rng(seed)
    base = datetime(2026, 10, 1, 10, tzinfo=UTC)
    matches, snapshots = [], []
    for i in range(n):
        p = float(rng.uniform(0.3, 0.7))
        home_won = int(rng.random() < p)
        mid = f"s{seed}_{i}"
        start = base + timedelta(hours=i)
        matches.append({"match_id": mid, "team_home": "A", "team_away": "B", "competition": "Ford Trophy",
                        "match_start_time": start.isoformat(), "family": "t20", "is_simulated": False,
                        "result_type": "win", "winner": "A" if home_won else "B", "winner_matched": True,
                        "home_won": home_won})
        book = {"home": _leg(p), "away": _leg(1.0 - p)}
        snapshots.append({"snapshot_id": f"{mid}:T-10m", "match_id": mid, "label": "T-10m",
                          "t": (start - timedelta(minutes=10)).isoformat(),
                          "time_to_start_s": 600, "in_play": False, "has_prices": True,
                          "book": book, "book_60s": book, "book_10m": book})
    return {"dir": "synthetic", "manifest": {"dataset": "synthetic", "generated_at_utc": "2026-11-03T00:00:00+00:00"},
            "matches": matches, "snapshots": snapshots}


def _write_dataset(ds: dict, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(json.dumps(ds["manifest"]), encoding="utf-8")
    with open(directory / "matches.jsonl", "w", encoding="utf-8") as h:
        for m in ds["matches"]:
            h.write(json.dumps(m) + "\n")
    with open(directory / "snapshots.jsonl", "w", encoding="utf-8") as h:
        for s in ds["snapshots"]:
            h.write(json.dumps(s) + "\n")
    return directory


def _market_p(ds: dict) -> dict[str, float]:
    return {str(r["snapshot"]): float(r["p_home"]) for r in ev.market_predictions(ds)}


@pytest.fixture(autouse=True)
def _fast_bootstrap(monkeypatch):
    monkeypatch.setattr(ev, "N_BOOT", 200)


# --------------------------------------------------------------------------- registry layout
def _artefact(tmp: Path, name: str, text: str = "x") -> Path:
    path = tmp / name
    path.write_text(text, encoding="utf-8")
    return path


def test_register_creates_versioned_directory_with_manifest_and_digests(tmp_path: Path):
    registry = reg.Registry(tmp_path / "registry")
    a = _artefact(tmp_path, "model.joblib", "abc")
    version = registry.register("prematch_t20", {"model.joblib": a}, {"run_id": "r1", "metrics": {"ll": 0.6}})
    assert version == "v0001"
    doc = registry.manifest("prematch_t20", version)
    assert doc["status"] == "candidate"
    assert doc["parent"] is None
    assert doc["run_id"] == "r1" and doc["metrics"] == {"ll": 0.6}
    assert doc["artefacts"]["model.joblib"] == reg._sha256(a)
    assert (registry.version_dir("prematch_t20", version) / "model.joblib").read_text(encoding="utf-8") == "abc"
    assert registry.champion_version("prematch_t20") is None


def test_versions_increment_and_are_listed_in_order(tmp_path: Path):
    registry = reg.Registry(tmp_path)
    for _ in range(3):
        registry.register("blend", {"latest.json": _artefact(tmp_path, "l.json")}, {})
    assert registry.versions("blend") == ["v0001", "v0002", "v0003"]
    assert registry.next_version("blend") == "v0004"


def test_names_are_validated(tmp_path: Path):
    registry = reg.Registry(tmp_path)
    with pytest.raises(ValueError):
        registry.name_dir("../escape")
    with pytest.raises(ValueError):
        registry.name_dir("Blend")


def test_register_refuses_to_overwrite_an_existing_version(tmp_path: Path, monkeypatch):
    registry = reg.Registry(tmp_path)
    registry.register("blend", {"latest.json": _artefact(tmp_path, "l.json")}, {})
    monkeypatch.setattr(registry, "next_version", lambda name: "v0001")
    with pytest.raises(FileExistsError):
        registry.register("blend", {"latest.json": _artefact(tmp_path, "l2.json")}, {})


# --------------------------------------------------------------------------- promotion rule
def _gate(source: str, *, r1: str, dataset_dir: str = "d") -> dict:
    return {"source": {"name": source}, "dataset": {"dir": dataset_dir},
            "results": [{"arena": "prematch", "segment": "all", "r1": {"status": r1}}]}


def _evidence(source: str, *, r1: str, challenger_ll=None, champion_ll=None, rows=50) -> dict:
    return {"gate_report": _gate(source, r1=r1), "challenger_source": source,
            "window": {"dataset": "d", "rows": rows}, "challenger_logloss": challenger_ll,
            "champion_logloss": champion_ll}


MANIFEST = {"gate_source": "blend_r1"}


def test_first_champion_needs_r1_pass_on_the_gate_report():
    ok, reasons = reg.promotion_decision(_evidence("blend_r1", r1="pass"), MANIFEST, champion_present=False)
    assert ok and reasons == []
    ok, reasons = reg.promotion_decision(_evidence("blend_r1", r1="insufficient"), MANIFEST, champion_present=False)
    assert not ok and reasons == ["r1_insufficient"]


def test_evidence_for_another_source_is_refused():
    ok, reasons = reg.promotion_decision(_evidence("blend_other", r1="pass"), MANIFEST, champion_present=False)
    assert not ok and "gate_report_for_other_source" in reasons


def test_challenger_must_beat_champion_strictly_on_the_same_rows():
    better = _evidence("blend_r1", r1="pass", challenger_ll=0.60, champion_ll=0.62)
    assert reg.promotion_decision(better, MANIFEST, champion_present=True) == (True, [])
    equal = _evidence("blend_r1", r1="pass", challenger_ll=0.62, champion_ll=0.62)
    assert reg.promotion_decision(equal, MANIFEST, champion_present=True) == (False, ["not_better_than_champion"])
    missing = _evidence("blend_r1", r1="pass")
    assert reg.promotion_decision(missing, MANIFEST, champion_present=True) == (False, ["no_same_window_comparison"])


def test_empty_window_and_window_from_another_dataset_are_refused():
    ev_empty = _evidence("blend_r1", r1="pass", rows=0)
    assert "empty_window" in reg.promotion_decision(ev_empty, MANIFEST, False)[1]
    ev_other = _evidence("blend_r1", r1="pass")
    ev_other["window"]["dataset"] = "elsewhere"
    assert "window_not_gate_dataset" in reg.promotion_decision(ev_other, MANIFEST, False)[1]


def test_promote_records_rejections_and_promotions_in_history(tmp_path: Path):
    registry = reg.Registry(tmp_path)
    v1 = registry.register("blend", {"latest.json": _artefact(tmp_path, "a.json")}, {"gate_source": "blend_r1"})
    rejected = registry.promote("blend", v1, _evidence("blend_r1", r1="fail"))
    assert rejected["promoted"] is False
    assert registry.champion_version("blend") is None
    assert registry.champion_state("blend")["history"][-1]["decision"] == "rejected"

    promoted = registry.promote("blend", v1, _evidence("blend_r1", r1="pass"))
    assert promoted["promoted"] is True
    assert registry.champion_version("blend") == v1
    assert registry.manifest("blend", v1)["status"] == "champion"

    v2 = registry.register("blend", {"latest.json": _artefact(tmp_path, "b.json")}, {"gate_source": "blend_r2"})
    assert registry.manifest("blend", v2)["parent"] == v1
    worse = registry.promote("blend", v2, _evidence("blend_r2", r1="pass", challenger_ll=0.7, champion_ll=0.6))
    assert worse["promoted"] is False and worse["champion"] == v1
    better = registry.promote("blend", v2, _evidence("blend_r2", r1="pass", challenger_ll=0.5, champion_ll=0.6))
    assert better["promoted"] is True and better["previous"] == v1
    assert registry.manifest("blend", v1)["status"] == "retired"
    assert registry.champion_version("blend") == v2
    decisions = [h["decision"] for h in registry.champion_state("blend")["history"]]
    assert decisions == ["rejected", "promoted", "rejected", "promoted"]


def test_set_champion_links_prematch_without_a_gate_of_its_own(tmp_path: Path):
    registry = reg.Registry(tmp_path)
    v1 = registry.register("prematch_t20", {"model.joblib": _artefact(tmp_path, "m.joblib")}, {})
    out = registry.set_champion("prematch_t20", v1, reason="linked to promoted blend v0001")
    assert out["champion"] == v1
    assert registry.champion_state("prematch_t20")["reason"] == "linked to promoted blend v0001"


# --------------------------------------------------------------------------- drift statistics
def test_psi_is_zero_for_the_same_sample_and_large_for_a_shift():
    rng = np.random.default_rng(0)
    base_vals = rng.uniform(0.2, 0.8, 2000)
    base = reg.distribution(base_vals)
    assert reg.psi(base["fractions"], reg._fractions(base_vals, base["edges"])) == pytest.approx(0.0, abs=1e-9)
    shifted = np.clip(base_vals * 0.3 + 0.7, 0, 1)  # everything pushed towards 1
    assert reg.psi(base["fractions"], reg._fractions(shifted, base["edges"])) > reg.PSI_FAIL


def test_distribution_handles_empty_and_missing_values():
    assert reg.distribution([]) == {"edges": [], "fractions": [], "n": 0}
    d = reg.distribution([0.4, None, 0.6, float("nan")])
    assert d["n"] == 2 and d["edges"][0] == 0.0 and d["edges"][-1] == 1.0


def test_drift_assessment_levels_follow_calibration_gap_and_psi():
    base_vals = list(np.linspace(0.3, 0.7, 100))
    baseline = {"p_market": reg.distribution(base_vals), "p_model": reg.distribution(base_vals)}
    calm = [{"p_home": p, "p_market": p, "p_model": p, "home_won": int(p > 0.5)} for p in base_vals]
    assert reg.drift_assessment(baseline, calm)["level"] == "ok"

    off = [{"p_home": 0.9, "p_market": 0.9, "p_model": 0.9, "home_won": 0} for _ in range(60)]
    report = reg.drift_assessment(baseline, off)
    assert report["level"] == "fail"
    assert report["calibration_gap"] == pytest.approx(0.9)
    assert report["psi"]["p_market"]["n"] == 60

    empty = reg.drift_assessment(baseline, [])
    assert empty["n"] == 0 and empty["level"] == "ok" and empty["calibration_gap"] is None


# --------------------------------------------------------------------------- weekly schedule
@pytest.mark.parametrize("now, expected", [
    (datetime(2026, 11, 4, 10, 0, tzinfo=UTC), datetime(2026, 11, 9, 3, 0, tzinfo=UTC)),  # Wednesday
    (datetime(2026, 11, 2, 2, 0, tzinfo=UTC), datetime(2026, 11, 2, 3, 0, tzinfo=UTC)),   # Monday, before 03:00
    (datetime(2026, 11, 2, 3, 0, tzinfo=UTC), datetime(2026, 11, 9, 3, 0, tzinfo=UTC)),   # exactly 03:00: next week
    (datetime(2026, 11, 8, 23, 0, tzinfo=UTC), datetime(2026, 11, 9, 3, 0, tzinfo=UTC)),  # Sunday night
    (datetime(2026, 11, 2, 4, 0, tzinfo=UTC), datetime(2026, 11, 9, 3, 0, tzinfo=UTC)),   # Monday, after 03:00
])
def test_next_weekly_run_is_monday_0300_utc(now, expected):
    got = reg.next_weekly_run(now)
    assert got == expected and got.weekday() == 0 and got.hour == 3


# --------------------------------------------------------------------------- orchestration with fake steps
def _fake_steps(ds: dict, *, blend_predictions: str = "oracle", fail_step: str | None = None):
    """A ``run_cmd`` that writes what each real script writes. The gate step runs the real S63 harness.

    ``blend_predictions``: ``oracle`` knows every outcome (passes R1); ``market`` copies the de-vigged market.
    """
    calls: list[list[str]] = []

    def run_cmd(cmd: list[str]) -> int:
        calls.append(cmd)
        script = Path(cmd[1]).name

        def arg(flag: str) -> str:
            return cmd[cmd.index(flag) + 1]

        if fail_step and fail_step in script:
            return 3
        if script == "train_prematch_model.py":
            out = Path(arg("--out-dir"))
            family = arg("--family")
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{family}_v3.joblib").write_bytes(b"model")
            (out / f"{family}_v3_walkforward.json").write_text(json.dumps({
                "counts": {"labelled": 300}, "folds": [{"season": 2025}], "gender": "all",
                "final_model_trained_through": "2026-01-01",
                "pooled": {"overall": {"v3": {"log_loss": 0.61, "brier": 0.2, "ece": 0.03, "n": 90}}},
            }), encoding="utf-8")
        elif script == "build_training_dataset.py":
            _write_dataset(ds, Path(arg("--out-dir")))
        elif script == "train_blend_model.py":
            out = Path(arg("--out-dir"))
            out.mkdir(parents=True, exist_ok=True)
            market = _market_p(ds)
            truth = {m["match_id"]: m["home_won"] for m in ds["matches"]}
            with open(out / "predictions.jsonl", "w", encoding="utf-8") as handle:
                for s in ds["snapshots"]:
                    sid = s["snapshot_id"]
                    p = float(truth[s["match_id"]]) if blend_predictions == "oracle" else market[sid]
                    handle.write(json.dumps({"match_id": s["match_id"], "snapshot": sid, "p_home": p,
                                             "p_sd": 0.01, "date": "2026-10-01", "p_market": market[sid],
                                             "p_model": p, "has_model": True, "n_train": 10,
                                             "train_max_date": None}) + "\n")
            (out / "latest.json").write_text(json.dumps({"usable_rows": len(ds["snapshots"]),
                                                         "rows_with_model": len(ds["snapshots"])}),
                                             encoding="utf-8")
        elif script == "evaluate_models.py":
            dataset = ev.load_dataset(Path(arg("--dataset")))
            preds = [json.loads(line) for line in Path(arg("--predictions")).read_text(encoding="utf-8").splitlines()]
            evaluation = ev.evaluate(dataset, preds)
            report = ev.build_report(dataset, evaluation, source_name=arg("--source-name"), source_kind="predictions",
                                     predictions_file=arg("--predictions"), predictions_rows=len(preds))
            ev.write_report(report, Path(arg("--out-dir")), arg("--date"))
        else:
            raise AssertionError(f"unexpected step {script}")
        return 0

    return run_cmd, calls


def _run(tmp: Path, now: datetime, run_cmd, dataset_dir: Path, **kw) -> dict:
    kw.setdefault("sync", False)
    return rw.run_retrain(now=now, registry_root=tmp / "registry", run_root=tmp / "runs",
                          dataset=dataset_dir, run_cmd=run_cmd, **kw)


def test_full_run_promotes_a_blend_that_beats_the_market_and_links_prematch(tmp_path: Path):
    ds = _dataset(210, seed=11)
    dataset_dir = _write_dataset(ds, tmp_path / "dataset")
    rec = _run(tmp_path, datetime(2026, 11, 2, 3, 0, tzinfo=UTC), _fake_steps(ds)[0], dataset_dir)
    assert rec["status"] == "promoted", rec["steps"]
    registry = reg.Registry(tmp_path / "registry")
    assert registry.champion_version("blend") == "v0001"
    assert registry.champion_version("prematch_t20") == rec["registered"]["prematch_t20"]
    assert registry.champion_version("prematch_odi") == rec["registered"]["prematch_odi"]
    assert "prematch/all" in registry.manifest("blend", "v0001")["gate_status"]["r1_pass"]
    assert [s["step"] for s in rec["steps"]] == ["prematch_t20", "prematch_odi", "dataset", "blend", "gate", "register"]
    saved = json.loads((tmp_path / "runs" / rec["run_id"] / "run.json").read_text(encoding="utf-8"))
    assert saved["status"] == "promoted"


def test_weaker_challenger_is_registered_but_not_promoted(tmp_path: Path):
    ds = _dataset(210, seed=12)
    dataset_dir = _write_dataset(ds, tmp_path / "dataset")
    first = _run(tmp_path, datetime(2026, 11, 2, 3, 0, tzinfo=UTC), _fake_steps(ds)[0], dataset_dir)
    assert first["status"] == "promoted"
    second = _run(tmp_path, datetime(2026, 11, 9, 3, 0, tzinfo=UTC),
                  _fake_steps(ds, blend_predictions="market")[0], dataset_dir)
    assert second["status"] == "not_promoted"
    reasons = second["promotion"]["blend"]["reasons"]
    assert "r1_fail" in reasons and "not_better_than_champion" in reasons
    registry = reg.Registry(tmp_path / "registry")
    assert registry.champion_version("blend") == "v0001"
    assert registry.versions("blend") == ["v0001", "v0002"]
    assert registry.manifest("blend", "v0002")["status"] == "candidate"


def test_a_failed_training_step_stops_before_anything_is_registered(tmp_path: Path):
    ds = _dataset(210, seed=13)
    dataset_dir = _write_dataset(ds, tmp_path / "dataset")
    run_cmd, calls = _fake_steps(ds, fail_step="train_prematch_model")
    rec = _run(tmp_path, datetime(2026, 11, 2, 3, 0, tzinfo=UTC), run_cmd, dataset_dir)
    assert rec["status"] == "failed"
    assert [s["step"] for s in rec["steps"]] == ["prematch_t20"]
    assert len(calls) == 1
    assert reg.Registry(tmp_path / "registry").versions("blend") == []


def test_no_sync_never_calls_the_download(tmp_path: Path):
    ds = _dataset(210, seed=14)
    dataset_dir = _write_dataset(ds, tmp_path / "dataset")

    def must_not_run(url, target):
        raise AssertionError("download must not run with --no-sync")

    rec = _run(tmp_path, datetime(2026, 11, 2, 3, 0, tzinfo=UTC), _fake_steps(ds)[0], dataset_dir,
               sync=False, sync_fn=must_not_run)
    assert rec["sync"].startswith("skipped")


def test_sync_failure_is_recorded_and_the_run_carries_on(tmp_path: Path):
    ds = _dataset(210, seed=15)
    dataset_dir = _write_dataset(ds, tmp_path / "dataset")

    def offline(url, target):
        raise OSError("no route to cricsheet")

    rec = _run(tmp_path, datetime(2026, 11, 2, 3, 0, tzinfo=UTC), _fake_steps(ds)[0], dataset_dir,
               sync=True, sync_fn=offline)
    assert rec["sync"].startswith("failed: OSError")
    assert rec["status"] == "promoted"


def test_sync_success_passes_the_archive_url_and_target(tmp_path: Path):
    ds = _dataset(210, seed=16)
    dataset_dir = _write_dataset(ds, tmp_path / "dataset")
    seen: dict = {}

    def fake_sync(url, target):
        seen.update(url=url, target=target)
        return 7

    rec = _run(tmp_path, datetime(2026, 11, 2, 3, 0, tzinfo=UTC), _fake_steps(ds)[0], dataset_dir,
               sync=True, sync_fn=fake_sync)
    assert seen == {"url": rw.DEFAULT_RECENT_URL, "target": rw.INCOMING_DIR}
    assert rec["sync"].startswith("ok (7 match files")


def test_recent_archive_url_matches_the_weekly_script():
    text = (REPO / "scripts" / "weekly_cricsheet_sync_and_retrain.py").read_text(encoding="utf-8")
    assert f'DEFAULT_RECENT_URL = "{rw.DEFAULT_RECENT_URL}"' in text


def test_summary_lines_show_status_steps_and_promotion():
    rec = {"run_id": "r", "status": "not_promoted", "sync": "skipped (--no-sync)",
           "steps": [{"step": "blend", "status": "ok", "exit_code": 0, "seconds": 1.5}],
           "registered": {"blend": "v0001"},
           "promotion": {"blend": {"promoted": False, "champion_before": None, "reasons": ["r1_insufficient"]}}}
    text = "\n".join(rw._summary_lines(rec))
    assert "run r: not_promoted" in text and "blend" in text and "r1_insufficient" in text


# --------------------------------------------------------------------------- deployment files
def test_compose_defines_a_recording_profile_trainer():
    yaml = pytest.importorskip("yaml")
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))
    trainer = compose["services"]["trainer"]
    assert trainer["profiles"] == ["recording"]
    assert trainer["restart"] == "unless-stopped"
    assert trainer["command"] == ["python", "scripts/retrain_weekly.py", "--loop"]
    assert trainer["build"]["dockerfile"] == "Dockerfile.scraper"
    mounts = set(trainer["volumes"])
    assert {"./logs:/app/logs", "./models:/app/models", "./data:/app/data", "./reports:/app/reports"} <= mounts


def test_scraper_image_copies_every_module_the_trainer_imports():
    text = (REPO / "Dockerfile.scraper").read_text(encoding="utf-8")
    copied = set(re.findall(r"^COPY (\S+) ", text, flags=re.M))
    for needed in ("strategy/", "training/", "scripts/build_training_dataset.py", "scripts/train_prematch_model.py",
                   "scripts/train_blend_model.py", "scripts/evaluate_models.py", "scripts/retrain_weekly.py",
                   "scripts/cricsheet_harmonizer.py", "scripts/synthetic_market.py", "scripts/train_fair_value.py"):
        assert needed in copied, needed
