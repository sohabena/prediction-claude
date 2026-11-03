"""S88 value-first scorecard: the measures the daily report and the dashboard lead with (Tier A), and the diagnostics
that follow them (Tier B). Both show the same numbers because both call the functions here.

Pure functions, except the read-only loaders (``load_match_facts``, ``load_live``) and ``read_refusals``, which reads the
trader log. Nothing here writes to the database or places a bet.

Definitions
- Real match: not simulated, by the registry flag or the scraper's name rule (``results_reconciliation.is_real``).
- Settled: a decided result (win with a winner, tie, no_result or abandoned), the rule the S76 list uses, so settled
  plus missing equals the real matches with a recent tick.
- Tradable book: a row of ``v_match_odds`` with both sides back and lay (all four prices), before the off or in play.
- R1 match: a real match with a pre-match price (a row before the off with both back prices) and a decided win with a
  winner. A log loss on home or away needs an outcome, so a tie, no result or abandonment does not count.
- R2 match (held out): a settled tradable real match that started after the newest production model's training
  cut-off (``final_model_trained_through`` in ``models/prematch/*_v3_walkforward.json``).
- R3 progress: per strategy variant, settled paper matches against 30 and settled paper bets against 300. Counted in
  matches, not bets (the review's ``settled_matches`` and ``settled_bets``).
- Live size cap: the trader's ``run_cycle(size_cap=...)`` default. Until the size unit is verified it is off.
"""

from __future__ import annotations

import inspect
import json
import math
from collections import Counter, defaultdict
from dataclasses import fields
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from scripts.results_reconciliation import has_decided_result, is_real
from strategy import model_edge
from strategy.base import bet_gross_pnl
from training.market import TRADING_DEVIG, market_prob_home
from virtual_trading import paper_review as review
from virtual_trading import paper_trader as pt

ROOT = Path(__file__).resolve().parent.parent
R1_MATCHES = 200  # profit-architecture R1: matches with a pre-match price and a decided result, per segment
R2_MATCHES = 150  # profit-architecture R2: held-out matches on real prices
R3_MATCHES = review.R3_MATCHES
R3_SETTLED_BETS = review.R3_SETTLED_BETS
RECENT_DAYS = 7
GUARD_WINDOW = timedelta(hours=24)
RECENT_LIMIT = 5  # trades listed per live match
MODEL_CUTOFF_FILES = ("models/prematch/t20_v3_walkforward.json", "models/prematch/odi_v3_walkforward.json")
TICKET_BUDGET = next(f.default for f in fields(model_edge.Variant) if f.name == "ticket_budget")  # 30
MAX_MATCH_LOSS = pt.MAX_MATCH_LOSS  # 100,000 per match and variant


# ----------------------------------------------------------------------------- helpers
def _aware(value: Any) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def live_size_cap() -> bool:
    """The live trader's size cap (S87): the default of ``run_cycle``. Off until the size unit is verified."""
    return bool(inspect.signature(pt.run_cycle).parameters["size_cap"].default)


