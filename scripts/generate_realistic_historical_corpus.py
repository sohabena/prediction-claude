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

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional


SYNTHETIC_SOURCE = "synthetic_archetype"


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

    @staticmethod
    def _state_home_win_prob(score: int, wickets: int, balls_remaining: int, target: int) -> float:
        """P(home wins) for the home side chasing `target`; a pure function of match state."""
        runs_needed = target - score
        wickets_left = max(0, 10 - wickets)
        if runs_needed <= 0:
            return 0.99
        if balls_remaining <= 0 or wickets_left <= 0:
            return 0.01
        req_rr = (runs_needed / max(1, balls_remaining)) * 6.0
        pressure = req_rr / 8.6
        z = 2.2 * (wickets_left / 10.0) ** 1.1 - 1.4 * pressure
        z = max(-50.0, min(50.0, z))
        return 1.0 / (1.0 + math.exp(-z))

    @staticmethod
    def _decide_winner(
        final_score: int, target: int, team_home: str, team_away: str, tiebreak_rng: random.Random
    ) -> str:
        """Winner is a consequence of the terminal state (chase succeeded or not).

        A tied score (final == target - 1) goes to a super over decided by a *separate* rng so
        the main path stream is never consumed by the result.
        """
        if final_score >= target:
            return team_home
        if final_score == target - 1:
            return team_home if tiebreak_rng.random() < 0.5 else team_away
        return team_away

    def _generate_single_match(
        self,
        match_id: str,
        team_home: str,
        team_away: str,
        competition: str,
        archetype: MatchArchetype,
        start_time: datetime,
    ) -> SimulatedCricketMatch:
        """Generate the score path first, price it from state only, then derive the winner.

        Archetype parameters shape the PATH only (final score ratio, score/wicket curves).
        Prices = state-only chase model + outcome-independent log-odds noise.
        """
        rng = self.rng
        tiebreak_rng = random.Random(f"{match_id}:tiebreak")
        ticks_count = rng.randint(150, 220)
        total_overs = 20.0

        target = rng.randint(145, 195)
        if archetype == MatchArchetype.COMFORTABLE_CHASE:
            ratio = rng.gauss(1.06, 0.07)
            score_exp, wkt_exp = 1.0, 1.2
        elif archetype == MatchArchetype.SUDDEN_COLLAPSE:
            ratio = rng.gauss(0.86, 0.10)
            score_exp, wkt_exp = 0.7, 2.5
        elif archetype == MatchArchetype.THRILLER_FINISH:
            ratio = rng.uniform(0.94, 1.05)
            score_exp, wkt_exp = 1.0, 1.0
        elif archetype == MatchArchetype.LOW_SCORING_DEFENSE:
            target = rng.randint(125, 145)
            ratio = rng.gauss(0.92, 0.08)
            score_exp, wkt_exp = 1.1, 0.9
        else:  # BLOWOUT: either the chase is routine or it collapses early
            ratio = rng.gauss(1.25, 0.10) if rng.random() < 0.6 else rng.gauss(0.60, 0.08)
            score_exp, wkt_exp = 0.9, 1.0

        final_score = max(20, int(round(ratio * target)))
        chase_succeeds = final_score >= target
        if chase_succeeds:
            end_progress = rng.uniform(0.80, 1.0)
            final_wickets = rng.randint(2, 7)
        else:
            end_progress = 1.0 if rng.random() < 0.6 else rng.uniform(0.7, 1.0)
            final_wickets = 10 if end_progress < 1.0 else rng.choice([6, 7, 8, 9, 10])

        noise_rng = random.Random(f"{match_id}:noise")
        noise_x = 0.0

        ticks: list[dict[str, Any]] = []
        curr_time = start_time
        prev_score = 0
        prev_wickets = 0

        for step in range(ticks_count):
            frac = (step + 1) / float(ticks_count)
            progress = frac * end_progress
            overs = round(progress * total_overs, 1)
            balls_bowled = int(progress * 120)
            balls_remaining = 120 - balls_bowled

            score = int(final_score * (frac ** score_exp) + rng.gauss(0, 1.5))
            score = max(prev_score, min(score, final_score))
            if chase_succeeds and step < ticks_count - 1:
                score = min(score, target - 1)
            if step == ticks_count - 1:
                score = final_score
            wickets = int(round(final_wickets * (frac ** wkt_exp)))
            wickets = max(prev_wickets, min(10, wickets))
            prev_score, prev_wickets = score, wickets

            # Outcome-independent market noise: mean-reverting walk in log-odds space
            # (theta=0.10, sigma=0.08 per tick).
            noise_x = 0.9 * noise_x + noise_rng.gauss(0.0, 0.08)
            p_state = self._state_home_win_prob(score, wickets, balls_remaining, target)
            p_c = max(1e-4, min(1.0 - 1e-4, p_state))
            p_home = 1.0 / (1.0 + math.exp(-(math.log(p_c / (1.0 - p_c)) + noise_x)))
            p_home = max(0.01, min(0.99, p_home))
            p_away = 1.0 - p_home

            crr = round((score / max(overs, 0.1)), 2)
            rrr = round(((target - score) / max(balls_remaining / 6.0, 0.1)), 2) if balls_remaining > 0 else 0.0

            back_h = max(1.02, min(50.0, round(1.0 / (p_home * 1.012), 2)))
            lay_h = max(round(back_h * 1.01, 2), round(back_h + rng.choice([0.01, 0.02, 0.03]), 2))

            back_a = max(1.02, min(50.0, round(1.0 / (p_away * 1.012), 2)))
            lay_a = max(round(back_a * 1.01, 2), round(back_a + rng.choice([0.01, 0.02, 0.03]), 2))

            ip_h = round(1.0 / back_h, 4)
            ip_a = round(1.0 / back_a, 4)
            overround = round(ip_h + ip_a - 1.0, 4)

            vol_h = round(rng.uniform(1500, 25000), 0)
            vol_a = round(rng.uniform(1500, 25000), 0)

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
                "score": score,
                "wickets": wickets,
                "overs": overs,
                "run_rate": crr,
                "req_run_rate": rrr,
                "innings": 2,
                "balls_remaining": balls_remaining,
                "source": SYNTHETIC_SOURCE,
            })

            curr_time += timedelta(seconds=rng.randint(3, 5))

        # Winner is derived from the terminal state AFTER the whole path has been priced.
        winner = self._decide_winner(prev_score, target, team_home, team_away, tiebreak_rng)
        loser = team_away if winner == team_home else team_home

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
