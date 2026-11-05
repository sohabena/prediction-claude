# PHOENIX paper replay

Window 2026-11-01T00:00:00+00:00 to 2026-11-03T10:56:38+00:00; 16 matches considered; 5298 cycles run, 8821 grid steps skipped (no fresh price, no open bet). Settings: {'include_srl': False, 'variants': ['w03_e03', 'w03_e06', 'w05_e03', 'w05_e06', 'w07_e03', 'w07_e06', 'market_only'], 'cycle_seconds': 15, 'fill_delay_seconds': 60, 'size_cap': True, 'unknown_size_cap': 10000.0, 'lay_size_unit': 'stake', 'max_match_loss': 100000.0, 'guards': {'max_spread': 0.15, 'in_play_max_age_s': 60.0, 'prematch_max_age_s': 900.0, 'max_trades_per_window': 3, 'trade_window_s': 600.0, 'flip_cooldown_s': 300.0}, 'suspensions': 'none (every variant trades; the live S73 state is not applied)'}.

Virtual only. The live ledger (`paper_bets`) was not read or written. CLV is in probability points against the reference price; P&L is gross of commission. Headline CIs are seeded percentile bootstraps that resample matches (S84): bets on one match move together. Each figure shows its matches and bets; a CI from fewer than 10 matches is labelled 'too few matches'. Churn: a trade is a filled bet; tiny trades have stake under 2,000.

## Per variant (match-clustered)

| variant | intents | fills | settled | void | CLV mean [95% CI] (matches / bets) | P&L total [95% CI] (matches / bets) | max worst case / match | trades per match | tiny trades |
|---|---:|---:|---:|---:|---|---|---:|---:|---:|
| w03_e03 | 93 | 46 | 44 | 0 | +0.0300 [n/a, n/a] (1 match / 40 bets; too few matches) | -19999 [n/a, n/a] (1 match / 44 bets; too few matches) | 20,000 | 23.00 | 41 (89.1%) |
| w03_e06 | 43 | 21 | 21 | 0 | +0.0689 [n/a, n/a] (1 match / 19 bets; too few matches) | -802 [n/a, n/a] (1 match / 21 bets; too few matches) | 802 | 21.00 | 21 (100.0%) |
| w05_e03 | 135 | 76 | 73 | 0 | +0.0257 [n/a, n/a] (1 match / 67 bets; too few matches) | -25457 [n/a, n/a] (1 match / 73 bets; too few matches) | 30,000 | 38.00 | 65 (85.5%) |
| w05_e06 | 107 | 58 | 56 | 0 | +0.0263 [n/a, n/a] (1 match / 50 bets; too few matches) | -43613 [n/a, n/a] (1 match / 56 bets; too few matches) | 43,613 | 29.00 | 51 (87.9%) |
| w07_e03 | 149 | 91 | 83 | 0 | +0.0150 [n/a, n/a] (1 match / 77 bets; too few matches) | -28228 [n/a, n/a] (1 match / 83 bets; too few matches) | 50,000 | 45.50 | 73 (80.2%) |
| w07_e06 | 103 | 66 | 63 | 0 | +0.0089 [n/a, n/a] (1 match / 57 bets; too few matches) | -42008 [n/a, n/a] (1 match / 63 bets; too few matches) | 42,008 | 33.00 | 58 (87.9%) |
| market_only | 0 | 0 | 0 | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) | 0 | n/a | 0 (n/a) |

## Fills and sizes (S87): stake fill rate, size cap, slippage vs the decision price

| variant | decided | fills | unfilled | fill rate (count) | stake fill rate | size-capped share | stake lapsed | size-unknown share | mean adverse slippage (odds / %) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| w03_e03 | 93 | 46 | 47 | 49.5% | 3.8% | 97.8% (45) | 552,520 | 10.9% (5) | -0.3778 / -8.7% |
| w03_e06 | 43 | 21 | 22 | 48.8% | 1.7% | 100.0% (21) | 167,929 | 0.0% (0) | -0.2033 / -5.6% |
| w05_e03 | 135 | 76 | 59 | 56.3% | 4.2% | 96.1% (73) | 1,210,569 | 13.2% (10) | -0.1882 / -4.3% |
| w05_e06 | 107 | 58 | 49 | 54.2% | 2.4% | 98.3% (57) | 1,469,591 | 12.1% (7) | -0.2490 / -5.6% |
| w07_e03 | 149 | 91 | 58 | 61.1% | 5.9% | 92.3% (84) | 1,332,244 | 18.7% (17) | -0.1240 / -2.6% |
| w07_e06 | 103 | 66 | 37 | 64.1% | 3.0% | 98.5% (65) | 1,607,441 | 12.1% (8) | -0.1771 / -3.7% |
| market_only | 0 | 0 | 0 | n/a | n/a | n/a (0) | 0 | n/a (0) | n/a / n/a |

## Pre-match and in-play, separately (match-clustered)