def held_out_cutoff(root: Path = ROOT) -> Optional[date]:
    """The latest ``final_model_trained_through`` of the production pre-match models, or None when unreadable."""
    dates: list[date] = []
    for rel in MODEL_CUTOFF_FILES:
        try:
            data = json.loads((root / rel).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        raw = data.get("final_model_trained_through") if isinstance(data, dict) else None
        try:
            dates.append(date.fromisoformat(str(raw)[:10]))
        except ValueError:
            continue
    return max(dates) if dates else None


def _is_win(fact: Mapping[str, Any]) -> bool:
    return str(fact.get("result_type") or "").strip().lower() == "win" and bool(str(fact.get("winner") or "").strip())


def _start_date(fact: Mapping[str, Any]) -> Optional[date]:
    when = _aware(fact.get("start_at")) or _aware(fact.get("last_price_at"))
    return when.astimezone(timezone.utc).date() if when else None


def _settled_when(fact: Mapping[str, Any]) -> Optional[datetime]:
    return _aware(fact.get("completed_at")) or _aware(fact.get("last_price_at"))


# ----------------------------------------------------------------------------- Tier A: matches
def match_counts(facts: Sequence[Mapping[str, Any]], now: datetime, cutoff: Optional[date]) -> dict[str, Any]:
    """Tier A counts over one row per match (``load_match_facts``). Matches, never bets."""
    real = [f for f in facts if is_real(f)]
    settled = [f for f in real if has_decided_result(f)]
    tradable = [f for f in settled if bool(f.get("tradable"))]
    since = now - timedelta(days=RECENT_DAYS)
    recent = [f for f in tradable if (_settled_when(f) is not None and _settled_when(f) >= since)]
    r1 = {str(f["match_id"]) for f in real if bool(f.get("prematch_price")) and _is_win(f)}
    held_out = None
    if cutoff is not None:
        held_out = {str(f["match_id"]) for f in tradable if (_start_date(f) or date.min) > cutoff}
    return {
        "real_matches": len({str(f["match_id"]) for f in real}),
        "settled_real": len({str(f["match_id"]) for f in settled}),
        "tradable_settled": len({str(f["match_id"]) for f in tradable}),
        "tradable_settled_recent": len({str(f["match_id"]) for f in recent}),
        "recent_days": RECENT_DAYS,
        "r1_matches": len(r1),
        "r1_target": R1_MATCHES,
        "r2_matches": None if held_out is None else len(held_out),
        "r2_target": R2_MATCHES,
        "r2_cutoff": cutoff.isoformat() if cutoff else None,
    }


# ----------------------------------------------------------------------------- Tier B
def read_refusals_from_lines(lines: Iterable[str], now: datetime, window: timedelta = GUARD_WINDOW) -> tuple[int, Counter]:
    """Cycles and guard refusals by reason in the trader log lines written within ``window`` before ``now``. Only
    cycle records (those with ``guard_refusals``) count; error records are skipped."""
    cycles = 0
    counts: Counter = Counter()
    since = now - window
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or "guard_refusals" not in rec:
            continue
        when = _aware(_parse_time(rec.get("cycle_at")))
        if when is None or when < since:
            continue
        cycles += 1
        for reason, n in (rec.get("guard_refusals") or {}).items():
            counts[str(reason)] += int(n)
    return cycles, counts


def _parse_time(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def read_refusals(now: datetime, path: Path = pt.LOG_PATH) -> Optional[tuple[int, Counter]]:
    """``read_refusals_from_lines`` on the trader log, or None when there is no log yet."""
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    return read_refusals_from_lines(lines, now)


def tier_b(
    rows: Sequence[Mapping[str, Any]],
    state: Mapping[str, Any],
    *,
    now: datetime,
    refusals: Optional[tuple[int, Counter]],
    cap: float = MAX_MATCH_LOSS,
    budget: int = TICKET_BUDGET,
) -> dict[str, Any]:
    """Worst case per match against the cap, trades per match against the ticket budget, guard refusals (24 h), and the
    share of matches with a model view. ``rows`` are every paper bet; ``state`` is the review's result for them."""
    by_variant: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_variant[str(row["variant"])].append(row)
    variants = []
    for v in state.get("variants", []):
        name = str(v["variant"])
        if v.get("placebo"):
            continue
        vr = by_variant.get(name, [])
        fills = [r for r in vr if r.get("status") in review.FILLED]
        exposure = review.worst_exposure(fills)
        trades = Counter(str(r["match_id"]) for r in vr)
        worst = max(exposure.values(), default=0.0)
        variants.append({
            "variant": name,
            "worst_max": worst,
            "worst_share": worst / cap if cap else math.nan,
            "matches_over_cap": sum(1 for x in exposure.values() if x > cap + 1e-6),
            "trades_max": max(trades.values(), default=0),
            "matches_over_budget": sum(1 for c in trades.values() if c > budget),
            "matches": len(trades),
        })
    matches = {str(r["match_id"]) for r in rows}
    rated = {str(r["match_id"]) for r in rows if r.get("p_model") is not None}
    return {
        "cap": cap,
        "budget": budget,
        "variants": variants,
        "refusals": refusals,  # (cycles, Counter) over the last 24 h, or None when there is no log
        "guard_window_hours": GUARD_WINDOW.total_seconds() / 3600.0,
        "rated_matches": len(rated),
        "ledger_matches": len(matches),
        "rated_share": (len(rated) / len(matches)) if matches else math.nan,
    }


# ----------------------------------------------------------------------------- the in-play gate (S89)
def gate_rows(gate: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One row per format of the in-play gate file: open or closed, decided matches, log loss difference."""
    out = []
    for fmt in sorted(gate):
        entry = gate[fmt]
        out.append({
            "format": str(fmt),
            "open": entry.get("inplay_model_allowed") is True,
            "matches": int(entry.get("matches") or 0),
            "logloss_diff": entry.get("logloss_diff"),
        })
    return out


# ----------------------------------------------------------------------------- live positions (dashboard)
def position_rows(bets: Sequence[Mapping[str, Any]], *, cap: float = MAX_MATCH_LOSS) -> list[dict[str, Any]]:
    """One match's positions per variant: P&L if the home side wins and if the away side wins, the worst case, and its
    share of the cap. ``bets`` are that match's paper bets (any status)."""
    by_variant: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in bets:
        by_variant[str(row["variant"])].append(row)
    out = []
    for name in sorted(by_variant):
        vr = by_variant[name]
        fills = [r for r in vr if r.get("status") in review.FILLED and r.get("price_filled") is not None]
        home = sum(_gross(r, "home") for r in fills)
        away = sum(_gross(r, "away") for r in fills)
        worst = max(0.0, -min(home, away)) if fills else 0.0
        out.append({
            "variant": name,
            "placebo": review.is_placebo(name),
            "fills": len(fills),
            "pending": sum(1 for r in vr if r.get("status") == "pending"),
            "home": home,
            "away": away,
            "worst": worst,
            "worst_share": worst / cap if cap else math.nan,
        })
    return out


def _gross(row: Mapping[str, Any], outcome: str) -> float:
    return bet_gross_pnl(row["side"], row["selection"], float(row["price_filled"]), float(row["stake"]), outcome)


def live_matches_from(
    registry: Sequence[Mapping[str, Any]],
    last_prices: Mapping[str, datetime],
    price_rows: Sequence[Mapping[str, Any]],
    ledger_rows: Sequence[Mapping[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    """The matches being traded now: a fresh price, a real match and not settled. Pure: the caller reads the rows."""
    cache = pt.PriceCache(price_rows)
    by_match: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in ledger_rows:
        by_match[str(row["match_id"])].append(row)
    out = []
    for reg in registry:
        mid = str(reg["match_id"])
        if mid not in last_prices or bool(reg.get("settled")) or not is_real(reg):
            continue
        start = _aware(reg.get("actual_start_time")) or _aware(reg.get("match_start_time"))
        found = cache.book_at(mid, now)
        book = found or {}
        p_market = market_prob_home(book, TRADING_DEVIG) if found else None
        bets = sorted(by_match.get(mid, []), key=lambda r: _aware(r["decided_at"]) or now, reverse=True)
        p_model = next((r["p_model"] for r in bets if r.get("p_model") is not None), None)
        out.append({
            "match_id": mid,
            "team_home": str(reg.get("team_home") or ""),
            "team_away": str(reg.get("team_away") or ""),
            "competition": str(reg.get("competition") or ""),
            "start": start,
            "in_play": bool(start and start <= now),
            "book": book,
            "p_market": p_market,
            "p_model": p_model,
            "positions": position_rows(bets),
            "last_trades": bets[:RECENT_LIMIT],
        })
    out.sort(key=lambda m: (m["start"] or now))
    return out


# ----------------------------------------------------------------------------- database reads (read-only)
MATCH_FACTS_SQL = """
    SELECT r.match_id, r.team_home, r.team_away, r.competition, r.is_simulated,
           COALESCE(r.actual_start_time, r.match_start_time) AS start_at,
           res.result_type, res.winner, res.completed_at,
           COALESCE(bool_or(v.back_home IS NOT NULL AND v.lay_home IS NOT NULL
                            AND v.back_away IS NOT NULL AND v.lay_away IS NOT NULL), false) AS tradable,
           COALESCE(bool_or(v.back_home IS NOT NULL AND v.back_away IS NOT NULL
                            AND v.time < COALESCE(r.actual_start_time, r.match_start_time)), false) AS prematch_price,
           max(v.time) AS last_price_at
    FROM match_training_status r
    LEFT JOIN match_results res ON res.match_id = r.match_id
    LEFT JOIN v_match_odds v ON v.match_id = r.match_id
    WHERE r.is_simulated IS NOT TRUE
    GROUP BY r.match_id, r.team_home, r.team_away, r.competition, r.is_simulated, r.actual_start_time,
             r.match_start_time, res.result_type, res.winner, res.completed_at
"""


async def load_match_facts(session: Any) -> list[dict[str, Any]]:
    """One row per registry match (simulated excluded in SQL; ``is_real`` applies the name rule in Python). Read-only."""
    from sqlalchemy import text

    result = await session.execute(text(MATCH_FACTS_SQL))
    return [dict(r) for r in result.mappings().all()]


async def load_live(session: Any, now: datetime) -> list[dict[str, Any]]:
    """The live matches for the dashboard, from the trader's own reads (DbFeed) and the ledger. Read-only."""
    from sqlalchemy import select

    from backend.models.paper_bets import PaperBet

    feed = pt.DbFeed(session)
    last = await feed.last_prices(now)
    ids = sorted(last)
    if not ids:
        return []
    registry = await feed.registry(ids)
    price_rows = await feed.price_rows(ids, now - pt.PENDING_LOOKBACK)
    stmt = select(PaperBet).where(PaperBet.match_id.in_(ids))
    ledger = [
        {name: getattr(row, name) for name in (c.name for c in PaperBet.__table__.columns)}
        for row in (await session.execute(stmt)).scalars().all()
    ]
    return live_matches_from(registry, last, price_rows, ledger, now)


__all__ = [
    "MATCH_FACTS_SQL",
    "R1_MATCHES",
    "R2_MATCHES",
    "R3_MATCHES",
    "R3_SETTLED_BETS",
    "TICKET_BUDGET",
    "gate_rows",
    "held_out_cutoff",
    "live_matches_from",
    "live_size_cap",
    "load_live",
    "load_match_facts",
    "match_counts",
    "position_rows",
    "read_refusals",
    "read_refusals_from_lines",
    "tier_b",
]
