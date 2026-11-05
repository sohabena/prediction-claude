# <Project> Change Stories

Source: <audit / goal>, branch `<branch>` @ `<sha>` (<date>).
Each story is self-contained so that one sub-agent can execute it with no other context.

## Rules for every agent

1. **File ownership is strict.** Edit only your story's *Owned files*. New
   `tests/**/test_sNN_*.py` files are always allowed. If another file must change, stop and report.
2. No fabricated numbers in anything persisted, logged as a result, or shown in a UI.
3. Match surrounding code style.
4. Every behavioural change gets a test.
5. Run the story's Verification commands; no new failures in the baseline suite.
6. Do not commit.

## Token budget rules (read first)

- This README + your story file are your complete context. Do not explore the repo broadly.
- Read owned files in full only when editing them; otherwise use the line ranges in the context pack.
- If the context pack is wrong or missing something: one targeted grep, fix forward, report the gap.
- Report (under ~40 lines): files changed, success criteria ✅/❌, test summary, open issues.

## Shared context pack (applies to every story)

**Environment.** OS, language version, how to run tests, installed / missing libraries,
baseline test result (`<N> passed in <t>s` at `<sha>`), services that may be down.

**Conventions.** Logging, typing, model/schema library, test layout and fixtures.

**Domain model.** The core types (fields, enums) every story touches, with file:line.

**Key formulas / invariants.** Domain maths the executors must get right.

## Contracts between stories

| Contract | Owner | Consumers | Definition |
|----------|-------|-----------|------------|

## Story list

| ID | Title | Priority | Tier | Depends on | Owned files |
|----|-------|----------|------|-----------|-------------|

## Parallelisation waves

- **Wave 1 (parallel):** ...
- **Wave 2:** ...

## Out of scope (backlog)

- ...
