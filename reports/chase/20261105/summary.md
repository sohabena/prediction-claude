# S107 chase model, 20261105

Model `models/chase/20261105.joblib` (version s107-chase-1), scikit-learn 1.6.1.
P(the chasing side wins) from one state before each legal ball of a 20-over T20 second innings. Core: runs needed, balls left, wickets in hand, required rate. Players: core plus both batters' career strike rate, average and balls (as of the match's date) and their runs and balls in this innings. Empirical: the base-rate table of the same state (runs bucket, balls bucket, wickets).

## Sample

- Cricsheet T20 matches read: 14721; chases used: 13387.
- Excluded, by reason: {'method': 507, 'no_result': 108, 'not_two_innings': 212, 'super_over': 99, 'not_20_overs': 408}.
- Training rows (final model): 753974 from 13387 chases. Subsampling: all states of the last 60 legal balls; one state per over before that.
- Walk-forward test: 12035 chases, 672164 rows, 627010 rows with both batters matched to a career record.

## Walk-forward log loss (season Y predicted by a model fitted on seasons before Y)

| model | rows | log loss |
|---|---:|---:|
| core | all | 0.3409 |
| players | matched | 0.3212 |
| core | matched (same rows as players) | 0.3470 |
| players | all rows (unmatched batters fall back to NaN) | 0.3155 |
| empirical base rate | all | 0.3659 |
| empirical base rate | matched | 0.3703 |

Players minus core on the matched rows, per match, clustered: -0.02587 [-0.02974, -0.02208] over 11847 chases. Negative means players is better.

## Per season

| season | test chases | train chases | core | players | empirical | players matched | core matched |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 2015 | 398 | 1352 | 0.3426 | 0.3604 | 0.3888 | 0.3766 | 0.3555 |
| 2016 | 553 | 1750 | 0.3667 | 0.3618 | 0.4001 | 0.3664 | 0.3723 |
| 2017 | 492 | 2303 | 0.3630 | 0.3650 | 0.3956 | 0.3729 | 0.3696 |
| 2018 | 789 | 2795 | 0.3399 | 0.3307 | 0.3885 | 0.3397 | 0.3496 |
| 2019 | 1106 | 3584 | 0.3336 | 0.3257 | 0.3617 | 0.3305 | 0.3393 |
| 2020 | 493 | 4690 | 0.3687 | 0.3567 | 0.3893 | 0.3665 | 0.3775 |
| 2021 | 1043 | 5183 | 0.3255 | 0.3046 | 0.3490 | 0.3107 | 0.3321 |
| 2022 | 1227 | 6226 | 0.3531 | 0.3259 | 0.3741 | 0.3287 | 0.3545 |
| 2023 | 1249 | 7453 | 0.3331 | 0.3039 | 0.3526 | 0.3095 | 0.3384 |
| 2024 | 1605 | 8702 | 0.3295 | 0.3024 | 0.3469 | 0.3075 | 0.3346 |
| 2025 | 1611 | 10307 | 0.3497 | 0.2986 | 0.3718 | 0.3036 | 0.3551 |
| 2026 | 1469 | 11918 | 0.3277 | 0.2741 | 0.3465 | 0.2800 | 0.3357 |

## Tail calibration (walk-forward, pooled)

States the model puts under 10% or over 90%: rows, mean predicted, observed win rate.

| model | P under 10%: rows | mean predicted | observed | P over 90%: rows | mean predicted | observed |
|---|---:|---:|---:|---:|---:|---:|
| core | 200728 | 0.0258 | 0.0263 | 148588 | 0.9694 | 0.9619 |
| players_matched | 190272 | 0.0250 | 0.0275 | 154339 | 0.9721 | 0.9671 |
| empirical | 182206 | 0.0212 | 0.0232 | 140924 | 0.9734 | 0.9547 |

Reliability by bin (walk-forward, pooled):

core:

| bin | rows | mean predicted | observed |
|---|---:|---:|---:|
| [0.00, 0.02) | 115056 | 0.0047 | 0.0032 |
| [0.02, 0.05) | 41649 | 0.0337 | 0.0325 |
| [0.05, 0.10) | 44023 | 0.0736 | 0.0806 |
| [0.10, 0.20) | 58397 | 0.1460 | 0.1770 |
| [0.20, 0.50) | 106663 | 0.3410 | 0.3589 |
| [0.50, 0.80) | 104557 | 0.6581 | 0.6404 |
| [0.80, 0.90) | 53231 | 0.8529 | 0.8199 |
| [0.90, 0.95) | 39261 | 0.9268 | 0.9057 |
| [0.95, 0.98) | 35795 | 0.9660 | 0.9566 |
| [0.98, 1.00) | 73532 | 0.9939 | 0.9945 |

players_matched:

| bin | rows | mean predicted | observed |
|---|---:|---:|---:|
| [0.00, 0.02) | 112763 | 0.0052 | 0.0032 |
| [0.02, 0.05) | 38195 | 0.0335 | 0.0362 |
| [0.05, 0.10) | 39314 | 0.0734 | 0.0889 |
| [0.10, 0.20) | 50288 | 0.1465 | 0.1651 |
| [0.20, 0.50) | 93488 | 0.3400 | 0.3658 |
| [0.50, 0.80) | 92590 | 0.6580 | 0.6497 |
| [0.80, 0.90) | 46033 | 0.8533 | 0.8309 |
| [0.90, 0.95) | 34569 | 0.9270 | 0.9155 |
| [0.95, 0.98) | 36671 | 0.9666 | 0.9572 |
| [0.98, 1.00) | 83099 | 0.9932 | 0.9930 |

empirical:

| bin | rows | mean predicted | observed |
|---|---:|---:|---:|
| [0.00, 0.02) | 119773 | 0.0025 | 0.0054 |
| [0.02, 0.05) | 25925 | 0.0336 | 0.0349 |
| [0.05, 0.10) | 36508 | 0.0737 | 0.0731 |
| [0.10, 0.20) | 58935 | 0.1504 | 0.1550 |
| [0.20, 0.50) | 132485 | 0.3526 | 0.3630 |
| [0.50, 0.80) | 109742 | 0.6613 | 0.6540 |
| [0.80, 0.90) | 47555 | 0.8524 | 0.8287 |
| [0.90, 0.95) | 35527 | 0.9264 | 0.8964 |
| [0.95, 0.98) | 26593 | 0.9654 | 0.9364 |
| [0.98, 1.00) | 79121 | 0.9969 | 0.9866 |

## Activation

- activated `models/chase/20261105.joblib` in current.json

## Reading

- A probability is only as good as the states it is judged on: the tail bets look at states under 10% and need the calibration above to hold. The walk-forward is season-clustered; no result of the test season enters its own features.
- The players model is used live only when both batters resolve to a career record (name match).

