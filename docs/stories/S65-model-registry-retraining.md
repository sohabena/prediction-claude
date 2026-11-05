# S65 — Model registry, champion/challenger and weekly retraining

**Priority:** P1 · **Tier:** haiku · **Depends on:** S58–S63 · **Wave:** 17C · **Plan:** docs/architecture/training-strategy.md §3

## Story

As the operator of an unattended system, I want models retrained weekly on the newest data, kept under versions, and
replaced only when the referee says the new one is better.

## Owned files

- New `training/registry.py`, `scripts/retrain_weekly.py`, `tests/unit/test_s65_registry.py`
- `docker-compose.yml`: a `trainer` service in profile `recording` (scraper image, `restart: unless-stopped`,
  `python scripts/retrain_weekly.py --loop`, `./logs`, `./models`, `./data`, `./reports` mounted, POSTGRES_* env as
  the scraper); `Dockerfile.scraper` COPY lines for what it needs
- `scripts/data_quality_monitor.py` and `tests/unit/test_s55_data_quality.py`: one new check `model_drift`
  (calibration of the champion blend on the last 7 days of settled recorded matches; population stability index of
  `p_mkt` and `p_model`), warn-only until there are ≥ 50 settled matches

## Tasks

1. Registry layout `models/registry/<name>/<version>/` with the artefact and `manifest.json` (data window, rows, git
   sha, metrics, gate status, parent version). `register`, `champion(name)`, `promote(name, version, evidence)`
   (only when the S63 report shows the challenger better on the same held-out window; record why).
2. `scripts/retrain_weekly.py`: Cricsheet sync (reuse `scripts/weekly_cricsheet_sync_and_retrain.py` pieces), S60
   train, S58 dataset build, S61 fit, S63 gate report, register; promote only with evidence. `--loop` sleeps until the
   next Monday 03:00 UTC; `--once` runs now.
3. Tests with temp dirs and fakes.

## Guardrails

Never write to the DB in tests. Do not start containers, do not commit.
