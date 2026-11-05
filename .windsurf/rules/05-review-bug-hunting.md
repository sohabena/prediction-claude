---
description: Forensic-level code review and bug hunting thought process. Reusable across ANY project. Defines systematic techniques for finding bugs that hide in plain sight — logic errors, contract mismatches, silent failures, race conditions, and data corruption.
globs: ["**/*"]
---
# Forensic Code Review & Bug Hunting Principles

This is a systematic methodology for finding bugs that automated tools miss. It's designed for the hardest class of bugs: the ones where "everything looks correct" but the system produces wrong results.

---

## Part 1: Mental Model — How Bugs Hide

Before hunting, understand WHERE bugs hide. They cluster in predictable locations:

### The 7 Bug Habitats

| # | Habitat | Why Bugs Live Here | Detection Method |
|---|---------|-------------------|-----------------|
| 1 | **Layer boundaries** | Serialization, format conversion, null handling | Trace data across every boundary |
| 2 | **Default values** | Wrong default silently produces wrong behavior | Audit every default, especially 0, "", null, false |
| 3 | **Error handling** | Catch-all swallows real errors; missing catch crashes | Search for bare `except:`, empty catch blocks, missing error paths |
| 4 | **State management** | Stale cache, in-memory loss on restart, race conditions | Map every stateful variable: where written, where read, when invalidated |
| 5 | **Implicit assumptions** | "This will always be non-null", "This runs after X" | Challenge every assumption with "what if this is wrong?" |
| 6 | **Copy-paste code** | Fixed in one copy, forgotten in others | Search for duplicated logic, verify all copies are consistent |
| 7 | **Configuration** | Env var missing, wrong type, dev value in prod | Audit every config value against its expected range and type |

---

## Part 2: The Forensic Review Process

### Step 1: Establish Ground Truth

Before looking for bugs, establish what "correct" means:

**For a feature:**
- What is the expected input? (types, ranges, formats, edge cases)
- What is the expected output? (exact shape, not vague description)
- What side effects should occur? (DB writes, events published, files created)
- What should NOT happen? (no duplicate writes, no data leaks, no crashes)

**For a bug report:**
- What is the user seeing? (exact error, wrong value, missing data)
- What should they see instead? (expected correct behavior)
- When did it start? (after a deploy, after data change, always broken)
- Is it consistent or intermittent? (consistent = logic bug, intermittent = timing/state bug)

### Step 2: Trace the Data Path (Forensic Detail)

Pick the broken value and trace it through every layer. At each layer, verify:

```
Layer 1: SOURCE — Where is this value born?
  □ What function/query creates it?
  □ What are its inputs? Are any of them wrong?
  □ What are the edge cases? (null, zero, empty, max value)
  □ Is the computation correct? (off-by-one, wrong operator, integer overflow)

Layer 2: STORAGE — Where does it persist?
  □ Which table/column or key stores it?
  □ Is the type correct? (string vs int, UTC vs local time, float precision)
  □ Is it written atomically? (partial write = corruption)
  □ Can it be overwritten by another process? (race condition)
  □ Is there a default value? Is it correct?

Layer 3: RETRIEVAL — How is it fetched?
  □ Does the query filter correctly? (WHERE clause, JOIN conditions)
  □ Does it handle zero rows? (null vs empty array vs exception)
  □ Does it handle multiple rows when expecting one? (first vs exception)
  □ Is the sort order correct? (ASC vs DESC, null ordering)
  □ Are aggregations correct? (SUM vs COUNT, NULL handling in AVG)

Layer 4: TRANSFORMATION — How is it processed after retrieval?
  □ Is it mapped/converted correctly? (enum mapping, unit conversion)
  □ Is null/undefined handled at every step?
  □ Is the type preserved? (string "0" vs number 0, "false" vs false)
  □ Is rounding/precision handled? (floating point, currency)

Layer 5: SERIALIZATION — How is it sent to the consumer?
  □ JSON key names match what consumer expects?
  □ Dates serialized correctly? (ISO 8601 with timezone)
  □ Numbers maintain precision? (no silent truncation)
  □ Null/undefined distinction preserved? (JSON null vs missing key)

Layer 6: CONSUMPTION — How does the receiver use it?
  □ Does the consumer's type/interface match the actual shape?
  □ Does it handle missing/null values gracefully?
  □ Is it displayed in the correct format? (%, decimal, date, currency)
  □ Does the UI update when the value changes? (reactivity, polling)
```

