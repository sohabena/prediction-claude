# S57 — Quarantine and block prices that do not belong to the match

**Priority:** P0 · **Tier:** haiku · **Depends on:** S50–S56 · **Wave:** 16D

## Story

As the person training on recorded prices I need every `odds_ticks` row to be a price of the match it is stored under.
Rows that are not must be moved to a quarantine table (kept, not deleted), blocked at recording time, and counted by
the monitor.

## Background (verified 2026-11-03 against the live DB)

Two defects, both from the listing page:

1. **Post-settlement reset.** `lb_36255271` Afghanistan v Zimbabwe: decisive from 16:00 to 16:36 UTC (Afghanistan lay
   1.01, Zimbabwe back 100–410; Afghanistan won by 7 wickets per Cricinfo). After a 14-minute gap, 16:50–16:59 rows show
   a fresh near-even market (2.04/2.14 v 1.88/1.96) under the same id. Melbourne Stars W v Perth Scorchers W
   (`lb_36262294`) and Oman v Netherlands (`lb_75878940`) also end on such rows (1.92/1.99/2.02/2.10).
2. **Cross-match copies.** At 63 identical timestamps `lb_75878940` has exactly the prices of `lb_36262294`
   (e.g. 5.6/1.14, 2.38/2.82/1.55/1.72). Two different matches cannot quote identical four-price books in the same
   scrape for minutes on end. Which side is the copy cannot be told afterwards, so both are quarantined.

`scraper/worker.py` now refuses odds outside the match's own row (defensive; did not reproduce the copy).

## Owned files

- New `scripts/quarantine_ticks.py` (CLI, `--dry-run` default, `--apply` to move rows; prints counts per rule/match)
- New `backend/models/odds_quarantine.py` + `backend/db_init.py` entry: table `odds_ticks_quarantine` = all columns of
  `odds_ticks` plus `reason TEXT NOT NULL`, `quarantined_at TIMESTAMPTZ NOT NULL DEFAULT now()`; plain table (not a
  hypertable); alembic `006_odds_ticks_quarantine.py` (not run)
- `scraper/manager.py`: recording-time guard only (task 3), in `_on_events` just before `_store_odds_tick`
- `scripts/data_quality_monitor.py` + `tests/unit/test_s55_data_quality.py`: new check `misattribution`
- New `tests/unit/test_s57_quarantine.py`

## Tasks

1. Rules (pure Python functions over lists of tick dicts so they are unit-testable; the CLI loads rows, applies them,
   and moves matches with one `INSERT ... SELECT` + `DELETE` per rule inside one transaction):
   - `post_settlement`: per match, find the first *decisive* tick (one side back ≤ 1.05, or lay ≤ 1.06, while the other
     side's back ≥ 15 or is null). Every later tick of that match that comes after a gap ≥ 5 min **and** is not
     decisive is quarantined, as is every tick after it.
   - `cross_match_copy`: two different match ids with the same `time` and identical non-null
     `(back_home, lay_home, back_away, lay_away)` → both rows.
2. Dry run against the live DB (read-only) and report the counts per rule and match in the final report.
3. Recording-time guard in the manager: keep `self._decided_at[match_id]` when a tick is decisive; a later non-decisive
   tick after a ≥ 5 min gap is not stored (log `post_settlement_tick_rejected`, count in `_quality_stats`). Two events in
   the same batch with identical non-null four-price books and different match ids: store neither, log
   `cross_match_copy_rejected`.
4. Monitor check `misattribution`: counts of rows in the window that the two rules would flag; fail if any.
5. Tests with fakes only: Afghanistan-shaped series; copy pair; nothing flagged for a normal match; guard rejects;
   monitor ok/fail.

## Guardrails

No DB writes except via `scripts/quarantine_ticks.py --apply`, which the agent must NOT run (orchestrator applies).
Read-only SELECTs are allowed for the dry run. Do not commit. Do not touch Docker.
