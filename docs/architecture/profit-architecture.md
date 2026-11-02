# PHOENIX v2: architecture for real-money profit on real cricket matches

Status: proposal, 2026-11-02, branch `betfair-historical-importer` @ `a902981`.
It replaces "PPO on synthetic markets" as the core strategy. Stories S35–S42 implement it.

## 1. What we know (measured, not assumed)

| Fact | Evidence |
|---|---|
| No strategy has ever been tested on a real price | Every corpus is synthetic. S18 recorded-odds evaluation: n=0 |
| The synthetic market pays no-skill policies, so its results say nothing about skill | S18: `random` odi_men CI low +4,276/match. Implied home p 0.39 vs actual 0.52 (S22) |
| We own almost no real data | `odds_ticks`: 4,046 LotusBook ticks over 17 matches since 2026-08-22, 3,563 of them from one match. 2 matches have both ticks and a result |
| Part of what we did record is not real cricket | Recorded and result ids include "SRL" matches (Simulated Reality League: computer-simulated games, e.g. `Hobart Hurricanes SRL`) |
| Synthetic ticks share the real-tick table | `odds_ticks.source='cricsheet_official'` rows (`cric_odi_*`) sit next to LotusBook rows |
| We only see one market, and it is a mirror | The scraper reads the LotusBook match-odds grid (`scraper/worker.py`), which on this kind of site mirrors Betfair exchange prices. No bookmaker or session ("fancy") markets are captured |
| Our information is slow | 3 s polling (median gap 3.2 s), plus a deliberate 5–15 s placement delay, plus a scorecard that lags TV pictures. Betfair adds a ~5 s in-play bet delay on its side |
| A Betfair historical importer already exists | `scraper/parsers/betfair_historical.py` parses stream files (`marketDefinition`, `ltp`, `batb/batl`, `WINNER`), but no files were ever downloaded |

## 2. Where real profit can and cannot come from

Ordered by how plausible each source is for **this** system:

1. **Information we model better than the crowd, on slow horizons.** This means a pre-match and in-play
   win-probability model built on what the market under-uses:
   - player-level and team strength ratings from 20 years of Cricsheet ball-by-ball data;
   - announced XIs at the toss;
   - venue and chase effects;
   - format and gender specifics;
   - low-profile competitions where liquidity is thin and prices are lazy.
   The edge, if it exists, persists for minutes, not seconds, so being slow is acceptable.
   This is the **primary thesis**.
2. **Market behaviour.** Possible over-reaction after wickets, boundaries and big overs, and drift in long markets
   (Tests, ODIs), testable directly on real price histories without any match state. This is the **secondary
   thesis**, and it is cheap to test once S35 exists.
3. **Soft-book vs sharp-line value:** betting a slow bookmaker when its price lags the exchange. This is the classic
   retail edge, but it needs a second, genuinely independent price. LotusBook's match odds are a mirror, so it is
   unavailable with today's data; it is a backlog item if a bookmaker-style market is ever recorded.
4. **Not available:**
   - Latency or courtsiding: we are 10–30 s behind the pictures.
   - Market making or spread capture: our slow information means we would be picked off on every ball.
   - Anything learned on the synthetic market (S22 evidence).

**What happens to RL:** PPO stops being the alpha source. It stays as a challenger behind a flag and is promoted only
if it beats the value strategy on real prices (§4).

## 3. Target architecture

