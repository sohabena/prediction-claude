# Historical match odds: download, verify, use (S98 to S103)

**Why.** The binding constraint is evidence, not code (Golden Rulebook section 8): about 7 real matches a day are
recorded, and R1 needs 200 matches per segment. Published or bought historical closing odds let us test R1 (does our
pre-match model beat the market's price?) on hundreds or thousands of past matches at once.

**Rules that shape this work** (rulebook section 2): Betfair data is never used in any form; historical odds are
bought or published, never scraped; ask the operator before downloading large datasets or buying data; no bot-check
is ever bypassed; tests never write to the database (this work never touches it at all).

## Sources (checked 2026-11-04)

| Source | Cost | What it has | How we get it |
|---|---|---|---|
| aussportsbetting.com BBL file | free | BBL match results with open, min, max, close odds (several seasons) | The page is behind a Cloudflare check: the **operator downloads the file by hand** into `data/historical_odds/incoming/aussportsbetting/`. S99 imports it. |
| OddsPapi `/v4/historical-odds` | free key | Per-bookmaker price history (Pinnacle, bet365 and others), from January 2026 only; 5 s cooldown per call | The operator creates a free account and puts the key in `.env` as `ODDSPAPI_API_KEY`. S100 downloads, dry run by default. |
| The Odds API historical | paid (about $119 a month for the needed quota) | h2h snapshots for cricket competitions since mid-2020 (IPL, BBL, PSL, CPL, T20 Blast, internationals) | Only after the operator buys a plan and sets `THE_ODDS_API_KEY`. S101 estimates the credit cost first and spends nothing without `--max-credits`. |
| Our own recordings | free | LotusBook, since 2026-11-01 | Already in the database. Not part of this work. |

Excluded: Betfair (all forms, including Betfair bookmakers inside an aggregator's feed), scraping OddsPortal or any
other site, datasets of unknown origin (often scraped).

## Flow

```
incoming file / API ──> S99, S100, S101 importers ──> OddsRecord (S98 contract) ──> data/historical_odds/<source>/
                                                                                         │
                                Cricsheet (data/raw) ──> S102 verifier (link, results, calibration, coverage) ──> report, FAIL/WARN
                                                                                         │
          S103: pre-match model walk-forward predictions (out of sample) vs de-vigged closing price ──> R1 per segment
```

## Stories

| Story | What | Files | Depends on |
|---|---|---|---|
| S98 | Shared record contract and store (done by the lead) | `training/historical_odds.py` | none |
| S99 | aussportsbetting file importer | `scraper/importers/aussportsbetting.py`, `scripts/import_aussportsbetting.py` | S98 |
| S100 | OddsPapi historical client and importer | `scraper/importers/oddspapi.py`, `scripts/import_oddspapi.py` | S98 |
| S101 | The Odds API historical client with a cost guard | `scraper/importers/the_odds_api.py`, `scripts/import_the_odds_api.py` | S98 |
| S102 | Verifier: clean, harmonised, complete | `training/historical_odds_verify.py`, `scripts/verify_historical_odds.py` | S98 |
| S103 | R1 benchmark on historical closing odds | `training/historical_benchmark.py`, `scripts/benchmark_historical_odds.py`, `scripts/train_prematch_model.py` | S98 |

S99 to S103 run in parallel (one Haiku agent each, own files). Each is tested with fixtures only; nothing is
downloaded by an agent. The lead runs the real imports once the operator has put a file or key in place.

## What "verified" means (S102)

A source passes when: no Betfair record; under 1% invalid prices; no duplicate rows; open/min/max/close consistent;
overround plausible for its kind; at least 80% of matches link to a Cricsheet match (unlinked names are listed for
the alias table); the stated winner agrees with Cricsheet on at least 98% of linked decided matches; closing fair
probabilities are calibrated against results (slope between 0.8 and 1.2); two sources on the same match agree within
3 probability points at the close (median). FAIL and WARN are explained, never ignored.
