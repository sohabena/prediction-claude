# S12 — Live decision cadence matches training cadence

**Priority:** P0 · **Tier:** sonnet · **Depends on:** none (S11 recommended first) · **Wave:** 5
**Conflicts with:** none · **Publishes/consumes:** consumes contract `decision tick` (README)

## Story

As the operator, I need the live policy to make decisions at the same points in the match as it did in
training (ball events, over boundaries), so that it does not make roughly 20× more decisions per match
than it was trained for, and so that its bet-count and cooldown behaviour carries over.

## Background (what is wrong today)

- Training episodes contain one tick per over start, over end, boundary or wicket
  (`scripts/prepare_specialist_datasets.py:265-276`), about 2–4 decisions per over.
- Live: `virtual_trading/live_trading_loop.py:309-420` `_handle_match_event` runs feature compute and
  `agent.predict` on **every** scraped tick (~3 s, `shared/config.py:67`). Between two balls the
  policy is queried about 10–30 times on almost identical states. Each query can open a bet, until
  `MAX_BETS_PER_MATCH=25` (`shared/constants.py:120`) or the cooldown stops it.
- `live_trading_loop.py:404` falls back to `confidence = 0.5` when the agent has no distribution. That is a
  fabricated number, which violates rule 2.

## Context pack

- `virtual_trading/live_trading_loop.py` (794 lines):
  - `class LiveVirtualTradingLoop` L48;
  - `__init__` L59-93 (holds `self._feature_pipeline`, `self._latest_events`, `self._engine`,
    `self._risk_manager`);
  - `start()` L94-157 (S10 stream read loop: do not change);
  - `_handle_match_event(data)` L309-420. The flow is:
    1. parse `OddsEvent`;
    2. `_latest_events` update L321;
    3. gates: live L324, completed L329, watched L334;
    4. `_resolve_context` L338;
    5. `compute` L340;
    6. `feature_store.store` L348;
    7. zoo routing L353-375;
    8. `predict` L382;
    9. `propose`, `risk.check`, `_schedule_placement` L406-418.
  - `_build_position_state` L542.
- `MatchContext` (`shared/schemas.py`) fields used: `score`, `wickets`, `overs`, `innings`. `OddsEvent` has
  `match_id, timestamp, back_home, lay_home, back_away, lay_away, is_live`.
- Delayed fills read `self._latest_events`, so that update must stay on **every** tick, before the gate.
- Contract `decision tick` (README): a tick is a decision tick when any of these hold:
  - it is the first tick for the match;
  - `innings` changed;
  - `wickets` increased;
  - `floor(overs)` changed (over boundary);
  - `score` rose by ≥ 4 since the last decision tick;
  - no decision tick for `max_quiet_seconds` (default 180 s; covers missing context).
  Without context, fall back to the time rule alone.
- Feature history: give the pipeline **only decision ticks**, so its history matches training. S11 makes the
  history tick-count based, so this is consistent.

## Owned files

- `virtual_trading/live_trading_loop.py` (`_handle_match_event` and `__init__` only)
- New: `virtual_trading/decision_gate.py`
- New: `tests/unit/test_s12_decision_gate.py`

## Tasks

1. `virtual_trading/decision_gate.py`: `class DecisionGate(max_quiet_seconds=180.0)` with
   `is_decision_tick(event: OddsEvent, context: MatchContext | None) -> bool` and `reset(match_id)`. It keeps
   per-match state. It is pure: no I/O.
2. In `_handle_match_event`, after the `_latest_events` update and the live/completed/watched gates, and after
   resolving context, return early unless `self._decision_gate.is_decision_tick(event, context)` is true.
   Log `decision_tick_skipped` at debug level.
3. Call `DecisionGate.reset` wherever the loop already clears per-match state on a match result (grep
   `clear_match` in this file).
4. Replace the `confidence = 0.5` fallback with `None`. Make sure `engine.propose(..., confidence=None)` is accepted
   (check `virtual_trading/engine.py:167` signature; if it requires a float, report it rather than editing engine.py).
5. Add a counter in the existing `tick_latency` log: `decision=True/False`.

## Success criteria

- [ ] Unit test: a 3 s-cadence replay of one over (with the score changing on 2 balls, 1 boundary and an over boundary)
      produces exactly the decision ticks the contract defines.
- [ ] Unit test with a fake agent: for 200 ticks in one over with no state change, `predict` is called at most twice.
- [ ] Non-decision ticks still update `_latest_events` (a test asserts the delayed fill sees the newest price).
- [ ] `grep -n "0\.5" virtual_trading/live_trading_loop.py` shows no confidence fallback.
- [ ] Baseline suite passes.

## Verification

```bash
python -m pytest tests/unit/test_s12_decision_gate.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Stream consumer changes (S10, done).
- Retraining at live cadence (backlog).