### Step 3: Challenge Every Assumption

For each layer, actively try to break it by asking:

**Null/Empty challenges:**
- What if this value is null?
- What if this string is empty?
- What if this array has 0 elements?
- What if this number is 0? Negative? NaN? Infinity?
- What if this date is in the future? In 1970? Null?

**Timing challenges:**
- What if this runs before the dependency is ready?
- What if this runs twice simultaneously?
- What if the external service is slow (30+ seconds)?
- What if the process restarts between step A and step B?
- What if the data changes between read and write?

**Scale challenges:**
- What if there are 0 records? 1? 1 million?
- What if the input string is 10MB?
- What if 100 users hit this endpoint simultaneously?
- What if the queue has 50,000 messages backed up?

**Permission/Auth challenges:**
- What if the user doesn't have permission?
- What if the token is expired?
- What if the API key is missing?

---

## Part 3: Specific Bug Hunting Techniques

### Technique 1: The Backwards Trace

**Start from the symptom and work backwards to the root cause.**

```
Symptom: Dashboard shows "0 training episodes"
  ↑ Frontend reads from /training/summary endpoint
  ↑ Endpoint queries SELECT MAX(episode) FROM training_metrics
  ↑ training_metrics table has 0 rows ← ROOT CAUSE FOUND
  ↑ Why? No callback writes to this table
  ↑ Callbacks only log to files/stdout, not DB
```

**Process:**
1. Identify the exact wrong value (not "the page is broken" but "win_rate shows 0.00")
2. Find where the frontend reads this value (which API call, which field)
3. Check what the API actually returns (call it directly, inspect JSON)
4. Check where the API gets the data (which query, which table/cache)
5. Check if the data exists in the source (query the DB/Redis directly)
6. Repeat backwards until you find the first point where reality diverges from expectation

### Technique 2: The Contract Audit

**Verify that every boundary between components agrees on the data shape.**

For each pair of connected components, compare:

```
Check 1: DB Schema vs ORM Model
  - Every column in the table has a matching model field?
  - Types match? (VARCHAR vs Text, TIMESTAMP vs DateTime)
  - Nullable matches? (DB allows NULL but code assumes non-null?)
  - Defaults match? (DB default vs code default)

Check 2: ORM Model vs API Response
  - Every field the API returns exists in the model?
  - Field names match? (snake_case in Python vs camelCase in JSON?)
  - Types survive serialization? (datetime → ISO string, Decimal → float)
  - Null handling consistent? (None → null vs None → omitted)

Check 3: API Response vs Frontend Interface
  - Every field in the TypeScript/JS interface exists in the API response?
  - Types match? (number vs string, Date vs string)
  - Optional fields marked as optional?
  - Default values provided for missing fields?

Check 4: Frontend Interface vs Component Usage
  - Every field the component accesses exists in the interface?
  - Null checks before accessing nested properties?
  - Correct formatting? (percentage needs ×100, dates need parsing)
```

### Technique 3: The State Machine Audit

**Map every state a system entity can be in, and verify every transition.**

```
Example — Order states:
  created → paid → shipped → delivered
                → refunded
          → cancelled
  
For each state, verify:
  □ Entry: What triggers this transition? Is the trigger correct?
  □ Guard: What conditions must be true? Are they checked?
  □ Action: What side effects occur on entry? Are they all executed?
  □ Exit: What transitions OUT are possible? Can it get stuck?
  □ Invalid: What transitions are NOT allowed? Are they prevented?
  
Common state bugs:
  - Missing transition (order stuck in "paid" because shipping logic has a bug)
  - Duplicate transition (order shipped twice because of retry without idempotency)
  - Invalid state reached (order is "delivered" but was never "shipped")
  - State not persisted (in-memory state lost on restart → entity in limbo)
```

### Technique 4: The Silent Failure Hunt

**Search for code that swallows errors without logging or re-raising.**

These are the hardest bugs because there's no error message — the system just produces wrong results quietly.

**Search patterns (regex):**
```
Python:
  except:              → bare except catches everything including KeyboardInterrupt
  except Exception:    → then check: does the handler log? re-raise? or just pass?
  except.*pass         → error completely swallowed
  except.*continue     → error swallowed in a loop

JavaScript/TypeScript:
  catch\s*\(\s*\)      → empty catch parameter (can't even log the error)
  catch.*{}            → empty catch body
  \.catch\(\(\)\s*=>   → promise error swallowed

General:
  # TODO              → unfinished work left behind
  FIXME               → known bug not fixed
  HACK                → temporary workaround that became permanent
  if False:           → dead code that might be accidentally re-enabled
```

