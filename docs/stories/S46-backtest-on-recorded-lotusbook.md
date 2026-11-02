# S46 — Backtest on our own LotusBook recordings (real prices plus true score timestamps)

**Priority:** P0 · **Tier:** haiku · **Depends on:** S37, S39, S43 committed · **Wave:** 15

## Story

As the strategy owner without access to Betfair data, I need every match our recorder captures to become a
backtestable episode in the same `real tick` format, carrying the score state as it was really shown. Then the
real-price validation ladder (R0–R2) runs on data we own, and its evidence grows every day the recorder runs.

## Background

- The recorder (S37) stores LotusBook prices in `odds_ticks` (back/lay home/away; volume when shown) and the live
  score in `match_context` (time, score, wickets, overs, innings, run_rate, req_run_rate, balls_remaining,
  batting_team). Both carry **real timestamps**, so no ball-time estimation (S36) is needed. The match result is in
  `match_results`.
- `scripts/export_recorded_corpus.py` (S16/S37):
  - `build_episode_checked(odds_rows, context_rows, result_row, *, cadence)` L120 does an as-of join of context onto
    prices (never forward, `_asof_context` L75) and returns a list of tick dicts with the result only in `_meta`;
  - `export_matches` L248 writes `data/recorded/<key>/<match_id>.json.gz` and a manifest; `load_recorded(key)` L231;
    `select_real_rows` L312 excludes simulated and synthetic rows; `_fetch_matches` L341 reads the DB.
  Its tick shape is the old corpus shape, not the README `real tick` contract.
- The backtester (S39/S43): `scripts/run_real_backtest.py` loads `scripts/build_real_price_corpus.load_real_corpus` and
  enriches with `pm_*`; it expects the `real tick` contract (README) and episode dicts (`match_id`, `family`,
  `competition`, `team_home`, `team_away`, `data_tier`, `start`, `ticks`, `competition_tier`).
- Tiny sample today (S36 found 1 real recorded match). The value of this story grows as the recorder runs.

## Owned files

- `scripts/export_recorded_corpus.py` (add a `--format real` output mode; keep the old mode)
- `scripts/run_real_backtest.py` (a `--corpus recorded|betfair` option, default `recorded`)
- New: `tests/unit/test_s46_recorded_backtest.py`

## Tasks

1. `to_real_episode(episode_ticks, meta) -> dict` converts an exported episode into the `real tick` contract:
   - `source="recorded_lotusbook"`, `data_tier="ladder_top"` (LotusBook shows best back and lay);
   - `ltp_*=None`;
   - `in_play` from `is_live`;
   - state fields copied as-is (they are already as-of joined);
   - `family` from `strategy.fair_value.family_of(match_format)`;
   - the result only in `_meta` on the first tick.
2. `--format real` writes `data/recorded_real/<family>/<match_id>.json.gz` plus a `manifest.json` in the same shape
   S35 writes, so `load_real_corpus`-style loading works. Add `load_recorded_real(family)`.
3. `run_real_backtest.py --corpus recorded` (default) loads that corpus. Seasons are calendar years, so with little data
   the walk-forward falls back to **expanding-window by match date** (train on earlier matches, test on the next block;
   `--min-train 30`). Say in the report when the sample is too small for any fold.
4. R0 on recorded data: random and hold must run and report even with very few matches.
5. Report: n matches per family, date range, and the share of ticks with score state.

## Success criteria

- [ ] A recorded fixture (fake rows) converts to `real tick` dicts; no tick except the first carries the result; state
      is never taken from a later context row (test).
- [ ] The backtest driver runs end to end on a 6-match recorded fixture (hold, random, value), producing a report
      with n and date range.
- [ ] The expanding-window split never trains on a match dated after any test match (test).
- [ ] Baseline suite passes.

## Verification

```bash
python -m pytest tests/unit/test_s46_recorded_backtest.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Writing to the database. New markets (S47).
