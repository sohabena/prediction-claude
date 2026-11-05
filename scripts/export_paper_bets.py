"""Downloadable paper (virtual) bet reports: one Excel workbook and CSV files. Read-only.

Sheets: Notes, Summary (per variant), By Match, Match x Variant, By Era, All Bets. Totals are formulas over the
All Bets sheet, so they recalculate in Excel. Eras mark which version of the trading code placed a bet: results
from the early eras came from bugs that were fixed the same day and are not evidence about the strategy.

Usage::

    POSTGRES_HOST=localhost python scripts/export_paper_bets.py [--out reports/paper/exports]
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Code eras (UTC, 2026-11-03). Each starts when a fix went live.
ERAS = [
    ("A", "2026-11-03 00:00", "Before the band fix: false edges from power de-vig, control traded"),
    ("B", "2026-11-03 10:24", "Band fix; no sanity guards yet; pre-match model priced in-play bets"),
    ("C", "2026-11-03 11:25", "S75 guards; in-play still allowed the pre-match model until S83"),
    ("D", "2026-11-03 12:36", "Current strategy: S83 in-play rule, S90 tickets, corrected price view, placebos"),
    ("E", "2026-11-04 02:50", "Adds the market-dynamics variants (mkt_dyn, mkt_dyn_e06); twin matches linked (S96)"),
    ("F", "2026-11-04 13:29", "S105: exchange market owns match_odds; bookmaker-only real matches are not traded"),
    ("G", "2026-11-05 07:41", "S106: model variants trade in play only (R1 failed pre-match); 30 bets a ceiling"),
]
CONTROLS = {"market_only", "placebo_random", "placebo_favourite"}

ROWS_SQL = """
    SELECT b.bet_id, b.variant, b.match_id, t.team_home, t.team_away, t.competition, t.is_simulated,
           b.decided_at, b.filled_at, b.status, b.side, b.selection, b.team, b.price_limit, b.price_filled,
           b.stake, b.liability, b.reason, b.source, b.in_play, b.p_model, b.p_market, b.p_used, b.edge,
           b.pnl, b.result, b.clv, b.closing_price, b.settled_at,
           r.result_type, r.winner, r.margin, r.source AS result_source
    FROM paper_bets b
    JOIN match_training_status t USING (match_id)
    LEFT JOIN match_results r USING (match_id)
    ORDER BY b.decided_at
