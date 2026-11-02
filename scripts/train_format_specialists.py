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
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

import numpy as np
import torch
from sqlalchemy import text
from stable_baselines3 import PPO

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


def load_corpus(key: str) -> List[List[Dict[str, Any]]]:
    """Load episodes for a specialist format."""
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
    
    episodes = [m["ticks"] for m in matches]
    total_ticks = sum(len(ep) for ep in episodes)
    print(f"Loaded {len(episodes)} matches ({total_ticks:,} ticks) from {fpath.name}")
    return episodes


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
                clv = (1.0 / bet.odds - 1.0 / closing) if back else (1.0 / closing - 1.0 / bet.odds)
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
    policy: Literal["model", "heuristic", "random", "hold"] = "model",
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


def train_single_specialist(
    specialist_key: str,
    format_name: str,
    timesteps: int = 8_000,
    target_bets_per_match: int = 25,
    eval_only: bool = False,
) -> Tuple[SpecialistEvaluationMetrics, List[Dict[str, Any]]]:
    """Train and evaluate one specialist model."""
    print("\n" + "=" * 80)
    print(f" {'EVALUATING' if eval_only else 'TRAINING'} {specialist_key.upper()} SPECIALIST ({format_name})")
    print(f" Strategy: Two-Way Rate Trading | Budget: $100k/match | Target Bets: {target_bets_per_match}/match")
    print("=" * 80)

    episodes = load_corpus(specialist_key)
    n_total = len(episodes)
    n_train = int(n_total * 0.70)
    n_val = int(n_total * 0.15)

    train_episodes = episodes[:n_train]
    val_episodes = episodes[n_train : n_train + n_val]
    holdout_episodes = episodes[n_train + n_val :]

    print(f"Split: {len(train_episodes)} Train | {len(val_episodes)} Val | {len(holdout_episodes)} Holdout Test")

    SPECIALISTS_DIR.mkdir(parents=True, exist_ok=True)
    model_path = SPECIALISTS_DIR / f"{specialist_key}_v1.zip"

    cfg = SPECIALIST_MAP.get(specialist_key, {})
    gamma = float(cfg.get("gamma", 0.99))
    ent_coef = float(cfg.get("ent_coef", 0.01))
    lr = float(cfg.get("lr", 3e-4))

    if eval_only and model_path.exists():
        print(f"Loading existing checkpoint: {model_path} for holdout evaluation...")
        model = PPO.load(str(model_path))
    else:
        # Train fresh model on format corpus
        train_env = CricketBettingEnv(
            data=train_episodes,
            initial_bankroll=100_000.0,
            settlement_mode="real",
            max_bets_per_episode=target_bets_per_match,
            exchange_commission=0.02,
        )

        model = PPO(
            policy="MlpPolicy",
            env=train_env,
            policy_kwargs={
                "net_arch": {"pi": [256, 256, 128], "vf": [256, 256, 128]},
                "activation_fn": torch.nn.ReLU,
            },
            learning_rate=lr,
            n_steps=256,
            batch_size=64,
            n_epochs=5,
            gamma=gamma,
            gae_lambda=0.95,
            clip_range=0.2,
            ent_coef=ent_coef,
            vf_coef=0.5,
            max_grad_norm=0.5,
            verbose=0,
        )

        print(f"Executing PPO curriculum training for {timesteps:,} timesteps...")
        model.learn(total_timesteps=timesteps, progress_bar=False)
        print("Training phase complete.")
        model.save(str(model_path))
        print(f"Specialist model checkpoint saved: {model_path}")

    # Evaluate on unseen holdout test matches (up to 75 representative holdout matches per format)
    eval_holdout = holdout_episodes[:min(75, len(holdout_episodes))]
    print(f"\nEvaluating strictly causal (zero-lookahead) on {len(eval_holdout)} unseen holdout test matches...")
    metrics, bet_records = evaluate_specialist_holdout(
        model=model,
        specialist_key=specialist_key,
        format_name=format_name,
        test_episodes=eval_holdout,
        max_bets_per_match=target_bets_per_match,
        commission=0.02,
        starting_bankroll=100_000.0,
    )
    metrics.matches_trained = len(train_episodes)
    metrics.timesteps = timesteps

    print(f"\nResults for {specialist_key.upper()} Specialist:")
    print(f"  - Win Rate:       {metrics.win_rate_pct:.1f}%")
    print(f"  - Total P&L:      ${metrics.total_pnl:+,.2f}")
    print(f"  - Mean ROI:       {metrics.mean_roi_pct:+.2f}%")
    print(f"  - Sharpe Ratio:   {metrics.sharpe_ratio:.2f}")
    print(f"  - Bets / Match:   {metrics.avg_bets_per_match:.1f} (Total Bets: {metrics.total_bets})")
    print(f"  - Hedge Rate:     {metrics.hedge_rate_pct:.1f}%")
    print(f"  - Profit Factor:  {metrics.profit_factor:.2f}")

    # Register in ModelZoo metadata
    zoo = get_model_zoo()
    zoo.register_specialist(
        key=specialist_key,
        model_path=model_path,
        format_name=format_name,
        matches_count=len(train_episodes) + len(val_episodes) + len(holdout_episodes),
        metrics=asdict(metrics),
    )

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
    parser.add_argument("--timesteps", type=int, default=8_000, help="Timesteps per specialist (default: 8,000)")
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
    print(f" Timesteps per model: {args.timesteps:,} | Target bets/match: {args.target_bets}")
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
        )
        all_metrics[key] = metrics
        all_bets.extend(bets)

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
