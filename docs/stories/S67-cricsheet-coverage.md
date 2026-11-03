# S67 — Cricsheet coverage for every competition LotusBook lists

**Priority:** P0 · **Tier:** haiku · **Depends on:** S58, S60, S61 · **Wave:** 17C · **Plan:** docs/architecture/training-strategy.md §3

## Story

As the blend model, I need a strength-model probability for most recorded matches. Today only 1 of 22 recorded
matches can be rated.

## Background (measured 2026-11-03)

- Local Cricsheet data covers `data/raw/{ipl,odis,t20s,tests}` only (`scripts/download_cricsheet_data.py` downloads
  t20s, odis, tests, ipl zips). Leagues LotusBook lists are missing: WBBL/BBL, PSL, CPL, SA20, ILT20, MLC, The Hundred,
  Vitality Blast, Super Smash, Nepal Premier League, Ford Trophy and other List A, NCL GT20, CWC League Two and more.
- `data/raw/t20s` has **no Afghanistan file at all** (grep finds 0), so the T20I set itself is incomplete.
- `training/dataset.py match_family` returns `unknown` for "Ford Trophy" (50 overs), "ICC Cricket World Cup League
  Two" (ODI) and "Emirates D20" (T20).
- Women's sides arrive as "Sydney Thunder W"; in Cricsheet they are "Sydney Thunder" with `info.gender = female`.
  `strategy/live_context.gender_of` and `training/segments.gender_of` disagree (bare "WBBL").

## Owned files

- `scripts/download_cricsheet_data.py` (dataset list and a `--dry-run` that prints URLs and sizes via HEAD requests
  only when asked; do NOT download anything in this story)
- `scripts/train_fair_value.py`: only the `FAMILY_DIRS` mapping so ratings read the new folders (keep existing keys)
- `strategy/live_context.py`: `gender_of` and team-name normalisation for women's suffixes only
- `training/dataset.py`: `match_family` only
- `training/segments.py`: `gender_of` only, sharing one rule with live_context (bump `SEGMENTS_VERSION`)
- `scripts/cricsheet_harmonizer.py`: aliases only (e.g. "Cdb A", franchise short names seen in recordings)
- New `tests/unit/test_s67_coverage.py`

## Tasks

1. Downloader: use Cricsheet's `all_json.zip` (every match, all formats and leagues) as the primary dataset, unzipped
   into `data/raw/all/`, keeping the existing per-format folders working. Classify each file by `info.match_type`
   (T20/IT20 → t20, ODI/ODM → odi, Test/MDM → test) and `info.gender`; the ratings must read `data/raw/all` without
   double counting files that also exist in the old folders (dedupe by Cricsheet match id = file stem).
2. `match_family`: competition and name rules for List A / one-day (Ford Trophy, One-Day Cup, Marsh Cup, League Two,
   Plunket?? no: Plunket is first-class → test), T20 tokens (D20, T20, GT20, Blast, Smash, Premier League with T20
   context), and a fallback from the Cricsheet link's `match_type` when linked.
3. One gender rule used by both modules: women's when the competition contains women/WBBL/WPL/"Women's" or a team ends
   with " W" or "Women"; normalise "X W" to "X" for rating lookups with gender female.
4. Tests: every recorded competition string in the current dataset (see `data/training/*/matches.jsonl`) maps to a
   family (no `unknown` for the 10 listed above); WBBL sides resolve to female and strip " W"; dedupe of overlapping
   Cricsheet files; the downloader dry run makes no GET requests (mock the HTTP layer).
5. Report the expected rated-match coverage after a download by checking team names against the Cricsheet register of
   people/teams is NOT possible offline — instead list, for each recorded real match, which teams are unknown today.

## Guardrails

Do not download files (the operator approves the download). No DB writes, no Docker, do not commit.
