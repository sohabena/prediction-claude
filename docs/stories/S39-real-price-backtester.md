# S39 — Real-price, event-driven backtester with conservative execution

**Priority:** P0 · **Tier:** sonnet · **Depends on:** S35 (S36 for state-dependent strategies) · **Wave:** 11
**Publishes:** contract `strategy interface` (README), also implemented by S40

## Story

As the strategy owner, I need to replay any strategy over real price histories with fills that are no better than
we would really get, walk-forward by season, so that a positive result can be trusted.

## Background

- `rl/environment.py` `CricketBettingEnv` (S03) fills `fill_delay_ticks` later at tick prices. That is designed for
  synthetic ticks every 45–90 s, with back/lay always present. Real BASIC data has 1-minute `ltp` only; ADVANCED/PRO
  have ladders at sub-second resolution. A separate, event-driven backtester is cleaner than bending the RL env.
- Execution realities to model:
  - our information latency (scrape 3 s plus processing);
  - Betfair's in-play bet delay (~5 s);
  - crossing the spread;
  - liquidity;
  - commission on **net market winnings** (base 2–5% depending on account; make it configurable, default 5% to be
    conservative).

## Context pack

- Corpus: contract `real tick` (README, S35); state fields added by S36 when aligned.
- Exchange maths: README shared pack (back win `S(o−1)`, lay liability `S(o−1)`, green-up, commission on net positive
  market P&L). The tick ladder is `rl/environment.py:67 betfair_tick_size` / `:75 shift_price`; import, don't copy.
- Metrics: `shared/metrics.py` (S15): `clv_prob(side, entry, close)`, `sharpe`, `max_drawdown`, `per_match_pnl`,
  `profit_factor`. Bootstrap: `backtesting/report.py bootstrap_ci`.
- Existing policies to wrap: `backtesting/baselines.py` (`HoldPolicy`, `RandomPolicy`), `strategy/value_policy.py
  ValuePolicy` (S14; `predict(obs, tick, env)`, `win_prob`).
- Risk caps: `shared/constants.py` (`PER_MATCH_BUDGET`, liability limits used by `virtual_trading/engine.py`).

## Owned files

- New: `backtesting/real_backtester.py`
- New: `backtesting/execution.py`
- New: `strategy/base.py` (the `strategy interface` contract)
- New: `scripts/run_real_backtest.py`
- New: `tests/unit/test_s39_real_backtester.py`

## Tasks

1. `strategy/base.py`: `class Strategy(Protocol)` with
   `on_tick(tick: dict, position: Position) -> list[OrderIntent]` and `reset(match_meta_without_result)`.
   `OrderIntent(side, selection, price_limit, stake, reason)`. Position holds net P&L if home or away wins, open
   orders and matched bets. Adapters for `HoldPolicy`, `RandomPolicy` and `ValuePolicy` live in
   `backtesting/real_backtester.py`.
2. `backtesting/execution.py` `ExecutionModel(latency_s=8.0, bet_delay_s=5.0, commission=0.05, spread_ticks_basic=1,
   max_volume_frac=0.05)`, with presets `ExecutionModel.preset("manual")` (latency 60 s: a person reads the signal and
   places the bet by hand; this is the **default** for every report, by operator decision 2026-11-02) and
   `preset("automated")` (latency 8 s, for comparison only):
   - The decision at tick t sees only ticks with `time ≤ t − latency`.
   - The order fills at the first tick at or after `t + bet_delay`, at the **opposite best** price if a ladder exists,
     else at `ltp` shifted by `spread_ticks_basic` ticks against us.
   - No fill if the price moved through the limit.
   - The stake is capped at `max_volume_frac × traded volume in the window`, when volume is known.
   - Commission applies on net market winnings at settlement. A draw settles both selections as losers.
3. `real_backtester.run(strategy, episodes, execution) -> dict`: per-match P&L, per-bet records (entry, fill, closing
   `ltp`, `clv_prob`), commission, and turnover.
4. Walk-forward driver `scripts/run_real_backtest.py --strategy value --family t20 [--execution manual|automated]`:
   - folds by season; the fair model per fold is trained only on earlier seasons (S38 artifacts, or training on the fly);
   - output `docs/reports/real_backtest_<strategy>_<family>.json` with per-fold and pooled mean P&L/match and 95% CI,
     CLV mean and CI, the hit rate, and segment breakdowns (phase, odds band, competition tier).
5. **R0 sanity, as a test and in the report:** random and hold policies on real prices. Random must lose roughly
   commission plus spread; flag the run if random's CI lower bound is > 0.
6. Market-behaviour probes (thesis 2), market-only and needing no state:
   - "fade the jump": after a |Δ logit| > k move in one minute, take the reverse position and exit after m minutes;
   - "drift": long-horizon momentum in Test/ODI markets.
   Report them the same way. These are hypotheses, not strategies, until they pass.

## Success criteria

- [ ] Hand-computed fixture: one back fill with latency and delay matches the expected price, stake and net P&L after
      commission.
- [ ] Look-ahead test: a strategy that peeks at a future tick raises (the backtester only hands out data ≤ t − latency).
- [ ] Random on a real-like fixture loses about the costs; the R0 flag works.
- [ ] Walk-forward never uses a test-season match to fit a model (asserted on fold metadata).
- [ ] Baseline suite passes.

## Verification

```bash
python -m pytest tests/unit/test_s39_real_backtester.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Live execution (S41). Changing `CricketBettingEnv`.

## Integration note (2026-11-02)

**CLV definition (binding, 2026-11-02):** always `shared.metrics.clv_prob` (probability points, side-aware). The reference price depends on when the bet was placed. **Pre-match bets** use the last price before the off. **In-play bets** use the same-side price **10 minutes after placement** (`scripts/audit_clv.py HORIZON_SECONDS`), because the price near match end restates the result. `clv_60s` is reported as an execution check for manual latency.
