"""S65: weekly retraining of the pre-match models and the market blend, with registry promotion.

    python scripts/retrain_weekly.py --once [--no-sync] [--dataset DIR]
    python scripts/retrain_weekly.py --loop          # container: sleeps until Monday 03:00 UTC, then runs

One run, in order. Every training step is a subprocess; a failed step stops the run before anything is registered.

  0. sync      Cricsheet recently-added archive into data/incoming_cricsheet. Skipped by --no-sync. A failure is
               recorded and the run carries on with the data already on disk.
  1. prematch  S60 ``train_prematch_model.py`` for t20 and odi          -> data/retrain/<run>/prematch/
  2. dataset   S58 ``build_training_dataset.py`` (read-only database)  -> data/retrain/<run>/dataset/
               (``--dataset DIR`` uses an existing S58 dataset instead)
  3. blend     S61 ``train_blend_model.py``                            -> data/retrain/<run>/blend/
  4. gate      S63 ``evaluate_models.py`` (the referee)                -> data/retrain/<run>/gates/<YYYYMMDD>.json
  5. register  ``prematch_t20``, ``prematch_odi`` and ``blend`` versions in models/registry/ (status candidate)
  6. promote   ``blend`` only if ``training.registry.promotion_decision`` accepts the S63 evidence on the same
               held-out window as the champion; then the prematch versions fitted for it.

Exit code: 0 when the run finished (promoted or not), 1 when a step failed.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.logging import setup_logging  # noqa: E402
from training.evaluate import build_eval_rows, load_dataset, log_loss  # noqa: E402
from training.registry import (  # noqa: E402
    DEFAULT_ROOT,
    Registry,
    distribution,
    next_weekly_run,
    read_jsonl,
)

logger = setup_logging("retrain_weekly")

DATA_ROOT = ROOT / "data"
RUN_ROOT = DATA_ROOT / "retrain"
INCOMING_DIR = DATA_ROOT / "incoming_cricsheet"
RATINGS_CACHE = DATA_ROOT / "ratings" / "cricket_ratings.joblib"
FAMILIES = ("t20", "odi")
BLEND_NAME = "blend"
PREMATCH_NAMES = {family: f"prematch_{family}" for family in FAMILIES}
STEP_TIMEOUT_S = 6 * 3600

# Same archive as scripts/weekly_cricsheet_sync_and_retrain.py (DEFAULT_RECENT_URL). Kept as a literal here: that
# module imports the PPO/RL stack, which the trainer image does not need.
DEFAULT_RECENT_URL = "https://cricsheet.org/downloads/recently_added_7_json.zip"

RunCmd = Callable[[list[str]], int]
SyncFn = Callable[[str, Path], int]


def download_recent_archive(url: str, target_dir: Path) -> int:
    """Download the recently-added Cricsheet archive and extract its match JSON files. Returns the file count."""
    target_dir.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "PHOENIX/2.0 (Cricket AI Trading Bot)"})
    with urllib.request.urlopen(request, timeout=60) as response:
        content = response.read()
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        members = [m for m in archive.infolist() if m.filename.endswith(".json")]
        archive.extractall(path=target_dir, members=members)
    return len(members)


def _run_cmd(cmd: list[str]) -> int:
    # joblib cannot count physical cores on Windows without wmic and warns on every step; tell it the logical count
    env = {**os.environ, "LOKY_MAX_CPU_COUNT": os.environ.get("LOKY_MAX_CPU_COUNT", str(os.cpu_count() or 1))}
    try:
        return subprocess.run(cmd, cwd=str(ROOT), timeout=STEP_TIMEOUT_S, check=False, env=env).returncode
    except subprocess.TimeoutExpired:
        return 124


def _git_sha() -> Optional[str]:
    try:
        completed = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True,
                                   text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)


def _data_window(dataset: Mapping[str, Any], dataset_dir: Path) -> dict[str, Any]:
    matches = dataset["matches"]
    dates = sorted(str(m["match_start_time"])[:10] for m in matches if m.get("match_start_time"))
    return {
        "dataset_dir": str(dataset_dir),
        "dataset_generated_at_utc": dataset["manifest"].get("generated_at_utc"),
        "matches": len(matches),
        "settled": sum(1 for m in matches if m.get("home_won") in (0, 1)),
        "first_match_date": dates[0] if dates else None,
        "last_match_date": dates[-1] if dates else None,
    }


def same_window_logloss(dataset: Mapping[str, Any], challenger: Sequence[Mapping[str, Any]],
                        champion: Sequence[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    """Mean source log loss of two prediction sets on the rows both cover, prematch arena, segment ``all``.

    Predictions whose snapshot is not in ``dataset`` are ignored, so both sides are scored on the same snapshots.
    Returns None when they share no snapshot.
    """
    snapshots = {str(s["snapshot_id"]) for s in dataset["snapshots"]}
    ch = {str(p["snapshot"]): p for p in challenger if str(p["snapshot"]) in snapshots}
    cp = {str(p["snapshot"]): p for p in champion if str(p["snapshot"]) in snapshots}
    common = sorted(set(ch) & set(cp))
    if not common:
        return None

    def mean_logloss(preds: Mapping[str, Mapping[str, Any]]) -> tuple[float, int]:
        rows = [r for r in build_eval_rows(dataset, [preds[s] for s in common])
                if r["arena"] == "prematch" and "all" in r["segments"]]
        if not rows:
            return float("nan"), 0
        p = np.asarray([r["p_home"] for r in rows], dtype=float)
        y = np.asarray([r["home_won"] for r in rows], dtype=float)
        return float(np.mean(log_loss(p, y))), len(rows)

    ll_challenger, n_c = mean_logloss(ch)
    ll_champion, n_p = mean_logloss(cp)
    if n_c != n_p:
        return None
    return {"rows": n_c, "snapshots": len(common), "challenger_logloss": ll_challenger,
            "champion_logloss": ll_champion}


def register_and_promote(
    registry: Registry,
    *,
    run_id: str,
    run_dir: Path,
    dataset_dir: Path,
    gate_json: Path,
    record: dict[str, Any],
) -> dict[str, Any]:
    """Register the run's artefacts as candidates, then promote on evidence. Mutates and returns ``record``."""
    gate = json.loads(gate_json.read_text(encoding="utf-8"))
    dataset = load_dataset(dataset_dir)
    blend_dir = run_dir / "blend"
    predictions = read_jsonl(blend_dir / "predictions.jsonl")
    latest = json.loads((blend_dir / "latest.json").read_text(encoding="utf-8"))
    gate_source = f"{BLEND_NAME}_{run_id}"
    git_sha = _git_sha()

    prematch_versions: dict[str, str] = {}
    prematch_meta: dict[str, Any] = {}
    for family in FAMILIES:
        name = PREMATCH_NAMES[family]
        report = json.loads((run_dir / "prematch" / f"{family}_v3_walkforward.json").read_text(encoding="utf-8"))
        prematch_meta[family] = {
            "labelled_matches": report.get("counts", {}).get("labelled"),
            "folds": [f["season"] for f in report.get("folds", [])],
            "trained_through": report.get("final_model_trained_through"),
            "pooled_overall": report.get("pooled", {}).get("overall", {}),
        }
        prematch_versions[family] = registry.register(
            name,
            {"model.joblib": run_dir / "prematch" / f"{family}_v3.joblib",
             "walkforward.json": run_dir / "prematch" / f"{family}_v3_walkforward.json"},
            {
                "run_id": run_id,
                "git_sha": git_sha,
                "family": family,
                "data_window": {"labelled_matches": prematch_meta[family]["labelled_matches"],
                                "folds": prematch_meta[family]["folds"],
                                "trained_through": prematch_meta[family]["trained_through"]},
                "metrics": {"walk_forward_pooled_v3": prematch_meta[family]["pooled_overall"].get("v3")},
                "gate_status": "not_gated",
                "gate_note": "promoted only together with the blend version it was fitted for",
            },
        )

    blend_version = registry.register(
        BLEND_NAME,
        {"latest.json": blend_dir / "latest.json",
         "predictions.jsonl": blend_dir / "predictions.jsonl",
         "gate.json": gate_json,
         "gate.md": gate_json.with_suffix(".md")},
        {
            "run_id": run_id,
            "git_sha": git_sha,
            "gate_source": gate_source,
            "gate_status": {"r1_pass": gate["summary"]["r1_pass"], "r2_pass": gate["summary"]["r2_pass"],
                            "signals_allowed": gate["summary"]["signals_allowed"]},
            "data_window": _data_window(dataset, dataset_dir),
            "rows": {"usable": latest.get("usable_rows"), "with_model": latest.get("rows_with_model")},
            "metrics": {"gate_counts": gate["summary"]["r1_counts"],
                        "walk_forward_predictions": len(predictions)},
            "prematch_versions": prematch_versions,
            "baseline": {
                "p_market": distribution([p.get("p_market") for p in predictions]),
                "p_model": distribution([p.get("p_model") for p in predictions]),
            },
        },
    )
    record["registered"] = {BLEND_NAME: blend_version,
                            **{PREMATCH_NAMES[family]: version for family, version in prematch_versions.items()}}

    champion = registry.champion_version(BLEND_NAME)
    window = None
    if champion is not None:
        champion_preds = read_jsonl(registry.version_dir(BLEND_NAME, champion) / "predictions.jsonl")
        window = same_window_logloss(dataset, predictions, champion_preds)
    evidence: dict[str, Any] = {
        "gate_report": gate,
        "challenger_source": gate_source,
        "window": {"dataset": gate["dataset"]["dir"],
                   "rows": (window or {}).get("rows", 0),
                   "snapshots": (window or {}).get("snapshots", 0)},
        "challenger_logloss": (window or {}).get("challenger_logloss"),
        "champion_logloss": (window or {}).get("champion_logloss"),
    }
    if champion is None:
        # No champion yet: the window is the gate's own, so the first promotion needs R1 alone.
        evidence["window"]["rows"] = gate["coverage"]["prematch"]["rows"]
    decision = registry.promote(BLEND_NAME, blend_version, evidence)
    record["promotion"] = {"blend": {"version": blend_version, "champion_before": champion, **decision}}
    if decision["promoted"]:
        for family in FAMILIES:
            linked = registry.set_champion(PREMATCH_NAMES[family], prematch_versions[family],
                                           reason=f"linked to promoted blend {blend_version}")
            record["promotion"][PREMATCH_NAMES[family]] = linked
    return record


