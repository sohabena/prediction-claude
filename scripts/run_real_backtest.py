"""Walk-forward real-price backtest driver (S39).

Usage:
    python scripts/run_real_backtest.py --strategy value --family t20 [--execution manual|automated]
        [--model-version v2|v1] [--corpus recorded|betfair] [--min-train 30]

S46: ``--corpus recorded`` (default) reads our own LotusBook recordings (``data/recorded_real``, written by
``export_recorded_corpus.py --format real``). Folds are by season when the data allows it, otherwise
expanding-window by match date (train on earlier matches only, test on the next block of matches).

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
from dataclasses import dataclass, field
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
    WalkForwardLeakError,
    assert_walk_forward,
    fold_metadata,
    r0_check,
    run,
    summarise,
    walk_forward_folds,
)
from scraper.match_filter import MatchClassifier  # noqa: E402
from scripts.build_real_price_corpus import DEFAULT_OUT, load_real_corpus  # noqa: E402
from scripts.export_recorded_corpus import RECORDED_REAL_DIR, load_recorded_real  # noqa: E402
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
CORPORA = ("recorded", "betfair")
EXPANDING_BLOCK = 10  # matches per test block in the expanding-window fallback (S46)


@dataclass(frozen=True)
class DatedFold:
    """Expanding-window fold (S46): train on every match dated before the test block, test on the block."""

    label: str
    train: tuple[dict[str, Any], ...] = field(repr=False)
    test: tuple[dict[str, Any], ...] = field(repr=False)


def _start(episode: dict[str, Any]) -> str:
    return str(episode.get("start") or episode["ticks"][0]["time"])


def expanding_folds(
    episodes: Sequence[dict[str, Any]], *, min_train: int, block: int = EXPANDING_BLOCK,
) -> list[DatedFold]:
    """Expanding window by match date. Matches are sorted by start; the first ``min_train`` only train.

    Because the order is by start time, every training match starts no later than any test match in its block.
    """
    ordered = sorted(episodes, key=lambda ep: (_start(ep), str(ep.get("match_id", ""))))
    folds: list[DatedFold] = []
    i = max(1, min_train)
    while i < len(ordered):
        test = ordered[i:i + block]
        folds.append(DatedFold(
            label=f"{_start(test[0])[:10]}..{_start(test[-1])[:10]}",
            train=tuple(ordered[:i]),
            test=tuple(test),
        ))
        i += block
    return folds


def make_folds(episodes: Sequence[dict[str, Any]], *, min_train: int) -> list[Any]:
    """Season folds where the data allows them (each with at least ``min_train`` earlier matches);
    otherwise the expanding-window fallback."""
    seasons = [f for f in walk_forward_folds(episodes, min_train_matches=min_train) if len(f.train) >= min_train]
    return seasons if seasons else expanding_folds(episodes, min_train=min_train)


def assert_no_future_training(fold: DatedFold, fitted_match_ids: Optional[Sequence[str]] = None) -> None:
    """Raise ``WalkForwardLeakError`` if training uses a match dated after any test match, or overlaps the test."""
    train_ids = {str(ep.get("match_id", "")) for ep in fold.train}
    test_ids = {str(ep.get("match_id", "")) for ep in fold.test}
    if train_ids & test_ids:
        raise WalkForwardLeakError(f"block {fold.label}: train and test share matches")
    if fold.train and max(_start(ep) for ep in fold.train) > min(_start(ep) for ep in fold.test):
        raise WalkForwardLeakError(f"block {fold.label}: training uses a match dated after a test match")
    if fitted_match_ids is not None:
        fitted = {str(m) for m in fitted_match_ids}
        if fitted - train_ids:
            raise WalkForwardLeakError(f"block {fold.label}: model fitted on matches outside training")
        if fitted & test_ids:
            raise WalkForwardLeakError(f"block {fold.label}: model fitted on test matches")


def dated_fold_metadata(fold: DatedFold) -> dict[str, Any]:
    starts_train = sorted(_start(ep) for ep in fold.train)
    starts_test = sorted(_start(ep) for ep in fold.test)
    return {
        "test_season": fold.label,
        "train_seasons": sorted({int(s[:4]) for s in starts_train}),
        "n_train": len(fold.train),
        "n_test": len(fold.test),
        "scheme": "expanding",
        "train_until": starts_train[-1][:10] if starts_train else None,
        "test_from": starts_test[0][:10],
        "test_to": starts_test[-1][:10],
    }


def corpus_summary(episodes: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """n matches per family, date range, and the share of ticks that carry score state (S46 report item 5)."""
    by_family: dict[str, int] = {}
    for ep in episodes:
        by_family[ep.get("family", "")] = by_family.get(ep.get("family", ""), 0) + 1
    ticks = [t for ep in episodes for t in ep.get("ticks", [])]
    starts = sorted(_start(ep)[:10] for ep in episodes if ep.get("ticks"))
    with_score = sum(1 for t in ticks if t.get("score") is not None)
    return {
        "n_matches": len(episodes),
        "n_matches_by_family": dict(sorted(by_family.items())),
        "date_range": {"first": starts[0] if starts else None, "last": starts[-1] if starts else None},
        "n_ticks": len(ticks),
        "share_ticks_with_score_state": round(with_score / len(ticks), 4) if ticks else 0.0,
    }


def load_corpus_episodes(
    corpus: str, family: str, *, betfair_dir: Path = DEFAULT_OUT, recorded_dir: Path = RECORDED_REAL_DIR,
) -> list[dict[str, Any]]:
    """Episodes for ``family`` from the chosen corpus (``recorded`` = our LotusBook recordings, S46)."""
    if corpus == "recorded":
        return load_recorded_real(family=family, base_dir=recorded_dir)
    if corpus == "betfair":
        return load_real_corpus(family=family, out=betfair_dir)
    raise ValueError(f"unknown corpus {corpus!r}; expected one of {CORPORA}")


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
    scheme: str = "season",
    min_train: int = 1,
) -> dict[str, Any]:
    """Walk-forward run for one strategy: per-fold and pooled statistics plus the R0 block.

    ``scheme="season"`` (betfair corpus) folds by calendar season. ``scheme="auto"`` (recorded corpus, S46) uses
    season folds when they have ``min_train`` earlier matches, otherwise expanding-window folds by match date.
    The v2 value strategy needs ``ratings`` (``strategy.live_context.load_ratings``); its episodes are enriched
    with ``pm_*`` in place before folding.
    """
    if scheme not in ("season", "auto"):
        raise ValueError(f"unknown scheme {scheme!r}; expected 'season' or 'auto'")
    if strategy_name == "value" and model_version == "v2":
        if ratings is None:
            raise ValueError("the v2 value strategy needs cricket ratings; pass strategy.live_context.load_ratings()")
        episodes = enrich_episodes(episodes, ratings)
    if scheme == "auto":
        folds = make_folds(episodes, min_train=min_train)
    else:
        folds = walk_forward_folds(episodes, min_train_matches=min_train)
    fold_reports: list[dict[str, Any]] = []
    pooled_matches: list[dict[str, Any]] = []
    pooled_bets: list[dict[str, Any]] = []
    test_episodes: list[dict[str, Any]] = []

    for fold in folds:
        dated = isinstance(fold, DatedFold)
        meta = dated_fold_metadata(fold) if dated else fold_metadata(fold)
        strategy, fitted, skip = build_strategy(
            strategy_name, fold.train, family, execution, seed, model_version=model_version,
        )
        if strategy is None:
            fold_reports.append({**_fold_header(meta), "skipped": skip})
            logger.warning("fold_skipped", test=meta["test_season"], reason=skip)
            continue
        if dated:
            assert_no_future_training(fold, fitted)
        else:
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

    # R0 has no fitted parameters, so when no fold is possible it runs on the whole corpus (mechanics only).
    r0_episodes = test_episodes if test_episodes else list(episodes)
    r0_scope = "out_of_sample_folds" if test_episodes else "all_matches_no_fold"
    r0: dict[str, Any] = {}
    for label, strategy in (
        ("random", PolicyStrategy(RandomPolicy(seed=seed), name="random")),
        ("hold", PolicyStrategy(HoldPolicy(), name="hold")),
    ):
        r0_result = run(strategy, r0_episodes, execution, bankroll=bankroll)
        r0[label] = summarise(r0_result, n_bootstrap=n_bootstrap)
    r0_block = r0_check(r0["random"])

    warnings = corpus_warnings(episodes)
    if not folds:
        warnings.append(f"no walk-forward fold: fewer than {min_train} earlier matches; pooled statistics are empty")
    return {
        "strategy": strategy_name,
        "family": family,
        "model_version": model_version if strategy_name == "value" else None,
        "prematch_fields": ratings is not None and strategy_name == "value" and model_version == "v2",
        "execution": {"name": execution.name, **_execution_params(execution)},
        "bankroll": bankroll,
        "n_episodes": len(episodes),
        "scheme": scheme,
        "min_train": min_train,
        "folds": fold_reports,
        "pooled": pooled,
        "r0": {"scope": r0_scope, "n_matches": len(r0_episodes), "summaries": r0, **r0_block},
        "warnings": warnings,
    }


def _fold_header(meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "test_season": meta["test_season"],
        "scheme": meta.get("scheme", "season"),
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
    parser.add_argument("--corpus", choices=CORPORA, default="recorded",
                        help="recorded: our LotusBook recordings (S46, default); betfair: S35 files (shelved)")
    parser.add_argument("--betfair-dir", type=Path, default=DEFAULT_OUT, help="real-price corpus directory (betfair)")
    parser.add_argument("--recorded-dir", type=Path, default=RECORDED_REAL_DIR,
                        help="recorded real-tick corpus directory (recorded)")
    parser.add_argument("--min-train", type=int, default=30,
                        help="minimum earlier matches before the first test block (expanding window)")
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
    try:
        episodes = load_corpus_episodes(
            args.corpus, args.family, betfair_dir=args.betfair_dir, recorded_dir=args.recorded_dir,
        )
    except (FileNotFoundError, ValueError) as exc:
        logger.error("corpus_load_failed", corpus=args.corpus, error=str(exc))
        return 1
    if not episodes:
        logger.error("no_episodes", family=args.family, corpus=args.corpus)
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
        scheme="auto" if args.corpus == "recorded" else "season",
        min_train=args.min_train,
    )
    report["corpus"] = {"name": args.corpus, **corpus_summary(episodes)}
    args.report_dir.mkdir(parents=True, exist_ok=True)
    suffix = "_recorded" if args.corpus == "recorded" else ""
    path = args.report_dir / f"real_backtest_{args.strategy}_{args.family}{suffix}.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    pooled = report["pooled"]
    summary = report["corpus"]
    logger.info(
        "real_backtest_done",
        strategy=args.strategy,
        family=args.family,
        corpus=args.corpus,
        execution=execution.name,
        corpus_matches=summary["n_matches"],
        date_range=summary["date_range"],
        matches=pooled["n_matches"],
        bets=pooled["n_bets"],
        r0_flagged=report["r0"]["flagged"],
        report=str(path),
    )
    if report["r0"]["flagged"]:
        logger.warning("r0_flag_raised", reason=report["r0"]["reason"])
    print(f"wrote {path}")
    print(f"corpus: {summary['n_matches']} matches, dates {summary['date_range']['first']}..{summary['date_range']['last']}, "
          f"{summary['share_ticks_with_score_state']:.1%} of ticks with score state")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
