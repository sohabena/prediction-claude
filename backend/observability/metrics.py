"""Prometheus metrics middleware for PHOENIX FastAPI backend.

Exposes a /metrics endpoint compatible with Prometheus scraping.
No external dependencies — uses the built-in prometheus_client library.

Usage:
    from backend.observability.metrics import add_metrics_middleware
    add_metrics_middleware(app)
"""

from __future__ import annotations

import time
from typing import Callable

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.routing import Match

try:
    from prometheus_client import (
        Counter,
        Histogram,
        Gauge,
        generate_latest,
        CONTENT_TYPE_LATEST,
    )

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False


if PROMETHEUS_AVAILABLE:
    REQUEST_COUNT = Counter(
        "phoenix_http_requests_total",
        "Total HTTP requests",
        ["method", "endpoint", "status"],
    )
    REQUEST_LATENCY = Histogram(
        "phoenix_http_request_duration_seconds",
        "Request latency in seconds",
        ["method", "endpoint"],
        buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
    )
    ACTIVE_REQUESTS = Gauge(
        "phoenix_http_active_requests",
        "Currently active HTTP requests",
    )
    ORCHESTRATOR_STATE = Gauge(
        "phoenix_orchestrator_state",
        "Current orchestrator state (0=accum,1=offline,2=online,3=virtual,4=graduated)",
    )
    MODEL_VERSION = Gauge(
        "phoenix_model_version",
        "Current model version number",
    )
    TRAINING_RUNS = Counter(
        "phoenix_training_runs_total",
        "Total training runs completed",
    )
    VIRTUAL_BETS_PLACED = Counter(
        "phoenix_virtual_bets_total",
        "Total virtual bets placed",
        ["outcome"],
    )


def _get_route_name(request: Request) -> str:
    """Resolve the matched route pattern for cleaner metric labels."""
    app = request.app
    for route in app.routes:
        match, _ = route.matches(request.scope)
        if match == Match.FULL:
            return getattr(route, "path", str(request.url.path))
    return request.url.path


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Middleware that records request count, latency, and active connections."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint,
    ) -> Response:
        if not PROMETHEUS_AVAILABLE:
            return await call_next(request)

        # Skip metrics endpoint itself to avoid self-referential metrics
        if request.url.path == "/metrics":
            return await call_next(request)

        method = request.method
        endpoint = _get_route_name(request)

        ACTIVE_REQUESTS.inc()
        start = time.perf_counter()
        try:
            response = await call_next(request)
            status = str(response.status_code)
        except Exception:
            status = "500"
            raise
        finally:
            elapsed = time.perf_counter() - start
            REQUEST_COUNT.labels(method=method, endpoint=endpoint, status=status).inc()
            REQUEST_LATENCY.labels(method=method, endpoint=endpoint).observe(elapsed)
            ACTIVE_REQUESTS.dec()

        return response


def add_metrics_middleware(app: FastAPI) -> None:
    """Add Prometheus metrics middleware and /metrics endpoint to a FastAPI app."""
    if not PROMETHEUS_AVAILABLE:
        return

    app.add_middleware(PrometheusMiddleware)

    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint() -> Response:
        """Prometheus-compatible metrics endpoint."""
        return Response(
            content=generate_latest(),
            media_type=CONTENT_TYPE_LATEST,
        )
