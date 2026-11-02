"""
PHOENIX Format-Specialist Model Zoo Training Pipeline.

Trains, tunes, and evaluates 12 dedicated reinforcement learning trading models:
- Men & Women separated: T20I Men, T20I Women, ODI Men, ODI Women, BBL Men, WBBL Women
- Major leagues separated: IPL, BBL, WBBL, T20 Blast, Franchise T20 (CPL, PSL, BPL)
- Multi-day & 50-over separated: Test Men, First-Class, ODI Men, ODI Women, List A

Each specialist uses format/league/gender-tailored rate strategies, calibrated par curves,
and is evaluated on unseen holdouts under strictly causal zero-lookahead time-series mechanics
(20-30 bets/match, $100k budget, 2% commission, lay liability <= 2.10).
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import inspect
import json
import logging
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

import numpy as np
import torch
from sqlalchemy import text
from stable_baselines3 import PPO

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # allow `python scripts/...`

from backtesting.baselines import (
    HeuristicPolicy,
    HoldPolicy,
    RandomPolicy,
    estimate_in_play_prob,
)
from rl.agent import PhoenixAgent
from rl.environment import CricketBettingEnv
from rl.model_zoo import ModelZoo, get_model_zoo
from rl.reward import RewardFunction
from shared.constants import (
    ACTION_SPACE_SIZE,
    KEY_AGENT_VERSION,
    KEY_ORCHESTRATOR_STATE,
    KEY_ORCHESTRATOR_STATS,
    OBSERVATION_SIZE,
)
from shared.db import get_session
from shared.logging import setup_logging
from shared.metrics import clv_prob
from shared.redis_client import get_redis
from shared.schemas import BettingAction

logger = setup_logging("train_format_specialists")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

SPECIALISTS_DIR = Path("models/specialists")
CORPUS_DIR = Path("data/specialists")

ALL_SPECIALISTS_CONFIG: List[Dict[str, Any]] = [
    {
        "key": "ipl",
        "name": "Indian Premier League",
        "gender": "men",
        "max_overs": 20.0,
        "par_1": 188.0,
        "wicket_decay_power": 0.65,
        "div": 18.0,
        "gamma": 0.98,
        "ent_coef": 0.015,
        "lr": 3e-4,
    },
    {
        "key": "bbl_men",
        "name": "Men's Big Bash League",
        "gender": "men",
        "max_overs": 20.0,
        "par_1": 168.0,
        "wicket_decay_power": 0.65,
        "div": 18.0,
        "gamma": 0.98,
        "ent_coef": 0.012,
        "lr": 3e-4,
    },
    {
        "key": "wbbl_women",
        "name": "Women's Big Bash League",
        "gender": "women",
        "max_overs": 20.0,
        "par_1": 138.0,
        "wicket_decay_power": 0.85,
        "div": 16.0,
        "gamma": 0.98,
        "ent_coef": 0.012,
        "lr": 3e-4,
    },
    {
        "key": "t20i_men",
        "name": "Men's T20 International",
        "gender": "men",
        "max_overs": 20.0,
        "par_1": 168.0,
        "wicket_decay_power": 0.65,
        "div": 18.0,
        "gamma": 0.98,
        "ent_coef": 0.012,
        "lr": 3e-4,
    },
    {
        "key": "t20i_women",
        "name": "Women's T20 International",
        "gender": "women",
        "max_overs": 20.0,
        "par_1": 136.0,
        "wicket_decay_power": 0.85,
        "div": 16.0,
        "gamma": 0.98,
        "ent_coef": 0.012,
        "lr": 3e-4,
    },
    {
        "key": "odi_men",
        "name": "Men's One-Day International",
        "gender": "men",
        "max_overs": 50.0,
        "par_1": 285.0,
        "wicket_decay_power": 0.65,
        "div": 35.0,
        "gamma": 0.99,
        "ent_coef": 0.008,
        "lr": 3e-4,
    },
    {
        "key": "odi_women",
        "name": "Women's One-Day International",
        "gender": "women",
        "max_overs": 50.0,
        "par_1": 215.0,
        "wicket_decay_power": 0.85,
        "div": 30.0,
        "gamma": 0.99,
        "ent_coef": 0.008,
        "lr": 3e-4,
    },
    {
        "key": "test_men",
        "name": "Men's Test Match",
        "gender": "men",
        "max_overs": 100.0,
        "par_1": 340.0,
        "wicket_decay_power": 0.65,
        "div": 45.0,
        "gamma": 0.995,
        "ent_coef": 0.006,
        "lr": 2e-4,
    },
    {
        "key": "t20_blast",
        "name": "T20 Blast & Domestic T20",
        "gender": "men",
        "max_overs": 20.0,
        "par_1": 172.0,
        "wicket_decay_power": 0.68,
        "div": 18.0,
        "gamma": 0.98,
        "ent_coef": 0.012,
        "lr": 3e-4,
    },
    {
        "key": "franchise_t20",
        "name": "Global Franchise T20 (CPL, PSL, BPL)",
        "gender": "men",
        "max_overs": 20.0,
        "par_1": 165.0,
        "wicket_decay_power": 0.65,
        "div": 18.0,
        "gamma": 0.98,
        "ent_coef": 0.012,
        "lr": 3e-4,
    },
    {
        "key": "first_class",
        "name": "First-Class Multi-Day",
        "gender": "men",
        "max_overs": 100.0,
        "par_1": 310.0,
        "wicket_decay_power": 0.65,
        "div": 45.0,
        "gamma": 0.995,
        "ent_coef": 0.006,
        "lr": 2e-4,
    },
    {
        "key": "list_a",
        "name": "List A Domestic 50-Over",
        "gender": "men",
        "max_overs": 50.0,
        "par_1": 265.0,
        "wicket_decay_power": 0.65,
        "div": 35.0,
        "gamma": 0.99,
        "ent_coef": 0.008,
        "lr": 3e-4,
    },
]

SPECIALIST_MAP: Dict[str, Dict[str, Any]] = {c["key"]: c for c in ALL_SPECIALISTS_CONFIG}

# Aliases for CLI convenience
KEY_ALIASES = {
    "t20i": "t20i_men",
    "odi": "odi_men",
    "test": "test_men",
    "bbl": "bbl_men",
    "wbbl": "wbbl_women",
}


@dataclass
class SpecialistEvaluationMetrics:
    key: str
    format_name: str
    matches_trained: int
    matches_tested: int
    timesteps: int
    total_bets: int
    avg_bets_per_match: float
    hedged_bets: int
    hedge_rate_pct: float
    win_rate_pct: float
    total_pnl: float
    mean_roi_pct: float
    sharpe_ratio: float
    max_drawdown_dollars: float
    max_drawdown_pct: float
    profit_factor: float
    # --- honest-evaluation additions (defaulted for backwards compatibility) ---
    policy: str = "model"
    evaluated_at: str = ""
    holdout_match_ids_hash: str = ""
    code_version: str = ""
    n_matches: int = 0
    bet_win_rate_pct: float = 0.0
    total_stake: float = 0.0
    roi_on_turnover_pct: float = 0.0
    mean_match_pnl: float = 0.0
    pnl_ci95_low: float = 0.0
    pnl_ci95_high: float = 0.0
    commission_paid: float = 0.0
    edge_vs_random: Optional[float] = None
    baselines: Dict[str, Dict[str, Any]] = field(default_factory=dict)


def load_corpus(key: str) -> List[Dict[str, Any]]:
    """Load a specialist corpus as match dicts (``match_id``, ``start_time``, ``ticks``, ...)."""
    resolved_key = KEY_ALIASES.get(key, key)
    fpath = CORPUS_DIR / f"corpus_{resolved_key}.json.gz"
    if not fpath.exists():
        # Fallback check
        alt = CORPUS_DIR / f"corpus_{key}.json.gz"
        if alt.exists():
            fpath = alt
        else:
            raise FileNotFoundError(f"Corpus file not found: {fpath}. Run prepare_specialist_datasets.py first.")

    with gzip.open(fpath, "rt", encoding="utf-8") as f:
        matches = json.load(f)

    total_ticks = sum(len(m["ticks"]) for m in matches)
    print(f"Loaded {len(matches)} matches ({total_ticks:,} ticks) from {fpath.name}")
    return matches


TEAM_RATINGS: Dict[str, int] = {
    # International Men
    "India": 125, "Australia": 122, "England": 118, "South Africa": 115,
    "New Zealand": 112, "Pakistan": 108, "West Indies": 102, "Sri Lanka": 100,
    "Bangladesh": 95, "Afghanistan": 92, "Ireland": 85, "Zimbabwe": 80,
    "Netherlands": 75, "Scotland": 75, "Nepal": 65, "Namibia": 65,
    "United Arab Emirates": 65, "Oman": 60, "United States of America": 60,
    # International Women
    "Australia Women": 130, "England Women": 122, "India Women": 120,
    "South Africa Women": 112, "New Zealand Women": 110, "West Indies Women": 98,
    "Sri Lanka Women": 92, "Pakistan Women": 90, "Bangladesh Women": 88,
    "Thailand Women": 78, "Ireland Women": 82, "Scotland Women": 76,
    # IPL
    "Chennai Super Kings": 105, "Mumbai Indians": 105, "Kolkata Knight Riders": 102,
    "Gujarat Titans": 102, "Rajasthan Royals": 100, "Royal Challengers Bangalore": 98,
    "Delhi Capitals": 96, "Punjab Kings": 95, "Sunrisers Hyderabad": 98, "Lucknow Super Giants": 100,
    # BBL Men
    "Perth Scorchers": 108, "Sydney Sixers": 106, "Brisbane Heat": 102,
    "Adelaide Strikers": 100, "Melbourne Stars": 98, "Hobart Hurricanes": 96,
    "Sydney Thunder": 95, "Melbourne Renegades": 94,
    # WBBL Women
    "Sydney Sixers Women": 108, "Perth Scorchers Women": 106, "Adelaide Strikers Women": 106,
    "Brisbane Heat Women": 104, "Melbourne Stars Women": 98, "Hobart Hurricanes Women": 96,
    "Sydney Thunder Women": 96, "Melbourne Renegades Women": 94,
}


def estimate_causal_in_play_prob(tick: Dict[str, Any], specialist_key: str) -> float:
    """
    Causal in-play home win probability from the state at time t (no winner/future access).
    Thin wrapper over backtesting.baselines.estimate_in_play_prob with the specialist's config.
    TEAM_RATINGS are static hand-assigned priors, not fitted on the corpus.
    """
    cfg = SPECIALIST_MAP.get(specialist_key, SPECIALIST_MAP.get("t20i_men", {}))
    return estimate_in_play_prob(tick, cfg, TEAM_RATINGS)


def _parse_tick_time(tick: Dict[str, Any]) -> datetime:
    """Best-effort tz-aware timestamp of a tick (falls back to now)."""
    raw = tick.get("timestamp", tick.get("time"))
    try:
        if isinstance(raw, datetime):
            dt = raw
        elif isinstance(raw, (int, float)):
            dt = datetime.fromtimestamp(float(raw), tz=timezone.utc)
        elif isinstance(raw, str):
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:
            return datetime.now(timezone.utc)
    except (ValueError, OSError, OverflowError):
        return datetime.now(timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _code_version() -> str:
    """Short git SHA of the working tree, or 'unknown'."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _action_confidence(model: Any, obs: np.ndarray, action: int) -> Optional[float]:
    """Policy probability of ``action`` for ``obs``; None if the model cannot report it."""
    try:
        obs_t = model.policy.obs_to_tensor(obs)[0]
        probs = model.policy.get_distribution(obs_t).distribution.probs
        return float(probs.detach().cpu().numpy().reshape(-1)[int(action)])
    except Exception:
        return None


