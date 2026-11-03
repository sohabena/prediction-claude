"""Soft bookmaker vs sharp exchange: is there takeable value on recorded matches? Read-only.

The site shows, on the same match page, an exchange-mirror match-odds market (``market_name = 'match_odds'``) and its
own in-house "Bookmaker 0 Commission" market. A bookmaker BACK price above the exchange LAY price (or a bookmaker LAY
below the exchange BACK) is a bet the exchange itself prices as positive value.

For every bookmaker row with an active runner it takes the exchange row of the same runner as of the same time and
asks:

1. Is there an opportunity (conservative: priced against the exchange's worst side for us)?
2. Does it survive the 60 s a person needs to place it: the bookmaker price 60 s later, if still active and still
   beating the exchange then, is the fill.
3. Was it value: CLV against the exchange mid-price 10 min after the fill, and P&L on settled matches (1 unit per
   episode; one episode per match, runner and side per 5 minutes).

Usage::

    POSTGRES_HOST=localhost python scripts/analyse_soft_sharp.py [--include-srl] [--out reports/soft_sharp]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BOOKMAKER = "Bookmaker 0 Commission"
EXCHANGE = "match_odds"
PLACE_DELAY_S = 60  # manual placement (Golden Rulebook rule 1)
CLV_HORIZON_S = 600  # in-play CLV reference: 10 min after the fill
EX_MAX_AGE_S = 30  # the exchange row must be this fresh to price a bookmaker row
EPISODE_S = 300  # one episode per match, runner and side per 5 minutes

ROWS_SQL = """
    SELECT m.match_id, m.time, m.market_name, lower(btrim(m.selection)) AS selection, m.back, m.lay,
           r.team_home, r.team_away, r.is_simulated
    FROM market_ticks m JOIN match_training_status r USING (match_id)
    WHERE m.market_name IN (:bk, :ex) AND m.source = 'lotusbook'
      AND lower(m.status) = 'open' AND lower(coalesce(m.runner_status, '')) = 'active'
    ORDER BY m.time
"""
RESULTS_SQL = """
    SELECT match_id, lower(btrim(winner)) AS winner, result_type FROM match_results
    WHERE result_type IN ('win', 'tie', 'no_result')
