"""
PHOENIX Structured Logging
JSON-structured logs with correlation IDs for traceability across microservices.
Falls back gracefully to stdlib logging if structlog is not installed.

Usage:
    from shared.logging import setup_logging
    logger = setup_logging("scraper")
    logger.info("odds_scraped", match_id="ind_usa", latency_ms=120)
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

try:
    import structlog
    HAS_STRUCTLOG = True
except ImportError:
    HAS_STRUCTLOG = False

_configured = False


class _StdlibFallbackLogger:
    """Fallback logger providing structlog key-value formatting over standard logging."""

    def __init__(self, service_name: str, level: int = logging.INFO) -> None:
        self.service_name = service_name
        self.logger = logging.getLogger(service_name)
        self.logger.setLevel(level)
        if not self.logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setLevel(level)
            self.logger.addHandler(handler)

    def _format(self, event: str, kw: dict[str, Any]) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": self.service_name,
            "event": event,
            **kw,
        }
        return json.dumps(payload, default=str)

    def info(self, event: str, **kw: Any) -> None:
        self.logger.info(self._format(event, kw))

    def warning(self, event: str, **kw: Any) -> None:
        self.logger.warning(self._format(event, kw))

    def error(self, event: str, **kw: Any) -> None:
        self.logger.error(self._format(event, kw))

    def debug(self, event: str, **kw: Any) -> None:
        self.logger.debug(self._format(event, kw))


def setup_logging(service_name: str, log_level: str = "INFO", log_format: str = "json") -> Any:
    """
    Configure structured logging for a PHOENIX microservice.

    Args:
        service_name: Name of the service (e.g., "scraper", "rl-trainer")
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        log_format: Output format - "json" for production, "console" for development

    Returns:
        Configured structlog or fallback logger
    """
    global _configured

    if not HAS_STRUCTLOG:
        level = getattr(logging, log_level.upper(), logging.INFO)
        return _StdlibFallbackLogger(service_name, level)

    if not _configured:
        _configured = True

        # Set stdlib logging level
        logging.basicConfig(
            format="%(message)s",
            stream=sys.stdout,
            level=getattr(logging, log_level.upper()),
        )

        # Choose renderer based on format
        if log_format == "console":
            renderer = structlog.dev.ConsoleRenderer(colors=True)
        else:
            renderer = structlog.processors.JSONRenderer()

        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.stdlib.add_log_level,
                structlog.stdlib.PositionalArgumentsFormatter(),
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                structlog.processors.UnicodeDecoder(),
                renderer,
            ],
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            wrapper_class=structlog.make_filtering_bound_logger(
                getattr(logging, log_level.upper())
            ),
            cache_logger_on_first_use=True,
        )

    return structlog.get_logger(service=service_name)
