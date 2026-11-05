# S27 — Fair-value probability integration and EV value gating in live trading loop

**Priority:** P0 · **Tier:** sonnet · **Depends on:** S14, S26 · **Wave:** 6
**Conflicts with:** none (wires `FairValueModel` into `live_trading_loop.py` and `virtual_trading/engine.py`)

## Story

As the risk manager, I need every bet proposed by the RL specialist in the live trading loop to be explicitly checked against our calibrated `FairValueModel`, rejecting any trade where calculated Expected Value (EV) fails to clear a minimum profit hurdle, so that the agent never places negative-EV bets based on black-box policy quirks.

## Background

- Currently, in [`virtual_trading/live_trading_loop.py`](file:///c:/Users/sohai/Downloads/prediction-claude/virtual_trading/live_trading_loop.py#L413), the engine calls:
  ```python
  proposal = self._engine.propose(action, event, confidence=confidence)
  ```
  without passing `win_prob`.
- Consequently, [`virtual_trading/engine.py`](file:///c:/Users/sohai/Downloads/prediction-claude/virtual_trading/engine.py#L162-L165) falls back to fixed fractional sizing (`budget * frac`) and performs **zero Expected Value verification**.
- In institutional sports trading, a trade must satisfy:
  $$\text{EV} = P_{\text{win}} \times \text{Odds} - 1.0 > \text{Hurdle} \quad (\text{typically } +2.5\% \text{ to clear spreads and commissions})$$
- In Story S14, we developed [`strategy/fair_value.py`](file:///c:/Users/sohai/Downloads/prediction-claude/strategy/fair_value.py) (`FairValueModel`), which produces an isotonically calibrated win probability from observable match state and format dynamics.
- This story hooks `FairValueModel` directly into the live decision path.

### Value Gating & Kelly Sizing Logic

1. At each tick, obtain $P_{\text{win}}$ from the format family's `FairValueModel` using current match state + `team_elo_diff`.
2. For BACK proposals on home:
   $$\text{Edge}_{\text{back}} = \frac{P_{\text{win}} \cdot O_{\text{back}} - 1.0}{O_{\text{back}} - 1.0}$$
3. For LAY proposals on home:
   $$\text{Edge}_{\text{lay}} = 1.0 - P_{\text{win}} \cdot O_{\text{lay}}$$
4. **Hurdle Gate:** If $\text{Edge} < \text{MIN\_EDGE\_HURDLE}$ (default $0.025$ or $2.5\%$), reject proposal immediately and log `bet_rejected_negative_ev`.
5. **Fractional Kelly Sizing:** When edge is positive, pass `win_prob` to `_target_liability` so stake scales proportionally with edge magnitude.

## Context Pack

- `strategy/fair_value.py`:
  - `FairValueModel.predict_home(tick_dict) -> float`
  - Models serialized in `models/fair_value/fair_value_<family>.joblib`
- `virtual_trading/live_trading_loop.py`:
  - Lines 380–425 (`handle_match_event`): where `agent.predict` is evaluated and `propose` is called.
- `virtual_trading/engine.py`:
  - Lines 139–170 (`_target_liability`): already has fractional Kelly logic when `win_prob` is provided!
- `shared/constants.py`:
  - Add `MIN_EDGE_HURDLE = 0.025` (2.5% edge threshold).

## Owned Files

- `virtual_trading/live_trading_loop.py`
- `virtual_trading/engine.py`
- `shared/constants.py` (only `MIN_EDGE_HURDLE`)
- New: `tests/unit/test_s27_ev_gating.py`

## Tasks

- [ ] Update `shared/constants.py`: add `MIN_EDGE_HURDLE = 0.025`.
- [ ] In `virtual_trading/live_trading_loop.py`:
  - Preload or lazy-load format family `FairValueModel` on startup.
  - In `handle_match_event()`, calculate `win_prob = self._fair_value_model.predict_home(tick_state)`.
  - Pass `win_prob=win_prob` into `self._engine.propose(action, event, confidence=confidence, win_prob=win_prob)`.
- [ ] In `virtual_trading/engine.py`:
  - Ensure `_target_liability` strictly enforces `edge >= MIN_EDGE_HURDLE`.
  - Log structured audit events when a bet is blocked due to insufficient EV.
- [ ] Implement `tests/unit/test_s27_ev_gating.py`:
  - Test that a bet proposal with negative EV (e.g. Back at 1.50 when $P_{\text{win}} = 0.60$, EV = $-0.10$) returns `None`.
  - Test that a bet proposal with strong positive EV (e.g. Back at 2.20 when $P_{\text{win}} = 0.55$, EV = $+0.21$) produces a properly sized proposal.
  - Test green-up hedging exemption: risk-reducing bets (`_reduces == True`) are permitted even if marginal entry edge is zero.

## Verification

```bash
# Run EV gating and sizing unit tests
python -m pytest tests/unit/test_s27_ev_gating.py -v
```

