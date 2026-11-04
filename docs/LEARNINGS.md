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
