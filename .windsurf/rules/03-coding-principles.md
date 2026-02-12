---
description: Coding thought process principles. Applied to every task. Defines how to think through building, modifying, and debugging code to produce correct, aligned output on the first attempt.
globs: ["**/*"]
---
# Coding Thought Process Principles

These principles are distilled from real bugs, wasted hours, and costly mistakes found across multiple sessions. They are ordered by when to apply them in a coding task — from receiving the request to shipping the fix.

---

## Principle 1: Understand Before Touching

**Never write code based on assumptions. Read the actual source first.**

The single most common class of bugs comes from assuming what code does instead of reading it. Functions may not do what their name suggests. Fields may not exist. Keys may not be populated.

**How to apply:**
- Before changing any file, read the relevant functions end-to-end
- Before referencing any field, confirm it exists in the model/schema/interface
- Before reading any Redis key, confirm which service writes to it and when

**Real example — the stale state bug:**
The `/health` endpoint read `agent:state` from Redis, assuming the orchestrator kept it updated. In reality, the orchestrator only wrote to `agent:state` during rare events (graduation, demotion). The actual authoritative state lived in `orchestrator:state`, updated every cycle. The fix was one line — but finding it required reading `orchestrator.py` to discover which keys it actually writes to.

**Anti-pattern:** "The health endpoint reads agent state from Redis, so I'll just update the Redis key." → Wrong. The question is: *who writes this key, and when?*

---

## Principle 2: Trace the Full Data Path

**For any value you're working with, trace it from origin → storage → API → display.**

Most bugs live at layer boundaries. A value may be computed correctly but stored wrong, stored correctly but queried wrong, queried correctly but displayed wrong.

**How to apply:**
For every field, answer these four questions:
1. **Where is this value created?** (which service, which function)
2. **Where is it stored?** (DB table + column, Redis key, in-memory)
3. **How does it reach the API?** (which query, which endpoint)
4. **How does the frontend read it?** (which TypeScript interface, which component)

**Real example — training metrics always zero:**
- Training callbacks computed metrics correctly ✅
- Callbacks logged metrics to structlog and JSON files ✅
- But NO callback wrote metrics to the `training_metrics` DB table ❌
- The `/training/summary` API queried the DB table → always 0
- Fix: added `DatabaseMetricsCallback` that flushes to DB every 5K steps

**Anti-pattern:** "Training is running and I can see metrics in the logs, so the API should show them." → Wrong. Logs and DB are different storage layers.

---

## Principle 3: Identify the Source of Truth

**Every value must have exactly one authoritative source. Find it. Read from it.**

When the same concept exists in multiple places (e.g., agent state in `agent:state` AND `orchestrator:state`), one is authoritative and the rest are stale copies. Always find and read the authoritative source.

**How to apply:**
- Ask: "Which service OWNS this data and updates it most frequently?"
- Read from that service's primary storage, not from downstream caches
- If you must read a cache, add a fallback to the source of truth

**Real example — two Redis keys for the same concept:**
- `agent:state` — written only during graduation/demotion (stale 99% of the time)
- `orchestrator:state` — written every orchestrator cycle (always current)
- Both the `/health` and `/agent/state` endpoints read the wrong one
- Fix: read `orchestrator:state` as primary, fall back to `agent:state`

---

## Principle 4: Defend Every Gate

**If a condition must be true for correct behavior, enforce it at EVERY layer that matters — not just one.**

A single gate can fail silently. Belt-and-suspenders: enforce critical conditions at multiple layers so a failure at one layer doesn't cascade.

**How to apply:**
- If only live matches should get bets → check `is_live` in BOTH the scraper AND the trading loop
- If bets should be limited per match → enforce in BOTH the engine AND the risk manager
- If data should never be NaN → validate in BOTH the feature pipeline AND the environment

**Real example — non-live match getting bets:**
- The scraper had a live gate: only publish events where `is_live=True`
- But the trading loop had NO live check — it bet on every event it received
- If a non-live event leaked through (race condition, bug, etc.), the agent would bet on it
- Fix: added `if not event.is_live: return` at the top of `_handle_match_event`

**Anti-pattern:** "The scraper already filters non-live matches, so the trading loop doesn't need to check." → Wrong. Every critical gate needs independent enforcement.

---

## Principle 5: Constants Are Code

**Review every constant that controls system behavior. A wrong default is a silent bug.**

Constants control bet limits, thresholds, timeouts, and feature dimensions. A single wrong value (especially 0 or unlimited) can cause catastrophic behavior that looks like a logic bug but is actually a config bug.

**How to apply:**
- When investigating a behavior issue, check the relevant constants FIRST
- Audit constants for dangerous defaults: 0 (unlimited), very large numbers, or very small thresholds
- Document the reasoning behind every non-obvious constant value

**Real example — 18,000+ bets on a single match:**
- `MAX_BETS_PER_MATCH = 0` was intended to mean "no limit during development"
- But it was never changed before going live → agent placed unlimited bets
- Result: 10,458 bets on one match, all pending, all unsettleable
- Fix: set to 20 (reasonable limit for virtual trading)

