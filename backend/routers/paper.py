"""S91: read-only API over the paper trader (prefix ``/api/paper``).

- ``GET /api/paper/live``: the real matches the trader considered in its last cycle (the last cycle record of
  ``logs/paper_trader.log``), with their books, p_market, p_model (last decision), the latest score, and per variant the
  P&L if the home or away side wins, the worst case against the 100,000 budget (hard limit 120,000), tickets used, and
  the last five trades. Placebos are flagged.
- ``GET /api/paper/evidence``: the S88 headline, computed by ``scripts.paper_report.gather`` and the functions it calls,
  so it shows the same numbers as the daily report. Includes ``r3_passed`` for the UI banner.

Nothing here writes. Every database read runs in a READ ONLY transaction (``readonly.read_only_session``).
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from fastapi import APIRouter
from sqlalchemy import select, text

from backend.models.paper_bets import PaperBet
from backend.routers.readonly import (
    age_seconds,
    jsonable,
    last_json_line,
    logs_dir,
    read_only_session,
    reports_dir,
    utc,
)
from scripts import paper_report as rep
from shared.logging import setup_logging
from virtual_trading import paper_review as review
from virtual_trading import paper_trader as pt
from virtual_trading import scorecard as sc

router = APIRouter()
logger = setup_logging("router_paper")

FRESH_CYCLE = timedelta(minutes=5)  # the last cycle older than this: the trader is not running
CONTEXT_WINDOW = timedelta(hours=12)  # how far back the latest score is looked for
TRADES_PER_VARIANT = sc.RECENT_LIMIT  # last five trades, per variant and per match

EMPTY_INPUTS: dict[str, Any] = {"registry": [], "last_prices": {}, "price_rows": [], "ledger": [], "context": []}

CONTEXT_SQL = text(
    "SELECT DISTINCT ON (match_id) match_id, time, is_live, score, wickets, overs, innings, batting_team, "
    "bowling_team, status, match_format FROM match_context "
    "WHERE match_id = ANY(:ids) AND time >= :since AND time <= :now ORDER BY match_id, time DESC"
)


# ----------------------------------------------------------------------------- files
def trader_log_path() -> Path:
    return logs_dir() / "paper_trader.log"


def last_cycle(path: Optional[Path] = None) -> Optional[dict[str, Any]]:
    """The last cycle record of the trader log (a record with ``cycle_at``), or None."""
    return last_json_line(path or trader_log_path(), accept=lambda rec: "cycle_at" in rec)


def logged_match_ids(cycle: Optional[Mapping[str, Any]]) -> list[str]:
    if not cycle:
        return []
    return [str(m["match_id"]) for m in cycle.get("matches") or [] if m.get("match_id")]


def latest_daily_report(folder: Optional[Path] = None) -> Optional[dict[str, Any]]:
    """Metadata of the newest daily paper report (``reports/paper/YYYYMMDD.md``). The file itself is not read."""
    folder = folder or (reports_dir() / "paper")
    try:
        names = [p for p in folder.iterdir() if re.fullmatch(r"\d{8}\.md", p.name)]
    except OSError:
        return None
    if not names:
        return None
    newest = max(names, key=lambda p: p.name)
    stat = newest.stat()
    stamp = newest.name[:8]
    return {
        "file": newest.name,
        "date": f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}",
        "bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def ticket_budgets() -> dict[str, int]:
    """Each variant's ticket budget, from the one place the variants are built."""
    return {v.name: int(v.ticket_budget) for v in pt.build_variants()}


# ----------------------------------------------------------------------------- live: database reads
async def latest_context(session: Any, ids: Sequence[str], now: datetime) -> list[dict[str, Any]]:
    rows = await session.execute(CONTEXT_SQL, {"ids": list(ids), "since": now - CONTEXT_WINDOW, "now": now})
    return [dict(r) for r in rows.mappings().all()]


