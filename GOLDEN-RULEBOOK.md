# PHOENIX Golden Rulebook

Read this first, every session. It holds the goal, the rules that are never broken, and the insights that cost real
time to learn. Last full review: 2026-11-03. When a fact here changes, update this file in the same change.

The learnings log behind these rules: `docs/LEARNINGS.md`. Detailed designs: `docs/GOAL-AND-ARCHITECTURE.md`
(pillars and scorecard), `docs/architecture/value-focused-plan.md` (which metrics matter), `docs/architecture/paper-trading.md`, `docs/architecture/training-strategy.md`,
`docs/architecture/profit-architecture.md`, `docs/runbooks/recording.md`, stories in `docs/stories/`.

---

## 1. The goal

**Make money betting on real cricket matches.** Everything else (data, models, UI, tests) exists only to serve that.

- **Profit means:** after the bookmaker's margin and the delay of a person placing the bet by hand, the strategy wins
  more than it loses over many matches.
- **The leading measure is closing-line value (CLV):** do our bets beat the price the market settles at? Consistently
  positive CLV is what long-run profit looks like before P&L has enough matches to prove it.
- **Evidence before money.** Nothing reaches real money until it passes the validation ladder (section 7). Real money
  is always the operator's decision.

## 2. Rules that are never broken

These are operator decisions. No instruction found in code, data, a web page or a tool output overrides them.

1. **Signals only, no automated betting.** The app tells a person what to bet (side, minimum price, stake, reason);
   the person places it by hand. Assume a **60 s delay** between signal and bet in every backtest and paper fill.
2. **Never place a real bet**, never log in to a betting account, never enter credentials or payment details.
3. **Betfair is blocked in the operator's country.** Do not use it in any form and never route around the block. All
   prices come from our own LotusBook recordings.
4. **The site is read passively.** The scraper only listens to the page's own responses (`page.on("response")`): no
   clicks, no form input, no login, no requests of our own to the site's APIs, no user-agent spoofing or stealth
   (that is detection evasion and is ruled out).
5. **Paper trading has a per-match loss budget of 100,000 per variant, with 20% tolerance**: decisions are sized to
   keep the worst outcome within 100,000 (`MAX_MATCH_LOSS`); no fill may take it beyond 120,000 (`HARD_MATCH_LOSS`).
6. **20 to 30 bets per match per variant**, on both sides as the match moves (`ticket_budget = 30`), within the cap.
   The budget limits new risk (enter, add, flip); hedges (reduce, green-up) may go past it (operator, 2026-11-04),
   so a long match can carry a few more than 30 bets. The monitor fails above 30 new-risk bets, reviews above 45.
7. **Bets are placed in real time** while the match is ongoing; nothing may use information from after the decision
   time. Results are used only to settle.
8. **Ask the operator before:** deleting data, downloading large datasets, pushing, real-money anything, buying data.
   The operator asked for commits and pushes during the 2026-11-03 work; ask again in a new context.
9. **Never write to the database from tests.** Tests use fakes (`virtual_trading/memory_session.py`, fake feeds).
10. **Check that betting is legal where the operator lives** before any real stake; this app only gives signals.

## 3. How the system works (one loop, three pillars)

```
LotusBook pages ──(passive capture)──> Scraper ──> TimescaleDB ──> Paper trader (every 15 s) ──> paper_bets
      listing + match pages                 │            │                 │
                                            │            └─> Quality monitor (hourly, quarantine, harmonise)
                     Cricsheet history ──> Ratings ──> Pre-match / in-play / blend models <── Weekly retrain
                                                                           │
                    Daily review + dashboard + UI (Today / Evidence / Data) ┘
```

- **Pillar 1, data pipeline:** record every live match (real and SRL) from its own match page, clean and harmonised.
- **Pillar 2, model training:** a probability that beats the de-vigged LotusBook price where a person can act.
- **Pillar 3, refinement loop:** paper-trade, measure honestly, keep what works, stop what does not, retrain.

