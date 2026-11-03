# S63 gate report: blend_20261103T075207Z (2026-11-03)

- Source: `blend_20261103T075207Z` (predictions), de-vig `power`, predictions rows: 0
- Dataset: `C:\Users\sohai\Downloads\prediction-claude\data\retrain\20261103T075207Z\dataset` (sha `8bbedb8032f9a19805bdb5ddf9175f4188eed502`), matches 28, settled 11
- Segments version `2026.11.03-2`, eval version `1.0.0`, bootstrap 2000 resamples by match, seed 20261103
- Thresholds: R1 >= 200 matches, R2 >= 150 matches (plan section 4)

**Signals allowed: no.** R1 pass: none. R2 pass: none.

## prematch

Rows 0, matches 0, bets 0. Binding close: last price before the off (T-0).

| segment | matches | rows | source-market log loss diff [95% CI] | R1 | bets | CLV per bet [95% CI] | simulated P&L [95% CI] | R2 |
|---|---|---|---|---|---|---|---|---|
| all | 0 | 0 | n/a | insufficient (need 200 more matches) | 0 | n/a | n/a | insufficient (need 150 more matches) |
| family_t20 | 0 | 0 | n/a | insufficient (need 200 more matches) | 0 | n/a | n/a | insufficient (need 150 more matches) |
| family_odi | 0 | 0 | n/a | insufficient (need 200 more matches) | 0 | n/a | n/a | insufficient (need 150 more matches) |
| family_test | 0 | 0 | n/a | insufficient (need 200 more matches) | 0 | n/a | n/a | insufficient (need 150 more matches) |
| gender_men | 0 | 0 | n/a | insufficient (need 200 more matches) | 0 | n/a | n/a | insufficient (need 150 more matches) |
| gender_women | 0 | 0 | n/a | insufficient (need 200 more matches) | 0 | n/a | n/a | insufficient (need 150 more matches) |
| tier_1 | 0 | 0 | n/a | insufficient (need 200 more matches) | 0 | n/a | n/a | insufficient (need 150 more matches) |
| tier_2 | 0 | 0 | n/a | insufficient (need 200 more matches) | 0 | n/a | n/a | insufficient (need 150 more matches) |
| market_favourite | 0 | 0 | n/a | insufficient (need 200 more matches) | 0 | n/a | n/a | insufficient (need 150 more matches) |
| market_mid | 0 | 0 | n/a | insufficient (need 200 more matches) | 0 | n/a | n/a | insufficient (need 150 more matches) |
| market_longshot | 0 | 0 | n/a | insufficient (need 200 more matches) | 0 | n/a | n/a | insufficient (need 150 more matches) |
| real | 0 | 0 | n/a | insufficient (need 200 more matches) | 0 | n/a | n/a | insufficient (need 150 more matches) |
| simulated | 0 | 0 | n/a | insufficient (need 200 more matches) | 0 | n/a | n/a | insufficient (need 150 more matches) |

## inplay

Rows 0, matches 0, bets 0. Binding close: same side 10 min after (book_10m).

| segment | matches | rows | source-market log loss diff [95% CI] | R1 | bets | CLV per bet [95% CI] | simulated P&L [95% CI] | R2 |
|---|---|---|---|---|---|---|---|---|
| all | 0 | 0 | n/a | insufficient (need 200 more matches) | 0 | n/a | n/a | insufficient (need 150 more matches) |
| family_t20 | 0 | 0 | n/a | insufficient (need 200 more matches) | 0 | n/a | n/a | insufficient (need 150 more matches) |
| family_odi | 0 | 0 | n/a | insufficient (need 200 more matches) | 0 | n/a | n/a | insufficient (need 150 more matches) |
| family_test | 0 | 0 | n/a | insufficient (need 200 more matches) | 0 | n/a | n/a | insufficient (need 150 more matches) |
| gender_men | 0 | 0 | n/a | insufficient (need 200 more matches) | 0 | n/a | n/a | insufficient (need 150 more matches) |
| gender_women | 0 | 0 | n/a | insufficient (need 200 more matches) | 0 | n/a | n/a | insufficient (need 150 more matches) |
| tier_1 | 0 | 0 | n/a | insufficient (need 200 more matches) | 0 | n/a | n/a | insufficient (need 150 more matches) |
| tier_2 | 0 | 0 | n/a | insufficient (need 200 more matches) | 0 | n/a | n/a | insufficient (need 150 more matches) |
| market_favourite | 0 | 0 | n/a | insufficient (need 200 more matches) | 0 | n/a | n/a | insufficient (need 150 more matches) |
| market_mid | 0 | 0 | n/a | insufficient (need 200 more matches) | 0 | n/a | n/a | insufficient (need 150 more matches) |
| market_longshot | 0 | 0 | n/a | insufficient (need 200 more matches) | 0 | n/a | n/a | insufficient (need 150 more matches) |
| real | 0 | 0 | n/a | insufficient (need 200 more matches) | 0 | n/a | n/a | insufficient (need 150 more matches) |
| simulated | 0 | 0 | n/a | insufficient (need 200 more matches) | 0 | n/a | n/a | insufficient (need 150 more matches) |

Rules: R1 passes when the source log loss is below the market's in a segment with at least 200 matches and the paired 95% CI lies entirely below zero. R2 passes when mean CLV per bet and total simulated P&L both have a 95% CI lower bound above zero, with at least 150 matches. Until R1 passes, the blend returns the market and no signals are emitted.
