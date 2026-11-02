# S15 — One definition of CLV, Sharpe and drawdown; a graduation gate that measures the live model

**Priority:** P0 · **Tier:** sonnet · **Depends on:** none · **Wave:** 5

## Story

As the person who decides when a model goes from virtual to real money, I need every metric
(CLV, Sharpe, drawdown, ROI) to be computed one way, side-aware, from the right model's bets,
so that "graduated" means the live model really showed an edge.

## Background (what is wrong today)

- **There are three CLV formulas:**
  - `virtual_trading/settlement.py:31-53` `compute_clv` = `(1/close)/(1/place) − 1`, ignoring side. For a LAY
    the sign is backwards, so a lay at 2.0 that closes at 1.8 (the lay got worse) reports +11%.
  - `backend/services/agent_service.py:131`: the same formula inline, also side-blind. Its home/away selection
    uses team-name string equality.
  - `scripts/train_format_specialists.py:453`: probability-difference CLV, side-aware (correct, but a different
    unit).
  Graduation reads `AVG(clv)` from the DB, so it averages mixed definitions.
- **The graduation query (`rl/lifecycle/graduation_handler.py:41-63`)** has no filter on `agent_version`:
  - It mixes every model version that bet in the last 30 days.
  - It would include S02 holdout rows (`agent_version LIKE 'holdout_eval:%'`) if their timestamps fell in the window.
- **Sharpe** is computed in three places: graduation_handler L93-97, `backtesting/backtester.py:60-68` and
  `backtesting/report.py:82-89`. All three:
  - use `sqrt(252)`, although cricket betting days are not trading days;
  - drop days with no settled bets, which inflates Sharpe;
  - need only 5 days.
- **Drawdown** in graduation_handler L100-103 is computed on cumulative daily *returns* with no zero days, and is
  compared to 0.15.
- **The `win_rate ≥ 0.55` criterion** (`shared/constants.py:56-65`) is meaningless for a back/lay trading book:
  laying a 1.3 favourite wins ~77% while losing money. It rewards the wrong behaviour.
- The S02 holdout evaluator already computes honest per-match metrics. Graduation does not reuse any of them.

## Context pack

- `virtual_trading/settlement.py` (187 lines):
  - `SettlementEngine.compute_clv(placement_odds, closing_odds)` static method, L31-53;
  - its only caller is L105 (`bet.clv = self.compute_clv(bet.odds, closing_odds)`). `bet.action` tells you the side;
    use `_BACK_ACTIONS`-style checks via `BettingAction` names (`name.startswith("BACK")`).
- `backend/services/agent_service.py` (149 lines): `settle_pending_bets()` L76-149; the CLV block is L118-133; row
  columns are read positionally (`row[9]` = team_home).
- `rl/lifecycle/graduation_handler.py` (135 lines): the check is L30-130 (SQL L41-63, Sharpe L83-97, drawdown L99-103,
  `self._graduation.evaluate(...)` L105-113, Redis status L116+).
- `rl/graduation.py` (177 lines): `GraduationEvaluator.evaluate(win_rate, roi, sharpe_ratio, max_drawdown,
  profitable_days, total_bets, avg_clv=0.0)` L72-171. It iterates `GRADUATION_CRITERIA`; `max_drawdown` is
  special-cased L115. Callers: graduation_handler L105 and `grep -rn "\.evaluate(win_rate" --include=*.py .` (tests).
- `shared/constants.py:56-66` `GRADUATION_CRITERIA` and `GRADUATION_REQUIRED_DAYS`. You own **only these lines** of
  constants.py.
- `backtesting/backtester.py` (335 lines): `BacktestResult.win_rate` L42, `roi` L50, `avg_clv` L55, `sharpe_ratio`
  L60-68, `max_drawdown` L71-78; bets get `"clv": None` at L312, so the backtest's avg_clv is always 0.
- `backtesting/report.py` (151 lines): `bootstrap_ci(values, statistic)` L25-80, `_compute_sharpe` L82-89.
- `virtual_bets` columns: `placed_at, match_id, action, team, odds, stake, confidence, settled_at, outcome,
  profit_loss, closing_odds, clv, agent_version, observation_hash, reward, id`. The live `agent_version` comes from
  `rl.model_zoo.get_model_zoo().get_agent_version(key)`.

## Owned files

- New: `shared/metrics.py`
- `virtual_trading/settlement.py`
- `backend/services/agent_service.py` (CLV block only)
- `rl/lifecycle/graduation_handler.py`
- `rl/graduation.py`
- `shared/constants.py` (`GRADUATION_CRITERIA` block only)
- `backtesting/backtester.py`, `backtesting/report.py`
- New: `tests/unit/test_s15_metrics.py`

## Tasks

1. `shared/metrics.py`, pure functions:
   - `clv_prob(side, entry_odds, closing_odds)`: `1/entry − 1/close` for BACK, `1/close − 1/entry` for LAY, in
     probability points. This is the canonical definition, the same as S02's.
   - `sharpe(per_period_pnl, periods_per_year=None)`: not annualised by default; reports n.
   - `daily_series(bets, start, end)`: fills zero-P&L days.
   - `max_drawdown(equity_curve) -> (abs, pct_of_peak)`.
   - `profit_factor`.
   - `per_match_pnl(bets)`.
2. Use `clv_prob` in `settlement.py` (keep `compute_clv` as a thin wrapper with a `side` kwarg, default BACK for
   compatibility) and in `agent_service.py`.
3. Graduation query:
   - filter `agent_version = :current_version` and `agent_version NOT LIKE 'holdout_eval:%'`;
   - compute daily P&L over **calendar days including zero days**;
   - Sharpe without annualisation;
   - drawdown on the equity curve (`starting_bankroll + cumsum(daily_pnl)`) as a fraction of peak.
   Get the current version from the zoo, or require it as a parameter of the check method.
4. Replace the `win_rate` criterion with:
   - `clv_mean > 0` **and** a CLV bootstrap 95% CI lower bound > 0 (from `backtesting/report.bootstrap_ci`);
   - `roi_on_turnover ≥ 0.02` (no longer 0.08);
   - `min_matches ≥ 30`.
   Keep `max_drawdown ≤ 0.15` and `bet_volume`. Update `GraduationEvaluator` so the criteria are data-driven from
   `GRADUATION_CRITERIA`, and keep the `evaluate(...)` keyword API backward compatible: accept and ignore `win_rate`.
5. The backtester computes CLV per bet with `clv_prob` against the episode's last tick, and uses `shared/metrics` for
   Sharpe and drawdown.

## Success criteria

- [ ] For a LAY at 2.0 closing at 1.8, CLV is negative; for a BACK at 2.0 closing at 1.8, it is positive (test).
- [ ] The graduation SQL excludes other versions and holdout rows. Test with a fake session that captures the SQL
      text and params, or extract the query into a function and test it.
- [ ] A series with 10 zero days and 5 profitable days gives a lower Sharpe than the same 5 days alone (test).
- [ ] `grep -rn "sqrt(252)" --include=*.py .` returns nothing outside tests.
- [ ] `GRADUATION_CRITERIA` has no `win_rate` entry; existing graduation tests pass or are updated (in your own test file).
- [ ] Baseline suite passes.

## Verification

```bash
python -m pytest tests/unit/test_s15_metrics.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- UI changes to display new fields.
- `scripts/train_format_specialists.py` (S02's evaluator is already side-aware).
