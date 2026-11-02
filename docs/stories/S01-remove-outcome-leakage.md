# S01 — Remove outcome leakage from synthetic odds

**Priority:** P0 · **Depends on:** none · **Wave:** 1

## Story

As a quant researcher, I need the synthetic training odds to depend only on information
available at time *t*, so that any edge a model learns is real and not a function of
the final match result.

## Background (what is wrong today)

Three generators compute an in-play win probability and then **bias it toward the
actual winner**:

- `scripts/cricsheet_harmonizer.py:156-162` — `CricketWinProbModel.calculate_win_prob`
  takes `winner_is_home` and adds `bias = 0.15 * match_progress**1.5` toward the winner.
- `scripts/prepare_specialist_datasets.py:99-105` — the same bias (this is the generator
  the weekly retrain uses via `parse_cricsheet_json`).
- `scripts/generate_realistic_historical_corpus.py:131-198` — picks `home_wins` first,
  then drifts `p_home` toward `0.98/0.02` based on it.

Because the agent also sees score/wickets/overs, it can compute
`odds − f(state)` and recover the winner. Every backtest built on this data is invalid.

Secondary defects in the same code:

- `match_progress` in innings 2 is divided by `2 * max_balls`, so the bias peaks at the
  end of innings 1 and drops at the innings break (a discontinuity the network can learn).
- `prepare_specialist_datasets.py:298` labels synthetic ticks `"source": "betfair"`;
  the harmonizer uses `"cricsheet_official"`. Synthetic data must never be labelled as
  real exchange data.
- Test matches: only the first two innings are processed, and matches without a winner
  (draws) are dropped, yet the env treats `draw` as void. Test/first-class corpora are
  therefore biased toward decisive results.

## Context pack

- `scripts/prepare_specialist_datasets.py` (494 lines) — **used by weekly retrain**:
  `class CricketWinProbModel` L30, `calculate_win_prob(fmt, innings, overs, score, wickets, target,
  batting_is_home, winner_is_home)` L34-108 (bias block L99-105); `synthesize_exchange_ticks(p_home, seed)`
  L110; `parse_cricsheet_json(...)` L136 (winner parsed L159-167, call to `calculate_win_prob` L232,
  tick dict incl. `"source": "betfair"` L298 and `_meta` L299-304, returned match dict L316-330);
  `index_cricsheet_catalog` L335; `build_specialist_dataset` L397; `main` L438.
- `scripts/cricsheet_harmonizer.py` (582 lines): same model L87-164 (bias L156-162),
  `synthesize_exchange_ticks` L167-195, `parse_and_harmonize_match(filepath, fmt_category, match_index)`
  L198-390 (innings loop L262-363, only `innings_list[:2]`), `insert_into_database` L393 (writes
  `odds_ticks` with `source='cricsheet_official'`, and `DELETE ... WHERE source = 'cricsheet_official'` L402 —
  update that string together with the label), `build_harmonized_dataset` L507 (shuffles files with
  `random.Random(42)` L530), `main` L567.
- `scripts/generate_realistic_historical_corpus.py` (269 lines): `RealisticCorpusGenerator(seed)`,
  `generate_corpus(match_count)` L72, `_generate_single_match(...)` L106-268 (winner pre-drawn L131-142,
  per-archetype drift L150-198, tick dict L222-250).
- **Importers of the corpus generator (do not edit; keep the public API stable):**
  `scraper/importers/betfair_importer.py:33,393` (`RealisticCorpusGenerator(seed=42).generate_corpus()`, reads
  `SimulatedCricketMatch` fields) and `scripts/benchmark_offline_improvements.py:25,115` (also imports
  `MatchArchetype`). Keep `SimulatedCricketMatch` fields and `MatchArchetype` names.
- Settlement consumer: `rl/environment.py:174-190` reads `_meta.winner/team_home/team_away/result_type`
  from the first tick (or a dict episode `{"_meta", "ticks"}`). Keep `_meta` on ticks; add `result_type`.
