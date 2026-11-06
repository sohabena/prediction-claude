"""Build clean training and testing dataset linking historical odds (OddsPapi, aussportsbetting) with Cricsheet.

Outputs:
- data/historical_odds/training_dataset.jsonl: one row per match with market prices, Elo features, and true outcomes.
"""

from __future__ import annotations

import json
import math
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import joblib
import numpy as np

from scripts.benchmark_historical_odds import _team_alias
from training import historical_benchmark as hb
from training import historical_odds as ho
from training.dataset import CricsheetIndex
from training.market import TRADING_DEVIG, market_prob_home


def logit(p: float, eps: float = 1e-6) -> float:
    p_clipped = max(eps, min(1.0 - eps, p))
    return float(np.log(p_clipped / (1.0 - p_clipped)))


def elo_win_probability(elo_diff: float) -> float:
    return float(1.0 / (1.0 + 10.0 ** (-elo_diff / 400.0)))


def main() -> int:
    data_root = ROOT / "data"
    ratings_path = data_root / "ratings" / "cricket_ratings.joblib"
    odds_root = data_root / "historical_odds"
    out_path = odds_root / "training_dataset.jsonl"

    print(f"Loading ratings bundle from {ratings_path}...")
    t0 = time.time()
    ratings_bundle = joblib.load(ratings_path)
    ratings = ratings_bundle["ratings"]
    print(f"Loaded {len(ratings.rows)} Cricsheet matches in {time.time() - t0:.2f}s")

    print("Building Cricsheet index...")
    t1 = time.time()
    entries = [
        {
            "match_id": mid,
            "teams": list(row.teams),
            "dates": [row.date],
            "match_type": row.match_type,
        }
        for mid, row in ratings.rows.items()
    ]
    index = CricsheetIndex(entries, team_alias=_team_alias())
    print(f"Index built in {time.time() - t1:.2f}s")

    print("Reading historical odds records...")
    t2 = time.time()
    records: List[ho.OddsRecord] = []
    source_counts = Counter()
    for source in ho.SOURCES:
        rows, manifest = ho.read_store(source, root=odds_root)
        records.extend(rows)
        source_counts[source] = len(rows)
    print(f"Read {len(records)} records from {dict(source_counts)} in {time.time() - t2:.2f}s")

    print("Linking closing odds to Cricsheet matches...")
    t3 = time.time()
    closing = hb.closing_by_match(records, index)
    print(f"Linked {len(closing)} matches in {time.time() - t3:.2f}s")

    out_records: List[Dict[str, Any]] = []
    skipped = Counter()

    for mid, c in closing.items():
        if mid not in ratings.rows:
            skipped["missing_cricsheet_row"] += 1
            continue
        row = ratings.rows[mid]

        # Determine winner
        if row.winner == row.home:
            home_won = 1
        elif row.winner == row.away:
            home_won = 0
        else:
            skipped["tie_or_no_result"] += 1
            continue

        price_home = float(c["price_home"])
        price_away = float(c["price_away"])
        p_market = float(c["p_home"])

        if price_home <= 1.0 or price_away <= 1.0 or not (0.01 < p_market < 0.99):
            skipped["bad_prices"] += 1
            continue

        match_date = str(row.date)[:10]

        # Compute pre-match ratings features
        try:
            pm = ratings.pre_match(mid, match_date)
            elo_home = float(pm["pm_elo_home"])
            elo_away = float(pm["pm_elo_away"])
            elo_diff = elo_home - elo_away
            p_elo = elo_win_probability(elo_diff)
            xi_home = float(pm.get("pm_xi_home", 0.0))
            xi_away = float(pm.get("pm_xi_away", 0.0))
            xi_diff = xi_home - xi_away
            toss_home = float(pm.get("pm_toss_home", 0.0))
            toss_bat = float(pm.get("pm_toss_bat", 0.5))
            venue_chase = float(pm.get("pm_venue_chase_pct", 50.0))
            tier = float(pm.get("pm_tier", row.tier))
        except Exception:
            elo_home = 1500.0
            elo_away = 1500.0
            elo_diff = 0.0
            p_elo = 0.5
            xi_home = 0.0
            xi_away = 0.0
            xi_diff = 0.0
            toss_home = 0.0
            toss_bat = 0.5
            venue_chase = 50.0
            tier = float(row.tier)

        overround = (1.0 / price_home) + (1.0 / price_away)
        l_market = logit(p_market)
        l_elo = logit(p_elo)
        delta_elo_mkt = l_elo - l_market

        record = {
            "match_id": str(mid),
            "date": match_date,
            "start_time_utc": c.get("start_time_utc", match_date),
            "home": str(row.home),
            "away": str(row.away),
            "winner": str(row.winner),
            "home_won": home_won,
            "family": str(row.family).lower(),
            "gender": str(row.gender).lower(),
            "competition": str(c.get("competition") or getattr(row, "competition", "unknown")),
            "tier": int(tier),
            "venue": str(row.venue or ""),
            "bookmaker": str(c.get("bookmaker", "unknown")),
            "bookmaker_rank": int(c.get("bookmaker_rank", 2)),
            "source": str(c.get("source", "unknown")),
            "price_home": price_home,
            "price_away": price_away,
            "p_market": p_market,
            "overround": overround,
            "p_elo": p_elo,
            "elo_home": elo_home,
            "elo_away": elo_away,
            "elo_diff": elo_diff,
            "xi_diff": xi_diff,
            "toss_home": toss_home,
            "toss_bat": toss_bat,
            "venue_chase_pct": venue_chase,
            "l_market": l_market,
            "l_elo": l_elo,
            "delta_elo_mkt": delta_elo_mkt,
            "is_home_favourite": 1 if p_market >= 0.5 else 0,
        }
        out_records.append(record)

    # Sort chronologically by date and match_id
    out_records.sort(key=lambda r: (r["date"], r["match_id"]))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for r in out_records:
            f.write(json.dumps(r) + "\n")

    print(f"\nSuccessfully wrote {len(out_records)} records to {out_path}")
    print(f"Skipped: {dict(skipped)}")
    print(f"Date range: {out_records[0]['date']} to {out_records[-1]['date']}")
    print(f"Home wins: {sum(r['home_won'] for r in out_records)} / {len(out_records)} ({sum(r['home_won'] for r in out_records)/len(out_records):.3f})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

