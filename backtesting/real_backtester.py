"""Event-driven backtester over real Betfair price histories (S39).

Input episodes are the dicts returned by ``scripts/build_real_price_corpus.load_real_corpus``
(real tick contract). Each episode's result sits only in ``ticks[0]["_meta"]``. It is read
here for settlement and never passed to a strategy.

Walk-forward folds are by season: the fair model for a test season is fitted only on earlier
seasons, and ``assert_walk_forward`` checks that on the fold metadata.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Iterable, Optional, Sequence

from backtesting.execution import ExecutionModel, Fill, plan_fill, quoted_price, valid_price
from backtesting.report import bootstrap_ci
from shared.constants import LARGE_STAKE_PERCENT, SMALL_STAKE_PERCENT
from shared.metrics import clv_prob
from shared.schemas import BettingAction
from strategy.base import MarketFeed, MatchedBet, OrderIntent, Position, Strategy, bet_gross_pnl

DEFAULT_BANKROLL = 1000.0
META_KEYS = (
    "market_id", "match_id", "family", "match_format", "competition", "team_home", "team_away",
    "data_tier", "start", "competition_tier",
)


class WalkForwardLeakError(AssertionError):
    """A fold or fitted model used data from outside its training seasons."""


# ================================================================
# Helpers
# ================================================================


def _epoch(ts: Any) -> float:
    if isinstance(ts, datetime):
        return ts.timestamp()
    return datetime.fromisoformat(str(ts)).timestamp()


def _strip(tick: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in tick.items() if k != "_meta"}


def _season(episode: dict[str, Any]) -> int:
    start = episode.get("start") or episode["ticks"][0]["time"]
    return int(str(start)[:4])


def match_outcome(ticks: Sequence[dict[str, Any]]) -> Optional[str]:
    """``home``, ``away``, ``draw`` or None (no result). Reads ``_meta`` on the first or last tick."""
    for tick in (ticks[0], ticks[-1]):
        meta = tick.get("_meta") or {}
        if not meta:
            continue
        if meta.get("result_type") == "draw":
            return "draw"
        if meta.get("winner_is_home") is True:
            return "home"
        if meta.get("winner_is_home") is False:
            return "away"
        winner, home = meta.get("winner"), tick.get("team_home")
        if winner and home:
            return "home" if winner == home else "away"
    return None


def _closing_ltp(ticks: Sequence[dict[str, Any]], selection: str) -> Optional[float]:
    for tick in reversed(ticks):
        value = valid_price(tick.get(f"ltp_{selection}"))
        if value is not None:
            return value
    return None


CLV_HORIZON_S = 600.0     # in-play CLV reference horizon (scripts/audit_clv.py HORIZON_SECONDS)
EXECUTION_CHECK_S = 60.0  # clv_60s: execution check for manual latency (audit POST_PLACEMENT_SECONDS)


def _reference_price(tick: dict[str, Any], side: str, selection: str) -> Optional[float]:
    """CLV reference price: the same-side ladder quote, else the last traded price.

    BASIC-tier files carry ``ltp`` only. Falling back to it keeps CLV measurable there; the fill
    was taken ``spread_ticks_basic`` worse than ltp, so the comparison still includes the spread."""
    price = quoted_price(tick, side, selection)
    if price is not None:
        return price
    return valid_price(tick.get(f"ltp_{selection}"))


def _same_side_at_or_after(
    ticks: Sequence[dict[str, Any]], times: Sequence[float], cutoff: float, side: str, selection: str,
) -> Optional[float]:
    """Same-side price (back for BACK, lay for LAY) of the first tick at or after ``cutoff``."""
    for tick in ticks[bisect_left(times, cutoff):]:
        price = _reference_price(tick, side, selection)
        if price is not None:
            return price
    return None


def _last_pre_off_same_side(ticks: Sequence[dict[str, Any]], side: str, selection: str) -> Optional[float]:
    """Last same-side price before the first in-play tick."""
    last: Optional[float] = None
    for tick in ticks:
        if tick.get("in_play"):
            break
        price = _reference_price(tick, side, selection)
        if price is not None:
            last = price
    return last


def _home_prob(tick: dict[str, Any]) -> Optional[float]:
    """Market implied P(home) from the last traded price, else the back/lay mid."""
    ltp = valid_price(tick.get("ltp_home"))
    if ltp is not None:
        return 1.0 / ltp
    back, lay = valid_price(tick.get("back_home")), valid_price(tick.get("lay_home"))
    if back is not None and lay is not None:
        return 0.5 * (1.0 / back + 1.0 / lay)
    return None


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1.0 - 1e-4)
    return math.log(p / (1.0 - p))


def odds_band(price: float) -> str:
    if price < 1.5:
        return "<1.50"
    if price < 2.5:
        return "1.50-2.49"
    if price < 5.0:
        return "2.50-4.99"
    return ">=5.00"


@dataclass
class _Pending:
    """An order placed but not yet resolved. It resolves at ``event_time``."""

    event_time: float
    placed_at: float
    intent: OrderIntent
    fill: Optional[Fill]  # None when the order lapses


def _reveal(pending: list[_Pending], position: Position, now: float) -> None:
    """Move resolved orders (fill or lapse known by ``now``) out of the open book."""
    still_open: list[_Pending] = []
    for item in pending:
        if item.event_time > now:
            still_open.append(item)
            continue
        if item.fill is not None:
            fill = item.fill
            position.matched.append(MatchedBet(
                side=item.intent.side,
                selection=item.intent.selection,
                price=fill.price,
                stake=fill.stake,
                placed_at=item.placed_at,
                matched_at=fill.time,
                fill_index=fill.index,
                reason=item.intent.reason,
            ))
    pending[:] = still_open


# ================================================================
# Match and run
# ================================================================


def run_match(
    strategy: Strategy,
    episode: dict[str, Any],
    execution: ExecutionModel,
    *,
    bankroll: float = DEFAULT_BANKROLL,
) -> dict[str, Any]:
    """Replay one market. Returns match-level P&L and per-bet records."""
    raw = episode["ticks"]
    if not raw:
        raise ValueError(f"episode {episode.get('match_id')!r} has no ticks")
    times = [_epoch(t["time"]) for t in raw]
    if any(later < earlier for earlier, later in zip(times, times[1:])):
        raise ValueError(f"episode {episode.get('match_id')!r} ticks are not in time order")
    clean = [_strip(t) for t in raw]
    outcome = match_outcome(raw)
    match_id = str(episode.get("match_id", ""))
    meta = {k: episode[k] for k in META_KEYS if k in episode}

    strategy.reset(dict(meta))
    position = Position(match_id=match_id, bankroll=bankroll)
    pending: list[_Pending] = []

    for k in range(len(clean)):
        decision_time = times[k] + execution.latency_s
        _reveal(pending, position, decision_time)
        position.feed = MarketFeed(clean, times, k)
        for intent in strategy.on_tick(dict(clean[k]), position) or []:
            check, fill = plan_fill(
                execution, clean, times, k, decision_time, intent, bankroll=bankroll,
            )
            pending.append(_Pending(
                event_time=times[check] if check is not None else math.inf,
                placed_at=decision_time,
                intent=intent,
                fill=fill,
            ))
        position.open_orders = [item.intent for item in pending]
    _reveal(pending, position, math.inf)

    bets: list[dict[str, Any]] = []
    gross_total = 0.0
    for bet in position.matched:
        gross = bet_gross_pnl(bet.side, bet.selection, bet.price, bet.stake, outcome) if outcome else 0.0
        gross_total += gross
        phase = "in_play" if raw[bet.fill_index].get("in_play") else "pre_match"
        # Binding CLV reference (integration note, 2026-11-02): pre-match bets use the last
        # same-side price before the off; in-play bets use the same-side price 10 min after
        # placement. The 60 s reference is an execution check for manual latency.
        if phase == "pre_match":
            reference = _last_pre_off_same_side(raw, bet.side, bet.selection)
            reference_rule = "last_pre_off"
        else:
            reference = _same_side_at_or_after(raw, times, bet.placed_at + CLV_HORIZON_S, bet.side, bet.selection)
            reference_rule = f"placed_plus_{int(CLV_HORIZON_S)}s"
        check_60s = _same_side_at_or_after(raw, times, bet.placed_at + EXECUTION_CHECK_S, bet.side, bet.selection)
        bets.append({
            "match_id": match_id,
            "family": episode.get("family"),
            "competition_tier": episode.get("competition_tier", "unknown"),
            "side": bet.side,
            "selection": bet.selection,
            "reason": bet.reason,
            "placed_at": bet.placed_at,
            "matched_at": bet.matched_at,
            "price": bet.price,
            "stake": bet.stake,
            "liability": bet.stake if bet.side == "BACK" else bet.stake * (bet.price - 1.0),
            "outcome": outcome or "void",
            "gross_pnl": gross,
            "closing_ltp": _closing_ltp(raw, bet.selection),
            "reference_price": reference,
            "reference_rule": reference_rule,
            "clv": clv_prob(bet.side, bet.price, reference) if reference is not None else None,
            "clv_60s": clv_prob(bet.side, bet.price, check_60s) if check_60s is not None else None,
            "phase": phase,
            "odds_band": odds_band(bet.price),
        })

    commission = execution.commission_on(gross_total) if outcome is not None else 0.0
    return {
        "match_id": match_id,
        "family": episode.get("family"),
        "competition": episode.get("competition"),
        "competition_tier": episode.get("competition_tier", "unknown"),
        "season": _season(episode),
        "outcome": outcome,
        "settled": outcome is not None,
        "n_bets": len(bets),
        "turnover": sum(b["stake"] for b in bets),
        "gross_pnl": gross_total if outcome is not None else 0.0,
        "commission": commission,
        "pnl": (gross_total - commission) if outcome is not None else 0.0,
        "bets": bets,
    }


def run(
    strategy: Strategy,
    episodes: Iterable[dict[str, Any]],
    execution: ExecutionModel,
    *,
    bankroll: float = DEFAULT_BANKROLL,
) -> dict[str, Any]:
    """Replay ``strategy`` over every episode. Returns matches, bets and the execution used."""
    matches = [run_match(strategy, ep, execution, bankroll=bankroll) for ep in episodes]
    bets = [bet for match in matches for bet in match["bets"]]
    return {
        "strategy": strategy.name,
        "execution": asdict(execution),
        "bankroll": bankroll,
        "matches": [{k: v for k, v in m.items() if k != "bets"} for m in matches],
        "bets": bets,
    }


# ================================================================
# Summary statistics
# ================================================================


def _mean(values: Sequence[float]) -> float:
    return float(sum(values) / len(values)) if values else 0.0


def _segment(bets: Sequence[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for bet in bets:
        groups.setdefault(str(bet.get(key, "unknown")), []).append(bet)
    out: dict[str, dict[str, Any]] = {}
    for label in sorted(groups):
        group = groups[label]
        clv = [b["clv"] for b in group if b["clv"] is not None]
        out[label] = {
            "n_bets": len(group),
            "gross_pnl_per_bet": _mean([b["gross_pnl"] for b in group]),
            "hit_rate": _mean([1.0 if b["gross_pnl"] > 0 else 0.0 for b in group]),
            "clv_mean": _mean(clv),
        }
    return out


def summarise(result: dict[str, Any], *, n_bootstrap: int = 10_000) -> dict[str, Any]:
    """Pooled statistics for a run or a set of pooled runs (``matches`` and ``bets`` keys)."""
    matches = [m for m in result["matches"] if m.get("settled")]
    bets = [b for b in result["bets"] if b["outcome"] != "void"]
    pnls = [float(m["pnl"]) for m in matches]
    clv = [float(b["clv"]) for b in bets if b["clv"] is not None]
    clv_60s = [float(b["clv_60s"]) for b in bets if b["clv_60s"] is not None]
    gross = [float(b["gross_pnl"]) for b in bets]
    return {
        "n_matches": len(matches),
        "n_bets": len(bets),
        "mean_pnl_per_match": _mean(pnls),
        "pnl_per_match_ci": bootstrap_ci(pnls, n_bootstrap=n_bootstrap),
        "total_pnl": sum(pnls),
        "commission": sum(float(m["commission"]) for m in matches),
        "turnover": sum(float(m["turnover"]) for m in matches),
        "n_clv": len(clv),
        "clv_mean": _mean(clv),
        "clv_ci": bootstrap_ci(clv, n_bootstrap=n_bootstrap),
        "clv_60s_mean": _mean(clv_60s),
        "clv_60s_ci": bootstrap_ci(clv_60s, n_bootstrap=n_bootstrap),
        "hit_rate": _mean([1.0 if g > 0 else 0.0 for g in gross]),
        "segments": {
            "phase": _segment(bets, "phase"),
            "odds_band": _segment(bets, "odds_band"),
            "competition_tier": _segment(bets, "competition_tier"),
        },
    }


def r0_check(random_summary: dict[str, Any]) -> dict[str, Any]:
    """R0 sanity: a random policy on real prices must not show a positive per-match P&L CI.

    The flag is raised when the 95% CI lower bound is above zero, which would mean the
    execution model or the corpus leaks information.
    """
    lower = float(random_summary["pnl_per_match_ci"]["ci_lower"])
    flagged = lower > 0.0
    return {
        "flagged": flagged,
        "random_ci_lower": lower,
        "reason": ("random policy CI lower bound above zero: check execution and corpus for leakage"
                   if flagged else "random policy does not beat the costs"),
    }


# ================================================================
# Walk-forward folds
# ================================================================


@dataclass(frozen=True)
class Fold:
    """Test one season with a model that saw only earlier seasons."""

    test_season: int
    train_seasons: tuple[int, ...]
    train: tuple[dict[str, Any], ...] = field(repr=False)
    test: tuple[dict[str, Any], ...] = field(repr=False)


def walk_forward_folds(episodes: Sequence[dict[str, Any]], *, min_train_matches: int = 1) -> list[Fold]:
    """One fold per season that has at least ``min_train_matches`` earlier matches."""
    seasons = sorted({_season(ep) for ep in episodes})
    folds: list[Fold] = []
    for season in seasons:
        train = [ep for ep in episodes if _season(ep) < season]
        test = [ep for ep in episodes if _season(ep) == season]
        if len(train) < min_train_matches or not test:
            continue
        folds.append(Fold(
            test_season=season,
            train_seasons=tuple(sorted({_season(ep) for ep in train})),
            train=tuple(train),
            test=tuple(test),
        ))
    return folds


def fold_metadata(fold: Fold) -> dict[str, Any]:
    return {
        "test_season": fold.test_season,
        "train_seasons": list(fold.train_seasons),
        "n_train": len(fold.train),
        "n_test": len(fold.test),
        "train_match_ids": sorted(str(ep.get("match_id", "")) for ep in fold.train),
        "test_match_ids": sorted(str(ep.get("match_id", "")) for ep in fold.test),
    }


def assert_walk_forward(meta: dict[str, Any], fitted_match_ids: Optional[Iterable[str]] = None) -> None:
    """Raise ``WalkForwardLeakError`` if a test season leaks into training.

    Checks: train and test match ids are disjoint; every training season is before the test
    season; and, when given, the model's fitted matches are a subset of the training matches.
    """
    train_ids = set(meta["train_match_ids"])
    test_ids = set(meta["test_match_ids"])
    if train_ids & test_ids:
        raise WalkForwardLeakError(f"season {meta['test_season']}: train and test share matches")
    if any(int(s) >= int(meta["test_season"]) for s in meta["train_seasons"]):
        raise WalkForwardLeakError(f"season {meta['test_season']}: training uses a non-earlier season")
    if fitted_match_ids is not None:
        fitted = {str(m) for m in fitted_match_ids}
        if fitted - train_ids:
            raise WalkForwardLeakError(f"season {meta['test_season']}: model fitted on matches outside training")
        if fitted & test_ids:
            raise WalkForwardLeakError(f"season {meta['test_season']}: model fitted on test matches")


# ================================================================
# Strategy adapters and market-behaviour probes
# ================================================================


class PolicyStrategy:
    """Wrap a ``backtesting.baselines`` policy or ``ValuePolicy`` as a ``Strategy``.

    Each non-HOLD action becomes one order. The stake is the action's bankroll percentage
    (``_SM`` or ``_LG``). The limit is the price the policy saw on the tick.
    """

    def __init__(self, policy: Any, *, name: Optional[str] = None) -> None:
        self._policy = policy
        self.name = name or str(getattr(policy, "name", type(policy).__name__))

    def reset(self, match_meta_without_result: dict[str, Any]) -> None:
        self._policy.reset()

    def on_tick(self, tick: dict[str, Any], position: Position) -> list[OrderIntent]:
        action = BettingAction(int(self._policy.predict(None, tick, None)))
        if action == BettingAction.HOLD:
            return []
        label = action.name
        side = "LAY" if label.startswith("LAY") else "BACK"
        selection = "home" if "_HOME_" in label else "away"
        fraction = LARGE_STAKE_PERCENT if label.endswith("_LG") else SMALL_STAKE_PERCENT
        return [OrderIntent(
            side=side,
            selection=selection,
            price_limit=quoted_price(tick, side, selection),
            stake=fraction * position.bankroll,
            reason=f"policy:{label}",
        )]


class FadeTheJump:
    """Probe: after a one-minute logit move above ``k_logit``, take the opposite side and exit.

    A hypothesis, not a strategy, until it passes the R0 and walk-forward checks.
    """

    name = "fade_jump"

    def __init__(
        self,
        *,
        k_logit: float = 0.15,
        lookback_s: float = 60.0,
        exit_after_s: float = 300.0,
        stake_fraction: float = SMALL_STAKE_PERCENT,
    ) -> None:
        self.k_logit = k_logit
        self.lookback_s = lookback_s
        self.exit_after_s = exit_after_s
        self.stake_fraction = stake_fraction
        self._entry_side: Optional[str] = None
        self._entry_at: Optional[float] = None  # tick time of the open entry
        self._entry_k: int = -1                 # visible index of the open entry

    def reset(self, match_meta_without_result: dict[str, Any]) -> None:
        self._entry_side = None
        self._entry_at = None
        self._entry_k = -1

    def on_tick(self, tick: dict[str, Any], position: Position) -> list[OrderIntent]:
        feed = position.feed
        if feed is None:
            return []
        if self._entry_at is not None:
            if any(o.reason == "fade_entry" for o in position.open_orders):
                return []  # entry still working; wait for it to match or lapse
            # Only this trade's entry: fills after the visible index at which it was placed.
            entries = [b for b in position.matched
                       if b.reason == "fade_entry" and b.fill_index > self._entry_k]
            if not entries:
                self._entry_at = None  # entry lapsed; nothing to close
                return []
            if feed.now - self._entry_at < self.exit_after_s:
                return []
            exit_side = "BACK" if self._entry_side == "LAY" else "LAY"
            self._entry_at = None
            return [OrderIntent(exit_side, "home", None, entries[0].stake, "fade_exit")]

        p_now = _home_prob(tick)
        prev = feed.lagged(self.lookback_s)
        p_then = _home_prob(prev) if prev is not None else None
        if p_now is None or p_then is None:
            return []
        move = _logit(p_now) - _logit(p_then)
        if abs(move) <= self.k_logit:
            return []
        side = "LAY" if move > 0 else "BACK"  # fade: lay a home price that just shortened
        self._entry_side = side
        self._entry_at = feed.now
        self._entry_k = len(feed) - 1
        return [OrderIntent(side, "home", None, self.stake_fraction * position.bankroll, "fade_entry")]


class DriftMomentum:
    """Probe: long-horizon momentum in Test and ODI markets. One entry per match.

    If the home logit moved more than ``k_logit`` over ``lookback_s``, back the side that
    gained and hold to settlement.
    """

    name = "drift"

    def __init__(
        self,
        *,
        families: Sequence[str] = ("odi", "multiday"),
        lookback_s: float = 3600.0,
        k_logit: float = 0.2,
        stake_fraction: float = SMALL_STAKE_PERCENT,
    ) -> None:
        self.families = tuple(families)
        self.lookback_s = lookback_s
        self.k_logit = k_logit
        self.stake_fraction = stake_fraction
        self._family: Optional[str] = None
        self._entered = False

    def reset(self, match_meta_without_result: dict[str, Any]) -> None:
        self._family = match_meta_without_result.get("family")
        self._entered = False

    def on_tick(self, tick: dict[str, Any], position: Position) -> list[OrderIntent]:
        feed = position.feed
        if self._entered or feed is None or self._family not in self.families:
            return []
        p_now = _home_prob(tick)
        prev = feed.lagged(self.lookback_s)
        p_then = _home_prob(prev) if prev is not None else None
        if p_now is None or p_then is None:
            return []
        move = _logit(p_now) - _logit(p_then)
        if abs(move) <= self.k_logit:
            return []
        self._entered = True
        selection = "home" if move > 0 else "away"
        return [OrderIntent("BACK", selection, None, self.stake_fraction * position.bankroll, "drift_entry")]
