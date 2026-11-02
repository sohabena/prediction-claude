"""S42 trust gate: a trust label per strategy version for manual-bet signals.

Labels: ``unproven`` (not yet proven against the reference price), ``trusted`` (every rung-R3 criterion met
for ``GRADUATION_REQUIRED_DAYS`` consecutive days), ``suspended`` (the kill switch is active).
The label only tells the person placing bets by hand how much to trust a signal. Nothing here places a bet.

CLV follows the binding definition in ``docs/architecture/profit-architecture.md``: ``shared.metrics.clv_prob``
against the reference price of ``CLV_REFERENCE_SOURCE`` (S45). That is LotusBook match odds by default, because
Betfair is blocked in the operator's country; ``betfair`` remains selectable. Only ticks of the configured source
count. Pre-match bets use the last pre-off price. In-play bets use the same-side price ``CLV_HORIZON_SECONDS``
after placement. The reference tick is always strictly after the fill (self-reference guard). Bets with no
reference price are excluded from CLV and never count as evidence for trust.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, fields
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping, Optional, Sequence

from backtesting.report import bootstrap_ci
from rl.graduation import GraduationEvaluator
from scraper.match_filter import is_simulated_name
from shared.constants import (
    CLV_HORIZON_SECONDS,
    CLV_REFERENCE_SOURCE,
    CLV_REFERENCE_SOURCES,
    KEY_TRUST_PREFIX,
    TRUST_CRITERIA,
    TRUST_STATE_PATH,
)
from shared.logging import setup_logging
from shared.metrics import clv_prob, max_drawdown, per_match_pnl, portfolio_clv, selection_of, side_of
from strategy.signals import Signal, SignalBus

logger = setup_logging("strategy_trust")

LABEL_UNPROVEN = "unproven"
LABEL_TRUSTED = "trusted"
LABEL_SUSPENDED = "suspended"
REASON_NO_REFERENCE = "insufficient reference-price evidence"
REASON_NO_BETFAIR = REASON_NO_REFERENCE  # S42 name, kept for imports
BETFAIR_SOURCE = "betfair"
_MIN_BOOTSTRAP_N = 5


def _valid_price(value: Any) -> Optional[float]:
    if value is None:
        return None
    price = float(value)
    return price if price > 1.0 else None


def _price_on_side(tick: Mapping[str, Any], side: str, selection: str) -> Optional[float]:
    return _valid_price(tick.get(f"{'lay' if side == 'LAY' else 'back'}_{selection.lower()}"))


def _from_source(tick: Mapping[str, Any], source: str) -> bool:
    """True when the tick belongs to ``source``. Ticks without a ``source`` key are taken as belonging to it."""
    return tick.get("source") in (None, source)


def in_play_at(ticks: Sequence[Mapping[str, Any]], placed_at: datetime) -> bool:
    """True when the last tick at or before ``placed_at`` was in play. ``ticks`` sorted by time."""
    state = False
    for tick in ticks:
        if tick["time"] > placed_at:
            break
        state = bool(tick.get("is_live"))
    return state


def last_pre_off_price(ticks: Sequence[Mapping[str, Any]], side: str, selection: str) -> Optional[float]:
    """Last valid pre-match price on the same side (the price before the off)."""
    for tick in reversed(ticks):
        if tick.get("is_live"):
            continue
        price = _price_on_side(tick, side, selection)
        if price is not None:
            return price
    return None


def price_at_horizon(
    ticks: Sequence[Mapping[str, Any]],
    placed_at: datetime,
    side: str,
    selection: str,
    seconds: float = CLV_HORIZON_SECONDS,
) -> Optional[float]:
    """First valid same-side price at or after ``placed_at + seconds``. ``ticks`` sorted by time."""
    cutoff = placed_at + timedelta(seconds=seconds)
    for tick in ticks:
        if tick["time"] < cutoff:
            continue
        price = _price_on_side(tick, side, selection)
        if price is not None:
            return price
    return None


def reference_clv(
    bet: Mapping[str, Any],
    ticks: Sequence[Mapping[str, Any]],
    source: str = CLV_REFERENCE_SOURCE,
) -> Optional[float]:
    """Side-aware CLV in probability points against the reference price of ``source``, or None when there is none.

    ``ticks`` are sorted by time. Ticks of other sources are ignored. The self-reference guard (reference tick
    strictly after the fill) is applied by :func:`annotate_reference`, which is the evaluation path.
    """
    action = bet["action"]
    side = side_of(action)
    selection = selection_of(action)
    entry = _valid_price(bet.get("odds"))
    if entry is None or selection == "":
        return None
    usable = [t for t in ticks if _from_source(t, source)]
    if bet.get("in_play"):
        price = price_at_horizon(usable, bet["placed_at"], side, selection)
    else:
        price = last_pre_off_price(usable, side, selection)
    if price is None:
        return None
    return clv_prob(side, entry, price)


def betfair_clv(bet: Mapping[str, Any], ticks: Sequence[Mapping[str, Any]]) -> Optional[float]:
    """S42 name: :func:`reference_clv` against the Betfair source."""
    return reference_clv(bet, ticks, source=BETFAIR_SOURCE)


def annotate_reference(
    bets: Iterable[Mapping[str, Any]],
    ticks_by_match: Mapping[str, Sequence[Mapping[str, Any]]],
    source: str = CLV_REFERENCE_SOURCE,
) -> list[dict[str, Any]]:
    """Copy each bet with ``in_play``, ``is_real`` (not a simulated league) and ``reference_clv``.

    The reference price is taken from ticks of ``source`` only and must be strictly after the fill
    (``placed_at``), so a fill can never be its own reference. ``betfair_clv`` is also written when
    ``source`` is ``betfair``. ``bets`` need ``match_id``, ``placed_at``, ``action``, ``odds``.
    ``team_home``/``team_away`` are optional.
    """
    if source not in CLV_REFERENCE_SOURCES:
        raise ValueError(f"source must be one of {CLV_REFERENCE_SOURCES}, got {source!r}")
    sorted_ticks = {
        str(m): sorted(ticks, key=lambda t: t["time"]) for m, ticks in ticks_by_match.items()
    }
    out: list[dict[str, Any]] = []
    for bet in bets:
        match_id = str(bet["match_id"])
        ticks = sorted_ticks.get(match_id, [])
        competition = next((t.get("competition") for t in ticks if t.get("competition")), "")
        tick_teams = {t.get("team_home") for t in ticks} | {t.get("team_away") for t in ticks}
        placed_at = bet["placed_at"]
        same_source = [t for t in ticks if _from_source(t, source)]
        row = dict(bet)
        row["match_id"] = match_id
        row["is_real"] = not is_simulated_name(
            competition, bet.get("team_home"), bet.get("team_away"), *tick_teams
        )
        row["in_play"] = in_play_at(same_source, placed_at)
        after_fill = [t for t in same_source if t["time"] > placed_at]  # self-reference guard
        row["reference_clv"] = reference_clv(row, after_fill, source=source)
        if source == BETFAIR_SOURCE:
            row["betfair_clv"] = row["reference_clv"]
        out.append(row)
    return out


def annotate_betfair(
    bets: Iterable[Mapping[str, Any]],
    ticks_by_match: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    """S42 name: :func:`annotate_reference` against the Betfair source (writes ``reference_clv`` and ``betfair_clv``)."""
    return annotate_reference(bets, ticks_by_match, source=BETFAIR_SOURCE)


def _nan_to_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    value = float(value)
    return None if math.isnan(value) else value


def trust_metrics(
    annotated: Sequence[Mapping[str, Any]],
    *,
    allocated_bankroll: float,
    missed_count: Optional[int] = None,
    n_bootstrap: int = 10_000,
) -> dict[str, Optional[float]]:
    """Trust criteria inputs from annotated bets (see :func:`annotate_reference`).

    Only settled bets on real matches count. ``None`` means not measured, which fails its criterion.
    ``missed_count`` is the number of signals that were not filled (None until S40 records them).
    The returned dict also holds reporting-only fields (``reference_clv_n``, ``reference_clv_mean``,
    ``lotusbook_clv_mean``); they are not criteria.
    """
    real = [b for b in annotated if b.get("is_real", True)]
    settled = [b for b in real if b.get("settled_at") is not None and b.get("profit_loss") is not None]

    clv_rows = [b for b in settled if b.get("reference_clv") is not None]
    clv = portfolio_clv(clv_rows, field="reference_clv", n_bootstrap=n_bootstrap)

    pnl_values = list(per_match_pnl(settled).values())
    pnl_ci_upper: Optional[float] = None
    if len(pnl_values) >= _MIN_BOOTSTRAP_N:
        pnl_ci_upper = float(bootstrap_ci(pnl_values, n_bootstrap=n_bootstrap)["ci_upper"])

    drawdown: Optional[float] = None
    if allocated_bankroll > 0:
        ordered = sorted(settled, key=lambda b: b.get("settled_at") or b["placed_at"])
        equity = [float(allocated_bankroll)]
        for b in ordered:
            equity.append(equity[-1] + float(b["profit_loss"]))
        drawdown = max_drawdown(equity)[0] / float(allocated_bankroll)

    missed_rate: Optional[float] = None
    if missed_count is not None:
        total = len(real) + int(missed_count)
        if total > 0:
            missed_rate = int(missed_count) / total

    clv_lotus = [float(b["clv"]) for b in settled if b.get("clv") is not None]
    return {
        "bet_volume": float(len(settled)),
        "min_matches": float(len({b["match_id"] for b in settled})),
        "clv_ci_lower": _nan_to_none(clv["ci95_lower"]),
        "pnl_per_match_ci_upper": pnl_ci_upper,
        "max_drawdown": drawdown,
        "missed_rate": missed_rate,
        "reference_clv_n": float(len(clv_rows)),
        "reference_clv_mean": _nan_to_none(clv["mean_clv"]),
        "lotusbook_clv_mean": (sum(clv_lotus) / len(clv_lotus)) if clv_lotus else None,
    }


def trust_reasons(metrics: Mapping[str, Optional[float]], status: Any) -> list[str]:
    """Human-readable reasons for each unmet trust criterion."""
    met = {c.name: c.met for c in status.criteria}
    reasons: list[str] = []
    for name, config in TRUST_CRITERIA.items():
        if met.get(name, False):
            continue
        raw = metrics.get(name)
        if raw is None and name == "clv_ci_lower":
            reasons.append(REASON_NO_REFERENCE)
        elif raw is None:
            reasons.append(f"{name} not measured")
        else:
            op = {"ge": ">=", "gt": ">", "le": "<="}.get(config.get("op", "ge"), ">=")
            reasons.append(f"{name} {raw:.4g} not {op} {config['threshold']}")
    return reasons


class TrustRegistry:
    """Per-version trust state. Reads are synchronous and cached; ``refresh`` is the nightly write.

    ``trust_label`` returns ``suspended`` while the kill switch is active for the version (or globally),
    ``trusted`` once the streak is complete, and ``unproven`` otherwise. ``reference_source`` names the
    price feed used for CLV (S45).
    """

    def __init__(
        self,
        *,
        redis: Any = None,
        kill_switch: Any = None,
        evaluator: Optional[GraduationEvaluator] = None,
        now_fn: Any = None,
        reference_source: str = CLV_REFERENCE_SOURCE,
    ) -> None:
        self._redis = redis
        self._kill = kill_switch
        self._source = reference_source
        self._evaluator = evaluator or GraduationEvaluator(
            state_path=TRUST_STATE_PATH, criteria=TRUST_CRITERIA, now_fn=now_fn
        )
        self._cache: dict[str, dict[str, Any]] = {}

    def _entry(self, strategy_version: str) -> Optional[dict[str, Any]]:
        return self._cache.get(strategy_version)

    def trust_label(self, strategy_version: str) -> str:
        if self._kill is not None and self._kill.blocked(strategy_version) is not None:
            return LABEL_SUSPENDED
        entry = self._entry(strategy_version)
        if entry is not None and entry["ready"]:
            return LABEL_TRUSTED
        return LABEL_UNPROVEN

    def trust_reason(self, strategy_version: str) -> str:
        if self._kill is not None:
            blocked = self._kill.blocked(strategy_version)
            if blocked is not None:
                return f"kill switch: {blocked}"
        entry = self._entry(strategy_version)
        if entry is None:
            return "not yet evaluated"
        return entry["reason"]

    async def load(self, strategy_versions: Iterable[str]) -> None:
        """Restore cached statuses from Redis (``trust:<version>``). Missing or failed reads leave the cache empty."""
        if self._redis is None:
            return
        for version in strategy_versions:
            try:
                data = await self._redis.get_json(f"{KEY_TRUST_PREFIX}{version}")
            except Exception as e:
                logger.warning("trust_load_failed", strategy_version=version, error=type(e).__name__)
                continue
            if data:
                self._cache[version] = data

    async def refresh(
        self,
        strategy_version: str,
        bets: Sequence[Mapping[str, Any]],
        ticks_by_match: Mapping[str, Sequence[Mapping[str, Any]]],
        *,
        allocated_bankroll: float,
        missed_count: Optional[int] = None,
        n_bootstrap: int = 10_000,
    ) -> dict[str, Any]:
        """Re-evaluate one version from its bets and their reference-price ticks, cache and persist the result."""
        annotated = annotate_reference(bets, ticks_by_match, source=self._source)
        metrics = trust_metrics(
            annotated,
            allocated_bankroll=allocated_bankroll,
            missed_count=missed_count,
            n_bootstrap=n_bootstrap,
        )
        status = self._evaluator.evaluate_metrics(metrics, version=strategy_version)
        if status.ready:
            reason = ""
        else:
            reasons = trust_reasons(metrics, status)
            if not reasons:
                reasons = [f"streak {status.consecutive_days}/{status.required_days} days"]
            reason = "; ".join(reasons) + f" (reference: {self._source})"
        entry: dict[str, Any] = {
            "ready": bool(status.ready),
            "consecutive_days": int(status.consecutive_days),
            "required_days": int(status.required_days),
            "reason": reason,
            "reference_source": self._source,
            "metrics": {k: v for k, v in metrics.items()},
            "evaluated_at": datetime.now().astimezone().isoformat(),
        }
        self._cache[strategy_version] = entry
        if self._redis is not None:
            try:
                await self._redis.set_json(f"{KEY_TRUST_PREFIX}{strategy_version}", entry)
            except Exception as e:
                logger.warning("trust_persist_failed", strategy_version=strategy_version, error=type(e).__name__)
        logger.info(
            "trust_evaluated",
            strategy_version=strategy_version,
            label=self.trust_label(strategy_version),
            reference_source=self._source,
            consecutive_days=entry["consecutive_days"],
            bet_volume=metrics["bet_volume"],
        )
        return entry


@dataclass(frozen=True)
class TrustedSignal(Signal):
    """A ``Signal`` that carries its trust label. ``SignalBus`` serialises it through ``to_dict``."""

    trust: str = LABEL_UNPROVEN
    trust_reason: str = ""

    def text_lines(self) -> list[str]:
        line = f"trust: {self.trust}" + (f" ({self.trust_reason})" if self.trust_reason else "")
        return super().text_lines() + [line]


def with_trust(signal: Signal, label: str, reason: str) -> TrustedSignal:
    base = {f.name: getattr(signal, f.name) for f in fields(Signal)}
    return TrustedSignal(**base, trust=label, trust_reason=reason)


class TrustedSignalBus:
    """Wraps a ``SignalBus``: attaches the trust label to each signal and drops suspended ones."""

    def __init__(self, bus: SignalBus, registry: TrustRegistry) -> None:
        self._bus = bus
        self._registry = registry

    async def publish(self, signal: Signal) -> bool:
        """Publish with a trust label. Returns False when suppressed (suspended) or as a duplicate."""
        label = self._registry.trust_label(signal.strategy_version)
        if label == LABEL_SUSPENDED:
            logger.info(
                "signal_suspended",
                match=signal.match,
                strategy_version=signal.strategy_version,
                reason=self._registry.trust_reason(signal.strategy_version),
            )
            return False
        reason = self._registry.trust_reason(signal.strategy_version) if label != LABEL_TRUSTED else ""
        return await self._bus.publish(with_trust(signal, label, reason))
