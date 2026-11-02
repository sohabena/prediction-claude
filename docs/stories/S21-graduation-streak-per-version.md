# S21 — Graduation streak is tracked per model version

**Priority:** P1 (before any real-money step) · **Tier:** sonnet · **Depends on:** S20 committed · **Wave:** 8
**Conflicts with:** none

## Story

As the person promoting a model to real money, I need the consecutive-days graduation streak to belong to one model
version and reset when the candidate changes, so that a model cannot graduate on another model's record.

## Background

- `rl/graduation.py` `GraduationEvaluator` keeps one `consecutive_days` counter and persists it in one state file
  (`_load_state` L44, `_save_state` L56). `evaluate(...)` L72 increments or resets it.
- S20 picks the candidate version per night (`rl/orchestrator.py` `_graduation_candidate_version`). When the winner
  changes between nights, the streak carries over from the previous version.

## Context pack

- `rl/graduation.py` (≈177 lines): `GraduationEvaluator.__init__` L30; `_load_state`/`_save_state` L44-70; `evaluate(...)`
  L72-171 (keyword API incl. S15's `clv_ci_lower`, `n_matches`); `get_progress_pct` L173.
- `rl/lifecycle/graduation_handler.py`: `check(agent_version)` calls `self._graduation.evaluate(...)`; the Redis status
  it writes includes `consecutive_days`, `required_days`.
- Callers of `evaluate(`: `grep -rn "\.evaluate(" rl tests | grep -i gradu`; keep `agent_version` optional so they work.
- State file path is set in `__init__`; keep the format backward compatible (old files have no version).

## Owned files

- `rl/graduation.py`
- `rl/lifecycle/graduation_handler.py` (pass `agent_version` into `evaluate`)
- New: `tests/unit/test_s21_graduation_streak.py`

## Tasks

1. Store `{"agent_version": ..., "consecutive_days": ..., "last_date": ...}`. `evaluate(..., agent_version=None)`:
   if the stored version differs from the given one, reset the streak to 0 before evaluating, and log it.
2. A day counts once: evaluating twice on the same date must not increment twice (use `last_date`).
3. Old state files without a version load as version `None` and reset on the first versioned call.
4. The handler passes the version it evaluated.

## Success criteria

- [ ] Streak of 5 days on version A, then one call for version B → streak is 1 at most (0 if criteria fail), not 6.
- [ ] Two calls on the same date count once.
- [ ] An old-format state file loads without error.
- [ ] Baseline suite passes.

## Verification

```bash
python -m pytest tests/unit/test_s21_graduation_streak.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Choosing thresholds (after S18).
