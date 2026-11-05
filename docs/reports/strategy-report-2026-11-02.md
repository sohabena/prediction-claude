# Strategy report 2026-11-02

All numbers are rendered from the JSON files listed in the appendix. Models were NOT activated.

## Verdict

- t20i_men: model: CI lower bound +11,203 > 0; value: CI lower bound +41 > 0; random mean +13,828
- odi_men: model: CI lower bound +1,619 > 0; value: CI lower bound +3,927 > 0; random mean +12,104
- WARNING t20i_men: `heuristic` baseline has CI lower bound +6,440 > 0 (mean +29,643); a market with no edge should not pay a no-skill policy, so positive synthetic P&L here is evidence of an exploitable simulator, not of skill.
- WARNING odi_men: `random` baseline has CI lower bound +4,276 > 0 (mean +12,104); a market with no edge should not pay a no-skill policy, so positive synthetic P&L here is evidence of an exploitable simulator, not of skill.
- WARNING odi_men: `heuristic` baseline has CI lower bound +32,898 > 0 (mean +53,103); a market with no edge should not pay a no-skill policy, so positive synthetic P&L here is evidence of an exploitable simulator, not of skill.

**Recommendation: collect more real data.** Reasoning: recorded real-odds sample is n=0 matches, so no real-edge claim is supported; synthetic results come from a martingale market built to carry no edge, so a positive result there would indicate an artefact, not alpha. (recorded n=0).

## Recorded real odds

Recorded matches evaluated: n=0. Export matches in manifest: 0, rejected: 0. Export return code: 1.

## Synthetic holdout (martingale market, chronological holdout)

### t20i_men (holdout n=52)

| policy | mean per-match P&L (95% CI) | CLV mean (95% CI) | max DD % | bets/match | commission/match | n matches |
|---|---|---|---|---|---|---|
| model | +50,286 (+11,203..+99,247) | -0.0012 (-0.0208..+0.0216, n=1448) | 2.23 | 27.85 | 1,179 | 52 |
| value | +1,907 (+41..+4,137) | +0.0105 (-0.0326..+0.0564, n=322) | 0.45 | 6.19 | 72 | 52 |
| heuristic | +29,643 (+6,440..+56,926) | +0.1411 (+0.1169..+0.1660, n=1147) | 2.02 | 22.06 | 784 | 52 |
| random | +13,828 (-3,327..+35,420) | -0.0365 (-0.0570..-0.0160, n=1499) | 2.73 | 28.83 | 471 | 52 |
| hold | +0 (+0..+0) | n/a | 0.00 | 0.00 | 0 | 52 |

### odi_men (holdout n=52)

| policy | mean per-match P&L (95% CI) | CLV mean (95% CI) | max DD % | bets/match | commission/match | n matches |
|---|---|---|---|---|---|---|
| model | +2,507 (+1,619..+3,401) | -0.0363 (-0.0641..-0.0097, n=1415) | 0.12 | 27.21 | 58 | 52 |
| value | +5,962 (+3,927..+8,259) | +0.0321 (-0.0068..+0.0706, n=585) | 0.12 | 11.25 | 136 | 52 |
| heuristic | +53,103 (+32,898..+74,026) | +0.2646 (+0.2357..+0.2925, n=1171) | 0.76 | 22.52 | 1,149 | 52 |
| random | +12,104 (+4,276..+21,963) | -0.0588 (-0.0866..-0.0337, n=1520) | 0.76 | 29.23 | 316 | 52 |
| hold | +0 (+0..+0) | n/a | 0.00 | 0.00 | 0 | 52 |

## Fair-value calibration (holdout, vs market-implied)

| key | family | n ticks | fair Brier | market Brier | fair ECE | market ECE |
|---|---|---|---|---|---|---|
| t20i_men | t20 | 1627 | 0.1965 | 0.2174 | 0.1227 | 0.1348 |
| odi_men | odi | 4027 | 0.2229 | 0.3474 | 0.1356 | 0.2999 |

## S17 provenance gate (evaluated as if activated; nothing was activated)

| key | tradeable | reason |
|---|---|---|
| t20i_men | True | ok |
| odi_men | True | ok |

## Audits

- `audit_outcome_leakage t20i_men: PASS max_auc_delta=+0.0096`
- `audit_synthetic_edge t20i_men: PASS excess_r2=+0.0049 hit_rate=0.509`
- `audit_outcome_leakage odi_men: PASS max_auc_delta=+0.0133`
- `audit_synthetic_edge odi_men: PASS excess_r2=+0.0055 hit_rate=0.511`
- `audit_outcome_leakage test_men: FAIL max_auc_delta=+0.0492`
- `audit_synthetic_edge test_men: PASS excess_r2=+0.0019 hit_rate=0.521`
- test_men: BLOCKED at step `audits`; not trained (audits must pass before training).

## Training

| key | train matches | planned steps | steps run |
|---|---|---|---|
| t20i_men | 240 | 216000 | 194400 |
| odi_men | 235 | 211500 | 212992 |

## Appendix: source JSON files

- `docs\reports\s18/t20i_men.json`
- `docs\reports\s18/odi_men.json`
- `docs\reports\s18/test_men.json`
- `docs\reports\s18/recorded.json`
- `models/specialists_v2/<key>_train_manifest.json`
- `models/fair_value/<family>_<key>_report.json`