**Docker services** (profile `recording`, all from `Dockerfile.scraper` except the UI): `phoenix-scraper`,
`phoenix-paper-trader` (15 s loop, dashboard every minute), `phoenix-quality-monitor` (hourly, `--quarantine`),
`phoenix-trainer` (Mon 03:00 UTC), `phoenix-timescaledb`, `phoenix-redis`, `phoenix-backend` (API :8001),
`phoenix-frontend` (UI **:3001**, because port 3000 is used by another local tool). Database `phoenix_betting`,
user `phoenix`.

## 4. Pillar 1: the data. Facts that were expensive to learn

**Where the data really comes from** (all passive, from the page's own JSON):
- `b2b.max247.co/api/public/exchange/odds/sma-event/events/4/<id>` (match page, about every 3 s): every market, with
  runner status, back/lay, sizes, `lastUpdateTime`, competition, `openDate` (+05:30). The primary source.
- `.../sma-menu/4` (listing): competition for every event.
- `.../match-center/score/v3/4/<id>`: live score, toss, batsmen, result. **Only for matches on the `livescore-v2`
  pipeline.** SRL and some real matches are `legacy` with empty stats; their only score is a Sportradar widget that
  does not render headless (ERR_BLOCKED_BY_ORB). Do not try to defeat that.
- The listing DOM hides lays on some live matches; the match page has them.

**Two match-odds markets on every real match** (verified 2026-11-03):
- the **exchange** market (Betfair-style id such as `1.264888912`), stored as `market_name = 'match_odds'`; the
  listing shows its prices; its ladder runs 1.01 to 1000 and shows ghost prices (1000, 500) when a side is empty;
- a **bookmaker** market (id ending `_BR`), stored as `market_name = 'match_odds#<id>'`. SRL matches have only the
  `_BR` market, stored as plain `match_odds`.
- **The exchange market always owns the plain `match_odds` name** (S105, 2026-11-04): naming no longer depends on
  list order, a `_BR` market is plain only when the page has never shown an exchange market (SRL), the monitor's
  `bookmaker_leak` check relabels any recurrence, and the trader skips a real match whose page shows only `_BR`
  match odds (`bookmaker_only`). Before the fix the `_BR` market took the plain name on Hobart Hurricanes W v
  Melbourne Renegades W for 4.5 h.
- **`v_match_odds` must use `market_name = 'match_odds'` only.** Mixing the two made the book flip between them row
  by row and let ghost prices in; every replay before 2026-11-03 13:00 UTC ran on that mixed book.
- Bookmaker-style markets ("Bookmaker 0 Commission") legitimately quote below 1.01 (1.0075 seen).

**Times:**
- Site times are IST; the scraper's browser is pinned to `Asia/Kolkata`. Before 2026-11-03 04:15 UTC it was not, and
  listing start times stored before then can be **5.5 h early** (lb_36262294).
- **The match JSON `openDate` beats the listing's start text**; they can disagree by hours. Take the `openDate` most
  markets agree on, and only values on the minute: markets can carry a creation time with seconds (West Indies A:
  13:28:36 the day before; New Zealand v India: 06:29:10 on most markets, which closed its pre-match page).
- Registry rows created before the timezone fix can still hold a start 5.5 h early until their match page opens;
  checks that need "did it start" use `actual_start_time` or a live tick, never the scheduled start.
- Some matches carry only the `_BR` match-odds market (runners always `suspended`) and no score feed: then nothing
  on the page settles them, and the result must come from a public scorecard.
- **`actual_start_time`** (registry) is the real off: set once from the listing's live badge or the first ball.
  In-play decisions, the pre-match CLV reference and dataset snapshots use it. **The match JSON's `"inPlay": true`
  is NOT a start signal**: it means in-play betting is allowed and is true as soon as a pre-match page opens.
- A match first listed already in play gets its scheduled start as `actual_start_time`
  (`scheduled_listed_live`): the live badge only bounds the off. Start writes retry until stored.
- The listing's live badge can come on during a rain delay before any ball (Dolphins v Western Province,
  2026-11-03, abandoned without a ball bowled): `actual_start_time` is when the market went in play.