async def load_live_inputs(session: Any, ids: Sequence[str], now: datetime) -> dict[str, Any]:
    """The rows behind the live view, for the logged matches that have a fresh price now. Read-only."""
    feed = pt.DbFeed(session)
    fresh = await feed.last_prices(now)
    live = sorted(i for i in ids if i in fresh)
    if not live:
        return dict(EMPTY_INPUTS)
    registry = await feed.registry(live)
    price_rows = await feed.price_rows(live, now - pt.PENDING_LOOKBACK)
    stmt = select(PaperBet).where(PaperBet.match_id.in_(live))
    ledger = [
        {name: getattr(row, name) for name in (c.name for c in PaperBet.__table__.columns)}
        for row in (await session.execute(stmt)).scalars().all()
    ]
    return {
        "registry": registry,
        "last_prices": {i: fresh[i] for i in live},
        "price_rows": price_rows,
        "ledger": ledger,
        "context": await latest_context(session, live, now),
    }


# ----------------------------------------------------------------------------- live: shaping (pure)
def trade_view(row: Mapping[str, Any]) -> dict[str, Any]:
    variant = str(row["variant"])
    return {
        "decided_at": row.get("decided_at"),
        "variant": variant,
        "placebo": review.is_placebo(variant),
        "side": row.get("side"),
        "selection": row.get("selection"),
        "team": row.get("team"),
        "in_play": bool(row.get("in_play")),
        "price_limit": row.get("price_limit"),
        "price_filled": row.get("price_filled"),
        "stake": row.get("stake"),
        "status": row.get("status"),
        "reason": row.get("reason"),  # enter | add | reduce | flip | green_up
        "p_used": row.get("p_used"),
        "pnl": row.get("pnl"),
        "result": row.get("result"),
    }


