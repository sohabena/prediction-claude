"""
Curriculum learning: progressive difficulty stages for RL training.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any

from shared.logging import setup_logging

logger = setup_logging("rl_curriculum")


class CurriculumStage(IntEnum):
    """Training stages in order of difficulty."""

    PATTERN_RECOGNITION = 1  # Simplified: only BACK/HOLD, historical data
    FULL_ACTIONS = 2         # All 7 actions, historical data
    LIVE_SIMULATION = 3      # Live odds with virtual execution + slippage
    ADVERSARIAL = 4          # Live market with noise injection


# Graduation criteria per stage
STAGE_CRITERIA: dict[CurriculumStage, dict[str, float]] = {
    CurriculumStage.PATTERN_RECOGNITION: {
        "win_rate": 0.52,
        "min_episodes": 100,
    },
    CurriculumStage.FULL_ACTIONS: {
        "roi": 0.05,
        "win_rate": 0.53,
        "min_episodes": 200,
    },
    CurriculumStage.LIVE_SIMULATION: {
        "roi": 0.08,
        "win_rate": 0.55,
        "sharpe": 1.0,
        "min_episodes": 200,
    },
    CurriculumStage.ADVERSARIAL: {
        "roi": 0.05,  # Lower bar under adversarial conditions
        "win_rate": 0.52,
        "min_episodes": 100,
    },
}


class CurriculumManager:
    """
    Manages progressive training difficulty.

    Stage 1: Pattern Recognition
    - Simplified actions (BACK_HOME / HOLD only)
    - Clean historical data
    - Goal: Learn when odds are mispriced (> 52% win rate)

    Stage 2: Full Actions
    - All 7 actions enabled
    - Historical data
    - Goal: Learn sizing and LAY (> 5% ROI)

    Stage 3: Live Simulation
    - Live odds with virtual execution + slippage
    - Goal: Handle market dynamics (> 55% win rate, > 8% ROI)

    Stage 4: Adversarial
    - Live market with noise injection
    - Goal: Robustness to regime changes
    """

    def __init__(self) -> None:
        self.current_stage = CurriculumStage.PATTERN_RECOGNITION
        self._stage_metrics: dict[CurriculumStage, list[dict[str, float]]] = {
            stage: [] for stage in CurriculumStage
        }

    def get_stage_data(
        self,
        all_data: list[Any],
        noise_std: float = 0.0,
    ) -> list[Any]:
        """
        Get training data appropriate for current curriculum stage.

        Args:
            all_data: Full dataset.
            noise_std: Noise to inject (for adversarial stage).
        """
        if not all_data:
            return all_data

        if self.current_stage == CurriculumStage.PATTERN_RECOGNITION:
            # Use first half of data (simpler matches)
            split = max(1, len(all_data) // 2)
            return all_data[:split]

        elif self.current_stage == CurriculumStage.FULL_ACTIONS:
            return all_data

        elif self.current_stage == CurriculumStage.LIVE_SIMULATION:
            return all_data

        elif self.current_stage == CurriculumStage.ADVERSARIAL:
            return all_data

        return all_data

    def get_allowed_actions(self) -> list[int]:
        """Get actions allowed at the current curriculum stage."""
        if self.current_stage == CurriculumStage.PATTERN_RECOGNITION:
            return [0, 1]  # HOLD, BACK_HOME_SM only
        return list(range(7))  # All actions

    def record_episode(self, metrics: dict[str, float]) -> None:
        """Record episode metrics for stage graduation tracking."""
        self._stage_metrics[self.current_stage].append(metrics)

    def should_advance(self) -> bool:
        """Check if agent should advance to next curriculum stage."""
        criteria = STAGE_CRITERIA.get(self.current_stage, {})
        history = self._stage_metrics[self.current_stage]

        if len(history) < criteria.get("min_episodes", 100):
            return False

        # Check recent performance (last 50 episodes)
        recent = history[-50:]

        for metric_name, threshold in criteria.items():
            if metric_name == "min_episodes":
                continue
            values = [ep.get(metric_name, 0) for ep in recent]
            if not values or sum(values) / len(values) < threshold:
                return False

        return True

    def advance(self) -> bool:
        """Advance to the next curriculum stage if possible."""
        if not self.should_advance():
            return False

        next_stage = self.current_stage.value + 1
        if next_stage > CurriculumStage.ADVERSARIAL:
            logger.info("curriculum_completed", final_stage=self.current_stage.name)
            return False

        old_stage = self.current_stage
        self.current_stage = CurriculumStage(next_stage)
        logger.info(
            "curriculum_advanced",
            from_stage=old_stage.name,
            to_stage=self.current_stage.name,
        )
        return True