"""


async def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    from sqlalchemy import text

    from shared.db import close_database, get_session

    async with get_session() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        rows = (await session.execute(text(ROWS_SQL), {"bk": BOOKMAKER, "ex": EXCHANGE})).mappings().all()
        results = (await session.execute(text(RESULTS_SQL))).mappings().all()
    await close_database()
    return pd.DataFrame([dict(r) for r in rows]), pd.DataFrame([dict(r) for r in results])


def _asof(left: pd.DataFrame, right: pd.DataFrame, at: str, prefix: str, tolerance_s: float) -> pd.DataFrame:
    """For each row of ``left`` the latest ``right`` row of the same match and runner at or before ``left[at]``."""
    r = right[["match_id", "selection", "time", "back", "lay"]].rename(
        columns={"time": f"{prefix}_time", "back": f"{prefix}_back", "lay": f"{prefix}_lay"}
    ).sort_values(f"{prefix}_time")
    out = pd.merge_asof(
        left.sort_values(at), r, left_on=at, right_on=f"{prefix}_time", by=["match_id", "selection"],
        direction="backward", tolerance=pd.Timedelta(seconds=tolerance_s),
    )
    return out


def analyse(rows: pd.DataFrame, results: pd.DataFrame, include_srl: bool) -> dict:
    if rows.empty:
        return {"matches": 0}
    rows = rows.copy()
    rows["time"] = pd.to_datetime(rows["time"], utc=True)
    if not include_srl:
        rows = rows[~rows["is_simulated"]]
    bk = rows[rows["market_name"] == BOOKMAKER].dropna(subset=["back", "lay"])
    ex = rows[rows["market_name"] == EXCHANGE].dropna(subset=["back", "lay"])
    matches_both = sorted(set(bk["match_id"]) & set(ex["match_id"]))
    bk = bk[bk["match_id"].isin(matches_both)]
    ex = ex[ex["match_id"].isin(matches_both)]

    # 1. Opportunities at t, priced against the exchange as of t (same JSON snapshot in practice).
    snap = _asof(bk, ex, "time", "ex", EX_MAX_AGE_S).dropna(subset=["ex_back", "ex_lay"])
    snap["back_edge"] = snap["back"] / snap["ex_lay"] - 1.0  # conservative: p = 1 / exchange lay
    snap["lay_edge"] = 1.0 - snap["lay"] / snap["ex_back"]  # conservative: p = 1 / exchange back
    snap["side"] = np.where(snap["back_edge"] > 0, "BACK", np.where(snap["lay_edge"] > 0, "LAY", ""))
    opp = snap[snap["side"] != ""].copy()
    opp["price"] = np.where(opp["side"] == "BACK", opp["back"], opp["lay"])
    opp["edge_now"] = np.where(opp["side"] == "BACK", opp["back_edge"], opp["lay_edge"])

    # 2. 60 s later: the bookmaker price then (active runner) and the exchange then.
    opp["t_fill"] = opp["time"] + pd.Timedelta(seconds=PLACE_DELAY_S)
    later_bk = bk[["match_id", "selection", "time", "back", "lay"]].rename(
        columns={"time": "bk1_time", "back": "bk1_back", "lay": "bk1_lay"}).sort_values("bk1_time")
    opp = pd.merge_asof(opp.sort_values("t_fill"), later_bk, left_on="t_fill", right_on="bk1_time",
                        by=["match_id", "selection"], direction="backward",
                        tolerance=pd.Timedelta(seconds=PLACE_DELAY_S))
    opp = _asof(opp, ex, "t_fill", "ex1", EX_MAX_AGE_S + PLACE_DELAY_S)
    opp["fill_price"] = np.where(opp["side"] == "BACK", opp["bk1_back"], opp["bk1_lay"])
    still = np.where(
        opp["side"] == "BACK",
        opp["bk1_back"] > opp["ex1_lay"],
        opp["bk1_lay"] < opp["ex1_back"],
    )
    opp["survives"] = still & opp["fill_price"].notna() & opp["ex1_back"].notna()

    # 3. CLV 10 min after the fill, against the exchange mid probability then.
    opp["t_ref"] = opp["t_fill"] + pd.Timedelta(seconds=CLV_HORIZON_S)
    opp = _asof(opp, ex, "t_ref", "ex2", 120)
    p_ref = 0.5 * (1.0 / opp["ex2_back"] + 1.0 / opp["ex2_lay"])
    opp["clv"] = np.where(opp["side"] == "BACK", p_ref - 1.0 / opp["fill_price"], 1.0 / opp["fill_price"] - p_ref)

    # Episodes: the first surviving opportunity per match, runner and side per 5 minutes.
    taken = opp[opp["survives"]].sort_values("time").copy()
    taken["block"] = (taken["time"].astype("int64") // (EPISODE_S * 1_000_000_000)).astype("int64")
    episodes = taken.drop_duplicates(subset=["match_id", "selection", "side", "block"], keep="first").copy()

    res = results.set_index("match_id") if not results.empty else pd.DataFrame()

    def pnl(row: pd.Series) -> float | None:
        if row["match_id"] not in res.index:
            return None
        r = res.loc[row["match_id"]]
        if r["result_type"] != "win":
            return 0.0
        won = r["winner"] == row["selection"]
        o = row["fill_price"]
        if row["side"] == "BACK":
            return (o - 1.0) if won else -1.0
        return -(o - 1.0) if won else 1.0

    episodes["pnl"] = episodes.apply(pnl, axis=1)
    settled = episodes.dropna(subset=["pnl"])

    def cluster_ci(df: pd.DataFrame, col: str) -> dict:
        from shared.metrics import cluster_bootstrap

        d = df.dropna(subset=[col])
        if d.empty:
            return {"estimate": None, "matches": 0, "n": 0}
        out = cluster_bootstrap(d[col].tolist(), d["match_id"].tolist(), seed=20261103)
        return {k: (float(v) if isinstance(v, (int, float, np.floating)) and v is not None else v)
                for k, v in (out.items() if isinstance(out, dict) else vars(out).items())}

    per_match = []
    for mid, g in snap.groupby("match_id"):
        o = opp[opp["match_id"] == mid]
        e = episodes[episodes["match_id"] == mid]
        per_match.append({
            "match_id": mid,
            "teams": f"{g['team_home'].iloc[0]} v {g['team_away'].iloc[0]}",
            "bookmaker_rows_priced": int(len(g)),
            "opportunities": int(len(o)),
            "survive_60s": int(o["survives"].sum()),
            "episodes": int(len(e)),
            "mean_edge_now": float(o["edge_now"].mean()) if len(o) else None,
            "mean_clv": float(e["clv"].mean()) if e["clv"].notna().any() else None,
            "pnl_units": float(e["pnl"].sum()) if e["pnl"].notna().any() else None,
        })
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "include_srl": include_srl,
        "matches_with_both_markets": len(matches_both),
        "bookmaker_rows_priced": int(len(snap)),
        "opportunities": int(len(opp)),
        "opportunity_share": float(len(opp) / len(snap)) if len(snap) else None,
        "by_side": {s: int((opp["side"] == s).sum()) for s in ("BACK", "LAY")},
        "median_edge_now": float(opp["edge_now"].median()) if len(opp) else None,
        "survive_60s": int(opp["survives"].sum()),
        "survive_share": float(opp["survives"].mean()) if len(opp) else None,
        "episodes": int(len(episodes)),
        "episode_clv": cluster_ci(episodes, "clv"),
        "settled_episodes": int(len(settled)),
        "settled_pnl_units": float(settled["pnl"].sum()) if len(settled) else None,
        "settled_pnl_per_episode": cluster_ci(settled, "pnl"),
        "per_match": per_match,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--include-srl", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "reports" / "soft_sharp"))
    args = ap.parse_args(argv)
    rows, results = asyncio.run(load())
    summary = analyse(rows, results, args.include_srl)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    name = "summary_srl.json" if args.include_srl else "summary.json"
    (out / name).write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "per_match"}, indent=2, default=str))
    for m in summary.get("per_match", []):
        print(m)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
