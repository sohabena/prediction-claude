# S05 — Risk limits, liability accounting and position sizing

**Priority:** P0 · **Depends on:** none · **Wave:** 1
**Publishes:** the engine/risk API that S06 wires into the live loop

## Story

As the owner of the bankroll, I need every bet to be checked against hard limits using
the **actual** stake and **liability** that will be placed, with working daily/weekly
breakers, so that no single bet or match can cause ruin.

## Background

- `virtual_trading/live_trading_loop.py:234` risk-checks `balance × 1 %/3 %`, but
  `virtual_trading/engine.py:236-245` then stakes `PER_MATCH_BUDGET × (5–35 %)`, where
  `PER_MATCH_BUDGET = 100_000` = the whole bankroll. One bet can be 35 % of bankroll
  (a lay at 3.0 → 70 % liability). The 20 % tolerance lifts the match ceiling to 120 %.
- "Fractional Kelly" (engine 233) uses the policy's softmax probability as "confidence";
  there is no win probability or odds term, so it is not Kelly.
  `TOLERANCE_MIN_CONFIDENCE = 0.75` is routinely exceeded by a peaked policy.
- Exposure is measured by stake everywhere (`risk_manager.py:62-73`).
- `PortfolioManager.reset_daily()` is never called → the daily limit is a lifetime limit.
  `MAX_WEEKLY_LOSS_PERCENT` is imported but never checked.
- `shared/constants.py` defines `MAX_BETS_PER_HOUR` twice (15 at line 38, 30 at line 123).
- Engine hedge detection (150-172) only considers existing BACK bets.
- Exposure is checked before the 5–15 s placement delay, so several in-flight bets can
  each pass the same check.
- No limit on exposure to the same team across concurrent matches.

## Context pack

- **Correction to the audit:** `PortfolioManager.can_place_bet` (`virtual_trading/portfolio.py:61-85`) already
  rejects stakes > 10 % of balance, so engine stakes above 10 % are **silently rejected** (most LG actions never fill)
  rather than placed. The real exposures: a ≤ 10 % *lay* can still carry 40–90 % liability, the risk manager is never
  told the real stake, and rejected LG bets are invisible. Fix all three.
- `virtual_trading/engine.py` (342 lines): `humanize_stake(raw)` L35; **`class VirtualBetEngine`** L65 — the API in
  this story is added to *this* class (the API block below uses the real name);
  `__init__(portfolio, slippage_pct=0.005, rejection_rate=0.05, per_match_budget=PER_MATCH_BUDGET)` L78-92 with
  per-match dicts `_match_bet_counts, _match_last_bet_time, _match_total_staked, _match_potential_payouts`;
  `execute_action(action, event, confidence=0.5) -> VirtualBet|None` L94-276 (random rejection L111, lay<1.10 and
  back>50 guards L120-138, gaussian slippage L141-143, hedge detection L146-172, count tolerance L174-196, cooldown
  `MIN_BET_INTERVAL_SECONDS` L198-202, budget tolerance L204-230, sizing L232-245, noise L248, cap/humanize L251-258,
  `portfolio.open_position(bet)` L268); `rebuild_state_from_db` L278 (SQL on `virtual_bets`); `clear_match` L308;
  `_resolve_action(action, event) -> (team, odds)` L315; `publish_action` L329; `publish_outcome` L339.
- `virtual_trading/portfolio.py` (175 lines): `PortfolioManager(initial_balance=100_000)`; properties `state`,
  `open_bets` (dict id→VirtualBet), `is_circuit_breaker_active`; `can_place_bet(stake)` L61; `open_position(bet)` L87
  (id = `f"{match_id}_{placed_at.timestamp()}"` → **collides** for same-timestamp bets: switch to uuid4);
  `close_position(bet_id, pnl)` L112 (circuit breaker set L141, never cleared); `get_drawdown` L156; `reset_daily` L162
  (sets `_daily_start_balance`, never called); `get_recent_win_rate(n)` L169.
