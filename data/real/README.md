# Real-price corpus (provenance)

`data/real/betfair/` holds real cricket Match Odds price histories built from Betfair
historical stream files that the operator downloaded. The raw files are not stored in this
repository.

- Built by `scripts/build_real_price_corpus.py --src <dir> --out data/real/betfair`.
- `betfair/manifest.json` lists every accepted market (with its sha1 and result) and every
  rejected market with a reason code.
- `betfair/<family>/<market_id>.json.gz` holds the real ticks of one market. The result is
  only in `ticks[0]["_meta"]`.
- Rebuild rather than editing corpus files by hand.

`data/` is gitignored. This README is the only tracked file in this folder (`git add -f`).
