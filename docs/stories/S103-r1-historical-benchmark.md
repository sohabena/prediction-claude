# S103 — R1 benchmark: our pre-match model against historical closing odds

**Pillars:** 2 and 3 · **Priority:** P1 · **Tier:** haiku · **Depends on:** S98 (and S102 before results are trusted) · **Wave:** 23

## User story

As the operator, I want to know, on hundreds of past matches, whether our pre-match model's probability beats the
bookmakers' closing price, because that is rung R1 of the validation ladder and our own recordings need months to
reach 200 matches per segment.

## Honest limits

- A closing price is the hardest benchmark (it holds all pre-match information). Beating Elo says nothing about
  beating the market.
- Only out-of-sample predictions count (Golden Rulebook section 7 rule 4): the model's prediction for a match must come
  from a walk-forward fold that never saw that match's season.
- Results from a store that FAILs S102 are not evidence.

## Acceptance criteria

1. `scripts/train_prematch_model.py` gains `--export-predictions PATH`: writes one JSON line per walk-forward **test**
   match (`match_id` = Cricsheet id, `date`, `season`, `gender`, `tier`, `home`, `away`, `label`, `p_v3`, `p_elo`,
   `fold_season`). Default behaviour and outputs are unchanged when the flag is absent (existing tests must pass).
2. `training/historical_benchmark.py` (pure):
   - `closing_by_match(records, index)`: one closing fair P(home) per linked Cricsheet match, oriented to the
     Cricsheet team order, choosing the bookmaker by priority `pinnacle` > `average`/consensus kinds > others (record
     which was used); `kind="close"` first, else the last `snapshot` before the start.
   - `benchmark(predictions, closing, *, segments)`: per segment (family t20/odi × gender, and competition), n
     matches, log loss and Brier of the model and of the market, the difference (model − market) with a match
     bootstrap 95% CI (`shared.metrics.cluster_bootstrap`, cluster = match), and a verdict: `beats market` only when
     the CI upper bound is below 0 **and** n ≥ 200; `too few matches` under 200; else `does not beat market`.
   - A blend check: season-wise cross-validated logistic regression on (logit market, logit model); report whether
     the blend's held-out log loss beats the market's (with CI). This is the realistic route to value.
   - Secondary only: flat-stake ROI of backing the model's side at the closing price when model − market exceeds
     3 / 6 points, with a match bootstrap CI, labelled "closing price, not takeable by a person; for direction only".
3. `scripts/benchmark_historical_odds.py --predictions PATH [--root data/historical_odds] [--out reports/historical_odds]`
   writes `r1-<UTC date>/report.json` and `report.md` (one table per segment, verdicts in words, sample sizes always
   shown) and prints the summary. No database, no network.
4. Tests `tests/unit/test_s103_historical_benchmark.py` with synthetic data: orientation when Cricsheet lists the
   away side first; bookmaker priority; a model equal to the market gives a CI around 0; a market-beating synthetic
   model is detected only with enough matches; under 200 matches says `too few matches`; the export flag writes test
   matches only (no training-season match appears) on a tiny fake dataset.
