# S107 — Tail bets: small backs on the long shot when its price beats the chase base rate

**Pillars:** 1, 2 and 3 · **Priority:** P1 · **Tier:** haiku (two agents) · **Depends on:** S68, S90, S106 · **Wave:** 24

## User story

As the operator, I want a paper variant that behaves like a professional's "1.01 lottery ticket": when one side is
priced at long odds in play, back it with a small, affordable stake, but only when the price is clearly longer than
its real chance, judged on runs needed, balls left, **wickets in hand and the batters at the crease**.

## Evidence (2026-11-05, `docs/strategy/live-betting-expert-persona.md`)

13,756 Cricsheet T20 chases: need 20+ off the last 6 balls 0.59% won (fair odds 170); 30+ off 12 2.1% (48); 50+ off
24 4.5% (22). Our recordings (27 matches): 2 of 22 sides at 10.0+ came back, 0 of 8 at 20.0+. The live score feed
(`match-center/score/v3`, livescore-v2 matches) sends `stats.batsmen` (name, batsmanRuns, balls, strikeRate,
isOnStrike), `stats.bowler`, `battingTeam.partnerShipScore/partnerShipBalls` and `stats.lastBatsmanOut`; the parser
reads none of them today.

## Part A — capture the players (agent A)

Files: `scraper/parsers/lotusbook_score.py`, `backend/db_init.py`, the `match_context` ORM model, tests
`tests/unit/test_s107a_players.py`.

1. `ScoreState` gains `players: Optional[dict]`: `{"batsmen": [{"name", "runs", "balls", "strike_rate",
   "on_strike"}], "bowler": {"name", "overs", "wickets", "runs"} | None, "partnership": {"runs", "balls"} | None,
   "last_out": {"name", "runs", "balls"} | None}`; missing parts are None, never estimated.
2. `match_context` gains a nullable `players JSONB` column (migration in `backend/db_init.py` with `ADD COLUMN IF NOT
   EXISTS`; ORM model updated); the context row writer stores it. Old code keeps working (nullable).
3. Tests with the real fixture `tests/fixtures/lotusbook/score_v3_real_live_36267578_20261103.json` and edited
   copies (no batsmen, one batsman, bad numbers). No database writes in tests.

## Part B — the chase model and the tail-bet variant (agent B)

Files: `training/chase_model.py`, `scripts/train_chase_model.py`, `strategy/tail_bet.py`,
`virtual_trading/paper_trader.py` (variant wiring only), `Dockerfile.scraper` (COPY lines for the new script and
modules if the image copies files one by one; check), tests `tests/unit/test_s107b_tail_bets.py`.

1. **Dataset** (`training/chase_model.py`, pure): from Cricsheet `data/raw/all` T20 second innings (20 overs, no DLS,
   no super over, decided result): one row per legal ball with runs needed, balls left, wickets in hand, the two
   current batters' runs and balls, their career T20 strike rate and average **as of that match's date** (no
   look-ahead: only earlier matches), gender, and the label (chase won).
2. **Model**: a small calibrated classifier (scikit-learn, pinned versions) for P(chase wins). Two versions: `core`
   (runs needed, balls left, wickets in hand) and `players` (plus the batter features). Season walk-forward
   evaluation: log loss, calibration in the tail (states with P under 10% and over 90%), compared with the empirical
   base-rate table. Saved to `models/chase/<date>.joblib` with `current.json`. Live, the `players` model is used only
   when both batters' names match Cricsheet; otherwise `core`.
3. **Decision rule** (`strategy/tail_bet.py`, pure): in play, second innings, with a fresh live score: for each side,
   fair odds = 1 / P(side wins) from the model. Back a side only when its takeable back price is at least 10.0 and
   at least `1.2 ×` fair odds (`TAIL_MIN_PRICE = 10.0`, `TAIL_VALUE_RATIO = 1.2`). Stake fixed `TAIL_STAKE = 2,000`
   (well under 2.5% of the 100,000 match budget), at most `TAIL_MAX_TICKETS = 3` per match, at least 5 minutes
   apart; never lay; never add to a side already backed more than twice. All S75 new-risk guards and the 60 s fill
   rule apply; the worst case stays within the match cap.
4. **Variant** `tail` in the paper trader (source `chase_model`): its own decision path (not `model_edge.decide`),
   recorded like every bet (`reason = "tail"`, `p_model` = model probability of the backed side), reported with the
   others and compared with the placebos. It does not change any other variant.
5. **Reports**: the variant appears in the export, the daily review and the evidence page like any other.
6. **Tests** (fakes only): no look-ahead in batter features, label alignment, the price and value thresholds, the
   ticket limit and spacing, never a lay, the `core` fallback when names do not match, no bet without a fresh score
   or in the first innings, and the variant leaves other variants unchanged.
7. **First test**: train, report walk-forward numbers, then a pinned replay (`--to 2026-11-05T06:00:00+00:00`) with the
   variant: bets, P&L, CLV, honest sample size.
