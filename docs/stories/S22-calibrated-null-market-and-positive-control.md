# S22 — Calibrated null market, calibration audit and a positive-control market

> **Status (2026-11-02, v2 plan): downgraded to P2.** The synthetic market is now used only for mechanics tests and RL research ([profit-architecture.md](../architecture/profit-architecture.md)). The backtester sanity null (R0) runs on real prices in S39. Run S22 only if PPO is trained on synthetic data again.

**Priority:** P0 · **Tier:** sonnet · **Depends on:** S13, S14, S18 committed · **Wave:** 9
**Conflicts with:** S23 (shares `scripts/prepare_specialist_datasets.py`, `scripts/cricsheet_harmonizer.py`, `scripts/synthetic_market.py`): run S22 first.

## Story

As the person judging whether any policy has an edge, I need a synthetic market whose prices are calibrated to
real match outcomes, so a no-skill policy loses about its costs. I also need a second market with a known, learnable
edge, so I can prove the pipeline finds a real edge when one exists.

## Background (evidence, measured 2026-11-02 on the S18-regenerated corpora)

- S18's report shows no-skill policies profiting on the "martingale" market: `random` odi_men CI lower bound +4,276 per
  match; `heuristic` t20i_men +6,440 and odi_men +32,898 (`docs/reports/strategy-report-2026-11-02.md`).
- Direct measurement of the corpus prices against the real results (`data/specialists/corpus_t20i_men.json.gz`, 343
  matches, every 3rd tick):
  - mean implied home probability 0.387 vs actual home win rate 0.522;
  - the decile calibration is shifted upward everywhere (implied 0.3–0.4 → actual 0.514; implied 0.1–0.2 → actual 0.334);
  - innings 1, home batting: implied 0.312 vs actual 0.508.
  - Per unit staked, back-home EV is +0.96 (se 0.04) and lay-home EV is −1.34 on t20i_men; odi_men shows the same
    pattern (+1.32 and −1.79).
  So `f(state)` in `CricketWinProbModel` (`scripts/synthetic_market.py`, moved there by S13) is badly miscalibrated
  against real outcomes. Any policy that is not symmetric between home and away (including random sizing and the
  heuristic) harvests that bias.
- Why S13's null test did not catch this: it tests whether the next-tick *price change* is predictable, and uses 30
  matches. Neither measures whether the *price level* is unbiased against the outcome.
- Consequence: the held-out P&L, the CLV figures and the S17 gate result in the S18 report say nothing about skill.

## Context pack

- `scripts/synthetic_market.py` (S13, ~300 lines): `MarketNoise(seed, sigma, theta, noise_mode)`,
  `CricketWinProbModel.calculate_win_prob(fmt, innings, overs, score, wickets, target, batting_is_home, is_women,
  specialist_key, noise_logit)`, `synthesize_exchange_ticks(p_home, seed)`, `synthesize_volumes`, `market_model_tag`,
  `CORPUS_FORMAT_VERSION = 2`. It has a latent per-match offset `z ~ N(0, 0.3)` in logit space.
- Callers: `scripts/prepare_specialist_datasets.py` (`parse_cricsheet_json`, `--noise-mode` CLI),
  `scripts/cricsheet_harmonizer.py`, and `scripts/weekly_cricsheet_sync_and_retrain.py:50` (imports names; keep them importable).
- `strategy/fair_value.py` (S14): `FairValueModel.fit(episodes)`, `predict_home(tick)`, isotonic calibration, per family
  (t20 / odi / multiday), `save`/`load`. Fitting needs outcome labels; those come only from `SETTLEMENT_ONLY_KEYS`
  (`winner`, `_meta`, `winner_is_home`) and only on the **training** split.
- Splits: the chronological split used by S09 in `scripts/train_format_specialists.py` (`grep -n "def .*split"`). The
  holdout must never be used to fit the market.
- Evaluator: `evaluate_specialist_holdout(model, key, name, episodes, policy=...)` returns metrics and baselines with
  bootstrap CIs (`pnl_ci95_low`, ...).
- Diagnostics to turn into code: the calibration check that found this (decile table of de-vigged implied home
  probability vs actual outcome, split by innings and by whether the home side is batting).
- Observed in the corpus: the home side always bats first in innings 1.

## Owned files

- `scripts/synthetic_market.py`
- `scripts/prepare_specialist_datasets.py` (market construction call and CLI only)
- `scripts/cricsheet_harmonizer.py` (the same change, kept in sync)
- New: `scripts/audit_market_calibration.py`
- New: `tests/unit/test_s22_calibrated_market.py`

## Tasks

1. **Calibrated base price.** Add `market_mode="calibrated"` (new default) to the corpus builders. The base fair
   probability comes from a `FairValueModel` fitted on the **training matches only**, cross-fitted: for each training
   match use a model fitted on the other folds (5 folds), so a match's own result never shapes its own price. For
   holdout matches use the model fitted on all training matches. Keep the martingale noise and the spread on top. Keep
   `legacy` (the current hand model) selectable, and tag its corpora `legacy:v2`.
2. **Audit.** `scripts/audit_market_calibration.py --corpus <path>` reports:
   - the de-vigged mid vs actual outcome, per decile;
   - ECE and a Brier skill score against the base rate;
   - the per-side EV per unit staked for back_home, back_away, lay_home and lay_away, with standard errors.
   It exits 1 if any |EV| > 3 se **and** |EV| > 0.03, or if ECE > 0.04. It must fail on a legacy-market fixture and pass
   on a calibrated one.
3. **Positive control.** `market_mode="lagged"`: price = calibrated fair value computed from the state `k` decision
   ticks earlier (`lag_ticks`, default 3), plus noise and spread. This market has a real, learnable edge (the price is
   stale). Tag it `lagged:v1`. A test shows that the `value` policy (S14) earns a significantly positive P&L on
   `lagged` and not on `calibrated` (bootstrap CI), on a fixture of at least 60 matches.
4. **A proper null test.** On `calibrated`, with at least 100 held-out matches, the `random` and `heuristic` baselines must
   have a P&L CI that includes zero or lies below it, and each side's EV must pass the audit. Keep this as a test that
   generates its own fixture; do not store a corpus.
5. Tag every tick with `market_model` (`calibrated:v1`, `lagged:v1`, `legacy:v2`) and bump `CORPUS_FORMAT_VERSION`. S17's
   gate must treat `legacy:*` and `ou:*` as non-tradeable evidence. If `rl/model_zoo.py` needs a change for that, report
   it and do not edit it.

## Success criteria

- [ ] The audit fails (exit 1) on a legacy-market fixture and passes on a calibrated one.
- [ ] On the calibrated fixture, random and heuristic P&L CIs include or fall below 0 (at least 100 matches).
- [ ] On the lagged fixture, the value policy has a P&L CI lower bound above 0.
- [ ] No holdout match influences any price: a test builds a corpus, changes one holdout match's winner, and shows that
      all prices are identical.
- [ ] Existing imports from `prepare_specialist_datasets` still work, and the baseline suite passes.

## Verification

```bash
python -m pytest tests/unit/test_s22_calibrated_market.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Regenerating the real corpora and retraining (rerun S18 afterwards).
- Real-odds data (needs the historical table; see S23).
