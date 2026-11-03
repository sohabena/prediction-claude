# S51 — Read the score from the match page (Sportradar scoreboard)

**Priority:** P0 · **Tier:** haiku · **Depends on:** none · **Wave:** 16A

## Story

As the strategy owner I want the live score (runs, wickets, overs, innings, target, run rates) recorded for every
match, because the LotusBook listing shows no score at all and `match_context` is currently empty for this run.

## Background (verified 2026-11-03)

- Match pages embed a Sportradar widget. Its scoreboard markup (class prefix `sr-simcrick-scb__`) is saved, unchanged
  apart from one attribute, in:
  - `tests/fixtures/lotusbook/scoreboard_srl_live_20261103.html`: 183/5 (RR 9.15) : 74/7 (CRR 4.58, RRR 28.70),
    status "Simulated match - INN 2 | 16.1/20 OV", comment "Trinbago Knight Riders SRL are 74/7 after 16.1 overs
    chasing 183".
  - `tests/fixtures/lotusbook/scoreboard_real_prestart_20261103.html`: real match, play not started: no
    `__result` nodes, status "03 Nov | 04:30", empty comment.
- **Open problem:** in the scraper's headless Chromium the widget never renders (`.sr-simcrick-scb__wrapper` count 0
  after 30 s at widths 1366, 900, 420) and the Sportradar loader (`ws-cdn001.akamaized.net/.../widgetloader`) is
  never requested. The page's own feed `b2b.max247.co/api/public/match-center/score/v3/4/<id>` is requested but
  returns `{"result":{"stats":{},"scorePipeline":"legacy"}}`. In a normal (headed) browser the widget renders.
- Existing context model: `shared/schemas.py` `MatchContext`, DB `match_context` (score, wickets, overs, run_rate,
  req_run_rate, innings, balls_remaining, batting_team, bowling_team, status, match_format).

## Owned files

- New `scraper/parsers/lotusbook_scoreboard.py` (pure parser: HTML → `ScoreboardState` dataclass or `None`)
- New `tests/unit/test_s51_scoreboard.py`
- New `docs/stories/S51-report.md` (discovery findings)

## Tasks

1. Parser, stdlib only (reuse the tiny DOM builder pattern in `scraper/parsers/lotusbook_markets.py`; do not import
   bs4). From the scoreboard it returns: `home_team`, `away_team` (team-name order: left then right), per side
   `runs`, `wickets` (from `__result` "183/5"; a score with no slash, e.g. "183", means all out is unknown → wickets
   None), `innings` (from "INN 2"), `overs` and `max_overs` (from "16.1/20"), `run_rate`/`crr`/`rrr` (from
   `__extra-value` next to RR/CRR/RRR labels), `target` (from "chasing N", else None), `batting_team` (the side
   whose extra shows CRR; else the side with the latest innings), `is_simulated` (presence of
   `__simulated-info`), `comment` (raw text), `started` (False when there is no `__result`). Also `balls_remaining`
   computed from overs (cricket overs: "16.1" is 16 overs 1 ball) and `max_overs`.
2. A second function `to_match_context(state, match_id, observed_at)` returning a `shared.schemas.MatchContext`
   (or a plain dict matching the `match_context` columns) **only when `started`**.
3. A result-comment helper: `parse_result_comment(text)` recognising finished-match comments such as
   "X won by 25 runs", "X won by 6 wickets", "Match tied", "No result", returning
   `(winner|None, result_type, margin)` or None. Write the regexes from these forms; mark them UNVERIFIED in the
   docstring because no finished-match markup has been captured.
4. **Discovery (read-only, no workaround):** with the scraper's Playwright in the container
   (`docker exec phoenix-scraper python ...`, files under `/app/logs`, set `MSYS_NO_PATHCONV=1` in Git Bash),
   check whether the widget renders in headless when the page is scrolled to the top, given more time, or with
   Playwright's `chromium` new-headless mode. **Do not change the user agent, do not hide `navigator.webdriver`,
   do not use any stealth plugin, do not call the score API yourself, do not click.** Record what you tried and
   what happened in `S51-report.md`. If it cannot render, say so plainly; the orchestrator decides next.
5. Tests: both fixtures; overs "16.1" → 97 balls bowled, 23 remaining of 120; RRR/CRR parsed; pre-start returns
   `started=False` and `to_match_context` returns None; result-comment forms.

## Guardrails

Pure parser; no network in tests. Do not edit `scraper/manager.py`, `scraper/market_pages.py` or any other existing
file. Delete any probe scripts you put in `logs/`. Do not commit.
