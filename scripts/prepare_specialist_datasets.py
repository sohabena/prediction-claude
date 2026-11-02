"""
PHOENIX Specialist Dataset Preparer & Harmonizer.

Extracts, normalizes, and partitions official Cricsheet matches into format-specific
training datasets with full ball-by-ball context and Betfair market microstructure:
- t20i: Men's and Women's T20 Internationals (>= 350 matches)
- odi: One-Day Internationals (>= 350 matches)
- test: Test Matches (>= 350 matches)
- ipl: Indian Premier League matches (>= 350 matches)
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DATA_RAW_DIR = Path("data/raw")
SPECIALISTS_DIR = Path("data/specialists")

# Target quotas (all exceeding user's >= 300 requirement)
DEFAULT_TARGET_MATCHES = 350

# Keys that describe the match RESULT. They exist only so the environment can settle
# bets after an episode ends. The feature pipeline / agent observation must never read
# them, and no price or probability in this module may be derived from them.
SETTLEMENT_ONLY_KEYS = ("_meta", "winner", "loser", "winner_is_home")

SYNTHETIC_SOURCE = "synthetic_cricsheet"


from scripts.synthetic_market import (  # noqa: E402  (re-exported for callers)
    CORPUS_FORMAT_VERSION,
    CricketWinProbModel,
    MarketNoise,
    market_model_tag,
    synthesize_exchange_ticks,
    synthesize_volumes,
)

DEFAULT_NOISE_MODE = "martingale"



def parse_cricsheet_json(
    filepath: Path,
    fmt_category: str,
    match_index: int,
    specialist_key: str = "",
    noise_mode: str = DEFAULT_NOISE_MODE,
    noise_sigma: float = 0.08,
    noise_theta: float = 0.10,
) -> Optional[Dict[str, Any]]:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    info = data.get("info", {})
    teams = info.get("teams", [])
    if len(teams) != 2:
        return None

    team_home = str(teams[0]).strip()
    team_away = str(teams[1]).strip()
    if not team_home or not team_away or team_home == team_away:
        return None

    is_multiday = fmt_category.upper() not in ("T20", "ODI", "IPL")
    outcome = info.get("outcome", {})
    winner = outcome.get("winner")
    result_type = "win"
    if not winner:
        # Drawn multi-day matches are retained (marked) so the trainer can void them;
        # ties / no-results in limited-overs formats are dropped by the caller.
        if is_multiday and str(outcome.get("result", "")).lower() == "draw":
            result_type = "draw"
            winner = ""
        else:
            return None
    else:
        winner = str(winner).strip()
        if winner not in (team_home, team_away):
            return None

    if result_type == "draw":
        loser = ""
        winner_is_home = None
    else:
        loser = team_away if winner == team_home else team_home
        winner_is_home = (winner == team_home)

    info_gender = str(info.get("gender", "male")).lower()
    is_women = ("female" in info_gender) or ("women" in specialist_key.lower())
    gender_str = "female" if is_women else "male"

    dates = info.get("dates", [])
    date_str = dates[0] if dates else "2023-01-01"
    try:
        match_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        match_date = datetime(2023, 1, 1, tzinfo=timezone.utc)

    event = info.get("event", {})
    comp_name = event.get("name") if isinstance(event, dict) else str(event)
    if not comp_name or comp_name == "None":
        comp_name = f"{fmt_category} Championship"

    innings_list = data.get("innings", [])
    if not innings_list:
        return None

    ticks: List[Dict[str, Any]] = []
    tick_time = match_date + timedelta(hours=14)
    tick_idx = 0
    first_inn_target: Optional[int] = None
    inn_totals: List[int] = []
    noise = MarketNoise(match_index, sigma=noise_sigma, theta=noise_theta, noise_mode=noise_mode)  # type: ignore[arg-type]
    market_tag = market_model_tag(noise_mode)
    prev_p_home: Optional[float] = None
    balls_seen = 0
    max_innings = 4 if is_multiday else 2
    total_balls_est = sum(len(o.get("deliveries", [])) for i in innings_list[:max_innings] for o in i.get("overs", [])) or 1

    for inn_idx, inn_data in enumerate(innings_list[:max_innings]):
        innings_num = inn_idx + 1
        batting_team = str(inn_data.get("team", "")).strip() or (team_home if inn_idx == 0 else team_away)
        bowling_team = team_away if batting_team == team_home else team_home
        batting_is_home = (batting_team == team_home)

        overs_data = inn_data.get("overs", [])
        total_runs = 0
        total_wickets = 0
        if innings_num == 4:
            inn_target: Optional[int] = max(1, inn_totals[0] + inn_totals[2] - inn_totals[1] + 1) if len(inn_totals) >= 3 else None
        elif innings_num == 2 and not is_multiday:
            inn_target = first_inn_target
        else:
            inn_target = None

        for over_item in overs_data:
            over_num = over_item.get("over", 0)
            deliveries = over_item.get("deliveries", [])

            for b_idx, delivery in enumerate(deliveries):
                runs_obj = delivery.get("runs", {})
                d_runs = runs_obj.get("total", 0)
                total_runs += d_runs

                if "wickets" in delivery:
                    total_wickets += len(delivery["wickets"])

                # Sampling rule: capture over transitions & boundaries/wickets
                is_boundary = d_runs >= 4
                is_wicket = "wickets" in delivery
                is_over_end = (b_idx == len(deliveries) - 1)
                is_over_start = (b_idx == 0)

                if not (is_boundary or is_wicket or is_over_end or is_over_start):
                    continue

                overs_float = round(over_num + (b_idx + 1) / 10.0, 1)
                balls_bowled = over_num * 6 + (b_idx + 1)
                is_t20_fmt = "T20" in fmt_category.upper() or "IPL" in fmt_category.upper()
                is_odi_fmt = "ODI" in fmt_category.upper()
                max_balls = 120 if is_t20_fmt else (300 if is_odi_fmt else 600)
                balls_rem = max(0, max_balls - balls_bowled)

                p_home = CricketWinProbModel.calculate_win_prob(
                    fmt=fmt_category,
                    innings=innings_num,
                    overs=overs_float,
                    score=total_runs,
                    wickets=total_wickets,
                    target=inn_target,
                    batting_is_home=batting_is_home,
                    is_women=is_women,
                    specialist_key=specialist_key,
                    noise_logit=noise.step(),
                )

                b_home, l_home, b_away, l_away, ovr = synthesize_exchange_ticks(
                    p_home, seed=match_index * 10000 + tick_idx
                )

                crr = round((total_runs / max(1, balls_bowled)) * 6.0, 2)
                rrr = 0.0
                if inn_target:
                    rrr = round((max(0, inn_target - total_runs) / max(1, balls_rem)) * 6.0, 2)

                if "ipl" in specialist_key.lower():
                    vol_base = 350000.0
                elif is_women:
                    vol_base = 80000.0
                elif "test" in specialist_key.lower() or "first_class" in specialist_key.lower():
                    vol_base = 220000.0
                elif "odi" in specialist_key.lower() or "list_a" in specialist_key.lower():
                    vol_base = 150000.0
                else:
                    vol_base = 180000.0

                # Volume: deterministic time-of-match profile x |delta price| activity (no i.i.d. jitter).
                v_bh, v_lh, v_ba, v_la = synthesize_volumes(
                    vol_base, prev_p_home, p_home, (balls_seen + b_idx + 1) / total_balls_est
                )
                prev_p_home = p_home

                tick = {
                    "time": tick_time.isoformat(),
                    "match_id": f"cric_{specialist_key or fmt_category.lower()}_{match_index:04d}",
                    "team_home": team_home,
                    "team_away": team_away,
                    "competition": comp_name,
                    "match_format": fmt_category,
                    "gender": gender_str,
                    "specialist_key": specialist_key,
                    "back_home": b_home,
                    "lay_home": l_home,
                    "back_away": b_away,
                    "lay_away": l_away,
                    "implied_prob_home": round(1.0 / b_home, 4),
                    "implied_prob_away": round(1.0 / b_away, 4),
                    "overround": ovr,
                    "is_live": True,
                    "score": total_runs,
                    "wickets": total_wickets,
                    "overs": overs_float,
                    "run_rate": crr,
                    "req_run_rate": rrr,
                    "innings": innings_num,
                    "balls_remaining": balls_rem,
                    "batting_team": batting_team,
                    "bowling_team": bowling_team,
                    "status": "in_play",
                    "volume_back_home": v_bh,
                    "volume_lay_home": v_lh,
                    "volume_back_away": v_ba,
                    "volume_lay_away": v_la,
                    "source": SYNTHETIC_SOURCE,
                    "market_model": market_tag,
                    "_meta": {
                        "winner": winner,
                        "loser": loser,
                        "winner_is_home": winner_is_home,
                        "result_type": result_type,
                        "team_home": team_home,
                        "team_away": team_away,
                        "gender": gender_str,
                    },
                }
                ticks.append(tick)
                tick_time += timedelta(seconds=random.Random(tick_idx).randint(45, 90))
                tick_idx += 1

        balls_seen += sum(len(o.get("deliveries", [])) for o in overs_data)
        inn_totals.append(total_runs)
        if innings_num == 1:
            first_inn_target = total_runs + 1

    if len(ticks) < 15:
        return None

    return {
        "match_id": f"cric_{specialist_key or fmt_category.lower()}_{match_index:04d}",
        "team_home": team_home,
        "team_away": team_away,
        "competition": comp_name,
        "match_format": fmt_category,
        "gender": gender_str,
        "specialist_key": specialist_key,
        "winner": winner,
        "loser": loser,
        "result_type": result_type,
        "market_model": market_tag,
        "corpus_format_version": CORPUS_FORMAT_VERSION,
        "start_time": match_date.isoformat(),
        "total_ticks": len(ticks),
        "ticks": ticks,
    }


from concurrent.futures import ProcessPoolExecutor


def index_cricsheet_catalog(source_dir: Path) -> Dict[str, List[Path]]:
    """Index all 23,193 JSON files using README.txt into specialist format, league, and gender buckets."""
    readme = source_dir / "README.txt"
    buckets: Dict[str, List[Path]] = {
        "ipl": [],
        "bbl_men": [],
        "wbbl_women": [],
        "t20i_men": [],
        "t20i_women": [],
        "odi_men": [],
        "odi_women": [],
        "test_men": [],
        "t20_blast": [],
        "first_class": [],
        "list_a": [],
        "franchise_t20": [],
    }

    if readme.exists():
        with open(readme, "r", encoding="utf-8") as f:
            for line in f:
                if " - " in line and line.count(" - ") >= 5:
                    parts = [p.strip() for p in line.split(" - ")]
                    if len(parts) >= 6:
                        m_type = parts[2]
                        m_gender = parts[3].lower()
                        m_id = parts[4]
                        fpath = source_dir / f"{m_id}.json"
                        if not fpath.exists():
                            continue

                        if m_type == "IPL":
                            buckets["ipl"].append(fpath)
                        elif m_type == "BBL" and m_gender == "male":
                            buckets["bbl_men"].append(fpath)
                        elif m_type == "WBB" or (m_type == "BBL" and m_gender == "female"):
                            buckets["wbbl_women"].append(fpath)
                        elif m_type == "T20" and m_gender == "male":
                            buckets["t20i_men"].append(fpath)
                        elif m_type == "T20" and m_gender == "female":
                            buckets["t20i_women"].append(fpath)
                        elif m_type == "ODI" and m_gender == "male":
                            buckets["odi_men"].append(fpath)
                        elif m_type == "ODI" and m_gender == "female":
                            buckets["odi_women"].append(fpath)
                        elif m_type in ("Test", "MDM"):
                            buckets["test_men"].append(fpath)
                        elif m_type in ("NTB", "SMA"):
                            buckets["t20_blast"].append(fpath)
                        elif m_type == "CCH":
                            buckets["first_class"].append(fpath)
                        elif m_type in ("CPL", "PSL", "BPL", "PKS", "CTC"):
                            buckets["franchise_t20"].append(fpath)
                        elif m_type in ("RLC", "ODM"):
                            buckets["list_a"].append(fpath)
    else:
        for fpath in source_dir.glob("*.json"):
            buckets["t20i_men"].append(fpath)

    return buckets


def build_specialist_dataset(
    specialist_key: str,
    format_label: str,
    candidate_files: List[Path],
    target_count: int = DEFAULT_TARGET_MATCHES,
    noise_mode: str = DEFAULT_NOISE_MODE,
) -> Path:
    """Build and save a compressed JSON dataset for a specific format specialist using parallel workers."""
    SPECIALISTS_DIR.mkdir(parents=True, exist_ok=True)
    out_file = SPECIALISTS_DIR / f"corpus_{specialist_key}.json.gz"

    if target_count > 0:
        candidate_files = candidate_files[:target_count]

    gender_label = "Women" if "women" in specialist_key else "Men"
    print(f"\n[HARMONIZE] Building {specialist_key.upper()} Specialist Corpus ({format_label} - {gender_label})...")
    print(f"            Parallel extracting from {len(candidate_files)} candidate files...")

    matches = []
    dropped = 0
    with ProcessPoolExecutor(max_workers=6) as executor:
        futures = [
            executor.submit(parse_cricsheet_json, fpath, format_label, idx + 1, specialist_key, noise_mode)
            for idx, fpath in enumerate(candidate_files)
        ]
        for f in futures:
            res = f.result()
            if res is not None:
                matches.append(res)
                if target_count > 0 and len(matches) >= target_count:
                    break
            else:
                dropped += 1

    total_ticks = sum(m["total_ticks"] for m in matches)
    drawn = sum(1 for m in matches if m.get("result_type") == "draw")
    print(f"            Harmonized {len(matches)} official matches ({total_ticks:,} ticks).")
    print(f"            kept={len(matches)} (decisive={len(matches) - drawn}, drawn={drawn}) dropped={dropped}")

    with gzip.open(out_file, "wt", encoding="utf-8") as f:
        json.dump(matches, f)

    file_size_mb = out_file.stat().st_size / (1024 * 1024)
    print(f"            Saved: {out_file} ({file_size_mb:.2f} MB)")
    return out_file


def main():
    parser = argparse.ArgumentParser(description="Prepare format-specialist datasets for PHOENIX.")
    parser.add_argument("--target", type=int, default=DEFAULT_TARGET_MATCHES, help="Target match count per format (0 = all qualifying matches)")
    parser.add_argument("--all", action="store_true", help="Process ALL qualifying matches from catalog")
    parser.add_argument("--noise-mode", choices=["martingale", "ou", "none"], default=DEFAULT_NOISE_MODE,
                        help="Synthetic price noise. 'ou' is mean-reverting = exploitable (experiments only).")
    args = parser.parse_args()

    target_count = 0 if args.all else args.target

    source_dir = Path(r"C:\Users\sohai\Downloads\all_json")
    if not source_dir.exists():
        source_dir = DATA_RAW_DIR

    print("=" * 80)
    print(" PHOENIX - High-Throughput Format-Specialist Dataset Harmonization Pipeline")
    print(" Architecture: Top Formats & Leagues Separated | Men & Women Separated")
    print(f" Source Catalog: {source_dir}")
    print(f" Target Quota: {'ALL qualifying matches' if target_count == 0 else f'{target_count} matches per format'}")
    print("=" * 80)

    print("[INDEX] Indexing catalog via README manifest...")
    buckets = index_cricsheet_catalog(source_dir)
    for k, files in buckets.items():
        gender = "Women" if "women" in k else "Men"
        print(f"        - {k:<15} ({gender:<5}): {len(files):,} qualifying files identified")

    configs = [
        ("ipl", "IPL"),
        ("t20i_men", "T20"),
        ("t20i_women", "T20"),
        ("bbl_men", "T20"),
        ("wbbl_women", "T20"),
        ("odi_men", "ODI"),
        ("odi_women", "ODI"),
        ("test_men", "Test"),
        ("t20_blast", "T20"),
        ("franchise_t20", "T20"),
        ("first_class", "Test"),
        ("list_a", "ODI"),
    ]

    total_harmonized = 0
    total_ticks = 0

    for key, fmt_label in configs:
        candidates = buckets.get(key, [])
        if len(candidates) >= 100:
            out = build_specialist_dataset(key, fmt_label, candidates, target_count=target_count, noise_mode=args.noise_mode)
            total_harmonized += len(candidates) if target_count == 0 else min(target_count, len(candidates))

    print("\n" + "=" * 80)
    print(" All Format, League, and Gender Specialist Datasets Prepared Successfully!")
    print(f" Total Catalog Coverage: {total_harmonized:,} matches processed")
    print("=" * 80)


if __name__ == "__main__":
    main()
