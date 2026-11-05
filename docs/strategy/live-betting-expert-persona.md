# The live betting expert: a persona for PHOENIX

A composite of how disciplined professional in-play cricket bettors think. It is a guide for designing signals, not
a promise of profit: every idea below must earn its place the PHOENIX way (closing-line value in excess of the
placebo, match-clustered, before any money). Written 2026-11-05 at the operator's request.

## Who: "the trader"

Bets cricket for a living, mostly in play. Watches a few leagues closely and ignores the rest. Thinks in prices, not
in teams: "who wins?" is the wrong question; "is this price wrong, and by how much?" is the right one. Keeps a
record of every bet and judges themself on whether they beat the later price (CLV), not on one night's result.
Expects to lose about four nights in ten even when good.

## The rules they never break

1. **Bankroll in units.** One unit is 1 to 2% of the bankroll. A normal idea is 1 unit; a strong one 2 to 3; a long
   shot 0.1 to 0.25 units. Never "all in", never "this one is certain".
2. **A loss limit per match and per day.** When it is hit, they stop. No chasing losses, no doubling up (martingale
   is how bankrolls die).
3. **Only bet when price beats probability.** Fair odds = 1 / their probability. They bet only when the offered odds
   are clearly above fair (after margin and the delay), and they pass on everything else. Most matches get no bet.
4. **Size by edge.** Bigger edge, bigger stake, but a fraction of Kelly (a quarter or less), because their
   probability is an estimate.
5. **Respect the delay and liquidity.** A person is 10 to 30 s behind the pictures and the price can move before the
   bet lands. They never bet into a suspended market and never assume a ghost price (1000 on an empty side) is real.
6. **Know the rules of settlement.** In PHOENIX a tie is void even when a super over decides the exchange market
   (operator, 2026-11-04).

## The live strategies they use

### 1. The "1.01 lottery ticket": small stakes on the outsider when the price is too long

When one side trades at 1.01 to 1.05, the other side is offered at 20 to 1000. Most of the time the outsider loses
and the stake is gone, which is why the stake is tiny. The bet makes money only when the outsider's real chance is
higher than the price says. The expert does not buy every lottery ticket; they buy the ones priced above fair.

What the real chance is, from 13,756 Cricsheet T20 chases (20 overs, no rain rule), measured 2026-11-05:

| Chasing side's position | Chases | Won | Fair odds | Bet only if offered more than about |
|---|---:|---:|---:|---:|
| Need 20+ off the last 6 balls | 3,393 | 0.59% | 170 | 200+ |
| Need 30+ off the last 12 | 3,780 | 2.1% | 48 | 60+ |
| Need 50+ off the last 24 | 4,097 | 4.5% | 22 | 28+ |
| Need 60+ off the last 30 | 4,010 | 5.0% | 20 | 25+ |
| Need 10+ off the last 6 with 2 or fewer wickets left | 1,938 | 2.2% | 45 | 55+ |
| Need 25 to 29 off the last 12 | 692 | 19.2% | 5.2 | 6.5+ |

So "back the outsider at 100 when they need 30 off 12" is a strong bet (fair 48), while "back them at 30" is a bad
one. Our own recordings so far (27 settled matches): 22 sides touched 10.0 or longer in play and 2 came back (9%);
8 touched 20.0 or longer and none came back. Far too few matches to judge.

The mirror image is the bet the expert refuses: **laying the outsider at 1.01-style prices, or backing the favourite
at 1.01 with big money.** It risks 100 to win 1; one comeback (2% of the time in the table above) wipes out a
hundred wins. Tiny returns, rare catastrophe.

### 2. Fade the over-reaction after a wicket or a big over

Prices jump when a set batter falls or an over goes for 20. Often the jump is too big, and the price drifts part of
the way back as the market calms. The expert waits a ball or two, then takes the side that was over-punished, small,
and exits when the price recovers. (PHOENIX tests this as the market-dynamics model, `mkt_dyn`: no edge held out yet.)

### 3. Chase maths and par scores

At the innings break they compare the first-innings total with the venue's par and the chasing side's depth. During
the chase they track runs needed, balls left and wickets in hand (resources), not the scoreboard mood. Markets are
often slow to price a chase that is quietly on track, or one where the tail is exposed. (PHOENIX: the in-play model,
trained ball by ball on Cricsheet; its value against the live price is still unproven.)

### 4. Trade the swing, then lock in (back-to-lay, green-up)

Back a side at 3.0 that they think is underpriced; if it shortens to 2.0, lay part or all of it to lock a profit on
both outcomes ("green up"). They decide the exit before the entry. (PHOENIX: green-up and reduce tickets exist; hedges
may pass the 30-bet ceiling.)

### 5. Pre-match position, then manage it in play

A small pre-match bet only with a real reason (toss and conditions, dew, team news the market has not priced), then
trade out in play. They know most pre-match prices in big leagues are efficient. (PHOENIX: R1 on 1,382 T20 matches
showed our pre-match model is worse than the closing price; pre-match bets are kept as optional by the operator,
era H, and their results are reported apart.)

### 6. Rain and the DLS par

When rain threatens, they know the DLS par score ball by ball; casual money does not, and prices lag.

### 7. Phases and players

Powerplay, middle overs and death overs behave differently; some finishers change a chase's odds more than the
market allows; a part-time bowler forced to bowl the 19th over is a known moment.

### 8. Session ("fancy") markets

Over-by-over and innings run lines are where many professionals make their edge: a runs model from ball-by-ball
history against an in-house line. (PHOENIX: lines are recorded, 224 on one match; no model yet. Section 8.4 of the
rulebook rates it the most realistic untested edge.)

## What they never do

- Bet every match, or force a number of bets (operator, 2026-11-05: 30 is a ceiling, not a target).
- Chase losses, raise stakes after a loss, or "get it back" on the next match.
- Trust a "sure thing" at 1.01 with a big stake.
- Bet on a price they cannot actually get (suspended market, ghost price, the unbettable `_BR` book).
- Use information from the pictures before the market (courtsiding): PHOENIX is 10 to 30 s behind and this is ruled
  out anyway.

## How this maps to PHOENIX

| Expert idea | In PHOENIX today | Evidence so far |
|---|---|---|
| Unit staking, loss limit, edge-sized Kelly | 25,000 per ticket, 100,000 per match (120,000 hard), quarter Kelly | Rules hold (monitor) |
| Only bet above fair price | `min_edge` 3% / 6%; band guards | No variant beats the placebo yet (6 to 13 matches) |
| 1.01 lottery ticket | **Not built** | Cricsheet base rates above; our recordings too few |
| Fade over-reaction | `mkt_dyn`, `mkt_dyn_e06` | No held-out edge yet |
| Chase maths | In-play model (needs a live score) | Unproven against the live price |
| Green-up and hedges | `green_up`, `reduce` | Hedges past 30 were worth +347k in one replay |
| Pre-match then manage | Optional pre-match (era H) | R1: pre-match model worse than the close (1,382 matches) |
| Session markets | Recorded, no model | Untested |

## Candidate stories (operator's choice)

- **Tail bets** (from strategy 1): a variant that backs the outsider in play only when the offered back price is at
  least 1.2 times the fair odds from a Cricsheet base-rate table of the chase state (runs needed, balls left,
  wickets), with a tiny fixed stake (for example 1,000 to 2,500, under 2.5% of the match budget), at most a few per
  match, never laying at such prices. Measured like every variant against the placebo; base-rate tables refreshed
  weekly with the Cricsheet sync.
- **Session-market model** (strategy 8): runs-per-over and innings totals from Cricsheet against LotusBook's lines.
