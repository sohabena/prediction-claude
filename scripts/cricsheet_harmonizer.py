"""
PHOENIX Official Match Harmonizer & Microstructure Synthesizer.

Harmonizes official Cricsheet match files into verified PHOENIX format:
1. Normalizes match formats to strictly: "T20", "ODI", "Test".
2. Normalizes team names across eras and abbreviations.
3. Filters for trustworthy international teams & tier-1 franchise teams with verified winners.
4. Generates realistic Betfair exchange in-play microstructure (Back/Lay ladders, 0.8-1.8% overround, spreads, volume).
5. Ingests into TimescaleDB (odds_ticks & match_training_status) and exports high-speed training cache.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from features.data_quality import EpisodeQualityGate
from shared.config import get_settings
from shared.db import get_session
from sqlalchemy import text
from shared.logging import setup_logging

logger = setup_logging("cricsheet_harmonizer")

# Result-describing keys: used only to settle bets after an episode; never an input to
# prices, probabilities or features.
SETTLEMENT_ONLY_KEYS = ("_meta", "winner", "loser", "winner_is_home")

SYNTHETIC_SOURCE = "synthetic_cricsheet"

# Canonical formats strictly as requested by user
FORMAT_T20 = "T20"
FORMAT_ODI = "ODI"
FORMAT_TEST = "Test"

# Canonical team dictionary
CANONICAL_TEAMS = {
    # Full International Members
    "India": "India",
    "Australia": "Australia",
    "England": "England",
    "South Africa": "South Africa",
    "New Zealand": "New Zealand",
    "Pakistan": "Pakistan",
    "West Indies": "West Indies",
    "Sri Lanka": "Sri Lanka",
    "Afghanistan": "Afghanistan",
    "Bangladesh": "Bangladesh",
    "Ireland": "Ireland",
    "Zimbabwe": "Zimbabwe",
    # Top Associate Members
    "Netherlands": "Netherlands",
    "Scotland": "Scotland",
    "Namibia": "Namibia",
    "Nepal": "Nepal",
    "USA": "USA",
    "United States of America": "USA",
    "UAE": "UAE",
    "United Arab Emirates": "UAE",
    "Canada": "Canada",
    "Oman": "Oman",
    "Papua New Guinea": "PNG",
    "PNG": "PNG",
    # Tier-1 IPL Franchise Teams
    "Chennai Super Kings": "Chennai Super Kings",
    "Mumbai Indians": "Mumbai Indians",
    "Royal Challengers Bangalore": "Royal Challengers Bengaluru",
    "Royal Challengers Bengaluru": "Royal Challengers Bengaluru",
    "Kolkata Knight Riders": "Kolkata Knight Riders",
    "Gujarat Titans": "Gujarat Titans",
    "Rajasthan Royals": "Rajasthan Royals",
    "Delhi Capitals": "Delhi Capitals",
    "Delhi Daredevils": "Delhi Capitals",
    "Sunrisers Hyderabad": "Sunrisers Hyderabad",
    "Deccan Chargers": "Sunrisers Hyderabad",
    "Lucknow Super Giants": "Lucknow Super Giants",
    "Punjab Kings": "Punjab Kings",
    "Kings XI Punjab": "Punjab Kings",
}


def canonicalize_team(name: str) -> Optional[str]:
    return CANONICAL_TEAMS.get(name.strip())


# Single shared definition (S13): MarketNoise / CricketWinProbModel / synthesize_exchange_ticks.
from scripts.synthetic_market import (  # noqa: E402,F401
    CORPUS_FORMAT_VERSION,
    CricketWinProbModel,
    MarketNoise,
    market_model_tag,
    synthesize_exchange_ticks,
    synthesize_volumes,
)

DEFAULT_NOISE_MODE = "martingale"


def parse_and_harmonize_match(
    filepath: Path,
    fmt_category: str,
    match_index: int,
    noise_mode: str = DEFAULT_NOISE_MODE,
) -> Optional[Dict[str, Any]]:
    """Parse a single Cricsheet JSON file into a harmonized match episode."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    info = data.get("info", {})
    teams_raw = info.get("teams", [])
    if len(teams_raw) != 2:
        return None

    home_raw, away_raw = teams_raw[0], teams_raw[1]
    team_home = canonicalize_team(home_raw)
    team_away = canonicalize_team(away_raw)

    if not team_home or not team_away or team_home == team_away:
        return None

    outcome = info.get("outcome", {})
    winner_raw = outcome.get("winner")
    is_multiday = fmt_category == FORMAT_TEST
    result_type = "win"
    if not winner_raw:
        # Drawn multi-day matches are retained (marked) so the trainer can void them.
        if is_multiday and str(outcome.get("result", "")).lower() == "draw":
            result_type = "draw"
            winner = ""
            loser = ""
        else:
            return None
    else:
        winner = canonicalize_team(winner_raw)
        if not winner:
            # If winner name is exact match to either canonical team
            if winner_raw == team_home:
                winner = team_home
            elif winner_raw == team_away:
                winner = team_away
            else:
                return None

        loser = team_away if winner == team_home else team_home

    dates = info.get("dates", [])
    date_str = dates[0] if dates else "2023-01-01"
    try:
        match_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        match_date = datetime(2023, 1, 1, tzinfo=timezone.utc)

    # Derive competition label
    event = info.get("event", {})
    comp_name = event.get("name") if isinstance(event, dict) else str(event)
    if not comp_name or comp_name == "None":
        comp_name = f"{fmt_category} International" if fmt_category != FORMAT_TEST else "Test Series"

    innings_list = data.get("innings", [])
    if not innings_list:
        return None

    ticks: List[Dict[str, Any]] = []
    tick_time = match_date + timedelta(hours=14)  # typical match start
    tick_idx = 0
    first_inn_target: Optional[int] = None
    inn_totals: List[int] = []
    noise = MarketNoise(match_index, noise_mode=noise_mode)  # type: ignore[arg-type]
    market_tag = market_model_tag(noise_mode)
    prev_p_home: Optional[float] = None
    balls_seen = 0
    max_innings = 4 if is_multiday else 2
    total_balls_est = sum(len(o.get("deliveries", [])) for i in innings_list[:max_innings] for o in i.get("overs", [])) or 1

    for inn_idx, inn_data in enumerate(innings_list[:max_innings]):
        innings_num = inn_idx + 1
        batting_raw = inn_data.get("team", "")
        batting_team = canonicalize_team(batting_raw) or (team_home if inn_idx == 0 else team_away)
        bowling_team = team_away if batting_team == team_home else team_home
        batting_is_home = (batting_team == team_home)

        overs_data = inn_data.get("overs", [])
        total_runs = 0
        total_wickets = 0
        if innings_num == 4:
            inn_target: Optional[int] = (
                max(1, inn_totals[0] + inn_totals[2] - inn_totals[1] + 1) if len(inn_totals) >= 3 else None
            )
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

                # Sampling rule: capture over transitions (ball 1 & 6) and key events (wickets, 4s, 6s)
                is_boundary = d_runs >= 4
                is_wicket = "wickets" in delivery
                is_over_end = (b_idx == len(deliveries) - 1)
                is_over_start = (b_idx == 0)

                if not (is_boundary or is_wicket or is_over_end or is_over_start):
                    continue

                ball_str = f"{over_num}.{b_idx + 1}"
                overs_float = round(over_num + (b_idx + 1) / 10.0, 1)

                balls_bowled = over_num * 6 + (b_idx + 1)
                max_balls = 120 if fmt_category == FORMAT_T20 else (300 if fmt_category == FORMAT_ODI else 600)
                balls_rem = max(0, max_balls - balls_bowled)

                run_rate = round((total_runs / max(1, balls_bowled)) * 6.0, 2)
                req_run_rate = 0.0
                if inn_target is not None:
                    runs_left = max(0, inn_target - total_runs)
                    req_run_rate = round((runs_left / max(1, balls_rem)) * 6.0, 2)

                # Compute dynamic win probability
                p_home = CricketWinProbModel.calculate_win_prob(
                    fmt=fmt_category,
                    innings=innings_num,
                    overs=overs_float,
                    score=total_runs,
                    wickets=total_wickets,
                    target=inn_target,
                    batting_is_home=batting_is_home,
                    noise_logit=noise.step(),
                )

                # Synthesize exchange microstructure
                tick_idx += 1
                seed = match_index * 10000 + tick_idx
                bh, lh, ba, la, ovr = synthesize_exchange_ticks(p_home, seed)

                # Realistic traded volumes ($10k - $80k)
                vol_base = 25000.0
                v_bh, v_lh, v_ba, v_la = synthesize_volumes(
                    vol_base, prev_p_home, p_home, (balls_seen + b_idx + 1) / total_balls_est
                )
                prev_p_home = p_home

                tick_time += timedelta(seconds=random.Random(seed).randint(15, 45))

                tick = {
                    "time": tick_time.isoformat(),
                    "match_id": f"cric_{fmt_category.lower()}_{match_index:04d}",
                    "team_home": team_home,
                    "team_away": team_away,
                    "competition": comp_name,
                    "match_format": fmt_category,
                    "back_home": bh,
                    "lay_home": lh,
                    "back_away": ba,
                    "lay_away": la,
                    "implied_prob_home": round(p_home, 4),
                    "implied_prob_away": round(1.0 - p_home, 4),
                    "overround": ovr,
                    "is_live": True,
                    "score": total_runs,
                    "wickets": total_wickets,
                    "overs": overs_float,
                    "run_rate": run_rate,
                    "req_run_rate": req_run_rate,
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
                }
                ticks.append(tick)

        balls_seen += sum(len(o.get("deliveries", [])) for o in overs_data)
        inn_totals.append(total_runs)
        if innings_num == 1:
            first_inn_target = total_runs + 1

    if len(ticks) < 30:
        return None

    # Attach outcome metadata to first tick
    ticks[0]["_meta"] = {
        "winner": winner,
        "loser": loser,
        "team_home": team_home,
        "team_away": team_away,
        "result_type": result_type,
        "has_real_outcome": result_type == "win",
    }

    match_id = f"cric_{fmt_category.lower()}_{match_index:04d}"
    return {
        "match_id": match_id,
        "team_home": team_home,
        "team_away": team_away,
        "competition": comp_name,
        "match_format": fmt_category,
        "winner": winner,
        "loser": loser,
        "result_type": result_type,
        "market_model": market_tag,
        "corpus_format_version": CORPUS_FORMAT_VERSION,
        "start_time": match_date.isoformat(),
        "total_ticks": len(ticks),
        "ticks": ticks,
    }


