---
description: Forensic analysis checklists. Applied to every file. Defines mandatory verification steps for every type of change — functional, technical, and UI — to prevent recurring bugs.
globs: ["**/*"]
trigger: model_decision
---
# PHOENIX — Forensic Analysis & Code Quality Rules

These checklists exist because of real bugs found repeatedly across sessions. Every checklist item maps to an actual defect class we've encountered. **Run the relevant checklist before considering any change complete.**

## CHECKLIST 1: Before Any Code Change

Run this mentally before writing any code:

- [ ] **Read the actual code first.** Never assume what a function does — read it. We've had bugs from methods that didn't exist, columns that weren't in the DB model, and fields that weren't returned by APIs.
- [ ] **Trace the full data path.** For any field you're touching, trace it from source → storage → API → frontend. A change at one layer usually requires changes at others.
- [ ] **Check cross-layer contracts.** If you add a field to a DB model, does the API return it? Does the frontend read it? If you change a Redis key format, do all consumers handle the new format?
- [ ] **Verify imports exist.** Before referencing any function, class, or constant — confirm it exists in the source file. We've had crashes from calling methods that were never implemented.

## CHECKLIST 2: Backend / Python Changes

### Database Model Changes
- [ ] Column exists in SQLAlchemy model (`backend/models/*.py`)
- [ ] Migration added to `backend/db_init.py` for existing tables (ALTER TABLE ADD COLUMN IF NOT EXISTS)
- [ ] All queries referencing the column updated
- [ ] Default value provided for new columns on existing rows