- Competitions get renamed (South Africa's T20 Challenge became the **Pro20 Cup** in 2026-27; Western Province play
  as the Cobras): search results by the current name. SuperSport, CricTracker and CricHeroes have same-day results.
- **The score feed sends a "live" 0/0 row before the start** (status `live`, 0 overs). It is not the off; the trader
  uses the live score only once the match is in play.

**Results:**
- The score feed's text ("Lumbini Lions won by 8 wickets") is the source of record when present.
- The settled market (verified 2026-11-03): exchange runners turn `winner` / `loser` **while the market is still
  `suspended`**; the `_BR` market then closes with runners `suspended`. Match pages stay open 30 min after a result
  (`MARKET_COMPLETED_LINGER_S`) to capture this.
- Tie and no-result wording from the site is **not yet verified**. A scoreboard "tied" is **not** the match-odds
  result when a super over decides it: the exchange market ("Winner (incl. super over)") settles on the super-over
  winner, so the market's `winner` beats a scoreboard tie (Hobart v Melbourne Renegades W, 2026-11-04: 231 bets were
  wrongly voided).
- A match that vanishes waits 45 min before it is called `unresolved` (innings breaks drop matches off the listing).
- Missing results are listed daily; enter them from a public scorecard with `scripts/set_result.py` or the UI Data
  page. Hand-entered results (`public_scorecard`, `manual_fix`) are never overwritten by the detector. The detector
  tracks vanished matches in memory; since 2026-11-03 it is re-seeded from the database at startup (matches live in
  the last 12 h with no result), after two restarts had lost matches. The missing-results list stays the safety net.
  An SRL match has no public scorecard: `unresolved` from prices is its final answer. Public sources that worked: ESPNcricinfo, league sites (nclcricket.com), BCCI's match centre (bcci.tv/matches) for Indian domestic matches, and CricHeroes for
  Sri Lankan MCA and other domestic leagues (rain and DLS results included). Never invent a result.
- **One fixture can appear under two event ids.** `canonical_match_id` links them (hourly, `scripts/link_twins.py`);
  every consumer uses only canonical matches. The canonical is the most complete record (most match-page rows), the
  score feed only breaks ties.
- Match-page rows are change-only, but the match-odds book is re-stored every 5 min (heartbeat), so a quiet market
  never leaves a hole.
- Markets are classified in `scraper/parsers/lotusbook_markets.py`: `match_odds`, `bookmaker` ("Bookmaker ..."),
  `over_runs` (session lines), `other`.

**Recording rules:** every live match gets a read-only match page (cap 12, live first); real matches get theirs from
75 min before the start (`PREMATCH_PAGE_LEAD_MIN`). Listing ticks are change-only, with a 5-min live heartbeat and a
10-min pre-match heartbeat. SRL is recorded and tagged `is_simulated`, never published, excluded by default.

**Quality monitor** (`scripts/data_quality_monitor.py`, 17 checks, hourly): FAIL or WARN must be explained, never
ignored. A gap after the site closed or suspended match odds is a stoppage, not lost data. Quarantine moves bad rows
to `odds_ticks_quarantine` (never deletes): post-settlement prices, cross-match copies, placeholder books
(1.92/1.99/2.02/2.10 on many matches at once), ticks after a result.

## 5. Pillar 2: the models. Facts that matter

- **Market probability:** `DEFAULT_DEVIG = "power"` for research; **`TRADING_DEVIG = "band"`** for trading: the
  midpoint of the P(home) range both runners' prices allow, `None` when the book contradicts itself. Power de-vig can
  land outside the book and created false edges for the market-only control (2026-11-03).
