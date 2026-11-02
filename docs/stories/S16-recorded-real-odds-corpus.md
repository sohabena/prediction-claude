# S16 — Recorded real-odds corpus: keep and replay the only real market data we have

**Priority:** P0 · **Tier:** sonnet · **Depends on:** none · **Wave:** 5

## Story

As the strategy owner, I need every live match the scraper records (real LotusBook prices, plus the
score and the result) kept permanently and replayable through the same environment and evaluator.
That is the only way to test whether any policy has a real edge.

## Background (what is wrong today)

- Every training and evaluation episode is synthetic (Cricsheet ball-by-ball with modelled prices). No
  Betfair historical files exist in the repo (`find . -name "*.bz2"` finds nothing), so
  `scripts/import_betfair_data.py` has never been fed.
- The scraper already stores **real** prices in `odds_ticks` and the score state in `match_context`, and
  `match_results` holds winners plus closing prices. But:
  - `odds_ticks` has a 90-day retention policy (`backend/db_init.py:138-145`). Recorded live matches, the only
    real market data the project owns, are deleted after 90 days.
  - Nothing exports them into the corpus shape the env and evaluator consume, so no backtest has ever run on a
    real price.
- Without this, S13's null test and S14's value policy can only be checked against a simulator.

## Context pack

- `odds_ticks` (`backend/models/odds.py:16-57`):
  - PK `(time, match_id, tick_seq)`;
  - columns `team_home, team_away, competition, back_home, lay_home, back_draw, lay_draw, back_away, lay_away,
    volume_back_home, volume_lay_home, volume_back_away, volume_lay_away, implied_prob_home, implied_prob_away,
    overround, score, wickets, overs, innings, is_live, source (default "lotusbook"), scrape_latency_ms`.
  - `odds_ticks_historical` (L63+) has the same shape and no retention.
- `match_context` (`backend/models/match.py:16-31`): `time, match_id, is_live, score, wickets, overs, run_rate,
  req_run_rate, innings, balls_remaining, batting_team, bowling_team, status, match_format`.
- `match_results` (`backend/models/results.py:17-33`): `match_id` PK, `completed_at, winner, loser, result_type,
  margin, team_home, team_away, source, closing_back_home, closing_back_away, closing_lay_home, closing_lay_away`.
- Corpus tick shape the env expects (README shared pack, "tick and corpus shapes"): a list of episodes, each a list of
  dicts with `time` (ISO), `match_id`, teams, `competition`, `match_format`, `gender`, `specialist_key`, the four prices,
  `implied_prob_*`, `overround`, `is_live`, `score`, `wickets`, `overs`, `run_rate`, `req_run_rate`, `innings`,
  `balls_remaining`, `batting_team`, `bowling_team`, `status`, `volume_*`. The settlement keys `winner`, `loser`,
  `winner_is_home` are on the **last** tick or in `_meta` (match S01: see `SETTLEMENT_ONLY_KEYS`
  `scripts/prepare_specialist_datasets.py:31`).
- Specialist routing: `rl/model_zoo.py:154 ModelZoo.resolve_specialist_key(format_name, event_name, match_name,
  team_home, team_away)`. Use it to fill `specialist_key`.
- DB access pattern for scripts: `from backend.database import get_session` (async). See
  `scripts/sync_specialists_to_db.py` for an example script. The DB may be down in tests: unit-test the
  transformation on in-memory rows, and make the DB part a thin layer.
- Use the decision-tick contract (README, S12) to optionally down-sample to training cadence (`--cadence decision|raw`).

## Owned files

- New: `scripts/export_recorded_corpus.py`
- New: `data/recorded/README.md` (format and provenance note only; no data files committed)
- New: `tests/unit/test_s16_recorded_corpus.py`

## Tasks

1. Pure function `build_episode(odds_rows, context_rows, result_row, *, cadence) -> list[dict] | None`:
   - as-of join of each odds row to the latest `match_context` row at or before its time (never after: that would be
     look-ahead);
   - drop suspended rows (any price None);
   - require a result;
   - set `source="recorded_lotusbook"` and `market_model="real"`;
   - put the result only in `_meta` / the settlement keys.
2. `main`: for every match in `match_results` with ticks in `odds_ticks` **or** `odds_ticks_historical`, write
   `data/recorded/<specialist_key>/<match_id>.json.gz`. Make it idempotent: skip files that exist unless `--force`.
   Write a manifest `data/recorded/manifest.json` (match_id, key, n_ticks, first/last time, sha1).
3. `--min-ticks 50` and `--min-coverage 0.6` (the fraction of the innings span covered by ticks). Matches below either
   threshold are listed in the manifest as rejected, with a reason.
4. Loader helper `load_recorded(key) -> list[episode]` in the same script, importable by the evaluator later.
5. Print the recorded match count per key. This is the sample size real-edge claims rest on; say clearly when it is
   below 30.

## Success criteria

- [ ] Unit test: the as-of join never attaches a context row with `time` > the odds row time.
- [ ] Unit test: the produced episode runs through `CricketBettingEnv(..., settlement_mode="real",
      episode_sampling="sequential")` with HOLD to termination, and settles using the recorded winner.
- [ ] Suspended rows are dropped; a match with no result is rejected with reason `no_result`.
- [ ] Running the export twice without `--force` writes nothing the second time.
- [ ] Baseline suite passes.

## Verification

```bash
python -m pytest tests/unit/test_s16_recorded_corpus.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Changing retention policies (operator decision; listed in the README backlog). Running the exporter before each
  90-day window closes is the mitigation, so schedule it weekly in the S07 sync job later.
- Betfair historical data purchase or import.
