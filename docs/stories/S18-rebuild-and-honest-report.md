# S18 — Rebuild corpora and specialists, publish an honest strategy report

**Priority:** P0 (operational) · **Tier:** sonnet · **Depends on:** S11, S13, S14, S15, S16, S17 committed · **Wave:** 6

## Story

As the strategy owner, I need every artifact rebuilt with the corrected pipeline and one report that
compares RL, value betting and the baselines on synthetic **and** recorded real odds. Then the
decision to continue, change strategy or stop rests on evidence.

## Background

- Every model and corpus on disk predates the fixes (see S17 background). Observations change in S11 and the
  market changes in S13, so every existing model is invalid.
- The 15-specialist zoo spreads very little data across many keys: `first_class` and `list_a` are trained for 4k
  timesteps. A smaller set of family models (T20 / ODI / multi-day) is likely to generalise better. This story
  measures that rather than assuming it.

## Context pack

- Data: `scripts/prepare_specialist_datasets.py` (`main`, `--help` for args) writes
  `data/specialists/corpus_<key>.json.gz`. **Back up the existing corpora** to `data/specialists/_pre_s13/` before
  regenerating. Do not delete `.bak` files.
- Synthetic audits: `scripts/audit_outcome_leakage.py`, `scripts/audit_synthetic_edge.py` (S13). Both must pass on
  every regenerated corpus before training.
- Fair value: `scripts/train_fair_value.py --key <key>` (S14).
- Training: `scripts/train_format_specialists.py` (`main` / `train_single_specialist`; S09 regime: n_envs,
  VecNormalize, chronological split). Check `--help` for the timesteps and key flags. **Train into a new versioned dir**
  (`models/specialists_v2/`), never overwriting `models/specialists/`.
- Evaluation: `evaluate_specialist_holdout(..., policy=...)` for `model`, `value`, `heuristic`, `random`, `hold`.
  Recorded real odds: `scripts/export_recorded_corpus.py` + `load_recorded(key)` (S16).
- Gate: `ModelZoo.is_tradeable` (S17). The report states which models pass.
- Compute budget: training is CPU-bound on Windows (SubprocVecEnv). Run long jobs in the background and log to
  `logs/s18_*.log`. If the full set exceeds ~2 h, train the three families (`t20i_men`, `odi_men`, `test_men`, or the
  family keys) first and report.

## Owned files

- New: `scripts/rebuild_all.py` (orchestration only; calls existing scripts and functions)
- New: `docs/reports/strategy-report-<date>.md`
- New output dirs: `models/specialists_v2/`, `models/fair_value/`, `data/specialists/` (regenerated), `data/recorded/`
- New: `tests/unit/test_s18_rebuild_smoke.py` (dry-run mode on a 3-match fixture)

## Tasks

1. `scripts/rebuild_all.py --keys ... --dry-run` runs these steps for each key, stopping on the first failure:
   1. back up;
   2. regenerate the corpus (`noise_mode=martingale`);
   3. run both audits;
   4. train the fair model;
   5. train PPO;
   6. evaluate all five policies;
   7. write metadata (inactive);
   8. run `is_tradeable`.
2. Run it for the three family keys first, then the rest if time allows.
3. Export recorded matches (S16) and evaluate `model`, `value`, `random` and `hold` on them. Report n matches.
4. Write `docs/reports/strategy-report-<date>.md`:
   - a table per key of mean per-match P&L with its 95% CI, CLV mean with its CI, max drawdown %, bets per match and
     commission, for each policy, on synthetic and on recorded;
   - the fair-model calibration (Brier and ECE against market-implied);
   - which models pass the S17 gate;
   - a recommendation (continue RL / switch to value policy / collect more real data), with the reasoning.
   **No number may be typed by hand; every number comes from the JSON outputs.**
5. Do not activate any model. Activation is the user's decision after reading the report.

## Success criteria

- [ ] The dry run on the fixture completes every step in < 2 min (test).
- [ ] Both audits pass on every regenerated corpus (log lines quoted in the report).
- [ ] The report exists, every table cell traces to a JSON file listed in an appendix, and the recorded-odds sample
      size is stated.
- [ ] `models/specialists/` is unchanged (`git status` shows no modification there).
- [ ] Baseline suite passes.

## Verification

```bash
python -m pytest tests/unit/test_s18_rebuild_smoke.py -q
python scripts/rebuild_all.py --keys t20i_men --dry-run
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Activating models, changing the live configuration, any real-money step.
