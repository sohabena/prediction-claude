"""S18: rebuild corpora, fair-value models and PPO specialists, then write an honest strategy report.

Orchestration only: every step calls an existing script or function. Nothing here activates a model
and nothing writes to ``models/specialists/``.

Per key: backup -> corpus -> audits -> fair model -> PPO -> evaluate 5 policies -> metadata (inactive)
-> ``is_tradeable`` check. Results land in ``docs/reports/s18/<key>.json``. ``--report`` renders the
markdown report purely from those JSON files; ``--recorded`` evaluates on recorded real-odds episodes.

Usage::

    python scripts/rebuild_all.py --keys t20i_men,odi_men,test_men [--max-timesteps N]
    python scripts/rebuild_all.py --recorded
    python scripts/rebuild_all.py --report
    python scripts/rebuild_all.py --keys t20i_men --dry-run
"""
from __future__ import annotations

import argparse
import gzip
import json
import pickle
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.logging import setup_logging  # noqa: E402

logger = setup_logging("rebuild_all")

POLICIES = ("model", "value", "heuristic", "random", "hold")
RECORDED_POLICIES = ("model", "value", "random", "hold")
FAMILY_KEYS = ("t20i_men", "odi_men", "test_men")
ALL_KEYS = (
    "t20i_men", "odi_men", "test_men", "t20i_women", "odi_women", "ipl", "bbl_men", "wbbl_women",
    "t20_blast", "franchise_t20", "first_class", "list_a", "franchise_t20",
)
ALL_KEYS = tuple(dict.fromkeys(ALL_KEYS))
FORMAT_LABEL = {
    "ipl": "IPL", "t20i_men": "T20", "t20i_women": "T20", "bbl_men": "T20", "wbbl_women": "T20",
    "odi_men": "ODI", "odi_women": "ODI", "test_men": "Test", "t20_blast": "T20",
    "franchise_t20": "T20", "first_class": "Test", "list_a": "ODI",
}
SOURCE_DIR = Path(r"C:\Users\sohai\Downloads\all_json")


@dataclass
class Paths:
    corpus_dir: Path = ROOT / "data" / "specialists"
    backup_dir: Path = ROOT / "data" / "specialists" / "_pre_s13"
    models_dir: Path = ROOT / "models" / "specialists_v2"
    fair_dir: Path = ROOT / "models" / "fair_value"
    report_dir: Path = ROOT / "docs" / "reports" / "s18"
    recorded_dir: Path = ROOT / "data" / "recorded"


