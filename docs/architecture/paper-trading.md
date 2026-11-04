# PHOENIX paper trading: virtual bets from the models, measured honestly

Status: plan, 2026-11-03. Builds on `training-strategy.md` (models S58–S68) and `profit-architecture.md` (ladder R3).
Stories S69–S73. No real money moves anywhere; every bet below is virtual.

## Goal (operator, 2026-11-03)

Place virtual bets automatically from the trained models' signals. Per match the worst case may not lose more than
**100,000**. The model may back or lay either side as the match progresses (enter, add, reduce, hedge). The strategy
improves over time from its own settled results.

## Why virtual betting now

No model has passed R1 yet (too few settled recordings). Paper bets placed on live matches at realistic prices are
exactly the evidence R3 needs, and they also grow the dataset the blend learns from. Paper mode is allowed to bet on
the model's own view before R1 passes; real signals to the operator stay behind the trust gate.

## Design

```
 every 15 s
 DB (v_match_odds, market_ticks, match_context, registry) ──► S72 paper trader service (Docker `paper-trader`)
                                                              │  builds a decision snapshot per live / pre-match match
                                                              ▼
                   S69 probability: pre-match v3 (Cricsheet), in-play state model when a live score exists,
                                    shrunk toward the de-vigged market:  p = w·p_model + (1−w)·p_mkt
                                                              ▼
                   S70 ModelEdgeStrategy (per variant): target position from edge; back/lay home/away;
                       add, reduce, hedge/green-up; worst-case loss per match ≤ 100,000
                                                              ▼
                   fill rule: decide at t on the book at t; fill at the takeable price at t+60 s if it is still
                   within the limit price, else no fill (manual-latency realism)
                                                              ▼
                   S71 paper ledger: paper_bets (one row per fill, with the decision context), settlement on
                       match_results (void on tie/unresolved/abandoned), CLV vs the price at the off / +10 min
                                                              ▼
                   S73 improvement loop: daily scoring per variant (CLV, P&L, calibration), Thompson-style
                       ranking, primary variant chosen on evidence; weekly report; kill switch per variant
```

**Variants.** The same strategy runs as a small grid at once (each with its own 100,000-per-match cap and ledger):
shrink weight `w` ∈ {0.3, 0.5, 0.7} × minimum edge ∈ {3%, 6%}, plus a `market_only` control that should lose about
the margin. Running them side by side on the same matches is what lets the system learn which settings work, without
waiting for separate samples.

**Both sides as the match moves.** A position is the net P&L per outcome (home wins / away wins). Each decision
computes a target exposure from the current edge, then trades toward it: back the side with positive edge, lay it when
the edge turns negative, and green up (equalise the outcome P&Ls) when the edge has decayed but a profit is locked.
Every trade is checked against the cap: the worst outcome of the position after the trade must stay ≥ −100,000.

**What it will not do.** No real money, no orders to any site, no automation of the operator's account. Signals to
the operator still pass the trust gate (`strategy/trust.py`).

## Sanity guards and replay (S74, S75, S76)

- **Replay.** `python scripts/replay_paper.py --from 2026-11-01 --to <pinned time> --out reports/replay/<name>` runs
  `run_cycle` over recorded history with an in-memory ledger (`virtual_trading/memory_session.py`) and an as-of feed
  (`virtual_trading/replay_feed.py`) that never returns a row after the decision time. Read-only; about 30 s for all
  recordings. Pin `--to` when comparing two runs.
- **Guards** (`strategy/trade_guards.py`, counted per cycle as `guard_refusals`). On every trade: crossed book, no band,
  contradictory band, market probability outside the band. On new risk only (enter, add, flip): a book field older
  than 60 s in play or 15 min pre-match, spread over 15% of the price, more than 3 trades per match per variant in
  10 minutes, a flip within 5 minutes of the last flip.
- **Cap.** Decisions are sized to the 100,000 budget; the operator allows 20% tolerance (2026-11-03), so no fill may
  take a variant's worst outcome on a match beyond 120,000 (`HARD_MATCH_LOSS`; refused fills are `over_loss_limit`).
  LAY stakes are sized at their limit price, so a fill above the decision price cannot breach the 100,000
  worst case (a replay found one at 101,994 before this).
