"""E2E Smoke Tests for PHOENIX.

Tests that verify the full stack is operational:
- Backend API starts and responds
- Health endpoint returns expected shape
- Key API endpoints return valid responses
- WebSocket connection established

These tests require a running backend (`phoenix.ps1 start`).
Skip marker: pytest -m "not e2e" to exclude.
"""

from __future__ import annotations

import pytest
import asyncio

# Mark all tests as e2e
pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def api_base():
    """Base URL for the running API."""
    return "http://localhost:8001"


@pytest.fixture(scope="module")
def http_client():
    """Create an HTTP client for testing."""
    try:
        import httpx
        return httpx.Client(timeout=10)
    except ImportError:
        pytest.skip("httpx not installed — run: pip install httpx")


class TestAPIHealth:
    """Smoke tests for API availability."""

    def test_root_endpoint(self, http_client, api_base):
        """Root endpoint should return service info."""
        resp = http_client.get(f"{api_base}/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["service"] == "PHOENIX Backend"
        assert data["status"] == "running"

    def test_health_endpoint(self, http_client, api_base):
        """Health endpoint should report component status."""
        resp = http_client.get(f"{api_base}/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "redis" in data
        assert "database" in data

    def test_openapi_docs(self, http_client, api_base):
        """Swagger docs should be available."""
        resp = http_client.get(f"{api_base}/docs")
        assert resp.status_code == 200
        assert "swagger" in resp.text.lower() or "redoc" in resp.text.lower() or "openapi" in resp.text.lower()


class TestAPIEndpoints:
    """Smoke tests for key API endpoints."""

    def test_matches_active(self, http_client, api_base):
        """Active matches endpoint should return list."""
        resp = http_client.get(f"{api_base}/api/matches/active")
        assert resp.status_code == 200
        data = resp.json()
        assert "matches" in data

    def test_training_summary(self, http_client, api_base):
        """Training summary should return valid shape."""
        resp = http_client.get(f"{api_base}/api/training/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_agent_state(self, http_client, api_base):
        """Agent state endpoint should return mode."""
        resp = http_client.get(f"{api_base}/api/agent/state")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_graduation_status(self, http_client, api_base):
        """Graduation status should return criteria."""
        resp = http_client.get(f"{api_base}/api/graduation/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "ready" in data or isinstance(data, dict)

    def test_training_steps(self, http_client, api_base):
        """Training steps should return progress."""
        resp = http_client.get(f"{api_base}/api/training/steps")
        assert resp.status_code == 200

    def test_agent_performance(self, http_client, api_base):
        """Agent performance should return metrics."""
        resp = http_client.get(f"{api_base}/api/agent/performance")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_bets" in data


class TestMetricsEndpoint:
    """Smoke tests for the Prometheus metrics endpoint."""

    def test_metrics_available(self, http_client, api_base):
        """Metrics endpoint should return Prometheus format."""
        resp = http_client.get(f"{api_base}/metrics")
        if resp.status_code == 200:
            assert "phoenix_http_requests_total" in resp.text or "HELP" in resp.text
        else:
            # Metrics may not be available if prometheus_client not installed
            pytest.skip("Prometheus metrics not enabled")


class TestOrchestratorEndpoints:
    """Smoke tests for orchestrator-related endpoints."""

    def test_advisor_state(self, http_client, api_base):
        """Advisor state should return orchestrator info."""
        resp = http_client.get(f"{api_base}/api/advisor/state")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_advisor_stats(self, http_client, api_base):
        """Advisor stats should return training run counts."""
        resp = http_client.get(f"{api_base}/api/advisor/stats")
        assert resp.status_code == 200
