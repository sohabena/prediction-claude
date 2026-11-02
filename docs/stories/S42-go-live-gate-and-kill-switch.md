# S42 — Signal trust gate (CLV vs the Betfair close) and signal kill switch

**Priority:** P1 · **Tier:** sonnet · **Depends on:** S40; S41 for the Betfair close (falls back to the LotusBook close, flagged) · **Wave:** 13
**Supersedes:** S21 · **Run after:** S32 (the other set; it also owns `virtual_trading/risk_manager.py`)

> **Operator decision (2026-11-02): no automated betting.** "Go-live" here means a strategy version's signals are marked
> **trusted** for manual real-money betting. The kill switch **stops emitting signals**; it has no orders to cancel.

## Story

As the person placing bets by hand, I need each signal labelled "unproven" or "trusted" according to that strategy
version's measured record against the sharp closing price, and signals to stop automatically when live evidence turns
against the strategy. Then I only risk money on signals with a real track record.

## Background

- `rl/graduation.py` keeps one shared consecutive-days counter (S21 issue). Its criteria (S15) are ROI, Sharpe (0.10/day
  placeholder), drawdown, volume, CLV mean and CI, and matches, all measured against the LotusBook mirror price.
- `rl/lifecycle/graduation_handler.py` (`check(agent_version)`, `GRADUATION_BETS_SQL`) and the orchestrator's candidate
  picking (S20) are keyed on PPO agent versions. Under v2, the unit is a **strategy version** (S40 tags paper bets
  `agent_version = "signal:<strategy>:<version>"`).

## Context pack

- `rl/graduation.py` (`GraduationEvaluator`, `evaluate(...)` keyword API, state file), `rl/lifecycle/graduation_handler.py`,
  `rl/orchestrator.py` (`_graduation_candidate_version`, S20), `shared/constants.py` `GRADUATION_CRITERIA` (data-driven
  `op` field, S15).
- `virtual_bets`: `agent_version`, `odds`, `closing_odds`, `clv`, `stake`, `profit_loss`, `settled_at`, `match_id`;
  "missed" signals (S40) count separately. `match_results.closing_*` carries a `source` (`betfair` after S41).
- Metrics: `shared/metrics.py` (`clv_prob`, `daily_series`, `max_drawdown`), `backtesting/report.py bootstrap_ci`.
- Signals: `strategy/signals.py` `Signal`, `SignalBus` (S40). Add a `trust` field via a wrapper; do not edit S40's file.
  If a field change in `Signal` is needed, report it.
- `virtual_trading/risk_manager.py` (`RiskManager.check(proposal)`); S32 adds portfolio limits there first.

## Owned files

- `rl/graduation.py`, `rl/lifecycle/graduation_handler.py`
- `shared/constants.py` (`GRADUATION_CRITERIA` block, new `KILL_*` constants)
- New: `virtual_trading/kill_switch.py`, `strategy/trust.py`
- `virtual_trading/risk_manager.py` (call the kill switch only)
- New: `tests/unit/test_s42_trust_and_kill.py`

## Tasks

1. Trust criteria (rung R3), per strategy version:
   - ≥ 300 settled paper bets from signals over ≥ 30 distinct real matches (simulated or synthetic excluded by source and
     name);
   - mean CLV per the binding definition: pre-match bets against the **Betfair** off price, in-play bets against the
     same-side Betfair price 10 minutes after placement (`clv_10m` from `scripts/audit_clv.py`, S33), with a bootstrap
     CI lower bound > 0. CLV measured on LotusBook prices is reported separately and does not count toward trust;
   - realised P&L/match CI not entirely below 0;
   - max drawdown ≤ 15% of the allocated bankroll;
   - a missed-signal rate ≤ 40% (otherwise signals are not actionable by hand).
   Drop the placeholder Sharpe criterion. The streak is per version and resets on change, counting a day once (absorbs S21).
2. `strategy/trust.py`: `trust_label(strategy_version) -> "unproven" | "trusted" | "suspended"` (cached, refreshed
   nightly), attached to every published signal through a wrapper around `SignalBus`.
3. `KillSwitch`. It suspends signals for a version, or globally, when any of these hold:
   - the daily paper loss limit is hit (`KILL_DAILY_LOSS`);
   - rolling CLV (same definition) over the last 100 paper bets is < 0 with its CI upper bound < 0;
   - the data is stale (no tick for `KILL_STALE_SECONDS` on a match with open paper positions).
   The state persists in Redis (`kill:<version>`) and needs a manual reset (CLI function), never an automatic one.
4. `RiskManager.check` consults the kill switch first.

## Success criteria

- [ ] A bet set with positive LotusBook-only CLV but no Betfair close stays "unproven", with reason "insufficient
      Betfair evidence".
- [ ] A version change resets the streak; the same day counts once.
- [ ] Each kill condition suspends signals in a test, and the suspension survives a restart (fake Redis).
- [ ] A published signal carries its trust label.
- [ ] Baseline suite passes.

## Verification

```bash
python -m pytest tests/unit/test_s42_trust_and_kill.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Any automated betting.
