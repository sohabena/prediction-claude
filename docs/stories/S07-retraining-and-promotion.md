# S07 — Weekly retraining pipeline and promotion gate

**Priority:** P1 · **Depends on:** S01 (clean data), S02 (honest evaluator); uses S03's
`episode_sampling` if merged · **Wave:** 2

## Story

As the operator, I need the weekly job to actually learn from new matches, evaluate the
candidate against the current champion on a frozen, time-ordered holdout, and only
promote it if it is better, with one-command rollback, so that retraining can never
silently make the live model worse.

## Background (`scripts/weekly_cricsheet_sync_and_retrain.py`)

- **New data never trained on:** new matches are appended to the end of the corpus
  (`append_to_corpora`, 184-213) but fine-tuning cycles episodes from index 0 for 4 000
  steps (≈ 40 matches) — `retrain_specialist_model`, 289-365.
- **Holdout contamination:** the 70/15/15 split is by list index; appending shifts the
  boundaries so last week's holdout drifts into train. Corpora were shuffled
  (`random.Random(42).shuffle`), so the split is not chronological either.
- **Unconditional overwrite:** `model.save(model_path)` happens before evaluation; no gate,
  no versioning, no rollback; `register_specialist` always runs.
- **Silent DB failure:** `sync_new_matches_to_db` calls `asyncio.run` from inside the
  running loop of `run_weekly_sync_and_retrain` → `RuntimeError` caught → returns 0.
- No monitoring of PPO update health (approx KL, clip fraction, value loss).
- `rl/graduation.py`: `_consecutive_pass_days` increments per **call**, not per day, and
  lives in memory only.

## Context pack

- `scripts/weekly_cricsheet_sync_and_retrain.py` (471 lines): constants `SPECIALISTS_DIR, CORPUS_DIR, INCOMING_DIR,
  SYNC_STATUS_FILE, DEFAULT_RECENT_URL` L54-59; `get_match_signature(team_home, team_away, date_str)` L62;
  `index_existing_matches() -> (sigs, counts)` L69; `download_recent_archive(url, target_dir)` L96;
  `classify_and_harmonize_incoming(files, existing_sigs) -> (new_by_spec, new_count, skipped)` L115 (uses
  `scripts.prepare_specialist_datasets.parse_cricsheet_json`); `append_to_corpora(new_by_spec)` L184-213 (writes `.bak`,
  calls `sync_new_matches_to_db`); `async_sync_new_matches_to_db` L216-278 (upsert `match_training_status` via
  `get_session`); `sync_new_matches_to_db` L281-286 (**nested `asyncio.run`**); `retrain_specialist_model(key,
  timesteps=4000, target_bets_per_match=25)` L289-365; `run_weekly_sync_and_retrain(url, local_zip, timesteps,
  force_retrain)` L368-458; CLI L461-471.
- Imports from `scripts.train_format_specialists`: `ALL_SPECIALISTS_CONFIG, SPECIALIST_MAP, TEAM_RATINGS,
  evaluate_specialist_holdout, persist_virtual_bets, update_redis_model_zoo_state`. After S02 the metrics dataclass
  has new fields (bootstrap CI, `edge_vs_random`, baselines) — get names with
  `grep -n "class SpecialistEvaluationMetrics" -A45 scripts/train_format_specialists.py`.