async def insert_into_database(episodes: List[Dict[str, Any]], clear_existing: bool = True) -> None:
    """Insert harmonized episodes into TimescaleDB."""
    settings = get_settings()
    print(f"\n[DATABASE] Ingesting {len(episodes)} harmonized matches into TimescaleDB...")

    async with get_session() as session:
        if clear_existing:
            # Clear historical ticks to guarantee fresh, pristine official data
            print("           Clearing existing historical odds_ticks & match_training_status...")
            await session.execute(text("DELETE FROM odds_ticks WHERE source IN ('cricsheet_official', 'synthetic_cricsheet') OR match_id LIKE 'cric_%'"))
            await session.execute(text("DELETE FROM match_training_status WHERE match_id LIKE 'cric_%'"))
            await session.commit()

        # Batch insert match_training_status
        print(f"           Inserting {len(episodes)} records into match_training_status...")
        for ep in episodes:
            await session.execute(
                text("""
                    INSERT INTO match_training_status (
                        match_id, team_home, team_away, competition, training_status,
                        scrape_status, auto_approved, created_at, quality_score, completeness
                    ) VALUES (
                        :match_id, :team_home, :team_away, :competition, 'approved',
                        'scrape_approved', true, NOW(), 1.0, 1.0
                    ) ON CONFLICT (match_id) DO UPDATE SET training_status = 'approved', scrape_status = 'scrape_approved'
                """),
                {
                    "match_id": ep["match_id"],
                    "team_home": ep["team_home"],
                    "team_away": ep["team_away"],
                    "competition": ep["competition"],
                },
            )
        await session.commit()

        # Batch insert odds_ticks
        print(f"           Inserting odds_ticks across {len(episodes)} matches...")
        tick_count = 0
        batch_size = 500
        current_batch = []

        for ep in episodes:
            for t in ep["ticks"]:
                current_batch.append({
                    "time": datetime.fromisoformat(t["time"]),
                    "match_id": t["match_id"],
                    "team_home": t["team_home"],
                    "team_away": t["team_away"],
                    "competition": t["competition"],
                    "back_home": t["back_home"],
                    "lay_home": t["lay_home"],
                    "back_away": t["back_away"],
                    "lay_away": t["lay_away"],
                    "implied_prob_home": t["implied_prob_home"],
                    "implied_prob_away": t["implied_prob_away"],
                    "overround": t["overround"],
                    "is_live": t["is_live"],
                    "source": t["source"],
                    "volume_back_home": t["volume_back_home"],
                    "volume_lay_home": t["volume_lay_home"],
                    "volume_back_away": t["volume_back_away"],
                    "volume_lay_away": t["volume_lay_away"],
                    "score": t["score"],
                    "wickets": t["wickets"],
                    "overs": t["overs"],
                    "innings": t["innings"],
                })
                tick_count += 1
                if len(current_batch) >= batch_size:
                    await session.execute(
                        text("""
                            INSERT INTO odds_ticks (
                                time, match_id, team_home, team_away, competition,
                                back_home, lay_home, back_away, lay_away,
                                implied_prob_home, implied_prob_away, overround,
                                is_live, source, volume_back_home, volume_lay_home,
                                volume_back_away, volume_lay_away, score, wickets, overs, innings
                            ) VALUES (
                                :time, :match_id, :team_home, :team_away, :competition,
                                :back_home, :lay_home, :back_away, :lay_away,
                                :implied_prob_home, :implied_prob_away, :overround,
                                :is_live, :source, :volume_back_home, :volume_lay_home,
                                :volume_back_away, :volume_lay_away, :score, :wickets, :overs, :innings
                            )
                        """),
                        current_batch,
                    )
                    await session.commit()
                    current_batch = []

        if current_batch:
            await session.execute(
                text("""
                    INSERT INTO odds_ticks (
                        time, match_id, team_home, team_away, competition,
                        back_home, lay_home, back_away, lay_away,
                        implied_prob_home, implied_prob_away, overround,
                        is_live, source, volume_back_home, volume_lay_home,
                        volume_back_away, volume_lay_away, score, wickets, overs, innings
                    ) VALUES (
                        :time, :match_id, :team_home, :team_away, :competition,
                        :back_home, :lay_home, :back_away, :lay_away,
                        :implied_prob_home, :implied_prob_away, :overround,
                        :is_live, :source, :volume_back_home, :volume_lay_home,
                        :volume_back_away, :volume_lay_away, :score, :wickets, :overs, :innings
                    )
                """),
                current_batch,
            )
            await session.commit()

        print(f"           Successfully ingested {tick_count} odds_ticks into database!")


