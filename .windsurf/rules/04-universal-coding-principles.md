---
description: Universal coding thought process principles. Reusable across ANY project. Defines how to think through building, modifying, and debugging software to produce correct, production-quality code on the first attempt.
globs: ["**/*"]
---
# Universal Coding Thought Process Principles

These principles apply to **any** software project — web apps, APIs, data pipelines, ML systems, CLI tools, or infrastructure. They define how to think, not what to build.

---

## Phase 1: Before Writing Any Code

### P1 — Understand the Request Completely

**Restate what the user actually wants before touching code.**

Most wasted work comes from solving the wrong problem. Before implementing, clarify:
- **What** is the desired end state? (behavior, not implementation)
- **Why** does this matter? (context prevents over/under-engineering)
- **What exists already?** (never rebuild what's already there)

```
Bad:  User says "fix the login" → immediately edit auth.py
Good: User says "fix the login" → read the login flow end-to-end,
      identify which step fails, THEN edit the precise failure point
```

### P2 — Read Before Writing

**Never assume what code does. Read it.**

The most common bug class: assuming a function does X when it actually does Y. Names lie. Comments go stale. Only the code is truth.

**Apply this when:**
- Modifying an existing function → read the full function body
- Calling a function → confirm its signature, return type, and side effects
- Using a library/framework method → verify it exists in the installed version
- Referencing a DB column → confirm it exists in the schema/model

```
Bad:  "This function probably fetches user data" → call it blindly
Good: Read the function → discover it actually returns cached data
      from 2 hours ago → now you know why it's stale
```

### P3 — Map the Architecture First

**Before changing anything, understand how the system's pieces connect.**

Every non-trivial system has layers. Changes at one layer ripple to others. Map the path before cutting.

**Questions to answer:**
1. What are the system's layers? (DB → Backend → API → Frontend, or similar)
2. How does data flow between them? (HTTP, pub/sub, shared memory, files)
3. Where is state stored? (DB, cache, in-memory, filesystem)
4. What are the external dependencies? (APIs, services, libraries)

```
Typical web app layers:
  Database → ORM Model → Repository/Query → API Route → JSON Response → Frontend Fetch → Component Render

Typical data pipeline:
  Source → Ingestion → Transformation → Storage → Query → Visualization
```

---

## Phase 2: Designing the Solution

### P4 — Find the Source of Truth

**Every piece of data must have exactly one authoritative source. Identify it.**

When the same value exists in multiple places (DB + cache, two config files, env var + hardcoded default), one is the authority and the rest are copies. Always:
- Write to the authority
- Read from the authority (or its freshest cache)
- Never let copies diverge without a sync mechanism

```
Bad:  User's role stored in JWT AND database → edit the JWT, forget the DB
Good: Database is authority → JWT is a cache → always verify against DB
      for sensitive operations
```

### P5 — Design for the Failure Case First

**Ask "what goes wrong?" before "what goes right?"**

Happy path code is easy. Production bugs live in edge cases:
- What if the input is null, empty, or malformed?
- What if the external API is down, slow, or returns unexpected data?
- What if the database query returns 0 rows? 1 million rows?
- What if the process crashes mid-operation?
- What if two requests arrive simultaneously?

```
Bad:  const user = await getUser(id); return user.name;
      → crashes if user is null

Good: const user = await getUser(id);
      if (!user) return { error: "User not found", status: 404 };
      return user.name;
```

### P6 — Prefer Minimal, Targeted Changes

**Fix the root cause with the smallest correct change. Resist rewriting.**

Every line you change is a line that can break. Large refactors introduce regressions. The best fix is often 1-5 lines at the exact failure point.

**Decision framework:**
- Can this be fixed in < 5 lines? → Do that
- Does this require a new function? → Keep it small and focused
- Does this require a new file? → Only if it represents a genuinely new concept
- Does this require a refactor? → Only if the current structure PREVENTS the fix

```
Bad:  "The sort order is wrong" → rewrite the entire query builder
Good: "The sort order is wrong" → change ORDER BY DESC to ASC (1 line)
```

---

## Phase 3: Implementation

### P7 — Trace the Full Data Path

**For any value, trace it from creation → storage → retrieval → display.**

Bugs hide at layer boundaries. A value created correctly can be stored wrong, queried wrong, serialized wrong, or displayed wrong. Trace the full path.

**The 5-point trace:**
1. **Created** — where and how is this value first computed?
2. **Stored** — where does it persist? (DB column, Redis key, file, memory)
3. **Retrieved** — what query/call fetches it? Does it filter/transform?
4. **Serialized** — how is it converted for transport? (JSON, protobuf, URL param)
5. **Displayed** — how does the consumer render/use it? (format, units, null handling)

```
Example trace for "user signup date":
  Created:    datetime.now(UTC) in register_user()
  Stored:     users.created_at (timestamptz) in PostgreSQL
  Retrieved:  SELECT created_at FROM users WHERE id = :id
  Serialized: .isoformat() in API response → "2024-01-15T10:30:00Z"
  Displayed:  new Date(str).toLocaleDateString() → "Jan 15, 2024"
  
  Bug found: frontend shows wrong date because it doesn't parse timezone
```

### P8 — Enforce Invariants at Every Layer

**If a condition must be true for correctness, check it at every layer that handles the data.**

Don't rely on a single validation point. Data enters systems from multiple paths (API, queue, migration, manual fix). Each path needs its own guard.

**Common invariants to enforce:**
- Input validation → at API boundary AND at database level (constraints)
- Auth checks → at middleware AND at route handler AND at DB query (row-level)
- Business rules → at service layer AND at database (CHECK constraints)
- Type safety → at API schema AND at ORM model AND at frontend interface

```
Bad:  Validate email format only in the frontend form
      → API accepts invalid emails from curl/scripts

Good: Validate in frontend (UX) + API schema (security) + DB constraint (integrity)
```

### P9 — Make State Recoverable

**Any state that matters must survive a process restart.**

In-memory state (dicts, sets, queues, counters) is lost when the process restarts. If losing that state causes incorrect behavior, persist it or provide a recovery mechanism.

**Decision matrix:**
| State Type | Persistence Strategy |
|---|---|
| Must never be lost | Database (ACID transactions) |
| Can tolerate seconds of loss | Redis with AOF persistence |
| Can be recomputed on startup | Rebuild from DB on init |
| Ephemeral / best-effort | In-memory is fine |

```
Bad:  Track "which jobs are in progress" in a Python set
      → restart = all jobs appear unstarted, run twice

Good: Track job status in DB with states: pending → running → done
      → restart = query for "running" jobs, resume or retry them
```

### P10 — Never Trust External Dependencies

**Any external service can fail, change, or disappear. Plan for it.**

APIs change endpoints. Services go down. Rate limits kick in. SSL certs expire. Always:
- Log external failures clearly (status code, URL, timestamp)
- Provide fallback behavior (cached data, manual override, degraded mode)
- Set timeouts on all external calls
- Use circuit breakers for repeated failures

```
Bad:  result = await fetch("https://api.weather.com/current")
      return result.temperature  # crashes if API is down

Good: try:
        result = await fetch(url, timeout=5)
        cache.set("weather", result, ttl=300)
        return result.temperature
      except:
        cached = cache.get("weather")
        if cached: return cached.temperature
        return { temperature: null, source: "unavailable" }
```

---

## Phase 4: Verification

### P11 — Verify Against Reality, Not Theory

**After every change, confirm it works with real data in the running system.**

Code that looks correct in review can fail against real data (null values, edge cases, format mismatches). Always verify:

| Change Type | Verification Method |
|---|---|
| API endpoint | Call it, inspect the JSON |
| DB query | Run it against actual data |
| Frontend component | Render it, check the browser |
| Background job | Trigger it, check logs + output |
| Config change | Restart service, verify behavior |

```
Bad:  "I changed the query, it should work now" → mark as done
Good: "I changed the query" → run it → see actual results → confirm correct → done
```

### P12 — Check the Contract at Every Boundary

**When layers communicate, the sender and receiver must agree on shape, types, and meaning.**

Contract mismatches are invisible until runtime. After any change that touches a boundary, verify both sides agree:

| Boundary | Check |
|---|---|
| DB ↔ ORM | Column names, types, nullable, defaults match |
| ORM ↔ API | Query selects right fields, response includes them |
| API ↔ Frontend | JSON keys match TypeScript interface properties |
| Service ↔ Service | Message format, channel/topic name, serialization |
| Code ↔ Config | Env var names, default values, type parsing |

```
Bad:  Backend returns { user_name: "Alice" }
      Frontend reads { username: "Alice" }  // undefined!

Good: Backend returns { username: "Alice" }  // matches interface
      OR frontend interface updated to read user_name
```

### P13 — Run Existing Tests Before and After

**Never skip the test suite. A passing suite before + after your change = confidence.**

Even if your change seems unrelated, run the tests. Indirect dependencies break silently.

```
Workflow:
  1. Run tests BEFORE your change (establish baseline)
  2. Make your change
  3. Run tests AFTER (catch regressions)
  4. If tests fail: your change broke something — investigate, don't skip
  5. If no tests exist for the area you changed: consider adding one
```

---

## Phase 5: Code Quality

### P14 — Constants and Defaults Are Code

**Review every magic number, default value, and configuration constant.**

A wrong default is a silent bug. A zero where you meant "unlimited" or "disabled" can cause catastrophic behavior that masquerades as a logic bug.

**Audit checklist for constants:**
- `0` → Does this mean "none", "unlimited", or "disabled"? (These are very different)
- `timeout = 30` → 30 what? Seconds? Milliseconds? Is this enough for slow networks?
- `max_retries = 3` → What happens after 3? Silent failure? Error? Data loss?
- `default = ""` → Will downstream code treat empty string as null? Or as a valid value?

### P15 — Respect Existing Patterns

**Match the codebase's existing style, patterns, and conventions.**

Consistency matters more than personal preference. A codebase with one style is maintainable. A codebase with five styles is a minefield.

**Match these:**
- Naming conventions (camelCase vs snake_case vs PascalCase)
- Error handling pattern (exceptions vs result types vs error codes)
- File organization (by feature vs by layer vs by domain)
- Import style (absolute vs relative, sorted vs unsorted)
- Logging approach (structured vs printf, log levels)
- Test organization (co-located vs separate directory)

```
Bad:  Existing code uses snake_case → you add a camelCase function
Good: Existing code uses snake_case → your new code uses snake_case too
```

### P16 — Log for Debuggability

**Every significant operation should produce a log entry that helps debug failures after the fact.**

When something breaks in production, logs are often the only evidence. Log:
- **What** happened (operation name, result)
- **Context** (IDs, parameters, timestamps)
- **Failures** explicitly (error message, stack trace, what was attempted)

```
Bad:  logger.error("Failed")  // useless

Good: logger.error("payment_failed",
        user_id=user.id,
        amount=amount,
        provider="stripe",
        error=str(e),
        retry_count=attempt)
```

---

## Phase 6: Architecture Decisions

### P17 — Fix Upstream, Not Downstream

**When data is wrong, fix it at the source — not at every consumer.**

Downstream patches accumulate into unmaintainable spaghetti. If the data is wrong at the source, fix the source. All consumers benefit automatically.

```
Bad:  API returns wrong date format → patch it in 5 different frontend components
Good: API returns wrong date format → fix the serializer in the API → all consumers fixed
```

### P18 — Separate "What Happened" from "What To Do About It"

**Keep data/event production separate from reaction/policy logic.**

When a match ends, store the result (fact). Separately, settle bets based on that result (policy). This separation means you can:
- Replay events against new policies
- Add new reactions without modifying the event producer
- Test policies independently of event production

```
Bad:  process_order() validates, charges, ships, emails, and logs in one function
Good: validate_order() → charge_payment() → create_shipment() → send_email()
      Each step is independent, testable, and replaceable
```

### P19 — Make Implicit Dependencies Explicit

**If component A breaks when component B changes, make that dependency visible.**

Hidden dependencies cause the worst bugs — the ones where "I only changed X, why did Y break?"

**Common hidden dependencies:**
- Shared database tables (A reads what B writes — but no import/interface connects them)
- Redis key names hardcoded in multiple files (rename in one → silent break in another)
- Assumed file paths or directory structures
- Implicit execution order ("service A must start before service B")
- Environment variables read in multiple places

```
Bad:  Service A writes to Redis key "user:123" 
      Service B reads from Redis key "user:123"
      Neither imports a shared constant → rename = silent break

Good: Both import KEY_USER_PROFILE from shared/constants.py
      Rename once → both updated → or compile error if missed
```

### P20 — Build for Observability

**Design every system so you can answer "what is happening right now?" from the outside.**

You should be able to check the system's health, current state, and recent activity without reading code or attaching a debugger.

**Observability checklist:**
- Health endpoint that checks real dependencies (DB, cache, queues)
- Status endpoint that shows current processing state
- Metrics for throughput, latency, error rate
- Structured logs that can be queried/filtered
- Admin endpoints for inspecting or correcting state

```
Good health endpoint:
  GET /health → {
    status: "healthy",
    database: "connected",
    cache: "connected",
    queue_depth: 42,
    last_processed: "2024-01-15T10:30:00Z",
    uptime_seconds: 86400
  }
```

---

## Quick Reference: Decision Framework

When you're unsure how to proceed, ask these questions in order:

1. **Do I understand the request?** → If no, clarify before coding
2. **Have I read the relevant code?** → If no, read it now
3. **Do I know the data path?** → If no, trace it end-to-end
4. **What's the smallest correct fix?** → Start there
5. **What can go wrong?** → Handle those cases
6. **Does this survive a restart?** → Persist if it matters
7. **Do all layers agree on the contract?** → Verify boundaries
8. **Does it work with real data?** → Test against the running system
9. **Do existing tests still pass?** → Run the suite
10. **Would a new developer understand this?** → If no, add context