- **Monitor `paper_sanity` (FAIL).** The control places more than 2 trades in 24 h, a filled bet's probability is more
  than 20 points from its price's implied probability (a WARN to review, not the edge, which is large at long odds), or a variant trades
  one match more than 35 times (budget 30). Counts bets decided from 2026-11-03 12:36 UTC (S90 live).
- **Settlement.** Every cycle; `draw`, `tie`, `no_result` and `abandoned` void; matches with any decided result are
  no longer candidates. Missing results are listed daily (`scripts/results_reconciliation.py`) and entered by hand
  with `scripts/set_result.py` from a public scorecard; the result detector never overwrites a hand-entered result.
- **Known open issue.** Churn: 31 to 48 fills per model variant on one T20 match in replay, half of them reduces.
  Addressed by S83 (no pre-match model in play) and S90 (target position with a ticket budget).

## In play without a live score (S83)

In play (`Candidate.in_play`, start <= now), a model variant may use the model probability only from the in-play model
with a fresh live score (`source` = `inplay`). Without one, the probability service's pre-match fallback is not traded
in play: the variant gets no model probability, so it may reduce or green up on the market alone and never enter, add
or flip. Each such variant-match in a cycle is counted as `guard_refusals["inplay_without_state"]`. The rows it writes
record `source` = `market_only` and `p_model` = null. Pre-match decisions are unchanged, and the switch happens at the
start time. The market-only control is unaffected in its decisions; its in-play rows are labelled `market_only` too.

## In-play model gate (S89)

`scripts/evaluate_inplay.py` (weekly, step 7 of `retrain_weekly.py`) scores the in-play model against the LotusBook
in-play price on our own `livescore-v2` recordings, match-clustered, and writes `models/registry/inplay_gate.json`
per format. A format opens only with at least 15 decided matches, the model's log loss no worse than the market's and
the CI's lower bound below zero. Decision (2026-11-03): the gate does **not** restrict paper trading, which is how
in-play evidence accumulates and costs nothing; it applies to in-play signals to the operator, alongside R3. On
2026-11-03 every format is closed (1 decided match).

## Placebo controls (S85)

Two controls show how much CLV the fill rule, the delay and price drift produce on their own. They trade at the decision
times of the reference variant (the primary in `models/paper/primary.json`, else `w05_e03`) with its stake, and they use
the market probability only (source `market_only`):

- `placebo_random`: a seeded-random side and selection (two bits of a SHA-256 over match, decision time and name).
- `placebo_favourite`: backs the market favourite (the lower back price).

Each placebo ticket is priced at the takeable price with the same limit rule, capped per ticket and per match, and
checked by the S75 new-risk guards (`model_edge.placebo_ticket`). A placebo is never primary, never ranked and never a
signal. Every strategy variant's CLV is reported as an excess over `placebo_random`: per match, mean CLV minus the
control's mean CLV on that match, with a match-clustered CI over matches. A CI from fewer than 10 matches counts as
"too few matches", and the daily review states in one sentence when no variant's excess CI lies above zero.

## Market-dynamics variants (S97)

`mkt_dyn` (minimum edge 3%) and `mkt_dyn_e06` (6%) take their probability from `strategy/market_dynamics.py`: the
band market probability now plus the predicted change over the next 10 minutes, clipped to 0.01..0.99 (not to the
book's band: inside a band no takeable price has a positive edge, so a band clip would stop every trade). The model
(`training/market_dynamics.py`, `models/market_dynamics/current.json`) uses only the odds' own movement and the live
scoreboard, with a no-scoreboard flag. In play only: pre-match they open no new risk. With no model file they open no
new risk. Source `market_dynamics`. The weekly job (`retrain_weekly.py` step 8) replaces the active model only when
its simulated CLV on matches it did not train on is not worse than the candidate's. Evidence is the replay and the
daily review, as for every variant.

## Measures of success

Per variant, rolling: mean CLV per bet with CI (primary), realised P&L with CI, calibration of `p` vs results,
bets per match, worst-case exposure used. R3 for a variant: ≥ 300 settled paper bets over ≥ 30 matches with CLV CI
lower bound > 0.
