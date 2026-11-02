"""Manual-bet signals (S40): what to back or lay, the worst acceptable price, a suggested stake, and why.

Signals are information for a person who places the bet by hand. Nothing here places a bet. ``SignalBus``
fans a signal out to Redis (the UI channel), an append-only JSON-lines log, and an optional webhook
(``SIGNAL_WEBHOOK_URL``, e.g. a Telegram bot or ntfy topic the operator configures).
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from shared.constants import SIGNAL_CHANNEL, SIGNAL_LOG_PATH, SIGNAL_WEBHOOK_ENV
from shared.logging import setup_logging

logger = setup_logging("signals")

KINDS = ("entry", "exit")
PostFn = Callable[[str, str], None]


@dataclass(frozen=True)
class Signal:
    """One actionable instruction for a manual bet."""

    id: str
    created_at: datetime
    valid_until: datetime
    match: str
    selection: str          # "home" or "away"
    side: str               # "BACK" or "LAY"
    price_limit: Optional[float]   # worst acceptable odds (lowest for BACK, highest for LAY)
    suggested_stake: float
    fair_prob: float        # calibrated P(selection wins)
    market_price: float     # quoted price the edge was measured at
    edge: float             # net edge per unit stake (after commission and half spread)
    reason: str
    strategy_version: str   # "<strategy>:<version>"
    kind: str               # "entry" or "exit"

    def __post_init__(self) -> None:
        if self.valid_until <= self.created_at:
            raise ValueError("valid_until must be after created_at")
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}, got {self.kind!r}")

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["created_at"] = self.created_at.isoformat()
        out["valid_until"] = self.valid_until.isoformat()
        return out

    def text_lines(self) -> list[str]:
        """Short one-line-per-field text for a webhook."""
        limit = "any" if self.price_limit is None else f"{self.price_limit:.2f}"
        return [
            f"kind: {self.kind}",
            f"match: {self.match}",
            f"bet: {self.side} {self.selection}",
            f"price limit: {limit}",
            f"market price: {self.market_price:.2f}",
            f"suggested stake: {self.suggested_stake:.2f}",
            f"fair probability: {self.fair_prob:.3f}",
            f"edge: {self.edge:.4f}",
            f"valid until: {self.valid_until.isoformat()}",
            f"reason: {self.reason}",
            f"strategy: {self.strategy_version}",
            f"id: {self.id}",
        ]


def _default_post(url: str, text: str) -> None:
    req = urllib.request.Request(
        url,
        data=text.encode("utf-8"),
        headers={"Content-Type": "text/plain; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 - URL is operator-configured
        resp.read(0)


class SignalBus:
    """Publishes signals to Redis, the JSON-lines log and the optional webhook.

    Duplicates are suppressed: a signal for the same (match, selection, side, kind) is dropped while an
    earlier one is still valid. ``webhook_url=None`` reads ``SIGNAL_WEBHOOK_URL``; with neither set, no
    webhook call is made. Delivery failures are logged and never raised.
    """

    def __init__(
        self,
        *,
        redis: Any = None,
        log_path: Path | str = SIGNAL_LOG_PATH,
        webhook_url: Optional[str] = None,
        post: Optional[PostFn] = None,
        channel: str = SIGNAL_CHANNEL,
    ) -> None:
        self._redis = redis
        self._log_path = Path(log_path)
        self._webhook = webhook_url if webhook_url is not None else (os.environ.get(SIGNAL_WEBHOOK_ENV) or None)
        self._post: PostFn = post or _default_post
        self._channel = channel
        self._active: dict[tuple[str, str, str, str], datetime] = {}

    async def publish(self, signal: Signal) -> bool:
        """Deliver ``signal``. Returns False when it was suppressed as a duplicate."""
        key = (signal.match, signal.selection, signal.side, signal.kind)
        until = self._active.get(key)
        if until is not None and until > signal.created_at:
            logger.debug("signal_duplicate_suppressed", match=signal.match, selection=signal.selection)
            return False
        self._active[key] = signal.valid_until
        self._append_log(signal)
        if self._redis is not None:
            try:
                await self._redis.publish_event(self._channel, signal.to_dict())
            except Exception as e:  # the UI channel is best effort
                logger.warning("signal_redis_failed", error=type(e).__name__)
        if self._webhook:
            text = "\n".join(signal.text_lines())
            try:
                await asyncio.to_thread(self._post, self._webhook, text)
            except Exception as e:  # never log the URL: it may carry a token
                logger.warning("signal_webhook_failed", error=type(e).__name__)
        logger.info(
            "signal_published",
            kind=signal.kind,
            match=signal.match,
            selection=signal.selection,
            side=signal.side,
            price_limit=signal.price_limit,
            suggested_stake=signal.suggested_stake,
            edge=round(signal.edge, 4),
        )
        return True

    def _append_log(self, signal: Signal) -> None:
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(signal.to_dict(), ensure_ascii=False) + "\n")
