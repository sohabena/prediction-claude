# PHOENIX paper replay

Window 2026-11-03T00:00:00+00:00 to 2026-11-03T10:56:38+00:00; 12 matches considered; 2408 cycles run, 211 grid steps skipped (no fresh price, no open bet). Settings: {'include_srl': False, 'variants': ['w03_e03', 'w03_e06', 'w05_e03', 'w05_e06', 'w07_e03', 'w07_e06', 'market_only'], 'cycle_seconds': 15, 'fill_delay_seconds': 60, 'max_match_loss': 100000.0, 'guards': {'max_spread': 0.15, 'in_play_max_age_s': 60.0, 'prematch_max_age_s': 900.0, 'max_trades_per_window': 3, 'trade_window_s': 600.0, 'flip_cooldown_s': 300.0}, 'suspensions': 'none (every variant trades; the live S73 state is not applied)'}.

Virtual only. The live ledger (`paper_bets`) was not read or written. CLV is in probability points against the reference price; P&L is gross of commission. CIs are seeded percentile bootstraps (95%). Churn: a trade is a filled bet; tiny trades have stake under 2,000.

## Per variant

| variant | intents | fills | settled | void | CLV mean [95% CI] | P&L total | P&L per bet [95% CI] | max worst case / match | trades per match | tiny trades |
|---|---:|---:|---:|---:|---|---:|---|---:|---:|---:|
| w03_e03 | 74 | 47 | 45 | 0 | +0.0293 [+0.0073, +0.0514] (n=45) | 10,000 | +222.22 [-4995.33, +5522.10] | 50,000 | 23.50 | 2 (4.3%) |
| w03_e06 | 54 | 31 | 31 | 0 | +0.0317 [-0.0046, +0.0729] (n=30) | 13,716 | +442.45 [-4262.50, +4709.58] | 64,127 | 31.00 | 1 (3.2%) |
| w05_e03 | 85 | 51 | 48 | 0 | +0.0209 [+0.0029, +0.0397] (n=48) | 2,175 | +45.31 [-5464.78, +6153.24] | 75,000 | 25.50 | 0 (0.0%) |
| w05_e06 | 71 | 42 | 40 | 0 | +0.0207 [-0.0012, +0.0452] (n=39) | 37,983 | +949.57 [-5323.04, +7252.83] | 99,142 | 21.00 | 0 (0.0%) |
| w07_e03 | 76 | 49 | 45 | 0 | +0.0109 [-0.0061, +0.0293] (n=45) | 55,622 | +1236.05 [-6810.96, +10458.05] | 100,000 | 24.50 | 0 (0.0%) |
| w07_e06 | 71 | 50 | 47 | 0 | +0.0113 [-0.0058, +0.0298] (n=47) | 50,049 | +1064.88 [-6539.50, +9169.71] | 75,000 | 25.00 | 0 (0.0%) |
| market_only | 0 | 0 | 0 | 0 | n/a [n/a, n/a] (n=0) | 0 | n/a [n/a, n/a] | 0 | n/a | 0 (n/a) |

## Intents by side

- w03_e03: BACK_away 18, BACK_home 2, LAY_away 52, LAY_home 2
- w03_e06: BACK_away 8, LAY_away 46
- w05_e03: BACK_away 25, BACK_home 7, LAY_away 43, LAY_home 10
- w05_e06: BACK_away 16, BACK_home 1, LAY_away 49, LAY_home 5
- w07_e03: BACK_away 21, BACK_home 12, LAY_away 24, LAY_home 19
- w07_e06: BACK_away 23, BACK_home 5, LAY_away 31, LAY_home 12
- market_only: none

## Skips (counted per cycle, summed over the run)

- Registry: {'settled': 20, 'simulated': 3492, 'too_early': 3801}
- Matches: {'no_market': 19}

## Guard refusals (S75, counted per cycle and per candidate or intent, summed over the run)

- {'flip_cooldown': 89, 'no_band': 41, 'spread_too_wide': 34, 'trade_rate': 742}

## Per match

| match | teams | competition | w03_e03 fills / worst | w03_e06 fills / worst | w05_e03 fills / worst | w05_e06 fills / worst | w07_e03 fills / worst | w07_e06 fills / worst | market_only fills / worst |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| lb_36267578 | Biratnagar Kings v Janakpur Bolts | Nepal Premier League | 45 / 0 | 31 / 64,127 | 48 / 0 | 40 / 99,142 | 45 / 0 | 47 / 0 | - |
| lb_36267634 | Lumbini Lions v Pokhara Avengers | Nepal Premier League | 2 / 50,000 | - | 3 / 75,000 | 2 / 50,000 | 4 / 100,000 | 3 / 75,000 | - |