def _bet_is_back(action: BettingAction) -> bool:
    return action in (
        BettingAction.BACK_HOME_SM, BettingAction.BACK_HOME_LG,
        BettingAction.BACK_AWAY_SM, BettingAction.BACK_AWAY_LG,
    )


def _bet_is_home(action: BettingAction) -> bool:
    return action in (
        BettingAction.BACK_HOME_SM, BettingAction.BACK_HOME_LG,
        BettingAction.LAY_HOME_SM, BettingAction.LAY_HOME_LG,
    )


def _make_env(
    ep_ticks: List[Dict[str, Any]], starting_bankroll: float, max_bets: int, commission: float
) -> CricketBettingEnv:
    kwargs: Dict[str, Any] = dict(
        data=[ep_ticks],
        initial_bankroll=starting_bankroll,
        settlement_mode="real",
        max_bets_per_episode=max_bets,
        exchange_commission=commission,
    )
    if "episode_sampling" in inspect.signature(CricketBettingEnv.__init__).parameters:
        kwargs["episode_sampling"] = "sequential"
    return CricketBettingEnv(**kwargs)


def _run_policy(
    act: Callable[[np.ndarray, Dict[str, Any], CricketBettingEnv, int], Tuple[int, Optional[float]]],
    reset_policy: Callable[[], None],
    specialist_key: str,
    test_episodes: List[List[Dict[str, Any]]],
    max_bets_per_match: int,
    commission: float,
    starting_bankroll: float,
    version: str,
) -> Dict[str, Any]:
    """Drive one policy through every holdout episode and collect raw per-bet / per-match results."""
    match_pnls: List[float] = []
    match_stakes: List[float] = []
    match_commission: List[float] = []
    bet_rows: List[Dict[str, Any]] = []
    hedged = 0
    total_bets = 0
    bet_wins = 0
    settled_bets = 0

    for ep_idx, ep_ticks in enumerate(test_episodes):
        env = _make_env(ep_ticks, starting_bankroll, max_bets_per_match, commission)
        obs, _ = env.reset()
        reset_policy()
        match_id = ep_ticks[0].get("match_id", f"{specialist_key}_test_{ep_idx}")
        last_tick = ep_ticks[-1]
        seen: set = set()
        placed: List[Dict[str, Any]] = []
        done = False
        step = 0

        while not done:
            tick = ep_ticks[min(step, len(ep_ticks) - 1)]
            action, conf = act(obs, tick, env, step)
            action = int(action)
            before_home, before_away = env._net_exposure_home, env._net_exposure_away
            obs, reward, terminated, truncated, info = env.step(action)
            done = terminated or truncated
            if info["step_info"].get("bet_placed"):
                pool = list(env._open_bets) + list(env._settled_bets_history)
                new = [b for b in pool if id(b) not in seen]
                if new:
                    bet = new[-1]
                    seen.add(id(bet))
                    if _bet_is_home(bet.action):
                        b_exp, a_exp = before_home, env._net_exposure_home
                    else:
                        b_exp, a_exp = before_away, env._net_exposure_away
                    is_hedge = b_exp != 0 and abs(a_exp) < abs(b_exp)
                    placed.append(
                        {"bet": bet, "tick": tick, "conf": conf, "reward": float(reward), "hedge": is_hedge}
                    )
            step += 1

        match_pnl = float(env._portfolio.session_pnl)
        gross = sum(float(p["bet"].profit_loss) for p in placed)
        match_pnls.append(match_pnl)
        match_stakes.append(sum(float(p["bet"].stake) for p in placed))
        match_commission.append(max(0.0, gross - match_pnl))
        total_bets += env._bets_this_episode

        for k, p in enumerate(placed):
            bet = p["bet"]
            back = _bet_is_back(bet.action)
            side = ("back_" if back else "lay_") + ("home" if _bet_is_home(bet.action) else "away")
            closing = last_tick.get(side)
            closing = float(closing) if closing and closing > 1.0 else None
            clv = None
            if closing is not None and bet.odds > 1.0:
                clv = clv_prob("BACK" if back else "LAY", bet.odds, closing)
            hedged += 1 if p["hedge"] else 0
            outcome = bet.outcome.value if hasattr(bet.outcome, "value") else str(bet.outcome)
            if outcome.lower() in ("win", "loss"):
                settled_bets += 1
                bet_wins += 1 if outcome.lower() == "win" else 0
            bet_rows.append({
                "placed_at": _parse_tick_time(p["tick"]) + timedelta(microseconds=k),
                "match_id": match_id,
                "action": bet.action.name if hasattr(bet.action, "name") else str(bet.action),
                "team": bet.team,
                "odds": bet.odds,
                "stake": bet.stake,
                "confidence": p["conf"],
                "settled_at": bet.settled_at,
                "outcome": outcome,
                "profit_loss": bet.profit_loss,
                "closing_odds": closing,
                "clv": clv,
                "agent_version": f"holdout_eval:{specialist_key}:{version}",
                "reward": p["reward"],
            })

    return {
        "match_pnls": match_pnls, "match_stakes": match_stakes, "match_commission": match_commission,
        "bet_rows": bet_rows, "hedged": hedged, "total_bets": total_bets,
        "bet_wins": bet_wins, "settled_bets": settled_bets,
    }