**What to do when you find one:**
1. Determine what error this catch is handling
2. Determine what the caller expects (does it check for failure?)
3. Add logging at minimum; add proper error handling if the error matters
4. If the error is expected (e.g., cache miss), document why it's swallowed

### Technique 5: The Consistency Audit

**Find places where the same logic is implemented multiple times and check if they agree.**

```
Common duplication points:
  - P&L calculation done in settlement.py AND in agent.py → do formulas match?
  - Date formatting done in 3 different components → same format?
  - Validation done in frontend AND backend → same rules?
  - Constants hardcoded in code AND in config → same values?

How to find duplicates:
  1. Search for the function/concept name across the codebase
  2. Search for key formulas (e.g., "stake * (odds - 1)" for P&L)
  3. Search for key strings (status names, error messages, Redis keys)
  4. Compare: are all implementations identical? If not, which is correct?
```

### Technique 6: The Dependency Freshness Check

**Verify that every cached or derived value is kept in sync with its source.**

```
For each cached/derived value:
  □ What is the source of truth?
  □ When does the cache update? (TTL, event-driven, on-access)
  □ What happens if the source updates but the cache doesn't?
  □ What happens if the cache TTL is too long? Too short?
  □ Can the cache return stale data that causes incorrect behavior?
  
Common freshness bugs:
  - Redis cache TTL too long → user sees old data for minutes
  - Frontend polls every 30s → live data appears frozen
  - Materialized view not refreshed → dashboard shows yesterday's numbers
  - Config cached at startup → config change requires restart (but nobody knows)
```

### Technique 7: The Boundary Value Probe

**Test at the exact boundaries of valid input ranges.**

```
For each input, test:
  - Minimum valid value (0, 1, "a", empty array)
  - Maximum valid value (MAX_INT, 10MB string, 1M array elements)
  - Just below minimum (negative, empty string, null)
  - Just above maximum (MAX_INT + 1, 10MB + 1 byte)
  - Type boundary (integer overflow, float precision limit)
  
For each conditional:
  - Exact threshold value (if x > 10, test x=10 and x=11)
  - Off-by-one (arrays: first, last, length-1, length, length+1)
  - Equality vs inequality (> vs >= , == vs ===, != vs !==)
```

---

## Part 4: Forensic Review Checklist

Use this checklist when doing a deep review of any codebase or feature:

### Data Integrity
- [ ] Every DB write is atomic (transactions where needed)
- [ ] Every DB read handles 0 rows, 1 row, and many rows
- [ ] No orphaned data (foreign keys enforced, cascade deletes configured)
- [ ] No duplicate data (unique constraints, upsert logic)
- [ ] Timestamps stored in UTC with timezone
- [ ] Floating point not used for money/precision-critical values

### Error Handling
- [ ] No bare `except:` or empty `catch {}`
- [ ] Every error is either handled, logged, or re-raised (never swallowed)
- [ ] External API calls have timeouts
- [ ] Retry logic has backoff and max attempts
- [ ] Partial failures handled (batch of 100 items, 1 fails — what happens to the other 99?)

### State & Concurrency
- [ ] In-memory state has a persistence/recovery strategy
- [ ] No race conditions on shared state (locks, atomic operations, or queue-based)
- [ ] Idempotency for operations that might run twice (retries, duplicate messages)
- [ ] Cleanup/timeout for orphaned state (jobs stuck in "processing")

### Security & Input Validation
- [ ] All user input validated and sanitized at the API boundary
- [ ] SQL queries use parameterized queries (no string concatenation)
- [ ] No secrets in code, logs, or error messages
- [ ] Auth checked on every endpoint that needs it
- [ ] Rate limiting on public endpoints

### Configuration & Environment
- [ ] All config values have sensible defaults
- [ ] Missing required config fails fast with clear error message
- [ ] No dev/test values hardcoded in production code
- [ ] Feature flags have a clear on/off behavior and cleanup plan

### Observability
- [ ] Health endpoint checks real dependencies
- [ ] Key operations produce structured log entries
- [ ] Errors include enough context to diagnose without reproduction
- [ ] Metrics for throughput, latency, error rate on critical paths

---

## Part 5: Bug Report Template

