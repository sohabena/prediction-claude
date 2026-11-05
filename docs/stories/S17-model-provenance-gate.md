# S17 — Model zoo provenance gate: never trade a model that was not honestly evaluated

**Priority:** P0 · **Tier:** sonnet · **Depends on:** none · **Wave:** 5

## Story

As the operator, I need the live loop to refuse any specialist whose metadata does not prove it was trained after
the leakage fix and beat the random baseline on holdout. Then a stale or fabricated model can never
place bets just by being present on disk.

## Background (what is wrong today)

- All 15 entries in `models/specialists/metadata.json` are pre-remediation:
  - the files are timestamped 01:11–08:44 on 2026-11-02, before the S01 commit `6d8cf46` (15:08);
  - 4k–15k timesteps;
  - none has `edge_vs_random`, `code_version` or `holdout_match_ids` (the S02 fields);
  - drawdowns are 90–440%.
  Every one is `"active": true`.
- `rl/model_zoo.py` does not check `active` or any metrics when loading (`get_agent` L364-402 →
  `_try_load` L340). Anything matching the path pattern is traded.
- `register_specialist` (L129-146) always sets `"active": True`.

## Context pack

- `rl/model_zoo.py` (577 lines):
  - `METADATA_FILE` L36;
  - `class ModelZoo` L75; `__init__(models_dir)` L94-106; `_load_metadata` L107-118; `save_metadata` L120-127;
  - `register_specialist(...)` L129-152;
  - `resolve_specialist_key(...)` L154-302;
  - `_candidate_paths(key)` L303-321; `_recently_failed` L322; `_file_info` L327; `_try_load(key, path)` L340-354;
  - `get_agent_version` L358; `get_agent(key, allow_load)` L364-402 (falls back to `"default"` / `DEFAULT_MODEL_PATH`
    at ~L395);
  - `preload` L403; `reload_if_changed` L413; `_reload_sync` L421-454; `get_agent_for_match(...)` L455-482;
  - `list_specialists` L534; `get_model_zoo()` L572.
- Metadata written by S02/S07: `metrics` has `edge_vs_random`, `code_version`, `evaluated_at`,
  `holdout_match_ids` (or hash), `policy`, `baselines`, and a bootstrap CI for mean per-match P&L. Grep
  `edge_vs_random\|bootstrap` in `scripts/train_format_specialists.py` for the exact key names and use those.
  S07's promotion gate writes metadata in `scripts/weekly_cricsheet_sync_and_retrain.py` (grep
  `register_specialist\|save_metadata`).
- Callers to keep working: `virtual_trading/live_trading_loop.py:353-375` (`get_agent_for_match(..., allow_load=False)`,
  then `get_agent_version`), `rl/orchestrator.py` (grep `get_model_zoo`), `backend/routers` (grep `list_specialists`).
- Corpus provenance from S13: ticks carry `market_model`. If metadata records the corpus `market_model`, require it to
  be `martingale:v2` or `real`.

## Owned files

- `rl/model_zoo.py`
- New: `tests/unit/test_s17_provenance_gate.py`

## Tasks

1. `ModelZoo.is_tradeable(key) -> tuple[bool, str]`. It returns True only if **all** of these hold:
   - `active` is true;
   - `metrics.code_version` is present;
   - `metrics.edge_vs_random` is present;
   - the lower bound of the mean per-match P&L 95% CI is > 0;
   - `metrics.matches_tested ≥ 50`;
   - `metrics.max_drawdown_pct ≤ 50` (on the S02 definition);
   - if a `market_model` is recorded, it is not `ou`.
   The reason string names the first failing check.
2. `get_agent` / `get_agent_for_match` return `(None, key)` for non-tradeable keys and log `specialist_not_tradeable`
   with the reason (once per key per process).
3. The default-model fallback goes through the same gate (key `"default"`, metadata entry optional). No metadata
   means not tradeable.
4. Env override `PHOENIX_ALLOW_UNVERIFIED_MODELS=1` (read with `os.getenv`, default off) bypasses the gate for
   development only. Log a warning on every load.
5. `register_specialist(..., active: bool = False)`: new registrations are inactive until the promotion gate
   activates them. Check the S07 caller passes `active=True` explicitly after promotion; if it does not, **report**
   it, because that file is not owned.
6. `list_specialists()` adds `tradeable` and `reason` per entry, so the UI shows why a model is not trading.

## Success criteria

- [ ] With the current `models/specialists/metadata.json` (copied to a temp dir in the test), **no** specialist is
      tradeable, and the reasons include `missing code_version`.
- [ ] A synthetic metadata entry that meets every rule is tradeable and loads.
- [ ] The live loop gets `None` for a non-tradeable match and places no bet (test with a fake zoo or a tmp models dir).
- [ ] With the override env var set, the gate is bypassed and a warning is logged.
- [ ] Baseline suite passes.

## Verification

```bash
python -m pytest tests/unit/test_s17_provenance_gate.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Retraining (S18).
- Editing `metadata.json` by hand: never do that.
