# S20 — Graduation wiring per live model, and the last `sqrt(252)`

**Priority:** P1 · **Tier:** sonnet · **Depends on:** S15 committed · **Wave:** 7
**Conflicts with:** none

## Story

As the person promoting a model to real money, I need the nightly graduation check to evaluate each model that is
actually placing bets, and every Sharpe in the code base to use the same definition, so that "graduated" cannot be
decided on the wrong model's numbers.

## Background

- S15 made `GraduationHandler.check(agent_version=None)` filter `virtual_bets` by `agent_version`. With no argument
  it falls back to `get_model_zoo().get_agent_version("default")`
  (`rl/lifecycle/graduation_handler.py:83-90`). The caller, `rl/orchestrator.py:255`
  (`await self._graduation_handler.check()` in `_check_nightly_schedule`), passes nothing.
- Live bets are tagged with the **specialist's** version (`live_trading_loop.py`: `model_version = zoo.get_agent_version(spec_key)`,
  e.g. `ipl@<hash>`), not the `"default"` model's. So the check may find no rows, or only the default model's rows,
  and say "not ready" or judge the wrong model.
- `virtual_trading/shadow_trader.py:501` still returns `mean/std * np.sqrt(252)` over `self._daily_pnl` values (days
  with bets only). That is the old definition S15 removed everywhere else.
- Sharpe threshold: S15 set `GRADUATION_CRITERIA["sharpe_ratio"]` to 0.10 per day (non-annualised). This is a
  placeholder: keep it, but record it as such in the constants comment.

## Context pack

- `rl/orchestrator.py` (521 lines): `_check_nightly_schedule(now)` L243-270. `self._graduation_handler` is built at L107
  from `GraduationHandler(self._graduation_eval)`. The imports are at L30.
- `rl/lifecycle/graduation_handler.py` (≈200 lines after S15): `GraduationHandler` L76; `_current_version()` L83-90;
  `check(agent_version=None) -> bool` L92+. Its SQL is the module constant `GRADUATION_BETS_SQL`.
- `rl/model_zoo.py`: `get_model_zoo().list_specialists()` returns per-key info with `tradeable` and `reason` (S17), and
  `get_agent_version(key)`. Only tradeable keys can place bets.
- `shared/metrics.py` (S15): `sharpe(per_period_pnl, periods_per_year=None)`, `daily_series(bets, start, end)`.
- `virtual_trading/shadow_trader.py` (513 lines): the Sharpe function ends at L501; `_daily_pnl` is a dict
  day → pnl; `grep -n "_daily_pnl" virtual_trading/shadow_trader.py`.
- Graduation state in `rl/graduation.py` is a single counter (consecutive days). With several models, run the check per
  version but keep **one** gate for live money: the orchestrator graduates only if the version that is the
  designated live candidate passes. Use `settings`/constants only if a field already exists; otherwise take the version
  with the most settled bets in the window and log which was chosen.

## Owned files

- `rl/orchestrator.py` (only `_check_nightly_schedule` and a small helper)
- `virtual_trading/shadow_trader.py` (only the Sharpe function)
- `shared/constants.py` (comment on the Sharpe threshold only)
- New: `tests/unit/test_s20_graduation_wiring.py`

## Tasks

1. Add a helper `_graduation_candidate_version()` in the orchestrator: among versions that placed settled bets in the
   graduation window (add `GraduationHandler.versions_with_bets()` if needed, in a handler-owned query, or query via a
   small SQL in the orchestrator helper), choose the one with the most settled bets whose specialist key is
   tradeable. Log the choice. Return None if none.
2. Call `check(agent_version=candidate)`; if None, skip the check and log `graduation_no_candidate`.
3. Replace the shadow trader's Sharpe with `shared.metrics.sharpe` on a calendar-day series (zero days included), not
   annualised. Remove the `np.sqrt(252)`.
4. Comment the 0.10 threshold as a placeholder to be calibrated from S18's report.

## Success criteria

- [ ] With two versions in a fake DB (or fake helper), the orchestrator passes the one with more settled bets.
- [ ] With no candidate, `check` is not called and no state changes.
- [ ] `grep -rn "sqrt(252)" --include=*.py .` returns nothing outside tests.
- [ ] A shadow-trader Sharpe test shows zero-bet days lower the figure.
- [ ] Baseline suite passes.

## Verification

```bash
python -m pytest tests/unit/test_s20_graduation_wiring.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Choosing the Sharpe threshold from data (after S18).
- UI.