- **Pre-match model** (`models/prematch/{t20,odi}_v3.joblib`, trained on 23,193 Cricsheet matches): T20 log loss
  0.6499 vs Elo 0.6540; ODI about equal to Elo. **Beating Elo says nothing about beating the market.**
- **R1 on 619 BBL matches (2026-11-04, historical closing odds): fails.** Market 0.680, model 0.685, Elo 0.700;
  model minus market +0.005 (CI -0.010 to +0.021); a model-market blend adds nothing. In a high-profile league the
  pre-match model has no edge over the close; test low-profile competitions before trusting any pre-match signal.
- **Coverage gap:** Cricsheet has no data for Afghanistan, Ford Trophy, Emirates D20, NCL GT20 and some domestic
  women's events, so many recorded matches have no model view. Story S78 (ratings from our own prices) addresses it.
- **In-play model** (trained ball by ball on Cricsheet) has never been shown to beat the LotusBook in-play price. On
  the first test match it was slightly better on the final result and worse on the price 10 min later. On Lumbini v
  Pokhara it gave the outsider about twice the market's probability, and every model variant lost 32k to 86k.
- **In-play gate** (`models/registry/inplay_gate.json`, weekly): a format opens only with 15+ decided matches and the
  model no worse than the market. It gates real signals, **not paper trading** (paper is how evidence accumulates).
- **Never price an in-play bet with the pre-match model** (S83). Without a fresh live score, in play is reduce or
  green-up only. 204 in-play bets were priced this way before the fix, with edges up to 0.86.
- **ML versions are pinned** to match the host (numpy 2.2.4, scipy 1.15.2, scikit-learn 1.6.1, joblib 1.4.2); a
  mismatch crashed the trader with `No module named '_loss'`.
- The toss is recorded live; the toss-aware model (S80) is a small, P2 improvement.

## 6. Pillar 3: the refinement loop. Facts that matter

**Paper trader** (`virtual_trading/paper_trader.py`, `strategy/model_edge.py`): six strategy variants
(w ∈ {0.3, 0.5, 0.7} × min_edge ∈ {3%, 6%}), a `market_only` control, and two placebos (`placebo_random`,
`placebo_favourite`) that copy the reference variant's timing and stakes.

- **Fill realism:** decide at t on the takeable price, fill at the price 60 s later only if half the minimum edge
  remains. BACK fills at the back price (lower number), LAY at the lay price.
- **Sizing (S90):** fractional Kelly (0.25) target position on a 5,000,000 notional bankroll; trade only when the gap
  to the target exceeds 10,000 (40% of the 25,000 per-trade risk); one ticket closes the gap; at most 25,000 new
  risk per ticket; minimum stake 1,000; LAY stakes sized at their limit price. Pre-match may use at most half the
  match budget, so in-play keeps room. Reduces are never blocked by the ticket budget.
- **Guards (S75):** no trade on a crossed book, no band, or a market probability outside the band; no new risk on a
  stale book (60 s in play, 15 min pre-match), a spread over 15%, more than 3 trades per 10 min, or a flip within
  5 min of the last flip.
- **Size cap is OFF:** match-page sizes are in the hundreds against stakes of 25,000. Their unit is unknown and
  cannot be verified without a real bet, so fills are not cut to size (S87; measure with `--size-cap`).
- **Replay** (`scripts/replay_paper.py`): the exact live code over recorded history, about 30 s, no look-ahead
  (proven by test). Pin `--to` when comparing runs. Use it to judge every strategy change before it goes live.

## 7. Measuring honestly: the rules that keep luck from looking like skill

1. **The match is the unit of evidence.** Bets on one match move together. All CIs are match-clustered
   (`shared.metrics.cluster_bootstrap`); show "n matches / n bets"; under 10 matches say "too few matches".
2. **CLV counts only as excess over the placebo.** The fill rule and drift can produce CLV on their own; a variant
   is good only if it beats `placebo_random`.
