"""S88 paper dashboard: a static, read-only page with the live positions and the scorecard headline.

Usage::

    python scripts/paper_dashboard.py              # write reports/paper/dashboard.html once
    python scripts/paper_dashboard.py --every 60   # rewrite it every 60 seconds until stopped

The page is self-contained: inline CSS, no script, no external resource, light or dark by prefers-color-scheme, and a
meta refresh every minute. It shows, for each real match being traded now, the current back and lay prices, p_market,
the last decision's p_model, each variant's P&L if the home side wins and if the away side wins, the worst case against
the 100,000 cap, and the last five trades. Below it is the same headline and Tier-B section as the daily report
(``scripts/paper_report.py``), so both show the same numbers. Read-only: SELECTs in a READ ONLY transaction, and the
only write is the HTML file (written to a temp file and replaced).

The trader loop (``scripts/run_paper_trader.py``) calls ``write_dashboard`` once a minute.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import paper_report as rep  # noqa: E402
from virtual_trading import scorecard as sc  # noqa: E402

REPORT_PATH = ROOT / "reports" / "paper" / "dashboard.html"
EVERY_SECONDS = 60
MAX_LIVE = 40  # matches shown in the live section

CSS = """
:root{--bg:#f6f6f3;--fg:#1c1d1a;--muted:#6a6c66;--card:#ffffff;--line:#dadad3;--good:#1d7a4a;--bad:#b3261e;--accent:#2a5db0;--head:#ecece6}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#141513;--fg:#e7e7e1;--muted:#9c9e96;--card:#1d1f1c;--line:#383b36;--good:#5fcf8c;--bad:#ff8a80;--accent:#8ab4f8;--head:#252823}}
:root[data-theme="dark"]{--bg:#141513;--fg:#e7e7e1;--muted:#9c9e96;--card:#1d1f1c;--line:#383b36;--good:#5fcf8c;--bad:#ff8a80;--accent:#8ab4f8;--head:#252823}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1120px;margin:0 auto;padding:16px}
h1{font-size:20px;margin:4px 0 2px}
h2{font-size:16px;margin:24px 0 8px;padding-bottom:4px;border-bottom:1px solid var(--line)}
h3{font-size:14px;margin:16px 0 6px}
h4{font-size:13px;margin:12px 0 4px;color:var(--muted)}
p{margin:6px 0}
.muted{color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin:10px 0}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;margin:6px 0 8px;font-size:13px}
th,td{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top;white-space:nowrap}
th{background:var(--head);font-weight:600}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.pos{color:var(--good)}
.neg{color:var(--bad)}
code{font-family:ui-monospace,Consolas,monospace;font-size:12px}
.kv{display:flex;flex-wrap:wrap;gap:6px 16px;margin:4px 0}
.kv span{white-space:nowrap}
footer{margin:28px 0 8px;color:var(--muted);font-size:12px}
"""


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _inline(text: str) -> str:
    """Escape, then turn the report's **bold** and `code` into HTML. Nothing else is interpreted."""
    out = _esc(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    return re.sub(r"`(.+?)`", r"<code>\1</code>", out)


def _signed(value: Any, digits: int = 0) -> str:
    if value is None:
        return "n/a"
    x = float(value)
    cls = "pos" if x > 0 else ("neg" if x < 0 else "")
    return f'<span class="{cls}">{x:+,.{digits}f}</span>'


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """A table whose cells are already HTML (escaped by the caller); the header is escaped here."""
    head = "".join(f"<th>{_esc(h)}</th>" for h in header)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _section_html(section: Mapping[str, Any], level: str) -> str:
    parts = [f"<{level}>{_esc(section['title'])}</{level}>"]
    parts.extend(f"<p>{_inline(line)}</p>" for line in section["lines"])
    if section["header"]:
        cells = [[_esc(c) for c in row] for row in section["rows"]]
        parts.append(_table(section["header"], cells))
    return "".join(parts)


def _live_card(match: Mapping[str, Any], cap: float) -> str:
    book = match["book"]

    def price(key: str) -> str:
        value = book.get(key)
        return "n/a" if value is None else f"{float(value):.2f}"

    start = match["start"]
    when = f"{start:%Y-%m-%d %H:%M} UTC" if start else "start not recorded"
    state = "in play" if match["in_play"] else "pre-match"
    p_market = "n/a" if match["p_market"] is None else f"{match['p_market']:.3f}"
    p_model = "n/a" if match["p_model"] is None else f"{float(match['p_model']):.3f}"
    positions = [
        [
            _esc(p["variant"]) + (' <span class="muted">(placebo)</span>' if p["placebo"] else ""),
            str(p["fills"]), str(p["pending"]), _signed(p["home"]), _signed(p["away"]),
            f"{p['worst']:,.0f}", f"{p['worst_share'] * 100:.1f}%",
        ]
        for p in match["positions"]
    ]
    trades = [
        [
            _esc(f"{t['decided_at']:%H:%M:%S}" if t.get("decided_at") else ""),
            _esc(t["variant"]), _esc(f"{t['side']} {t['selection']}"),
            _esc("n/a" if t.get("price_limit") is None else f"{float(t['price_limit']):.2f}"),
            _esc("n/a" if t.get("price_filled") is None else f"{float(t['price_filled']):.2f}"),
            _esc(f"{float(t['stake']):,.0f}"), _esc(t.get("status") or ""),
            _esc("n/a" if t.get("p_used") is None else f"{float(t['p_used']):.3f}"),
        ]
        for t in match["last_trades"]
    ]
    return (
        '<section class="card">'
        f"<h3>{_esc(match['team_home'])} v {_esc(match['team_away'])} "
        f'<span class="muted">{_esc(match["match_id"])}, {_esc(match["competition"] or "competition not recorded")}, '
        f"{_esc(state)}, start {_esc(when)}</span></h3>"
        '<div class="kv">'
        f"<span>back home {price('back_home')} / lay home {price('lay_home')}</span>"
        f"<span>back away {price('back_away')} / lay away {price('lay_away')}</span>"
        f"<span>p_market {p_market}</span>"
        f"<span>p_model {p_model} <span class=\"muted\">(last decision)</span></span>"
        "</div>"
        "<h4>Positions by variant (P&amp;L if the side wins, against the "
        f"{cap:,.0f} cap)</h4>"
        + _table(["variant", "fills", "pending", "if home wins", "if away wins", "worst case", "share of cap"],
                 positions)
        + "<h4>Last five trades (any variant)</h4>"
        + _table(["decided", "variant", "side", "limit", "filled at", "stake", "status", "p used"], trades)
        + "</section>"
    )


def render_html(data: Mapping[str, Any], now: datetime) -> str:
    """The whole page from ``paper_report.gather`` output plus ``live`` (``scorecard.load_live``). Pure: no network."""
    state = data["state"]
    cap = float(sc.MAX_MATCH_LOSS)
    headline = rep.headline_sections(state, data["rows"], counts=data.get("counts"), missing=data.get("missing"),
                                     gate=data.get("gate"))
    tier = rep.tier_sections(data.get("tier"))
    live = list(data.get("live") or [])
    if live:
        live_html = "".join(_live_card(m, cap) for m in live[:MAX_LIVE])
        more = f'<p class="muted">Showing {min(len(live), MAX_LIVE)} of {len(live)} live matches.</p>' if len(live) > MAX_LIVE else ""
        live_html = live_html + more
    else:
        live_html = ('<p class="card muted">No real match is being traded right now: no match has a price in the last '
                     '5 minutes. The trader is waiting for the next match.</p>')
    first = state.get("first_decided_at") or "n/a"
    last = state.get("last_decided_at") or "n/a"
    body = [
        "<h1>PHOENIX paper dashboard</h1>",
        f'<p class="muted">Generated {now.astimezone(timezone.utc):%Y-%m-%d %H:%M:%S} UTC. Read-only. Virtual bets only: '
        f"no real money, no site access. Paper bets decided {_esc(first)} to {_esc(last)}. The daily report is "
        f"<code>reports/paper</code>.</p>",
        "<h2>Headline: the measures that decide money</h2>",
        '<p class="muted">Every interval resamples matches, not bets. A count below its target is progress, not a result.</p>',
    ]
    body.extend(_section_html(s, "h3") for s in headline)
    body.append("<h2>Live now: positions per real match</h2>")
    body.append(live_html)
    body.append("<h2>Tier B: diagnostics</h2>")
    body.extend(_section_html(s, "h3") for s in tier)
    body.append(f"<footer>Refreshes every minute. Trading logic: <code>virtual_trading/paper_trader.py</code>. "
                f"Scorecard: <code>virtual_trading/scorecard.py</code>.</footer>")
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f'<meta http-equiv="refresh" content="{EVERY_SECONDS}">'
        "<meta name=\"color-scheme\" content=\"light dark\">"
        "<title>PHOENIX paper dashboard</title>"
        f"<style>{CSS}</style></head><body><main>"
        + "".join(body)
        + "</main></body></html>\n"
    )


async def build(now: datetime) -> dict[str, Any]:
    """Every input of the page, read in one READ ONLY transaction."""
    from sqlalchemy import text

    from shared.db import get_session

    async with get_session() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        data = await rep.gather(session, now)
        data["live"] = await sc.load_live(session, now)
    return data


async def write_dashboard(now: Optional[datetime] = None, path: Path = REPORT_PATH) -> Path:
    """Build the page and write it atomically (temp file, then replace). Returns the path."""
    now = now or datetime.now(timezone.utc)
    data = await build(now)
    text_out = render_html(data, now)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text_out, encoding="utf-8")
    os.replace(tmp, path)
    return path


async def _run(every: int) -> int:
    from shared.db import close_database

    try:
        while True:
            path = await write_dashboard()
            print(f"wrote {path} at {datetime.now(timezone.utc):%H:%M:%S} UTC", flush=True)
            if every <= 0:
                return 0
            await asyncio.sleep(every)
    finally:
        await close_database()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="S88 static paper dashboard (read-only; writes one HTML file).")
    parser.add_argument("--every", type=int, default=0, metavar="SECONDS",
                        help="rewrite the page every N seconds until stopped (0: once, the default)")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.every))


if __name__ == "__main__":
    raise SystemExit(main())
