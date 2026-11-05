"""As-of strength ratings for the fair-value model (S38).

Thin adapter over ``shared/elo.py`` (team Elo, S24) and ``shared/venue_priors.py`` (venue par and toss priors, S25),
plus three things those modules do not provide:

* a home-ground term. A team is "at home" when the venue is the one it has played at most (as of the cutoff, at
  least ``HOME_MIN_MATCHES`` earlier matches there). ``shared/elo.py`` is not changed: the term is added to the
  pre-match Elo the model reads (``pm_elo_*``), not to the tracker's update;
* player strength: exponentially decayed batting runs and bowling runs and wickets per ball, shrunk towards the
  league average with ``SHRINK_BALLS`` pseudo-balls, summed over the XI (mean batting plus mean bowling);
* ball-level state ticks for Cricsheet innings (``state_ticks``), in the same tick shape as the S14 corpus.

Every query takes a cutoff date and uses only matches dated strictly before it, so a match never sees its own
result. Cricsheet has no true home side. ``home`` is a deterministic label (parity of the match id), so the batting
order cannot leak into it; the home-ground term is the only use of "home" in the ratings.
"""
from __future__ import annotations

import bisect
import math
import zlib
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from shared.elo import CricketEloTracker, family_for_match_type
from shared.logging import setup_logging
from shared.venue_priors import VenuePriors, canonical_venue

logger = setup_logging("ratings")

HOME_ADVANTAGE_ELO = 35.0
HOME_MIN_MATCHES = 3
PLAYER_HALF_LIFE_DAYS = 730.0
SHRINK_BALLS = 120.0
WICKET_RUN_VALUE = 25.0
XI_SIZE = 11
BOWLER_WICKET_KINDS = frozenset({"bowled", "caught", "caught and bowled", "lbw", "stumped", "hit wicket"})
_EPOCH = date(2000, 1, 1).toordinal()
_ZERO5: Tuple[float, ...] = (0.0,) * 5

RawSource = Union[Iterable[Mapping[str, Any]], Callable[[], Iterable[Mapping[str, Any]]]]


def competition_tier(info: Mapping[str, Any]) -> int:
    """0 international, 1 club (franchise or league), 2 other. Cricsheet ``info.team_type`` decides."""
    team_type = str(info.get("team_type") or "").strip().lower()
    if team_type == "international":
        return 0
    if team_type == "club":
        return 1
    return 2


def home_label(match_id: str, teams: Sequence[str]) -> str:
    """Deterministic 'home' side for a Cricsheet match (not a real home side; see module docstring)."""
    return teams[0] if zlib.crc32(match_id.encode("utf-8")) % 2 == 0 else teams[1]


def _ordinal(iso: str) -> int:
    return date.fromisoformat(iso).toordinal()


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class MatchRow:
    """One Cricsheet match reduced to what the ratings and the tick generator need."""

    match_id: str
    match_type: str
    family: str
    gender: str
    date: str
    teams: Tuple[str, str]
    home: str
    away: str
    venue: str
    venue_key: str
    city: str
    tier: int
    overs: Optional[int]
    toss_winner: str
    toss_decision: str
    winner: str
    outcome: Mapping[str, Any]
    players: Mapping[str, Tuple[str, ...]]
    player_totals: Mapping[str, Tuple[float, ...]]
    league_totals: Tuple[float, ...]


