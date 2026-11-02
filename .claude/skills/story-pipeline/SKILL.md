---
name: story-pipeline
description: Token-efficient plan→execute pipeline. Opus audits and writes self-contained change stories (with context packs, owned files, success criteria) into docs/stories/; Sonnet sub-agents implement one story each in parallel waves; Haiku sub-agents verify. Invoke manually with `/story-pipeline plan <goal>`, `/story-pipeline run <wave|story ids>`, `/story-pipeline verify <story ids>`, or `/story-pipeline status`.
disable-model-invocation: true
argument-hint: "plan <goal> | run <wave N | S01,S03> | verify <S01,...> | status"
---

# Story pipeline (plan with Opus, execute with Sonnet, verify with Haiku)

Arguments: `$ARGUMENTS`

The point of this pipeline is **context economy**. The planner (the expensive model, with
the full picture) pays once to read the codebase and writes everything an executor needs
into the story file. Executors never re-explore the repo; they read one story + the README
and edit only their owned files. Verifiers are cheap and mechanical.

Pick the mode from the first word of the arguments. If there are no arguments, run `status`.

---

## Mode `plan <goal>` — run on Opus (the main session)

1. **Scope the work.** Read `docs/stories/README.md` if it exists (append new stories, don't
   renumber old ones). Otherwise create it from [templates/README-template.md](templates/README-template.md).
2. **Investigate once, cheaply.** Use targeted `Grep`/`Read` with offsets, not whole-repo
   reads. For every module a story will touch, collect:
   - an outline (`grep -nE "^class |^def |^    def |^async def "`), with line numbers;
   - exact signatures and data shapes the executor must keep compatible;
   - **every caller/importer** of anything that will change (`grep -rn "<symbol>(" --include=*.py`);
   - test fixtures/helpers to reuse, and the current test baseline (run the suite once and
     record pass count and time).
   Verify each claim in code before it goes into a story. If the investigation contradicts an
   earlier finding, correct the story and tell the user.
3. **Cut stories by file ownership**, not by topic. Two stories that must edit the same file
   either merge, or go in different waves with a stated order. Shared test files get
   exactly one owner; other stories re-test in their own `test_sNN_*.py`.
4. **Define contracts between stories** (dict keys, method signatures, dataclass fields) in the
   README, owned by one story and consumed by the others, so parallel executors agree without
   talking to each other.
5. **Write each story** from [templates/story-template.md](templates/story-template.md). It is
   mandatory to fill in: Background with file:line evidence, **Context pack** (signatures, line
   ranges, callers, data shapes, gotchas), Owned files, numbered Tasks, checkable Success
   criteria (each one testable), Verification commands, Out of scope, model Tier.
   Rule of thumb: if the executor would need to open a file you didn't list, the context pack
   is incomplete.
6. **Index.** Update the README story table (ID, title, priority, tier, depends on, owned
   files) and the parallel waves.
7. Stop and report: story list, waves, anything unverified. Do not start executors in this mode.

## Mode `run <wave N | S01,S03,...>` — dispatch executors

1. Read `docs/stories/README.md` (story table and waves only). Resolve the story IDs. Refuse to
   run two stories in parallel if their *Owned files* overlap, or if a dependency is not
   merged/committed yet (check `git log --oneline` for `S<NN>` commits) — say which.
2. For each story start one sub-agent **in the same message** so they run in parallel:
   - `Agent(subagent_type="general-purpose", model=<story tier, default "sonnet">,
     description="S<NN> <short title>", prompt=<the dispatch prompt below>)`.
   - Use `isolation: "worktree"` only if the user asks for branches per story; the default is
     the shared working tree (safe because owned files are disjoint).
3. Dispatch prompt (fill in `NN`; nothing else — the story file carries the context):

   > You are executing one change story in this repository. Read `docs/stories/README.md`
   > (sections "Rules for every agent", "Token budget rules", "Shared context pack", and any
   > contract table your story mentions) and then `docs/stories/SNN-*.md`. Implement only
   > that story and edit only its **Owned files**. Do not explore the repository beyond the
   > story's context pack; use targeted greps if something is missing and report the gap.
   > Run the story's **Verification** commands. Do not commit. Reply with the report format
   > from the README (files changed, success criteria ✅/❌, test summary, open issues), under
   > 40 lines.

4. When each executor reports, do **not** paste its full report to the user. Collect: ✅/❌
   counts, open issues, files changed. Then go to `verify` for those stories.

## Mode `verify <S01,...>` — run cheap checks with Haiku

For each story start a sub-agent with `model="haiku"` (all in one message) and this prompt:

> Verify story SNN in this repo. Read only `docs/stories/SNN-*.md`. (1) Run
> `git status --porcelain` and `git diff --stat`; list any changed file that is NOT in the
> story's Owned files (new `tests/**/test_sNN_*.py` files are allowed). (2) Run every command
> under **Verification** and report pass/fail counts. (3) For each success criterion, find the
> test or code that proves it (grep) and mark ✅, ❌ or ❓ (cannot tell). Do not edit any file.
> Reply in under 25 lines.

Then, in the main session:
- If a verifier reports files outside ownership or ❌ criteria, either fix small things
  yourself or re-dispatch the executor with `SendMessage` (keeps its context) listing exactly
  what failed.
- Run the full unit suite once yourself after a wave.
- Offer to commit **one commit per story**: `git add <owned files + its tests>` and a message
  `<type>(<area>): S<NN> <title>` ending with the attribution trailer required by the session.
  Commit only with the user's go-ahead.

## Mode `status`

Read the README story table, then `git log --oneline -50 | grep -E "S[0-9]{2}"` and
`git status --porcelain`. Report per story: done (committed), in progress (uncommitted changes
in its owned files), or pending; and which wave can run next.

---

## Token-saving rules (all modes)

- The planner writes line ranges, not code dumps; executors `Read` with `offset/limit`.
- Never ask an executor to "understand the codebase". Its context is the story.
- Keep reports short and structured; the orchestrator only needs ✅/❌, files and open issues.
- Prefer `SendMessage` to an existing executor over starting a fresh one for fixes.
- Use the cheapest tier that can do the job: Opus plans and reviews, Sonnet implements
  stories that need judgement, Haiku verifies and does fully mechanical edits.
