"""S91 shared helpers for the read-only paper and data routers.

- ``repo_root`` and ``logs_dir`` / ``reports_dir``: the logs and reports folders. The root is computed from this file,
  overridable with the environment variable ``PHOENIX_ROOT`` (the backend container sets it to ``/app``).
- ``read_only_session``: a session whose first statement is ``SET TRANSACTION READ ONLY``.
- ``last_json_line``: the last complete JSON object of a JSON-lines file, read from the end of the file.
- ``jsonable``: a JSON-safe copy (datetimes as ISO strings, NaN and infinity as null, numpy and Decimal as Python).
"""

from __future__ import annotations

import json
import math
import os
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Mapping, Optional

import numpy as np
from sqlalchemy import text

_TAIL_BLOCK = 64 * 1024


def repo_root() -> Path:
    """The repository root: ``PHOENIX_ROOT`` when set, else the folder above ``backend/``."""
    env = os.environ.get("PHOENIX_ROOT")
    return Path(env) if env else Path(__file__).resolve().parent.parent.parent


def logs_dir() -> Path:
    return repo_root() / "logs"


def reports_dir() -> Path:
    return repo_root() / "reports"


@asynccontextmanager
async def read_only_session() -> AsyncIterator[Any]:
    """A session in a READ ONLY transaction. The commit at the end of ``get_session`` writes nothing."""
    from shared.db import get_session

    async with get_session() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        yield session


def last_json_line(path: Path, accept: Optional[Callable[[dict[str, Any]], bool]] = None) -> Optional[dict[str, Any]]:
    """The last line of a JSON-lines file that parses to an object (and passes ``accept``), or None when there is none.

    Reads from the end in blocks, so a large log costs one block. A last line still being written does not parse, so
    the previous complete line is returned instead.
    """
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            pos = fh.tell()
            data = b""
            while pos > 0:
                step = min(_TAIL_BLOCK, pos)
                pos -= step
                fh.seek(pos)
                data = fh.read(step) + data
                if b"\n" in data.rstrip(b"\r\n"):
                    break
    except OSError:
        return None
    for raw in reversed(data.rstrip(b"\r\n").split(b"\n")):
        line = raw.strip()
        if not line.startswith(b"{"):
            continue
        try:
            record = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(record, dict) and (accept is None or accept(record)):
            return record
    return None


def utc(value: Any) -> Optional[datetime]:
    """A timezone-aware datetime from a datetime or an ISO string, or None."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def jsonable(value: Any) -> Any:
    """A JSON-safe copy of ``value``: mappings and sequences recursed, datetimes as ISO strings (UTC when naive),
    NaN and infinite floats as None, numpy scalars, Decimals and UUIDs as plain Python values."""
    if isinstance(value, Mapping):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(v) for v in value]
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, datetime):
        return utc(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, np.generic):
        value = value.item()
        return jsonable(value)
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, uuid.UUID):
        return str(value)
    return str(value)


def age_seconds(when: Any, now: datetime) -> Optional[float]:
    moment = utc(when)
    return None if moment is None else round((now - moment).total_seconds(), 1)
