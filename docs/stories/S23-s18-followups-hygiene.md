# S23 — S18 follow-ups: Test-corpus leakage audit, missing historical table, warning spam, fair-value file naming

**Priority:** P1 · **Tier:** sonnet · **Depends on:** S18 committed, S22 committed · **Wave:** 9
**Conflicts with:** S22 (shares market-builder files): run S23 after S22.

## Story

As the operator, I need the rebuild to run cleanly on every format, to be able to export recorded real odds, and to
produce unambiguous artifacts.

## Background (from the S18 report, `docs/reports/strategy-report-2026-11-02.md`)

1. **`test_men` fails the leakage audit:** max AUC delta +0.0492 against the 0.03 threshold
   (`scripts/audit_outcome_leakage.py`). Its edge audit passes. Likely cause: multi-day handling (draws, innings 3–4
   target, follow-on) in the price model or the progress term.
2. **Recorded odds are unavailable:** `scripts/export_recorded_corpus.py` exits 1 because the table
   `odds_ticks_historical` does not exist in the database. S08 defines it in `alembic/versions/002_timescale_hardening.py`
   and `backend/db_init.py`, but it was never applied here.
3. **Warning spam in training:** `FeaturePipeline` logs many `invalid_observation` warnings (max |x| about 100–145)
   because S11 switched the pipeline to raw features and the validity check still assumes the old normalised range.
4. **Fair-value file naming:** the evaluator's `value` policy reads `models/fair_value/<family>.joblib`, so the file for a
   family holds whichever key trained last. Per-key copies are `<family>_<key>.joblib`.

## Context pack

- `scripts/audit_outcome_leakage.py` (198 lines): `audit_matches(...)`, the threshold constant, and a `--corpus` CLI.
- `scripts/prepare_specialist_datasets.py` `parse_cricsheet_json`: the multi-day branch is `max_innings = 4 if is_multiday`,
  with `inn_target` for innings 3/4 around L238-246. `CricketWinProbModel` lives in `scripts/synthetic_market.py`; the
  progress denominators were an S01 fix for innings 2.
- `features/pipeline.py` (S11): find the check with `grep -n invalid_observation features/pipeline.py`.
  `features/data_quality.py` may hold range constants.
- `alembic/versions/002_timescale_hardening.py`, `backend/db_init.py`. Applying the migration is an operator action:
  **do not run it against any database.** Write the exact steps into the story report, and make
  `export_recorded_corpus.py` print a clear message when the table is missing and continue with `odds_ticks` only.
- `strategy/fair_value.py`, `scripts/train_fair_value.py`, `scripts/rebuild_all.py`, and the `value` branch in
  `evaluate_specialist_holdout` (S14) in `scripts/train_format_specialists.py`. Do not edit that file; resolve paths only
  through `strategy/fair_value.py` helpers that the dispatch already calls, or report the gap.

## Owned files

- `scripts/audit_outcome_leakage.py` (diagnostics only; do not loosen the 0.03 threshold)
- `scripts/synthetic_market.py`, `scripts/prepare_specialist_datasets.py`, `scripts/cricsheet_harmonizer.py` (only the multi-day fix)
- `features/pipeline.py` (the validity check only)
- `scripts/export_recorded_corpus.py`
- `scripts/train_fair_value.py`, `scripts/rebuild_all.py`
- `strategy/fair_value.py` (path helpers only)
- New: `tests/unit/test_s23_followups.py`

## Tasks

1. Reproduce the `test_men` failure on a regenerated sample of at least 100 matches. Print the per-feature AUC deltas, find
   the leaking feature, and fix its cause in the price model or tick builder. The threshold stays at 0.03.
2. `export_recorded_corpus.py`: detect a missing `odds_ticks_historical`, log it, and continue with `odds_ticks` only. If
   nothing is available, exit 0 with a "no recorded matches" summary instead of crashing.
3. `FeaturePipeline`: make the invalid-observation check range-aware (raw mode accepts the wide range, normalised mode
   keeps the tight one) and log at most once per match, not per tick. Do not change feature values.
4. Fair-value artifacts: write `<family>_<key>.joblib` and a small `<family>.json` pointer. The evaluator and
   `rebuild_all.py` resolve per key first and fall back to the family file.

## Success criteria

- [ ] `audit_outcome_leakage` passes on a regenerated `test_men` sample (at least 100 matches) with the 0.03 threshold.
- [ ] `export_recorded_corpus.py` with no historical table exits 0 and says why (test with a fake session).
- [ ] A test feeds raw-scale features to a raw pipeline and sees no `invalid_observation` warning, and still sees one for NaN.
- [ ] Two keys of the same family keep separate fair-value files, and each `value` evaluation uses its own.
- [ ] Baseline suite passes.

## Verification

```bash
python -m pytest tests/unit/test_s23_followups.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Applying database migrations.
- Retraining the non-family keys (rerun S18 after S22).
