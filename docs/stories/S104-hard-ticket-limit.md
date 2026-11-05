# S104 — 30 bets per match is a hard limit, reduces included

**Pillar:** 3 · **Priority:** P1 · **Tier:** haiku · **Depends on:** S90 · **Wave:** 23 · **Status:** rejected by the operator (2026-11-04)

## User story

As the operator, who places every bet by hand and asked for 20 to 30 bets per match per variant, I want no variant
to exceed 30 matched bets on one match, because each extra ticket is work for a person and pays the spread again.

## Evidence (2026-11-04)

`mkt_dyn_e06` on New Zealand v India (lb_36271322, ODI) made 36 matched bets: 14 new-risk tickets and 22 reduces.
The model kept rating New Zealand above the market while New Zealand drifted from 0.43 to 0.07; each add was
followed by reduces as the Kelly target shrank with the price. The S90 budget blocks only new risk; reduces were
never blocked, so the count passed 30 (the monitor's limit is 35 and failed at 36). Most of those reduces were
rebalancing (a smaller target on the same side), not needed to keep the worst case within the cap: every fill is
already sized within the cap and the hard limit is checked at fill time.

## Acceptance criteria

1. `strategy/model_edge.py` `decide`: when `position.trades >= variant.ticket_budget`, return no order, **unless**
   the worst case is beyond the cap (`position.worst > cap + 1e-6`, with `cap = variant.match_cap(in_play)`); then
   only a risk-reducing ticket (reduce or green-up, never enter, add or flip) is allowed. Below the budget nothing
   changes (the S90 reserve still keeps the last `ticket_reserve` tickets for reduces and green-ups).
2. The placebo path (`placebo_ticket`) and the market-only control follow the same limit.
3. Update the module docstring's "Ticket budget (S90)" section: the budget is a hard limit on all tickets; past it only
   a ticket that brings the worst case back within the cap is allowed.
4. Tests in `tests/unit/test_s104_hard_ticket_limit.py` (pure, no database): at 30 trades with the worst case inside
   the cap, a strong hedge signal and a target signal both give no order; at 30 trades with the worst case beyond the
   cap a reduce is still made; at 28 trades (reserve) an add is blocked and a reduce is made; at 27 an add is made;
   the placebo stops at 30. Existing S90 tests that expected a reduce beyond the budget are updated to the new rule,
   each change named in the report.
5. Replay before and after with the same pinned `--to` (`scripts/replay_paper.py`): the maximum matched bets per
   variant-match is at most 30 after the change; report P&L, CLV and bets per variant before and after.

## Outcome

Built and tested, then reverted. In replay to 2026-11-04 12:00 every strategy variant topped out at exactly 30
bets, but the 37 bets past the 30th (35 reduces, 2 green-ups) had been worth +347k, so most variants' P&L fell.
The operator chose to keep hedges past 30. Instead, the monitor's `paper_sanity` check now counts new-risk bets
(enter, add, flip) against the 30-ticket budget (FAIL above 30) and reviews a variant-match above 45 matched bets
(WARN).
