"""
Data Quality Gate for PHOENIX.

Validates data at two levels:
  1. Tick-level  -- individual odds ticks (used by scraper before storage)
  2. Episode-level -- full match episodes (used by data loader before training)

Each check returns structured issues so the dashboard can surface them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from shared.constants import VALID_ODDS_MAX, VALID_ODDS_MIN
from shared.logging import setup_logging

logger = setup_logging("data_quality")


# ============================================================
# Quality Report Structures
# ============================================================


@dataclass
class TickIssue:
    """A single quality problem found in one tick."""

    severity: str  # "warn" | "error"
    code: str  # machine-readable code
    message: str  # human-readable description


@dataclass
class EpisodeQualityReport:
    """Full quality assessment for a training episode."""

    match_id: str
    total_ticks: int
    valid_ticks: int
    quality_score: float  # 0.0 – 1.0
    passed: bool
    issues: list[str] = field(default_factory=list)

    # Breakdown
    completeness: float = 0.0  # % of ticks with both back prices
    odds_jump_count: int = 0  # number of suspicious jumps
    duplicate_count: int = 0  # number of duplicate timestamps
    missing_odds_pct: float = 0.0  # % of ticks with zero odds
    overround_violations: int = 0  # ticks with impossible overround
    context_violations: int = 0  # ticks with invalid cricket stats


# ============================================================
# Thresholds
# ============================================================

# Tick-level
ODDS_JUMP_THRESHOLD = 0.50  # >50% relative change between consecutive ticks
OVERROUND_MIN = -0.05  # Overround should be ≥ ~0 (small negative from rounding ok)
OVERROUND_MAX = 0.50  # Overround > 50% is almost certainly a scraper error

# Episode-level
MIN_COMPLETENESS = 0.40  # At least 40% of ticks must have both back prices
MIN_QUALITY_SCORE = 0.50  # Episodes below this are rejected
MAX_DUPLICATE_PCT = 0.30  # More than 30% duplicate timestamps → reject
MAX_ODDS_JUMP_PCT = 0.20  # More than 20% ticks with suspicious jumps → reject

# Context-level (cricket rules)
MAX_WICKETS = 10
MAX_OVERS_T20 = 20.0
MAX_OVERS_ODI = 50.0
MAX_OVERS_TEST = 450.0  # 5 days × 90 overs
MAX_RUN_RATE = 36.0  # 6 runs per ball = 36 per over (theoretical max)
MAX_INNINGS = 4  # Test cricket max


# ============================================================
# Tick-Level Validator
# ============================================================


class TickValidator:
    """Validates individual odds ticks before storage.

    Maintains per-match state to detect cross-tick anomalies like
    odds jumps and duplicate timestamps.
    """

    def __init__(self) -> None:
        # Per-match previous tick state
        self._prev_ticks: dict[str, dict[str, Any]] = {}

    def validate(
        self, match_id: str, tick: dict[str, Any]
    ) -> tuple[bool, list[TickIssue]]:
        """Validate a single tick.

        Returns:
            (is_valid, issues) -- is_valid is True if no errors (warnings ok).
        """
        issues: list[TickIssue] = []

        # --- 1. Missing odds ---
        back_home = tick.get("back_home")
        back_away = tick.get("back_away")
        if back_home is None and back_away is None:
            issues.append(TickIssue("error", "NO_ODDS", "Both back prices are None"))

        # --- 2. Odds range ---
        for key in ("back_home", "lay_home", "back_away", "lay_away", "back_draw", "lay_draw"):
            val = tick.get(key)
            if val is not None:
                if not (VALID_ODDS_MIN <= val <= VALID_ODDS_MAX):
                    issues.append(
                        TickIssue("error", "ODDS_RANGE", f"{key}={val} outside [{VALID_ODDS_MIN}, {VALID_ODDS_MAX}]")
                    )

        # --- 3. Spread consistency ---
        for side in ("home", "away", "draw"):
            back = tick.get(f"back_{side}")
            lay = tick.get(f"lay_{side}")
            if back is not None and lay is not None and back > lay:
                issues.append(
                    TickIssue("error", "SPREAD_INVERTED", f"back_{side}={back} > lay_{side}={lay}")
                )

        # --- 4. Overround sanity ---
        overround = tick.get("overround")
        if overround is not None:
            if overround < OVERROUND_MIN:
                issues.append(
                    TickIssue("warn", "OVERROUND_LOW", f"overround={overround:.4f} < {OVERROUND_MIN}")
                )
            elif overround > OVERROUND_MAX:
                issues.append(
                    TickIssue("warn", "OVERROUND_HIGH", f"overround={overround:.4f} > {OVERROUND_MAX}")
                )

        # --- 5. Odds jump detection (vs previous tick) ---
        prev = self._prev_ticks.get(match_id)
        if prev is not None:
            for key in ("back_home", "back_away"):
                curr_val = tick.get(key)
                prev_val = prev.get(key)
                if curr_val and prev_val and prev_val > 0:
                    pct_change = abs(curr_val - prev_val) / prev_val
                    if pct_change > ODDS_JUMP_THRESHOLD:
                        issues.append(
                            TickIssue(
                                "warn",
                                "ODDS_JUMP",
                                f"{key} jumped {pct_change:.0%} ({prev_val:.2f} -> {curr_val:.2f})",
                            )
                        )

        # --- 6. Duplicate timestamp ---
        ts = tick.get("timestamp") or tick.get("time")
        prev_ts = prev.get("timestamp") or prev.get("time") if prev else None
        if ts and prev_ts and ts == prev_ts:
            issues.append(
                TickIssue("warn", "DUPLICATE_TS", f"Same timestamp as previous tick: {ts}")
            )

        # --- 7. Match context sanity ---
        wickets = tick.get("wickets")
        if wickets is not None and (wickets < 0 or wickets > MAX_WICKETS):
            issues.append(
                TickIssue("warn", "INVALID_WICKETS", f"wickets={wickets} (valid: 0-{MAX_WICKETS})")
            )

        overs = tick.get("overs")
        if overs is not None and overs < 0:
            issues.append(
                TickIssue("warn", "NEGATIVE_OVERS", f"overs={overs}")
            )

        run_rate = tick.get("run_rate")
        if run_rate is not None and run_rate > MAX_RUN_RATE:
            issues.append(
                TickIssue("warn", "EXTREME_RUN_RATE", f"run_rate={run_rate} > {MAX_RUN_RATE}")
            )

        innings = tick.get("innings")
        if innings is not None and (innings < 1 or innings > MAX_INNINGS):
            issues.append(
                TickIssue("warn", "INVALID_INNINGS", f"innings={innings} (valid: 1-{MAX_INNINGS})")
            )

        score = tick.get("score")
        if score is not None and score < 0:
            issues.append(
                TickIssue("warn", "NEGATIVE_SCORE", f"score={score}")
            )

        # --- 8. Volume validation ---
        volume_fields = ("volume_back_home", "volume_lay_home", "volume_back_away", "volume_lay_away")
        has_any_volume = False
        for vol_key in volume_fields:
            vol = tick.get(vol_key)
            if vol is not None:
                has_any_volume = True
                if vol < 0:
                    issues.append(
                        TickIssue("warn", "NEGATIVE_VOLUME", f"{vol_key}={vol}")
                    )

        # Warn if no volume data (important for market depth signals)
        if not has_any_volume and (back_home is not None or back_away is not None):
            issues.append(
                TickIssue("warn", "NO_VOLUME_DATA", "Tick has odds but no volume data")
            )

        # Update state
        self._prev_ticks[match_id] = tick

        has_error = any(i.severity == "error" for i in issues)
        return (not has_error, issues)

    def clear_match(self, match_id: str) -> None:
        """Clear state for a finished match."""
        self._prev_ticks.pop(match_id, None)


# ============================================================
# Episode-Level Quality Gate
# ============================================================


class EpisodeQualityGate:
    """Evaluates entire episodes (match histories) before RL training.

    Scores each episode on completeness, consistency, and validity.
    Episodes below ``min_quality`` are rejected.
    """

    def __init__(self, min_quality: float = MIN_QUALITY_SCORE) -> None:
        self.min_quality = min_quality

    def evaluate(self, episode: list[dict[str, Any]]) -> EpisodeQualityReport:
        """Score an episode's quality.

        Args:
            episode: List of tick dicts (time-sorted) for one match.

        Returns:
            EpisodeQualityReport with score and detailed breakdown.
        """
        match_id = episode[0].get("match_id", "unknown") if episode else "empty"
        total = len(episode)

        if total == 0:
            return EpisodeQualityReport(
                match_id=match_id,
                total_ticks=0,
                valid_ticks=0,
                quality_score=0.0,
                passed=False,
                issues=["Empty episode"],
            )

        issues: list[str] = []

        # --- 1. Completeness: % of ticks with both back prices ---
        complete_count = sum(
            1 for t in episode
            if t.get("back_home") is not None and t.get("back_away") is not None
        )
        completeness = complete_count / total

        # --- 2. Missing odds: % with neither back price ---
        missing_count = sum(
            1 for t in episode
            if t.get("back_home") is None and t.get("back_away") is None
        )
        missing_odds_pct = missing_count / total

        # --- 3. Duplicate timestamps ---
        timestamps = [t.get("timestamp") or t.get("time") for t in episode]
        unique_ts = len(set(ts for ts in timestamps if ts is not None))
        ts_count = sum(1 for ts in timestamps if ts is not None)
        duplicate_count = max(0, ts_count - unique_ts)
        duplicate_pct = duplicate_count / total if total > 0 else 0

        # --- 4. Odds jumps ---
        jump_count = 0
        for i in range(1, total):
            for key in ("back_home", "back_away"):
                curr = episode[i].get(key)
                prev = episode[i - 1].get(key)
                if curr and prev and prev > 0:
                    pct = abs(curr - prev) / prev
                    if pct > ODDS_JUMP_THRESHOLD:
                        jump_count += 1
                        break  # count once per tick
        jump_pct = jump_count / total if total > 0 else 0

        # --- 5. Overround violations ---
        overround_violations = 0
        for t in episode:
            ov = t.get("overround")
            if ov is not None and (ov < OVERROUND_MIN or ov > OVERROUND_MAX):
                overround_violations += 1

        # --- 6. Context violations ---
        context_violations = 0
        for t in episode:
            w = t.get("wickets")
            if w is not None and (w < 0 or w > MAX_WICKETS):
                context_violations += 1
                continue
            o = t.get("overs")
            if o is not None and o < 0:
                context_violations += 1
                continue
            s = t.get("score")
            if s is not None and s < 0:
                context_violations += 1
                continue
            rr = t.get("run_rate")
            if rr is not None and rr > MAX_RUN_RATE:
                context_violations += 1

        # --- 7. Valid ticks (have at least one back price) ---
        valid_ticks = total - missing_count

        # --- Build quality score (weighted) ---
        # Weights: completeness matters most, then jumps, then duplicates
        score = (
            0.40 * completeness
            + 0.20 * (1.0 - min(jump_pct / MAX_ODDS_JUMP_PCT, 1.0))
            + 0.15 * (1.0 - min(duplicate_pct / MAX_DUPLICATE_PCT, 1.0))
            + 0.15 * (1.0 - min(missing_odds_pct, 1.0))
            + 0.05 * (1.0 - min(overround_violations / max(total, 1), 1.0))
            + 0.05 * (1.0 - min(context_violations / max(total, 1), 1.0))
        )
        score = max(0.0, min(1.0, score))

        # --- Collect human-readable issues ---
        if completeness < MIN_COMPLETENESS:
            issues.append(f"Low completeness: {completeness:.0%} (min {MIN_COMPLETENESS:.0%})")
        if duplicate_pct > MAX_DUPLICATE_PCT:
            issues.append(f"High duplicate rate: {duplicate_pct:.0%} ({duplicate_count} dupes)")
        if jump_pct > MAX_ODDS_JUMP_PCT:
            issues.append(f"Excessive odds jumps: {jump_pct:.0%} ({jump_count} ticks)")
        if missing_odds_pct > 0.50:
            issues.append(f"High missing odds: {missing_odds_pct:.0%}")
        if overround_violations > total * 0.10:
            issues.append(f"Overround violations: {overround_violations}/{total}")
        if context_violations > total * 0.10:
            issues.append(f"Context violations: {context_violations}/{total}")

        passed = score >= self.min_quality

        return EpisodeQualityReport(
            match_id=match_id,
            total_ticks=total,
            valid_ticks=valid_ticks,
            quality_score=round(score, 4),
            passed=passed,
            issues=issues,
            completeness=round(completeness, 4),
            odds_jump_count=jump_count,
            duplicate_count=duplicate_count,
            missing_odds_pct=round(missing_odds_pct, 4),
            overround_violations=overround_violations,
            context_violations=context_violations,
        )


# ============================================================
# Observation Validator (for the RL agent)
# ============================================================


def validate_observation(obs: "np.ndarray") -> tuple[bool, str]:
    """Check that an observation vector is safe to feed to the RL agent.

    Args:
        obs: The normalized feature vector (shape = OBSERVATION_SIZE).

    Returns:
        (is_valid, reason) -- is_valid False if observation is degenerate.
    """
    import numpy as np

    if obs is None:
        return False, "Observation is None"

    if np.any(np.isnan(obs)):
        nan_count = int(np.sum(np.isnan(obs)))
        return False, f"Observation contains {nan_count} NaN values"

    if np.any(np.isinf(obs)):
        inf_count = int(np.sum(np.isinf(obs)))
        return False, f"Observation contains {inf_count} Inf values"

    # All-zeros means the pipeline produced no signal at all
    if np.all(obs == 0.0):
        return False, "Observation is all zeros (no signal)"

    # Check for extreme values that survived normalization clipping
    max_abs = float(np.max(np.abs(obs)))
    if max_abs > 100.0:
        return False, f"Observation has extreme value: max |x| = {max_abs:.1f}"

    return True, "ok"
