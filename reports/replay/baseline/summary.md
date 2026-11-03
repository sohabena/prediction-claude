# PHOENIX paper replay

Window 2026-11-01T00:00:00+00:00 to 2026-11-03T10:56:38.110544+00:00; 15 matches considered; 5300 cycles run, 8820 grid steps skipped (no fresh price, no open bet). Settings: {'include_srl': False, 'variants': ['w03_e03', 'w03_e06', 'w05_e03', 'w05_e06', 'w07_e03', 'w07_e06', 'market_only'], 'cycle_seconds': 15, 'fill_delay_seconds': 60, 'max_match_loss': 100000.0, 'suspensions': 'none (every variant trades; the live S73 state is not applied)'}.

Virtual only. The live ledger (`paper_bets`) was not read or written. CLV is in probability points against the reference price; P&L is gross of commission. CIs are seeded percentile bootstraps (95%). Churn: a trade is a filled bet; tiny trades have stake under 2,000.

## Per variant

| variant | intents | fills | settled | void | CLV mean [95% CI] | P&L total | P&L per bet [95% CI] | max worst case / match | trades per match | tiny trades |
|---|---:|---:|---:|---:|---|---:|---|---:|---:|---:|
| w03_e03 | 96 | 65 | 63 | 0 | +0.0260 [+0.0061, +0.0468] (n=57) | 341,066 | +5413.75 [+83.42, +11032.38] | 100,000 | 16.25 | 3 (4.6%) |
| w03_e06 | 77 | 47 | 47 | 0 | +0.0097 [-0.0181, +0.0374] (n=43) | 301,819 | +6421.68 [+338.75, +12459.26] | 100,000 | 15.67 | 1 (2.1%) |
| w05_e03 | 104 | 68 | 64 | 0 | +0.0141 [-0.0023, +0.0308] (n=58) | 377,554 | +5899.28 [-1070.83, +12794.23] | 100,000 | 17.00 | 1 (1.5%) |
| w05_e06 | 97 | 63 | 60 | 0 | +0.0221 [+0.0049, +0.0395] (n=54) | 375,414 | +6256.89 [-325.11, +13517.81] | 99,979 | 15.75 | 1 (1.6%) |
| w07_e03 | 103 | 69 | 65 | 0 | +0.0184 [+0.0021, +0.0350] (n=64) | 410,957 | +6322.42 [-591.55, +14222.25] | 100,000 | 17.25 | 3 (4.3%) |
| w07_e06 | 94 | 68 | 64 | 0 | +0.0266 [+0.0079, +0.0440] (n=59) | 417,663 | +6525.98 [-810.14, +14821.41] | 100,000 | 17.00 | 2 (2.9%) |
| market_only | 1 | 1 | 1 | 0 | -0.0019 [n/a, n/a] (n=1) | -2,000 | -2000.00 [n/a, n/a] | 2,000 | 1.00 | 0 (0.0%) |

## Intents by side

- w03_e03: BACK_away 23, BACK_home 8, LAY_away 50, LAY_home 15
- w03_e06: BACK_away 9, BACK_home 4, LAY_away 51, LAY_home 13
- w05_e03: BACK_away 27, BACK_home 13, LAY_away 38, LAY_home 26
- w05_e06: BACK_away 20, BACK_home 8, LAY_away 47, LAY_home 22
- w07_e03: BACK_away 30, BACK_home 16, LAY_away 31, LAY_home 26
- w07_e06: BACK_away 26, BACK_home 12, LAY_away 32, LAY_home 24
- market_only: BACK_away 1

## Skips (counted per cycle, summed over the run)

- Registry: {'no_start': 876, 'settled': 80, 'simulated': 3652, 'too_early': 5260}
- Matches: {'no_market': 569}

## Per match

| match | teams | competition | w03_e03 fills / worst | w03_e06 fills / worst | w05_e03 fills / worst | w05_e06 fills / worst | w07_e03 fills / worst | w07_e06 fills / worst | market_only fills / worst |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| lb_36262294 | Melbourne Stars W v Perth Scorchers W | WBBL | 14 / 0 | 11 / 0 | 13 / 0 | 13 / 0 | 12 / 0 | 12 / 0 | - |
| lb_36267578 | Biratnagar Kings v Janakpur Bolts | Nepal Premier League | 44 / 0 | 32 / 99,648 | 46 / 0 | 42 / 0 | 48 / 0 | 47 / 0 | - |
| lb_36267634 | Lumbini Lions v Pokhara Avengers | Nepal Premier League | 2 / 50,000 | - | 4 / 100,000 | 3 / 75,000 | 4 / 100,000 | 4 / 100,000 | - |
| lb_75878940 | Oman v Netherlands | ICC Cricket World Cup League Two | 5 / 100,000 | 4 / 100,000 | 5 / 99,979 | 5 / 99,979 | 5 / 99,979 | 5 / 99,979 | - |
| lb_76073798 | Incredibles v Warriors | T20 PCB Super-3, Women | - | - | - | - | - | - | 1 / 2,000 |