| variant | segment | fills | CLV mean [95% CI] (matches / bets) | stake-weighted CLV [95% CI] | P&L total [95% CI] (matches / bets) |
|---|---|---:|---|---|---|
| w03_e03 | pre-match | 5 | +0.0507 [n/a, n/a] (1 match / 3 bets; too few matches) | +0.0507 [n/a, n/a] (1 match / 3 bets; too few matches) | -24000 [n/a, n/a] (1 match / 3 bets; too few matches) |
| w03_e03 | in-play | 41 | +0.0283 [n/a, n/a] (1 match / 37 bets; too few matches) | +0.0380 [n/a, n/a] (1 match / 37 bets; too few matches) | +4001 [n/a, n/a] (1 match / 41 bets; too few matches) |
| w03_e06 | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| w03_e06 | in-play | 21 | +0.0689 [n/a, n/a] (1 match / 19 bets; too few matches) | +0.0314 [n/a, n/a] (1 match / 19 bets; too few matches) | -802 [n/a, n/a] (1 match / 21 bets; too few matches) |
| w05_e03 | pre-match | 10 | +0.0177 [n/a, n/a] (1 match / 7 bets; too few matches) | +0.0187 [n/a, n/a] (1 match / 7 bets; too few matches) | -34440 [n/a, n/a] (1 match / 7 bets; too few matches) |
| w05_e03 | in-play | 66 | +0.0267 [n/a, n/a] (1 match / 60 bets; too few matches) | +0.0269 [n/a, n/a] (1 match / 60 bets; too few matches) | +8983 [n/a, n/a] (1 match / 66 bets; too few matches) |
| w05_e06 | pre-match | 7 | +0.0499 [n/a, n/a] (1 match / 5 bets; too few matches) | +0.0499 [n/a, n/a] (1 match / 5 bets; too few matches) | -50000 [n/a, n/a] (1 match / 5 bets; too few matches) |
| w05_e06 | in-play | 51 | +0.0237 [n/a, n/a] (1 match / 45 bets; too few matches) | +0.0327 [n/a, n/a] (1 match / 45 bets; too few matches) | +6387 [n/a, n/a] (1 match / 51 bets; too few matches) |
| w07_e03 | pre-match | 17 | -0.0011 [n/a, n/a] (1 match / 9 bets; too few matches) | -0.0002 [n/a, n/a] (1 match / 9 bets; too few matches) | -37800 [n/a, n/a] (1 match / 9 bets; too few matches) |
| w07_e03 | in-play | 74 | +0.0171 [n/a, n/a] (1 match / 68 bets; too few matches) | -0.0031 [n/a, n/a] (1 match / 68 bets; too few matches) | +9572 [n/a, n/a] (1 match / 74 bets; too few matches) |
| w07_e06 | pre-match | 8 | +0.0434 [n/a, n/a] (1 match / 5 bets; too few matches) | +0.0434 [n/a, n/a] (1 match / 5 bets; too few matches) | -50000 [n/a, n/a] (1 match / 5 bets; too few matches) |
| w07_e06 | in-play | 58 | +0.0055 [n/a, n/a] (1 match / 52 bets; too few matches) | -0.0181 [n/a, n/a] (1 match / 52 bets; too few matches) | +7992 [n/a, n/a] (1 match / 58 bets; too few matches) |
| market_only | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| market_only | in-play | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |

## Detail: per-bet intervals (reference only; they ignore the match correlation)

| variant | CLV mean per bet [95% CI] | P&L per bet [95% CI] |
|---|---|---|
| w03_e03 | +0.0300 [-0.0066, +0.0694] | n/a [-1177.08, +89.62] |
| w03_e06 | +0.0689 [+0.0139, +0.1322] | n/a [-181.56, +72.60] |
| w05_e03 | +0.0257 [-0.0025, +0.0537] | n/a [-1107.32, +295.99] |
| w05_e06 | +0.0263 [-0.0107, +0.0653] | n/a [-1525.22, -62.51] |
| w07_e03 | +0.0150 [-0.0117, +0.0390] | n/a [-1019.68, +263.86] |
| w07_e06 | +0.0089 [-0.0260, +0.0426] | n/a [-1433.14, -36.75] |
| market_only | n/a [n/a, n/a] | n/a [n/a, n/a] |

## Intents by side

- w03_e03: BACK_away 9, BACK_home 1, LAY_away 83
- w03_e06: BACK_away 7, LAY_away 36
- w05_e03: BACK_away 15, BACK_home 8, LAY_away 112
- w05_e06: BACK_away 9, BACK_home 2, LAY_away 96
- w07_e03: BACK_away 17, BACK_home 15, LAY_away 116, LAY_home 1
- w07_e06: BACK_away 10, BACK_home 1, LAY_away 92
- market_only: none

## Skips (counted per cycle, summed over the run)

- Registry: {'settled': 80, 'simulated': 3652, 'too_early': 5764}
- Matches: {'no_market': 748}

## Guard refusals (S75, counted per cycle and per candidate or intent, summed over the run)

- {'crossed_book': 2, 'flip_cooldown': 194, 'inplay_without_state': 26448, 'no_band': 41, 'trade_rate': 121}

## Per match

| match | teams | competition | w03_e03 fills / worst | w03_e06 fills / worst | w05_e03 fills / worst | w05_e06 fills / worst | w07_e03 fills / worst | w07_e06 fills / worst | market_only fills / worst |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| lb_36267578 | Biratnagar Kings v Janakpur Bolts | Nepal Premier League | 44 / 19,999 | 21 / 802 | 73 / 25,457 | 56 / 43,613 | 83 / 28,228 | 63 / 42,008 | - |
| lb_36267634 | Lumbini Lions v Pokhara Avengers | Nepal Premier League | 2 / 20,000 | - | 3 / 30,000 | 2 / 20,000 | 8 / 50,000 | 3 / 30,000 | - |