- `virtual_trading/risk_manager.py` (120 lines): `RiskManager(portfolio)`; `check_all(proposed_stake, match_id) ->
  {"allowed", "violations"}` L44-102; `record_bet()` L104; `get_risk_summary()` L108.
- `shared/constants.py`: risk L32-39 (`MAX_BET_PERCENT=0.05, MAX_MATCH_EXPOSURE_PERCENT=0.20,
  MAX_TOTAL_EXPOSURE_PERCENT=0.50, MAX_DAILY_LOSS_PERCENT=0.10, MAX_WEEKLY_LOSS_PERCENT=0.20, MAX_DRAWDOWN_PERCENT=0.15,
  MAX_BETS_PER_HOUR=15, CIRCUIT_BREAKER_CONSECUTIVE=5`); stakes L50-51; anti-detection/budget L115-131
  (`BET_DELAY_*`, `STAKE_NOISE_PERCENT=0.20`, `MAX_BETS_PER_MATCH=25`, `BETS_TOLERANCE_PERCENT`,
  `MAX_BETS_WITH_TOLERANCE`, duplicate `MAX_BETS_PER_HOUR=30` L123, `MIN_BET_INTERVAL_SECONDS=15`,
  `STAKE_ROUND_BUCKETS`, `PER_MATCH_BUDGET=100_000` L128, `BUDGET_TOLERANCE_PERCENT`, `MAX_PER_MATCH_BUDGET`,
  `TOLERANCE_MIN_CONFIDENCE=0.75`).
- **Importers** of changed constants: `PER_MATCH_BUDGET`, `MAX_PER_MATCH_BUDGET`, `TOLERANCE_MIN_CONFIDENCE`,
  `MAX_BETS_WITH_TOLERANCE` → only `virtual_trading/engine.py` and `tests/unit/test_budget_tolerance.py`. grep any
  other constant before removing it.
- **Callers:** `virtual_trading/live_trading_loop.py:239,244` (S06 rewires later — keep `execute_action` and
  `check_all` working as shims) and `tests/unit/test_budget_tolerance.py` L43-93 (owned by you). You also own
  `test_budget_tolerance.py::test_env_bet_volume_tolerance` (L95+): **delete it** (S03 re-tests env tolerance).
- `tests/unit/test_settlement.py`, `tests/unit/test_portfolio.py`, `tests/integration/test_settlement_integration.py`
  use `PortfolioManager`; keep them green (only `test_portfolio.py` is owned — report others if they need changes).

## Owned files

- `virtual_trading/engine.py`
- `virtual_trading/risk_manager.py`
- `virtual_trading/portfolio.py`
- `shared/constants.py`
- `tests/unit/test_budget_tolerance.py`, `tests/unit/test_portfolio.py` (update)
- New: `tests/unit/test_s05_risk.py`

## API to deliver (S06 depends on this exact shape)

```python
@dataclass
class BetProposal:
    match_id: str
    action: BettingAction
    team: str
    side: Literal["back", "lay"]
    odds: float
    stake: float
    liability: float          # stake for back, stake*(odds-1) for lay
    reduces_liability: bool   # True if it lowers the match's worst-case loss
    confidence: float
    reservation_id: str

class VirtualBetEngine:          # existing class in virtual_trading/engine.py
    def propose(self, action: int, event: OddsEvent, confidence: float) -> Optional[BetProposal]: ...
    def commit(self, proposal: BetProposal, fill_odds: float | None = None) -> VirtualBet: ...
    def release(self, proposal: BetProposal) -> None: ...   # cancel a reservation
    # execute_action(...) stays as a thin wrapper: propose -> risk.check -> commit

class RiskManager:
    def check(self, proposal: BetProposal) -> RiskDecision: ...   # RiskDecision(allowed, violations)
    def check_all(self, proposed_stake, match_id) -> dict:        # keep, deprecated shim
```

## Tasks

