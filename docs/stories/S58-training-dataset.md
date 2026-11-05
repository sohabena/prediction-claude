# S58 — Training dataset from LotusBook recordings

**Priority:** P0 · **Tier:** haiku · **Depends on:** S50–S57 · **Wave:** 17A · **Plan:** docs/architecture/training-strategy.md §3

## Story

As the person training and evaluating models, I need every recorded match turned into a versioned, tidy dataset of
match facts and price snapshots, linked to its Cricsheet match, so every model and backtest reads the same rows.

## Background (2026-11-03)

- Prices: view `v_match_odds` (match_id, time, back_home, lay_home, back_away, lay_away, source `listing` or
  `match_page`) merges `odds_ticks` (listing) and `market_ticks` (`market_type='match_odds'`, one row per runner).
  Registry: `match_training_status` (team_home, team_away, competition, match_start_time UTC, is_simulated).
  Results: `match_results` (winner, result_type win/tie/unresolved/..., source). Quarantined rows are already removed.
- `odds_ticks.is_live` marks in-play rows. Match-page rows are change-only; listing rows are change-only plus a 5-min
  live heartbeat.
- Cricsheet raw JSON lives in `data/raw/{ipl,odis,t20s,tests}/*.json`; team canonicalisation exists in
  `scripts/cricsheet_harmonizer.py` (`canonicalize_team`) and `shared/naming.py` (`canonical_team_name`).
- The existing exporter `scripts/export_recorded_corpus.py` reads `odds_ticks` only; do not change it.

## Owned files

- New package `training/__init__.py`, `training/dataset.py`, `scripts/build_training_dataset.py`
- New `tests/unit/test_s58_training_dataset.py`

## Tasks

1. `training/dataset.py`, pure functions over plain row dicts (tests need no DB):
   - `build_snapshots(price_rows, match_row, *, snapshot_offsets, inplay_every_s=120)` returns snapshot dicts at
     T-6h, T-60m, T-10m, T-0 (last price before `match_start_time`) and every 2 min in play. Each snapshot holds the
     as-of book: each price field from the most recent row at or before t that has it, within 15 min (older: None),
     `stale_s` per field, the takeable prices (back and lay per runner), `book_60s` (as-of book at t+60 s) and
     `book_10m` (as-of book at t+10 min) for latency-aware labels, `time_to_start_s`, `in_play`.
   - `match_record(match_row, result_row, cricsheet_link)` returns match facts incl. `home_won` (1/0; None for tie,
     unresolved or no result), `result_source`, `is_simulated`, `family` (t20/odi/test from competition and name;
     `unknown` allowed).
   - `link_cricsheet(match_row, cricsheet_index)` returns a Cricsheet match id or None: both canonical team names
     equal (either order, flag `reversed`) and date within ±1 day of `match_start_time`. Build the index from Cricsheet
     `info` (teams, dates, match_type, gender) in the script.
2. `scripts/build_training_dataset.py`: read the DB (read-only), build both tables, write
   `data/training/<YYYYMMDD-HHMM>/matches.jsonl`, `snapshots.jsonl` and `manifest.json` (row counts, matches with and
   without a result, with and without a Cricsheet link, simulated count, time window, git sha). `--dry-run` prints the
   counts only. Make sure `data/training/` is git-ignored.
3. Tests: the as-of book never looks forward; a listing back and a match-page lay merge into one book; a field older
   than 15 min is dropped; look-ahead books; tie or unresolved gives `home_won` None; Cricsheet link with reversed
   teams and ±1 day; SRL flagged.
4. Run `--dry-run` against the live DB and report the counts.

## Guardrails

Never write to the database in tests. Read-only SELECTs against the live DB are allowed for the dry run. Do not touch
Docker containers, do not apply migrations, do not commit. Outputs are data, never bets. Type hints and docstrings in
the repo's style; no new heavy dependencies (numpy, scipy, scikit-learn, pandas are available).
