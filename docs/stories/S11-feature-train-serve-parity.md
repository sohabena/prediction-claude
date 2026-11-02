# S11 — Feature pipeline train/serve parity

**Priority:** P0 · **Tier:** sonnet · **Depends on:** none · **Wave:** 5
**Conflicts with:** none · **Publishes/consumes:** contract `decision tick` (README, consumed by S12)

## Story

As the person relying on the policy in live trading, I need the observation vector computed in
live to come from the same distribution the policy was trained on, so that live actions mean what
the training metrics say they mean.

## Background (what is wrong today)

- `features/pipeline.py:64` `GAP_THRESHOLD_SECONDS = 30.0`, but synthetic training ticks are
  45–90 s apart (`scripts/prepare_specialist_datasets.py:360`
  `tick_time += timedelta(seconds=randint(45, 90))`). Every training tick therefore triggers
  gap detection (`pipeline.py:127-151`), and the odds history is reset to `[event]`. As a result
  the momentum, market, volume and bookmaker feature groups are computed from a one-event
  history for **every training tick**. In live trading, ticks come every ~3 s
  (`shared/config.py:67 poll_interval=3`), so the history is full and those same features take
  values the policy never saw in training. This is the largest remaining train/serve skew.
- Two normalisers run on top of each other. `pipeline.py:68` creates an
  `OnlineNormalizer(OBSERVATION_SIZE)`, and `pipeline.py:209` updates and applies it on every
  call. S09 then wraps the env in `VecNormalize(norm_obs=True)`
  (`scripts/train_format_specialists.py:725`), and `rl/agent.py:118-150` applies the saved
  VecNormalize stats at inference. The inner normaliser:
  - is never saved or loaded (`save_normalizer`/`load_normalizer`, L265-270, have no callers);
  - starts fresh in each SubprocVecEnv worker and in each live process;
  - returns raw values for the first 10 calls (`features/normalizer.py:27`);
  - keeps adapting to live data.
  So the same market state maps to different observations in training and in live.

## Context pack

- `features/pipeline.py` (271 lines):
  - `class FeaturePipeline` L51.
  - `GAP_THRESHOLD_SECONDS` L64.
  - `__init__(lookback_seconds=120)` L66-79.
  - `add_event` L80.
  - `compute(match_id, event, context, portfolio, position_state) -> np.ndarray(50,)` L93-221. The feature groups
    are built at L155-207 (momentum L~160, market L~166, volume L~178, bookmaker L~181, timing L~190, match
    momentum L~196). The normaliser is applied at L209.
  - `_compute_timing` L223.
  - `_get_recent_history` L250-258 (time cutoff = last ts − lookback).
  - `clear_match` L259.
  - `save_normalizer`/`load_normalizer` L265-271.
- Callers of `FeaturePipeline`, which must keep working with no positional changes:
  - `rl/environment.py:184` (`self.feature_pipeline = FeaturePipeline()`, used in `_get_observation` L632-649 via
    `_tick_to_event` L724 and `_tick_to_context` L752);
  - `virtual_trading/live_trading_loop.py:340` (`self._feature_pipeline.compute(...)`);
  - `grep -rn "FeaturePipeline(" --include=*.py .` before editing.
- `features/normalizer.py` (OnlineNormalizer, ~80 lines): `update` L29, `transform` L40 (clip ±10, identity while
  `count < 10`), `update_and_transform` L52, `save`/`load` L57-80.
- VecNormalize is the single source of observation scaling after this story. Inference applies it in
  `rl/agent.py:118` (`_normalize_obs`), and training builds it at `scripts/train_format_specialists.py:709-725`. Do not
  edit those files.
- Gotcha: every saved model becomes stale when the observation changes. That is expected: S18 retrains. Do not touch
  `models/`.

## Owned files

- `features/pipeline.py`
- `features/normalizer.py`
- New: `tests/unit/test_s11_feature_parity.py`

## Tasks

1. Add keyword-only constructor params with defaults:
   - `gap_threshold_seconds: float = 600.0`, larger than any synthetic inter-tick gap. Document that it
     must exceed the largest training gap.
   - `normalize: bool = False`.
   Keep the `GAP_THRESHOLD_SECONDS` class attribute as an alias for the default.
2. With `normalize=False`, `compute` returns the raw feature vector, clipped to ±1e6 with NaN/inf → 0. It does not
   touch the `OnlineNormalizer`. Keep `normalize=True` working for anyone who opts in.
3. Make momentum and history features **tick-count based as well as time based**. `_get_recent_history`
   returns the last `max(n_ticks_in_lookback, min_history_ticks)` events, with `min_history_ticks: int = 5`.
   This gives a 45–90 s cadence and a 3 s cadence the same number of history points. Document the choice in
   the docstring.
4. Add `FeaturePipeline.feature_names() -> list[str]` (50 names, in order). The parity test and S18's report use it.
5. Parity test. Build one 30-tick fixture episode. Feed it tick by tick:
   - (a) through `CricketBettingEnv` (`_get_observation`);
   - (b) through a fresh `FeaturePipeline().compute` with the same events and contexts, as the live loop would.
   The vectors must be equal (atol 1e-6), except the portfolio and position groups, which you mask out by
   index in the test.
6. Skew test. Feed the same fixture at 60 s spacing and at 3 s spacing (re-timestamp it). For every
   history-based feature, the mean absolute difference between the two must be below a stated bound. The point
   is that the feature means the same thing at either cadence. Report any feature that cannot meet the bound.

## Success criteria

- [ ] With default params, no training tick in a 45–90 s fixture triggers `gap_detected`.
- [ ] `FeaturePipeline().compute` output does not depend on how many earlier calls were made on *other* matches
      (normaliser state removed). A test calls it on match A, then B, then A again and asserts A's vector is unchanged.
- [ ] The env path and the live path produce identical market-feature vectors for the same ticks.
- [ ] `feature_names()` returns 50 unique names.
- [ ] The baseline suite still passes (`214 passed` at `443c42e`).

## Verification

```bash
python -m pytest tests/unit/test_s11_feature_parity.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Live decision cadence (S12).
- Retraining models (S18).
- Changing `OBSERVATION_SIZE` or adding features (backlog: fair-value edge feature, see S14).
