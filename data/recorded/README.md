# Recorded real-odds corpus

Produced by `python scripts/export_recorded_corpus.py`. No data files are committed.

- Source: real LotusBook prices in `odds_ticks` / `odds_ticks_historical`, as-of joined to
  `match_context` (never a context row later than the tick), settled from `match_results`.
- Layout: `data/recorded/<specialist_key>/<match_id>.json.gz` (one episode: a list of tick dicts)
  plus `data/recorded/manifest.json` (match_id, key, n_ticks, first/last time, sha1, rejected list).
- Each tick has `source="recorded_lotusbook"` and `market_model="real"`. The result lives only in
  `_meta` on the first tick (`winner`, `loser`, `winner_is_home`, `team_home`, `team_away`, `result_type`).
- Suspended rows (any missing price) are dropped. Matches with no result, fewer than `--min-ticks`
  ticks, or time-bin coverage below `--min-coverage` are listed as rejected with a reason.
- `--cadence decision` (default) keeps only decision ticks; `raw` keeps every tick.
- Load with `load_recorded(key)` from the script. Run it before the 90-day `odds_ticks`
  retention window closes. Fewer than 30 matches per key is not enough to claim a real edge.
