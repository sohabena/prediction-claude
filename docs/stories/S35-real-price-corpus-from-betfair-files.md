# S35 — Real-price corpus from Betfair historical files

**Priority:** P0 · **Tier:** sonnet · **Depends on:** none (files must be downloaded by the operator to *run*; the code and tests use fixtures) · **Wave:** 10
**Conflicts with:** none. Does not edit `scripts/import_betfair_data.py` (owned by the other set's S28) · **Publishes:** contract `real tick` (README)

## Story

As the strategy owner, I need thousands of real cricket Match Odds price histories, each with its result, in one
provenance-tagged corpus, so that every strategy is judged on prices real people traded.

## Background

- `scraper/parsers/betfair_historical.py` (581 lines) already parses Betfair stream files (JSON/JSONL, .bz2, .gz) and CSV:
  - `BetfairHistoricalParser.parse_file` L81, `parse_stream_lines` L102-232 (reads `marketDefinition`, `rc` with `ltp`,
    `batb`, `batl`, `tv`; runner `status == "WINNER"` L170);
  - `_extract_stream_tick` L288, `_build_match_from_stream` L354, `_build_match_from_csv` L435;
  - `ParsedHistoricalMatch` L38-56 (match_id, teams, competition, start_time, completed_at, winner, loser,
    result_type, ticks, closing_* prices).
- `scraper/importers/betfair_importer.py` (431 lines) writes parsed matches **into the DB** and marks them
  `scrape_approved` / `training_status="approved"` (L154-175). It also has `generate_synthetic_match` (L287), which
  must never feed the real corpus.
- `scripts/import_betfair_data.py` (185 lines) is the CLI (`run_dry_run` L28, `run_import` L78).
- No Betfair files exist in the repo. The operator downloads them (BASIC tier = 1-minute last traded price; ADVANCED/PRO
  add the ladder and volume).
- Gotcha: BASIC files carry `ltp` only, with no `batb`/`batl`. The corpus must record which fields are real and which are
  missing, and must never invent back/lay prices; spreads are modelled in S39.

## Context pack

- Corpus destination: `data/real/betfair/<family>/<market_id>.json.gz` plus `data/real/betfair/manifest.json`. `data/` is
  gitignored, so commit nothing under it except README files (`git add -f`).
- Family classification: reuse `strategy/fair_value.py:25 family_of(match_format)` (t20 / odi / multiday) and
  `scraper/match_filter.py:68 MatchClassifier` (`classify`, `is_international_or_major`). Market names in Betfair files:
  event name `"A v B"`, market type `MATCH_ODDS`; Test matches have a third runner, "The Draw".
- Team canonicalisation: `scripts/cricsheet_harmonizer.py:89 canonicalize_team(name)`.
- Contract `real tick` (README): a dict with
  - `time`, `market_id`, `match_id` (`bf_<market_id>`), `team_home`, `team_away` (Betfair runner order 0 and 1),
    `competition`, `match_format`, `family`, `in_play` (bool);
  - `ltp_home`, `ltp_away`, `ltp_draw`, and `back_home`, `lay_home`, `back_away`, `lay_away` (None unless present in the
    file);
  - `traded_volume_home`, `traded_volume_away` (None if absent);
  - `source="betfair_historical"`, `data_tier` (`basic|advanced|pro`), `market_model="real"`.
  The result lives only in `_meta` on the first tick (`winner`, `winner_is_home`, `result_type` including draw / no
  result), matching S01's `SETTLEMENT_ONLY_KEYS`.
- Exclusions: void or abandoned markets (no WINNER), markets with fewer than 10 in-play ticks, and simulated or virtual
  events (`SRL`, `Simulated`, `Virtual` in event or runner names).

## Owned files

- `scraper/parsers/betfair_historical.py`
- New: `scripts/build_real_price_corpus.py`
- New: `data/real/README.md` (provenance note only)
- New: `tests/unit/test_s35_real_corpus.py` with small fixture files under `tests/fixtures/betfair/` (hand-made stream
  lines for one T20 market: BASIC ltp-only, one ADVANCED with batb/batl, one void market, one SRL market)

## Tasks

1. `build_real_price_corpus.py --src <dir> --out data/real/betfair`:
   - walk `.bz2/.gz/.json` files and parse them with `BetfairHistoricalParser`;
   - convert to `real tick` dicts and write one file per market, plus a manifest (market_id, family, competition, start,
     n_ticks, n_in_play, data_tier, winner, sha1) with a rejection list and reasons.
   It is idempotent.
2. Parser fixes needed for this:
   - keep `ltp` separate from back/lay (never copy ltp into back/lay);
   - keep traded volume when present;
   - record the in-play flag per tick from `marketDefinition.inPlay`;
   - support the three-runner Test market (draw).
3. Exclusions as listed, each with a reason code.
4. Loader: `load_real_corpus(family=None, tier=None) -> list[episode]` in the same script, importable.
5. Print a summary: matches per family and tier, and in-play tick-interval statistics.

## Success criteria

- [ ] The BASIC fixture yields ticks with `ltp_*` set and every `back_*`/`lay_*` None; the ADVANCED fixture fills both.
- [ ] The void and SRL fixtures are rejected with reasons `no_winner` and `simulated_event`.
- [ ] The result appears only in `_meta`; no other tick carries `winner*`.
- [ ] Re-running writes nothing new.
- [ ] Baseline suite passes.

## Verification

```bash
python -m pytest tests/unit/test_s35_real_corpus.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Ball-by-ball state (S36). The DB import path (unchanged).
