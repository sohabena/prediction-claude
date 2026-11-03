# S51 report: match-page scoreboard parser and discovery

Date of discovery: 2026-11-03 (UTC ~02:50 to 03:00). Read-only. No clicks, no typing, no login, no user agent
change, no stealth flags, no direct score API calls. The scraper container was not restarted or stopped.

## Parser (built)

`scraper/parsers/lotusbook_scoreboard.py` (stdlib DOM builder reused from `lotusbook_markets.py`, no bs4):

- `parse_scoreboard_html(html) -> ScoreboardState | None`. `SideScore(team, runs, wickets, run_rate, crr, rrr)`
  per side (`home` = left, `away` = right). `ScoreboardState` carries `started`, `innings`, `overs`,
  `balls_bowled`, `max_overs`, `balls_remaining`, `target`, `batting_team`, `is_simulated`, `comment`,
  `status_text`, plus a `batting_side` property.
- `to_match_context(state, match_id, observed_at) -> MatchContext | None`. Returns None when not started, or when
  the batting side, its runs or wickets, the innings, the overs or the max overs cannot be read. Nothing is
  estimated.
- `parse_result_comment(text) -> (winner|None, "runs"|"wickets"|"tie"|"no_result", margin|None) | None`.
  UNVERIFIED: no finished-match markup has been captured.

Deviations from the story, on purpose:
- `batting_team`: the markup has no per-side innings. The side showing a CRR label is the batting side. If neither
  side shows CRR, the side without an RR label is inferred to be batting when the other side shows RR (a completed
  innings). Otherwise None.
- `to_match_context` returns None rather than defaults (for example wickets 0) when a value is unknown.

## Discovery

Listing: `https://lotusbook.cc/cricket`, default headless Chromium, viewport 1366x768.
- At 8 s and at 30 s the listing had 0 anchors with `#inPlay` (`a[href*="inPlay"]` count 0). About 30 anchors were
  present with `/cricket/<teams>/4/<id>` hrefs and no hash.
- Anchors whose text starts with "LIVE" were: Toronto Kings vs Montreal Warriors (76321652), St Lucia Kings SRL vs
  Trinbago Knight Riders SRL (76110448), Sri Lanka SRL vs New Zealand SRL (76108412). I used these as live matches.

Match page variants (45 s polling for `.sr-simcrick-scb__wrapper`, the Toronto match unless noted):

| Variant | Result |
|---|---|
| A. default headless, 1366x768, no scroll | wrapper count 0, no `sr-simcrick` nodes after 45 s |
| B. as A, scroll to bottom then top at about 6 s | wrapper count 0 |
| C. `channel="chromium"` (new headless), 1366x768 | wrapper count 0 |
| D. default headless, 900x800 | wrapper count 0 |
| E. SRL match 76110448, default headless, 40 s, failed requests logged | widgetloader `net::ERR_BLOCKED_BY_ORB` |

What the page did in every variant: the `widgetloader` `<script>` element was present in the DOM, the widgetloader
request went out, and the `b2b.max247.co/api/public/match-center/score/v3/4/<id>` and `sma-event` requests returned
200. No Sportradar iframe was created.

Key finding (E): the widgetloader request is started at about 2.3 s and fails at about 2.4 s with
`net::ERR_BLOCKED_BY_ORB` (Opaque Response Blocking). No response reaches the page, so the widget is never
inserted. This differs from the story's background, which says the loader is never requested. Here it is requested
and blocked at the network layer. I did not investigate why ORB blocks it (that needs response headers and a
change to request handling, which is outside this read-only task).

Not tried: headed Chromium (no display in the container); a different user agent or stealth flags (prohibited);
clicking through the page (prohibited).

Conclusion: in the scraper's headless Chromium the Sportradar widget does not render under any variant tried
(default, scrolled, new headless, 900 px width). The loader is blocked by ORB before the page runs. The
orchestrator decides the next step. Parsing is unaffected: the captured fixtures are the only source of markup.

## Tests

- `tests/unit/test_s51_scoreboard.py`: 24 tests, all passing. Covers both fixtures, "16.1" = 97 bowled / 23
  remaining, RR/CRR/RRR, pre-start (`started=False`, no context), synthetic edge cases, and result-comment forms.
- Full suite `python -m pytest tests -q -p no:cacheprovider --ignore=tests/e2e`: 829 passed, 3 failed. The 3
  failures are in other agents' in-progress tests and do not import the new module:
  - `tests/unit/test_s37_recording.py::test_simulated_event_is_not_recorded_even_with_filter_disabled`
  - `tests/unit/test_s47_market_pages.py::TestEligibility::test_simulated_match_is_never_eligible`
  - `tests/unit/test_s50_record_srl.py::test_srl_event_is_not_result_detected`

Files created: `scraper/parsers/lotusbook_scoreboard.py`, `tests/unit/test_s51_scoreboard.py`,
`docs/stories/S51-report.md`. Probe scripts in `logs/` were deleted. Nothing committed.
