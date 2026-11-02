"""Export recorded live matches (real LotusBook prices) into a replayable corpus.

Reads ``odds_ticks`` / ``odds_ticks_historical`` joined (as-of) to ``match_context`` and
``match_results`` and writes ``data/recorded/<specialist_key>/<match_id>.json.gz``, one episode
per match, in the shape ``rl.environment.CricketBettingEnv`` consumes: a list of tick dicts with
the settlement result only in ``_meta`` on the first tick.

Usage::

    python scripts/export_recorded_corpus.py [--force] [--cadence decision|raw]
                                             [--min-ticks 50] [--min-coverage 0.6]
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper.match_filter import is_simulated_name  # noqa: E402
from shared.logging import setup_logging  # noqa: E402

logger = setup_logging("export_recorded_corpus")

RECORDED_DIR = ROOT / "data" / "recorded"
RECORDED_SOURCE = "recorded_lotusbook"  # tag written on exported ticks
RECORDED_ODDS_SOURCE = "lotusbook"  # ``source`` value of real scraped ticks in odds_ticks
PRICE_KEYS = ("back_home", "lay_home", "back_away", "lay_away")
VOLUME_KEYS = ("volume_back_home", "volume_lay_home", "volume_back_away", "volume_lay_away")
CONTEXT_KEYS = (
    "is_live", "score", "wickets", "overs", "run_rate", "req_run_rate",
    "innings", "balls_remaining", "batting_team", "bowling_team", "status", "match_format",
)
MIN_RELIABLE_SAMPLE = 30
DECISION_GAP_SECONDS = 180.0
COVERAGE_BINS = 20


# --------------------------------------------------------------------------- helpers
def _as_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    mapping = getattr(row, "_mapping", None)
    return dict(mapping) if mapping is not None else dict(row)


def _to_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _valid_price(v: Any) -> bool:
    return v is not None and isinstance(v, (int, float)) and not isinstance(v, bool) and v > 1.0


def _asof_context(
    contexts: list[tuple[datetime, dict[str, Any]]], ts: datetime, idx: int
) -> tuple[Optional[dict[str, Any]], int]:
    """Advance through time-sorted contexts; return the latest one with time <= ts."""
    while idx + 1 < len(contexts) and contexts[idx + 1][0] <= ts:
        idx += 1
    if contexts and contexts[idx][0] <= ts:
        return contexts[idx][1], idx
    return None, idx


def _is_decision(prev: Optional[dict[str, Any]], tick: dict[str, Any], last_decision: Optional[datetime]) -> bool:
    """Decision-tick contract (README, S12), evaluated on a recorded tick."""
    ts = _to_dt(tick["time"])
    if prev is None or last_decision is None:
        return True
    if ts is not None and (ts - last_decision).total_seconds() >= DECISION_GAP_SECONDS:
        return True
    if "overs" not in tick or "overs" not in prev:
        return False
    if tick.get("innings") != prev.get("innings"):
        return True
    if (tick.get("wickets") or 0) > (prev.get("wickets") or 0):
        return True
    if math.floor(tick.get("overs") or 0) != math.floor(prev.get("overs") or 0):
        return True
    return (tick.get("score") or 0) - (prev.get("score") or 0) >= 4


def _coverage(all_times: list[datetime], kept_times: list[datetime]) -> float:
    """Fraction of equal time bins across the raw span that hold at least one kept tick."""
    if not all_times or not kept_times:
        return 0.0
    start, end = min(all_times), max(all_times)
    span = (end - start).total_seconds()
    if span <= 0:
        return 1.0
    hit = {
        min(COVERAGE_BINS - 1, int((t - start).total_seconds() / span * COVERAGE_BINS))
        for t in kept_times
    }
    return len(hit) / COVERAGE_BINS


# --------------------------------------------------------------------------- core
def build_episode_checked(
    odds_rows: Iterable[Any],
    context_rows: Iterable[Any],
    result_row: Optional[Any],
    *,
    cadence: str = "raw",
    specialist_key: str = "",
    gender: str = "",
    min_ticks: int = 0,
    min_coverage: float = 0.0,
) -> tuple[Optional[list[dict[str, Any]]], str]:
    """Build an episode and return ``(episode, reason)``; ``episode`` is None when rejected."""
    if cadence not in ("decision", "raw"):
        raise ValueError("cadence must be 'decision' or 'raw'")
    result = _as_dict(result_row) if result_row is not None else None
    if not result or not result.get("winner"):
        return None, "no_result"

    contexts = sorted(
        ((_to_dt(c.get("time")), c) for c in (_as_dict(r) for r in context_rows)),
        key=lambda p: p[0] or datetime.min.replace(tzinfo=timezone.utc),
    )
    contexts = [(t, c) for t, c in contexts if t is not None]
    odds = sorted(
        ((_to_dt(o.get("time")), o) for o in (_as_dict(r) for r in odds_rows)),
        key=lambda p: p[0] or datetime.min.replace(tzinfo=timezone.utc),
    )
    odds = [(t, o) for t, o in odds if t is not None]
    if not odds:
        return None, "no_ticks"

    ticks: list[dict[str, Any]] = []
    ctx_idx = 0
    prev: Optional[dict[str, Any]] = None
    last_decision: Optional[datetime] = None
    for ts, o in odds:
        if not all(_valid_price(o.get(k)) for k in PRICE_KEYS):
            continue  # suspended / incomplete market
        ctx, ctx_idx = _asof_context(contexts, ts, ctx_idx)
        tick: dict[str, Any] = {
            "time": ts.isoformat(),
            "match_id": o.get("match_id") or result.get("match_id"),
            "team_home": o.get("team_home") or result.get("team_home", ""),
            "team_away": o.get("team_away") or result.get("team_away", ""),
            "competition": o.get("competition") or "",
            "gender": gender,
            "specialist_key": specialist_key,
        }
        for k in PRICE_KEYS + ("back_draw", "lay_draw") + VOLUME_KEYS:
            tick[k] = o.get(k)
        inv_h, inv_a = 1.0 / o["back_home"], 1.0 / o["back_away"]
        tick["implied_prob_home"] = o.get("implied_prob_home") or inv_h / (inv_h + inv_a)
        tick["implied_prob_away"] = o.get("implied_prob_away") or inv_a / (inv_h + inv_a)
        tick["overround"] = o.get("overround") or (inv_h + inv_a)
        tick["is_live"] = bool(o.get("is_live", False))
        if ctx is not None:
            for k in CONTEXT_KEYS:
                if ctx.get(k) is not None:
                    tick[k] = ctx[k]
        else:
            for k in ("score", "wickets", "overs", "innings"):
                if o.get(k) is not None:
                    tick[k] = o[k]
        tick["source"] = RECORDED_SOURCE
        tick["market_model"] = "real"

        if cadence == "decision":
            if not _is_decision(prev, tick, last_decision):
                prev = tick
                continue
            last_decision = ts
        else:
            last_decision = ts
        prev = tick
        ticks.append(tick)

    raw_times = [t for t, _ in odds]
    kept_times = [_to_dt(t["time"]) for t in ticks]
    kept_times = [t for t in kept_times if t is not None]
    if len(ticks) < max(1, min_ticks):
        return None, "too_few_ticks"
    if _coverage(raw_times, kept_times) < min_coverage:
        return None, "low_coverage"

    home = result.get("team_home") or ticks[0]["team_home"]
    winner = result["winner"]
    ticks[0]["_meta"] = {
        "winner": winner,
        "loser": result.get("loser"),
        "winner_is_home": winner == home,
        "team_home": home,
        "team_away": result.get("team_away") or ticks[0]["team_away"],
        "result_type": result.get("result_type") or "win",
        "gender": gender,
    }
    return ticks, "ok"


def build_episode(
    odds_rows: Iterable[Any],
    context_rows: Iterable[Any],
    result_row: Optional[Any],
    *,
    cadence: str = "raw",
    **kwargs: Any,
) -> Optional[list[dict[str, Any]]]:
    """As-of join odds to context, drop suspended rows, require a result; None if rejected."""
    episode, _ = build_episode_checked(odds_rows, context_rows, result_row, cadence=cadence, **kwargs)
    return episode


def load_recorded(key: str, base_dir: Path = RECORDED_DIR) -> list[list[dict[str, Any]]]:
    """Load every recorded episode for a specialist key (sorted by file name)."""
    episodes: list[list[dict[str, Any]]] = []
    for path in sorted((Path(base_dir) / key).glob("*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            episodes.append(json.load(f))
    return episodes


def _write_episode(path: Path, episode: list[dict[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(episode, default=str).encode("utf-8")
    with gzip.open(path, "wb") as f:
        f.write(payload)
    return hashlib.sha1(payload).hexdigest()


def export_matches(
    matches: Iterable[dict[str, Any]],
    *,
    out_dir: Path,
    key_resolver: Callable[[dict[str, Any]], str],
    cadence: str = "decision",
    min_ticks: int = 50,
    min_coverage: float = 0.6,
    force: bool = False,
) -> dict[str, Any]:
    """Write episodes for ``matches`` (dicts with odds/context/result). Returns the manifest."""
    out_dir = Path(out_dir)
    manifest_path = out_dir / "manifest.json"
    prior: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            prior = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            prior = {}
    entries: dict[str, dict[str, Any]] = {e["match_id"]: e for e in prior.get("matches", [])}
    rejected: dict[str, dict[str, Any]] = {e["match_id"]: e for e in prior.get("rejected", [])}

    for m in matches:
        mid = str(m["match_id"])
        result = m.get("result")
        key = key_resolver(m)
        path = out_dir / key / f"{mid}.json.gz"
        if path.exists() and not force:
            continue
        gender = "female" if key.endswith("_women") else "male"
        episode, reason = build_episode_checked(
            m.get("odds", []), m.get("context", []), result, cadence=cadence,
            specialist_key=key, gender=gender, min_ticks=min_ticks, min_coverage=min_coverage,
        )
        if episode is None:
            rejected[mid] = {"match_id": mid, "specialist_key": key, "reason": reason}
            entries.pop(mid, None)
            continue
        digest = _write_episode(path, episode)
        rejected.pop(mid, None)
        entries[mid] = {
            "match_id": mid, "specialist_key": key, "n_ticks": len(episode),
            "first_time": episode[0]["time"], "last_time": episode[-1]["time"], "sha1": digest,
        }

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cadence": cadence, "min_ticks": min_ticks, "min_coverage": min_coverage,
        "matches": sorted(entries.values(), key=lambda e: e["match_id"]),
        "rejected": sorted(rejected.values(), key=lambda e: e["match_id"]),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def counts_per_key(manifest: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in manifest.get("matches", []):
        counts[e["specialist_key"]] = counts.get(e["specialist_key"], 0) + 1
    return counts


# --------------------------------------------------------------------------- DB layer
def select_real_rows(rows: Iterable[Any]) -> tuple[list[dict[str, Any]], int]:
    """Keep only real LotusBook ticks from simulated-free matches; return ``(kept, dropped)``.

    Synthetic rows (``source='cricsheet_official'``) and simulated-league names (SRL, Virtual, ...)
    are dropped even if the SQL filter let them through.
    """
    kept: list[dict[str, Any]] = []
    dropped = 0
    for row in rows:
        d = _as_dict(row)
        if d.get("source") != RECORDED_ODDS_SOURCE or is_simulated_name(
            d.get("competition"), d.get("team_home"), d.get("team_away")
        ):
            dropped += 1
            continue
        kept.append(d)
    return kept, dropped


async def _historical_table_exists(session: Any) -> bool:
    """True when ``odds_ticks_historical`` exists (it is created by the S08 migration)."""
    from sqlalchemy import text

    row = (await session.execute(text("SELECT to_regclass('odds_ticks_historical') AS t"))).fetchone()
    if row is None:
        return False
    return _as_dict(row).get("t") is not None


async def _fetch_matches() -> list[dict[str, Any]]:
    from sqlalchemy import text

    from shared.db import get_session

    odds_cols = (
        "time, match_id, team_home, team_away, competition, back_home, lay_home, back_draw, lay_draw, "
        "back_away, lay_away, volume_back_home, volume_lay_home, volume_back_away, volume_lay_away, "
        "implied_prob_home, implied_prob_away, overround, score, wickets, overs, innings, is_live, source"
    )
    ctx_cols = (
        "time, match_id, is_live, score, wickets, overs, run_rate, req_run_rate, innings, "
        "balls_remaining, batting_team, bowling_team, status, match_format"
    )
    matches: list[dict[str, Any]] = []
    simulated_matches = 0
    dropped_rows = 0
    async with get_session() as session:
        has_historical = await _historical_table_exists(session)
        if not has_historical:
            logger.info("historical_table_missing", table="odds_ticks_historical", action="using odds_ticks only")
        tables = ("odds_ticks", "odds_ticks_historical") if has_historical else ("odds_ticks",)
        results = (await session.execute(text("SELECT * FROM match_results"))).fetchall()
        for r in results:
            res = _as_dict(r)
            mid = res["match_id"]
            if is_simulated_name(res.get("team_home"), res.get("team_away")):
                simulated_matches += 1
                continue
            odds: list[Any] = []
            for table in tables:
                rows = await session.execute(
                    text(
                        f"SELECT {odds_cols} FROM {table} WHERE match_id = :m AND source = :src ORDER BY time"
                    ),
                    {"m": mid, "src": RECORDED_ODDS_SOURCE},
                )
                kept, dropped = select_real_rows(rows.fetchall())
                dropped_rows += dropped
                odds.extend(kept)
            if not odds:
                continue
            ctx = await session.execute(
                text(f"SELECT {ctx_cols} FROM match_context WHERE match_id = :m ORDER BY time"), {"m": mid}
            )
            context = [_as_dict(x) for x in ctx.fetchall()]
            matches.append({"match_id": mid, "result": res, "odds": odds, "context": context})
    logger.info(
        "recorded_rows_filtered",
        simulated_matches=simulated_matches,
        dropped_rows=dropped_rows,
        matches=len(matches),
    )
    return matches


def _resolver() -> Callable[[dict[str, Any]], str]:
    from rl.model_zoo import ModelZoo

    zoo = ModelZoo()

    def resolve(m: dict[str, Any]) -> str:
        res = m["result"]
        fmt = next((c.get("match_format") for c in m["context"] if c.get("match_format")), "") or ""
        comp = next((o.get("competition") for o in m["odds"] if o.get("competition")), "") or ""
        return zoo.resolve_specialist_key(fmt, comp, "", res.get("team_home") or "", res.get("team_away") or "")

    return resolve


async def main_async(args: argparse.Namespace) -> int:
    matches = await _fetch_matches()
    if not matches:
        print(
            "No recorded matches: nothing qualified after excluding simulated (SRL/virtual) and "
            "synthetic (cricsheet_official) rows. Nothing supports any real-edge claim yet."
        )
        return 0
    manifest = export_matches(
        matches, out_dir=Path(args.out_dir), key_resolver=_resolver(), cadence=args.cadence,
        min_ticks=args.min_ticks, min_coverage=args.min_coverage, force=args.force,
    )
    counts = counts_per_key(manifest)
    print(f"Recorded matches exported: {sum(counts.values())}  rejected: {len(manifest['rejected'])}")
    for key, n in sorted(counts.items()):
        warn = f"  WARNING: below {MIN_RELIABLE_SAMPLE}, real-edge claims are not statistically reliable" if n < MIN_RELIABLE_SAMPLE else ""
        print(f"  {key}: {n}{warn}")
    if not counts:
        print("No recorded matches passed the quality thresholds; nothing supports any real-edge claim yet.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", default=str(RECORDED_DIR))
    p.add_argument("--cadence", choices=("decision", "raw"), default="decision")
    p.add_argument("--min-ticks", type=int, default=50)
    p.add_argument("--min-coverage", type=float, default=0.6)
    p.add_argument("--force", action="store_true")
    return asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
