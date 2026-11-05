"""
Episode replay buffer for online learning from live virtual bets.
Stores completed episodes from settled matches; triggers incremental training when full.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Callable, Optional

from shared.logging import setup_logging

logger = setup_logging("episode_replay_buffer")


class EpisodeReplayBuffer:
    """
    In-memory buffer of completed episodes from live virtual trading.
    When buffer reaches threshold, triggers incremental training.
    """

    def __init__(
        self,
        max_episodes: int = 100,
        train_threshold: int = 5,
        min_ticks_per_episode: int = 10,
        on_train_ready: Optional[Callable[[list], None]] = None,
    ) -> None:
        """
        Args:
            max_episodes: Max episodes to keep (FIFO eviction).
            train_threshold: Trigger training when buffer has this many new episodes.
            min_ticks_per_episode: Minimum ticks to accept an episode.
            on_train_ready: Async callback (match_ids) when training should run.
        """
        self._buffer: deque[list[dict[str, Any]]] = deque(maxlen=max_episodes)
        self._train_threshold = train_threshold
        self._min_ticks = min_ticks_per_episode
        self._on_train_ready = on_train_ready
        self._pending_count = 0

    def add(self, episode: list[dict[str, Any]], match_id: str = "") -> bool:
        """
        Add a completed episode. Returns True if added, False if rejected.
        May trigger on_train_ready when threshold reached.
        """
        if len(episode) < self._min_ticks:
            logger.debug(
                "episode_rejected_too_short",
                match_id=match_id,
                ticks=len(episode),
                min_required=self._min_ticks,
            )
            return False

        self._buffer.append(episode)
        self._pending_count += 1

        if self._pending_count >= self._train_threshold and self._on_train_ready:
            episodes = self.get_recent(self._pending_count)
            self._pending_count = 0
            logger.info(
                "replay_buffer_train_trigger",
                episode_count=len(episodes),
                buffer_size=len(self._buffer),
            )
            self._on_train_ready(episodes)

        return True

    def get_recent(self, n: int) -> list[list[dict[str, Any]]]:
        """Get up to n most recent episodes (for training)."""
        if not self._buffer:
            return []
        # Most recent at end
        start = max(0, len(self._buffer) - n)
        return list(self._buffer)[start:]

    def get_all(self) -> list[list[dict[str, Any]]]:
        """Get all episodes in buffer."""
        return list(self._buffer)

    def clear(self) -> None:
        """Clear the buffer."""
        self._buffer.clear()
        self._pending_count = 0

    def __len__(self) -> int:
        return len(self._buffer)