def _bootstrap_mean_ci(values: List[float], n_resamples: int = 1000, seed: int = 0) -> Tuple[float, float]:
    """Seeded 95% percentile bootstrap CI for the mean."""
    if not values:
        return 0.0, 0.0
    arr = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(arr), size=(n_resamples, len(arr)))
    means = arr[idx].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def _summarize_run(
    raw: Dict[str, Any],
    specialist_key: str,
    format_name: str,
    policy: str,
    starting_bankroll: float,
    holdout_hash: str,
    code_version: str,
    seed: int,
) -> SpecialistEvaluationMetrics:
    """Turn raw run results into a metrics object (all values derived from simulated bets)."""
    pnls = raw["match_pnls"]
    n = len(pnls)
    total_pnl = float(sum(pnls))
    total_stake = float(sum(raw["match_stakes"]))
    mean_pnl = float(np.mean(pnls)) if n else 0.0
    std_pnl = float(np.std(pnls, ddof=1)) if n > 1 else 0.0
    sharpe = mean_pnl / std_pnl if std_pnl > 1e-9 else 0.0

    cum = np.cumsum(pnls) if n else np.zeros(1)
    max_dd = float(np.max(np.maximum.accumulate(cum) - cum))
    capital = starting_bankroll * max(1, n)
    gross_win = float(sum(p for p in pnls if p > 0))
    gross_loss = float(sum(-p for p in pnls if p < 0))
    ci_lo, ci_hi = _bootstrap_mean_ci(pnls, 1000, seed)
    total_bets = int(raw["total_bets"])
    if gross_loss > 0:
        profit_factor = round(gross_win / gross_loss, 3)
    else:
        profit_factor = 0.0 if gross_win == 0 else float("inf")

    return SpecialistEvaluationMetrics(
        key=specialist_key,
        format_name=format_name,
        matches_trained=0,
        matches_tested=n,
        timesteps=0,
        total_bets=total_bets,
        avg_bets_per_match=round(total_bets / max(1, n), 2),
        hedged_bets=int(raw["hedged"]),
        hedge_rate_pct=round(100.0 * raw["hedged"] / max(1, total_bets), 2),
        win_rate_pct=round(100.0 * sum(1 for p in pnls if p > 0) / max(1, n), 2),
        total_pnl=round(total_pnl, 2),
        mean_roi_pct=round(100.0 * mean_pnl / starting_bankroll, 4),
        sharpe_ratio=round(sharpe, 4),
        max_drawdown_dollars=round(max_dd, 2),
        max_drawdown_pct=round(100.0 * max_dd / capital, 4),
        profit_factor=profit_factor,
        policy=policy,
        evaluated_at=datetime.now(timezone.utc).isoformat(),
        holdout_match_ids_hash=holdout_hash,
        code_version=code_version,
        n_matches=n,
        bet_win_rate_pct=round(100.0 * raw["bet_wins"] / max(1, raw["settled_bets"]), 2),
        total_stake=round(total_stake, 2),
        roi_on_turnover_pct=round(100.0 * total_pnl / total_stake, 4) if total_stake > 0 else 0.0,
        mean_match_pnl=round(mean_pnl, 2),
        pnl_ci95_low=round(ci_lo, 2),
        pnl_ci95_high=round(ci_hi, 2),
        commission_paid=round(float(sum(raw["match_commission"])), 2),
    )


