# Value-focused plan: assessment of the three pillars

Status: plan, 2026-11-03 (afternoon). Follows `next-phase-plan.md` (S74–S82) with what the first replay and the first
settled matches showed. Stories S83–S90. Goal and pillars: `docs/GOAL-AND-ARCHITECTURE.md`.

## What the evidence says today

The S74 replay ran the live paper trader over everything recorded since 1 November (2026-11-03 10:56 UTC cut-off).

| Fact | Number | What it means |
|---|---|---|
| Model variants' replay P&L | +301k to +418k each | Looks like profit; it is not evidence yet |
| Settled matches behind that P&L | 3 | Effective sample size is 3, not 60 bets |
| Share of P&L from one match (lb_36262294) | about 70% | One result decides the headline |
| Replay bets placed in play | 572 of 596 | Almost everything is in-play trading |
| In-play bets priced with the **pre-match** model | 204 | No live score, so the trader bet a pre-match opinion against a price that already knows the score |
| Largest recorded edge | 0.86 | Impossible; a symptom of the line above |
| Trades per match per variant | 16–17 (271 reduces, 195 adds, 38 flips) | Churn; S75 guards address it |
| Market-only control trades | 1 | The control is inert, so it cannot tell us what CLV the fill rule produces on its own |
| Real matches with a decided result | 8 | About 7 real matches a day are recorded |
| Listing start vs match-page start, one match | 5.5 h apart | "In play" and the pre-match CLV reference depend on the start time |

**Verdict.** The architecture of all three pillars is sound; what is weakest is **how we measure**. The current
numbers would let luck and modelling errors pass as edge. The next stories make the measurement honest first (Pillar 3),
stop the one structural modelling error (Pillar 2), and make the two data facts that every metric depends on (when the
match started, who won) true (Pillar 1). Everything else waits behind them.

## Which metrics carry value

A metric earns weight by how directly it predicts real-money profit and how hard it is to fool.

**Tier A: decide money.** Headline of every report and gate.

| Metric | Why it carries value | Gap today |
|---|---|---|
| CLV at the takeable price after the 60 s delay, **clustered by match**, as **excess over a placebo**, split pre-match / in-play | The leading indicator of profit that luck cannot fake once the unit is the match and the fill mechanics are subtracted | Per-bet CIs; no placebo; segments pooled |
| Settled real matches with a tradable book (both sides back and lay) | The sample size every gate needs; grows only with recording | Not tracked as a single number |
| Result coverage and correctness | A wrong or missing label corrupts every other metric | 1 real match missing; settled-market path never seen on real data |
| Fill realism: stake within the size shown, fill rate, slippage | P&L at sizes the market cannot take is fiction | Fills ignore size |
| Log loss vs the de-vigged market on matches with a pre-match price (R1) | The only direct test that the model knows more than the market | 8 matches; R1 needs 200 |

**Tier B: diagnose.** Shown, never a headline.

Worst case per match vs the 100k cap; trades per match and guard refusals; share of real matches with a strength view;
calibration of the market probability; replay vs live parity.

**Tier C: low value.** Kept in the monitor or dropped from reports.

| Metric | Why it carries little value |
|---|---|
| Rows, ticks and market rows recorded | Volume is not quality; the monitor already checks quality |
| Model log loss vs Elo on Cricsheet | Beating Elo says nothing about beating the market |
| Per-bet P&L and CLV confidence intervals | Bets on one match move together; the CI is several times too narrow |
| Number of variants, number of monitor checks | Activity, not evidence |
| Live-score coverage for `legacy` and SRL matches | Not tradable by the strategy; SRL is excluded by default |
| Historic monitor warnings (a fixed 15-min gap) | Already explained; adds noise to the daily signal |

## Pillar by pillar

**Pillar 3, refinement loop (most weight).** The loop is complete (replay, ledger, review, retrain), but its
statistics treat correlated bets as independent and have no placebo baseline, and its fills ignore size. Until that is
fixed, variant evolution (S79) would evolve towards noise, so S79 now waits for S84 and S85.

