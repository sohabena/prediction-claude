# S62 — Bet selection and uncertainty-aware staking

**Priority:** P0 · **Tier:** haiku · **Depends on:** S59 · **Wave:** 17B · **Plan:** docs/architecture/training-strategy.md §3

## Story

As the person placing bets by hand, I want a signal only when the edge survives the margin, the 60 s delay and our
uncertainty, with a stake that respects all three.

## Background

`strategy/value_policy.py` (`ValuePolicy`, `ValueStrategy`, price tick rounding, `round_stake`), `strategy/signals.py`
(`Signal`), `backtesting/execution.py` (`plan_fill`, latency), `shared/metrics.py`.

## Owned files

- New `strategy/staking.py`, `tests/unit/test_s62_staking.py`
- `strategy/value_policy.py`: only an optional hook that uses `strategy.staking` when a `p_sd` is supplied; existing
  behaviour and tests unchanged when it is not

## Tasks

1. `edge(side, p, price)`: back = p·price − 1 per unit stake; lay = (1 − p) − p·(price − 1) per unit of backer's
   stake, documented with a worked example. `edge_lower(side, p_mean, p_sd, price, z=1.0)` evaluates the edge at the
   pessimistic probability.
2. `select(p_mean, p_sd, book_after_latency, *, segment, min_edge_by_segment)`: returns side and price or None; bets
   only if `edge_lower` exceeds the segment minimum at the takeable price after the 60 s latency (the caller passes
   that book).
3. `stake(p_mean, p_sd, price, side, bankroll, *, kelly_fraction=0.25, max_match_frac=0.02, max_day_frac=0.06,
   open_match_exposure=0.0, open_day_exposure=0.0)`: Kelly on the mean edge times the shrink factor
   `1 / (1 + (p_sd / edge_prob)^2)` (edge_prob = p_mean − 1/price for a back), capped, rounded with `round_stake`,
   0 below the minimum stake.
4. Tests: no edge, no bet; an edge that is gone at the 60 s price gives no bet; larger uncertainty gives a smaller
   stake; caps bind; lay maths against a hand-computed case.

## Guardrails

No DB, no Docker, do not commit. Signals only; never place or automate bets.