def evaluate_specialist_holdout(
    model: PPO,
    specialist_key: str,
    format_name: str,
    test_episodes: List[List[Dict[str, Any]]],
    max_bets_per_match: int = 25,
    commission: float = 0.02,
    starting_bankroll: float = 100_000.0,
    *,
    policy: Literal["model", "heuristic", "random", "hold", "value"] = "model",
    include_baselines: bool = True,
    seed: int = 0,
    version: str = "v1",
) -> Tuple[SpecialistEvaluationMetrics, List[Dict[str, Any]]]:
    """Evaluate a policy on unseen holdout matches by simulating real bets in the environment.

    With ``policy="model"`` every action is ``model.predict(obs, deterministic=True)``.
    All metrics derive from the bets the environment actually settled. Unless disabled,
    ``random`` and ``hold`` baselines are run through the same loop on the same episodes and
    attached under ``metrics.baselines``; ``edge_vs_random`` is model minus random mean P&L.
    """
    cfg = SPECIALIST_MAP.get(specialist_key, SPECIALIST_MAP.get("t20i_men", {}))
    ids = [str(ep[0].get("match_id", i)) if ep else str(i) for i, ep in enumerate(test_episodes)]
    holdout_hash = hashlib.sha1("|".join(ids).encode("utf-8")).hexdigest()[:12]
    code_version = _code_version()

    def run(pol: str) -> Dict[str, Any]:
        if pol == "model":
            def act(obs: np.ndarray, tick: Dict[str, Any], env: Any, step: int) -> Tuple[int, Optional[float]]:
                a, _ = model.predict(obs, deterministic=True)
                a = int(np.asarray(a).reshape(-1)[0])
                return a, _action_confidence(model, obs, a)

            return _run_policy(act, lambda: None, specialist_key, test_episodes,
                               max_bets_per_match, commission, starting_bankroll, version)
        if pol == "heuristic":
            n_ticks = int(np.mean([len(e) for e in test_episodes])) if test_episodes else 100
            pobj: Any = HeuristicPolicy(cfg, TEAM_RATINGS, n_ticks=n_ticks, max_bets=max_bets_per_match)
        elif pol == "random":
            pobj = RandomPolicy(seed=seed, n_actions=ACTION_SPACE_SIZE)
        elif pol == "value":
            from strategy.fair_value import FairValueModel, family_of
            from strategy.value_policy import ValuePolicy

            fam = family_of(test_episodes[0][0].get("match_format") if test_episodes else None)
            fv_path = Path("models/fair_value") / f"{fam}.joblib"
            if not fv_path.exists():
                raise FileNotFoundError(
                    f"No fair-value model at {fv_path}; run scripts/train_fair_value.py --key {specialist_key}"
                )
            pobj = ValuePolicy(FairValueModel.load(fv_path), commission=commission, max_bets=max_bets_per_match)
        else:
            pobj = HoldPolicy()
        return _run_policy(
            lambda obs, tick, env, step: (pobj.predict(obs, tick, env), None),
            pobj.reset, specialist_key, test_episodes,
            max_bets_per_match, commission, starting_bankroll, version,
        )

    raw = run(policy)
    metrics = _summarize_run(raw, specialist_key, format_name, policy, starting_bankroll,
                             holdout_hash, code_version, seed)
    if include_baselines:
        for name in ("random", "hold"):
            if name == policy:
                continue
            b = _summarize_run(run(name), specialist_key, format_name, name, starting_bankroll,
                               holdout_hash, code_version, seed)
            metrics.baselines[name] = asdict(b)
        if policy == "random":
            metrics.edge_vs_random = 0.0
        elif "random" in metrics.baselines:
            metrics.edge_vs_random = round(
                metrics.mean_match_pnl - metrics.baselines["random"]["mean_match_pnl"], 2
            )
    return metrics, raw["bet_rows"]


