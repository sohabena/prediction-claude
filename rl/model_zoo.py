"""
PHOENIX Specialist Model Zoo & Dynamic Format Router.

Manages format-specialized RL agent models (T20I, ODI, Test, IPL) and provides
intelligent routing based on match format, tournament name, and tier.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np

from features.extractors.category_features import MatchCategoryClassifier, MatchFormat, MatchTier
from rl.agent import PhoenixAgent
from shared.constants import (
    FRANCHISE_TEAMS,
    INTERNATIONAL_TEAMS,
)
from shared.logging import setup_logging

logger = setup_logging("model_zoo")

SPECIALISTS_DIR = Path("models/specialists")
METADATA_FILE = SPECIALISTS_DIR / "metadata.json"
DEFAULT_MODEL_PATH = Path("models/best_model.zip")


class ModelZoo:
    """
    Registry, router, and lifecycle manager for format-specialist trading agents.
    
    Supports:
    - T20I Specialist: Optimized for rapid rate fluctuations, early boundary hedging.
    - ODI Specialist: Optimized for 50-over pacing, middle-over accumulation, DLS chases.
    - Test Specialist: Optimized for multi-day session decay, pitch wear, 3-way draw markets.
    - IPL Specialist: Optimized for high-liquidity order books, tight tick spreads, death-over volatility.
    """

    def __init__(self, models_dir: Path = SPECIALISTS_DIR) -> None:
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_file = self.models_dir / "metadata.json"
        self._models: Dict[str, PhoenixAgent] = {}
        self._default_agent: Optional[PhoenixAgent] = None
        self._metadata: Dict[str, Any] = {}
        self._classifier = MatchCategoryClassifier()
        self._load_metadata()

    def _load_metadata(self) -> None:
        """Load specialist metadata if available."""
        if self.metadata_file.exists():
            try:
                with open(self.metadata_file, "r", encoding="utf-8") as f:
                    self._metadata = json.load(f)
            except Exception as e:
                logger.warning("failed_to_load_metadata", error=str(e))
                self._metadata = {}
        elif METADATA_FILE.exists() and self.models_dir == SPECIALISTS_DIR:
            try:
                with open(METADATA_FILE, "r", encoding="utf-8") as f:
                    self._metadata = json.load(f)
            except Exception as e:
                logger.warning("failed_to_load_metadata", error=str(e))
                self._metadata = {}
        else:
            self._metadata = {}

    def save_metadata(self) -> None:
        """Persist specialist metadata to disk."""
        self.models_dir.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.metadata_file, "w", encoding="utf-8") as f:
                json.dump(self._metadata, f, indent=2)
        except Exception as e:
            logger.error("failed_to_save_metadata", error=str(e))

    def register_specialist(
        self,
        key: str,
        model_path: str | Path,
        format_name: str,
        matches_count: int,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Register a newly trained specialist in metadata."""
        self._metadata[key] = {
            "key": key,
            "path": str(model_path),
            "format": format_name,
            "matches_trained_on": matches_count,
            "metrics": metrics or {},
            "active": True,
        }
        self.save_metadata()
        # Invalidate in-memory cache to force reload on next query
        if key in self._models:
            del self._models[key]

    def resolve_specialist_key(
        self,
        format_name: str = "",
        event_name: str = "",
        match_name: str = "",
        team_home: str = "",
        team_away: str = "",
    ) -> str:
        """
        Determine the appropriate specialist key from format or tournament details.
        
        Resolution Priority:
        1. Event/Tournament specific (e.g. 'ipl' if Indian Premier League)
        2. Format specific: 't20i' (T20), 'odi' (ODI), 'test' (Test / Multi-Day)
        3. MatchCategoryClassifier structural fallback
        4. Fallback: 'default'
        """
        combined = f"{format_name} {event_name} {match_name} {team_home} {team_away}".lower()

        # Check for IPL / Franchise
        ipl_teams = [t.lower() for t in FRANCHISE_TEAMS.get("IPL", [])]
        if (
            "ipl" in combined
            or "indian premier league" in combined
            or any(t in combined for t in ipl_teams)
        ):
            return "ipl"

        # Check for Test / First Class
        if (
            "test" in combined
            or "first-class" in combined
            or "county" in combined
            or "sheffield" in combined
            or "plunket" in combined
        ):
            return "test"

        # Check for ODI / List A / 50-over
        if (
            "odi" in combined
            or "one-day" in combined
            or "one day" in combined
            or "50-over" in combined
            or "rlc" in combined
            or ("cricket world cup" in combined and "t20" not in combined)
            or ("world cup" in combined and "t20" not in combined)
            or "champions trophy" in combined
        ):
            return "odi"

        # Check for T20 / 20-over
        if (
            "t20" in combined
            or "twenty20" in combined
            or "hundred" in combined
            or "bbl" in combined
            or "psl" in combined
            or "cpl" in combined
            or "blast" in combined
        ):
            return "t20i"

        # Structural classification fallback using existing classifier
        th = team_home
        ta = team_away
        if match_name and not (th and ta):
            parts = re.split(r"\s+vs\s+|\s+v\s+", match_name, flags=re.IGNORECASE)
            if len(parts) >= 2:
                th, ta = parts[0].strip(), parts[1].strip()

        try:
            cat = self._classifier.classify(
                competition=event_name or format_name,
                team_home=th,
                team_away=ta,
            )
            # Only map if classifier recognized a distinct cricket tier or non-empty signal
            if cat.tier in (MatchTier.ICC_MEN, MatchTier.MAJOR_FRANCHISE, MatchTier.TOP_DOMESTIC):
                if cat.tier == MatchTier.MAJOR_FRANCHISE:
                    return "ipl"
                if cat.format == MatchFormat.TEST:
                    return "test"
                if cat.format == MatchFormat.ODI:
                    return "odi"
                if cat.format == MatchFormat.T20:
                    return "t20i"
        except Exception:
            pass

        return "default"

    def get_agent(self, key: str = "default") -> Optional[PhoenixAgent]:
        """
        Retrieve agent for a specific specialist key, loading on-demand.
        Falls back to default champion if the specialist is not trained yet.
        """
        if key in self._models:
            return self._models[key]

        # Check specialist path
        candidate_paths = [
            self.models_dir / f"{key}_v1.zip",
            self.models_dir / f"{key}.zip",
        ]
        
        # Check metadata for path
        if key in self._metadata and "path" in self._metadata[key]:
            candidate_paths.insert(0, Path(self._metadata[key]["path"]))

        for path in candidate_paths:
            if path.exists():
                try:
                    agent = PhoenixAgent.load(path, env=None)
                    self._models[key] = agent
                    logger.info("loaded_specialist_agent", key=key, path=str(path))
                    return agent
                except Exception as e:
                    logger.error("error_loading_specialist", key=key, path=str(path), error=str(e))

        # Fallback to default champion model
        if self._default_agent is None and DEFAULT_MODEL_PATH.exists():
            try:
                self._default_agent = PhoenixAgent.load(DEFAULT_MODEL_PATH, env=None)
                logger.info("loaded_default_agent", path=str(DEFAULT_MODEL_PATH))
            except Exception as e:
                logger.error("error_loading_default_agent", error=str(e))

        return self._default_agent

    def get_agent_for_match(
        self,
        format_name: str = "",
        event_name: str = "",
        match_name: str = "",
        team_home: str = "",
        team_away: str = "",
    ) -> Tuple[Optional[PhoenixAgent], str]:
        """
        Resolve match details to specialist key and return the loaded agent.
        Returns: (agent, resolved_key)
        """
        key = self.resolve_specialist_key(
            format_name=format_name,
            event_name=event_name,
            match_name=match_name,
            team_home=team_home,
            team_away=team_away,
        )
        agent = self.get_agent(key)
        if key in self._models:
            return self._models[key], key
        return agent, ("default" if agent is self._default_agent else key)

    def predict(
        self,
        observation: np.ndarray,
        format_name: str = "",
        event_name: str = "",
        match_name: str = "",
        team_home: str = "",
        team_away: str = "",
        deterministic: bool = True,
    ) -> Tuple[int, Optional[np.ndarray], str]:
        """
        Route observation to the appropriate specialist agent and return action.
        Returns: (action, states, specialist_key_used)
        """
        agent, key = self.get_agent_for_match(
            format_name=format_name,
            event_name=event_name,
            match_name=match_name,
            team_home=team_home,
            team_away=team_away,
        )
        if agent is None:
            return 0, None, "none"
        action, states = agent.predict(observation, deterministic=deterministic)
        return action, states, key

    def get_action_distribution(
        self,
        observation: np.ndarray,
        format_name: str = "",
        event_name: str = "",
        match_name: str = "",
        team_home: str = "",
        team_away: str = "",
    ) -> Tuple[np.ndarray, str]:
        """
        Route observation to specialist agent and return action probability distribution.
        Returns: (probs, specialist_key_used)
        """
        agent, key = self.get_agent_for_match(
            format_name=format_name,
            event_name=event_name,
            match_name=match_name,
            team_home=team_home,
            team_away=team_away,
        )
        if agent is None:
            return np.ones(10) / 10.0, "none"
        probs = agent.get_action_distribution(observation)
        return probs, key

    def list_specialists(self) -> Dict[str, Any]:
        """List all available specialists and their metadata status."""
        self._load_metadata()
        result = {}
        for key in ["t20i", "odi", "test", "ipl"]:
            model_path = self.models_dir / f"{key}_v1.zip"
            meta = self._metadata.get(key, {})
            result[key] = {
                "key": key,
                "format": meta.get("format", key.upper()),
                "available": model_path.exists(),
                "path": str(model_path) if model_path.exists() else None,
                "matches_trained_on": meta.get("matches_trained_on", 0),
                "metrics": meta.get("metrics", {}),
            }
        return result


# Singleton instance
_global_model_zoo: Optional[ModelZoo] = None


def get_model_zoo() -> ModelZoo:
    """Get or create singleton ModelZoo instance."""
    global _global_model_zoo
    if _global_model_zoo is None:
        _global_model_zoo = ModelZoo()
    return _global_model_zoo
