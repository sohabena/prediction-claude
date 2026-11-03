# S63 — Evaluation harness and gate report

**Priority:** P0 · **Tier:** haiku · **Depends on:** S58, S59 · **Wave:** 17B · **Plan:** docs/architecture/training-strategy.md §3–4

## Story

As the operator, I need one referee that scores any probability source and any policy against the LotusBook price on
held-out recorded matches, and says pass, fail or not enough data for each rung, without letting anyone tune it into
an edge.

## Owned files

- New `training/evaluate.py`, `training/segments.py` (pre-registered segment definitions; S61 imports it),
  `scripts/evaluate_models.py`, `tests/unit/test_s63_evaluate.py`

## Tasks

1. `training/segments.py`: a frozen list of segment definitions (name, predicate over a match record): all, family
   (t20/odi/test), gender, competition tier, market favourite (p_mkt ≥ 0.65) / mid / longshot (≤ 0.35), simulated vs
   real. Include a module-level version string; the gate report records it. Segments cannot be passed in at runtime.
2. Prediction contract: rows `{match_id, snapshot, p_home, p_sd?}` from any source (S60, S61, the market itself).
3. Metrics against the market at the same snapshot: log loss, Brier, ECE and reliability bins; paired bootstrap CI
   (2,000 resamples by match, seeded) of the log-loss difference.
4. Policy simulation over S58 snapshots with `strategy.staking.select`/`stake` when available (fall back to a fixed
   minimum-edge rule): fill at the 60 s takeable price, settle on `home_won`, CLV per bet with
   `shared.metrics.clv_prob` against the binding reference (pre-match: last price before the off; in-play: same side
   10 min after), P&L with bootstrap CI.
5. Gate report per segment: R1 and R2 status (`pass` / `fail` / `insufficient`, with matches still needed) using the
   thresholds in the plan §4. Write `reports/gates/<YYYYMMDD>.json` and `.md`. Do not modify `strategy/trust.py`.
6. Tests: a source equal to the market has exactly zero difference; an oracle passes on a constructed set; tiny n is
   `insufficient`; unknown segment names are rejected.

## Guardrails

Never write to the DB in tests. Do not touch Docker, do not commit.
