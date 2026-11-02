"""Walk-forward real-price backtest driver (S39).

Usage:
    python scripts/run_real_backtest.py --strategy value --family t20 [--execution manual|automated]
        [--model-version v2|v1]

Folds are by season. The value strategy fits a fair model per fold on earlier seasons only.
S43: the value strategy is the live ``ValueStrategy`` object (no wrapper), and with the v2 model every tick
carries the pre-match ``pm_*`` fields from ``strategy.ratings`` as of the match date, as in live trading.
Output: docs/reports/real_backtest_<strategy>_<family>.json (per-fold and pooled statistics,
R0 sanity block, segment breakdowns, warnings). Every report defaults to the ``manual``
execution preset (5 s decision lag, 60 s to place the bet), because bets are placed by hand.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backtesting.baselines import HoldPolicy, RandomPolicy  # noqa: E402
from backtesting.execution import ExecutionModel  # noqa: E402
from backtesting.real_backtester import (  # noqa: E402
    DEFAULT_BANKROLL,
    DriftMomentum,
    FadeTheJump,
    PolicyStrategy,
    assert_walk_forward,
    fold_metadata,
    r0_check,
    run,
    summarise,
    walk_forward_folds,
)
from scraper.match_filter import MatchClassifier  # noqa: E402
from scripts.build_real_price_corpus import DEFAULT_OUT, load_real_corpus  # noqa: E402
from scripts.cricsheet_harmonizer import canonicalize_team  # noqa: E402
from shared.logging import setup_logging  # noqa: E402
from shared.schemas import OddsEvent  # noqa: E402
from strategy.fair_value import FairValueModel  # noqa: E402
from strategy.live_context import DEFAULT_CACHE, DEFAULT_DATA_ROOT, load_ratings, prematch_fields  # noqa: E402
from strategy.value_policy import ValueStrategy  # noqa: E402

logger = setup_logging("run_real_backtest")

STRATEGIES = ("value", "hold", "random", "fade_jump", "drift")
FAMILIES = ("t20", "odi", "multiday")
MODEL_VERSIONS = ("v1", "v2")
REPORT_DIR = _REPO_ROOT / "docs" / "reports"
STATE_FIELDS = ("score", "innings", "balls_remaining", "run_rate")
_EPISODE_META = ("match_id", "team_home", "team_away", "family", "match_format", "competition")


def competition_tier(episode: dict[str, Any], classifier: MatchClassifier) -> str:
    """Same tier rule the corpus builder uses for ``is_major``."""
    event = OddsEvent(
        match_id=episode["match_id"],
        timestamp=datetime.fromisoformat(str(episode["start"])),
        team_home=episode["team_home"],
        team_away=episode["team_away"],
        competition=episode.get("competition", ""),
    )
    return "international_or_major" if classifier.is_international_or_major(event) else "other"


def enrich_episodes(
    episodes: Sequence[dict[str, Any]],
    ratings: Any,
    *,
    normalize_team: Optional[Any] = canonicalize_team,
) -> list[dict[str, Any]]:
    """Attach the v2 ``pm_*`` fields to every tick, as of each match's date (S43). Done in place.

    An episode is one match, so its fields are computed once (from the match metadata, dated at its start) and
    copied to every tick. Multi-day episodes have no rated family and are left unchanged. The episodes passed in
    are modified, so pass freshly loaded data.
    """
    for ep in episodes:
        ticks = ep.get("ticks") or []
        if not ticks:
            continue
        head = {k: ep[k] for k in _EPISODE_META if ep.get(k) is not None}
        head["time"] = ep.get("start") or ticks[0]["time"]
        fields = prematch_fields(head, ratings, normalize_team=normalize_team, as_of=str(head["time"])[:10])
        if fields:
            for tick in ticks:
                tick.update(fields)
    return list(episodes)


def build_strategy(
    name: str,
    train: Sequence[dict[str, Any]],
    family: str,
    execution: ExecutionModel,
    seed: int,
    *,
    model_version: str = "v2",
) -> tuple[Optional[Any], Optional[list[str]], Optional[str]]:
    """Return ``(strategy, fitted_match_ids, skip_reason)`` for one fold.

    ``value`` is the live ``ValueStrategy`` over a fair model fitted on the fold's training matches (S43). It
    uses no execution-specific settings, so backtest and live use the same commission and thresholds. ``execution``
    is kept for the other strategies and for the call signature.
    """
    if name == "value":
        if model_version not in MODEL_VERSIONS:
            raise ValueError(f"unknown model version {model_version!r}; expected one of {MODEL_VERSIONS}")
        model = FairValueModel(family=family, seed=seed, version=model_version)
        try:
            model.fit([ep["ticks"] for ep in train])
        except ValueError as exc:
            return None, None, f"fair_model_fit_failed: {exc}"
        # Decide with the same commission the execution model charges, so signals only clear real costs
        return ValueStrategy(model, commission=execution.commission), list(model.train_match_ids), None
    if name == "hold":
        return PolicyStrategy(HoldPolicy(), name="hold"), [], None
    if name == "random":
        return PolicyStrategy(RandomPolicy(seed=seed), name="random"), [], None
    if name == "fade_jump":
        return FadeTheJump(), [], None
    if name == "drift":
        return DriftMomentum(), [], None
    raise ValueError(f"unknown strategy {name!r}")


def corpus_warnings(episodes: Sequence[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    if episodes and not any(f in episodes[0]["ticks"][0] for f in STATE_FIELDS):
        warnings.append(
            "corpus ticks carry no match-state fields (score, innings, balls_remaining, run_rate); "
            "the fair model sees constant features until S36 alignment is in place"
        )
    return warnings


def run_walk_forward(
    episodes: Sequence[dict[str, Any]],
    strategy_name: str,
    family: str,
    execution: ExecutionModel,
    *,
    bankroll: float = DEFAULT_BANKROLL,
    n_bootstrap: int = 10_000,
    seed: int = 0,
    model_version: str = "v2",
    ratings: Any = None,
) -> dict[str, Any]:
    """Walk-forward run for one strategy: per-fold and pooled statistics plus the R0 block.

    The v2 value strategy needs ``ratings`` (``strategy.live_context.load_ratings``); its episodes are enriched
    with ``pm_*`` in place before folding.
    """
    if strategy_name == "value" and model_version == "v2":
        if ratings is None:
            raise ValueError("the v2 value strategy needs cricket ratings; pass strategy.live_context.load_ratings()")
        episodes = enrich_episodes(episodes, ratings)
    folds = walk_forward_folds(episodes)
    fold_reports: list[dict[str, Any]] = []
    pooled_matches: list[dict[str, Any]] = []
    pooled_bets: list[dict[str, Any]] = []
    test_episodes: list[dict[str, Any]] = []

    for fold in folds:
        meta = fold_metadata(fold)
        strategy, fitted, skip = build_strategy(
            strategy_name, fold.train, family, execution, seed, model_version=model_version,
        )
        if strategy is None:
            fold_reports.append({**_fold_header(meta), "skipped": skip})
            logger.warning("fold_skipped", season=fold.test_season, reason=skip)
            continue
        assert_walk_forward(meta, fitted)
        result = run(strategy, fold.test, execution, bankroll=bankroll)
        fold_reports.append({
            **_fold_header(meta),
            "skipped": None,
            "summary": summarise(result, n_bootstrap=n_bootstrap),
        })
        pooled_matches.extend(result["matches"])
        pooled_bets.extend(result["bets"])
        test_episodes.extend(fold.test)

    pooled = summarise({"matches": pooled_matches, "bets": pooled_bets}, n_bootstrap=n_bootstrap)

    r0: dict[str, Any] = {}
    for label, strategy in (
        ("random", PolicyStrategy(RandomPolicy(seed=seed), name="random")),
        ("hold", PolicyStrategy(HoldPolicy(), name="hold")),
    ):
        r0_result = run(strategy, test_episodes, execution, bankroll=bankroll)
        r0[label] = summarise(r0_result, n_bootstrap=n_bootstrap)
    r0_block = r0_check(r0["random"])

    return {
        "strategy": strategy_name,
        "family": family,
        "model_version": model_version if strategy_name == "value" else None,
        "prematch_fields": ratings is not None and strategy_name == "value" and model_version == "v2",
        "execution": {"name": execution.name, **_execution_params(execution)},
        "bankroll": bankroll,
        "n_episodes": len(episodes),
        "folds": fold_reports,
        "pooled": pooled,
        "r0": {"summaries": r0, **r0_block},
        "warnings": corpus_warnings(episodes),
    }


def _fold_header(meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "test_season": meta["test_season"],
        "train_seasons": meta["train_seasons"],
        "n_train": meta["n_train"],
        "n_test": meta["n_test"],
    }


def _execution_params(execution: ExecutionModel) -> dict[str, Any]:
    return {
        "latency_s": execution.latency_s,
        "bet_delay_s": execution.bet_delay_s,
        "commission": execution.commission,
        "spread_ticks_basic": execution.spread_ticks_basic,
        "max_volume_frac": execution.max_volume_frac,
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--strategy", choices=STRATEGIES, required=True)
    parser.add_argument("--family", choices=FAMILIES, required=True)
    parser.add_argument("--execution", choices=("manual", "automated"), default="manual")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_OUT, help="real-price corpus directory")
    parser.add_argument("--model-version", choices=MODEL_VERSIONS, default="v2",
                        help="value strategy fair model: v2 (with pre-match ratings) or v1 (match state only)")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT, help="Cricsheet root for the ratings")
    parser.add_argument("--ratings-cache", type=Path, default=DEFAULT_CACHE, help="cached ratings (joblib)")
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    parser.add_argument("--bankroll", type=float, default=DEFAULT_BANKROLL)
    parser.add_argument("--n-bootstrap", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=0)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    execution = ExecutionModel.preset(args.execution)
    episodes = load_real_corpus(family=args.family, out=args.corpus)
    if not episodes:
        logger.error("no_episodes", family=args.family, corpus=str(args.corpus))
        return 1

    classifier = MatchClassifier()
    for episode in episodes:
        episode["competition_tier"] = competition_tier(episode, classifier)

    ratings = None
    if args.strategy == "value" and args.model_version == "v2":
        ratings = load_ratings(args.data_root, cache=args.ratings_cache)

    report = run_walk_forward(
        episodes,
        args.strategy,
        args.family,
        execution,
        bankroll=args.bankroll,
        n_bootstrap=args.n_bootstrap,
        seed=args.seed,
        model_version=args.model_version,
        ratings=ratings,
    )
    args.report_dir.mkdir(parents=True, exist_ok=True)
    path = args.report_dir / f"real_backtest_{args.strategy}_{args.family}.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    pooled = report["pooled"]
    logger.info(
        "real_backtest_done",
        strategy=args.strategy,
        family=args.family,
        execution=execution.name,
        matches=pooled["n_matches"],
        bets=pooled["n_bets"],
        r0_flagged=report["r0"]["flagged"],
        report=str(path),
    )
    if report["r0"]["flagged"]:
        logger.warning("r0_flag_raised", reason=report["r0"]["reason"])
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