- Corpus match dicts carry `start_time` (ISO) — see README shared context.
- `rl/model_zoo.py:121 register_specialist(key, model_path, format_name, matches_count, metrics)` writes
  `metadata.json[key] = {key, path, format, matches_trained_on, metrics, active}` and invalidates the cache. Write
  champion/version/history keys with your own atomic writer in the script (model_zoo is S06's); keep `path` = champion
  so `ModelZoo.get_agent` loads it.
- SB3 update stats: in a `BaseCallback._on_rollout_end`, read `self.model.logger.name_to_value` (`"train/approx_kl"`,
  `"train/clip_fraction"`, `"train/value_loss"`; values refer to the previous update). Return `False` from `_on_step`
  to abort training.
- `rl/graduation.py` (127 lines): `GraduationEvaluator()` (`criteria = GRADUATION_CRITERIA`, `required_days =
  GRADUATION_REQUIRED_DAYS = 14`); `evaluate(win_rate, roi, sharpe_ratio, max_drawdown, profitable_days, total_bets,
  avg_clv=0.0) -> GraduationStatus` L31-120; `get_progress_pct()` L122. Real thresholds (`shared/constants.py:57-65`):
  win_rate ≥ 0.55, roi ≥ 0.08, sharpe ≥ 1.5, max_drawdown ≤ 0.15, profitable_days ≥ 10, bet_volume ≥ 100,
  avg_clv ≥ 0. Callers: `rl/orchestrator.py:85` (`GraduationEvaluator()` — keep the no-arg constructor) and
  `tests/unit/test_graduation.py:11`. Inject `now_fn` for tests.

## Owned files

- `scripts/weekly_cricsheet_sync_and_retrain.py`
- `rl/graduation.py`
- `tests/unit/test_graduation.py` (update)
- New: `scripts/rollback_specialist.py`
- New: `tests/unit/test_s07_retrain_pipeline.py`

## Tasks

1. **Chronological corpus:** when appending, sort the combined corpus by `start_time`
   (fallback: first tick time) and de-duplicate by match signature.
2. **Frozen split manifest:** `data/specialists/splits_<key>.json` with
   `{"holdout_ids": [...], "val_ids": [...], "created_at": ..., "cutoff_date": ...}`.
   Created once from the latest 15 % (holdout) and the 15 % before it (val) by date.
   On later runs, existing IDs never move; new matches go to **train** except a
   configurable `--holdout-refresh` that appends the newest N matches to a *new* holdout
   generation (v2, v3 …) without moving old ones into train.
3. **Fine-tune data mix:** build the training set as all new matches since the last run
   plus an equal-size (min 200) uniform sample of older training matches (replay to limit
   forgetting). Use `episode_sampling="random"` if the env supports it; otherwise shuffle
   the list before constructing the env.
4. **Training budget:** default `--timesteps` = `max(50_000, 20 × total ticks in the mix)`;
   fresh learning-rate schedule with lower LR for fine-tunes (`lr × 0.3`), and
   `reset_num_timesteps=False` so logs continue.
5. **Update-health guard:** a callback reads SB3's `train/approx_kl`, `train/clip_fraction`,
   `train/value_loss`; abort the fine-tune (candidate rejected) if mean approx_kl > 0.05
   over the last 5 updates or value loss is NaN/inf. Log all three to the status file.
6. **Candidate → evaluate → promote:**
   - save to `models/specialists/candidates/<key>_<UTC timestamp>.zip`;
   - evaluate candidate **and** current champion with S02's `evaluate_specialist_holdout`
     on the same frozen holdout (all generations);
   - promote only if: candidate mean per-match P&L − champion ≥ 0 **and** the candidate's
     95 % bootstrap CI lower bound for mean P&L > 0 **and** max drawdown ≤
     `MAX_DRAWDOWN_PERCENT` **and** `edge_vs_random > 0`;
   - promotion = copy to `models/specialists/<key>_v<N+1>.zip`, update `metadata.json`
     (`champion_path`, `version`, `history[]` with previous path & metrics) atomically
     (write temp + `os.replace`);
   - never overwrite or delete an older version.
7. `scripts/rollback_specialist.py --key ipl_men [--to-version N]` restores the previous
   champion pointer in `metadata.json` and prints what changed.
8. **Async bug:** make `append_to_corpora` async (or return the records) and `await`
   `async_sync_new_matches_to_db` directly; remove the nested `asyncio.run`. Errors must be
   logged and reflected as `"db_sync": "failed"` in the status record, not swallowed.
9. **Graduation:** count consecutive **calendar days** (UTC) on which all criteria passed;
   multiple evaluations on one day count once; a failed evaluation resets. Persist state to
   a JSON path passed in the constructor (default `models/graduation_state.json`), loading
   it on init.
10. `--dry-run` flag: does everything except promotion and DB writes.

## Success criteria

- [ ] Test: after appending 10 new matches, all 10 appear in the fine-tune training set,
      and no ID in the existing holdout manifest appears in train.
- [ ] Test: running the split builder twice with extra matches leaves `holdout_ids` unchanged.
- [ ] Test: a candidate that is worse than the champion is not promoted; `metadata.json`
      champion pointer is unchanged; candidate file kept under `candidates/`.
- [ ] Test: a better candidate is promoted to `_v<N+1>` and `rollback_specialist.py`
      restores `_v<N>`.
- [ ] Test: the DB sync coroutine is awaited (mock) — no `asyncio.run` remains inside an
      async call path (`grep -n "asyncio.run" scripts/weekly_cricsheet_sync_and_retrain.py`
      shows only the `__main__` entry).
- [ ] Test: KL guard aborts on injected approx_kl = 0.2.
- [ ] Graduation: 3 evaluations on the same day → `consecutive_days == 1`; state survives
      re-instantiation.
- [ ] `python scripts/weekly_cricsheet_sync_and_retrain.py --local-zip <fixture> --dry-run --timesteps 2000`
      completes against a temp corpus directory (add `--corpus-dir` / `--models-dir` args).

## Verification

```bash
python -m pytest tests/unit/test_s07_retrain_pipeline.py tests/unit/test_graduation.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- PPO hyper-parameters and architecture (S09).
- Re-running retraining on production corpora.
