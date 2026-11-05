# PHOENIX paper replay

Window 2026-11-01T00:00:00+00:00 to 2026-11-03T10:56:38+00:00; 14 matches considered; 5300 cycles run, 8820 grid steps skipped (no fresh price, no open bet). Settings: {'include_srl': False, 'variants': ['w03_e03', 'w03_e06', 'w05_e03', 'w05_e06', 'w07_e03', 'w07_e06', 'market_only'], 'cycle_seconds': 15, 'fill_delay_seconds': 60, 'max_match_loss': 100000.0, 'guards': {'max_spread': 0.15, 'in_play_max_age_s': 60.0, 'prematch_max_age_s': 900.0, 'max_trades_per_window': 3, 'trade_window_s': 600.0, 'flip_cooldown_s': 300.0}, 'suspensions': 'none (every variant trades; the live S73 state is not applied)'}.

Virtual only. The live ledger (`paper_bets`) was not read or written. CLV is in probability points against the reference price; P&L is gross of commission. CIs are seeded percentile bootstraps (95%). Churn: a trade is a filled bet; tiny trades have stake under 2,000.

## Per variant

| variant | intents | fills | settled | void | CLV mean [95% CI] | P&L total | P&L per bet [95% CI] | max worst case / match | trades per match | tiny trades |
|---|---:|---:|---:|---:|---|---:|---|---:|---:|---:|
| w03_e03 | 91 | 64 | 62 | 0 | +0.0219 [+0.0042, +0.0396] (n=58) | 274,897 | +4433.83 [-713.04, +9787.37] | 99,270 | 16.00 | 2 (3.1%) |
| w03_e06 | 71 | 46 | 46 | 0 | +0.0410 [+0.0051, +0.0818] (n=42) | 277,166 | +6025.34 [-320.67, +12333.91] | 100,000 | 15.33 | 1 (2.2%) |
| w05_e03 | 99 | 65 | 62 | 0 | +0.0156 [+0.0006, +0.0313] (n=61) | 243,241 | +3923.24 [-2679.17, +10991.79] | 99,283 | 16.25 | 0 (0.0%) |
| w05_e06 | 85 | 56 | 54 | 0 | +0.0145 [-0.0018, +0.0323] (n=52) | 279,601 | +5177.79 [-1765.93, +12776.76] | 99,502 | 14.00 | 1 (1.8%) |
| w07_e03 | 91 | 64 | 60 | 0 | +0.0128 [-0.0036, +0.0301] (n=59) | 284,936 | +4748.93 [-2917.98, +13214.45] | 100,000 | 16.00 | 1 (1.6%) |
| w07_e06 | 87 | 65 | 62 | 0 | +0.0163 [-0.0028, +0.0404] (n=61) | 287,172 | +4631.80 [-2707.26, +12480.70] | 100,000 | 16.25 | 1 (1.5%) |
| market_only | 0 | 0 | 0 | 0 | n/a [n/a, n/a] (n=0) | 0 | n/a [n/a, n/a] | 0 | n/a | 0 (n/a) |

## Intents by side

- w03_e03: BACK_away 18, BACK_home 6, LAY_away 52, LAY_home 15
- w03_e06: BACK_away 10, BACK_home 4, LAY_away 46, LAY_home 11
- w05_e03: BACK_away 25, BACK_home 11, LAY_away 43, LAY_home 20
- w05_e06: BACK_away 16, BACK_home 5, LAY_away 49, LAY_home 15
- w07_e03: BACK_away 22, BACK_home 16, LAY_away 24, LAY_home 29
- w07_e06: BACK_away 24, BACK_home 10, LAY_away 31, LAY_home 22
- market_only: none

## Skips (counted per cycle, summed over the run)

- Registry: {'no_start': 876, 'settled': 80, 'simulated': 3652, 'too_early': 5764}
- Matches: {'no_market': 569}

## Guard refusals (S75, counted per cycle and per candidate or intent, summed over the run)

- {'flip_cooldown': 157, 'no_band': 41, 'spread_too_wide': 36, 'trade_rate': 944}

## Per match

| match | teams | competition | w03_e03 fills / worst | w03_e06 fills / worst | w05_e03 fills / worst | w05_e06 fills / worst | w07_e03 fills / worst | w07_e06 fills / worst | market_only fills / worst |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| lb_36262294 | Melbourne Stars W v Perth Scorchers W | WBBL | 12 / 0 | 10 / 0 | 8 / 0 | 8 / 0 | 8 / 0 | 8 / 0 | - |
| lb_36267578 | Biratnagar Kings v Janakpur Bolts | Nepal Premier League | 45 / 0 | 31 / 64,127 | 48 / 0 | 40 / 99,142 | 45 / 0 | 47 / 0 | - |
| lb_36267634 | Lumbini Lions v Pokhara Avengers | Nepal Premier League | 2 / 50,000 | - | 3 / 75,000 | 2 / 50,000 | 4 / 100,000 | 3 / 75,000 | - |
| lb_75878940 | Oman v Netherlands | ICC Cricket World Cup League Two | 5 / 99,270 | 5 / 100,000 | 6 / 99,283 | 6 / 99,502 | 7 / 100,000 | 7 / 100,000 | - |