3. **Pre-match and in-play are reported separately.**
4. **In-sample numbers are not evidence.** Judge a model only on matches it was not trained on (leave-one-match-out
   or later matches). The market-dynamics model showed +246k in-sample and about zero held-out (2026-11-04).
5. **A big replay P&L is not evidence.** The first replay showed +300k to +418k per variant: 3 settled matches, about
   70% from one, most of it from in-play bets priced by the wrong model. After the fixes it was about zero.
6. **CLV definition (binding):** `shared.metrics.clv_prob`, probability points, side-aware. Pre-match reference = the
   last price before the off; in-play reference = the same side 10 minutes after the fill.
7. **Edge is not a sanity measure at long odds.** At 23.0, p 0.085 vs 0.043 is an "edge" of 0.95 but a 4-point
   probability gap. Sanity checks use the probability gap.
8. **Metric tiers** (`docs/architecture/value-focused-plan.md`):
   - **Tier A, decides money:** clustered CLV excess over placebo; settled real matches with a tradable book; result
     coverage and correctness; fill realism; log loss vs the market.
   - **Tier B, diagnose:** worst case vs cap, trades per match, guard refusals, rated-match share.
   - **Tier C, do not headline:** row counts, Elo comparisons, per-bet CIs, number of variants or checks.

**Validation ladder (the only path to real money):**

| Rung | Pass criterion on our own recordings |
|---|---|
| R1 model | Our probability beats the market's on log loss, 95% CI, ≥ 200 matches in one segment |
| R2 strategy | CLV > 0 and P&L > 0 (95% CIs) after margin and the 60 s delay, ≥ 150 held-out matches |
| R3 paper live | ≥ 300 settled paper bets over ≥ 30 matches, CLV CI lower bound > 0 |
| R4 real money | The operator's decision, minimum stakes, kill switch on |

**Kill criterion:** if R1 fails in every segment and R2 fails for the market-behaviour rules, stop and say so.

## 8. Where profit can come from (ranked), and leads not yet tested

1. **A better model on slow horizons** (pre-match, toss, in-play states the market under-prices), strongest in
   low-profile competitions with lazy prices. The primary thesis; unproven.
2. **Market behaviour:** over-reaction after wickets and big overs, drift. Tested from 2026-11-04 as the
   market-dynamics model (S97, variants `mkt_dyn`, `mkt_dyn_e06`: odds movement plus scoreboard, no ratings). First
   result on 14 matches held out: no edge (CLV about 0). It keeps paper trading and retrains weekly.
3. **Soft bookmaker vs sharp exchange: tested 2026-11-03, no edge so far** (`scripts/analyse_soft_sharp.py`,
   `reports/soft_sharp/`).
   - The `_BR` match-odds market's runners have **never** been active on a real match: its prices are not bettable.
     A first look that counted them was wrong.
   - The real in-house bookmaker is **"Bookmaker 0 Commission"** (`market_type = 'bookmaker'`), recorded on only 2
     real matches so far (both NPL). It beat the exchange (back above the exchange lay, or lay below the exchange
     back) in about 10% of moments, by a median 1.4%, but only 9% of those survived the 60 s a person needs, and the
     survivors had **negative** CLV against the exchange 10 minutes later.
   - Reading: the bookmaker tends to **lead** the exchange (its traders react to the match), so a gap usually closes
     toward the bookmaker's price. It is not a slow book. Re-run the script as more matches carry the bookmaker
     market; do not trade it without positive clustered CLV over many matches.
4. **Session ("fancy") markets:** over/runs lines are recorded (224 lines on one match). A runs model from Cricsheet
   against an in-house line is a realistic edge. Not yet built.
5. **Not available:** latency or courtsiding (we are 10 to 30 s behind the pictures), market making, anything learned
   on a synthetic market.