---

## Principle 6: In-Memory State Dies on Restart

**Any state held only in memory is lost on process restart. If it matters, persist it.**

Background services crash, get restarted, or get redeployed. Any state tracked only in Python dicts or lists vanishes. If that state is needed for correctness (e.g., which bets are open), it must be backed by a persistent store.

**How to apply:**
- For every in-memory dict/set, ask: "What happens if this process restarts right now?"
- If the answer is "data loss" → add a DB/Redis persistence layer or a recovery mechanism
- At minimum, provide a way to reconstruct state from the DB on startup

**Real example — 18K bets stuck as "pending" forever:**
- `LiveTradingLoop._open_bets` tracked open bets in a Python dict
- When the trading loop restarted, the dict was empty
- Match results arrived but couldn't find the bets to settle
- Fix: added `POST /agent/settle-pending` endpoint that joins `virtual_bets` with `match_results` in the DB and settles retroactively

---

## Principle 7: External APIs Will Break

**Any external dependency (API, service, website) can change or disappear without notice. Build fallbacks.**

Third-party APIs change their endpoints, add rate limits, or shut down entirely. Your system must degrade gracefully, not fail silently.

**How to apply:**
- Log clearly when an external API fails (status code, endpoint, timestamp)
- Always have a fallback mechanism (manual input, alternative data source, cached data)
- Never depend on a single external source for a critical path

**Real example — Cricbuzz API returning 404:**
- `MatchResultCollector` fetched match results from `cricbuzz.com/api/cricket-match/recent`
- The endpoint started returning 404 — match results stopped flowing
- No warning was logged — the failure was completely silent
- Result: 0 match results, 0 settlements, all bets stuck as pending
- Fix: replaced with `LotusResultDetector` that infers results from LotusBook odds data (the data we already have), plus manual result submission as fallback

---

## Principle 8: Verify With Real Data, Not Assumptions

**After making a change, verify it works with actual system data — not just in your head.**

A code change that looks correct can still fail due to data format mismatches, empty tables, null values, or race conditions. Always verify against the running system.

**How to apply:**
- After fixing a backend endpoint: call it and check the JSON response
- After fixing a DB query: run it against the actual database
- After fixing a frontend display: check the rendered page
- After fixing a Redis consumer: check the actual Redis key/channel data

**Real example — verifying the health endpoint fix:**
```
Before fix: agent_state=paused, agent_version=1  (stale/wrong)
After fix:  agent_state=training, agent_version=5  (correct)
```
The fix looked correct in code review, but verification confirmed it actually worked against real Redis data.

---

## Principle 9: Cross-Layer Contract Alignment

**When any layer changes, verify the contract still holds across all layers: DB model ↔ API response ↔ Frontend interface.**

The most insidious bugs are contract mismatches where the backend returns a field the frontend doesn't read, or the frontend expects a field the API doesn't return. These bugs are invisible until a user sees wrong data.

**How to apply:**
For every change, check the three-layer contract:
1. **DB → API:** Does the query select the right columns? Does the API return them?
2. **API → Frontend:** Does the TypeScript interface include all returned fields?
3. **Frontend → User:** Does the component render the field correctly (right format, right unit)?

**Checklist for contract changes:**
- Adding a DB column → update ORM model + migration + any queries + API response + frontend interface
- Changing an API response → update frontend interface + any components that read it
- Changing a Redis key format → update ALL producers and consumers

---

## Principle 10: Fix Upstream, Not Downstream

**When you find a bug, fix it at the root cause — not at the symptom.**

Downstream workarounds accumulate into unmaintainable spaghetti. If the data is wrong at the source, fix the source — don't patch every consumer.

**How to apply:**
- Trace the bug to the earliest point where the data goes wrong
- Fix it there, then verify all downstream consumers now get correct data
- Remove any temporary workarounds that were masking the real issue

**Real example — health endpoint mapping:**
- The first attempt mapped orchestrator states to non-existent `AgentState` enum values (`"virtual_trading"`, `"advisor"`)
- This would crash at runtime because those enum values don't exist
- The correct fix: map to EXISTING enum values (`EVALUATING`, `LIVE`) that the frontend already knows how to display
- One upstream fix → all downstream consumers (health badge, dashboard, sidebar) work correctly

---

## Application Order

When receiving any coding task, apply these principles in this order:

1. **Read** the relevant code (Principle 1)
2. **Trace** the data path end-to-end (Principle 2)
3. **Identify** the source of truth (Principle 3)
4. **Check** constants and defaults (Principle 5)
5. **Implement** the fix at the root cause (Principle 10)
6. **Add** defensive gates at every critical layer (Principle 4)
7. **Ensure** persistence for any state that matters (Principle 6)
8. **Add** fallbacks for external dependencies (Principle 7)
9. **Verify** cross-layer contracts (Principle 9)
10. **Test** with real data against the running system (Principle 8)
