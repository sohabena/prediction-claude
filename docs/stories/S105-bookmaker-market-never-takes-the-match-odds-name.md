# S105 — The unbettable `_BR` market never takes the plain `match_odds` name on a real match

**Pillar:** 1 · **Priority:** P0 · **Tier:** haiku · **Depends on:** S56, the 2026-11-03 canonical-market fix · **Wave:** 23

## User story

As every consumer of `v_match_odds` (paper trader, CLV recorder, replay, market-dynamics features, training
dataset), I want the match-odds book of a real match to come only from the exchange market, because the `_BR`
market's runners are always suspended and its prices cannot be bet.

## Evidence (2026-11-04)

`scraper/parsers/lotusbook_json.event_records` gives the plain name to whichever market comes **first in each
payload** for a (type, name, selection) slot; later ones get `#<market id>`. On Hobart Hurricanes W v Melbourne
Renegades W (lb_36266944) the `_BR` market (`1.75326606.340._BR`) was stored as plain `match_odds` for 560 rows
(06:55 to 11:36 UTC) next to the exchange market (`1.264885668`): 500 runner-timestamps where `v_match_odds` served
the `_BR` book. On West Indies A v England Lions (lb_36269512) the same happened for one payload at 20:27:57, after
the exchange market had left the page. The lead relabelled the 560 Hobart rows by hand (`match_odds#<id>`).

Exchange runner statuses are `active`, `winner`, `loser`; `_BR` runners are always `suspended`. SRL matches carry
only the `_BR` market and keep storing it as plain `match_odds` (by design: they are recorded, never traded).

## Acceptance criteria

1. **Parser** (`scraper/parsers/lotusbook_json.py`): `event_records(payload, match_id, observed_at, *,
   exchange_seen=False)`. Within a payload, a market whose id does not end in `_BR` owns the plain name of its slot
   whatever its position in the list; a `_BR` market gets the plain name only when no non-`_BR` market fills that
   slot in the payload **and** `exchange_seen` is False; otherwise it is stored as `<name>#<market id>`. Non-match-odds
   markets keep today's rule (first owner), but also order-independent: the owner is the lowest market id by string
   order among the markets sharing the slot, so names never flip between payloads.
2. **Caller** (`scraper/market_pages.py`): the match-page worker remembers, for its match, whether a non-`_BR`
   match-odds market has ever appeared (in memory, plus seeded from the stored rows when the page is attached:
   any `market_ticks` row of the match with `market_type='match_odds'` and a `market_id` not ending `_BR`), and
   passes `exchange_seen` to `event_records`.
3. **Monitor** (`scripts/data_quality_monitor.py`):
   - new check `bookmaker_leak` (FAIL): in the window, rows of real (not simulated) matches with
     `market_type='match_odds'`, `market_name='match_odds'` and `market_id LIKE '%\_BR'` on a match that also has a
     non-`_BR` match-odds market. Detail names the matches and row counts.
   - the hourly harmonise step relabels such rows to `'match_odds#' || market_id` (UPDATE only, never DELETE), and
     skips any row whose relabel would collide with an existing primary key; the count of skipped collisions is
     logged and reported by the check as WARN (they are duplicates the operator may choose to remove).
   - SQL rules of this monitor: each query starts with WITH or SELECT and contains no `;`.
4. Tests (no database writes; fakes and pure functions): parser with the `_BR` market listed first, last, alone
   (SRL: plain name), alone with `exchange_seen=True` (suffixed), and names stable across two payloads with
   different market order; worker passes `exchange_seen` after the first exchange payload and after seeding; monitor
   check fires on a leak, passes on SRL-only `_BR`, and reports collisions; harmonise SQL is an UPDATE that cannot
   touch simulated matches or collide.
5. Update existing S56 parser tests only where they assumed list-order naming; name each one in the report.
6. **Trader skips a bookmaker-only real match** (added 2026-11-04: Karnataka v Chhattisgarh, Abu Dhabi v Ajman and
   others carry only the `_BR` market, and the trader priced them every cycle). `select_candidates` skips a row with
   `bookmaker_only` true (reason `bookmaker_only`); a row without the key is not skipped (old feeds). Live feed
   (`virtual_trading/paper_trader.py` registry SQL): `bookmaker_only` = the match has a `market_ticks` match-odds row
   whose `market_id` ends `_BR` and none whose `market_id` does not. Replay feed (`virtual_trading/replay_feed.py`):
   the same, as of the replay cursor (first `_BR` row at or before t, and no exchange row at or before t), from one
   read of the first times per match, so no look-ahead. A match with no match-page rows (listing only, before its page
   opens) is never skipped. Tests: live and replay rules, the as-of boundary, the old-feed default, the skip counter.
