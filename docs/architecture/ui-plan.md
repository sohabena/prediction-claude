# UI plan: keep what helps the operator make profit

Status: plan, 2026-11-03. Goal and pillars: `docs/GOAL-AND-ARCHITECTURE.md`. Stories S91–S95.

## What the UI is today

A Next.js app (`frontend/`, 7 pages, about 3,700 lines) on a FastAPI backend (`backend/`). Neither runs: both are in
the `dev`/`prod` Docker profiles, while the system runs in `recording`. Almost every page serves the retired
reinforcement-learning agent, not the paper trader that now does the work.

| Page | What it shows | Serves the goal? | Decision |
|---|---|---|---|
| Dashboard `/` | PPO timesteps, episodes, Sharpe, agent bets | No: the RL agent is retired | Replace with **Today** |
| Training `/training` | Policy loss, value loss, entropy, episode reward | No | Remove |
| Virtual Trading `/trading` | The RL agent's bets (`/agent/bets`) | No: paper bets live in `paper_bets` | Remove (Today replaces it) |
| Graduation `/graduation` | RL graduation progress | No: the validation ladder R1–R3 replaces it | Remove (Evidence replaces it) |
| Advisor `/advisor` | Lifecycle, specialist model zoo, shadow trading, demote button | No | Remove |
| Live Matches `/matches` | Match list, scrape approval toggles, manual result, **delete match data**, Cricsheet sync | Partly: the list and manual result help; approvals are obsolete (every match is recorded), one-click deletion is dangerous, Cricsheet sync is weekly and automatic | Keep the list and manual result inside **Data**; remove the rest |
| Watch `/matches/[id]/watch` | Virtual bets for one match (demo API) | Partly | Folded into Today (per-match positions) |

## What the operator needs

The UI is read-only except for one validated action. It shows only what the value-focused plan ranks as deciding
money (Tier A), plus what is happening right now.

1. **Today**: what the paper trader holds at this moment. Every match being traded, its prices, the model and market
   probabilities, each variant's P&L if home wins / if away wins, the worst case against the 100,000 cap, the last
   trades. And a plain banner: no real-money signals until a variant passes R3.
2. **Evidence**: is anything working? The Tier-A scorecard: CLV in excess of the placebo (match-clustered, pre-match
   and in-play apart), settled real matches with a tradable book, R1/R2/R3 progress counted in matches, the in-play
   model gate, fill realism.
3. **Data**: is the data trustworthy? The latest quality-monitor run (each check, status, detail), real matches
   missing a result with a form to enter one from a public scorecard (the `scripts/set_result.py` rules: winner must
   be a registry team, trusted results not overwritten), and the recorded matches (live, pre-match, finished; page
   open; score pipeline; actual start; result).

No page places bets, changes strategy settings or deletes data.

## Stories

| Story | What it does | Depends on |
|---|---|---|
| S91 Read-only API for the paper trader and data health | `/api/paper/live`, `/api/paper/evidence`, `/api/data/health`, `/api/data/matches`, `/api/data/missing-results`, `POST /api/data/results` (set_result rules) | S76, S85, S88 |
| S92 Today page | Live positions per match and variant, cap bars, last trades, no-signals banner; refresh every 15 s | S91 |
| S93 Evidence page | Tier-A scorecard and per-variant table with placebo excess and R-ladder progress | S91 |
| S94 Data page | Monitor checks, missing results with the enter-result form, recorded matches list | S91 |
| S95 Remove the obsolete UI and run it with the recorder | Delete the RL pages and components; three-item sidebar; backend and frontend join the `recording` profile | none |

Order: S95 and S91 in parallel (frontend clean-up vs backend), then S92–S94 in parallel (one page each).

## How we will know it worked

- `http://localhost:3000` opens on Today and shows the live paper positions within 15 s of a trade.
- The sidebar has three items; no page mentions PPO, graduation, specialists or shadow trading.
- Entering a result for a finished match settles its paper bets and the match leaves the missing list.
- No UI control can delete data, place a bet or change a strategy.