def _clv_stats(bet_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    from scripts.train_format_specialists import _bootstrap_mean_ci

    vals = [float(b["clv"]) for b in bet_rows if b.get("clv") is not None]
    if not vals:
        return {"clv_n": 0, "clv_mean": None, "clv_ci95_low": None, "clv_ci95_high": None}
    lo, hi = _bootstrap_mean_ci(vals, 1000, 0)
    return {"clv_n": len(vals), "clv_mean": sum(vals) / len(vals), "clv_ci95_low": lo, "clv_ci95_high": hi}


def evaluate_policies(
    key: str, model_path: Optional[Path], episodes: List[List[Dict[str, Any]]],
    policies: tuple[str, ...], seed: int = 0,
) -> Dict[str, Any]:
    """Run each policy through ``evaluate_specialist_holdout`` on the same episodes."""
    from stable_baselines3 import PPO

    import scripts.train_format_specialists as tfs
    from rl.agent import PhoenixAgent

    results: Dict[str, Any] = {}
    fmt_name = tfs.SPECIALIST_MAP.get(key, {}).get("name", key)
    model: Any = None
    if "model" in policies and model_path is not None and model_path.exists():
        raw = PPO.load(str(model_path))
        vn = PhoenixAgent.default_normalize_path(model_path)
        vecnorm = pickle.load(open(vn, "rb")) if vn.exists() else None
        model = tfs.NormalizedModel(raw, vecnorm)
    for pol in policies:
        if pol == "model" and model is None:
            results[pol] = {"status": "no model"}
            continue
        try:
            m, rows = tfs.evaluate_specialist_holdout(
                model=model, specialist_key=key, format_name=fmt_name, test_episodes=episodes,
                max_bets_per_match=25, commission=tfs.TRAIN_COMMISSION,
                starting_bankroll=tfs.TRAIN_BANKROLL, policy=pol, include_baselines=False, seed=seed,
            )
        except FileNotFoundError as exc:
            results[pol] = {"status": f"unavailable: {exc}"}
            continue
        d = asdict(m)
        d.pop("baselines", None)
        d.update(_clv_stats(rows))
        d["commission_per_match"] = d["commission_paid"] / max(1, d["n_matches"])
        d["status"] = "ok"
        results[pol] = d
    return results


def _load_corpus(paths: Paths, key: str) -> List[Dict[str, Any]]:
    with gzip.open(paths.corpus_dir / f"corpus_{key}.json.gz", "rt", encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------- fixture
def make_fixture(n: int = 3, ticks: int = 40) -> List[Dict[str, Any]]:
    """Tiny synthetic corpus (home wins match 0, away wins match 1, ...) for the dry run."""
    import numpy as np

    rng = np.random.default_rng(7)
    out: List[Dict[str, Any]] = []
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(n):
        home_wins = i % 2 == 0
        start = t0 + timedelta(days=i)
        tl: List[Dict[str, Any]] = []
        p = 0.5
        for k in range(ticks):
            frac = k / ticks
            p = float(np.clip(p + rng.normal(0, 0.02), 0.1, 0.9))
            bh, ba = 1.0 / p, 1.0 / (1 - p)
            tl.append({
                "time": (start + timedelta(seconds=3 * k)).isoformat(), "match_id": f"fx{i}",
                "team_home": "H", "team_away": "A", "competition": "fixture", "match_format": "T20",
                "gender": "male", "specialist_key": "t20i_men",
                "back_home": round(bh, 2), "lay_home": round(bh + 0.05, 2),
                "back_away": round(ba, 2), "lay_away": round(ba + 0.05, 2),
                "implied_prob_home": round(p, 4), "implied_prob_away": round(1 - p, 4), "overround": 0.02,
                "is_live": True, "score": int(160 * frac), "wickets": int(8 * frac), "overs": round(20 * frac, 1),
                "run_rate": 8.0, "req_run_rate": 8.0, "innings": 1 if frac < 0.5 else 2,
                "balls_remaining": int(120 * (1 - frac)), "batting_team": "H", "bowling_team": "A",
                "status": "in_play", "volume_back_home": 1e4, "volume_lay_home": 1e4,
                "volume_back_away": 1e4, "volume_lay_away": 1e4, "source": "fixture",
                "_meta": {"winner": "H" if home_wins else "A", "loser": "A" if home_wins else "H",
                          "winner_is_home": home_wins, "gender": "male"},
            })
        out.append({
            "match_id": f"fx{i}", "team_home": "H", "team_away": "A", "competition": "fixture",
            "match_format": "T20", "gender": "male", "specialist_key": "t20i_men",
            "winner": "H" if home_wins else "A", "loser": "A" if home_wins else "H",
            "start_time": start.isoformat(), "total_ticks": len(tl), "ticks": tl,
            "market_model": "martingale",
        })
    return out


# --------------------------------------------------------------------------- steps
def step_backup(paths: Paths, key: str) -> Dict[str, Any]:
    src = paths.corpus_dir / f"corpus_{key}.json.gz"
    if not src.exists():
        return {"backed_up": False, "reason": "no existing corpus"}
    paths.backup_dir.mkdir(parents=True, exist_ok=True)
    dst = paths.backup_dir / src.name
    if not dst.exists():  # never overwrite a prior backup
        shutil.copy2(src, dst)
    return {"backed_up": True, "path": str(dst)}


def step_corpus(paths: Paths, key: str, target: int, dry_run: bool) -> Dict[str, Any]:
    if dry_run:
        matches = make_fixture()
        paths.corpus_dir.mkdir(parents=True, exist_ok=True)
        with gzip.open(paths.corpus_dir / f"corpus_{key}.json.gz", "wt", encoding="utf-8") as f:
            json.dump(matches, f)
        return {"matches": len(matches), "noise_mode": "fixture"}
    from scripts.prepare_specialist_datasets import build_specialist_dataset, index_cricsheet_catalog

    buckets = index_cricsheet_catalog(SOURCE_DIR)
    files = buckets.get(key, [])
    if not files:
        raise RuntimeError(f"no cricsheet files for {key}")
    out = build_specialist_dataset(key, FORMAT_LABEL[key], files, target_count=target, noise_mode="martingale")
    matches = json.loads(gzip.open(out, "rt", encoding="utf-8").read())
    return {"matches": len(matches), "noise_mode": "martingale", "path": str(out)}


def step_audits(paths: Paths, key: str, dry_run: bool) -> Dict[str, Any]:
    import scripts.audit_outcome_leakage as a1
    import scripts.audit_synthetic_edge as a2

    matches = _load_corpus(paths, key)
    res: Dict[str, Any] = {"enforced": not dry_run}
    try:
        ok1, rep1 = a1.audit_matches(matches)
        res["leakage"] = {"passed": bool(ok1), "max_delta": max([r["delta"] for r in rep1], default=None),
                          "buckets": rep1}
    except ValueError as exc:
        res["leakage"] = {"passed": False, "error": str(exc)}
    try:
        ok2, rep2 = a2.audit_matches(matches)
        res["edge"] = {"passed": bool(ok2), **rep2}
    except ValueError as exc:
        res["edge"] = {"passed": False, "error": str(exc)}
    res["log_lines"] = [
        f"audit_outcome_leakage {key}: {'PASS' if res['leakage']['passed'] else 'FAIL'}"
        + (f" max_auc_delta={res['leakage']['max_delta']:+.4f}" if res["leakage"].get("max_delta") is not None else ""),
        f"audit_synthetic_edge {key}: {'PASS' if res['edge']['passed'] else 'FAIL'}"
        + (f" excess_r2={res['edge']['r2']:+.4f} hit_rate={res['edge']['hit_rate']:.3f}" if "r2" in res["edge"] else ""),
    ]
    res["passed"] = bool(res["leakage"]["passed"] and res["edge"]["passed"])
    if not res["passed"] and not dry_run:
        err = RuntimeError(f"audits failed for {key}: {res['log_lines']}")
        err.result = res  # type: ignore[attr-defined]
        raise err
    return res


def step_fair(paths: Paths, key: str) -> Dict[str, Any]:
    from scripts.train_fair_value import train

    rep = train(key, paths.fair_dir, corpus=_load_corpus(paths, key))
    src = paths.fair_dir / f"{rep['family']}.joblib"
    shutil.copy2(src, paths.fair_dir / f"{rep['family']}_{key}.joblib")
    return rep


def step_ppo(paths: Paths, key: str, max_timesteps: Optional[int], dry_run: bool, seed: int) -> Dict[str, Any]:
    import scripts.train_format_specialists as tfs

    matches = _load_corpus(paths, key)
    kwargs: Dict[str, Any] = {}
    if dry_run:
        kwargs = dict(timesteps=256, n_envs=1, backend="dummy", eval_freq=128, overrides={"n_steps": 128, "batch_size": 64})
    metrics, _ = tfs.train_single_specialist(
        specialist_key=key, format_name=tfs.SPECIALIST_MAP.get(key, {}).get("name", key),
        models_dir=paths.models_dir, matches=matches, seed=seed,
        max_timesteps=max_timesteps, **kwargs,
    )
    manifest = json.loads((paths.models_dir / f"{key}_train_manifest.json").read_text(encoding="utf-8"))
    return {"matches_trained": metrics.matches_trained, "timesteps_run": metrics.timesteps,
            "manifest": str(paths.models_dir / f"{key}_train_manifest.json"),
            "planned_timesteps": manifest["planned_timesteps"], "split_sizes": manifest["split_sizes"]}


def step_eval(paths: Paths, key: str) -> Dict[str, Any]:
    import scripts.train_format_specialists as tfs

    matches = _load_corpus(paths, key)
    _, _, hold = tfs.split_matches(matches, key)
    episodes = [m["ticks"] for m in hold][:75]
    res = evaluate_policies(key, paths.models_dir / f"{key}_v1.zip", episodes, POLICIES)
    return {"n_holdout_matches": len(episodes), "policies": res}


def step_metadata(paths: Paths, key: str, evaluation: Dict[str, Any], ppo: Dict[str, Any]) -> Dict[str, Any]:
    """Register inactive metadata and ask ``is_tradeable`` as if active (nothing is saved active)."""
    from rl.model_zoo import ModelZoo

    model = dict(evaluation["policies"]["model"])
    rnd = evaluation["policies"].get("random", {})
    if model.get("status") != "ok":
        return {"registered": False, "tradeable": False, "reason": "no model evaluation"}
    model["edge_vs_random"] = round(model["mean_match_pnl"] - rnd.get("mean_match_pnl", 0.0), 2)
    model["timesteps"] = ppo["timesteps_run"]
    model["matches_trained"] = ppo["matches_trained"]
    model["market_model"] = "martingale"
    zoo = ModelZoo(models_dir=paths.models_dir)
    zoo.register_specialist(key, paths.models_dir / f"{key}_v1.zip", model.get("format_name", key),
                            ppo["matches_trained"], model, active=False)
    inactive = zoo.is_tradeable(key)
    saved_active = zoo._metadata[key]["active"]
    zoo._metadata[key]["active"] = True  # in memory only; never saved
    ok, reason = zoo.is_tradeable(key)
    zoo._metadata[key]["active"] = saved_active
    return {"registered": True, "active_saved": bool(saved_active), "tradeable_if_activated": bool(ok),
            "reason": reason, "as_registered": {"tradeable": inactive[0], "reason": inactive[1]}}


def run_key(paths: Paths, key: str, *, target: int, max_timesteps: Optional[int], dry_run: bool,
            seed: int = 0) -> Dict[str, Any]:
    """Run all eight steps for one key; stops on the first failure."""
    out: Dict[str, Any] = {"key": key, "started_at": datetime.now(timezone.utc).isoformat(), "steps": {}}
    timings: Dict[str, float] = {}

    def run(name: str, fn, *a, **k):
        t = time.time()
        logger.info("rebuild_step", key=key, step=name)
        print(f"[{key}] {name} ...", flush=True)
        try:
            out["steps"][name] = fn(*a, **k)
        except Exception as exc:  # persist what we know, then stop on the first failure
            out["steps"][name] = {"error": str(exc), **(getattr(exc, "result", None) or {})}
            out["failed_step"] = name
            (paths.report_dir / f"{key}.json").write_text(json.dumps({**out, "timings_s": timings}, indent=2, default=str), encoding="utf-8")
            raise
        timings[name] = round(time.time() - t, 2)
        paths.report_dir.mkdir(parents=True, exist_ok=True)
        (paths.report_dir / f"{key}.json").write_text(json.dumps({**out, "timings_s": timings}, indent=2, default=str), encoding="utf-8")

    run("backup", step_backup, paths, key)
    run("corpus", step_corpus, paths, key, target, dry_run)
    run("audits", step_audits, paths, key, dry_run)
    run("fair", step_fair, paths, key)
    run("ppo", step_ppo, paths, key, max_timesteps, dry_run, seed)
    run("eval", step_eval, paths, key)
    run("metadata", step_metadata, paths, key, out["steps"]["eval"], out["steps"]["ppo"])
    out["steps"]["tradeable"] = {k: out["steps"]["metadata"].get(k) for k in ("tradeable_if_activated", "reason")}
    out["timings_s"] = timings
    (paths.report_dir / f"{key}.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    return out


def run_dry(keys: List[str]) -> Dict[str, Any]:
    """Dry run on a 3-match fixture inside a temp tree. Leaves the repo untouched."""
    tmp = Path(tempfile.mkdtemp(prefix="s18_dry_"))
    paths = Paths(corpus_dir=tmp / "specialists", backup_dir=tmp / "specialists" / "_pre_s13",
                  models_dir=tmp / "specialists_v2", fair_dir=tmp / "fair_value",
                  report_dir=tmp / "reports", recorded_dir=tmp / "recorded")
    paths.corpus_dir.mkdir(parents=True)
    # Pre-existing corpus so the backup step is exercised.
    with gzip.open(paths.corpus_dir / f"corpus_{keys[0]}.json.gz", "wt", encoding="utf-8") as f:
        json.dump(make_fixture(), f)
    # The value evaluator reads Path("models/fair_value/<family>.joblib") (relative to cwd): lend it ours.
    lend = Path("models/fair_value/t20.joblib")
    lent = not lend.exists()
    t0 = time.time()
    results = {}
    try:
        for key in keys:
            r = _run_key_with_lend(paths, key, lend)
            results[key] = r
    finally:
        if lent and lend.exists():
            lend.unlink()
            try:
                lend.parent.rmdir()
            except OSError:
                pass
    return {"tmp": str(tmp), "elapsed_s": round(time.time() - t0, 1), "results": results}


def _run_key_with_lend(paths: Paths, key: str, lend: Path) -> Dict[str, Any]:
    """Run steps; copy the freshly trained fair model into the evaluator's hard-coded location if absent."""
    orig = step_fair

    def fair_and_lend(p: Paths, k: str) -> Dict[str, Any]:
        rep = orig(p, k)
        if not lend.exists():
            lend.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p.fair_dir / f"{rep['family']}.joblib", lend)
        return rep

    globals()["step_fair"] = fair_and_lend
    try:
        return run_key(paths, key, target=0, max_timesteps=None, dry_run=True)
    finally:
        globals()["step_fair"] = orig


# --------------------------------------------------------------------------- recorded
def run_recorded(paths: Paths, keys: List[str]) -> Dict[str, Any]:
    """Export recorded matches (S16) if the DB is reachable, then evaluate on them."""
    import subprocess

    from scripts.export_recorded_corpus import load_recorded

    export: Dict[str, Any] = {}
    try:
        p = subprocess.run([sys.executable, str(ROOT / "scripts" / "export_recorded_corpus.py"),
                            "--out-dir", str(paths.recorded_dir)], capture_output=True, text=True,
                           timeout=300, cwd=str(ROOT), env={**__import__("os").environ, "PYTHONPATH": str(ROOT)})
        export = {"returncode": p.returncode, "stdout_tail": p.stdout[-800:], "stderr_tail": p.stderr[-800:]}
    except Exception as exc:  # DB unreachable etc.
        export = {"returncode": None, "error": str(exc)}
    paths.report_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = paths.recorded_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    out: Dict[str, Any] = {"export": export, "manifest_matches": len(manifest.get("matches", [])),
                           "manifest_rejected": len(manifest.get("rejected", [])), "keys": {}}
    rec_keys = sorted(d.name for d in paths.recorded_dir.iterdir() if d.is_dir()) if paths.recorded_dir.exists() else []
    for key in rec_keys:
        eps = load_recorded(key, paths.recorded_dir)
        if not eps:
            continue
        res = evaluate_policies(key, paths.models_dir / f"{key}_v1.zip", eps, RECORDED_POLICIES)
        out["keys"][key] = {"n_matches": len(eps), "policies": res}
    (paths.report_dir / "recorded.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    return out


# --------------------------------------------------------------------------- report
def _f(v: Any, spec: str = "+,.0f") -> str:
    return "n/a" if v is None else format(v, spec)


def _row(pol: str, d: Dict[str, Any]) -> str:
    if d.get("status") != "ok":
        return f"| {pol} | {d.get('status', 'n/a')} | | | | | |"
    clv = "n/a" if d["clv_mean"] is None else (
        f"{d['clv_mean']:+.4f} ({d['clv_ci95_low']:+.4f}..{d['clv_ci95_high']:+.4f}, n={d['clv_n']})")
    return (f"| {pol} | {d['mean_match_pnl']:+,.0f} ({d['pnl_ci95_low']:+,.0f}..{d['pnl_ci95_high']:+,.0f}) "
            f"| {clv} | {d['max_drawdown_pct']:.2f} | {d['avg_bets_per_match']:.2f} "
            f"| {d['commission_per_match']:,.0f} | {d['n_matches']} |")


HEAD = ("| policy | mean per-match P&L (95% CI) | CLV mean (95% CI) | max DD % | bets/match "
        "| commission/match | n matches |\n|---|---|---|---|---|---|---|")


def _verdict(synth: Dict[str, Dict[str, Any]], rec_n: int) -> List[str]:
    """Rule-based recommendation; every number is read from the JSON data."""
    lines: List[str] = []
    for key, r in synth.items():
        pol = r["steps"]["eval"]["policies"]
        def ci(p: str) -> Optional[tuple]:
            d = pol.get(p, {})
            return (d["mean_match_pnl"], d["pnl_ci95_low"], d["pnl_ci95_high"]) if d.get("status") == "ok" else None
        m, v, rnd = ci("model"), ci("value"), ci("random")
        parts = []
        for name, c in (("model", m), ("value", v)):
            if c is None:
                parts.append(f"{name}: not evaluated")
            elif c[1] > 0:
                parts.append(f"{name}: CI lower bound {c[1]:+,.0f} > 0")
            else:
                parts.append(f"{name}: CI lower bound {c[1]:+,.0f} <= 0 (no demonstrated edge)")
        if rnd:
            parts.append(f"random mean {rnd[0]:+,.0f}")
        lines.append(f"- {key}: " + "; ".join(parts))
    for key, r in synth.items():
        pol = r["steps"]["eval"]["policies"]
        for base in ("random", "heuristic"):
            d = pol.get(base, {})
            if d.get("status") == "ok" and d["pnl_ci95_low"] > 0:
                lines.append(
                    f"- WARNING {key}: `{base}` baseline has CI lower bound {d['pnl_ci95_low']:+,.0f} > 0 "
                    f"(mean {d['mean_match_pnl']:+,.0f}); a market with no edge should not pay a no-skill policy, "
                    "so positive synthetic P&L here is evidence of an exploitable simulator, not of skill.")
    any_model_edge = any((r["steps"]["eval"]["policies"].get("model", {}).get("pnl_ci95_low") or -1) > 0 for r in synth.values())
    any_value_edge = any((r["steps"]["eval"]["policies"].get("value", {}).get("pnl_ci95_low") or -1) > 0 for r in synth.values())
    lines.append("")
    if rec_n == 0:
        rec = "collect more real data"
        why = (f"recorded real-odds sample is n={rec_n} matches, so no real-edge claim is supported; synthetic "
               "results come from a martingale market built to carry no edge, so a positive result there would indicate an artefact, not alpha.")
    elif any(r["steps"]["eval"]["policies"].get(b, {}).get("pnl_ci95_low", -1) > 0 for r in synth.values() for b in ("random", "heuristic")):
        rec, why = "collect more real data", "the synthetic market still pays no-skill baselines (see warnings), so it cannot rank RL against value betting."
    elif any_value_edge and not any_model_edge:
        rec, why = "switch to the value policy", "value policy has a CI lower bound above zero where PPO does not."
    elif any_model_edge:
        rec, why = "continue RL", "PPO has a CI lower bound above zero on at least one key."
    else:
        rec, why = "collect more real data", "neither policy shows a CI lower bound above zero."
    lines.append(f"**Recommendation: {rec}.** Reasoning: {why} (recorded n={rec_n}).")
    return lines


def write_report(paths: Paths, keys: List[str], out_path: Optional[Path] = None) -> Path:
    today = date.today().isoformat()
    out_path = out_path or ROOT / "docs" / "reports" / f"strategy-report-{today}.md"
    synth = {k: json.loads((paths.report_dir / f"{k}.json").read_text(encoding="utf-8"))
             for k in keys if (paths.report_dir / f"{k}.json").exists()}
    blocked = {k: r for k, r in synth.items() if "eval" not in r["steps"]}
    synth = {k: r for k, r in synth.items() if "eval" in r["steps"]}
    rec_path = paths.report_dir / "recorded.json"
    rec = json.loads(rec_path.read_text(encoding="utf-8")) if rec_path.exists() else {"keys": {}}
    rec_n = sum(v["n_matches"] for v in rec.get("keys", {}).values())
    files = [f"{k}.json" for k in [*synth, *blocked]] + (["recorded.json"] if rec_path.exists() else [])
    L: List[str] = [f"# Strategy report {today}", "",
                    "All numbers are rendered from the JSON files listed in the appendix. Models were NOT activated.", ""]
    L += ["## Verdict", ""] + _verdict(synth, rec_n) + [""]
    L += [f"## Recorded real odds", "",
          f"Recorded matches evaluated: n={rec_n}. Export matches in manifest: {rec.get('manifest_matches', 0)}, "
          f"rejected: {rec.get('manifest_rejected', 0)}. Export return code: {rec.get('export', {}).get('returncode')}.", ""]
    for key, v in rec.get("keys", {}).items():
        L += [f"### {key} recorded (n={v['n_matches']})", "", HEAD] + [_row(p, v["policies"][p]) for p in v["policies"]] + [""]
    L += ["## Synthetic holdout (martingale market, chronological holdout)", ""]
    for key, r in synth.items():
        ev = r["steps"]["eval"]
        L += [f"### {key} (holdout n={ev['n_holdout_matches']})", "", HEAD]
        L += [_row(p, ev["policies"][p]) for p in POLICIES if p in ev["policies"]] + [""]
    L += ["## Fair-value calibration (holdout, vs market-implied)", "",
          "| key | family | n ticks | fair Brier | market Brier | fair ECE | market ECE |", "|---|---|---|---|---|---|---|"]
    for key, r in synth.items():
        f = r["steps"]["fair"]
        L.append(f"| {key} | {f['family']} | {f['n_ticks']} | {f['fair']['brier']:.4f} | {f['market_implied']['brier']:.4f} "
                 f"| {f['fair']['ece']:.4f} | {f['market_implied']['ece']:.4f} |")
    L += ["", "## S17 provenance gate (evaluated as if activated; nothing was activated)", "",
          "| key | tradeable | reason |", "|---|---|---|"]
    for key, r in synth.items():
        t = r["steps"]["tradeable"]
        L.append(f"| {key} | {t['tradeable_if_activated']} | {t['reason']} |")
    L += ["", "## Audits", ""]
    for key, r in synth.items():
        L += [f"- `{ln}`" for ln in r["steps"]["audits"]["log_lines"]]
    for key, r in blocked.items():
        a = r["steps"].get("audits", {})
        L += [f"- `{ln}`" for ln in a.get("log_lines", [])] + [
            f"- {key}: BLOCKED at step `{r.get('failed_step')}`; not trained (audits must pass before training)."]
    L += ["", "## Training", "", "| key | train matches | planned steps | steps run |", "|---|---|---|---|"]
    for key, r in synth.items():
        p = r["steps"]["ppo"]
        L.append(f"| {key} | {p['matches_trained']} | {p['planned_timesteps']} | {p['timesteps_run']} |")
    L += ["", "## Appendix: source JSON files", ""] + [f"- `{paths.report_dir.relative_to(ROOT) if paths.report_dir.is_relative_to(ROOT) else paths.report_dir}/{f}`" for f in files]
    L += [f"- `models/specialists_v2/<key>_train_manifest.json`", f"- `models/fair_value/<family>_<key>_report.json`", ""]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(L), encoding="utf-8")
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keys", default=",".join(FAMILY_KEYS), help="comma separated, or 'all'")
    ap.add_argument("--dry-run", action="store_true", help="run every step on a 3-match fixture in a temp tree")
    ap.add_argument("--target", type=int, default=350, help="matches per corpus (0 = all)")
    ap.add_argument("--max-timesteps", type=int, default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--recorded", action="store_true", help="export + evaluate recorded real odds")
    ap.add_argument("--report", action="store_true", help="write the markdown report from existing JSON")
    args = ap.parse_args()
    keys = list(ALL_KEYS) if args.keys == "all" else [k.strip() for k in args.keys.split(",") if k.strip()]
    paths = Paths()
    if args.dry_run:
        res = run_dry(keys)
        print(f"dry run complete in {res['elapsed_s']}s")
        return 0
    if args.recorded:
        r = run_recorded(paths, keys)
        print(json.dumps({k: v["n_matches"] for k, v in r["keys"].items()}), "export rc", r["export"].get("returncode"))
        return 0
    if args.report:
        print(write_report(paths, keys))
        return 0
    for key in keys:
        run_key(paths, key, target=args.target, max_timesteps=args.max_timesteps, dry_run=False, seed=args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