def _ball_totals(innings: Sequence[Mapping[str, Any]]) -> tuple[Dict[str, Tuple[float, ...]], Tuple[float, ...]]:
    """Per-player vector (bat_runs, bat_balls, conceded, bowl_balls, bowler_wickets) and league totals."""
    totals: Dict[str, List[float]] = {}
    league = [0.0] * 5
    for inn in innings:
        if inn.get("super_over"):
            continue
        for over in inn.get("overs") or []:
            for d in over.get("deliveries") or []:
                extras = d.get("extras") or {}
                legal = 0.0 if ("wides" in extras or "noballs" in extras) else 1.0
                runs = d.get("runs") or {}
                bat = float(runs.get("batter") or 0)
                total = float(runs.get("total") or 0)
                byes = float(extras.get("byes") or 0) + float(extras.get("legbyes") or 0)
                conceded = total - byes
                wickets = float(
                    sum(1 for w in d.get("wickets") or [] if str(w.get("kind", "")).lower() in BOWLER_WICKET_KINDS)
                )
                batter = d.get("batter")
                bowler = d.get("bowler")
                if batter:
                    v = totals.setdefault(str(batter), [0.0] * 5)
                    v[0] += bat
                    v[1] += legal
                if bowler:
                    v = totals.setdefault(str(bowler), [0.0] * 5)
                    v[2] += conceded
                    v[3] += legal
                    v[4] += wickets
                league[0] += bat
                league[1] += legal
                league[2] += conceded
                league[3] += legal
                league[4] += wickets
    return {k: tuple(v) for k, v in totals.items()}, tuple(league)


def parse_row(raw: Mapping[str, Any]) -> Optional[MatchRow]:
    """Reduce one Cricsheet match (with ``raw["match_id"]``) to a :class:`MatchRow`; None if unusable."""
    info = raw.get("info") or {}
    match_id = str(raw.get("match_id") or "").strip()
    family = family_for_match_type(info.get("match_type"))
    gender = str(info.get("gender") or "").strip().lower()
    dates = info.get("dates") or []
    teams = tuple(str(t).strip() for t in (info.get("teams") or []))
    if not match_id or family is None or gender not in ("male", "female") or not dates:
        return None
    if len(teams) != 2 or teams[0] == teams[1] or not all(teams):
        return None
    try:
        match_date = date.fromisoformat(str(dates[0])[:10]).isoformat()
    except ValueError:
        return None
    outcome = dict(info.get("outcome") or {})
    winner = str(outcome.get("winner") or "").strip()
    toss = info.get("toss") or {}
    player_map = info.get("players") or {}
    players = {t: tuple(str(p) for p in (player_map.get(t) or [])[:XI_SIZE]) for t in teams}
    player_totals, league_totals = _ball_totals(raw.get("innings") or [])
    home = home_label(match_id, teams)
    venue = str(info.get("venue") or "").strip()
    return MatchRow(
        match_id=match_id,
        match_type=str(info.get("match_type") or ""),
        family=family,
        gender=gender,
        date=match_date,
        teams=(teams[0], teams[1]),
        home=home,
        away=teams[1] if home == teams[0] else teams[0],
        venue=venue,
        venue_key=canonical_venue(venue),
        city=str(info.get("city") or "").strip(),
        tier=competition_tier(info),
        overs=_int_or_none(info.get("overs")),
        toss_winner=str(toss.get("winner") or "").strip(),
        toss_decision=str(toss.get("decision") or "").strip().lower(),
        winner=winner,
        outcome=outcome,
        players=players,
        player_totals=player_totals,
        league_totals=league_totals,
    )


