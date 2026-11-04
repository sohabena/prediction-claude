# PHOENIX learnings log

A dated record of what we observed, the evidence, and what changed. Newest first. The **Golden Rulebook**
(`GOLDEN-RULEBOOK.md`) holds the rules; this log holds the story behind them, so a future session can see why a rule
exists and what was already tried.

**How to maintain it.** Add an entry whenever something surprising is found on real data, a bug is fixed, an idea is
tested, or a number changes what we believe. One entry: what was seen, the evidence (match ids, numbers, commit),
what was done, and the rule it produced (if any; copy rules into the rulebook). Keep entries short and factual. Never
delete an entry; if it turns out wrong, add a later entry that corrects it.

---

## 2026-11-04

**OddsPapi verified on real responses** (operator's free key). Cricket is sport 27; the match-winner market is **271**
("Winner (incl. super over)", outcomes 271 = participant 1, 272 = participant 2), not 101 (football's 1X2, the
importer's unverified guess, which would have found nothing); 273 is the 1X2 with the tie (274), 278 the coin toss.
`/v4/historical-odds` is free (not counted) and returned history for about 60% of real fixtures sampled from March to
November 2026: Pinnacle on the earlier dates, 1xbet (low-profile leagues: MCA Super Premier League, PCB women,
Topklasse, Delhi Premier League) and bet365 later. A 404 "No historical odds found" means no prices for that
fixture, not an error; `hasOdds` is false on every finished fixture. About 60% of cricket fixtures are SRL: the
importer now skips them and lists fixtures in 9-day windows (one billable call each). Responses are about 12 KB.

**First R1 test on bought or published odds: the pre-match model does not beat the BBL closing price.** The
operator saved the aussportsbetting BBL file (672 matches, 2011-12 to 2026-01; Odds Portal average for all, bet365
open/min/max/close for 423 from 2018). Verified (S102): 95% linked to Cricsheet once the verifier read Cricsheet's
`all` folder (the four format folders hold no BBL match: 0% linked before), winners agree on 631 of 631, bet365
close calibration slope 0.99 on 417 matches. R1 (S103, walk-forward out-of-sample, 619 matches): log loss market
0.680, our v3 model 0.685, Elo 0.700; model minus market +0.005 (95% CI -0.010 to +0.021); a blend of model and
market +0.003 (CI -0.004 to +0.010); flat backing at the closing price when the model differs by 3 or 6 points:
ROI +4.7% / +6.1% with CIs from about -4% to +14% / +17% (not takeable, direction only). Reading: in a high-profile
league the closing price already holds what our ratings know. The thesis (section 8.1) is untested where it should
be strongest, low-profile competitions with lazy prices; BBL is not that. Rule: beating Elo is not beating the
market, now measured on 619 matches.

**The unbettable `_BR` market leaked back into the trading book.** The parser gave the plain `match_odds` name to
whichever market came first in each payload. On Hobart Hurricanes W v Melbourne Renegades W (lb_36266944) the `_BR`
market took it for 560 rows (06:55 to 11:36), next to the exchange market, so `v_match_odds` served a `_BR` book at
500 runner-timestamps; West Indies A v England Lions got 2 such rows after its exchange market left the page.
Impact checked with the live code: every decision and fill saw the exchange book (the as-of rule picked exchange
rows); recomputing CLV changed 1 of 231 Hobart bets (reference 3.50 to 3.45), so the recorded evidence stands. The
560 rows were relabelled `match_odds#<id>` (no deletion). S105 makes naming order-independent and adds a monitor
check that relabels any recurrence. Rule: a market's stored name must not depend on its position in the payload.

**Most recorded real matches have no bettable book.** In the three days to 2026-11-04, 17 of 26 real matches with
match odds carried only the `_BR` market (Senior Women's T20 Trophy, Emirates D20, ICC League 2 and others); the
trader priced them every cycle and the scorecard counted them as tradable. Now (S105, era F from 13:29 UTC) the
trader skips them (`bookmaker_only`) and the scorecard counts neither tradable nor R1 pre-match prices on them: the
R2 count fell from 22 to 11 settled matches, R1 from 15 to 7. Rule: evidence counts only prices a person can bet;
the binding constraint (matches with a bettable book) is about half of what was thought.

**Offline CLV recomputes are not the live recorder.** Recomputing a reference 10 minutes after a fill that falls
after the result finds stale or ghost prices (2.22, 1000) where the live recorder had stored none; never rewrite
such references from a recompute.

**Reduces broke the 30-bet limit.** `mkt_dyn_e06` made 36 matched bets on New Zealand v India (14 new risk, 22
reduces): it kept rating New Zealand above the market while their price drifted from 0.43 to 0.07, and every add was
followed by reduces as the Kelly target shrank. S90 never blocked reduces, so the count passed the operator's 30.
S104 (built and tested, **not deployed**, waiting on the operator): 30 is a hard limit; past it only a ticket that
brings the worst case back within the cap. Replay to 2026-11-04 12:00 (6 to 13 matches per variant): every strategy
variant then tops out at exactly 30 bets, but the 37 bets past the 30th (35 reduces, 2 green-ups) had been worth
+347k, so most variants' replay P&L falls (w07_e03 +21.6k to -93.3k; mkt_dyn_e06 +220k to +187k); mean CLV barely
moves. Few matches, so this is not evidence of value either way (rulebook 7.5), but late reduces are real hedging,
not just churn. **Operator's decision (2026-11-04): hedges may pass 30.** S104 was reverted; the monitor now fails
only when new-risk bets (enter, add, flip) on one variant-match pass the 30-ticket budget, and reviews a
variant-match over 45 matched bets. Rule: the budget limits new risk, never a hedge.

**Pondicherry v Hyderabad (Senior Women's T20 Trophy) had nothing on the page to settle it**: only the `_BR` market,
frozen at 1.60 / 2.25 for 30 minutes, and no score feed, so it ended `unresolved`. BCCI's match centre
(bcci.tv/matches) had the result: Hyderabad Women won by 7 wickets; entered with `set_result.py`. Rule: for Indian
domestic matches, BCCI's match centre has same-day results.

**Historical odds: what can actually be obtained** (plan `docs/architecture/historical-odds-plan.md`, S98 to S103).
The aussportsbetting.com BBL file is free but its page sits behind a Cloudflare check (HTTP 403 to scripts; "Just a
moment" in a browser pane): it is not bypassed, the operator downloads it by hand. OddsPapi's
`/v4/historical-odds` needs a free key, covers January 2026 onward only, allows 3 bookmakers per call and one call
per 5 s. The Odds API has cricket snapshots since mid-2020 but is paid. Aggregators carry Betfair as a bookmaker:
every importer drops Betfair rows, and the store (`training/historical_odds.py`) refuses them. Rule: one record
contract for every source, verified against Cricsheet results (S102) before any model is judged on it (S103).

**A match first listed already in play never got a start time** (Pondicherry v Hyderabad, Senior Women's T20,
scheduled 03:30, first listed at 04:51 live). The live-badge write ran before its registry row existed, updated
nothing, and the match was marked done anyway. Fix: retry until a start is stored; and a match first seen already
live (no pre-live sighting, scheduled start 10 min to 12 h earlier) records its scheduled start
(`scheduled_listed_live`), because the badge only proves the off was earlier. The backfill's "first live tick" has the
same limit for late-listed matches.

**The churn check counted unfilled orders as bets.** On Central Stags v Auckland Aces (Ford Trophy, 50 overs) a
variant recorded 54 orders but only 33 matched bets (30 tickets plus reduces, which the budget never blocks); 21
orders lapsed in the 60 s delay. The monitor failed on 54. Fix: churn counts matched bets (filled, settled, void);
the control is still judged on every order. Rule: an order that did not fill is not a bet.

**The market-dynamics model (S97) has no edge out of sample yet.** Trained on 14 real matches (4,032 in-play
snapshots, 6 with a scoreboard), it predicts the market probability 10 minutes ahead. Leave-one-match-out: error
0.067 against 0.058 for "no change" (worse), direction right 49% of the time, simulated CLV no better than the
placebo. The replay looked excellent in-sample (+145,899 and +246,321, CLV 3 to 4 points above the placebo) and
collapsed held-out (CLV about 0, P&L -41,763 and -3,819). Rule: never judge a model on the matches it was trained
on; replay with `--dynamics lomo` (or any held-out scheme) before believing a number. Deployed to paper anyway (era E,
02:50 UTC) to learn from live matches; weekly retraining keeps a new version only if it is not worse.

**Clipping a model probability to the market band blocks every trade**: inside the band no takeable price has
positive edge, so a model can only act when it disagrees with the book.

**The site can list one fixture under two event ids** (S96). Otago Volts v Canterbury Kings was recorded as
`lb_36266991` (score feed, left the listing at 23:53 mid-match, marked unresolved) and `lb_75330686` (no score feed,
still live, 13 times more match-page rows). Fix: `canonical_match_id` in the registry, linked hourly by the monitor
(`scripts/link_twins.py`); consumers (trader, dataset, results checks) skip the non-canonical twin; decided results
copy across twins (`twin:<id>`, protected from the detector); check `duplicate_fixtures`. Rule: the canonical twin is
the most complete record, with the score feed only as a tie-break, so the trader follows the live record.

**Change-only match-page rows plus a listing drop left a 6 min 20 s hole** in a quiet live market (`lb_75330686`,
22:46-22:52). Fix: the match page stores an unchanged match-odds book again every 5 min (heartbeat); the duplicates
check ignores those rows. First version had a flaw: a closed row did not reset the heartbeat clock, so a market
that left the page and came back closed was re-stored 216 s after its closed row (lb_76110454, 2026-11-04 02:49;
the monitor flagged 2 duplicates). Fixed: closed rows reset the clock and only open markets get a heartbeat.

**The operator proposed a model that uses only the odds' movement and the scoreboard** (no ratings), betting both
sides as the odds change. Written as S97 (market-dynamics model): it predicts the market probability 10 minutes
ahead and trades predictable moves; profit cannot be ensured and is measured as CLV in excess of the placebo.

**An `openDate` with seconds is a creation stamp, not a start.** New Zealand v India's pre-match page opened at
00:45 (75 min before 02:00) and closed 3 s later: most of its markets carried `openDate` 2026-11-03 06:29:10, so the
majority rule still picked a bogus start, the planner saw the window as passed, and the registry took the bad value.
Fix: only on-the-minute `openDate` values count as a start; registry corrected to 02:00; the page reopened at 01:48
and recorded pre-match rows. Rule: a start time with seconds is never a scheduled start. Confirmed: the match went
live at 02:00:02 (listing badge), with 1,375 match-page rows recorded before the off.

**Paper results so far (export `scripts/export_paper_bets.py`, 01:44 UTC).** Strategy variants combined: -177,160
on 11.23 M staked (-1.6%) over 3 settled real matches; two variants slightly positive (w07_e06 +5,048, w05_e06
+604). By code era: A -222,614 and B -600,000 (both bugs, fixed the same day), C +46,639, **D (current strategy)
+598,814 on 2 matches** against the placebos' -38,643. Rule: always report results split by code era; early-era
losses are bug evidence, not strategy evidence. Two matches prove nothing (R3 needs 30).

## 2026-11-03 (evening and night)

**Missing results found on public sites.** Dubai v Ajman (Emirates D20): "Ajman won by 3 wickets" (CricTracker).
Dolphins v Western Province: "abandoned without a ball bowled" (SuperSport; the competition is now the Pro20 Cup and
Western Province play as the Cobras). Hnb A v Hayleys Group A: "HNB - A won by 8 runs (DLS)" (CricHeroes). Searches
by the old competition name failed. Rule: try SuperSport, CricTracker, CricHeroes and league sites; search by the
current competition name; never invent a result.

**Some matches have no settlement source on the page.** Dubai v Ajman and Dolphins v Western Province carried only the
`_BR` match-odds market (runners always `suspended`) and no score feed; the market closed without a winner. Their
result can only come from a public scorecard.

**Restarts lost live matches in the result detector** (Hnb A v Hayleys Group A; Chennai Super Kings SRL v Rajasthan
Royals SRL never got a result row). The detector kept them in memory only. Fix (830ffa1): at startup it is re-seeded
with every match live in the last 12 h without a result. The SRL match got its row within a second of the next
restart.

**SRL results have no public source**: `unresolved` from prices is final. The monitor reports it, does not fail.

**The scheduled start came from the first market's `openDate`.** On West Indies A v England Lions one market carried
its creation time (2026-11-02 13:28:36 UTC; the match started 17:00). Fix (55440f7): the `openDate` most markets
agree on.

**Checks used the scheduled start to decide "started".** New Zealand v India still had a pre-timezone-fix 20:30
start and was counted as started without a pre-match page; Sudurpaschim Royals v Karnali Yaks (6 Nov) was listed as
missing a result. Fix: checks use `actual_start_time` or a live tick.

**A 21-point model-market gap was real.** West Indies A needed about 14 an over; the in-play model gave England Lions
0.90 against the price's 0.69, and England won. All six strategy variants profited on the match (+31k to +110k),
the random placebo lost 43k, and the result came from the settled market (`lotusbook_settled`), the first real use
of that path. Rule: a large probability gap is a warning to review, not a failure.

**The listing's live badge can come on during a rain delay** before any ball (Dolphins v Western Province).

**Ford Trophy starts were stale by 5.5 h** (registered 16:00 on 2 Nov; real start 21:30). The match JSON corrected
them when their pages opened; all four started at 21:30:01 from the live badge.

## 2026-11-03 (afternoon)

**The JSON `"inPlay": true` is not the off.** West Indies A v England Lions got an "actual start" at 15:45 (its
pre-match page opening) for a 17:00 start; a Ford Trophy match got 15:49 for 21:30. Five variants bet "in play" at
16:23. Fix (99a44c7): the off comes from the listing badge or the first ball; two wrong starts cleared, nine bets
relabelled pre-match. Rule: verify a new signal on a real match before anything trades on it.

**The score feed sends a "live" 0/0 row before the start.** The trader used it to switch pre-match decisions to the
in-play model. Fix: the live score is used only in play.

**Soft bookmaker vs sharp exchange tested** (`scripts/analyse_soft_sharp.py`). A first look said the bookmaker beat
the exchange in 6 to 7% of moments, but that was the `_BR` market, whose runners are never bettable. Against the
real "Bookmaker 0 Commission" (2 NPL matches): opportunities in about 10% of moments, median edge 1.4%, only 9%
survived 60 s, and the survivors had negative CLV. Reading: the bookmaker leads the exchange. No edge so far.

**"Bookmaker 0 Commission" was stored as `other`**, so bookmaker checks skipped it. Reclassified as `bookmaker`;
987 rows relabelled.

**Settled-market wording verified** on Lumbini Lions v Pokhara Avengers: exchange runners turned `winner` / `loser`
while the market was still `suspended`; the `_BR` market closed with runners `suspended`. The parser required a
CLOSED market and would have missed it; fixed. Match pages now stay open 30 min after a result.

**Lumbini v Pokhara: every strategy variant lost (32k to 86k).** The in-play model rated the outsider about twice the
market's probability during the chase, and the market was right. One match; S89 measures this over time.

**Monitor false alarms fixed:** an "edge" over 20% at long odds (p 0.085 at 23.0 is an edge of 0.95 but a 4-point
gap: now a probability-gap check); bookmaker prices below 1.01 (1.0075 is real); a gap after the site closed match
odds (a stoppage, not lost data); a no-result counted as an empty winner.

**Operator decisions:** 20 to 30 bets per match per variant (ticket budget 30); 20% tolerance on the 100,000 budget
(no fill beyond 120,000).

## 2026-11-03 (morning and midday)

**The first replay's profit was an illusion.** +300k to +418k per variant over 3 settled matches, about 70% from one
match, much of it from in-play bets priced by the pre-match model. After S83 (in play needs a live score) it was
about zero. Rule: the match is the unit of evidence; report CLV as excess over a placebo.

**The price view mixed two markets.** Real matches carry an exchange match-odds market (`match_odds`) and a `_BR` one
(`match_odds#<id>`); `v_match_odds` took max prices across both, so the book flipped between them and let ghost
prices (500, 1000) in. Fix: one canonical market. Every earlier replay ran on the mixed book.

**Power de-vig landed outside the book** (Janakpur 25.5% against a 23.1 to 24.4% band) and the market-only control
traded on false edges for an hour (-63,994). Fix: trade on the band probability.

**A LAY fill above its decision price breached the cap** (101,994). Fix: size LAY at its limit price; later a hard
limit at fill time.

**Churn**: 30 to 48 fills per variant on one T20 match from Kelly increments on a noisy probability. Fix (S90):
target position with a no-trade band and a ticket budget.

**Match-page sizes are in the hundreds** against stakes of 25,000; the unit cannot be verified without a real bet, so
fills are not size-capped (S87 measures it).

**Listing start times were 5.5 h early** before the browser was pinned to IST; the JSON `openDate` and the actual
off now take precedence. A pre-match page opened and closed on every poll until the JSON start was made to beat the
listing start in the page planner.

**Placeholder books** (1.92/1.99/2.02/2.10 on many markets at once) appear when markets open; they are quarantined.

**Infrastructure:** Docker images copy scripts one by one (missing copies broke the monitor and the trader twice);
containers need `./reports` mounted to write reports; port 3000 is taken by another local tool, so the UI is on 3001;
ML versions must match the host (`No module named '_loss'`).