def run_retrain(
    *,
    now: Optional[datetime] = None,
    registry_root: Path = DEFAULT_ROOT,
    run_root: Path = RUN_ROOT,
    sync: bool = True,
    dataset: Optional[Path] = None,
    url: str = DEFAULT_RECENT_URL,
    incoming_dir: Path = INCOMING_DIR,
    run_cmd: Optional[RunCmd] = None,
    sync_fn: Optional[SyncFn] = None,
) -> dict[str, Any]:
    """Execute one weekly run end to end and return its record (also written to ``<run>/run.json``)."""
    run_cmd = run_cmd or _run_cmd
    sync_fn = sync_fn or download_recent_archive
    now = now or _utc_now()
    run_id = now.strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(run_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {"run_id": run_id, "started_at": now.isoformat(), "steps": [],
                              "sync": "skipped (--no-sync)" if not sync else None, "status": "running"}
    py = sys.executable

    def step(name: str, cmd: list[str]) -> bool:
        started = time.monotonic()
        print(f"\n[{name.upper()}] {' '.join(cmd[1:])}", flush=True)
        code = run_cmd(cmd)
        record["steps"].append({"step": name, "status": "ok" if code == 0 else "failed", "exit_code": code,
                                "seconds": round(time.monotonic() - started, 1)})
        return code == 0

    def finish(status: str) -> dict[str, Any]:
        record["status"] = status
        record["finished_at"] = _utc_now().isoformat()
        _write_json(run_dir / "run.json", record)
        return record

    if sync:
        try:
            count = sync_fn(url, incoming_dir)
            record["sync"] = f"ok ({count} match files from {url})"
        except Exception as exc:  # the run carries on with the data already on disk
            record["sync"] = f"failed: {type(exc).__name__}: {exc}"
            logger.error("cricsheet_sync_failed", error=str(exc))
        print(f"\n[SYNC] {record['sync']}", flush=True)

    for family in FAMILIES:
        if not step(f"prematch_{family}", [py, "scripts/train_prematch_model.py", "--family", family,
                                           "--out-dir", str(run_dir / "prematch"),
                                           "--data-root", str(DATA_ROOT)]):
            return finish("failed")

    if dataset is not None:
        dataset_dir = Path(dataset)
        record["steps"].append({"step": "dataset", "status": "skipped", "detail": f"--dataset {dataset_dir}"})
    else:
        dataset_dir = run_dir / "dataset"
        if not step("dataset", [py, "scripts/build_training_dataset.py", "--out-dir", str(dataset_dir)]):
            return finish("failed")

    if not step("blend", [py, "scripts/train_blend_model.py", "--dataset", str(dataset_dir),
                          "--prematch-dir", str(run_dir / "prematch"), "--out-dir", str(run_dir / "blend"),
                          "--data-root", str(DATA_ROOT), "--ratings-cache", str(RATINGS_CACHE)]):
        return finish("failed")

    stamp = now.strftime("%Y%m%d")
    gates_dir = run_dir / "gates"
    if not step("gate", [py, "scripts/evaluate_models.py", "--dataset", str(dataset_dir),
                         "--predictions", str(run_dir / "blend" / "predictions.jsonl"),
                         "--source-name", f"{BLEND_NAME}_{run_id}", "--date", stamp,
                         "--out-dir", str(gates_dir)]):
        return finish("failed")

    try:
        register_and_promote(Registry(registry_root), run_id=run_id, run_dir=run_dir, dataset_dir=dataset_dir,
                             gate_json=gates_dir / f"{stamp}.json", record=record)
    except Exception as exc:
        record["steps"].append({"step": "register", "status": "failed", "detail": f"{type(exc).__name__}: {exc}"})
        logger.error("register_failed", error=str(exc))
        return finish("failed")
    record["steps"].append({"step": "register", "status": "ok"})
    promoted = record["promotion"]["blend"]["promoted"]
    return finish("promoted" if promoted else "not_promoted")


def _summary_lines(record: Mapping[str, Any]) -> list[str]:
    lines = [f"run {record['run_id']}: {record['status']}", f"  sync: {record['sync']}"]
    for s in record["steps"]:
        extra = s.get("detail") or (f"exit {s['exit_code']}, {s['seconds']}s" if "exit_code" in s else "")
        lines.append(f"  {s['step']:<12} {s['status']:<8} {extra}")
    if record.get("registered"):
        lines.append("  registered: " + ", ".join(f"{k} {v}" for k, v in record["registered"].items()))
    blend = (record.get("promotion") or {}).get("blend")
    if blend:
        reasons = ", ".join(blend.get("reasons") or []) or "none"
        lines.append(f"  blend promotion: {'promoted' if blend['promoted'] else 'not promoted'} "
                     f"(champion before: {blend.get('champion_before')}; reasons: {reasons})")
    return lines


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="run now and exit")
    mode.add_argument("--loop", action="store_true", help="run every Monday 03:00 UTC, forever")
    parser.add_argument("--no-sync", action="store_true", help="skip the Cricsheet download")
    parser.add_argument("--dataset", type=Path, default=None,
                        help="use this existing S58 dataset instead of building one (no database read)")
    parser.add_argument("--url", default=DEFAULT_RECENT_URL, help="Cricsheet recently-added archive")
    parser.add_argument("--registry-root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args(argv)

    kwargs = dict(registry_root=args.registry_root, sync=not args.no_sync, dataset=args.dataset, url=args.url)
    if args.once:
        record = run_retrain(**kwargs)
        print("\n" + "\n".join(_summary_lines(record)))
        return 1 if record["status"] == "failed" else 0

    while True:
        now = _utc_now()
        nxt = next_weekly_run(now)
        print(f"next run {nxt.isoformat()}", flush=True)
        time.sleep(max(1.0, (nxt - now).total_seconds()))
        try:
            record = run_retrain(**kwargs)
            print("\n".join(_summary_lines(record)), flush=True)
        except Exception as exc:  # keep the trainer alive; the next Monday retries
            logger.error("retrain_run_crashed", error=f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
