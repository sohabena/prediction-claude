"""S65 model registry: versioned artefacts, manifests, the champion pointer, the promotion rule and drift statistics.

Layout::

    models/registry/<name>/<version>/manifest.json     data window, rows, git sha, metrics, gate status, parent
    models/registry/<name>/<version>/<artefact files>  copied in, never edited afterwards (sha256 in the manifest)
    models/registry/<name>/champion.json               {"version", "promoted_at", "evidence", "history": [...]}

Names in use: ``blend`` (S61, promoted only on S63 evidence) and ``prematch_t20`` / ``prematch_odi`` (S60; promoted
only together with the blend version they were fitted for, see :meth:`Registry.set_champion`).

Promotion rule (:func:`promotion_decision`). A challenger replaces the champion only when all hold:

1. the S63 gate report is the one written for this challenger (``source.name`` equals the registered gate source)
   and was computed on the same dataset as the held-out window;
2. the challenger passes R1 in the pre-registered ``prematch / all`` segment (its source log loss beats the market
   with a 95% paired CI entirely below zero), so a model that only re-derives the market never gets to replace it;
3. when a champion exists, the challenger's log loss is lower than the champion's on the same held-out rows.

Every rejection is appended to the champion history with its reasons. Nothing is promoted by default.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = ROOT / "models" / "registry"

MANIFEST = "manifest.json"
CHAMPION = "champion.json"
VERSION_FORMAT = "v{:04d}"
_VERSION_RE = re.compile(r"^v(\d{4})$")
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

R1_SEGMENT = ("prematch", "all")
PSI_BINS = 10
PSI_WARN = 0.10
PSI_FAIL = 0.25
CAL_GAP_WARN = 0.05
CAL_GAP_FAIL = 0.10
DRIFT_MIN_SETTLED = 50
_EPS = 0.5  # Jeffreys smoothing for histogram counts


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)
    os.replace(tmp, path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


class Registry:
    """File-backed registry rooted at ``root`` (``models/registry`` by default)."""

    def __init__(self, root: Path = DEFAULT_ROOT) -> None:
        self.root = Path(root)

    # ----------------------------------------------------------------- paths and reads
    def _check_name(self, name: str) -> str:
        if not _NAME_RE.match(name):
            raise ValueError(f"invalid registry name {name!r}")
        return name

    def name_dir(self, name: str) -> Path:
        return self.root / self._check_name(name)

    def version_dir(self, name: str, version: str) -> Path:
        return self.name_dir(name) / version

    def versions(self, name: str) -> list[str]:
        base = self.name_dir(name)
        if not base.is_dir():
            return []
        found = [p.name for p in base.iterdir() if p.is_dir() and _VERSION_RE.match(p.name)
                 and (p / MANIFEST).is_file()]
        return sorted(found)

    def manifest(self, name: str, version: str) -> dict[str, Any]:
        with open(self.version_dir(name, version) / MANIFEST, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def champion_state(self, name: str) -> Optional[dict[str, Any]]:
        path = self.name_dir(name) / CHAMPION
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def champion_version(self, name: str) -> Optional[str]:
        state = self.champion_state(name)
        return state.get("version") if state else None

    def next_version(self, name: str) -> str:
        numbers = [int(_VERSION_RE.match(v).group(1)) for v in self.versions(name)]
        return VERSION_FORMAT.format((max(numbers) if numbers else 0) + 1)

    # ----------------------------------------------------------------- writes
    def register(self, name: str, artefacts: Mapping[str, Path], manifest: Mapping[str, Any]) -> str:
        """Copy ``artefacts`` (arcname -> source file) into a new version directory. Returns the version."""
        version = self.next_version(name)
        dest = self.version_dir(name, version)
        dest.mkdir(parents=True, exist_ok=False)
        digests: dict[str, str] = {}
        for arcname, source in artefacts.items():
            target = dest / arcname
            shutil.copyfile(source, target)
            digests[arcname] = _sha256(target)
        doc = dict(manifest)
        doc.update({
            "name": name,
            "version": version,
            "status": "candidate",
            "parent": self.champion_version(name),
            "registered_at": _utc_iso(),
            "artefacts": digests,
        })
        _write_json(dest / MANIFEST, doc)
        return version

    def _set_status(self, name: str, version: str, status: str) -> None:
        path = self.version_dir(name, version) / MANIFEST
        doc = self.manifest(name, version)
        doc["status"] = status
        _write_json(path, doc)

    def _write_champion(self, name: str, state: dict[str, Any]) -> None:
        _write_json(self.name_dir(name) / CHAMPION, state)

    def promote(self, name: str, version: str, evidence: Mapping[str, Any]) -> dict[str, Any]:
        """Replace the champion with ``version`` only if :func:`promotion_decision` accepts the evidence."""
        manifest = self.manifest(name, version)
        current = self.champion_version(name)
        ok, reasons = promotion_decision(evidence, manifest, champion_present=current is not None)
        state = self.champion_state(name) or {"name": name, "version": None, "history": []}
        now = _utc_iso()
        if not ok:
            state["history"].append({"at": now, "version": version, "decision": "rejected", "reasons": reasons,
                                     "champion": current})
            self._write_champion(name, state)
            return {"promoted": False, "reasons": reasons, "champion": current}
        self._install(name, version, state, now, reason="gated promotion", evidence=dict(evidence))
        return {"promoted": True, "reasons": [], "champion": version, "previous": current}

    def set_champion(self, name: str, version: str, reason: str) -> dict[str, Any]:
        """Point the champion at ``version`` without a gate of its own. Only for artefacts that were fitted for a
        gated champion (the prematch models behind a promoted blend)."""
        current = self.champion_version(name)
        state = self.champion_state(name) or {"name": name, "version": None, "history": []}
        self._install(name, version, state, _utc_iso(), reason=reason, evidence=None)
        return {"promoted": True, "champion": version, "previous": current}

    def _install(self, name: str, version: str, state: dict[str, Any], now: str, *, reason: str,
                 evidence: Optional[dict[str, Any]]) -> None:
        previous = state.get("version")
        if previous and previous != version:
            self._set_status(name, previous, "retired")
        self._set_status(name, version, "champion")
        state["history"].append({"at": now, "version": version, "decision": "promoted", "reason": reason,
                                 "previous": previous})
        state.update({"version": version, "promoted_at": now, "reason": reason, "evidence": evidence})
        self._write_champion(name, state)


# ----------------------------------------------------------------- promotion rule
def r1_status(gate_report: Mapping[str, Any], arena: str = R1_SEGMENT[0], segment: str = R1_SEGMENT[1]) -> Optional[str]:
    for result in gate_report.get("results", []):
        if result.get("arena") == arena and result.get("segment") == segment:
            return result.get("r1", {}).get("status")
    return None


def promotion_decision(evidence: Mapping[str, Any], manifest: Mapping[str, Any],
                       champion_present: bool) -> tuple[bool, list[str]]:
    """Pure promotion rule. Returns ``(promote, reasons)``; ``reasons`` is empty when promotion is allowed."""
    reasons: list[str] = []
    gate = evidence.get("gate_report") or {}
    source = evidence.get("challenger_source")
    if not gate:
        reasons.append("no_gate_report")
    else:
        if source is None or gate.get("source", {}).get("name") != source or source != manifest.get("gate_source"):
            reasons.append("gate_report_for_other_source")
        status = r1_status(gate)
        if status != "pass":
            reasons.append(f"r1_{status or 'missing'}")
    window = evidence.get("window") or {}
    if int(window.get("rows") or 0) < 1:
        reasons.append("empty_window")
    if gate and window.get("dataset") != gate.get("dataset", {}).get("dir"):
        reasons.append("window_not_gate_dataset")
    if champion_present:
        challenger_ll = evidence.get("challenger_logloss")
        champion_ll = evidence.get("champion_logloss")
        if challenger_ll is None or champion_ll is None:
            reasons.append("no_same_window_comparison")
        elif not float(challenger_ll) < float(champion_ll):
            reasons.append("not_better_than_champion")
    return (not reasons), reasons


# ----------------------------------------------------------------- drift statistics
def distribution(values: Iterable[Any], n_bins: int = PSI_BINS) -> dict[str, Any]:
    """Quantile bin edges over [0, 1] and smoothed bin fractions of ``values`` (the baseline for PSI)."""
    arr = _finite(values)
    if arr.size == 0:
        return {"edges": [], "fractions": [], "n": 0}
    inner = np.quantile(arr, np.linspace(0.0, 1.0, n_bins + 1)[1:-1])
    edges = np.unique(np.concatenate([[0.0], inner, [1.0]]))
    return {"edges": edges.tolist(), "fractions": _fractions(arr, edges), "n": int(arr.size)}


def _finite(values: Iterable[Any]) -> np.ndarray:
    out = []
    for v in values:
        if v is None:
            continue
        f = float(v)
        if math.isfinite(f):
            out.append(f)
    return np.asarray(out, dtype=float)


def _fractions(values: np.ndarray, edges: Sequence[float]) -> list[float]:
    counts, _ = np.histogram(values, bins=np.asarray(edges, dtype=float))
    smoothed = counts.astype(float) + _EPS
    return (smoothed / smoothed.sum()).tolist()


def psi(expected: Sequence[float], actual: Sequence[float]) -> float:
    """Population stability index between two binned distributions with the same bins."""
    e = np.asarray(expected, dtype=float)
    a = np.asarray(actual, dtype=float)
    return float(np.sum((a - e) * np.log(a / e)))


def _level(value: float, warn: float, fail: float) -> str:
    return "fail" if value > fail else "warn" if value > warn else "ok"


def drift_assessment(baseline: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Calibration of the champion on settled rows and PSI of ``p_market`` and ``p_model`` against the baseline.

    ``rows`` carry ``p_home``, ``home_won`` (0/1) and optionally ``p_market`` / ``p_model``. ``baseline`` is the
    manifest's ``baseline`` block (``distribution`` output per key). Levels are ``ok``, ``warn`` or ``fail``.
    """
    n = len(rows)
    levels: list[str] = []
    cal_gap = mean_p = observed = None
    if n:
        mean_p = float(np.mean([float(r["p_home"]) for r in rows]))
        observed = float(np.mean([float(r["home_won"]) for r in rows]))
        cal_gap = abs(mean_p - observed)
        levels.append(_level(cal_gap, CAL_GAP_WARN, CAL_GAP_FAIL))
    psi_out: dict[str, Optional[dict[str, Any]]] = {}
    for key in ("p_market", "p_model"):
        base = baseline.get(key) or {}
        values = [r[key] for r in rows if r.get(key) is not None]
        if not base.get("edges") or not values:
            psi_out[key] = None
            continue
        value = psi(base["fractions"], _fractions(_finite(values), base["edges"]))
        psi_out[key] = {"psi": value, "n": len(values)}
        levels.append(_level(value, PSI_WARN, PSI_FAIL))
    level = "fail" if "fail" in levels else "warn" if "warn" in levels else "ok"
    return {"n": n, "mean_p_home": mean_p, "observed_rate": observed, "calibration_gap": cal_gap,
            "psi": psi_out, "level": level}


# ----------------------------------------------------------------- weekly schedule
def next_weekly_run(now: datetime) -> datetime:
    """The next Monday 03:00 UTC strictly after ``now`` (naive datetimes are taken as UTC)."""
    now = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    candidate = now.replace(hour=3, minute=0, second=0, microsecond=0) + timedelta(days=(0 - now.weekday()) % 7)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


__all__ = [
    "DEFAULT_ROOT",
    "DRIFT_MIN_SETTLED",
    "Registry",
    "distribution",
    "drift_assessment",
    "next_weekly_run",
    "promotion_decision",
    "psi",
    "r1_status",
    "read_jsonl",
]
