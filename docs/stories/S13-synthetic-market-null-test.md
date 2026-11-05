# S13 — Synthetic market: remove the exploitable edge, add a null-market test

**Priority:** P0 · **Tier:** sonnet · **Depends on:** none · **Wave:** 5

## Story

As the person deciding whether the RL agent has found a real edge, I need the synthetic training market
to carry no free edge that real markets lack, and I need an automated null test. The pipeline must show
≈ −costs when there is no edge, so that a positive backtest means something.

## Background (what is wrong today)

- The synthetic price is computed as `p = CricketWinProbModel(state) ⊕ noise_logit`
  (`scripts/prepare_specialist_datasets.py:282-293`), where the noise is an **Ornstein–Uhlenbeck process**,
  `MarketNoise` L37-53 (`x ← 0.9x + N(0, 0.08)`, so its half-life is about 6.6 ticks).
- The observation contains the full match state: score, wickets, overs, run rates, innings and balls remaining
  (the `match_stats` group). So the agent can learn `fair = f(state)` and see that `price − fair` is noise that
  will revert. Back when the noise is low and lay when it is high, and the trade wins by construction.
- That is a **guaranteed synthetic edge with no live counterpart**. A real market already prices the
  public state, and its deviations are not a known, stationary, mean-reverting process. The positive ROI
  in `models/specialists/metadata.json` (all 15 models) is consistent with exploiting it. Those models also
  predate the S01 leak fix (they were built at 08:44, before the S01 commit at 15:08).
- Volume features carry no information. `volume_* = vol_base × uniform(0.7, 1.4)`
  (`prepare_specialist_datasets.py:304-316`) is independent of everything.
- `MarketNoise`, `CricketWinProbModel` and `synthesize_exchange_ticks` are copy-pasted in
  `scripts/cricsheet_harmonizer.py:93-220`.

## Context pack

- `scripts/prepare_specialist_datasets.py` (554 lines):
  - `SETTLEMENT_ONLY_KEYS` L31, `SYNTHETIC_SOURCE` L33;
  - `MarketNoise(seed, sigma=0.08, theta=0.10)` L37-53;
  - `CricketWinProbModel.calculate_win_prob(fmt, innings, overs, score, wickets, target, batting_is_home, is_women,
    specialist_key, noise_logit=0.0)` L55-136;
  - `synthesize_exchange_ticks(p_home, seed) -> (bh, lh, ba, la, ovr)` L138-161;
  - `parse_cricsheet_json(...)` L164-388 (noise created at L235, tick dict at L317-358, clock at L360);
  - `index_cricsheet_catalog` L390; `build_specialist_dataset(...)` L452; `main` L498.
- `scripts/cricsheet_harmonizer.py` (632 lines): duplicate classes L93-220; `parse_and_harmonize_match` L222 (noise at
  L293, ticks at L364).
- Callers: `scripts/weekly_cricsheet_sync_and_retrain.py:50` imports from `prepare_specialist_datasets`. Keep those
  names importable (re-export them).
- Test helpers: `CricketBettingEnv(data=[ticks], initial_bankroll=..., settlement_mode="real",
  exchange_commission=0.02, episode_sampling="sequential")`. The evaluator is
  `scripts/train_format_specialists.py::evaluate_specialist_holdout(model, key, name, episodes, *, policy=...)`.
  Baselines live in `backtesting/baselines.py` (`HoldPolicy`, `RandomPolicy`, `HeuristicPolicy`).
- Outcome-leak guard from S01: `scripts/audit_outcome_leakage.py`. Run it after the change; it must still pass.

## Owned files

- `scripts/prepare_specialist_datasets.py`
- `scripts/cricsheet_harmonizer.py` (replace the duplicate classes with imports only)
- New: `scripts/synthetic_market.py` (single home for `MarketNoise`, `CricketWinProbModel`, `synthesize_exchange_ticks`)
- New: `scripts/audit_synthetic_edge.py`
- New: `tests/unit/test_s13_synthetic_market.py`

## Tasks

1. Move the three shared pieces into `scripts/synthetic_market.py` and re-export them from both scripts.
2. Add `noise_mode: Literal["martingale", "ou", "none"] = "martingale"` to `MarketNoise`:
   - **martingale**: `x ← x + N(0, σ)`. The next-tick expected price change given the observation is 0.
   - **ou**: current behaviour, kept for experiments and marked "exploitable".
   - **none**.
   The fair-value layer must also not be a pure function of observable state the agent can invert. Add an
   unobserved per-match latent strength offset `z ~ N(0, 0.3)` in logit space, fixed for the match, so `f(state)`
   alone is not the price.
3. Volume: derive volumes from information that exists. Make them proportional to `|Δprice|` around events, plus a
   time-of-match profile, or emit `None` and document that the volume features are uninformative on synthetic data.
   Do not keep pure i.i.d. jitter.
4. Tag every tick with `"market_model": "<noise_mode>:v2"` so corpora are distinguishable. Bump the corpus format
   version wherever it is recorded.
5. `scripts/audit_synthetic_edge.py --corpus <path>` computes, on held-out ticks, how well the **next-tick mid-price
   change** can be predicted from the current tick's public fields (score, wickets, overs, rrr, current price, last
   3 price changes). It uses ridge regression from sklearn and reports out-of-sample R² and the sign hit rate. Exit
   code 1 if R² > 0.02 or hit rate > 0.55.
6. Null-market test: generate 30 matches with `noise_mode="martingale"`. Run `evaluate_specialist_holdout` with
   `policy="random"` and with `policy="heuristic"`. Mean per-match P&L must not be significantly positive: the
   bootstrap 95% CI must include or lie below 0. Also run the audit script and assert it passes on martingale data
   and **fails** on `ou` data. That proves the audit detects the edge.

## Success criteria

- [ ] The audit flags `ou` corpora (exit 1) and passes `martingale` corpora (exit 0) in a test.
- [ ] On a martingale fixture, the random and heuristic baselines show no significantly positive P&L.
- [ ] Only one definition of `MarketNoise` remains: `grep -rn "class MarketNoise" scripts` → 1 hit.
- [ ] `scripts/audit_outcome_leakage.py` still passes on a regenerated fixture corpus.
- [ ] Baseline suite passes.

## Verification

```bash
python -m pytest tests/unit/test_s13_synthetic_market.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Regenerating the real corpora in `data/specialists/` and retraining (S18).
- Real-odds data (S16).