class _Ledger:
    """Per-key sums with optional exponential decay, queryable strictly as of a date.

    Entries are stored as cumulative sums of ``w * v`` with ``w = exp(lambda * (t - EPOCH))``. A query at
    cutoff ``c`` multiplies the prefix sum (entries dated before ``c``) by ``exp(-lambda * (c - EPOCH))``, which
    is the decayed total ``sum v * exp(-lambda * (c - t))``.
    """

    def __init__(self, half_life_days: Optional[float]) -> None:
        self._lam = math.log(2.0) / half_life_days if half_life_days else 0.0
        self._pending: Dict[tuple, List[Tuple[int, Tuple[float, ...]]]] = {}
        self._ords: Dict[tuple, List[int]] = {}
        self._cum: Dict[tuple, List[Tuple[float, ...]]] = {}

    def add(self, key: tuple, ordinal: int, vec: Tuple[float, ...]) -> None:
        self._pending.setdefault(key, []).append((ordinal, vec))

    def freeze(self) -> None:
        for key, entries in self._pending.items():
            entries.sort(key=lambda e: e[0])
            ords: List[int] = []
            cum: List[Tuple[float, ...]] = [_ZERO5]
            running = _ZERO5
            for ordinal, vec in entries:
                w = math.exp(self._lam * (ordinal - _EPOCH))
                running = tuple(a + w * b for a, b in zip(running, vec))
                ords.append(ordinal)
                cum.append(running)
            self._ords[key] = ords
            self._cum[key] = cum
        self._pending = {}

    def at(self, key: tuple, cutoff_ordinal: int) -> Tuple[float, ...]:
        ords = self._ords.get(key)
        if not ords:
            return _ZERO5
        idx = bisect.bisect_left(ords, cutoff_ordinal)
        if idx == 0:
            return _ZERO5
        scale = math.exp(-self._lam * (cutoff_ordinal - _EPOCH))
        return tuple(x * scale for x in self._cum[key][idx])


