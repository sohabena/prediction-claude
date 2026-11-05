# S43 — Signal-path integration: one execution model, the same strategy in backtest and live, ratings wired in

**Priority:** P0 · **Tier:** haiku (operator request) · **Depends on:** S38, S39, S40 committed · **Wave:** 13 (parallel with S42)
**Conflicts with:** none. S42 owns `shared/constants.py`, `virtual_trading/risk_manager.py` and the graduation files; do not touch them.

## Story

As the person betting by hand from signals, I need the backtest to test exactly the strategy, model and execution
delay that produce live signals, and the live model to receive the pre-match information it was trained on. Otherwise
a backtest result says nothing about the signals I act on.

## Background (open items from S40, verified in code at `1143d94`)

1. **Two definitions of "manual" execution.**
   - `backtesting/execution.py` presets (L29-30): `"manual": {"latency_s": 60.0}`, i.e. the decision sees prices 60 s old,
     plus the default `bet_delay_s=5` (65 s info-to-fill).
   - `strategy/runner.py:57 live_execution()`: `latency_s=0, bet_delay_s=MANUAL_LATENCY_SECONDS (60)` (60 s).
   - Correct model: the app decides on fresh data (scrape and processing ≈ 5 s on historical exchange timestamps), then a
     person needs ≈ 60 s to place the bet.
2. **The backtest tests a different object.** `scripts/run_real_backtest.py:72-81 build_strategy("value")` fits a v1
   `FairValueModel` and wraps `ValuePolicy` in `PolicyStrategy`. Live signals come from `strategy/value_policy.py
   ValueStrategy` with the **v2** model.
3. **The live v2 model runs without its pre-match fields.** `virtual_trading/live_trading_loop.py:351-367 _get_runner`
   builds `ValueStrategy(model)` per family but never builds a `CricketRatings`. So `pm_*` fields are neutral and v2
   degrades to state-only (out of distribution vs training).
4. **Per-match runner state is never released.** `strategy/runner.py:192 StrategyRunner.finish(match_id)` is never called;
   `_handle_match_result` (`live_trading_loop.py:569`) should call it.
5. **No v2 model artifact is installed.** `_load_fair_models` (`live_trading_loop.py:68-80`) loads
   `model_path(family, None, "v2")` = `models/fair_value/<family>_v2_all.joblib`. Neither file exists; the S38 walk-forward
   wrote its model to a scratch folder.

## Context pack

- `strategy/ratings.py`: `CricketRatings(source: RawSource, *, k_by_family=None)` where `RawSource` is a callable
  returning an iterator of raw Cricsheet dicts (each with `match_id` = file stem). See
  `scripts/train_fair_value.py:141 iter_cricsheet(dirs, family, stats)` and `FAMILY_DIRS` (L48), `DEFAULT_DATA_ROOT =
  Path("data")` (L46). `pre_match_live(team_home, team_away, family, gender, as_of, *, venue=None, city=None,
  players=None, toss_winner=None, toss_decision=None, tier=None, normalize_team=None) -> dict` returns the `pm_*` keys.
  Building ratings scans all Cricsheet files of a family; measure the time and cache the result.
- `strategy/fair_value.py`: `FairValueModel(family, version="v2")`, `model_path(family, key, version, root)`, `family_of`,
  `FAMILIES`; the v2 two-stage fit (`fit` / `fit_arrays`).
- `strategy/value_policy.py ValueStrategy(model, ...)`: implements `strategy/base.py Strategy` (`on_tick(tick, position)`,
  `reset(meta)`).
- `backtesting/real_backtester.py`: `run(strategy, episodes, execution)`, `PolicyStrategy`, `walk_forward_folds`.
  `backtesting/execution.py ExecutionModel(name, latency_s, bet_delay_s, commission, spread_ticks_basic,
  max_volume_frac)`, `preset(name)` (L66).