```
Real data layer (S35, S36, S37)
  Betfair historical files ──► real-price corpus (prices + result)  ──┐
  Cricsheet ball-by-ball  ──► conservative time alignment (S36) ─────┼─► state-enriched real corpus
  Live recorder (LotusBook now, Betfair Stream later, S41) ──────────┘     (append-only, provenance-tagged,
                                                                             SRL/synthetic excluded)
Model layer (S38)
  Ratings (team/player Elo by format) + state features ─► calibrated FairValue p(home)  (walk-forward)
Strategy layer (S40)
  Strategy interface on_tick(market, state, position) -> order intents
    ValueStrategy: edge = p·price − 1 net of commission and spread, threshold, fractional Kelly,
                   no-bet windows, exits / green-up
    MarketStrategy: rules found in thesis 2 (only if S39 validates them)
    PPOStrategy: challenger, shadow mode only
Execution layer (S39 sim, S41 live)
  Conservative fill model: decide on info ≤ t − latency, fill at t + delay at the opposite best price,
  liquidity cap, commission on net market winnings. No order placement at all: strategies emit manual-bet signals,
  paper-tracked with 60 s manual latency.
Evaluation and safety (S39, S42)
  Walk-forward real-price backtest  →  live paper trading with CLV vs Betfair closing price  →  go-live gate
  Kill switch: daily loss limit, rolling-CLV floor, data-staleness halt
```

## 4. The validation ladder (each rung must pass before the next)

| Rung | Evidence required to pass |
|---|---|
| R0 mechanics | Random and hold policies on **real** prices lose ≈ costs (backtester sanity) |
| R1 model | FairValue v2 beats the Betfair implied probability on Brier / log loss, on walk-forward held-out seasons, in at least one segment (format × phase) with ≥ 200 matches |
| R2 strategy | Value strategy walk-forward on real prices: mean P&L/match 95% CI > 0 after conservative execution, ≥ 150 held-out matches, stable across ≥ 3 seasons |
| R3 paper live | ≥ 300 settled paper bets over ≥ 30 real matches: CLV vs Betfair close, CI lower bound > 0; realised P&L not significantly negative |
| R4 real money | **The operator's decision**, at minimum stakes, with the kill switch on |

**Kill criterion for the whole project:** if R1 fails in every segment and R2 fails for the market-behaviour rules,
there is no demonstrated edge. The honest outcome is then to stop, or to collect more data, not to tune further.

## 5. Decisions that belong to the operator (code cannot make them)

**Decided 2026-11-02:** (1) the operator downloads the Betfair historical files; (2) **no automated betting**: the app emits signals (what, minimum price, stake, why, valid until) and a person places bets by hand. Signals are paper-tracked at the price available 60 s later, and backtests use the same 60 s latency; (3) scraping runs automatically (S37 watchdog plus a Windows scheduled task the operator installs once).

1. **Get real historical prices.** Register at Betfair's historic data site and download cricket Match Odds files.
   The BASIC tier (1-minute last-traded prices) was free at the time of writing (verify); ADVANCED/PRO add the ladder
   and volume at a cost. A few hundred matches across formats is enough to start R0–R2.
2. **The real-money venue.** An exchange with an official API (Betfair) permits automated betting, publishes its
   commission and has real historical data to validate against. Automating a site that does not offer an API (the
   current LotusBook path) typically breaches its terms, and its "anti-detection" placement delay (5–15 s) is pure
   cost to the edge. Legality depends on your jurisdiction; check it before R4.
3. **Live data access for R3:** a Betfair app key (and a timestamped ball-by-ball feed if available) for S41.
4. **Keep the recorder running daily** (S37). Every real match not recorded is lost data.

## 6. Mapping from the old plan

- Another story set (S24–S34, written earlier the same day) overlaps this plan. The merged order and a verdict per story are in `docs/stories/README.md` under "Merged plan". Its Elo, venue and player stories (S24, S25, S29) feed S38; its PPO stories (S26, S30) are shelved.

- Keep: S03 env, S05 risk and sizing, S06 fill guard, S08 DB, S10 streams, S11/S12 parity and cadence (still needed
  for any live model), S14 fair value (becomes the v1 baseline), S15 metrics, S16 export, S17 gate, S19, S20.
- Superseded: S21 → S42. S22 → P2 (synthetic market only for RL research and mechanics tests). S23 → split (historical
  table into S37, fair-value naming into S38; the rest stays P2).
- Shelved: PPO training on synthetic markets (S09/S18 pipeline stays runnable, no further investment).
