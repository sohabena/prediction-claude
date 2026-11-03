"""S74 replay: run the exact paper-trading cycle (``paper_trader.run_cycle``) over every recorded match, as if live.

Usage::

    python scripts/replay_paper.py --from 2026-11-01                       # every recorded match, all variants
    python scripts/replay_paper.py --from 2026-11-01 --variants w05_e03,market_only --out reports/replay/quick
    python scripts/replay_paper.py --from 2026-11-01 --include-srl         # also simulated (SRL) matches

Clock: decisions every 15 s of recorded time from ``--from`` to ``--to`` (default: the last recorded tick). When no
real match has a fresh price and no bet is open, the clock jumps to the next recorded tick on the 15 s grid. Nothing
can happen in between, so the jump gives the same decisions as stepping (a parity-tested shortcut).

Reads: the recorded rows are loaded once, read-only (``virtual_trading.replay_feed.ReplayFeed``), with the session set
to READ ONLY. Writes: the in-memory ledger (``virtual_trading.memory_session.MemorySession``) and the files under
``--out`` (summary.json, summary.md, bets.csv). ``paper_bets`` is never touched.

Output per variant: intents, fills, settled bets, CLV mean with a seeded bootstrap 95% CI, P&L with a CI, the worst
case per match, churn (trades per match and the share of trades under 2,000 stake), and a per-match table. S75 guard
refusals are counted by reason across the run. The
summary files do not contain wall-clock times, so the same inputs give the same files. Runtime is printed only.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from backend.models.paper_bets import PaperBet  # noqa: E402
from virtual_trading import paper_ledger as ledger  # noqa: E402
from virtual_trading import paper_review as review  # noqa: E402
from virtual_trading import paper_trader as pt  # noqa: E402
from virtual_trading.memory_session import MemorySession  # noqa: E402
from virtual_trading.replay_feed import ReplayFeed  # noqa: E402

STEP = timedelta(seconds=15)  # the live cycle's period
SMALL_TRADE = 2_000.0  # plan: churn is measured as the share of trades under this stake
REPLAY_DIR = ROOT / "reports" / "replay"
BET_COLUMNS = [c.name for c in PaperBet.__table__.columns]
TRADE_STATUSES = review.FILLED  # filled, settled or void: the bets that were matched


# ----------------------------------------------------------------------------- the clock
@dataclass
class ReplayResult:
    session: MemorySession
    start: datetime
    end: datetime
    cycles: int = 0  # cycles that ran (decisions taken at each)
    skipped_steps: int = 0  # grid steps jumped over (no fresh price, no open bet)
    intents: list[dict[str, Any]] = field(default_factory=list)  # every intent, with decided_at
    skipped_registry: dict[str, int] = field(default_factory=dict)
    skipped_matches: dict[str, int] = field(default_factory=dict)
    guard_refusals: dict[str, int] = field(default_factory=dict)  # S75: every refusal, by reason, summed over cycles
    matches_considered: set[str] = field(default_factory=set)


def _grid_ceil(t: datetime, start: datetime) -> datetime:
    """The first 15 s grid point (from ``start``) at or after ``t``."""
    steps = math.ceil((t - start) / STEP)
    return start + STEP * steps


def _add_counts(into: dict, counts: Mapping[str, int]) -> None:
    for key, value in counts.items():
        into[key] = into.get(key, 0) + int(value)


async def replay_window(
    feed: Any,
    probability: Any,
    variants: Sequence[pt.Variant],
    *,
    start: datetime,
    end: datetime,
    include_simulated: bool = False,
    guards: Optional[pt.GuardConfig] = None,
) -> ReplayResult:
    """Step the clock from ``start`` to ``end`` (inclusive) and run ``run_cycle`` at each step on the in-memory ledger.
    ``guards`` defaults to the S75 settings (``GuardConfig()``)."""
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware")
    result = ReplayResult(session=MemorySession(), start=start, end=end)
    session = result.session
    now = start
    while now <= end:
        feed.seek(now)
        open_bets = any(r.status in ("pending", "filled") for r in session.rows)
        fresh = await feed.last_prices(now)
        if not fresh and not open_bets:
            nxt = feed.next_price_time(now)
            if nxt is None or nxt > end:
                result.skipped_steps += max(0, math.floor((end - now) / STEP))
                break
            target = _grid_ceil(nxt, start)
            if target > end:
                break
            result.skipped_steps += int((target - now) / STEP) - 1
            now = target
            continue

        report = await pt.run_cycle(
            session, feed, probability, variants, now=now, include_simulated=include_simulated,
            guards=guards or pt.GuardConfig(),
        )
        result.cycles += 1
        _add_counts(result.skipped_registry, report["skipped_registry"])
        _add_counts(result.skipped_matches, report["skipped_matches"])
        _add_counts(result.guard_refusals, report["guard_refusals"])
        result.matches_considered.update(m["match_id"] for m in report["matches"])
        for intent in report["intent_detail"]:
            result.intents.append({**intent, "decided_at": now.isoformat()})
        now = now + STEP
    return result


# ----------------------------------------------------------------------------- metrics
def _bet_dicts(rows: Sequence[Any]) -> list[dict[str, Any]]:
    return [{name: getattr(row, name) for name in BET_COLUMNS} for row in rows]


def _churn(fills: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    matches = {str(r["match_id"]) for r in fills}
    tiny = sum(1 for r in fills if float(r["stake"]) < SMALL_TRADE)
    return {
        "trades": len(fills),
        "trade_matches": len(matches),
        "trades_per_match": (len(fills) / len(matches)) if matches else math.nan,
        "tiny_trades": tiny,
        "tiny_share": (tiny / len(fills)) if fills else math.nan,
    }


def build_summary(result: ReplayResult, variants: Sequence[pt.Variant], feed: Any, *, settings: Mapping[str, Any]) -> dict[str, Any]:
    """Every number of the report, as plain data. Deterministic: no wall-clock times."""
    rows = _bet_dicts(result.session.rows)
    day = review.review_day_for(result.end)
    intents_by_variant: dict[str, list[dict[str, Any]]] = {}
    for intent in result.intents:
        intents_by_variant.setdefault(intent["variant"], []).append(intent)

    variant_out: dict[str, Any] = {}
    for variant in variants:
        mine = [r for r in rows if r["variant"] == variant.name]
        s = review.summarise_variant(variant.name, mine, bankroll=variant.bankroll, review_day=day)
        fills = [r for r in mine if r["status"] in TRADE_STATUSES]
        s["intents"] = len(intents_by_variant.get(variant.name, []))
        s["intents_by_side"] = _count(intents_by_variant.get(variant.name, []), lambda i: f"{i['side']}_{i['selection']}")
        s["churn"] = _churn(fills)
        s["max_worst_case_per_match"] = s["max_worst_exposure"]
        variant_out[variant.name] = s

    per_match: list[dict[str, Any]] = []
    match_ids = sorted({i["match_id"] for i in result.intents} | {str(r["match_id"]) for r in rows})
    for mid in match_ids:
        info = feed.match_info(mid)
        cells: dict[str, Any] = {}
        for variant in variants:
            mine = [r for r in rows if r["variant"] == variant.name and str(r["match_id"]) == mid]
            fills = [r for r in mine if r["status"] in TRADE_STATUSES]
            if not mine:
                continue
            cells[variant.name] = {
                "intents": sum(1 for i in intents_by_variant.get(variant.name, []) if i["match_id"] == mid),
                "fills": len(fills),
                "settled": sum(1 for r in fills if r["status"] == "settled"),
                "stake": round(sum(float(r["stake"]) for r in fills), 2),
                "pnl": round(sum(float(r["pnl"]) for r in fills if r.get("pnl") is not None), 2),
                "worst_case": round(review.worst_exposure(fills).get(mid, 0.0), 2),
            }
        per_match.append({
            "match_id": mid,
            "team_home": info.get("team_home"),
            "team_away": info.get("team_away"),
            "competition": info.get("competition"),
            "is_simulated": bool(info.get("is_simulated")),
            "variants": cells,
        })

    return {
        "window": {
            "from": result.start.isoformat(),
            "to": result.end.isoformat(),
            "step_seconds": int(STEP.total_seconds()),
            "cycles_run": result.cycles,
            "grid_steps_skipped": result.skipped_steps,
            "matches_considered": len(result.matches_considered),
        },
        "settings": dict(settings),
        "skipped_registry": dict(sorted(result.skipped_registry.items())),
        "skipped_matches": dict(sorted(result.skipped_matches.items())),
        "guard_refusals": dict(sorted(result.guard_refusals.items())),
        "ledger_rows": len(rows),
        "variants": variant_out,
        "per_match": per_match,
    }


def _count(items: Sequence[Mapping[str, Any]], key: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        k = key(item)
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))


# ----------------------------------------------------------------------------- outputs
def _n(value: Any, digits: int = 4, signed: bool = False) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return format(float(value), f"{'+' if signed else ''}.{digits}f")


def _ci(mean: Any, lo: Any, hi: Any, digits: int = 4) -> str:
    return f"{_n(mean, digits, True)} [{_n(lo, digits, True)}, {_n(hi, digits, True)}]"


def _pct(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{float(value) * 100:.1f}%"


def render_markdown(summary: Mapping[str, Any]) -> str:
    w = summary["window"]
    out = [
        "# PHOENIX paper replay",
        "",
        f"Window {w['from']} to {w['to']}; {w['matches_considered']} matches considered; {w['cycles_run']} cycles run, "
        f"{w['grid_steps_skipped']} grid steps skipped (no fresh price, no open bet). Settings: {summary['settings']}.",
        "",
        "Virtual only. The live ledger (`paper_bets`) was not read or written. CLV is in probability points against the "
        "reference price; P&L is gross of commission. CIs are seeded percentile bootstraps (95%). Churn: a trade is a "
        "filled bet; tiny trades have stake under 2,000.",
        "",
        "## Per variant",
        "",
        "| variant | intents | fills | settled | void | CLV mean [95% CI] | P&L total | P&L per bet [95% CI] | "
        "max worst case / match | trades per match | tiny trades |",
        "|---|---:|---:|---:|---:|---|---:|---|---:|---:|---:|",
    ]
    for name, v in summary["variants"].items():
        churn = v["churn"]
        out.append(
            f"| {name} | {v['intents']} | {v['filled']} | {v['settled_bets']} | {v['void_bets']} | "
            f"{_ci(v['clv_mean'], v['clv_ci_lo'], v['clv_ci_hi'])} (n={v['clv_n']}) | {v['pnl']:,.0f} | "
            f"{_ci(v['pnl_mean'], v['pnl_ci_lo'], v['pnl_ci_hi'], 2)} | {v['max_worst_case_per_match']:,.0f} | "
            f"{_n(churn['trades_per_match'], 2)} | {churn['tiny_trades']} ({_pct(churn['tiny_share'])}) |"
        )
    out += ["", "## Intents by side", ""]
    for name, v in summary["variants"].items():
        out.append(f"- {name}: {', '.join(f'{k} {c}' for k, c in v['intents_by_side'].items()) or 'none'}")
    out += ["", "## Skips (counted per cycle, summed over the run)", ""]
    out.append(f"- Registry: {summary['skipped_registry'] or 'none'}")
    out.append(f"- Matches: {summary['skipped_matches'] or 'none'}")
    out += ["", "## Guard refusals (S75, counted per cycle and per candidate or intent, summed over the run)", ""]
    out.append(f"- {summary.get('guard_refusals') or 'none'}")
    out += ["", "## Per match", ""]
    names = list(summary["variants"])
    out.append("| match | teams | competition | " + " | ".join(f"{n} fills / worst" for n in names) + " |")
    out.append("|---|---|---|" + "|".join("---:" for _ in names) + "|")
    for m in summary["per_match"]:
        cells = []
        for n in names:
            c = m["variants"].get(n)
            cells.append("-" if c is None else f"{c['fills']} / {c['worst_case']:,.0f}")
        teams = f"{m['team_home']} v {m['team_away']}" + (" (SRL)" if m["is_simulated"] else "")
        out.append(f"| {m['match_id']} | {teams} | {m['competition'] or ''} | " + " | ".join(cells) + " |")
    out.append("")
    return "\n".join(out) + "\n"


def write_outputs(summary: Mapping[str, Any], session: MemorySession, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    json_path = out_dir / "summary.json"
    json_path.write_text(json.dumps(review._clean(dict(summary)), indent=2, sort_keys=True), encoding="utf-8")
    paths.append(json_path)
    md_path = out_dir / "summary.md"
    md_path.write_text(render_markdown(summary), encoding="utf-8")
    paths.append(md_path)
    csv_path = out_dir / "bets.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=BET_COLUMNS)
        writer.writeheader()
        for row in _bet_dicts(session.rows):
            writer.writerow({
                k: (json.dumps(review._clean(v), sort_keys=True, default=str) if isinstance(v, (dict, list)) else
                    ("" if v is None else v.isoformat() if isinstance(v, datetime) else v))
                for k, v in row.items()
            })
    paths.append(csv_path)
    return paths


# ----------------------------------------------------------------------------- driver
async def _load(start: Optional[datetime], end: Optional[datetime]) -> tuple[ReplayFeed, datetime, datetime]:
    from shared.db import close_database, get_session

    try:
        async with get_session() as session:
            await session.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
            await session.execute(text("SET TRANSACTION READ ONLY"))
            last = (await session.execute(text("SELECT max(time) FROM v_match_odds"))).scalar()
            first = (await session.execute(text("SELECT min(time) FROM v_match_odds"))).scalar()
            if last is None:
                raise SystemExit("no recorded prices in v_match_odds")
            stop = end or last
            feed = await ReplayFeed.load(session, end=stop)
    finally:
        await close_database()
    begin = start or first
    return feed, begin, stop


def _parse_day(text_value: str) -> datetime:
    dt = datetime.fromisoformat(text_value)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _select_variants(names: Optional[str]) -> tuple[pt.Variant, ...]:
    variants = pt.build_variants()
    if not names:
        return variants
    wanted = [n.strip() for n in names.split(",") if n.strip()]
    known = {v.name for v in variants}
    unknown = [n for n in wanted if n not in known]
    if unknown:
        raise SystemExit(f"unknown variant(s) {unknown}; known: {sorted(known)}")
    return tuple(v for v in variants if v.name in wanted)


async def _run(args: argparse.Namespace, probability: Any, variants: Sequence[pt.Variant]) -> int:
    started = time.perf_counter()
    start_arg = _parse_day(args.from_) if args.from_ else None
    end_arg = _parse_day(args.to) if args.to else None
    feed, start, end = await _load(start_arg, end_arg)
    loaded = time.perf_counter()
    if end < start:
        raise SystemExit("--to is before --from")
    guards_cfg = pt.GuardConfig()
    result = await replay_window(
        feed, probability, variants, start=start, end=end, include_simulated=args.include_srl, guards=guards_cfg
    )
    replayed = time.perf_counter()
    settings = {
        "include_srl": bool(args.include_srl),
        "variants": [v.name for v in variants],
        "cycle_seconds": int(STEP.total_seconds()),
        "fill_delay_seconds": int(ledger.FILL_DELAY.total_seconds()),
        "max_match_loss": pt.MAX_MATCH_LOSS,
        "guards": asdict(guards_cfg),
        "suspensions": "none (every variant trades; the live S73 state is not applied)",
    }
    summary = build_summary(result, variants, feed, settings=settings)
    out_dir = Path(args.out) if args.out else REPLAY_DIR / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    write_outputs(summary, result.session, out_dir)
    print(f"replay {start.isoformat()} .. {end.isoformat()}: {result.cycles} cycles, "
          f"{result.skipped_steps} grid steps skipped, {len(result.intents)} intents, "
          f"{len(result.session.rows)} ledger rows")
    print(f"load {loaded - started:.1f}s, replay {replayed - loaded:.1f}s, total {time.perf_counter() - started:.1f}s")
    print(f"wrote {out_dir}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="S74 replay of the paper trader over recorded history (read-only).")
    parser.add_argument("--from", dest="from_", help="first decision time, e.g. 2026-11-01 (UTC); default: first tick")
    parser.add_argument("--to", help="last decision time (UTC); default: last recorded tick")
    parser.add_argument("--variants", help="comma-separated variant names; default: all")
    parser.add_argument("--out", help="output directory; default: reports/replay/<timestamp>")
    parser.add_argument("--include-srl", action="store_true", help="also replay simulated (SRL) matches")
    args = parser.parse_args(argv)
    variants = _select_variants(args.variants)
    probability = pt.load_probability_service(pt.ROOT)  # blocking; before the event loop starts
    return asyncio.run(_run(args, probability, variants))


if __name__ == "__main__":
    raise SystemExit(main())