class CricketRatings:
    """Team Elo, venue priors, home ground and player strength for one set of Cricsheet matches.

    ``source`` is either an iterable of raw Cricsheet dicts (``match_id`` set) or a zero-argument factory that
    returns a fresh iterable. The factory form is streamed twice (ratings, then venue priors), so the full
    corpus never has to sit in memory.
    """

    def __init__(self, source: RawSource, *, k_by_family: Optional[Mapping[str, float]] = None) -> None:
        factory: Callable[[], Iterable[Mapping[str, Any]]] = source if callable(source) else (lambda: source)  # type: ignore[assignment]
        self.rejected: Counter = Counter()
        self.rows: Dict[str, MatchRow] = {}
        parsed: List[MatchRow] = []
        for raw in factory():
            row = parse_row(raw)
            if row is None:
                self.rejected["unusable_match"] += 1
                continue
            parsed.append(row)
        parsed.sort(key=lambda r: (r.date, r.match_id))

        self._elo = CricketEloTracker(k_by_family=k_by_family)
        self._player = _Ledger(PLAYER_HALF_LIFE_DAYS)
        self._league = _Ledger(None)
        self._venue_hist: Dict[tuple, List[Tuple[str, str]]] = {}
        self._home_cache: Dict[tuple, Optional[str]] = {}

        for row in parsed:
            if row.match_id in self.rows:
                self.rejected["duplicate_match"] += 1
                continue
            self.rows[row.match_id] = row
            try:
                self._elo.update({
                    "match_id": row.match_id,
                    "dates": [row.date],
                    "teams": list(row.teams),
                    "match_type": row.match_type,
                    "gender": row.gender,
                    "outcome": row.outcome,
                })
            except ValueError:
                self.rejected["elo_rejected"] += 1
            ordinal = _ordinal(row.date)
            for name, vec in row.player_totals.items():
                self._player.add((row.family, row.gender, name), ordinal, vec)
            self._league.add((row.family, row.gender), ordinal, row.league_totals)
            if row.venue_key:
                for team in row.teams:
                    self._venue_hist.setdefault((team, row.gender), []).append((row.date, row.venue_key))
        self._player.freeze()
        self._league.freeze()
        self._venue_dates = {k: [d for d, _ in v] for k, v in self._venue_hist.items()}
        self._priors = VenuePriors.build(factory())
        self._live_cache: Dict[tuple, Dict[str, float]] = {}
        self._unknown_logged: set[tuple] = set()

    # ------------------------------------------------------------------ home ground
    def _home_ground(self, team: str, gender: str, cutoff_iso: str) -> Optional[str]:
        key = (team, gender)
        dates = self._venue_dates.get(key)
        if not dates:
            return None
        idx = bisect.bisect_left(dates, cutoff_iso)
        if idx < HOME_MIN_MATCHES:
            return None
        cache_key = (key, idx)
        if cache_key not in self._home_cache:
            counts = Counter(v for _, v in self._venue_hist[key][:idx])
            venue, n = counts.most_common(1)[0]
            self._home_cache[cache_key] = venue if n >= HOME_MIN_MATCHES else None
        return self._home_cache[cache_key]

    # ------------------------------------------------------------------ players
    def _player_rating(self, family: str, gender: str, name: str, cutoff_ordinal: int) -> Tuple[float, float]:
        """(batting runs per ball above expectation, bowling runs saved per ball incl. wickets)."""
        v = self._player.at((family, gender, name), cutoff_ordinal)
        lg = self._league.at((family, gender), cutoff_ordinal)
        l_bat = lg[0] / lg[1] if lg[1] else 0.0
        l_conc = lg[2] / lg[3] if lg[3] else 0.0
        l_wkt = lg[4] / lg[3] if lg[3] else 0.0
        bat = (v[0] - l_bat * v[1]) / (v[1] + SHRINK_BALLS)
        bowl_runs = (l_conc * v[3] - v[2]) / (v[3] + SHRINK_BALLS)
        bowl_wkts = WICKET_RUN_VALUE * (v[4] - l_wkt * v[3]) / (v[3] + SHRINK_BALLS)
        return bat, bowl_runs + bowl_wkts

    def xi_strength(self, family: str, gender: str, names: Sequence[str], cutoff_iso: str) -> float:
        """Mean batting plus mean bowling rating over the XI (unknown players rate 0 = league average)."""
        if not names:
            return 0.0
        cutoff_ordinal = _ordinal(cutoff_iso)
        bats: List[float] = []
        bowls: List[float] = []
        for name in names[:XI_SIZE]:
            bat, bowl = self._player_rating(family, gender, name, cutoff_ordinal)
            bats.append(bat)
            bowls.append(bowl)
        return sum(bats) / len(bats) + sum(bowls) / len(bowls)

    # ------------------------------------------------------------------ public
    def pre_match(self, match_id: str, as_of: str) -> Dict[str, float]:
        """Pre-match ``pm_*`` tick fields for a match, using only information dated before ``as_of``."""
        row = self.rows[match_id]
        cutoff = date.fromisoformat(str(as_of)[:10]).isoformat()
        fam, gen = row.family, row.gender

        def at_home(team: str) -> float:
            ground = self._home_ground(team, gen, cutoff)
            return 1.0 if ground and ground == row.venue_key else 0.0

        elo_home = self._elo.rating(row.home, fam, gen, cutoff) + HOME_ADVANTAGE_ELO * at_home(row.home)
        elo_away = self._elo.rating(row.away, fam, gen, cutoff) + HOME_ADVANTAGE_ELO * at_home(row.away)
        if row.toss_winner == row.home:
            toss_home = 1.0
        elif row.toss_winner == row.away:
            toss_home = -1.0
        else:
            toss_home = 0.0
        toss_bat = 1.0 if row.toss_decision == "bat" else 0.0 if row.toss_decision == "field" else 0.5
        priors = self._priors.get(row.venue, fam, gen, cutoff, city=row.city or None)
        chase = priors["chase_win_pct"]
        return {
            "pm_elo_home": float(elo_home),
            "pm_elo_away": float(elo_away),
            "pm_xi_home": float(self.xi_strength(fam, gen, row.players.get(row.home, ()), cutoff)),
            "pm_xi_away": float(self.xi_strength(fam, gen, row.players.get(row.away, ()), cutoff)),
            "pm_toss_home": toss_home,
            "pm_toss_bat": toss_bat,
            "pm_venue_chase_pct": 50.0 if chase is None else float(chase),
            "pm_venue_n": float(priors["venue_n"]),
            "pm_tier": float(row.tier),
        }

    def pre_match_live(
        self,
        team_home: str,
        team_away: str,
        family: str,
        gender: str,
        as_of: str,
        *,
        venue: Optional[str] = None,
        city: Optional[str] = None,
        players: Optional[Mapping[str, Sequence[str]]] = None,
        toss_winner: Optional[str] = None,
        toss_decision: Optional[str] = None,
        tier: Optional[int] = None,
        normalize_team: Optional[Callable[[str], Optional[str]]] = None,
    ) -> Dict[str, float]:
        """Pre-match ``pm_*`` fields for a live match that has no Cricsheet id yet (S40).

        Same keys and units as ``pre_match``, using only information dated before ``as_of``. Team names go through
        ``normalize_team`` (the caller's canonicalizer, which may return None) and fall back to ``strip``. Home
        ground and venue priors need a known venue; XI strength needs both XIs; toss needs the winner. Anything
        missing is neutral (no information), so a live match with only teams and a date gets Elo alone. Unknown
        teams get the base Elo and are logged once. Results are cached per (teams, family, gender, date, inputs).
        """
        cutoff = date.fromisoformat(str(as_of)[:10]).isoformat()
        gen = str(gender or "").strip().lower()
        fam = str(family)

        def _name(team: Optional[str]) -> str:
            canon = normalize_team(team) if (normalize_team and team) else None
            return canon or str(team or "").strip()

        home, away = _name(team_home), _name(team_away)
        players_key = None
        if players is not None:
            players_key = tuple(sorted((_name(k), tuple(v)) for k, v in players.items()))
        cache_key = (home, away, fam, gen, cutoff, venue, city, toss_winner, toss_decision, tier, players_key)
        if cache_key in self._live_cache:
            return dict(self._live_cache[cache_key])

        def at_home(team: str, venue_key: str) -> float:
            ground = self._home_ground(team, gen, cutoff) if venue_key else None
            return 1.0 if ground and ground == venue_key else 0.0

        def elo(team: str) -> float:
            if not self._team_known(team, fam, gen):
                self._log_unknown_team(team, fam, gen)
                return self._elo.base
            return self._elo.rating(team, fam, gen, cutoff)

        venue_key = canonical_venue(venue) if venue else ""
        elo_home = elo(home) + HOME_ADVANTAGE_ELO * at_home(home, venue_key)
        elo_away = elo(away) + HOME_ADVANTAGE_ELO * at_home(away, venue_key)

        toss_name = _name(toss_winner) if toss_winner else ""
        if toss_name and toss_name == home:
            toss_home = 1.0
        elif toss_name and toss_name == away:
            toss_home = -1.0
        else:
            toss_home = 0.0
        toss_bat = 1.0 if toss_decision == "bat" else 0.0 if toss_decision == "field" else 0.5

        if venue_key:
            priors = self._priors.get(venue, fam, gen, cutoff, city=city or None)
            chase = priors["chase_win_pct"]
            venue_n = float(priors["venue_n"])
        else:
            chase, venue_n = None, 0.0

        xi_home = xi_away = 0.0
        if players:
            names_home = players.get(team_home) or players.get(home)
            names_away = players.get(team_away) or players.get(away)
            if names_home and names_away:
                xi_home = self.xi_strength(fam, gen, list(names_home), cutoff)
                xi_away = self.xi_strength(fam, gen, list(names_away), cutoff)

        out = {
            "pm_elo_home": float(elo_home),
            "pm_elo_away": float(elo_away),
            "pm_xi_home": float(xi_home),
            "pm_xi_away": float(xi_away),
            "pm_toss_home": toss_home,
            "pm_toss_bat": toss_bat,
            "pm_venue_chase_pct": 50.0 if chase is None else float(chase),
            "pm_venue_n": venue_n,
            "pm_tier": 2.0 if tier is None else float(tier),
        }
        self._live_cache[cache_key] = dict(out)
        return out

    def _team_known(self, team: str, family: str, gender: str) -> bool:
        """True when the corpus has at least one dated result for ``team`` in this family and gender."""
        try:
            key = self._elo._key(team, family, gender)
        except ValueError:
            return False
        return key in self._elo._timeline_dates

    def _log_unknown_team(self, team: str, family: str, gender: str) -> None:
        marker = (team, family, gender)
        if marker in self._unknown_logged:
            return
        self._unknown_logged.add(marker)
        logger.info("prematch_unknown_team_neutral", team=team, family=family, gender=gender)


