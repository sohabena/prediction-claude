"""
PHOENIX Structured Logging
JSON-structured logs with correlation IDs for traceability across microservices.

Usage:
    from shared.logging import setup_logging
    logger = setup_logging("scraper")
    logger.info("odds_scraped", match_id="ind_usa", latency_ms=120)
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


def setup_logging(service_name: str, log_level: str = "INFO", log_format: str = "json") -> Any:
    """
    Configure structured logging for a PHOENIX microservice.

    Args:
        service_name: Name of the service (e.g., "scraper", "rl-trainer")
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        log_format: Output format - "json" for production, "console" for development

    Returns:
        Configured structlog logger
    """
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

    logger = structlog.get_logger(service=service_name)
    logger.info("logger_initialized", service=service_name, level=log_level, format=log_format)
    return logger