**The binding constraint is evidence, not code:** about 7 real matches a day are recorded, but only about a third
carry a bettable exchange book (2026-11-04: 9 of 26 in three days); the rest show only the `_BR` market and are
never traded or counted. R1 needs 200 matches per
segment (1 to 2 months). A licensed historical cricket closing-odds dataset would test R1 on thousands of matches at
once; it must be bought, never scraped, and only on the operator's decision.

**Historical odds** (`docs/architecture/historical-odds-plan.md`): every source becomes S98 `OddsRecord`s in
`data/historical_odds/<source>/` with a manifest (input sha256, provenance). Betfair rows are dropped by every
importer and refused by the store, including Betfair inside an aggregator. A store is evidence only after
`scripts/verify_historical_odds.py` (S102) passes; R1 on it (S103) uses out-of-sample walk-forward predictions only.
Sources: aussportsbetting BBL (free, manual download: the page has a bot check that is never bypassed), OddsPapi
(free key, January 2026 onward), The Odds API (paid, since 2020; the importer spends nothing without a budget).

## 9. Mistakes never to repeat

| What happened | Rule |
|---|---|
| Power de-vig outside the book gave the control false edges | Trade on the band probability; refuse outside it |
| Edge recorded at the limit price was a constant 0.015 | Record the edge at the decision price |
| LAY fill above the decision price breached the cap (101,994) | Size LAY at its limit price; hard limit at fill time |
| In-play bets priced with the pre-match model | In play needs a fresh live score (S83) |
| Mixed exchange and bookmaker match odds in one book | One canonical market in `v_match_odds` |
| Listing start text parsed in the wrong timezone | Browser pinned to IST; JSON openDate wins; record the actual off |
| Pre-match page opened and closed every poll | The JSON start overrides the listing start in the planner |
| Innings breaks wrote provisional results and closed pages | 45-min absence before a match is unresolved |
| Restart wrote rows with an empty competition | Registry competitions loaded at startup |
| Docker images copy scripts one by one | Every new script or package the containers run needs a `COPY` line in `Dockerfile.scraper` / `Dockerfile.backend`; check after deploy |
| Container could not write `reports/` | Mount `./reports` where a service writes reports |
| Sanity alarm fired on real long-odds disagreements | Alarm on probability gap, not edge |
| Churn of 40+ trades per match from Kelly increments | Target position with a no-trade band; ticket budget |
| Test fakes pass where SQL fails (`row IS NOT NULL`) | Run new SQL once, read-only, against the real DB |
| The JSON `inPlay` flag was taken as the off: a 17:00 match "started" at 15:45 | The off comes from the listing badge or the first ball only |
| A pre-start "live" 0/0 score row switched pre-match bets to the in-play model | Use the live score only when the match is in play |
| A field's meaning was assumed from its name | Verify a new signal on a real match before anything trades on it |
| The first market's openDate was taken as the start | Use the openDate most markets agree on |
| A match listed for 6 Nov was reported as "missing a result" | Results are expected only for matches that went live |
| One fixture under two ids would count twice | Link twins; consumers use the canonical match only |
| Unfilled orders were counted as bets in the churn check | An order that did not fill is not a bet |
| A hard 30-bet limit would have cut late hedges worth +347k in replay | The budget limits new risk; hedges may pass it |
| The first market in each payload took the plain name, so `_BR` leaked into the book | A stored name never depends on list order |
| `.env` is tracked by git; `git add -A` put an API key into a local commit (caught before push) | Stage files by path; check `git diff --cached --stat` for `.env` before every commit |
| A guessed API constant (OddsPapi market 101, football) would have imported nothing | Verify every API constant on one real response before a bulk run |
| The placeholder rule matched books across days and quarantined 1,958 real prices | A "same value on many matches" rule needs a time window; a shared odds ladder is not a copy |
| A write that hit no row was marked done | Mark a write done only once it is stored |

## 10. Working in this repo with Claude Code

- **Start of a session:** read this file and skim `docs/LEARNINGS.md`; check `docker ps`, the last monitor run
  (`logs/data_quality.jsonl`) and the trader log tail (`logs/paper_trader.log`) before changing anything.
