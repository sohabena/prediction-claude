# S40 — Signal engine: the backtested strategy emits manual-bet signals live (PPO as shadow)

**Priority:** P1 · **Tier:** sonnet · **Depends on:** S39 (strategy interface), S38 (fair model v2; v1 works meanwhile) · **Wave:** 12

> **Operator decision (2026-11-02): no automated betting.** The app gives signals; a person places bets by hand. Every
> signal is also tracked as a paper bet at the price a person could realistically get, so the signal's quality is
> measured (CLV, P&L) exactly as the backtest measured it.

## Story

As the person placing bets by hand, I need a clear, actionable signal: what to back or lay, the minimum acceptable
price, a suggested stake, why, and until when it is valid. It must come from the same strategy object that was
backtested, so I can trust it and act on it in under a minute.

## Background

- The live loop decides with PPO only (`virtual_trading/live_trading_loop.py:_handle_match_event`: zoo routing ~L353-375,
  `agent.predict`, `engine.propose(...)`, `_schedule_placement` L261 with a random 5–15 s "anti-detection" delay).
- `VirtualBetEngine.propose(action, event, confidence=None, win_prob=None)` (`virtual_trading/engine.py:167`) sizes by
  fractional Kelly when `win_prob` is given (`_target_liability` L139-165). Nothing passes `win_prob` today.
- A human needs roughly 30–90 s from alert to placed bet. That is the execution latency the backtest must assume
  (S39 `ExecutionModel` preset `manual`), and only edges that survive that long are worth signalling.

## Context pack

- Contract `strategy interface` (`strategy/base.py`, S39): `on_tick(tick, position) -> list[OrderIntent]`, `reset(meta)`;
  `OrderIntent(side, selection, price_limit, stake, reason)`.
- `strategy/value_policy.py` (S14): `ValuePolicy(fair_model, min_edge, commission, max_bets, green_up_at)`, `_edges` L53,
  `_green_up` L72, `predict` L90, `win_prob` L43.
- Live tick → strategy tick: build the dict from `OddsEvent` (`back_*`, `lay_*`, timestamp, `match_id`, teams) and
  `MatchContext` (score, wickets, overs, innings, target).
- Decision cadence: `virtual_trading/decision_gate.py` (S12).
- Delivery channels already present: Redis Pub/Sub for the UI websocket (`backend/routers/websocket_router.py`,
  `shared/redis_client.py publish_event`). Desktop and phone notification: no channel exists; add a generic webhook
  (`SIGNAL_WEBHOOK_URL`, optional; e.g. a Telegram bot or ntfy topic the operator configures). Never hard-code a URL.
- Paper tracking: `virtual_bets` via `engine.commit` and `bet_persister`; tag `agent_version = "signal:<strategy>:<version>"`.

## Owned files

- `strategy/value_policy.py` (implement the interface; keep `predict` for the evaluator)
- `strategy/ratings.py` (add `pre_match_live` only; see the S38 integration notes)
- `virtual_trading/engine.py` (the one `register_match` call only; see the wave-11 notes)
- New: `strategy/runner.py`, `strategy/signals.py`
- `virtual_trading/live_trading_loop.py` (decision section of `_handle_match_event`, `_schedule_placement` delay source)
- `shared/constants.py` (new `STRATEGY_*`, `SIGNAL_*`, `MANUAL_LATENCY_SECONDS` constants only)
- New: `tests/unit/test_s40_signal_engine.py`

## Tasks

