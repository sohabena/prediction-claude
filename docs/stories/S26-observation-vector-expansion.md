# S26 — Observation vector expansion: incorporate Elo, venue, and toss priors

**Priority:** P1 · **Tier:** sonnet · **Depends on:** S24, S25 · **Wave:** 6
**Conflicts with:** none (strictly scoped to feature pipeline, constants, and feature parity tests)

## Story

As the ML engineer, I need to expand the observation vector from 50 to 54 dimensions to incorporate pre-match Team Elo differential, venue par run-rate ratio, and toss advantage, while preserving strict train-serve parity and zero-lookahead protection.

## Background

- PHOENIX currently uses a 50-dimensional observation vector ([`features/pipeline.py`](file:///c:/Users/sohai/Downloads/prediction-claude/features/pipeline.py#L68-L86)).
- Stories S24 and S25 introduce zero-lookahead Team Elo ratings and empirical venue priors.
- We need to expose these high-leverage context features to the RL agents and supervised models in a normalized, bounded manner without breaking train-serve parity.

### New Features (50 → 54)

| Index | Name | Description | Normalization / Bounds |
|---|---|---|---|
| **50** | `team_elo_diff` | Pre-match $(R_{\text{home}} - R_{\text{away}}) / 400.0$ | Logistic scale, typically $[-1.5, +1.5]$ |
| **51** | `team_elo_home_norm` | Normalized home rating: $(R_{\text{home}} - 1500.0) / 500.0$ | Zero-centered at 1500 base |
| **52** | `venue_par_rr_ratio` | $\text{current\_run\_rate} / \text{venue\_avg\_par\_run\_rate}$ | Bounded $[0.2, 3.0]$; default $1.0$ if unknown |
| **53** | `toss_advantage_home` | Toss winner and optimal decision alignment | $+1.0$ (home won toss & chose optimal), $-1.0$ (away), $0.0$ (neutral) |

## Context Pack

- `shared/constants.py`:
  - `OBSERVATION_SIZE = 50` -> update to `54`.
- `features/pipeline.py`:
  - Update `_FEATURE_NAMES` tuple to include the 4 new features.
  - Wire in `shared/elo.py` and `shared/venue_priors.py` lookups.
- `shared/schemas.py`:
  - Update `MatchContext` to carry optional `venue: str = ""`, `toss_winner: str = ""`, `toss_decision: str = ""`.
- `tests/unit/test_s11_feature_parity.py`:
  - Update parity fixture and assertions for 54 dimensions.

## Owned Files

- `shared/constants.py` (only `OBSERVATION_SIZE` and feature group comments)
- `features/pipeline.py`
- `features/extractors/match_stats_features.py` (or new `features/extractors/prior_features.py`)
- `tests/unit/test_s11_feature_parity.py`
- `tests/unit/test_features.py`

## Tasks

- [ ] Update `shared/constants.py`: set `OBSERVATION_SIZE = 54`.
- [ ] Implement `features/extractors/prior_features.py`:
  - Computes `compute_prior_features(team_home, team_away, venue, format, as_of_date, current_rr) -> list[float]`.
  - Handles missing data gracefully (fallbacks to $0.0$ or $1.0$).
- [ ] Update `features/pipeline.py`:
  - Add the 4 feature names to `_FEATURE_NAMES`.
  - Append prior features to the output vector.
  - Ensure OnlineNormalizer handles the expanded 54-dim space.
- [ ] Update `tests/unit/test_s11_feature_parity.py` and `tests/unit/test_features.py` to assert 54-dimensional outputs and parity.

## Verification

```bash
# Run feature unit tests and train/serve parity suite
python -m pytest tests/unit/test_features.py tests/unit/test_s11_feature_parity.py -v
```