When you find a bug, document it precisely:

```
## Bug: [One-line description]

**Symptom:** What the user/system sees (exact error, wrong value, missing data)

**Expected:** What should happen instead

**Root Cause:** The exact code/config/data that causes the wrong behavior
  - File: path/to/file.py
  - Line: 42
  - Issue: [reads from wrong source / missing null check / wrong formula / etc.]

**Data Path:**
  Source → [correct here] → Storage → [correct here] → API → [BUG HERE] → Frontend

**Fix:** Minimal change description
  - Change X to Y in file Z
  - Add null check in function F
  - Update default value from A to B

**Verification:**
  - Before fix: [exact wrong output]
  - After fix: [exact correct output]
  - Tests: [which tests to run]

**Prevention:** What rule/check would have caught this earlier
```

---

## Quick Reference: Bug Hunting Order of Operations

When something is wrong and you don't know where to start:

1. **Reproduce** — confirm the exact symptom (don't guess)
2. **Isolate** — is it data, code, config, or infrastructure?
3. **Backwards trace** — start from symptom, work to source
4. **Check boundaries** — verify contracts at each layer crossing
5. **Check state** — is cached/in-memory data stale or lost?
6. **Check errors** — are any errors being swallowed silently?
7. **Check timing** — could this be a race condition or ordering issue?
8. **Check defaults** — is a constant/config set to a wrong value?
9. **Verify fix** — test against real data, not just logic
10. **Prevent recurrence** — add a test, a check, or a rule

---

## Part 6: Iterative Hunt Strategy

For exhaustive codebase-wide bug hunts, use this multi-pass convergence strategy. The goal is to reach **2 consecutive clean passes** (zero bugs found) before declaring the codebase clean.

### Process

```
Repeat:
  1. Run a fresh hunt pass applying ALL 7 techniques (Part 3)
  2. Log every bug found with file, line, root cause, and fix
  3. Fix all bugs found in this pass
  4. Run full test suite — all tests must pass
  5. If bugs were found → increment pass counter, go to step 1
  6. If zero bugs found → increment consecutive-clean counter
  7. If consecutive-clean counter reaches 2 → DONE
  8. If bugs were found in this pass → reset consecutive-clean counter to 0
```

### Pass Strategy: Vary Your Angle

Each pass should attack from a **different angle** to avoid blind spots:

| Pass | Primary Focus | Secondary Focus |
|------|--------------|----------------|
| 1 | Silent failures, consistency audit, hardcoded strings | State machine transitions, P&L formulas |
| 2 | is_live/default value safety, action enum consistency | Contract audit (DB ↔ API ↔ Frontend) |
| 3 | Boundary values, race conditions, error swallowing | Configuration validation, import correctness |
| 4 | Fresh eyes on all previous fix sites, edge cases | Documentation accuracy, remaining tech debt |

### What Counts as a Bug

**Counts (must fix):**
- Wrong default values (e.g., `is_live=True` when safe default is `False`)
- Missing model save after training (data loss on restart)
- Double-commit or redundant DB operations
- Swapped enum mappings (wrong action names for indices)
- Stale documentation that contradicts code (wrong dimension counts)
- Hardcoded strings that should use constants (in production code)
- Silent error swallowing in critical paths

**Does NOT count (note but skip):**
- Style issues in utility/admin scripts (not production)
- Redundant-but-harmless operations in one-off scripts
- Documentation preferences (wording, not factual errors)
- Potential future issues that aren't bugs today

### Tracking Template

Use a structured TODO list across passes:

```
Pass #1: [status] — found N bugs (brief list)
Pass #2: [status] — found N bugs (brief list)
Pass #3: [status] — CLEAN (0 bugs) ← 1st consecutive clean
Pass #4: [status] — CLEAN (0 bugs) ← 2nd consecutive clean ✅ DONE
```

### After Each Fix Round

1. Run `python -m pytest tests/ -v --tb=short` — **all tests must pass**
2. Verify fixes didn't introduce new issues (check related code paths)
3. Update stale documentation if the fix changes observable behavior
4. Log the fix in your pass summary (file, line, what changed, why)

### Exit Criteria

- **2 consecutive passes with 0 bugs found**
- **All tests pass** after every fix round
- **No known unfixed bugs** remaining in the backlog

This strategy works because each pass naturally finds fewer bugs, and requiring 2 consecutive clean passes provides high confidence that no systematic blind spots remain.
