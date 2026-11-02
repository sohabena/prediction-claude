"""
PHOENIX Specialist Model Zoo & Dynamic Format Router.

Manages format, league, and gender-specialized RL agent models:
- Men & Women separated (T20I Men, T20I Women, ODI Men, ODI Women, BBL Men, WBBL Women)
- Top leagues separated (IPL, BBL, WBBL, T20 Blast, Franchise T20)
- Multi-day & 50-over separated (Test Men, First-Class, ODI Men, ODI Women, List A)

Provides strictly causal zero-lookahead routing based on match format, tournament name, and tier.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

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
LOAD_FAILURE_TTL_SECONDS = 600.0  # negative cache for failed model loads

ALL_SPECIALIST_DEFS: List[Tuple[str, str]] = [
    ("ipl", "IPL (Indian Premier League)"),
    ("bbl_men", "BBL (Men's Big Bash League)"),
    ("wbbl_women", "WBBL (Women's Big Bash League)"),
    ("t20i_men", "T20I (Men's International)"),
    ("t20i_women", "T20I (Women's International)"),
    ("odi_men", "ODI (Men's International)"),
    ("odi_women", "ODI (Women's International)"),
    ("test_men", "Test Matches (Men)"),
    ("t20_blast", "T20 Blast & Domestic T20"),
    ("franchise_t20", "Global Franchise T20 (CPL, PSL, etc.)"),
    ("first_class", "First-Class Multi-Day"),
    ("list_a", "List A (50-Over Domestic)"),
]

KEY_ALIASES: Dict[str, str] = {
    "t20i": "t20i_men",
    "odi": "odi_men",
    "test": "test_men",
    "bbl": "bbl_men",
    "wbbl": "wbbl_women",
}


def _match_team_name(text: str, candidates: Any) -> bool:
    for c in candidates:
        if len(c) <= 3:
            if re.search(r"\b" + re.escape(c) + r"\b", text):
                return True
        else:
            if c in text:
                return True
    return False


class ModelZoo:
    """
    Registry, router, and lifecycle manager for format-specialist trading agents.
    
    Supports 12 distinct format, league, and gender specialists:
    - IPL: Ultra high liquidity, tight tick spreads, death-over volatility, ~188 par.
    - BBL (Men): Australian drop-in pitches, Power Surge mechanics, ~168 par.
    - WBBL (Women): High wicket-decay sensitivity, ~138 par.
    - T20I (Men): Men's international pacing, boundary rate momentum, ~168 par.
    - T20I (Women): Women's international rate strategy, 1.35x wicket sensitivity, ~136 par.
    - ODI (Men): 50-over middle-over accumulation and death acceleration, ~285 par.
    - ODI (Women): Women's 50-over pacing, ~215 par.
    - Test (Men): Multi-day session decay, pitch wear, 3-way draw markets, ~340 par.
    - T20 Blast: English county conditions, early swing bias, ~172 par.
    - Franchise T20: Global franchise dynamics (CPL, PSL, BPL), ~165 par.
    - First-Class: County Championship / domestic multi-day red-ball cricket, ~310 par.
    - List A: Domestic 50-over tournaments (Royal London, Vijay Hazare), ~265 par.
    """

    def __init__(self, models_dir: Path = SPECIALISTS_DIR) -> None:
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_file = self.models_dir / "metadata.json"
        self._models: Dict[str, PhoenixAgent] = {}
        self._default_agent: Optional[PhoenixAgent] = None
        self._model_info: Dict[str, Dict[str, Any]] = {}
        self._load_failures: Dict[str, float] = {}
        self._lock = threading.RLock()
        self._metadata: Dict[str, Any] = {}
        self._classifier = MatchCategoryClassifier()
        self._load_metadata()

    def _load_metadata(self) -> None:
        """Load specialist metadata if available."""
        target_file = self.metadata_file if self.metadata_file.exists() else METADATA_FILE
        if target_file.exists():
            try:
                with open(target_file, "r", encoding="utf-8") as f:
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
        for alias_k, target_k in KEY_ALIASES.items():
            if target_k == key and alias_k in self._models:
                del self._models[alias_k]

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
        1. Women's tournament / match detection (WBBL, Women's T20I, Women's ODI)
        2. Distinct league identification (IPL, BBL, T20 Blast, First-Class, List A, Franchise T20)
        3. Men's international formats (Test Men, ODI Men, T20I Men)
        4. MatchCategoryClassifier structural fallback
        5. Fallback: 'default'
        """
        combined = f"{format_name} {event_name} {match_name} {team_home} {team_away}".lower()

        # Check gender markers
        is_women = (
            "women" in combined
            or "female" in combined
            or "w-t20" in combined
            or "wt20" in combined
            or "w-odi" in combined
            or "wodi" in combined
            or "wbbl" in combined
            or "wpl" in combined
        )

        # 1. WBBL / Women's BBL
        bbl_teams = [t.lower() for t in FRANCHISE_TEAMS.get("BBL", [])]
        if (
            "wbbl" in combined
            or (("big bash" in combined or "bbl" in combined or _match_team_name(combined, bbl_teams)) and is_women)
        ):
            return "wbbl_women"

        # 2. Men's BBL
        if "bbl" in combined or "big bash" in combined or _match_team_name(combined, bbl_teams):
            return "bbl_men"

        # 3. IPL / Indian Premier League
        ipl_teams = [t.lower() for t in FRANCHISE_TEAMS.get("IPL", [])]
        if (
            "ipl" in combined
            or "indian premier league" in combined
            or _match_team_name(combined, ipl_teams)
        ):
            return "ipl"

        # 4. T20 Blast & Domestic T20 (Syed Mushtaq Ali)
        if (
            "blast" in combined
            or "vitality" in combined
            or "mushtaq ali" in combined
            or "t20 blast" in combined
        ):
            return "t20_blast"

        # 5. First-Class Multi-Day / County Championship
        if (
            "first-class" in combined
            or "first class" in combined
            or "county championship" in combined
            or "sheffield" in combined
            or "plunket" in combined
            or "ranji" in combined
        ):
            return "first_class"

        # 6. List A Domestic 50-Over
        if (
            "list a" in combined
            or "list-a" in combined
            or "royal london" in combined
            or "marsh one-day" in combined
            or "vijay hazare" in combined
            or "ford trophy" in combined
            or "one-day cup" in combined
            or "one day cup" in combined
        ):
            return "list_a"

        # 7. Other Franchise T20 leagues (PSL, CPL, BPL, Super Smash, MLC, SA20, ILT20)
        franchise_signals = [
            "psl", "pakistan super league", "cpl", "caribbean premier",
            "bpl", "bangladesh premier", "super smash", "sa20",
            "major league cricket", "mlc", "ilt20", "lpl", "lanka premier"
        ]
        if any(fs in combined for fs in franchise_signals):
            return "franchise_t20"

        # 8. Test Matches (Men)
        if "test" in combined:
            return "test_men"

        # 9. ODI / One-Day / 50-over
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
            return "odi_women" if is_women else "odi_men"

        # 10. T20 / 20-over
        if (
            "t20" in combined
            or "twenty20" in combined
            or "hundred" in combined
        ):
            return "t20i_women" if is_women else "t20i_men"

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
            if cat.tier in (MatchTier.ICC_MEN, MatchTier.ICC_WOMEN, MatchTier.MAJOR_FRANCHISE, MatchTier.TOP_DOMESTIC):
                if cat.tier == MatchTier.MAJOR_FRANCHISE:
                    return "ipl"
                if cat.format == MatchFormat.TEST:
                    return "test_men"
                if cat.format == MatchFormat.ODI:
                    return "odi_women" if is_women else "odi_men"
                if cat.format == MatchFormat.T20:
                    return "t20i_women" if is_women else "t20i_men"
        except Exception:
            pass

        return "default"

    # ------------------------------------------------------------ loading
    def _candidate_paths(self, key: str) -> List[Path]:
        """Ordered candidate weight files for a specialist key."""
        resolved_key = KEY_ALIASES.get(key, key)
        paths = [
            self.models_dir / f"{resolved_key}_v1.zip",
            self.models_dir / f"{resolved_key}.zip",
            self.models_dir / f"{key}_v1.zip",
            self.models_dir / f"{key}.zip",
        ]
        for alias_k, target_k in KEY_ALIASES.items():
            if target_k == resolved_key or target_k == key:
                paths.append(self.models_dir / f"{alias_k}_v1.zip")
                paths.append(self.models_dir / f"{alias_k}.zip")
        if resolved_key in self._metadata and "path" in self._metadata[resolved_key]:
            paths.insert(0, Path(self._metadata[resolved_key]["path"]))
        if key in self._metadata and "path" in self._metadata[key]:
            paths.insert(0, Path(self._metadata[key]["path"]))
        return paths

    def _recently_failed(self, path: Path) -> bool:
        ts = self._load_failures.get(str(path))
        return ts is not None and (time.monotonic() - ts) < LOAD_FAILURE_TTL_SECONDS

    @staticmethod
    def _file_info(key: str, path: Path) -> Dict[str, Any]:
        """Version record: key, path, mtime and sha256[:12] of the weights."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return {
            "key": key,
            "path": str(path),
            "mtime": path.stat().st_mtime_ns,
            "sha256": h.hexdigest()[:12],
        }

    def _try_load(self, key: str, path: Path) -> Optional[Tuple[PhoenixAgent, Dict[str, Any]]]:
        """Load weights from path; failures are cached for LOAD_FAILURE_TTL_SECONDS."""
        if self._recently_failed(path):
            return None
        try:
            info = self._file_info(key, path)
            agent = PhoenixAgent.load(path, env=None)
            self._load_failures.pop(str(path), None)
            return agent, info
        except Exception as e:
            self._load_failures[str(path)] = time.monotonic()
            logger.error("error_loading_specialist", key=key, path=str(path), error=str(e))
            return None

    @staticmethod
    def _version_str(info: Dict[str, Any]) -> str:
        return f"{info['key']}@{info['sha256']}"

    def get_agent_version(self, key: str = "default") -> str:
        """Version id (``key@sha256[:12]``) of the weights serving this key, or ''."""
        resolved = KEY_ALIASES.get(key, key)
        info = self._model_info.get(resolved) or self._model_info.get(key)
        return self._version_str(info) if info else ""

    def get_agent(self, key: str = "default", allow_load: bool = True) -> Optional[PhoenixAgent]:
        """
        Retrieve agent for a specific specialist key, loading on-demand.
        Falls back to default champion if the specialist is not trained yet.

        Failed loads are cached for 10 minutes. Async callers pass allow_load=False so
        they only serve already-loaded models (loading belongs in preload/reload).
        """
        resolved_key = KEY_ALIASES.get(key, key)
        if resolved_key in self._models:
            return self._models[resolved_key]
        if key in self._models:
            return self._models[key]

        if allow_load:
            for path in self._candidate_paths(key):
                if not path.exists():
                    continue
                loaded = self._try_load(resolved_key, path)
                if loaded is None:
                    continue
                agent, info = loaded
                with self._lock:
                    self._models[resolved_key] = agent
                    self._model_info[resolved_key] = info
                    if key != resolved_key:
                        self._models[key] = agent
                logger.info("loaded_specialist_agent", key=resolved_key, path=str(path))
                return agent

            if self._default_agent is None and DEFAULT_MODEL_PATH.exists():
                loaded = self._try_load("default", DEFAULT_MODEL_PATH)
                if loaded is not None:
                    with self._lock:
                        self._default_agent, self._model_info["default"] = loaded
                    logger.info("loaded_default_agent", path=str(DEFAULT_MODEL_PATH))

        return self._default_agent

    def preload(self, keys: Optional[Iterable[str]] = None) -> Dict[str, str]:
        """Load specialists (default: all known) plus the default model.

        Blocking: call via ``asyncio.to_thread`` from async code. Returns {key: version}.
        """
        for key in (list(keys) if keys is not None else [k for k, _ in ALL_SPECIALIST_DEFS]):
            self.get_agent(key)
        self.get_agent("default")
        return {k: self._version_str(i) for k, i in self._model_info.items()}

    async def reload_if_changed(self) -> List[str]:
        """Reload weights whose file changed (or newly appeared), off the event loop.

        New agents are fully built before being swapped in, so readers never see a
        half-loaded model. Returns the keys that were swapped.
        """
        return await asyncio.to_thread(self._reload_sync)

    def _reload_sync(self) -> List[str]:
        swapped: List[str] = []
        for key, info in list(self._model_info.items()):
            path = Path(info["path"])
            try:
                if not path.exists() or path.stat().st_mtime_ns == info["mtime"]:
                    continue
            except OSError:
                continue
            self._load_failures.pop(str(path), None)
            loaded = self._try_load(info["key"], path)
            if loaded is None:
                continue
            agent, new_info = loaded
            with self._lock:
                if key == "default":
                    self._default_agent = agent
                else:
                    old = self._models.get(key)
                    for k, v in list(self._models.items()):
                        if v is old:
                            self._models[k] = agent
                self._model_info[key] = new_info
            if new_info["sha256"] != info["sha256"]:
                swapped.append(key)
                logger.info("specialist_reloaded", key=key, version=self._version_str(new_info))
        # Specialists whose weights appeared after startup
        for key, _ in ALL_SPECIALIST_DEFS:
            if key not in self._models:
                self.get_agent(key)
                if key in self._models:
                    swapped.append(key)
        return swapped

    def get_agent_for_match(
        self,
        format_name: str = "",
        event_name: str = "",
        match_name: str = "",
        team_home: str = "",
        team_away: str = "",
        allow_load: bool = True,
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
        agent = self.get_agent(key, allow_load=allow_load)
        if key in self._models:
            return self._models[key], key
        resolved = KEY_ALIASES.get(key, key)
        if resolved in self._models:
            return self._models[resolved], resolved
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
        result: Dict[str, Any] = {}

        for key, default_name in ALL_SPECIALIST_DEFS:
            model_path = self.models_dir / f"{key}_v1.zip"
            # Fallback check for aliases (e.g. t20i_v1.zip for t20i_men)
            if not model_path.exists():
                for alias_k, target_k in KEY_ALIASES.items():
                    if target_k == key:
                        cand = self.models_dir / f"{alias_k}_v1.zip"
                        if cand.exists():
                            model_path = cand
                            break

            meta = self._metadata.get(key, {})
            result[key] = {
                "key": key,
                "format": meta.get("format", default_name),
                "available": model_path.exists(),
                "path": str(model_path) if model_path.exists() else None,
                "matches_trained_on": meta.get("matches_trained_on", 0),
                "metrics": meta.get("metrics", {}),
            }

        # Backwards compatibility: add alias keys for consumers expecting legacy names
        for alias_k, target_k in KEY_ALIASES.items():
            if alias_k not in result and target_k in result:
                result[alias_k] = result[target_k]

        return result


# Singleton instance
_global_model_zoo: Optional[ModelZoo] = None


def get_model_zoo() -> ModelZoo:
    """Get or create singleton ModelZoo instance."""
    global _global_model_zoo
    if _global_model_zoo is None:
        _global_model_zoo = ModelZoo()
    return _global_model_zoo
