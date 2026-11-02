# S25 — In-house venue par and toss prior engine from Cricsheet

**Priority:** P1 · **Tier:** sonnet · **Depends on:** none · **Wave:** 10 (merged plan)
**Conflicts with:** none (creates standalone script, database table, and test file)

## Story

As the strategy owner, I need an automated venue and toss intelligence engine that aggregates historical venue par scores, run rates, and toss decision bias across our 28,470 Cricsheet matches, so that the trading engine knows whether a team is ahead or behind relative to that specific stadium's scoring environment.

## Background

- A score of 170/4 in 20 overs is an above-par, winning total at Chepauk (MA Chidambaram Stadium, Chennai), but a severely below-par, losing total at Chinnaswamy (Bengaluru) or Wankhede (Mumbai).
- Currently, PHOENIX uses generic hardcoded par scores (e.g. 168.0 for men's T20, 188.0 for IPL) without adjusting for venue altitude, pitch conditions, ground boundaries, or day/night dew factor.
- Cricsheet includes `info.venue` and `info.toss` on almost every match.
- This story computes empirical rolling venue priors from open historical data with zero lookahead bias.

### Metrics Computed per Venue & Format

1. **`avg_first_innings_score`:** Mean 1st innings total for completed 20/50-over matches.
2. **`avg_par_run_rate`:** Empirical average runs per over at this ground in that format.
3. **`chase_win_pct`:** Percentage of matches won by the team batting second (dew factor / pitch deterioration indicator).
4. **`toss_win_match_win_pct`:** Correlation between winning the toss and winning the match.

## Context Pack

- Cricsheet metadata fields:
  ```json
  "info": {
    "venue": "M Chinnaswamy Stadium, Bengaluru",
    "toss": {
      "winner": "Royal Challengers Bangalore",
      "decision": "field"
    }
  }
  ```
- Output JSON artifact: `data/ratings/venue_priors.json` mapping `(canonical_venue_name, format) -> {par_score, par_rr, chase_win_pct, sample_size}`.
- TimescaleDB table:
  ```sql
  CREATE TABLE IF NOT EXISTS venue_priors (
      venue_name VARCHAR(150) NOT NULL,
      format_category VARCHAR(20) NOT NULL,
      sample_matches INT NOT NULL,
      avg_first_innings_score DOUBLE PRECISION NOT NULL,
      avg_par_run_rate DOUBLE PRECISION NOT NULL,
      chase_win_pct DOUBLE PRECISION NOT NULL,
      toss_win_match_win_pct DOUBLE PRECISION NOT NULL,
      updated_at TIMESTAMPTZ NOT NULL,
      PRIMARY KEY (venue_name, format_category)
  );
  ```

## Owned Files

- New: `scripts/compute_venue_priors.py`
- New: `shared/venue_priors.py`
- New: `tests/unit/test_s25_venue_priors.py`

## Tasks

- [ ] Implement `shared/venue_priors.py`:
  - Canonicalize venue aliases (e.g. "M. Chinnaswamy Stadium", "M Chinnaswamy Stadium, Bengaluru" -> "Bengaluru").
  - `VenuePriorsLookup` class with fast in-memory dictionary and safe fallback to format defaults when venue sample size $< 5$.
- [ ] Implement `scripts/compute_venue_priors.py`:
  - Parses Cricsheet match files, grouping by venue and format.
  - Aggregates 1st innings scores, run rates, chase outcomes, and toss choices.
  - Exports `data/ratings/venue_priors.json` and inserts into TimescaleDB `venue_priors`.
- [ ] Implement `tests/unit/test_s25_venue_priors.py`:
  - Test venue name canonicalization.
  - Assert that venues with high-scoring games produce expected higher par rates.
  - Assert safe fallback to format default when venue is unknown.

## Verification

```bash
# Run unit tests
python -m pytest tests/unit/test_s25_venue_priors.py -v

# Run batch venue computation (dry run)
python -m scripts.compute_venue_priors --limit 100 --dry-run
```

## Executor notes (review 2026-11-02, binding; they override the text above where they differ)

- **Input is raw Cricsheet only:** `data/raw/{ipl,odis,t20s,tests}/*.json` (11,235 files) plus `data/incoming_cricsheet/*.json`.
  Never read `data/specialists/corpus_*.json.gz`; those are synthetic. Useful `info` keys: `dates`, `teams`, `gender`
  (`male`/`female`), `match_type` (`T20`, `ODI`, `Test`, `MDM`, `IT20`, `ODM`), `team_type` (`international`/`club`),
  `venue`, `city`, `toss {winner, decision}`, `outcome {winner, by {runs|wickets}, result: tie|draw|no result}`, `event`.
- **Rating keys include gender:** `(family, gender)`, where `family` = `strategy/fair_value.py:25 family_of(match_type)`
  (t20 / odi / multiday). Club and international teams can share a family because their names differ.
- **Team names:** `scripts/cricsheet_harmonizer.py:89 canonicalize_team(name)` returns `None` for unknown names. Use
  `canonicalize_team(n) or n.strip()`, never drop a team.
- **Strict as-of:** every query takes `as_of` (a date) and uses only matches with `dates[0] < as_of`. A match never sees
  its own result. Tests must assert this.
- **No database in tests or by default:** the DB insert is optional behind `--to-db`. The default writes JSON under
  `data/ratings/` (gitignored). Tests use small in-memory match dicts, not files.
- **Consumer:** S38 (`strategy/ratings.py`) imports your module. Keep the public API below stable and documented.
- Baseline: `python -m pytest tests/unit tests/rl -q` → 277+ passed. Do not commit. Do not edit files outside Owned files.
- **Public API:** `VenuePriors.build(match_infos_with_innings)`;
  `get(venue, family, gender, as_of) -> dict(par_first_innings, par_run_rate, chase_win_pct, toss_winner_win_pct, n)`
  computed only from matches before `as_of`. Fall back to the (family, gender) average, computed the same as-of way, when
  `n < 5`, and set `fallback=True`. `canonical_venue(name, city) -> str`.
- Venue aliases: canonicalise by lowercasing, stripping punctuation and the trailing `, <city>`, and an explicit alias
  table for the top 30 grounds. Keep `city` as a tiebreak.
- The full-period aggregate JSON is allowed only as a convenience snapshot. Backtests must use `get(..., as_of)`. Add a
  test that a venue's prior for match N does not include match N or later.
- **Family mapping gotcha:** `family_of("ODM")` returns `multiday` (bug, fixed in S38). Map Cricsheet `match_type` yourself: `T20`/`IT20` -> t20, `ODI`/`ODM` -> odi, `Test`/`MDM` -> multiday.
