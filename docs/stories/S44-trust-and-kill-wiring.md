# S44 — Wire the trust gate and kill switch into the live signal path

**Priority:** P0 (before acting on any signal) · **Tier:** haiku (operator request) · **Depends on:** S42, S43 committed · **Wave:** 14
**Conflicts with:** none (runs alone after S43)

## Story

As the person betting by hand, I need every signal I see to carry its strategy's trust label, and signals to stop
automatically when the kill switch trips. I also need one command to clear a suspension after I have looked at it.
Today S42's pieces exist but nothing calls them.

## Background (S42 open items, verified at `2b2bc53`)

1. `strategy/trust.py` `TrustedSignalBus` (labels signals, drops suspended ones) is not used. The live loop still builds
   a plain `SignalBus` (`virtual_trading/live_trading_loop.py _get_runner`).
2. `virtual_trading/kill_switch.py KillSwitch` is never constructed:
   - `RiskManager(..., kill_switch=None)` is the default (`virtual_trading/risk_manager.py`);
   - `KillSwitch.load(versions)` is never called at boot;
   - `evaluate_version` / `evaluate_feed` never run.
3. The nightly trust refresh (`rl/lifecycle/graduation_handler.py check_signal_version`, `TrustRegistry.refresh`) has no
   caller. The orchestrator's nightly hook is `rl/orchestrator.py _check_nightly_schedule` (S20 candidate picking).
4. **Missed signals are not persisted.** `strategy/runner.py` produces `PaperFill` outcomes (filled / missed / lapsed /
   rejected), but only fills reach `virtual_bets`. The trust criterion "missed-signal rate ≤ 40%"
   (`shared/constants.py TRUST_CRITERIA`) can therefore never be met, so every version stays unproven.
5. `scripts/audit_clv.py` (S33) mixes LotusBook and Betfair ticks (`TICKS_SQL` filters
   `source IN ('lotusbook','betfair')`). The binding CLV definition counts Betfair prices only.
6. No CLI to reset a kill (`KillSwitch.reset(scope, operator=...)` exists).

## Context pack

- `strategy/trust.py`: `TrustRegistry` (`trust_label(version)`, `trust_reason`, `refresh(..., missed_count=)`, `load`),
  `TrustedSignal`, `TrustedSignalBus(bus, registry, kill_switch)`. Read the module for exact constructor arguments.
- `virtual_trading/kill_switch.py`: `KillSwitch` (`load(versions)`, `evaluate_version(...)`, `evaluate_feed(...)`,
  `blocked(scope)`, `reset(scope, operator=...)`), Redis keys `kill:<scope>`, fail-closed on unreadable state.
- `rl/lifecycle/graduation_handler.py`: `load_signal_evidence(...)`, `check_signal_version(version, ...)`.
- `strategy/runner.py`: `StrategyRunner`, `PaperFill`, `TickOutcome`, `finish(match_id)`. `strategy/signals.py`:
  `Signal`, `SignalBus` (writes `logs/signals.jsonl`).
- `virtual_trading/live_trading_loop.py`: `_get_runner` (built by S40/S43), `_handle_match_event`, `_handle_match_result`.
- `rl/orchestrator.py`: `_check_nightly_schedule(now)`, `_graduation_candidate_version` (S20).
- Signal versions are `agent_version = "signal:<strategy>:<version>"` on paper bets.

## Owned files

- `virtual_trading/live_trading_loop.py` (runner construction and the periodic feed check only)
- `strategy/runner.py` (persisting non-fill outcomes only)
- `rl/orchestrator.py` (nightly trust refresh only)
- `scripts/audit_clv.py` (a `--source betfair|lotusbook|all` option, default `betfair`)
- New: `scripts/signals_admin.py`
- New: `tests/unit/test_s44_wiring.py`

## Tasks

1. **Live loop:** build one `KillSwitch` at runner construction and call `KillSwitch.load(known_versions)`. Pass it to
   `RiskManager` (keyword `kill_switch=`) and to a `TrustedSignalBus` wrapping the existing `SignalBus`. Every 60 s, run
   `evaluate_feed(...)` with the last tick time per live match.
2. **Outcome log:** `StrategyRunner` appends every non-fill outcome (missed, lapsed, rejected) to
   `logs/signal_outcomes.jsonl` (one JSON line: time, strategy_version, match_id, signal_id, outcome, reason). Add
   `count_outcomes(version, since) -> dict` for the trust refresh.
3. **Nightly:** in `_check_nightly_schedule`, for each signal version seen in the window, call
   `check_signal_version(...)` and `TrustRegistry.refresh(..., missed_count=...)` from the outcome log. Also run
   `KillSwitch.evaluate_version(...)`. Never change orchestrator state from this; only labels and suspensions change.
4. `scripts/audit_clv.py --source` (default `betfair`) filters `odds_ticks.source` in `TICKS_SQL`, keeping the S33
   behaviour available as `--source all`.
5. `scripts/signals_admin.py`:
   - `status` prints the trust label and reason per version, plus active kills;
   - `reset --scope <version|all> --operator <name>` clears a kill through `KillSwitch.reset`;
   - `show --last 20` prints recent signals with labels.
   Read-only except `reset`.

## Success criteria

- [ ] A signal published through the live runner carries a trust label; with a kill on its version it is not published
      (fakes).
- [ ] `RiskManager` built by the live loop has the kill switch (test asserts the attribute).
- [ ] A missed fill writes one outcome line, and `count_outcomes` counts it.
- [ ] The nightly hook calls `check_signal_version` once per version (fake handler) and changes no orchestrator state.
- [ ] `audit_clv --source betfair` excludes LotusBook ticks (fake session).
- [ ] `signals_admin reset` without `--operator` fails; with it, it calls `KillSwitch.reset`.
- [ ] Baseline suite passes.

## Verification

```bash
python -m pytest tests/unit/test_s44_wiring.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Calibrating the placeholder thresholds (`KILL_DAILY_LOSS`, `KILL_STALE_SECONDS`, `KILL_CLV_WINDOW_BETS`): operator
  decision after real paper data.
- Per-version blocking inside `RiskManager` (needs a `strategy_version` on `BetProposal`; the signal bus already
  enforces per-version suspension).
