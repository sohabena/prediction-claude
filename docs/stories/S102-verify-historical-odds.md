# S102 — Verify historical odds: clean, harmonised, complete

**Pillars:** 1 and 3 · **Priority:** P1 · **Tier:** haiku · **Depends on:** S98 · **Wave:** 23

## User story

As the operator, I want every historical-odds store checked against itself, against Cricsheet results and against
the other sources before any model is judged on it, so a bad file can never produce a false R1 result.

## Acceptance criteria

1. `training/historical_odds_verify.py` (pure): `verify(records_by_source, cricsheet_matches, *, aliases) -> dict`
   with one entry per check (`status` OK/WARN/FAIL, numbers, up to 50 examples) and an overall status. Checks, per
   source:
   - `schema`: `validate()` problem counts. FAIL on any `forbidden_bookmaker`; FAIL when more than 1% of records
     have `bad_price`; WARN on other problems.
   - `duplicates`: same (source, event, bookmaker, kind, snapshot time) twice. FAIL.
   - `range_consistency`: where open/min/max/close exist for one event and side, min ≤ open, close ≤ max. WARN with
     count.
   - `overround`: median and share outside 1.00 to 1.15 per kind (`max` kinds can be below 1: best-of prices; skip
     them). WARN when over 5% of records are outside.
   - `times`: starts in the future or before 2000, snapshots after the start. FAIL on a snapshot after the start
     (that would be look-ahead).
   - `cricsheet_link`: share of events linked via `historical_odds.link_detail` (Cricsheet index with the same team
     aliases as `scripts/build_training_dataset._team_alias`), per competition and season; WARN under 80%; the
     unlinked team names with counts (top 50), to grow the alias table.
   - `results`: for linked events with a stated winner and a decided Cricsheet result (`info.outcome.winner`), the
     agreement rate (orient with `reversed`). FAIL under 98%, listing the disagreements.
   - `calibration`: closing fair P(home) (`p_home_fair`, one close per event: the bookmaker with most events) against
     the Cricsheet result: log loss, Brier, 10-bin table, and the logistic calibration slope. WARN when the slope is
     outside 0.8 to 1.2 or fewer than 100 matches.
   - `cross_source`: events present in two sources (same Cricsheet id): median absolute difference of closing fair
     P(home); WARN over 0.03.
   - `coverage`: events per source × competition × season × format × gender.
2. Cricsheet loading in the module or script: read `info` (teams, dates, match_type, gender, event name, outcome)
   from `data/raw/*/*.json` the same way `scripts/build_training_dataset._load_cricsheet_entries` does (reuse it if it
   fits; add outcome and gender without changing that function's output for its current callers).
3. `scripts/verify_historical_odds.py [--root data/historical_odds] [--out reports/historical_odds]`: reads every
   store, writes `verify-<UTC date>/report.json` and `report.md` (plain English, a table per check, FAIL first), prints
   the summary, exit code 1 on FAIL, 0 otherwise. No database, no network.
4. Tests `tests/unit/test_s102_verify_historical_odds.py` with synthetic records and fake Cricsheet entries: each check
   passes on clean data and fires on its fault (Betfair record, 2% bad prices, duplicate, close above max, snapshot
   after start, low link rate with unlinked names listed, a wrong winner, a miscalibrated source, two sources 10
   points apart); the report files are written to `tmp_path`.
