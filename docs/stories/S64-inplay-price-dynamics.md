# S64 — In-play price dynamics (research track)

**Priority:** P1 · **Tier:** haiku · **Depends on:** S58, S59, S63 · **Wave:** 17B · **Plan:** docs/architecture/training-strategy.md §3

## Story

As the strategy owner without a live score, I want to know whether in-play LotusBook prices over- or under-react at
horizons a person can act on, measured honestly, before anyone trusts an in-play signal.

## Owned files

- New `training/inplay.py`, `scripts/research_inplay_dynamics.py`, `tests/unit/test_s64_inplay.py`

## Tasks

1. From S58 in-play snapshots (every 2 min), price-only features: de-vigged p change over the last 2/6/10 min, size
   of the last move, rolling volatility, spread, minutes since the first in-play row. Label: de-vigged p 10 min later
   minus p at t + 60 s (what a manual bettor could capture).
2. Baselines: no change (random walk); momentum and reversion sign rules. Models: ridge regression and a small GBM.
   Walk-forward by match date, purged by match.
3. Report out-of-sample R², hit rate on large moves, and an S63 policy simulation of "fade large moves". Write
   `reports/research/inplay_<YYYYMMDD>.md`. This story produces no signals.
4. Tests on synthetic paths: a mean-reverting process is detected, a random walk is not; no look-ahead.

## Guardrails

No DB writes, no Docker, do not commit.
