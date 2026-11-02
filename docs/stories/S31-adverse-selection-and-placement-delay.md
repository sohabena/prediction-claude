# S31 — Real-world bet placement delay and adverse selection simulator

**Priority:** P0 · **Tier:** sonnet · **Depends on:** none · **Wave:** 11
**Conflicts with:** none (strictly touches virtual execution simulator and environment)

## Story

As the trading strategist, I need our virtual trading engine and gym environment to simulate the realistic 5–8 second bookmaker placement suspension delay and model adverse selection (rejecting fills if a wicket or boundary occurs before confirmation), so that virtual trading profits are not an artifact of instant-fill illusions.

## Background

- In real-world cricket betting on platforms like LotusBook, Betfair, or Bet365:
  1. A bet is not confirmed immediately. There is a mandatory **5 to 8-second countdown delay** ("Bet Waiting...").
  2. If a key event occurs during that window (a wicket falls or a boundary is struck), the bookmaker **suspends the market and rejects the pending bet**, or reprices it adversely.
  3. Fills only succeed if the market remains stable or drifts unfavourably for the bettor.
- Currently, our virtual trading engine ([`virtual_trading/live_trading_loop.py`](file:///c:/Users/sohai/Downloads/prediction-claude/virtual_trading/live_trading_loop.py#L424-L426)) schedules placement with a random delay, but the fill logic in [`virtual_trading/engine.py`](file:///c:/Users/sohai/Downloads/prediction-claude/virtual_trading/engine.py) does not reject bets if a ball/scorecard update arrived during that delay indicating a wicket.
- This creates **adverse selection leakage**: in paper trading, the agent might receive fills that would have been rejected by a real bookmaker.

### Implementation Logic

1. **Pending Queue:** When a trade is proposed at time $T$, place it in `_pending_proposals` with execution time $T + \Delta t$ ($\Delta t \in [5.0, 8.0]$ seconds).
2. **Adverse Event Detection:** When execution time arrives, inspect all match ticks that arrived in the interval $[T, T + \Delta t]$.
3. **Cancellation Rule:**
   - If a wicket fell ($\Delta \text{wickets} > 0$) or boundary was scored ($\Delta \text{score} \ge 4$) during the pending window, mark proposal as `REJECTED_MARKET_SUSPENDED`.
   - If odds shifted by more than 3 ticks against the bettor, mark as `REJECTED_PRICE_CHANGED`.
   - Only fill if the market state was calm or drifted within acceptable slippage tolerances.

## Context Pack

- Virtual Trading Loop: `virtual_trading/live_trading_loop.py`
  - `_schedule_placement(proposal, price, model_version)`
  - `_execute_placement(...)`
- Virtual Engine: `virtual_trading/engine.py`
- Gym Environment: `rl/environment.py` (add optional `adverse_selection=True` mode for evaluation)
- Schema: `shared/schemas.py` (`VirtualBet` with `rejection_reason`)

## Owned Files

- `virtual_trading/live_trading_loop.py`
- `virtual_trading/engine.py`
- `rl/environment.py`
- New: `tests/unit/test_s31_adverse_selection.py`

## Tasks

- [ ] In `virtual_trading/live_trading_loop.py`:
  - Enhance `_execute_placement()` to verify intervening match events during the pending delay.
  - Reject fill if `context.wickets` increased or `context.score` jumped by $\ge 4$ runs.
  - Log structured audit event `bet_rejected_adverse_selection` with reason (`wicket`, `boundary`, `price_drift`).
- [ ] In `virtual_trading/engine.py`:
  - Track fill rate metrics: `total_proposals`, `placed_bets`, `adverse_rejections`.
  - Expose `rejection_rate` in portfolio stats.
- [ ] In `rl/environment.py`:
  - In `step()`, implement delayed fill evaluation when `adverse_selection=True`.
- [ ] Implement `tests/unit/test_s31_adverse_selection.py`:
  - Test that a bet proposed 2 seconds before a wicket is rejected.
  - Test that a bet proposed during normal dot/single overs executes cleanly.

## Verification

```bash
# Run adverse selection unit tests
python -m pytest tests/unit/test_s31_adverse_selection.py -v
```