**Pillar 2, model training (one P0).** The in-play path falls back to the pre-match model when no fresh live score
exists, and the trader then treats that as an in-play edge. That is a category error, not a calibration issue. After it
is fixed, the in-play model needs its own test on our recordings before it trades (S89). The toss model (S80) is small
and moves to P2.

**Pillar 1, data pipeline (two P0s).** The pipeline is clean on what it measures; two facts every metric depends on
are not yet trustworthy: the actual off time (the listing can disagree with the match page by hours), and the result
of every real match. Volume is not the constraint; tradable, settled, correctly timed matches are.

## Stories

| Story | Pillar | Priority | What it changes | Tier-A metric it moves |
|---|---|---|---|---|
| S83 No pre-match opinions in play | 2/3 | P0 | In play, a model edge needs the in-play model with a fresh live score; otherwise the variant trades as market-only (reduce and green-up only) | Removes false in-play edges (204 bets in replay) |
| S84 Match-clustered evidence | 3 | P0 | Match-block bootstrap for CLV and P&L everywhere (review, replay, gates); effective n = matches; pre-match and in-play reported apart | Honest CLV CIs; gates that cannot pass on 3 matches |
| S85 Placebo controls | 3 | P0 | Two seeded placebo variants (random side, same timing and stakes; always back the favourite); every variant's CLV reported as excess over the placebo | CLV that excludes fill-rule and drift artefacts |
| S86 True off time | 1 | P0 | Actual start from the match JSON's in-play flip or the score feed's first ball, stored as `actual_start_time`; used for in-play, the CLV reference and dataset snapshots; monitor flags listing vs JSON disagreements | Correct in-play/pre-match split and pre-match CLV reference |
| S87 Liquidity-aware fills | 3/1 | P1 | Fill only up to the size shown on the match page at fill time; partial fills; fill rate and slippage per variant; delay sensitivity (30/60/120 s) in replay | Fill realism |
| S88 Value-first scorecard and report | 3 | P1 | Daily report and dashboard lead with the Tier-A metrics; Tier-C metrics removed from reports; R1/R2/R3 progress shown as matches, not bets (absorbs S82) | What the operator sees is what decides money |
| S90 Positions a person can place | 3 | P1 | Target position with a no-trade band and a per-match ticket budget (default 10), so a variant changes position in a few deliberate tickets | Realistic P&L and CLV for manual betting (31–48 fills per variant on one match today) |
| S89 In-play model test on our recordings | 2 | P1 | In-play model vs the market at the 10-min horizon on `livescore-v2` matches, clustered by match; in-play model trading allowed per format only when it is not worse than the market | In-play log loss vs market |

**Re-prioritised.** S78 market-implied ratings stays P1. S79 variant evolution moves behind S84 and S85. S80 toss
model moves to P2. S82 dashboard is absorbed by S88. S81 deploys stays P2.

**Operator decision, not a story.** R1 needs 200 matches with a pre-match price per segment; at about 7 real matches a
day that is one to two months. A licensed historical cricket closing-odds dataset would let R1 be tested on thousands
of past matches at once. It costs money and must be bought, not scraped; we take no step without the operator's say.

## Order of work

- **Wave 20A (parallel, after S75 lands):** S83, S84, S86. S83 touches the trader and probability service; S84 the
  review, replay and gate statistics; S86 the scraper, registry and dataset. No shared files.
- **Wave 20B:** S85 (needs S84's statistics), S87, S89, S90.
- **Wave 20C:** S88, then S79 and S78.

## How we will know it worked

- Replay after S83: zero in-play trades priced by the pre-match model; the largest recorded edge under 0.20.
- Every CLV and P&L figure in reports carries its match count, and no gate passes with fewer matches than its rung.
- Model variants' CLV is reported as excess over the placebo. If the excess is not above zero, the report says so plainly.
- Every real match has an `actual_start_time` within 2 minutes of its first in-play price.
- Replay P&L at size-capped fills is reported next to the uncapped figure.