"""


def era_of(decided_at: datetime) -> str:
    era = ERAS[0][0]
    for code, start, _ in ERAS:
        if decided_at >= datetime.fromisoformat(start).replace(tzinfo=timezone.utc):
            era = code
    return era


async def load() -> list[dict]:
    from sqlalchemy import text

    from shared.db import close_database, get_session

    async with get_session() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        rows = [dict(r) for r in (await session.execute(text(ROWS_SQL))).mappings().all()]
    await close_database()
    for r in rows:
        r["era"] = era_of(r["decided_at"])
        r["match"] = f"{r['team_home']} v {r['team_away']}"
        r["group"] = "control" if r["variant"] in CONTROLS else "strategy"
    return rows


COLUMNS = [
    ("decided_at", "Decided (UTC)"), ("era", "Era"), ("match_id", "Match id"), ("match", "Match"),
    ("competition", "Competition"), ("variant", "Variant"), ("group", "Group"), ("status", "Status"),
    ("side", "Side"), ("team", "Team"), ("in_play", "In play"), ("reason", "Reason"), ("source", "Priced by"),
    ("price_limit", "Price limit"), ("price_filled", "Price filled"), ("stake", "Stake"), ("liability", "Liability"),
    ("p_model", "P model"), ("p_market", "P market"), ("p_used", "P used"), ("edge", "Edge"), ("clv", "CLV (prob)"),
    ("pnl", "P&L"), ("result_type", "Match result"), ("winner", "Winner"), ("margin", "Margin"),
]


def _cell(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def write_workbook(rows: list[dict], path: Path) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.workbook.properties import CalcProperties

    font, bold = Font(name="Arial", size=10), Font(name="Arial", size=10, bold=True)
    head_fill = PatternFill("solid", fgColor="DDE3EA")
    money, pct = '#,##0;(#,##0);"-"', "0.0%"

    wb = Workbook()
    wb.calculation = CalcProperties(fullCalcOnLoad=True)

    def header(ws, labels, row=1):
        for c, label in enumerate(labels, 1):
            cell = ws.cell(row=row, column=c, value=label)
            cell.font, cell.fill = bold, head_fill
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    def widths(ws, cols):
        for i, w in enumerate(cols, 1):
            ws.column_dimensions[get_column_letter(i)].width = w

    # ---- All Bets (values) ------------------------------------------------------------------------------------
    bets = wb.active
    bets.title = "All Bets"
    header(bets, [label for _, label in COLUMNS])
    for i, r in enumerate(rows, 2):
        for c, (key, _) in enumerate(COLUMNS, 1):
            cell = bets.cell(row=i, column=c, value=_cell(r.get(key)))
            cell.font = font
            if key in ("stake", "liability", "pnl"):
                cell.number_format = money
            elif key == "decided_at":
                cell.number_format = "yyyy-mm-dd hh:mm:ss"
    n = len(rows) + 1
    widths(bets, [19, 5, 13, 34, 26, 17, 9, 9, 6, 22, 7, 9, 11, 10, 10, 11, 11, 8, 8, 8, 8, 9, 11, 11, 20, 18])
    bets.freeze_panes = "A2"
    bets.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{n}"
    col = {key: get_column_letter(i) for i, (key, _) in enumerate(COLUMNS, 1)}
    rng = {k: f"'All Bets'!${v}$2:${v}${n}" for k, v in col.items()}

    # ---- Summary per variant (formulas) -----------------------------------------------------------------------
    summ = wb.create_sheet("Summary", 0)
    variants = sorted({r["variant"] for r in rows}, key=lambda v: (v in CONTROLS, v))
    summ["A1"] = "Paper (virtual) bets: results per variant"
    summ["A1"].font = Font(name="Arial", size=13, bold=True)
    summ["A2"] = f"All bets in paper_bets as of {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. Paper money only."
    summ["A2"].font = font
    labels = ["Variant", "Group", "Settled bets", "Matches settled", "Staked (settled)", "P&L (settled)",
              "Return on stake", "Open bets", "Unfilled", "P&L, current strategy (era D)"]
    header(summ, labels, row=4)
    for i, v in enumerate(variants, 5):
        summ.cell(row=i, column=1, value=v)
        summ.cell(row=i, column=2, value="control" if v in CONTROLS else "strategy")
        summ.cell(row=i, column=3, value=f'=COUNTIFS({rng["variant"]},A{i},{rng["status"]},"settled")')
        settled_matches = len({r["match_id"] for r in rows if r["variant"] == v and r["status"] == "settled"})
        summ.cell(row=i, column=4, value=settled_matches)
        summ.cell(row=i, column=5, value=f'=SUMIFS({rng["stake"]},{rng["variant"]},A{i},{rng["status"]},"settled")')
        summ.cell(row=i, column=6, value=f'=SUMIFS({rng["pnl"]},{rng["variant"]},A{i},{rng["status"]},"settled")')
        summ.cell(row=i, column=7, value=f"=IFERROR(F{i}/E{i},0)")
        summ.cell(row=i, column=8, value=f'=COUNTIFS({rng["variant"]},A{i},{rng["status"]},"filled")')
        summ.cell(row=i, column=9, value=f'=COUNTIFS({rng["variant"]},A{i},{rng["status"]},"unfilled")')
        summ.cell(row=i, column=10, value=(f'=SUMIFS({rng["pnl"]},{rng["variant"]},A{i},{rng["status"]},"settled",'
                                           f'{rng["era"]},"D")'))
        for c in range(1, 11):
            cell = summ.cell(row=i, column=c)
            cell.font = font
            cell.number_format = pct if c == 7 else (money if c in (5, 6, 10) else "General")
    last = 4 + len(variants)
    strat_rows = [i for i, v in enumerate(variants, 5) if v not in CONTROLS]
    t = last + 2
    summ.cell(row=t, column=1, value="All strategy variants").font = bold
    for c, letter in ((3, "C"), (5, "E"), (6, "F"), (8, "H"), (9, "I"), (10, "J")):
        cell = summ.cell(row=t, column=c, value=f'=SUMIFS({letter}5:{letter}{last},$B$5:$B${last},"strategy")')
        cell.font, cell.number_format = bold, money if c in (5, 6, 10) else "General"
    cell = summ.cell(row=t, column=7, value=f"=IFERROR(F{t}/E{t},0)")
    cell.font, cell.number_format = bold, pct
    summ.cell(row=t + 2, column=1, value=(
        "Read with the By Era sheet: eras A and B were bugs fixed on 2026-11-03 (false edges; pre-match model on "
        "in-play bets). Only era D is the current strategy. Matches settled is a count of distinct matches.")).font = font
    widths(summ, [22, 10, 12, 12, 16, 14, 12, 10, 10, 18])
    summ.freeze_panes = "A5"

    # ---- By Match (formulas) ----------------------------------------------------------------------------------
    bym = wb.create_sheet("By Match", 1)
    matches = []
    for r in rows:
        if r["match_id"] not in [m["match_id"] for m in matches]:
            matches.append(r)
    header(bym, ["Match id", "Match", "Competition", "Result", "Winner", "Settled bets", "Strategy P&L",
                 "Control P&L (placebos + market_only)", "Open bets", "First bet (UTC)"])
    for i, m in enumerate(matches, 2):
        values = [m["match_id"], m["match"], m["competition"], m.get("result_type") or "pending", m.get("winner") or "",
                  f'=COUNTIFS({rng["match_id"]},A{i},{rng["status"]},"settled")',
                  f'=SUMIFS({rng["pnl"]},{rng["match_id"]},A{i},{rng["status"]},"settled",{rng["group"]},"strategy")',
                  f'=SUMIFS({rng["pnl"]},{rng["match_id"]},A{i},{rng["status"]},"settled",{rng["group"]},"control")',
                  f'=COUNTIFS({rng["match_id"]},A{i},{rng["status"]},"filled")',
                  _cell(m["decided_at"])]
        for c, v in enumerate(values, 1):
            cell = bym.cell(row=i, column=c, value=v)
            cell.font = font
            if c in (7, 8):
                cell.number_format = money
            elif c == 10:
                cell.number_format = "yyyy-mm-dd hh:mm"
    widths(bym, [13, 36, 26, 11, 20, 11, 14, 18, 9, 17])
    bym.freeze_panes = "A2"

    # ---- Match x Variant grid (formulas) ----------------------------------------------------------------------
    grid = wb.create_sheet("Match x Variant", 2)
    header(grid, ["Match id", "Match"] + variants)
    for i, m in enumerate(matches, 2):
        grid.cell(row=i, column=1, value=m["match_id"]).font = font
        grid.cell(row=i, column=2, value=m["match"]).font = font
        for c, v in enumerate(variants, 3):
            letter = get_column_letter(c)
            cell = grid.cell(row=i, column=c, value=(f'=SUMIFS({rng["pnl"]},{rng["match_id"]},$A{i},'
                                                    f'{rng["variant"]},{letter}$1,{rng["status"]},"settled")'))
            cell.font, cell.number_format = font, money
    tot = len(matches) + 2
    grid.cell(row=tot, column=2, value="Total").font = bold
    for c in range(3, 3 + len(variants)):
        letter = get_column_letter(c)
        cell = grid.cell(row=tot, column=c, value=f"=SUM({letter}2:{letter}{tot - 1})")
        cell.font, cell.number_format = bold, money
    widths(grid, [13, 36] + [14] * len(variants))
    grid.freeze_panes = "C2"

    # ---- By Era (formulas) ------------------------------------------------------------------------------------
    era = wb.create_sheet("By Era", 3)
    header(era, ["Era", "From (UTC)", "What the code did", "Settled bets", "Strategy P&L", "Control P&L"])
    for i, (code, start, desc) in enumerate(ERAS, 2):
        values = [code, start, desc,
                  f'=COUNTIFS({rng["era"]},A{i},{rng["status"]},"settled")',
                  f'=SUMIFS({rng["pnl"]},{rng["era"]},A{i},{rng["status"]},"settled",{rng["group"]},"strategy")',
                  f'=SUMIFS({rng["pnl"]},{rng["era"]},A{i},{rng["status"]},"settled",{rng["group"]},"control")']
        for c, v in enumerate(values, 1):
            cell = era.cell(row=i, column=c, value=v)
            cell.font = font
            if c in (5, 6):
                cell.number_format = money
    widths(era, [6, 17, 80, 12, 14, 14])

    # ---- Notes ------------------------------------------------------------------------------------------------
    notes = wb.create_sheet("Notes", 0)
    lines = [
        ("PHOENIX paper (virtual) bet report", True),
        (f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC from the paper_bets table (read-only).", False),
        ("Paper money only: no real bet was placed. Budget 100,000 per match per variant, hard limit 120,000.", False),
        ("Strategy variants: w = model weight (0.3/0.5/0.7), e = minimum edge (3%/6%).", False),
        ("Controls: market_only (no model), placebo_random (random side, same timing and stakes as the reference "
         "variant), placebo_favourite (backs the favourite at the same times).", False),
        ("Settled = the match has a result. Open = filled, match not finished. Unfilled = the price moved away "
         "within the 60 s placement delay.", False),
        ("Eras: the trading code was fixed several times on 2026-11-03. Eras A and B lost money through bugs that are "
         "now fixed; only era D reflects the current strategy. See the By Era sheet.", False),
        ("Evidence: a few settled matches cannot show an edge. The project's rule is at least 30 settled matches "
         "(R3) before any conclusion. Totals here are sums, not confidence intervals.", False),
        ("Totals are formulas over the All Bets sheet and recalculate when the workbook opens.", False),
    ]
    for i, (text_, is_bold) in enumerate(lines, 1):
        cell = notes.cell(row=i, column=1, value=text_)
        cell.font = Font(name="Arial", size=13 if i == 1 else 10, bold=is_bold)
    notes.column_dimensions["A"].width = 130
    wb.active = 1  # open on Summary
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def write_csvs(rows: list[dict], out: Path, stamp: str) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    bets_path = out / f"paper_bets_{stamp}.csv"
    with bets_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([label for _, label in COLUMNS])
        for r in rows:
            w.writerow([_cell(r.get(k)) for k, _ in COLUMNS])
    match_path = out / f"paper_matches_{stamp}.csv"
    with match_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Match id", "Match", "Competition", "Result", "Winner", "Settled bets", "Strategy P&L",
                    "Control P&L", "Open bets"])
        seen: dict[str, dict] = {}
        for r in rows:
            m = seen.setdefault(r["match_id"], {"r": r, "n": 0, "s": 0.0, "c": 0.0, "o": 0})
            if r["status"] == "settled":
                m["n"] += 1
                m["s" if r["group"] == "strategy" else "c"] += float(r["pnl"] or 0.0)
            elif r["status"] == "filled":
                m["o"] += 1
        for mid, m in seen.items():
            r = m["r"]
            w.writerow([mid, r["match"], r["competition"], r.get("result_type") or "pending", r.get("winner") or "",
                        m["n"], round(m["s"]), round(m["c"]), m["o"]])
    return [bets_path, match_path]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(ROOT / "reports" / "paper" / "exports"))
    args = ap.parse_args(argv)
    rows = asyncio.run(load())
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    out = Path(args.out)
    xlsx = out / f"paper_bets_report_{stamp}.xlsx"
    write_workbook(rows, xlsx)
    paths = [xlsx, *write_csvs(rows, out, stamp)]
    for p in paths:
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