# ---------------------------------------------------------------------------
# Training regime (S09): config, chronological split, vec envs, early stopping
# ---------------------------------------------------------------------------

CONFIG_PATH = Path("configs/specialists.yaml")
DEFAULT_MODELS_DIR = Path("models/specialists/candidates")
TRAIN_COMMISSION = 0.02
TRAIN_BANKROLL = 100_000.0
SMOKE_TIMESTEPS = 2_000
SMOKE_MATCHES = 20


def load_training_config(key: str, path: Path = CONFIG_PATH) -> Dict[str, Any]:
    """Merge built-in defaults < yaml ``defaults`` < yaml ``specialists[key]``."""
    import yaml

    cfg: Dict[str, Any] = {
        "gamma": 0.99, "lr": 3e-4, "ent_coef": 0.01, "n_steps": 2048, "batch_size": 64,
        "n_epochs": 5, "net_arch": {"pi": [256, 256, 128], "vf": [256, 256, 128]},
        "timesteps_per_match": 300, "epochs_over_data": 3, "n_envs": 8, "patience": 5,
    }
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        cfg.update(raw.get("defaults") or {})
        cfg.update((raw.get("specialists") or {}).get(key) or {})
    return cfg


def _match_sort_key(m: Dict[str, Any]) -> Tuple[str, str]:
    start = m.get("start_time") or ""
    if not start and m.get("ticks"):
        start = _parse_tick_time(m["ticks"][0]).isoformat()
    return (str(start), str(m.get("match_id", "")))