1. `ValueStrategy(Strategy)`:
   - edge = `p·price − 1` net of commission and half spread;
   - signal only if the edge still clears `min_edge` at `price_limit` (the worst price we accept);
   - stake suggestion via fractional Kelly (`engine.propose(..., win_prob=p)`), capped by the risk limits and rounded to a
     human-friendly amount;
   - exit signals ("lay X at ≤ price to green up / cut") when the position's equity gain reaches `green_up_at × entry
     edge`, or when the edge reverses;
   - **no-signal windows:** within `n` seconds after a state change, when data is stale (`MAX_DATA_AGE_SECONDS`), and in
     the last overs of an innings if backtests show edges there don't survive manual latency.
2. `strategy/signals.py`: a `Signal` dataclass with `id, created_at, valid_until, match, selection, side, price_limit,
   suggested_stake, fair_prob, market_price, edge, reason, strategy_version, kind (entry|exit)`.
   `SignalBus.publish(signal)` sends it to Redis channel `signals` (UI), appends to `logs/signals.jsonl`, and posts to
   `SIGNAL_WEBHOOK_URL` if set (short text, one line per field). Dedupe so the same idea is not re-signalled every tick.
3. `strategy/runner.py`: `StrategyRunner(strategy, engine, risk, bus)`. For each intent it:
   - publishes a Signal;
   - records a **paper bet** filled at the first price at or after `created_at + MANUAL_LATENCY_SECONDS` (default 60) if
     that price is no worse than `price_limit`, else records "missed". Use the same fill logic as S39's
     `ExecutionModel`, so live and backtest agree.
4. Live loop: select via `PHOENIX_STRATEGY=value|ppo` (default `value`). PPO runs in shadow: its action is logged as
   `shadow_decision` and never signalled. Remove the random anti-detection delay from the default path; keep it only
   behind `PLACEMENT_DELAY_MODE=random`.
5. No code path places a real bet anywhere.

## Success criteria

- [ ] For the same tick sequence, the S39 backtest adapter and the live runner produce identical intents and identical
      paper fills (parity test).
- [ ] A signal carries every field above; `valid_until` > `created_at`; duplicates are suppressed.
- [ ] A paper fill uses the price `MANUAL_LATENCY_SECONDS` later; a price worse than `price_limit` gives "missed".
- [ ] With `PHOENIX_STRATEGY=value`, PPO actions are only logged.
- [ ] No signal is emitted inside a no-signal window.
- [ ] The webhook is called only when the env var is set (test with a fake transport).
- [ ] Baseline suite passes.

## Verification

```bash
python -m pytest tests/unit/test_s40_signal_engine.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Automated order placement (operator decision: not wanted).
- UI layout for signals (other set's S34, P2, adapted to show signals).

## Integration notes from wave 11 (binding)

- S32's per-competition liability cap (`virtual_trading/risk_manager.py MAX_COMPETITION_LIABILITY_PCT = 0.15`) only works if
  matches are registered with their competition. `virtual_trading/engine.py:212` calls
  `pf.register_match(match_id, home, away)` without it. In S40's runner path, pass `competition=event.competition`. You
  may edit **that one call** in `engine.py`.
- CLV is defined in `docs/architecture/profit-architecture.md` ("CLV definition (binding)"): pre-match bets use the
  off price, in-play bets the price 10 minutes after placement. Paper bets need no extra columns: `scripts/audit_clv.py`
  computes `clv_10m` from `odds_ticks`.
- S32 changed the daily breaker to realised loss only (open worst-case liability is bounded by the liability caps).

## Integration notes from S38 (binding)

- The fair model to use is v2 (`strategy/fair_value.py`, `version="v2"`, files resolved by `model_path(family, key, "v2")`;
  two-stage: logistic pre-match prior + monotonic in-play GBM). It reads pre-match fields `pm_elo_home`, `pm_elo_away`,
  `pm_xi_home`, `pm_xi_away`, `pm_toss_home`, `pm_toss_bat`, `pm_venue_chase_pct`, `pm_venue_n`, `pm_tier` from each
  tick. Missing fields fall back to neutral values (`prematch_features`), which degrades the model to state-only.
- Live ticks have none of these, and `strategy/ratings.py CricketRatings.pre_match(match_id, as_of)` needs a
  **Cricsheet** match id that a live match does not have yet. Add to `strategy/ratings.py` (you may edit **only** to add
  this method) `pre_match_live(team_home, team_away, family, gender, as_of, *, venue=None, city=None, players=None,
  toss_winner=None, toss_decision=None, tier=None) -> dict` returning the same `pm_*` keys. Use Elo by team name
  (normalised with `canonicalize_team(n) or n.strip()` semantics, passed in, not imported from the harmonizer).
  Use venue priors only when a venue is known (LotusBook gives none: use neutral) and XI strength only when players
  are known (else 0 difference). Compute once per match per day and cache it; never on every tick.
- Tests: a live lookup for two known teams returns the same Elo difference as `pre_match` for a historical Cricsheet
  match between them on the same date; unknown teams give neutral values and are logged once.
- `strategy/fair_value.py:family_of("ODM")` is fixed (odi). Gender for live matches: LotusBook names ending in
  `W`/`Women` mean female (see `scraper/match_filter.py _strip_womens_suffix`).