def score_view(row: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    return {
        "at": row.get("time"),
        "is_live": row.get("is_live"),
        "score": row.get("score"),
        "wickets": row.get("wickets"),
        "overs": row.get("overs"),
        "innings": row.get("innings"),
        "batting_team": row.get("batting_team"),
        "bowling_team": row.get("bowling_team"),
        "status": row.get("status"),
        "match_format": row.get("match_format"),
    }


def cycle_view(cycle: Optional[Mapping[str, Any]], now: datetime) -> Optional[dict[str, Any]]:
    if cycle is None:
        return None
    age = age_seconds(cycle.get("cycle_at"), now)
    return {
        "cycle_at": cycle.get("cycle_at"),
        "age_seconds": age,
        "stale": age is None or age > FRESH_CYCLE.total_seconds(),
        "reference": cycle.get("reference"),
        "candidates": cycle.get("candidates"),
        "considered": cycle.get("considered"),
        "intents": cycle.get("intents"),
        "guard_refusals": cycle.get("guard_refusals") or {},
        "primary": cycle.get("primary"),
        "suspended": cycle.get("suspended") or [],
    }


def build_live(
    *,
    now: datetime,
    cycle: Optional[Mapping[str, Any]],
    inputs: Mapping[str, Any],
    budgets: Mapping[str, int],
) -> dict[str, Any]:
    """The /api/paper/live payload. Pure: ``inputs`` are the rows of ``load_live_inputs``. The position arithmetic is
    ``scorecard.live_matches_from`` and ``scorecard.position_rows``, the dashboard's own functions."""
    registry = {str(r["match_id"]): r for r in inputs["registry"]}
    context = {str(r["match_id"]): r for r in inputs["context"]}
    by_match_variant: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in inputs["ledger"]:
        by_match_variant[(str(row["match_id"]), str(row["variant"]))].append(row)

    matches = sc.live_matches_from(
        inputs["registry"], inputs["last_prices"], inputs["price_rows"], inputs["ledger"], now,
    )
    out = []
    for m in matches:
        mid = m["match_id"]
        reg = registry.get(mid, {})
        variants = []
        for p in m["positions"]:
            name = p["variant"]
            rows = by_match_variant[(mid, name)]
            recent = sorted(rows, key=lambda r: utc(r.get("decided_at")) or now, reverse=True)[:TRADES_PER_VARIANT]
            variants.append({
                "variant": name,
                "placebo": p["placebo"],
                "pnl_if_home": p["home"],
                "pnl_if_away": p["away"],
                "worst_case": p["worst"],
                "budget": pt.MAX_MATCH_LOSS,
                "hard_limit": pt.HARD_MATCH_LOSS,
                "worst_share_of_budget": p["worst_share"],
                "fills": p["fills"],
                "pending": p["pending"],
                "tickets_used": len(rows),
                "ticket_budget": int(budgets.get(name, sc.TICKET_BUDGET)),
                "last_trades": [trade_view(r) for r in recent],
            })
        out.append({
            "match_id": mid,
            "team_home": m["team_home"],
            "team_away": m["team_away"],
            "competition": m["competition"],
            "in_play": m["in_play"],
            "start": m["start"],
            "match_start_time": reg.get("match_start_time"),
            "actual_start_time": reg.get("actual_start_time"),
            "latest_score": score_view(context.get(mid)),
            "book": m["book"],
            "p_market": m["p_market"],
            "p_model": m["p_model"],
            "variants": variants,
            "last_trades": [trade_view(r) for r in m["last_trades"]],
        })
    return {
        "generated_at": now,
        "virtual_only": True,
        "budget": pt.MAX_MATCH_LOSS,
        "hard_limit": pt.HARD_MATCH_LOSS,
        "cycle": cycle_view(cycle, now),
        "matches_logged": len(logged_match_ids(cycle)),
        "matches": out,
    }


@router.get("/live")
async def get_live() -> dict[str, Any]:
    """The matches the trader considered in its last cycle, with positions per variant."""
    now = datetime.now(timezone.utc)
    cycle = last_cycle()
    ids = logged_match_ids(cycle)
    inputs = dict(EMPTY_INPUTS)
    if ids:
        try:
            async with read_only_session() as session:
                inputs = await load_live_inputs(session, ids, now)
        except Exception as exc:
            logger.warning("Database unavailable for live paper inputs: %s", exc)
    return jsonable(build_live(now=now, cycle=cycle, inputs=inputs, budgets=ticket_budgets()))


# ----------------------------------------------------------------------------- evidence (pure shaping)
def _above_zero(value: Any) -> bool:
    return isinstance(value, (int, float)) and value > 0


def variant_view(v: Mapping[str, Any]) -> dict[str, Any]:
    """One variant's Tier-A figures, as the daily report shows them (matches and bets counted, never bets alone)."""
    return {
        "variant": v["variant"],
        "placebo": bool(v.get("placebo")),
        "bets": v["bets"],
        "filled": v["filled"],
        "unfilled": v["unfilled"],
        "pending": v["pending"],
        "fill_rate": v["fill_rate"],
        "matches": v["matches"],
        "settled_bets": v["settled_bets"],
        "settled_matches": v["settled_matches"],
        "void_bets": v["void_bets"],
        "clv": {
            "mean": v["clv_mean"], "ci_lo": v["clv_ci_lo"], "ci_hi": v["clv_ci_hi"],
            "matches": v["clv_matches"], "bets": v["clv_bets"], "too_few_matches": v["clv_too_few_matches"],
        },
        "clv_stake_weighted": {"mean": v["clv_w_mean"], "ci_lo": v["clv_w_ci_lo"], "ci_hi": v["clv_w_ci_hi"]},
        "pnl": {
            "total": v["pnl"], "ci_lo": v["pnl_ci_lo"], "ci_hi": v["pnl_ci_hi"],
            "matches": v["pnl_matches"], "bets": v["pnl_bets"],
        },
        "segments": {
            label: {
                "matches": s.get("matches"), "bets": s.get("bets"),
                "clv_mean": s.get("clv_mean"), "clv_ci_lo": s.get("clv_ci_lo"), "clv_ci_hi": s.get("clv_ci_hi"),
                "clv_matches": s.get("clv_matches"), "pnl": s.get("pnl"),
            }
            for label, s in v["segments"].items()
        },
        "r3": {
            "met": bool(v["r3_met"]),
            "settled_bets": v["settled_bets"], "settled_bets_target": review.R3_SETTLED_BETS,
            "settled_matches": v["settled_matches"], "settled_matches_target": review.R3_MATCHES,
            "clv_ci_lo_above_zero": _above_zero(v["clv_ci_lo"]),
        },
        "day_pnl": v["day_pnl"],
        "max_worst_exposure": v["max_worst_exposure"],
        "brier": v["brier"],
        "brier_n": v["brier_n"],
    }


def _excess_block(excess: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    return {
        name: {
            "matches": e["matches"], "bets": e["bets"], "control_bets": e["control_bets"],
            "estimate": e["estimate"], "ci_lo": e["ci_lo"], "ci_hi": e["ci_hi"],
            "too_few_matches": e["too_few_matches"], "above_zero": e["above_zero"],
        }
        for name, e in excess.items()
    }


def build_evidence(data: Mapping[str, Any], now: datetime, daily: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """The /api/paper/evidence payload from ``paper_report.gather`` output. Pure."""
    state = data["state"]
    rows = data["rows"]
    counts = dict(data.get("counts") or {})
    variants = state["variants"]
    strategies = [v for v in variants if not v.get("placebo")]
    names = [v["variant"] for v in strategies]
    pre = review.excess_over_placebo(
        [r for r in rows if not bool(r.get("in_play"))], names, control=review.PLACEBO_RANDOM,
    )
    live = review.excess_over_placebo(
        [r for r in rows if bool(r.get("in_play"))], names, control=review.PLACEBO_RANDOM,
    )
    return {
        "generated_at": now,
        "virtual_only": True,
        "review_day": state.get("review_day"),
        "first_decided_at": state.get("first_decided_at"),
        "last_decided_at": state.get("last_decided_at"),
        "bets_seen": state.get("bets_seen"),
        "r3_passed": any(bool(v.get("r3_met")) for v in strategies),
        "r3_target": {"settled_bets": review.R3_SETTLED_BETS, "matches": review.R3_MATCHES},
        "tradable": counts,
        "progress": {
            "r1": {"matches": counts.get("r1_matches"), "target": counts.get("r1_target")},
            "r2": {"matches": counts.get("r2_matches"), "target": counts.get("r2_target"),
                   "cutoff": counts.get("r2_cutoff")},
            "r3": {"target_matches": review.R3_MATCHES, "target_settled_bets": review.R3_SETTLED_BETS},
        },
        "variants": [variant_view(v) for v in variants],
        "excess_over_placebo": {
            "control": review.PLACEBO_RANDOM,
            "pre_match": _excess_block(pre),
            "in_play": _excess_block(live),
            "sentence_pre_match": review.excess_sentence(pre),
            "sentence_in_play": review.excess_sentence(live),
            "sentence_all_bets": (state.get("placebo") or {}).get("sentence"),
        },
        "in_play_gate": sc.gate_rows(data.get("gate") or {}),
        "fill_realism": {
            "live_size_cap": sc.live_size_cap(),
            "variants": [
                {
                    "variant": v["variant"],
                    "fill_rate": v["fill_rate"],
                    "stake_fill_rate": (v.get("sizing") or {}).get("stake_fill_rate"),
                    "size_capped_share": (v.get("sizing") or {}).get("size_capped_share"),
                    "size_unknown_share": (v.get("sizing") or {}).get("size_unknown_share"),
                    "mean_adverse_slippage_pct": (v.get("sizing") or {}).get("mean_adverse_slippage_pct"),
                }
                for v in strategies
            ],
        },
        "missing_results": len(data.get("missing") or []),
        "daily_report": dict(daily) if daily else None,
    }


async def load_evidence_inputs(now: datetime) -> dict[str, Any]:
    """``paper_report.gather`` in one READ ONLY transaction: the same inputs as the daily report. Writes nothing."""
    async with read_only_session() as session:
        return await rep.gather(session, now, log_path=trader_log_path())


@router.get("/evidence")
async def get_evidence() -> dict[str, Any]:
    """The S88 headline: tradable settled matches, excess over placebo, R1 to R3 progress, the in-play gate, fills."""
    now = datetime.now(timezone.utc)
    try:
        data = await load_evidence_inputs(now)
    except Exception as exc:
        logger.warning("Database unavailable for evidence inputs: %s", exc)
        data = {}
    return jsonable(build_evidence(data, now, latest_daily_report()))


# ----------------------------------------------------------------------------- S117: Real-Time Trading Signals
SIGNALS_SQL = text("""
    SELECT pb.bet_id, pb.variant, pb.match_id, pb.side, pb.selection, pb.team, 
           COALESCE(pb.price_filled, pb.price_limit) as price,
           pb.price_limit, pb.price_filled, pb.stake, pb.reason, pb.edge, 
           pb.p_model, pb.p_market, pb.status, pb.decided_at, pb.in_play,
           vm.team_home, vm.team_away, vm.competition,
           mc.score, mc.wickets, mc.overs, mc.is_live, mc.batting_team
    FROM paper_bets pb
    LEFT JOIN v_matches vm ON pb.match_id = vm.match_id
    LEFT JOIN (
        SELECT DISTINCT ON (match_id) match_id, score, wickets, overs, is_live, batting_team
        FROM match_context
        ORDER BY match_id, time DESC
    ) mc ON pb.match_id = mc.match_id
    ORDER BY pb.decided_at DESC
    LIMIT :limit
""")


@router.get("/signals")
async def get_signals(limit: int = 150) -> dict[str, Any]:
    """S117: Real-time trading signals stream across all running models with match context, scores, odds, and edge."""
    now = datetime.now(timezone.utc)
    clamped_limit = max(1, min(limit, 300))
    async with read_only_session() as session:
        rows = await session.execute(SIGNALS_SQL, {"limit": clamped_limit})
        records = [dict(r) for r in rows.mappings().all()]
    
    signals = []
    for r in records:
        decided_at = r.get("decided_at")
        age = age_seconds(decided_at, now)
        price = float(r.get("price") or 0.0)
        stake = float(r.get("stake") or 0.0)
        side = str(r.get("side") or "").upper()
        liability = stake * (price - 1.0) if side == "LAY" and price > 1.0 else stake
        score_val = r.get("score")
        wickets_val = r.get("wickets")
        overs_val = r.get("overs")
        score_state = f"{score_val}/{wickets_val} ({overs_val} ov)" if score_val is not None else None

        signals.append({
            "bet_id": str(r.get("bet_id")),
            "variant": r.get("variant"),
            "match_id": r.get("match_id"),
            "match_name": f"{r.get('team_home') or 'Home'} v {r.get('team_away') or 'Away'}",
            "competition": r.get("competition") or "Cricket Match",
            "team_home": r.get("team_home"),
            "team_away": r.get("team_away"),
            "side": side,
            "selection": r.get("selection"),
            "team": r.get("team") or (r.get("team_home") if r.get("selection") == "home" else r.get("team_away")),
            "price": price,
            "price_limit": float(r.get("price_limit") or 0.0) if r.get("price_limit") else None,
            "price_filled": float(r.get("price_filled") or 0.0) if r.get("price_filled") else None,
            "stake": stake,
            "liability": liability,
            "reason": r.get("reason"),
            "edge": float(r.get("edge") or 0.0) if r.get("edge") is not None else None,
            "p_model": float(r.get("p_model") or 0.0) if r.get("p_model") is not None else None,
            "p_market": float(r.get("p_market") or 0.0) if r.get("p_market") is not None else None,
            "status": r.get("status"),
            "in_play": bool(r.get("in_play")),
            "score_state": score_state,
            "batting_team": r.get("batting_team"),
            "is_live": bool(r.get("is_live")),
            "decided_at": decided_at.isoformat() if isinstance(decided_at, datetime) else str(decided_at or ""),
            "age_seconds": age,
            "is_fresh": age is not None and age <= 180,
        })

    return jsonable({
        "generated_at": now.isoformat(),
        "total": len(signals),
        "signals": signals,
    })