def split_matches(
    matches: List[Dict[str, Any]], key: str, corpus_dir: Optional[Path] = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Chronological train/val/holdout split (oldest 70% / next 15% / newest 15%).

    If S07's manifest ``splits_<key>.json`` exists its ``holdout_ids`` / ``val_ids`` are used
    (``pending_ids`` are excluded from train); everything else is train. Never shuffles.
    """
    ordered = sorted(matches, key=_match_sort_key)
    manifest = (corpus_dir or CORPUS_DIR) / f"splits_{KEY_ALIASES.get(key, key)}.json"
    if manifest.exists():
        sp = json.loads(manifest.read_text(encoding="utf-8"))
        hold_ids, val_ids = set(sp.get("holdout_ids", [])), set(sp.get("val_ids", []))
        pending = set(sp.get("pending_ids", []))
        holdout = [m for m in ordered if m.get("match_id") in hold_ids]
        val = [m for m in ordered if m.get("match_id") in val_ids]
        train = [m for m in ordered if m.get("match_id") not in (hold_ids | val_ids | pending)]
        return train, val, holdout
    n = len(ordered)
    n_train, n_val = int(n * 0.70), int(n * 0.15)
    return ordered[:n_train], ordered[n_train:n_train + n_val], ordered[n_train + n_val:]


def _ids_hash(matches: List[Dict[str, Any]]) -> str:
    return hashlib.sha1("|".join(str(m.get("match_id", "")) for m in matches).encode("utf-8")).hexdigest()[:12]


def _make_train_env(
    episodes: List[List[Dict[str, Any]]], max_bets: int, bankroll: float, commission: float
) -> CricketBettingEnv:
    """Picklable env factory target (module level so SubprocVecEnv can spawn it on Windows)."""
    return CricketBettingEnv(
        data=episodes,
        initial_bankroll=bankroll,
        settlement_mode="real",
        max_bets_per_episode=max_bets,
        exchange_commission=commission,
        episode_sampling="random",
    )


def build_vec_env(
    episodes: List[List[Dict[str, Any]]],
    n_envs: int,
    seed: int,
    max_bets: int,
    backend: str = "auto",
    bankroll: float = TRAIN_BANKROLL,
    commission: float = TRAIN_COMMISSION,
) -> Any:
    """VecNormalize(VecMonitor(...)) over ``n_envs`` random-sampling envs with distinct seeds."""
    from functools import partial

    from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv, VecMonitor, VecNormalize

    fns = [partial(_make_train_env, episodes, max_bets, bankroll, commission) for _ in range(n_envs)]
    venv: Any = None
    if backend != "dummy" and n_envs > 1:
        try:
            venv = SubprocVecEnv(fns, start_method="spawn")
        except Exception as exc:  # pragma: no cover - platform dependent
            logger.warning("subproc_vec_env_failed", error=str(exc))
    if venv is None:
        venv = DummyVecEnv(fns)
    venv.seed(seed)  # SB3 seeds env i with seed + i
    venv = VecMonitor(venv)
    return VecNormalize(venv, norm_obs=True, norm_reward=True, clip_obs=10.0)


class _NormPolicy:
    """Policy view that normalises raw observations (for the S02 confidence helper)."""

    def __init__(self, model: Any, norm: Callable[[np.ndarray], np.ndarray]) -> None:
        self._model, self._norm = model, norm

    def obs_to_tensor(self, obs: np.ndarray) -> Any:
        return self._model.policy.obs_to_tensor(self._norm(obs))

    def get_distribution(self, obs_t: Any) -> Any:
        return self._model.policy.get_distribution(obs_t)


class NormalizedModel:
    """Adapts an SB3 PPO + VecNormalize obs stats to the raw-observation ``predict`` the evaluator uses."""

    def __init__(self, model: Any, vecnorm: Any) -> None:
        self.model, self.vecnorm = model, vecnorm
        self.policy = _NormPolicy(model, self._norm)

    def _norm(self, obs: np.ndarray) -> np.ndarray:
        if self.vecnorm is None:
            return obs
        return self.vecnorm.normalize_obs(np.asarray(obs, dtype=np.float32))

    def predict(self, obs: np.ndarray, deterministic: bool = True) -> Tuple[Any, Any]:
        return self.model.predict(self._norm(obs), deterministic=deterministic)


def make_val_early_stopping(
    evaluate: Callable[[Any], float], eval_freq: int, patience: int, ckpt_dir: Path, vecnorm: Any
) -> Any:
    """SB3 callback: val-evaluate every ``eval_freq`` steps, keep best checkpoint, stop on stale patience."""
    from stable_baselines3.common.callbacks import BaseCallback

    class _CB(BaseCallback):
        def __init__(self) -> None:
            super().__init__()
            self.best = -float("inf")
            self.stale = 0
            self.n_evals = 0
            self.history: List[Dict[str, float]] = []
            self._next = int(eval_freq)

        def _on_step(self) -> bool:
            if self.num_timesteps < self._next:
                return True
            self._next += int(eval_freq)
            score = float(evaluate(self.model))
            self.n_evals += 1
            self.history.append({"timesteps": self.num_timesteps, "val_mean_match_pnl": score})
            if score > self.best:
                self.best, self.stale = score, 0
                self.model.save(str(ckpt_dir / "best_model.zip"))
                vecnorm.save(str(ckpt_dir / "best_vecnorm.pkl"))
            else:
                self.stale += 1
            print(f"  [val] step={self.num_timesteps:,} mean_match_pnl={score:+.2f} "
                  f"best={self.best:+.2f} stale={self.stale}/{patience}")
            return self.stale < patience

    return _CB()


def train_single_specialist(
    specialist_key: str,
    format_name: str,
    timesteps: Optional[int] = None,
    target_bets_per_match: int = 25,
    eval_only: bool = False,
    *,
    models_dir: Optional[Path] = None,
    smoke: bool = False,
    max_timesteps: Optional[int] = None,
    n_envs: Optional[int] = None,
    seed: int = 0,
    backend: str = "auto",
    eval_freq: Optional[int] = None,
    overrides: Optional[Dict[str, Any]] = None,
    matches: Optional[List[Dict[str, Any]]] = None,
    max_val_matches: int = 40,
) -> Tuple[SpecialistEvaluationMetrics, List[Dict[str, Any]]]:
    """Train (with val early stopping) and evaluate one specialist; writes only to ``models_dir``.

    Budget: ``timesteps_per_match * n_train * epochs_over_data`` unless ``timesteps`` is given;
    capped by ``max_timesteps``. The holdout is evaluated exactly once, on the selected checkpoint.
    """
    import pickle
    import random
    import shutil
    import tempfile

    from stable_baselines3.common.utils import set_random_seed

    print("\n" + "=" * 80)
    print(f" {'EVALUATING' if eval_only else 'TRAINING'} {specialist_key.upper()} SPECIALIST ({format_name})")
    print(f" Strategy: Two-Way Rate Trading | Budget: $100k/match | Target Bets: {target_bets_per_match}/match")
    print("=" * 80)

    cfg = load_training_config(specialist_key)
    cfg.update({k: v for k, v in (overrides or {}).items() if v is not None})
    models_dir = Path(models_dir or DEFAULT_MODELS_DIR)
    models_dir.mkdir(parents=True, exist_ok=True)
    model_path = models_dir / f"{specialist_key}_v1.zip"
    vecnorm_path = PhoenixAgent.default_normalize_path(model_path)
    manifest_path = models_dir / f"{specialist_key}_train_manifest.json"

    if matches is None:
        matches = load_corpus(specialist_key)
    train_m, val_m, hold_m = split_matches(matches, specialist_key)
    if smoke:
        train_m, val_m, hold_m = train_m[-SMOKE_MATCHES:], val_m[:8], hold_m[:10]
    train_eps = [m["ticks"] for m in train_m]
    val_eps = [m["ticks"] for m in val_m][:max_val_matches]
    hold_eps = [m["ticks"] for m in hold_m][:75]
    train_ticks = sum(len(e) for e in train_eps)
    print(f"Split (chronological): {len(train_m)} Train | {len(val_m)} Val | {len(hold_m)} Holdout")

    n_envs = int(n_envs or (2 if smoke else cfg["n_envs"]))
    if timesteps is not None:
        planned = int(timesteps)
    elif smoke:
        planned = SMOKE_TIMESTEPS
    else:
        planned = int(cfg["timesteps_per_match"]) * len(train_eps) * int(cfg["epochs_over_data"])
    if max_timesteps:
        planned = min(planned, int(max_timesteps))
    print(f"Budget: {len(train_eps)} matches, {train_ticks:,} ticks -> {planned:,} planned timesteps "
          f"({n_envs} envs, seed={seed})")

    random.seed(seed)
    np.random.seed(seed)
    set_random_seed(seed)

    best_val: Optional[float] = None
    steps_run = 0
    history: List[Dict[str, float]] = []
    vecnorm_obj: Any = None

    if eval_only and model_path.exists():
        print(f"Loading existing checkpoint: {model_path} for holdout evaluation...")
        final_model: Any = PPO.load(str(model_path))
        if vecnorm_path.exists():
            with open(vecnorm_path, "rb") as f:
                vecnorm_obj = pickle.load(f)
    else:
        venv = build_vec_env(train_eps, n_envs, seed, target_bets_per_match, backend)
        agent = PhoenixAgent(
            venv,
            learning_rate=float(cfg["lr"]), n_steps=int(cfg["n_steps"]), batch_size=int(cfg["batch_size"]),
            n_epochs=int(cfg["n_epochs"]), gamma=float(cfg["gamma"]), ent_coef=float(cfg["ent_coef"]),
            net_arch=cfg["net_arch"], n_envs=n_envs, seed=seed,
        )
        agent.model.verbose = 0
        ckpt_dir = Path(tempfile.mkdtemp(prefix=f"s09_{specialist_key}_"))

        def val_score(model: Any) -> float:
            vm, _ = evaluate_specialist_holdout(
                model=NormalizedModel(model, venv), specialist_key=specialist_key,
                format_name=format_name, test_episodes=val_eps,
                max_bets_per_match=target_bets_per_match, commission=TRAIN_COMMISSION,
                starting_bankroll=TRAIN_BANKROLL, include_baselines=False, seed=seed,
            )
            return vm.mean_match_pnl

        freq = int(eval_freq or max(planned // 10, 1_000))
        cb = make_val_early_stopping(val_score, freq, int(cfg["patience"]), ckpt_dir, venv)
        print(f"Executing PPO training for up to {planned:,} timesteps (eval every {freq:,})...")
        try:
            agent.model.learn(total_timesteps=planned, callback=cb, progress_bar=False)
            steps_run = int(agent.model.num_timesteps)
            if cb.n_evals == 0:
                best_val = val_score(agent.model)  # ensure a selected checkpoint exists
                agent.model.save(str(ckpt_dir / "best_model.zip"))
                venv.save(str(ckpt_dir / "best_vecnorm.pkl"))
            else:
                best_val = cb.best
            history = cb.history
            shutil.copyfile(ckpt_dir / "best_model.zip", model_path)
            shutil.copyfile(ckpt_dir / "best_vecnorm.pkl", vecnorm_path)
        finally:
            venv.close()
            shutil.rmtree(ckpt_dir, ignore_errors=True)
        print(f"Training phase complete ({steps_run:,} steps). Best checkpoint saved: {model_path}")
        final_model = PPO.load(str(model_path))
        with open(vecnorm_path, "rb") as f:
            vecnorm_obj = pickle.load(f)

    # Holdout is evaluated exactly once, on the selected checkpoint.
    print(f"\nEvaluating strictly causal (zero-lookahead) on {len(hold_eps)} unseen holdout test matches...")
    metrics, bet_records = evaluate_specialist_holdout(
        model=NormalizedModel(final_model, vecnorm_obj),
        specialist_key=specialist_key,
        format_name=format_name,
        test_episodes=hold_eps,
        max_bets_per_match=target_bets_per_match,
        commission=TRAIN_COMMISSION,
        starting_bankroll=TRAIN_BANKROLL,
    )
    metrics.matches_trained = len(train_m)
    metrics.timesteps = steps_run

    if not eval_only:
        manifest = {
            "specialist_key": specialist_key,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "config": dict(cfg),
            "seed": seed,
            "n_envs": n_envs,
            "data_hash": f"{_ids_hash(matches)}:{sum(len(m['ticks']) for m in matches)}",
            "split_ids_hash": {"train": _ids_hash(train_m), "val": _ids_hash(val_m), "holdout": _ids_hash(hold_m)},
            "split_sizes": {"train": len(train_m), "val": len(val_m), "holdout": len(hold_m)},
            "git_sha": _code_version(),
            "planned_timesteps": planned,
            "timesteps_run": steps_run,
            "best_val_mean_match_pnl": best_val,
            "val_history": history,
            "holdout_metrics": asdict(metrics),
            "model_path": str(model_path),
            "vecnorm_path": str(vecnorm_path),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        print(f"Train manifest written: {manifest_path}")

    print(f"\nResults for {specialist_key.upper()} Specialist:")
    print(f"  - Mean match P&L: ${metrics.mean_match_pnl:+,.2f} "
          f"(95% CI {metrics.pnl_ci95_low:+,.2f}..{metrics.pnl_ci95_high:+,.2f})")
    print(f"  - Edge vs random: {metrics.edge_vs_random}")
    print(f"  - Win Rate:       {metrics.win_rate_pct:.1f}%")
    print(f"  - Total P&L:      ${metrics.total_pnl:+,.2f}")
    print(f"  - Sharpe Ratio:   {metrics.sharpe_ratio:.2f}")
    print(f"  - Bets / Match:   {metrics.avg_bets_per_match:.1f} (Total Bets: {metrics.total_bets})")

    # Candidates are NOT registered in the model zoo: promotion to champion is S07's job.
    return metrics, bet_records


async def persist_virtual_bets(bets: List[Dict[str, Any]]) -> int:
    """Store holdout-evaluation bets in TimescaleDB, tagged via agent_version="holdout_eval:...".

    Returns the number of rows actually inserted (conflicting PKs are skipped and logged).
    """
    if not bets:
        return 0
    print(f"\n[DB] Storing {len(bets)} holdout-evaluation bets in TimescaleDB...")
    inserted = 0
    try:
        async with get_session() as session:
            for i, b in enumerate(bets):
                row = dict(b)
                # PK is (placed_at, match_id): make placed_at unique per bet.
                row["placed_at"] = row["placed_at"] + timedelta(microseconds=i)
                result = await session.execute(
                    text("""
                        INSERT INTO virtual_bets (
                            placed_at, match_id, action, team, odds, stake, confidence,
                            settled_at, outcome, profit_loss, closing_odds, clv,
                            agent_version, observation_hash, reward
                        ) VALUES (
                            :placed_at, :match_id, :action, :team, :odds, :stake, :confidence,
                            :settled_at, :outcome, :profit_loss, :closing_odds, :clv,
                            :agent_version, 'holdout_eval', :reward
                        ) ON CONFLICT DO NOTHING
                    """),
                    row,
                )
                inserted += max(0, result.rowcount or 0)
            await session.commit()
        logger.info("holdout_bets_persisted", attempted=len(bets), inserted=inserted)
        print(f"     Virtual bets recorded: {inserted}/{len(bets)} inserted.")
    except Exception as e:
        print(f"     Warning: database insert skipped ({e})")
    return inserted


async def update_redis_model_zoo_state(all_metrics: Dict[str, SpecialistEvaluationMetrics]) -> None:
    """Update Redis keys with model zoo status."""
    print("\n[REDIS] Publishing Model Zoo state to Redis...")
    try:
        redis = await get_redis()
        
        zoo_summary = {
            "specialists_active": len(all_metrics),
            "models": {k: asdict(m) for k, m in all_metrics.items()},
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        
        await redis.set_json("phoenix:model_zoo:status", zoo_summary)
        
        total_pnl = sum(m.total_pnl for m in all_metrics.values())
        avg_win_rate = np.mean([m.win_rate_pct for m in all_metrics.values()])
        avg_sharpe = np.mean([m.sharpe_ratio for m in all_metrics.values()])
        
        await redis.set_json(KEY_AGENT_VERSION, {
            "version": "v3_specialist_zoo",
            "strategy_name": f"Format Specialist Model Zoo ({len(all_metrics)} Models Active)",
            "specialists": list(all_metrics.keys()),
            "total_pnl": round(float(total_pnl), 2),
            "avg_win_rate": round(float(avg_win_rate), 2),
            "trained_at": datetime.now(timezone.utc).isoformat(),
        })

        await redis.set_json(KEY_ORCHESTRATOR_STATS, {
            "strategy": "Format Specialist Model Zoo",
            "total_pnl": round(float(total_pnl), 2),
            "win_rate_pct": round(float(avg_win_rate), 2),
            "sharpe_ratio": round(float(avg_sharpe), 2),
            "specialists_count": len(all_metrics),
            "total_matches_trained": sum(m.matches_trained for m in all_metrics.values()),
            "total_matches_tested": sum(m.matches_tested for m in all_metrics.values()),
        })
        print("        Redis state successfully synchronized with Model Zoo!")
    except Exception as e:
        print(f"        Warning: Redis sync skipped ({e})")


async def main():
    parser = argparse.ArgumentParser(description="Train PHOENIX Format-Specialist Model Zoo.")
    parser.add_argument(
        "--specialist",
        type=str,
        default="all",
        help="Specialist to train (e.g. 'all', 'ipl', 't20i_men', 't20i_women', 'bbl_men', 'wbbl_women', 'odi_men', 'odi_women', 'test_men', etc.)",
    )
    parser.add_argument("--timesteps", type=int, default=None, help="Override planned timesteps (default: timesteps_per_match x train matches x epochs)")
    parser.add_argument("--max-timesteps", type=int, default=None, help="Cap on planned timesteps")
    parser.add_argument("--smoke", action="store_true", help="Tiny run: 2,000 steps, 20 train matches; skips DB/Redis")
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS_DIR, help="Output dir for candidates (never the champion path)")
    parser.add_argument("--n-envs", type=int, default=None, help="Parallel envs (default from configs/specialists.yaml)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--vec-backend", choices=["auto", "subproc", "dummy"], default=None)
    parser.add_argument("--eval-freq", type=int, default=None, help="Steps between validation evaluations")
    parser.add_argument("--lr", type=float, default=None)
    parser.add_argument("--gamma", type=float, default=None)
    parser.add_argument("--ent-coef", type=float, default=None)
    parser.add_argument("--n-steps", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--n-epochs", type=int, default=None)
    parser.add_argument("--patience", type=int, default=None)
    parser.add_argument("--target-bets", type=int, default=25, help="Target bets per match (default: 25 in 20-30 range)")
    parser.add_argument("--eval-only", action="store_true", help="Evaluate existing checkpoints on holdout without retraining")
    args = parser.parse_args()

    specialists_to_run = ALL_SPECIALISTS_CONFIG
    if args.specialist != "all":
        req = KEY_ALIASES.get(args.specialist, args.specialist)
        specialists_to_run = [c for c in ALL_SPECIALISTS_CONFIG if c["key"] == req]
        if not specialists_to_run:
            raise ValueError(f"Unknown specialist: {args.specialist}. Available: {[c['key'] for c in ALL_SPECIALISTS_CONFIG]}")

    print("=" * 80)
    print(" PHOENIX - 12-SPECIALIST MODEL ZOO PIPELINE")
    print(f" Models to {'evaluate' if args.eval_only else 'train'}: {[c['key'] for c in specialists_to_run]}")
    print(f" Timesteps: {args.timesteps if args.timesteps else 'config budget'} | Target bets/match: {args.target_bets} | Out: {args.models_dir}")
    print("=" * 80)

    all_metrics: Dict[str, SpecialistEvaluationMetrics] = {}
    all_bets: List[Dict[str, Any]] = []

    for cfg in specialists_to_run:
        key = cfg["key"]
        name = cfg["name"]
        metrics, bets = train_single_specialist(
            specialist_key=key,
            format_name=name,
            timesteps=args.timesteps,
            target_bets_per_match=args.target_bets,
            eval_only=args.eval_only,
            models_dir=args.models_dir,
            smoke=args.smoke,
            max_timesteps=args.max_timesteps,
            n_envs=args.n_envs,
            seed=args.seed,
            backend=args.vec_backend or ("dummy" if args.smoke else "auto"),
            eval_freq=args.eval_freq,
            overrides={
                "lr": args.lr, "gamma": args.gamma, "ent_coef": args.ent_coef,
                "n_steps": args.n_steps, "batch_size": args.batch_size,
                "n_epochs": args.n_epochs, "patience": args.patience,
            },
        )
        all_metrics[key] = metrics
        all_bets.extend(bets)

    if not args.smoke:
        await persist_virtual_bets(all_bets)
        await update_redis_model_zoo_state(all_metrics)

    print("\n" + "=" * 105)
    print(" 12-SPECIALIST MODEL ZOO - EXECUTIVE PERFORMANCE TEAR-SHEET (ZERO LOOKAHEAD)")
    print("=" * 105)
    print(f"{'Specialist Model':<28} | {'Matches':<8} | {'Win Rate':<10} | {'Total P&L':<14} | {'ROI':<8} | {'Sharpe':<7} | {'Bets/Match'}")
    print("-" * 105)
    for k, m in all_metrics.items():
        print(f"{m.format_name:<28} | {m.matches_tested:<8} | {m.win_rate_pct:>6.1f}%    | ${m.total_pnl:>+11,.2f} | {m.mean_roi_pct:>+5.1f}% | {m.sharpe_ratio:>5.2f} | {m.avg_bets_per_match:>5.1f}")
    print("-" * 105)
    tot_pnl = sum(m.total_pnl for m in all_metrics.values())
    avg_wr = np.mean([m.win_rate_pct for m in all_metrics.values()])
    print(f"{'PORTFOLIO TOTAL':<28} | {sum(m.matches_tested for m in all_metrics.values()):<8} | {avg_wr:>6.1f}%    | ${tot_pnl:>+11,.2f} |          |         |")
    print("=" * 105)


if __name__ == "__main__":
    asyncio.run(main())
