# S48 — Session-runs model: calibrated distribution of runs in an over window

**Priority:** P1 · **Tier:** haiku · **Depends on:** S24/S25 (ratings, venue priors) · **Wave:** 15 (model only; market evaluation needs S47 data)

## Story

As the strategy owner, I need a calibrated probability that a batting side scores at least N runs between now and
the end of a fixed over window (6, 10, 15, 20 overs in T20; 10, 20, 30, 40, 50 in ODIs). Then session ("fancy") lines can
be priced, and mispriced lines signalled.

## Background

- Session markets quote a NO value and a YES value (e.g. NO 52 / YES 53) for "runs by over K". Backing YES at line L
  wins if runs ≥ L; NO wins if runs < L (the NO value; check exact conventions from S47's discovery notes, and keep the
  rule in one function).
- Cricsheet has every delivery for about 11k matches (`data/raw/{t20s,odis,ipl}`), so the conditional distribution of
  runs in the remaining part of a window, given the state (score, wickets, balls, batting strength, venue), can be
  learnt directly.
- Ratings and priors: `strategy/ratings.py CricketRatings` (`pre_match`, `pre_match_live`, `xi_strength`),
  `shared/venue_priors.py VenuePriors.get(...)` (par run rate, as-of). Walk-forward and leak rules follow S38
  (`scripts/train_fair_value.py walk_forward`, season splits, the as-of cutoff).

## Owned files

- New: `strategy/session_model.py`
- New: `scripts/train_session_model.py`
- New: `tests/unit/test_s48_session_model.py`

## Tasks

1. Training rows from Cricsheet: for each innings, each legal ball `b` and each window end `K` with `b < K·6`, the
   features are:
   - balls left in the window, runs so far in the innings, wickets down, current run rate;
   - format family, gender, venue par run rate (as-of), and the batting/bowling XI strength difference (as-of);
   - powerplay flag.
   The target is the runs from ball `b` to the window end. Exclude innings that ended or were rain-shortened before `K`.
2. Model: predict the conditional distribution of remaining runs, not just the mean. Use a negative-binomial GLM, or a
   gradient-boosted quantile set over 5–95% interpolated to a CDF, whichever is calibrated better. Provide
   `p_at_least(state, window_end, line) -> float`.
3. Walk-forward by season (train < Y−1, calibrate Y−1, test Y). Report per family: CRPS, the pinball loss at 10/50/90%,
   and calibration of `p_at_least` at common lines (reliability by decile).
4. Pricing helper: `fair_rates(state, window_end, line)` returns fair YES/NO odds, plus an `edge(side, quoted_rate)`
   formula. Keep the conventions configurable until S47 confirms them.
5. Leak tests as in S38: a match's own deliveries after ball `b` never enter its features; shuffling holdout results
   changes no features.

## Success criteria

- [ ] For T20, the walk-forward 50% pinball loss beats a "current run rate × balls left" baseline in most seasons
      (report it, do not tune on the holdout).
- [ ] `p_at_least` is monotone non-increasing in `line` (test).
- [ ] The leak tests pass.
- [ ] Baseline suite passes.

## Verification

```bash
python -m pytest tests/unit/test_s48_session_model.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Signals on session markets (a follow-up once S47 has recorded lines and the conventions are confirmed).