### API Endpoint Changes
- [ ] Response schema matches what frontend expects (check the TypeScript interface)
- [ ] Handles empty/null data gracefully (don't crash on empty DB)
- [ ] Error responses return proper HTTP status + detail message
- [ ] If new endpoint: added to router AND router registered in `backend/main.py`

### Scraper Changes
- [ ] `is_live` detection uses section-aware DOM walk (not just `#inPlay` element)
- [ ] Upcoming matches marked `is_live = false` (date patterns: DD/MM/YYYY, "Tomorrow", "Today")
- [ ] Completed matches stop being scraped (`_completed_match_ids`)
- [ ] Stale matches cleaned up (not seen for 2h → removed from active_matches)
- [ ] LIVE GATE enforced: ticks only stored when `is_live=True AND approved AND not completed`
- [ ] `active_matches` Redis key includes `updated_at` timestamp

### RL / Training Changes
- [ ] Observation vector is exactly 48 floats, dtype=float32
- [ ] No NaN in observations (replace with 0.0 or safe default)
- [ ] Model saved to `models/best_model.zip` after training
- [ ] Reward components all have correct sign (win=positive, loss=negative)
- [ ] LAY bet P&L is inverted from BACK (win=stake, loss=-stake*(odds-1))

### Virtual Trading Changes
- [ ] BACK bet P&L: win = stake * (odds - 1), loss = -stake
- [ ] LAY bet P&L: win = stake, loss = -stake * (odds - 1)
- [ ] CLV computed using closing odds from last tick before settlement
- [ ] Settlement handles all result types (win, loss, tie, no_result, abandoned)

### Redis Pub/Sub Changes
- [ ] Channel name from `shared/constants.py` (never hardcoded strings)
- [ ] Publisher uses `await redis.publish_event(channel, data)`
- [ ] Subscriber handles message["type"] != "message" (skip subscribe confirmations)
- [ ] Data is JSON-serializable (datetime → .isoformat())

## CHECKLIST 3: Frontend / UI Changes

### Data Accuracy (most critical)
- [ ] **UI reflects actual backend state.** Never show hardcoded/fake values.
- [ ] **is_live badge** only shown when `is_live === true` in active_matches API response
- [ ] **Scraping status** shown as "Approved" when approved but not live, "Scraping" only when live
- [ ] **Lifecycle state** comes from `/advisor/state` API, not hardcoded
- [ ] **Health status** comes from `/health` API, not hardcoded "System Active"
- [ ] **Empty states** have meaningful messages explaining what needs to happen next

### Common Frontend Bugs (all previously found)
- [ ] Use `replaceAll("_", " ")` not `replace("_", " ")` — JS replace only replaces first occurrence
- [ ] No currency symbols (£, $) — this is virtual trading with abstract units
- [ ] Sidebar active detection uses `startsWith` for nested routes, exact match only for "/"
- [ ] API polling interval appropriate (5-15s for live data, 30s+ for slow-changing data)
- [ ] Handles API errors gracefully (no white screen on 500)
- [ ] Numbers formatted contextually: rates as %, odds as decimal, counts as integers

### Badge & Status Consistency
| Match State | Badge Color | Badge Text | Meta Text |
|---|---|---|---|
| Discovered | Blue | "Discovered" | "Awaiting approval" |
| Approved + NOT live | Blue | "Approved" | "Waiting for live match" |
| Approved + LIVE | Green | "Scraping" | "X ticks collected" |
| Approved + finished | Blue | "Approved" | "X ticks collected" |
| Skipped | Red | "Skipped" | "Skipped" |

### Graduation Criteria Display
- win_rate, roi, drawdown → show as percentage (value * 100 + "%")
- sharpe_ratio → show as decimal (value.toFixed(2))
- Always show threshold alongside current value

## CHECKLIST 4: When Running Analysis / Bug Hunt

Use this when the user asks for forensic analysis, gap review, or bug hunt:

### Layer 1: Infrastructure
- [ ] Backend API responding (`GET /health` → status: "healthy")
- [ ] Redis connected and pub/sub working
- [ ] Database connected and tables exist
- [ ] Frontend compiling and serving (HTTP 200 on localhost:3000)
- [ ] Scraper running and producing data

### Layer 2: Data Pipeline
- [ ] Scraper extracting matches from LotusBook (check `scrape_complete` logs)
- [ ] `is_live` correctly distinguishes In Play vs Upcoming (check active_matches Redis key)
- [ ] Only approved + live matches getting ticks stored
- [ ] Completed matches stop being scraped
- [ ] Odds ticks flowing to TimescaleDB (check odds_ticks table count)

### Layer 3: RL Pipeline
- [ ] Orchestrator running and in expected state
- [ ] Data loader can find qualifying matches for training
- [ ] Training produces model file at configured path
- [ ] Observation vector has no NaN/Inf
- [ ] Agent action distribution reasonable (mostly HOLD)

### Layer 4: Virtual Trading
- [ ] Bets being placed when agent is in online_training/virtual_trading
- [ ] Settlement running when match results arrive
- [ ] P&L calculations correct for both BACK and LAY
- [ ] Risk limits enforced (exposure, drawdown, bet frequency)

### Layer 5: API
- [ ] All endpoints return valid JSON (not HTML error pages)
- [ ] `/matches/active` returns correct is_live flags
- [ ] `/advisor/state` returns actual orchestrator state from Redis
- [ ] `/health` checks real Redis/DB connectivity + scraper freshness
- [ ] `/training/summary` returns real data (not zeros when training has occurred)

### Layer 6: UI
- [ ] Dashboard shows current lifecycle state (not hardcoded)
- [ ] Live Matches page: LIVE badge only on actually live matches
- [ ] Approved but not-live matches show "Approved" (not "Scraping")
- [ ] Training page: meaningful empty state when no training data
- [ ] Graduation page: criteria values in correct format (% vs decimal)
- [ ] All pages handle loading and error states
- [ ] Sidebar highlights correct nav item for current route (including nested)
- [ ] No console errors in browser

## CHECKLIST 5: Pydantic v2 Compatibility

We use Pydantic v2. Common migration traps:
- [ ] Use `pattern=` not `regex=` in Field validators
- [ ] Use `model_dump()` not `.dict()`
- [ ] Use `model_validate()` not `parse_obj()`
- [ ] Use `ConfigDict` not inner `class Config`
- [ ] Validators use `@field_validator` not `@validator`

## CHECKLIST 6: Testing After Changes

- [ ] Run `python -m pytest tests/unit/ -v --tb=short` — all tests must pass
- [ ] If scraper changed: restart scraper, verify active_matches in Redis
- [ ] If API changed: verify endpoint returns expected JSON
- [ ] If frontend changed: verify page renders (HTTP 200) and no TypeScript errors
- [ ] If DB model changed: verify migration runs without error on existing DB

## CHECKLIST 7: Iterative Bug Hunt (Full Codebase Sweep)

Use when the user requests a comprehensive forensic review or "bug hunt until clean". See `05-review-bug-hunting.md` Part 6 for the full methodology.

### Process
- [ ] Run a fresh pass applying all 7 techniques from `05-review-bug-hunting.md` Part 3
- [ ] Vary your angle each pass (defaults, contracts, boundaries, state, errors, consistency, silent failures)
- [ ] Log every bug with file, line, root cause, and minimal fix
- [ ] Fix all bugs found in the pass
- [ ] Run full test suite — **all tests must pass** before starting next pass
- [ ] Repeat until **2 consecutive passes find 0 bugs**

### What Counts as a Bug
- Wrong defaults (e.g., `is_live=True` when safe default is `False`)
- Missing persistence (model not saved after training)
- Double operations (redundant commit inside auto-committing context manager)
- Enum/constant mismatches (swapped indices, hardcoded strings vs constants)
- Stale docs contradicting code (wrong dimension counts, wrong group names)

### Exit Criteria
- **2 consecutive clean passes** (0 bugs found per pass)
- **All tests pass** after every fix round
- **No known unfixed bugs** in the backlog

### Tracking
Use the `todo_list` tool to track pass status:
```
Pass #1: [completed] — found N bugs (brief summary)
Pass #2: [completed] — found N bugs (brief summary)
Pass #3: [completed] — CLEAN (1st consecutive)
Pass #4: [completed] — CLEAN (2nd consecutive) ✅ DONE
```