def _state_tick(base: Dict[str, Any], meta: Dict[str, Any], *, when: str, innings_no: int, bat: str, bowl: str,
                score: int, wickets: int, legal: int, target: Optional[int], max_balls: int) -> Dict[str, Any]:
    balls_left = max(0, max_balls - legal)
    run_rate = score * 6.0 / legal if legal else 0.0
    req_rr = 0.0
    if innings_no == 2 and target is not None and target - score > 0:
        req_rr = min(60.0, (target - score) * 6.0 / balls_left) if balls_left > 0 else 60.0
    tick = dict(base)
    tick.update({
        "time": when,
        "in_play": True,
        "innings": innings_no,
        "score": float(score),
        "wickets": float(wickets),
        "overs": legal / 6.0,
        "balls_remaining": float(balls_left),
        "run_rate": run_rate,
        "req_run_rate": req_rr,
        "batting_team": bat,
        "bowling_team": bowl,
        "target": target,
    })
    if meta:
        tick["_meta"] = meta
    return tick


def state_ticks(raw: Mapping[str, Any], row: MatchRow, pre: Mapping[str, float], max_balls: int) -> List[Dict[str, Any]]:
    """Pre-match tick followed by one state tick per delivery of the first two innings.

    The first tick carries ``in_play=False`` and the pre-match ``pm_*`` fields and the label in ``_meta``.
    Innings state (score, wickets, legal balls, target) is taken from the deliveries as they happen; the result
    is only in ``_meta``.
    """
    innings = [i for i in (raw.get("innings") or []) if not i.get("super_over")][:2]
    base: Dict[str, Any] = {
        "match_id": row.match_id,
        "team_home": row.home,
        "team_away": row.away,
        "competition": "",
        "match_format": row.match_type,
        "family": row.family,
        "gender": row.gender,
        "market_model": "cricsheet_state",
        **pre,
    }
    meta = {
        "winner": row.winner or None,
        "winner_is_home": (row.winner == row.home) if row.winner else None,
        "gender": row.gender,
    }
    pre_tick = _state_tick(base, meta, when=row.date, innings_no=1, bat="", bowl="", score=0, wickets=0,
                           legal=0, target=None, max_balls=max_balls)
    pre_tick["in_play"] = False
    ticks: List[Dict[str, Any]] = [pre_tick]
    first_total: Optional[int] = None
    for idx, inn in enumerate(innings):
        innings_no = idx + 1
        bat = str(inn.get("team") or "").strip()
        bowl = row.away if bat == row.home else row.home
        target: Optional[int] = None
        if innings_no == 2:
            declared = (inn.get("target") or {}).get("runs")
            if declared is not None:
                target = int(declared)
            elif first_total is not None:
                target = first_total + 1
        score = 0
        wickets = 0
        legal = 0
        for over in inn.get("overs") or []:
            for d in over.get("deliveries") or []:
                extras = d.get("extras") or {}
                is_legal = not ("wides" in extras or "noballs" in extras)
                score += int((d.get("runs") or {}).get("total") or 0)
                wickets += len(d.get("wickets") or [])
                legal += 1 if is_legal else 0
                ticks.append(_state_tick(base, {}, when=row.date, innings_no=innings_no, bat=bat, bowl=bowl,
                                         score=score, wickets=wickets, legal=legal, target=target,
                                         max_balls=max_balls))
        if innings_no == 1:
            first_total = score
    return ticks
