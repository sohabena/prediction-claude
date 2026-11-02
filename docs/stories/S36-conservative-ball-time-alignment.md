# S36 — Align Cricsheet ball-by-ball to real price histories, conservatively

**Priority:** P0 · **Tier:** sonnet · **Depends on:** S35 · **Wave:** 11

## Story

As the strategy owner, I need each real price tick to carry the match state (score, wickets, overs, innings, target)
that was **already public** at that moment, so that model-based strategies can be backtested on real prices without
look-ahead.

## Background

- Cricsheet JSON (`data/raw/**`, `data/incoming_cricsheet/`) has every delivery but **no timestamps**, only the match date.
  Betfair files have timestamps and prices but no score.
- Alignment errors are not symmetric. If the state is shown **earlier** than it really happened, a backtest
  "knows" a wicket before the market reacts: a fake edge. If it is shown **later**, the result is merely pessimistic.
  The alignment must therefore err late, always.
- Ground truth for measuring alignment error exists: LotusBook recordings carry real timestamps for both price
  (`odds_ticks`) and state (`match_context`: time, score, wickets, overs, innings), for about 16 matches
  (`scripts/export_recorded_corpus.py`, S16, joins them).

## Context pack

- Real ticks: contract `real tick` (README, S35), `load_real_corpus(family)` in `scripts/build_real_price_corpus.py`.
- Cricsheet parsing helpers: `scripts/prepare_specialist_datasets.py:164 parse_cricsheet_json` walks innings, overs and
  deliveries (runs.total, wickets, extras) and builds state fields; reuse its state arithmetic (score, wickets,
  balls_remaining, target, rrr), not its price code. `index_cricsheet_catalog(source_dir)` L390.
- Matching a Cricsheet file to a Betfair market: same date (±1 day for timezones), canonical team names
  (`scripts/cricsheet_harmonizer.py:89 canonicalize_team`), format family. Reject ambiguous matches (double-headers).
- Anchors available in the price series: the first `in_play=True` tick (≈ first ball), the long low-volatility stretch at
  the innings break, and price jumps (|Δ logit| large) that coincide with wickets.

## Owned files

- New: `scripts/align_ball_by_ball.py`
- New: `tests/unit/test_s36_alignment.py`

## Tasks

1. `estimate_ball_times(deliveries, price_ticks, family) -> list[datetime]`:
   - anchor innings 1 at the first in-play tick;
   - spread deliveries by format-typical pace (balls per minute, with extra time for wickets and over changes; fit the
     pace constants on the LotusBook-recorded matches);
   - detect the innings break from the price series;
   - optionally snap wicket balls to the nearest qualifying price jump within ±3 minutes.
2. **Conservative release:** a delivery's state becomes visible at `estimated_time + safety_lag`. The default
   `safety_lag` is the 95th percentile of the measured early-error, and at least 60 s. When unsure, release later.
3. `enrich(real_episode, cricsheet_match) -> episode` as-of joins each price tick to the latest **released** state, and
   records `alignment_quality` (anchor count, pace residual) in `_meta`.
4. Validation: on the LotusBook-recorded matches that also have a Cricsheet file, replace the true state times with
   estimated ones and report the error distribution (median, p95, and the fraction of states released early).
   **Require early releases ≤ 1%.** If there are too few matches (< 5), say so and fall back to a larger safety lag
   (180 s).
5. CLI: `--real data/real/betfair --cricsheet data/raw --out data/real/aligned`, with a manifest of matched, unmatched and
   rejected markets.

## Success criteria

- [ ] On a synthetic fixture where true ball times are known, no state is visible before its true time (0 early
      releases).
- [ ] On the recorded-match validation, the early-release fraction is ≤ 1%, or the fallback lag is used and reported.
- [ ] Ambiguous matches (two candidate markets) are rejected, not guessed.
- [ ] Baseline suite passes.

## Verification

```bash
python -m pytest tests/unit/test_s36_alignment.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Buying a timestamped ball-by-ball feed (operator decision; it would replace estimation).
