"""
Realistic Historical Cricket Dataset Generator for PHOENIX.

Generates an expanded, high-fidelity cricket dataset with:
1. 100 diverse matches across 5 distinct cricket archetypes:
   - COMFORTABLE_CHASE (favorite steadily chases target)
   - SUDDEN_COLLAPSE (unexpected cluster of wickets causing violent odds flip)
   - THRILLER_FINISH (nail-biter with extreme death-overs odds oscillation)
   - LOW_SCORING_DEFENSE (disciplined bowling defending low total)
   - BLOWOUT (early dominance leading to one-sided finish)
2. Full in-play cricket context on every tick:
   - score, wickets, overs, run_rate, req_run_rate, balls_remaining, innings
3. Realistic Betfair exchange microstructure:
   - Tight back/lay spreads (1-3 ticks)
   - Volume depth across back and lay ladders
   - Non-zero overrounds (0.5% - 2.0%)
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional


class MatchArchetype(str, Enum):
    COMFORTABLE_CHASE = "comfortable_chase"
    SUDDEN_COLLAPSE = "sudden_collapse"
    THRILLER_FINISH = "thriller_finish"
    LOW_SCORING_DEFENSE = "low_scoring_defense"
    BLOWOUT = "blowout"


@dataclass
class SimulatedCricketMatch:
    match_id: str
    team_home: str
    team_away: str
    competition: str
    archetype: MatchArchetype
    start_time: datetime
    completed_at: datetime
    winner: str
    loser: str
    ticks: list[dict[str, Any]]


class RealisticCorpusGenerator:
    """Generates varied, high-fidelity cricket match episodes with full context."""

    TEAMS_INTERNATIONAL = [
        "India", "Australia", "England", "South Africa", "New Zealand",
        "Pakistan", "West Indies", "Sri Lanka", "Afghanistan", "Bangladesh"
    ]

    TEAMS_FRANCHISE = [
        ("Mumbai Indians", "Chennai Super Kings", "IPL"),
        ("Royal Challengers Bengaluru", "Kolkata Knight Riders", "IPL"),
        ("Gujarat Titans", "Rajasthan Royals", "IPL"),
        ("Sunrisers Hyderabad", "Delhi Capitals", "IPL"),
        ("Perth Scorchers", "Sydney Sixers", "BBL"),
        ("Melbourne Stars", "Adelaide Strikers", "BBL"),
        ("Lahore Qalandars", "Karachi Kings", "PSL"),
        ("Islamabad United", "Peshawar Zalmi", "PSL"),
    ]

    def __init__(self, seed: int = 42) -> None:
        self.rng = random.Random(seed)

    def generate_corpus(self, match_count: int = 100) -> list[SimulatedCricketMatch]:
        matches: list[SimulatedCricketMatch] = []
        now = datetime.now(timezone.utc)

        archetypes = list(MatchArchetype)

        for i in range(match_count):
            archetype = archetypes[i % len(archetypes)]

            # Select teams
            if i % 2 == 0:
                h_idx, a_idx = self.rng.sample(range(len(self.TEAMS_INTERNATIONAL)), 2)
                team_h = self.TEAMS_INTERNATIONAL[h_idx]
                team_a = self.TEAMS_INTERNATIONAL[a_idx]
                comp = "ICC T20 World Cup" if i % 4 == 0 else "T20 International"
            else:
                franchise_pair = self.rng.choice(self.TEAMS_FRANCHISE)
                team_h, team_a, comp = franchise_pair

            match_id = f"bf_corpus_{team_h[:3].lower()}_{team_a[:3].lower()}_{i+1:03d}"
            start_time = now - timedelta(days=match_count - i, hours=self.rng.randint(1, 12))

            match = self._generate_single_match(
                match_id=match_id,
                team_home=team_h,
                team_away=team_a,
                competition=comp,
                archetype=archetype,
                start_time=start_time,
            )
            matches.append(match)

        return matches

    def _generate_single_match(
        self,
        match_id: str,
        team_home: str,
        team_away: str,
        competition: str,
        archetype: MatchArchetype,
        start_time: datetime,
    ) -> SimulatedCricketMatch:
        # Determine total ticks (120 to 200 ticks per match)
        ticks_count = self.rng.randint(150, 220)
        total_overs = 20.0  # T20 match

        # Home team pre-match win probability
        p_home_init = self.rng.uniform(0.42, 0.58)

        # Target score for 2nd innings
        target_score = self.rng.randint(145, 195)
        current_score = 0
        current_wickets = 0

        p_home = p_home_init
        ticks: list[dict[str, Any]] = []
        curr_time = start_time

        # Pre-determine winner based on archetype
        if archetype == MatchArchetype.COMFORTABLE_CHASE:
            home_wins = self.rng.random() < 0.70
        elif archetype == MatchArchetype.SUDDEN_COLLAPSE:
            home_wins = False if self.rng.random() < 0.80 else True
        elif archetype == MatchArchetype.THRILLER_FINISH:
            home_wins = self.rng.random() < 0.50
        elif archetype == MatchArchetype.LOW_SCORING_DEFENSE:
            target_score = self.rng.randint(125, 145)
            home_wins = self.rng.random() < 0.60
        else:  # BLOWOUT
            home_wins = self.rng.random() < 0.85

        for step in range(ticks_count):
            progress = (step + 1) / float(ticks_count)
            overs = round(progress * total_overs, 1)
            balls_bowled = int(progress * 120)
            balls_remaining = 120 - balls_bowled

            # Update match context score and wickets based on archetype
            if archetype == MatchArchetype.COMFORTABLE_CHASE:
                # Steady scoring, few wickets
                current_score = int(progress * (target_score + 4 if home_wins else target_score - 10))
                current_wickets = min(9, int(progress * 4) + (1 if self.rng.random() < 0.3 else 0))
                # Probability drifts smoothly
                target_p = 0.98 if home_wins else 0.02
                p_home += (target_p - p_home) * (progress ** 1.5) * 0.12 + self.rng.gauss(0, 0.02)

            elif archetype == MatchArchetype.SUDDEN_COLLAPSE:
                # Favorite starts strong, then sudden collapse at progress 0.65 - 0.80
                if progress < 0.65:
                    current_score = int(progress * target_score * 1.1)
                    current_wickets = min(2, int(progress * 2))
                    p_home = 0.85 + self.rng.gauss(0, 0.02)
                elif 0.65 <= progress < 0.85:
                    # Rapid collapse: 4 wickets fall
                    current_score += self.rng.randint(0, 3)
                    current_wickets = min(8, current_wickets + self.rng.choice([1, 1, 2]))
                    p_home -= 0.18 + self.rng.gauss(0, 0.04)  # Sudden odds crash!
                else:
                    current_score += self.rng.randint(2, 6)
                    target_p = 0.98 if home_wins else 0.02
                    p_home += (target_p - p_home) * 0.25

            elif archetype == MatchArchetype.THRILLER_FINISH:
                # Close run-rate, high oscillation throughout
                current_score = int(progress * (target_score - 5 + self.rng.randint(-4, 4)))
                current_wickets = min(8, int(progress * 6))
                # Odds oscillate around 0.50 until very end
                if progress < 0.85:
                    p_home += self.rng.gauss(0, 0.06)
                else:
                    target_p = 0.98 if home_wins else 0.02
                    p_home += (target_p - p_home) * 0.35 + self.rng.gauss(0, 0.08)

            elif archetype == MatchArchetype.LOW_SCORING_DEFENSE:
                # Low boundaries, high dot balls, regular wickets
                current_score = int(progress * (target_score - 8 if not home_wins else target_score + 2))
                current_wickets = min(9, int(progress * 8))
                target_p = 0.98 if home_wins else 0.02
                p_home += (target_p - p_home) * (progress ** 1.3) * 0.10 + self.rng.gauss(0, 0.025)

            else:  # BLOWOUT
                # Immediate divergence
                current_score = int(progress * target_score * (1.3 if home_wins else 0.6))
                current_wickets = min(9, int(progress * (3 if home_wins else 9)))
                target_p = 0.99 if home_wins else 0.01
                p_home += (target_p - p_home) * 0.18 + self.rng.gauss(0, 0.015)

            # Bound probability
            p_home = max(0.01, min(0.99, p_home))
            p_away = 1.0 - p_home

            # Run rates
            crr = round((current_score / max(overs, 0.1)), 2)
            rrr = round(((target_score - current_score) / max(balls_remaining / 6.0, 0.1)), 2) if balls_remaining > 0 else 0.0

            # Derive prices with 1.2% exchange margin
            back_h = max(1.02, min(50.0, round(1.0 / (p_home * 1.012), 2)))
            lay_h = max(round(back_h * 1.01, 2), round(back_h + self.rng.choice([0.01, 0.02, 0.03]), 2))

            back_a = max(1.02, min(50.0, round(1.0 / (p_away * 1.012), 2)))
            lay_a = max(round(back_a * 1.01, 2), round(back_a + self.rng.choice([0.01, 0.02, 0.03]), 2))

            ip_h = round(1.0 / back_h, 4)
            ip_a = round(1.0 / back_a, 4)
            overround = round(ip_h + ip_a - 1.0, 4)

            vol_h = round(self.rng.uniform(1500, 25000), 0)
            vol_a = round(self.rng.uniform(1500, 25000), 0)

            ticks.append({
                "match_id": match_id,
                "team_home": team_home,
                "team_away": team_away,
                "competition": competition,
                "timestamp": curr_time,
                "back_home": back_h,
                "lay_home": lay_h,
                "back_away": back_a,
                "lay_away": lay_a,
                "back_draw": None,
                "lay_draw": None,
                "volume_back_home": vol_h,
                "volume_lay_home": vol_h * 1.1,
                "volume_back_away": vol_a,
                "volume_lay_away": vol_a * 1.1,
                "implied_prob_home": ip_h,
                "implied_prob_away": ip_a,
                "overround": overround,
                "is_live": True,
                # FULL CRICKET CONTEXT
                "score": current_score,
                "wickets": current_wickets,
                "overs": overs,
                "run_rate": crr,
                "req_run_rate": rrr,
                "innings": 2,
                "balls_remaining": balls_remaining,
            })

            curr_time += timedelta(seconds=self.rng.randint(3, 5))

        winner = team_home if home_wins else team_away
        loser = team_away if home_wins else team_home

        return SimulatedCricketMatch(
            match_id=match_id,
            team_home=team_home,
            team_away=team_away,
            competition=competition,
            archetype=archetype,
            start_time=start_time,
            completed_at=curr_time,
            winner=winner,
            loser=loser,
            ticks=ticks,
        )

