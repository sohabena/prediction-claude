# S32 — Cross-match portfolio exposure limits and concurrent event circuit breakers

**Priority:** P0 · **Tier:** sonnet · **Depends on:** none · **Wave:** 11
**Conflicts with:** none (strictly touches risk management and portfolio accounting)

## Story

As the portfolio risk manager, I need cross-match exposure governance and correlated drawdown circuit breakers across all concurrent live matches, so that simultaneous losses during multi-match match days (e.g. 3 live WBBL / T20 matches occurring simultaneously) cannot breach overall portfolio safety boundaries.

## Background

- PHOENIX currently enforces per-match bankroll partitions ($100,000 budget cap per match with a 20% tolerance buffer).
- However, during busy tournament days (e.g. IPL double-headers, ICC World Cup matches, or simultaneous BBL/WBBL games), 3 to 5 matches can run at the same time.
- If all 5 matches experience simultaneous adverse swings, the aggregate portfolio could suffer a combined drawdown exceeding 30–40% if cross-match risk is unmonitored.
- We need institutional-grade portfolio risk controls:
  1. **Aggregate Exposure Ceiling:** Total open liability across *all active matches* cannot exceed 50% of the master bankroll ($500k on a $1M account).
  2. **Correlated Tournament Cap:** Max 30% aggregate exposure to a single league or format on any given day.
  3. **Global Portfolio Circuit Breaker:** If total portfolio daily loss hits $-10\%$, all active trading is frozen across all matches, pending bets are cancelled, and the system transitions to `HALTED`.

## Context Pack

- Risk Manager: `virtual_trading/risk_manager.py`
  - `RiskDecision`, `RiskManager.check(proposal)`
- Portfolio Accounting: `virtual_trading/portfolio.py`
  - `VirtualPortfolio.total_exposure`, `match_books()`
- Constants: `shared/constants.py`
  - `MAX_TOTAL_EXPOSURE_PERCENT = 0.50`
  - `MAX_DAILY_LOSS_PERCENT = 0.10`

## Owned Files

- `virtual_trading/risk_manager.py`
- `virtual_trading/portfolio.py`
- New: `tests/unit/test_s32_portfolio_risk.py`

## Tasks

- [ ] In `virtual_trading/portfolio.py`:
  - Implement `aggregate_open_liability() -> float` summing current worst-case liability across all active match books.
  - Implement `league_open_liability(league: str) -> float`.
- [ ] In `virtual_trading/risk_manager.py`:
  - In `check(proposal)`:
    - Verify `aggregate_open_liability + proposal.liability <= master_bankroll * MAX_TOTAL_EXPOSURE_PERCENT`.
    - Verify `league_open_liability <= master_bankroll * 0.30`.
    - If daily realized + unrealized loss $\le -10\%$, trip the global circuit breaker and reject with `GLOBAL_DAILY_LOSS_LIMIT`.
- [ ] Implement `tests/unit/test_s32_portfolio_risk.py`:
  - Test that 3 concurrent matches each taking 18% liability are allowed (total 54% with 20% tolerance buffer), but a 4th match attempting to add 15% is rejected.
  - Test that when daily loss reaches $-10\%$, all proposals across all matches are blocked.

## Verification

```bash
# Run cross-match portfolio risk unit tests
python -m pytest tests/unit/test_s32_portfolio_risk.py -v
```


## Executor notes (review 2026-11-02, binding; they override the text above where they differ)

- **Most of this already exists (S05).** `virtual_trading/risk_manager.py:57 RiskManager.check(proposal)` already enforces
  per-bet, per-match, per-team and **total** liability (`MAX_TOTAL_LIABILITY_PCT`), daily and weekly loss, drawdown, the
  circuit breaker and the hourly limit. Do **not** re-implement or duplicate those checks.
- Real names: the class is `PortfolioManager` (`virtual_trading/portfolio.py:90`), not `VirtualPortfolio`. Useful members:
  `match_books()` L229 (per-match (pnl_if_home, pnl_if_away) books), `worst_loss(book)` L85, `register_match(match_id,
  team_home, team_away)` L200, `match_teams` L204, `reserve`/`release` L208-218, `daily_start_balance` L171.
  `BetProposal` (L40) has no competition field.
- **Build only what is new:**
  1. A per-competition cap: extend `register_match(..., competition: str | None = None)` (keyword, default None), store
     it, and add `competition_open_liability(competition) -> float` (the sum of worst-case losses of that competition's
     match books). In `check`, reject a non-reducing proposal if it would push that above
     `MAX_COMPETITION_LIABILITY_PCT` (new constant 0.30, defined at the top of `risk_manager.py`, since you do not own
     constants.py) of the balance. Unknown competition = no competition cap.
  2. The daily breaker counts unrealised risk: `daily_loss` uses realised P&L **plus** the sum of current worst-case open
     losses (conservative). Liability-reducing proposals stay exempt.
- Operator decision 2026-11-02: bets are placed by hand from signals. "Halt" means no new signals or proposals; there
  are no exchange orders to cancel. Releasing open reservations is enough.
- Tests: the scenarios in the story, rewritten for these two features, plus a test that existing S05 behaviour is
  unchanged (rerun `tests/unit/test_budget_tolerance.py` and any `test_s05_*`).
- Baseline: `python -m pytest tests/unit tests/rl -q` → 373 passed. Do not commit. Do not edit files outside Owned files.
