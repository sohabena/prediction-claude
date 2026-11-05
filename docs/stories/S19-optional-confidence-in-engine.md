# S19 — Engine accepts "no confidence": stop persisting a fabricated 0.5

**Priority:** P1 · **Tier:** sonnet · **Depends on:** S12 committed · **Wave:** 7
**Conflicts with:** none

## Story

As the person reading `virtual_bets`, I need `confidence` to be NULL when the policy could not report
a probability, so that no row carries an invented number and sizing doesn't treat a missing
confidence as 0.5.

## Background

- S12 removed the `confidence = 0.5` fallback in `virtual_trading/live_trading_loop.py` (`_handle_match_event`). When the
  agent has no distribution it now passes no `confidence` kwarg, so `VirtualBetEngine.propose` applies its own default of
  0.5 (`virtual_trading/engine.py:171`), which is then written to `BetProposal.confidence` (L266), the `VirtualBet`
  (L295) and the DB.
- `_target_liability` (L139-165) scales stake by `0.25 + 0.75 * clamp(confidence, 0, 1)` (L164). With `None` there
  should be no confidence scaling.

## Context pack

- `virtual_trading/engine.py` (403 lines):
  - `VirtualBetEngine` L91;
  - `_target_liability(..., confidence: float, win_prob)` L139-165;
  - `propose(action, event, confidence: float = 0.5, win_prob=None) -> Optional[BetProposal]` L167-272;
  - the `BetProposal(... confidence=confidence ...)` build is at L266;
  - `commit(proposal, fill_odds=None)` L273-307 (L295 `confidence=proposal.confidence`);
  - `execute_action(action, event, confidence: float = 0.5, win_prob=None)` L313-333 (calls `propose` at L321).
- `shared/schemas.py:164`: `VirtualBet.confidence: Optional[float] = None` already allows None.
  `BetProposal` is defined in engine.py or schemas.py (`grep -n "class BetProposal"`); make its `confidence` Optional.
- DB: `backend/models/bets.py:29` `confidence` is nullable. `virtual_trading/bet_persister.py` and
  `live_trading_loop.py:~478-486` write `bet.confidence`; check they do not coerce None to a number (`grep -n confidence`).
- Caller: `virtual_trading/live_trading_loop.py` (`self._engine.propose(action, event, confidence=confidence)` after
  `confidence = None` fallback). The call is in S12's handler; you may edit **only that call line** and its local
  variable handling.
- Existing tests that pass a confidence to `propose`: `grep -rn "propose(" tests | head`; keep them working.

## Owned files

- `virtual_trading/engine.py`
- `virtual_trading/live_trading_loop.py` (only the `propose` call in `_handle_match_event`)
- `virtual_trading/bet_persister.py` (only if it coerces None)
- New: `tests/unit/test_s19_optional_confidence.py`

## Tasks

1. Change `propose` and `execute_action` to `confidence: Optional[float] = None`, and `_target_liability` to accept
   Optional. `None` means no confidence scaling (factor 1.0). Note this changes sizing only for callers that relied on
   the old 0.5 default (factor 0.625). State that in the docstring.
2. Pass `None` straight through to `BetProposal` and `VirtualBet`.
3. In the live loop, pass `confidence=confidence` unconditionally (it may be None).
4. Make sure no persistence path turns None into a number.

## Success criteria

- [ ] `propose(action, event)` yields a proposal whose `confidence is None` and whose stake is the unscaled target
      (test compares against `confidence=1.0`).
- [ ] An engine-to-persister test shows a NULL confidence for such a bet (use the existing persister test pattern or a
      captured-params fake session).
- [ ] An explicit `confidence=0.8` sizes exactly as before this story (regression test with a fixed expected stake).
- [ ] Baseline suite passes.

## Verification

```bash
python -m pytest tests/unit/test_s19_optional_confidence.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Feeding `win_prob` into live sizing (backlog).