def build_harmonized_dataset(
    target_matches: int = 1000,
    export_cache: bool = True,
) -> List[Dict[str, Any]]:
    """Harmonizes matches across T20, ODI, and Test archives."""
    print("=" * 65)
    print(f" PHOENIX - Harmonizing Official Cricket Corpus (Target: {target_matches} matches)")
    print("=" * 65)

    sources = [
        ("T20", Path("data/raw/t20s"), int(target_matches * 0.40)),
        ("T20", Path("data/raw/ipl"), int(target_matches * 0.20)),
        ("ODI", Path("data/raw/odis"), int(target_matches * 0.25)),
        ("Test", Path("data/raw/tests"), int(target_matches * 0.15)),
    ]

    all_episodes: List[Dict[str, Any]] = []
    global_idx = 1
    gate = EpisodeQualityGate()

    for fmt, folder, quota in sources:
        print(f"\n[HARMONIZE] Processing {fmt} matches from {folder} (Quota: {quota})...")
        files = sorted(list(folder.glob("*.json")))
        random.Random(42).shuffle(files)
        
        admitted = 0
        dropped = 0
        drawn = 0
        for f in files:
            if admitted >= quota:
                break
            ep = parse_and_harmonize_match(f, fmt, global_idx)
            if not ep:
                dropped += 1
                continue

            # Quality gate validation
            report = gate.evaluate(ep["ticks"])
            if not report.passed:
                dropped += 1
                continue

            all_episodes.append(ep)
            global_idx += 1
            admitted += 1
            if ep.get("result_type") == "draw":
                drawn += 1

        print(f"            Admitted: {admitted} verified {fmt} matches (drawn={drawn}, dropped={dropped}).")

    print("\n" + "=" * 65)
    print(f" Harmonization Complete! Total Verified Matches: {len(all_episodes)}")
    print(f" Total In-Play Ticks Generated: {sum(e['total_ticks'] for e in all_episodes):,}")
    print("=" * 65)

    if export_cache:
        cache_path = Path("data/harmonized_corpus.json.gz")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"\n[CACHE] Exporting high-speed training cache to {cache_path}...")
        with gzip.open(cache_path, "wt", encoding="utf-8") as f:
            json.dump(all_episodes, f)
        print(f"        Saved {cache_path} ({cache_path.stat().st_size / (1024*1024):.2f} MB)")

    return all_episodes


async def main():
    parser = argparse.ArgumentParser(description="Harmonize Cricsheet matches for PHOENIX.")
    parser.add_argument("--matches", type=int, default=1000, help="Number of matches to harmonize (default: 1000)")
    parser.add_argument("--no-db", action="store_true", help="Skip database insertion")
    args = parser.parse_args()

    episodes = build_harmonized_dataset(target_matches=args.matches)
    
    if not args.no_db:
        await insert_into_database(episodes)
        
    print("\n[DONE] Harmonization and ingestion finished.")


if __name__ == "__main__":
    asyncio.run(main())
