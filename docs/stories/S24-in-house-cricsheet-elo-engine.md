# S24 — In-house chronological Cricket Elo engine from Cricsheet

**Priority:** P1 · **Tier:** sonnet · **Depends on:** none · **Wave:** 10 (merged plan)
**Conflicts with:** none (creates standalone script, database table, and test file)

## Story

As the strategy owner, I need an in-house, 100% legal, open-source Elo rating engine that parses our 28,470 Cricsheet matches in strict chronological order and computes zero-lookahead Team Elo ratings per format, so that the trading models have an authentic baseline prior of team quality without relying on third-party scrapers or proprietary rankings.

## Background

- Currently, PHOENIX's 50-dimensional observation vector has no concept of team quality. It evaluates match scorecards in a vacuum (e.g., India vs. Oman is evaluated using generic format averages), creating a significant Sim2Real gap against sharp bookmakers who price pre-match talent disparity.
- Cricsheet is licensed under the Open Database License (ODbL) / CC-BY 4.0, which explicitly permits derivative analytical modeling.
- By processing all 28,470 matches strictly in chronological order ($T_1 < T_2 < \dots < T_N$), we can compute an exact, leak-free Elo rating for every international and domestic franchise team prior to ball 1 of match $N$.

### Mathematical Formulation

1. **Base Rating:** All teams start at $R_0 = 1500$.
2. **Format Isolation:** Separate Elo rating trackers for:
   - `test` (Men's & Women's First-Class / Tests)
   - `odi` (Men's & Women's 50-over)
   - `t20` (Men's & Women's T20 Internationals)
   - `franchise` (IPL, BBL, WBBL, PSL, CPL, Blast, etc.)
3. **Expected Win Probability:**
   $$E_{\text{home}} = \frac{1}{1 + 10^{(R_{\text{away}} - R_{\text{home}}) / 400}}$$
4. **Margin of Victory (MOV) Multiplier:**
   To reward dominant wins over narrow coin-flips:
   - In limited-overs (T20/ODI):
     $$\text{MOV} = \ln\left(1 + \frac{|\Delta \text{runs}|}{25}\right) \quad \text{or} \quad \ln\left(1 + \frac{|\text{wickets in hand}|}{2}\right)$$
   - Bound $\text{MOV} \in [0.8, 2.2]$.
5. **Rating Update:**
   $$R_{\text{home}}' = R_{\text{home}} + K \times \text{MOV} \times (S_{\text{home}} - E_{\text{home}})$$
   - Format $K$-factors: $K_{\text{T20}} = 32$, $K_{\text{ODI}} = 24$, $K_{\text{Test}} = 16$.
   - $S_{\text{home}} = 1.0$ (win), $0.5$ (tie/draw/no result), $0.0$ (loss).

## Context Pack

- Cricsheet matches directory: `data/raw/` or individual specialist archives `data/specialists/corpus_<key>.json.gz`.
- Database table schema (TimescaleDB):
  ```sql
  CREATE TABLE IF NOT EXISTS team_elo_ratings (
      team_name VARCHAR(100) NOT NULL,
      format_category VARCHAR(20) NOT NULL,
      match_date TIMESTAMPTZ NOT NULL,
      match_id VARCHAR(50) NOT NULL,
      pre_match_elo DOUBLE PRECISION NOT NULL,
      post_match_elo DOUBLE PRECISION NOT NULL,
      opponent VARCHAR(100) NOT NULL,
      is_winner BOOLEAN,
      PRIMARY KEY (team_name, format_category, match_date, match_id)
  );
  ```
- Output JSON artifact: `data/ratings/team_elo_latest.json` (mapping `(team_name, format) -> current_elo`) and `data/ratings/team_elo_history.json.gz` for historical backtests.

## Owned Files

- New: `scripts/compute_cricsheet_elo.py`
- New: `shared/elo.py`
- New: `tests/unit/test_s24_cricsheet_elo.py`

## Tasks

- [ ] Implement `shared/elo.py` with `CricketEloTracker` class:
  - Supports separate format tracking (`t20`, `odi`, `test`, `franchise`).
  - Implements logistic win expectation $E$, MOV scaling, and rating update.
  - Implements zero-lookahead query method: `get_pre_match_elo(team, format, as_of_date) -> float`.
- [ ] Implement `scripts/compute_cricsheet_elo.py`:
  - Scans and orders all Cricsheet match files by `info.dates[0]`.
  - Normalizes team names using `CANONICAL_TEAMS` from `scripts/cricsheet_harmonizer.py`.
  - Runs sequential rating updates across all matches.
  - Generates `data/ratings/team_elo_latest.json` and inserts into TimescaleDB `team_elo_ratings`.
- [ ] Implement `tests/unit/test_s24_cricsheet_elo.py`:
  - Test rating updates for known outcomes.
  - Verify that a dominant win yields a higher rating delta than a 1-run win.
  - Assert zero lookahead bias: updating match $N$ does not change pre-match Elo for match $N$.
  - Assert format isolation: T20 matches do not alter Test Elo ratings.

## Verification

```bash
# Run unit tests
python -m pytest tests/unit/test_s24_cricsheet_elo.py -v

# Run batch Elo computation (dry-run or limited matches for quick CI test)
python -m scripts.compute_cricsheet_elo --limit 100 --dry-run
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
- **Public API:** `CricketEloTracker(k_by_family={"t20": 32, "odi": 24, "multiday": 16}, base=1500.0)`;
  `update(match_info: dict) -> None` (call in date order); `rating(team, family, gender, as_of) -> float`;
  `expected(team_a, team_b, family, gender, as_of) -> float` (P(team_a wins)); `history() -> list[dict]` with
  `{match_id, date, team, family, gender, pre, post}`; `save(path)` / `load(path)`. No "home" concept: Cricsheet has no
  home flag, so use team order only for bookkeeping.
- Draws, ties and no-results: S = 0.5 for draw/tie; skip no-result matches (no update).
- Report in your summary: the number of matches processed and skipped (with reasons), and the top 5 teams per (family, gender).
- **Family mapping gotcha:** `family_of("ODM")` returns `multiday` (bug, fixed in S38). Map Cricsheet `match_type` yourself: `T20`/`IT20` -> t20, `ODI`/`ODM` -> odi, `Test`/`MDM` -> multiday.
