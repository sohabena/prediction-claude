# S66 — SRL feasibility analysis (separate track)

**Priority:** P2 · **Tier:** haiku · **Depends on:** S58, S59 · **Wave:** 17B · **Plan:** docs/architecture/training-strategy.md §3

## Story

As the strategy owner, I want to know whether simulated (SRL) matches are beatable before any SRL model is built.

## Owned files

- New `scripts/srl_feasibility.py`, `tests/unit/test_s66_srl.py`

## Tasks

1. From the S58 dataset (simulated matches only): closing (T-0) de-vigged calibration against results with Wilson
   CIs and the fitted favourite–longshot line (`training.market.fit_fav_longshot`); margin distribution; how much SRL
   books move between T-60m and T-0; in-play gap between listing and match-page prices.
2. Verdict rule written into the report: "feasible" only if a calibration bias exceeds the median margin with a 95% CI
   on ≥ 300 settled SRL matches; otherwise "not feasible yet" with the number of matches still needed.
3. Write `reports/research/srl_<YYYYMMDD>.md`. Tests on synthetic data with and without a planted bias.

## Guardrails

No DB writes, no Docker, do not commit.