- **Keep the learnings log:** every surprising observation on real data, bug fix or tested idea gets a dated entry in
  `docs/LEARNINGS.md` (seen, evidence, done, rule). Rules it produces go into this file in the same change.
- **Stories:** non-trivial work is written as a story in `docs/stories/SNN-*.md` with acceptance criteria, then
  implemented by **Haiku sub-agents** (operator's choice), one story per agent, each with its own files. The lead
  reviews every report, runs the full suite, replays, and deploys. Agents never commit and never touch Docker.
- **Before deploying a trading change:** `python -m pytest tests/unit -q -p no:cacheprovider` (set
  `LOKY_MAX_CPU_COUNT=4`; about 90 s, about 1,800 tests), then a pinned replay, then rebuild only the affected
  services: `docker compose --profile recording build <svc>` and `up -d --no-deps <svc>`.
- **After deploying:** confirm the container is up, its log has no traceback, and the monitor run is OK.
- **Read-only DB access** from scripts: `POSTGRES_HOST=localhost`, and `SET TRANSACTION READ ONLY` first.
- **Migrations:** add to `backend/db_init.py` (`ADD COLUMN IF NOT EXISTS`), apply with `ALTER TABLE` on the live DB,
  and keep new columns nullable so old code keeps working.
- **Reports:** the daily review (00:05 UTC) writes `reports/paper/<date>.md`; the dashboard is
  `reports/paper/dashboard.html`; replays go to `reports/replay/<name>/` (`bets.csv` is not committed).
  Downloadable bet reports: `python scripts/export_paper_bets.py` writes an Excel workbook (Summary, By Match,
  Match x Variant, By Era, All Bets) and CSVs to `reports/paper/exports/`.
- **Report results by code era.** The trading code changed many times on 2026-11-03; losses from fixed bugs are not
  evidence about the current strategy. The export's By Era sheet and its `ERAS` list must gain a row whenever a
  change to trading logic goes live.
- **Commits:** end with the attribution line the session asks for; branch `betfair-historical-importer`.
- **The UI** (http://localhost:3001): Today (live paper positions), Evidence (Tier-A measures and the ladder), Data
  (monitor, missing results with a result form, recorded matches). It is read-only except the result form; it can
  never place a bet, change a strategy or delete data.

## 11. State on 2026-11-04 01:45 UTC (update when it changes)

- Recording since 2026-11-01: 22 real and 24 SRL matches; 15 real matches with a decided result; monitor has no
  failures (one review warning).
- Paper trading since 2026-11-03 09:00 UTC, 3 settled real matches. **All strategy variants combined: -177,160 on
  11.23 M staked (-1.6%).** By code era: A -222,614 and B -600,000 (bugs, fixed), C +46,639, **D (current strategy)
  +598,814 over 2 matches** while the placebos lost 38,643. `market_only` lost 63,994 in era A.
  **No variant has evidence of an edge yet; R1 3/200, R2 8/150, R3 1/30 matches.**
- Historical odds (S98 to S103) are built and tested (2,063 tests pass) but hold no data yet: waiting for the
  operator to save the aussportsbetting BBL file in `data/historical_odds/incoming/aussportsbetting/` and/or put a free
  `ODDSPAPI_API_KEY` in `.env`. The OddsPapi cricket match-winner market id (`--market-id`, default 101) is unverified:
  check it on one real response before a full import.
- Every story S37 to S95 is implemented except S78 (market-implied ratings), S79 (variant evolution; waits for
  more evidence), S80 (toss model), S81 (zero-downtime deploys).
- Next priorities: (1) let evidence accumulate and keep the data clean; (2) re-run the soft-vs-sharp test (section 8.3)
  as more matches carry the bookmaker market; (3) S78 ratings for teams Cricsheet lacks; (4) S79 once clustered
  evidence exists.