- No other repo code filters on `source == "betfair"` or `"cricsheet_official"`.
- Cricsheet JSON shape: `info.teams[2]`, `info.outcome.winner` (absent for draw/tie/no result;
  `info.outcome.result` = "draw"/"tie"/"no result"), `info.dates[0]`, `info.event.name`,
  `innings[i].team`, `innings[i].overs[j].over`, `.deliveries[k].runs.total`, `.deliveries[k].wickets` (list).
- scikit-learn is installed.

## Owned files

- `scripts/cricsheet_harmonizer.py`
- `scripts/prepare_specialist_datasets.py`
- `scripts/generate_realistic_historical_corpus.py`
- New: `scripts/audit_outcome_leakage.py`
- New: `tests/unit/test_s01_no_outcome_leakage.py`

## Tasks

1. Remove the `winner_is_home` parameter from every `calculate_win_prob` and delete the
   bias block. The probability must be a pure function of
   `(fmt, innings, overs, score, wickets, target, batting_is_home)` plus optional
   **outcome-independent** noise.
2. Add optional market noise that does not depend on the outcome: e.g. a per-match
   mean-reverting random walk in log-odds space (`σ≈0.05–0.15` per tick, seeded by
   `match_index`). Document the parameters in the docstring. Noise is allowed; correlation
   with the result is not.
3. `generate_realistic_historical_corpus.py`: generate the **ball/score path first**
   from the archetype, derive `p_home` from the state-only model, and only then *sample*
   the winner from the terminal state (e.g. chase succeeded or not). The winner must be a
   consequence of the path, never an input to the price.
4. Change synthetic tick `source` to `"synthetic_cricsheet"` in both Cricsheet generators
   and `"synthetic_archetype"` in the corpus generator. `grep -rn '"betfair"\|cricsheet_official'`
   across the repo and list (in your report, do not edit) any consumer that filters on
   the old values so the orchestrator can patch it.
5. Multi-day formats: process all innings (up to 4) and keep drawn matches with
   `result_type="draw"` in the episode meta. For draws the 2-way market is out of scope:
   mark them so the trainer can exclude or void them, but do not drop them silently.
   Record counts of kept / dropped / drawn matches in the script output.
6. Keep `_meta` (winner, loser, gender) only for settlement. Add a module-level
   constant `SETTLEMENT_ONLY_KEYS = ("_meta", "winner", "loser", "winner_is_home")` in
   `prepare_specialist_datasets.py` and a docstring note that the feature pipeline must
   never read these.
7. Write `scripts/audit_outcome_leakage.py`:
   - loads a corpus file (`data/specialists/corpus_<key>.json.gz` or a path argument),
   - fits a logistic regression predicting `home_won` from **state features only**
     (score, wickets, overs, innings, target, rrr) and a second one from state + odds,
   - reports AUC for both on a held-out 30% split, per innings-progress bucket,
   - exits non-zero if `AUC(state+odds) − AUC(state) > 0.03` in any bucket.
   Use sklearn if installed, otherwise numpy-only logistic regression.

## Success criteria

- [ ] `grep -rn "winner_is_home" scripts/cricsheet_harmonizer.py scripts/prepare_specialist_datasets.py`
      shows no use inside any probability/price computation.
- [ ] Unit test: for a fixed match state, `calculate_win_prob` returns identical values
      regardless of the eventual winner (function no longer accepts it).
- [ ] Unit test: generating the same Cricsheet JSON twice with the `outcome.winner`
      field swapped produces byte-identical odds columns (`back_*`, `lay_*`,
      `implied_prob_*`, volumes).
- [ ] Unit test: in `generate_realistic_historical_corpus`, ticks do not change when only
      the RNG draw for the winner changes (winner sampled after path).
- [ ] No tick has `source == "betfair"` from any synthetic generator.
- [ ] `python scripts/audit_outcome_leakage.py --synthetic-sample 500` (generate a sample
      in-memory if no corpus file exists) passes with incremental AUC ≤ 0.03.
- [ ] Test-format episodes include innings 3–4 when present; drawn matches are retained
      with `result_type="draw"`.

## Verification

```bash
python -m pytest tests/unit/test_s01_no_outcome_leakage.py -q
python scripts/audit_outcome_leakage.py --synthetic-sample 500
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Regenerating the production `data/specialists/` corpora (orchestrator does this after merge).
- Any change to `rl/` or the feature pipeline.