- Real ticks need match state for an in-play model; use the S36 aligned corpus (`data/real/aligned`, loader in
  `scripts/align_ball_by_ball.py`) when present. Pre-match fields come from `pre_match_live` with `as_of` = match date,
  attached once per episode.
- Gender for live LotusBook matches: team names ending in `W` / `Women` → female (`scraper/match_filter.py
  _strip_womens_suffix`). Team-name normaliser: pass `scripts/cricsheet_harmonizer.canonicalize_team` from the caller
  (do not import the harmonizer into `strategy/`).

## Owned files

- `backtesting/execution.py` (presets only)
- `strategy/runner.py` (`live_execution` only)
- `scripts/run_real_backtest.py` (`build_strategy` and episode enrichment)
- `virtual_trading/live_trading_loop.py` (`_get_runner`, `_handle_match_result`, and where pre-match fields are attached
  to the strategy tick)
- New: `strategy/live_context.py` (ratings loading/caching and `pm_*` attachment)
- New: `tests/unit/test_s43_integration.py`

## Tasks

1. **One manual execution model.** `ExecutionModel.preset("manual")` = `latency_s=5.0` (scrape and processing on
   historical exchange timestamps), `bet_delay_s=60.0` (a person placing the bet). `live_execution()` = the same
   `bet_delay_s` with `latency_s=0` (live ticks are already as late as the scrape made them). Document this in both
   docstrings, and add a test that both give a fill at decision + 60 s on the same tick stream.
2. **Backtest the live object.** `build_strategy("value", ...)` fits `FairValueModel(family, version=<--model-version,
   default v2>)` on the fold's training matches and returns `ValueStrategy(model)` directly (no `PolicyStrategy`). For
   v2, enrich every episode's ticks with `pm_*` from `pre_match_live` as of the match date (ratings built once per run).
   Keep `--model-version v1` available.
3. **`strategy/live_context.py`:**
   - `load_ratings(data_root=Path("data"), cache=Path("data/ratings/cricket_ratings.joblib"))` builds `CricketRatings`
     for the t20 and odi families, or loads the cache when its source fingerprint (file count plus newest mtime of the
     Cricsheet dirs) matches;
   - `attach_prematch(tick, ratings, *, normalize_team) -> tick` adds `pm_*` once per (match, day) using
     `pre_match_live`.
4. **Live loop.** In `_get_runner`, load ratings with `asyncio.to_thread(load_ratings)` alongside the fair models.
   Before each strategy tick, call `attach_prematch`. In `_handle_match_result`, call `await runner.finish(match_id)`
   when a runner exists.
5. **Install the v2 models (local artifacts, not committed).** Run, in the background, with logs under `logs/s43_*.log`:
   `python scripts/train_fair_value.py --family t20 --version v2` and the same with `--family odi`, writing their final
   models to `models/fair_value/<family>_v2_all.joblib` (use the script's own output option; check `--help`). Report the
   pooled walk-forward Brier for each family against the v1 baseline, copied from the JSON reports.

## Success criteria

- [ ] Backtest and live give an identical fill time and price for the same signal on the same ticks (test).
- [ ] `build_strategy("value")` returns a `ValueStrategy` whose model has `version == "v2"` and whose ticks carry
      `pm_*` (test on a small fixture with a fake ratings object).
- [ ] With ratings attached, a live strategy tick for two known teams has a non-zero `pm_elo_home - pm_elo_away`; for
      unknown teams it is neutral (test).
- [ ] `_handle_match_result` calls `runner.finish` (test with a fake runner).
- [ ] `models/fair_value/t20_v2_all.joblib` and `odi_v2_all.joblib` exist and load with `FairValueModel.load`; pooled
      Brier figures reported.
- [ ] Baseline suite passes (537 at `1143d94`).

## Verification

```bash
python -m pytest tests/unit/test_s43_integration.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Real-money anything. Changing the v2 model design (S38). Constants and risk files (S42).