1. **Constants** (`shared/constants.py`): remove the duplicate `MAX_BETS_PER_HOUR`
   (keep one value, 15, and say which in the report). Replace `PER_MATCH_BUDGET` (absolute)
   with `PER_MATCH_BUDGET_PCT = 0.10` of current bankroll, and add
   `MAX_BET_LIABILITY_PCT = 0.03`, `MAX_MATCH_LIABILITY_PCT = 0.10`,
   `MAX_TOTAL_LIABILITY_PCT = 0.30`, `MAX_TEAM_LIABILITY_PCT = 0.12`,
   `KELLY_FRACTION = 0.25`, `CIRCUIT_BREAKER_COOLDOWN_MINUTES = 60`.
   Keep `PER_MATCH_BUDGET` / `MAX_PER_MATCH_BUDGET` as deprecated aliases computed from the
   default bankroll only if something else imports them (`grep` first; report importers).
2. **Sizing** in `engine.propose`:
   - Base stake = `small/large fraction × match budget`, then size by **liability**
     (lay stake = target_liability / (odds−1)).
   - If a calibrated win probability `p` is supplied (`event`/kwarg), use fractional Kelly:
     `f = KELLY_FRACTION × max(0, (p·o − 1)/(o − 1))` for back (mirror for lay), clipped to
     `MAX_BET_LIABILITY_PCT`. Otherwise use fixed fractional sizing. Rename/remove the
     "Kelly" wording where it is not Kelly. Confidence may scale stake down only (≤ 1×).
   - Stake noise and `humanize_stake` are applied **before** the cap, never after.
3. **Tolerance buffer:** the +20 % bet-count and budget tolerance may be used **only** by
   proposals with `reduces_liability=True`. Remove the confidence-based unlock.
4. **Reservations:** `propose` reserves the liability against match/team/total limits
   immediately; `commit` converts it; `release` frees it. In-flight proposals therefore
   count for every subsequent check.
5. **RiskManager.check** enforces on liability, using portfolio state plus reservations:
   per-bet, per-match, per-team (both teams across all open matches), total, daily loss,
   weekly loss, drawdown, hourly rate, circuit breaker. Liability-reducing proposals are
   always allowed through exposure caps (but not through breakers that halt trading
   entirely: daily/weekly loss, drawdown, circuit breaker — these block everything except
   liability-reducing bets).
6. **Portfolio:** automatic UTC day/week rollover (`_maybe_roll_periods(now)` called from
   every mutating method) that resets `daily_pnl` / `weekly_pnl`; add `weekly_pnl`.
   Circuit breaker clears after `CIRCUIT_BREAKER_COOLDOWN_MINUTES` with no new losses.
   Track liability (not stake) in `total_exposure` and document the field meaning.
7. **Hedge detection:** compute `reduces_liability` from the match's per-outcome book
   (pnl if home wins / if away wins) before vs after the proposal — covers back and lay.

## Success criteria

- [ ] A proposal is never committed with liability > `MAX_BET_LIABILITY_PCT × bankroll`
      (property-style test over odds 1.1–50, both sides, confidence 0–1).
- [ ] The stake checked by `RiskManager.check` is identical to the stake committed (test
      asserts object equality of the proposal).
- [ ] Sum of committed liability on one match never exceeds `MAX_MATCH_LIABILITY_PCT`
      except via liability-reducing bets (test: 50 random proposals).
- [ ] Two proposals created back-to-back before either commits cannot jointly breach a cap.
- [ ] Same-team exposure across two concurrent matches is capped.
- [ ] Losing 10 % on day 1 halts new risk on day 1 and allows trading on day 2
      (inject clock); weekly 20 % loss halts until the next ISO week.
- [ ] Circuit breaker clears after cooldown.
- [ ] `grep -n "MAX_BETS_PER_HOUR" shared/constants.py` shows exactly one definition.
- [ ] Existing tolerance/portfolio tests updated and green.

## Verification

```bash
python -m pytest tests/unit/test_s05_risk.py tests/unit/test_budget_tolerance.py tests/unit/test_portfolio.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Editing `virtual_trading/live_trading_loop.py` (S06 wires the new API).
- RL environment sizing (S03).
