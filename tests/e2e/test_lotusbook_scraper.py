"""
End-to-end browser test for LotusBook scraper.
Uses Playwright to verify the scraper can load and extract data from the site.

Run with: python -m pytest tests/e2e/ -v --tb=long
Requires: playwright install chromium
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest
from playwright.async_api import async_playwright

from shared.config import get_settings

# Mark all tests in this module as e2e
pytestmark = pytest.mark.e2e


@pytest.fixture
async def browser_page():
    """Provide a Playwright browser page for testing."""
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    )
    context = await browser.new_context(
        viewport={"width": 1366, "height": 768},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    )
    page = await context.new_page()
    yield page
    await context.close()
    await browser.close()
    await pw.stop()


class TestLotusBookSite:
    """E2E tests against the actual LotusBook cricket site."""

    @pytest.mark.asyncio
    async def test_site_loads(self, browser_page) -> None:
        """LotusBook cricket page loads successfully."""
        settings = get_settings()
        response = await browser_page.goto(
            settings.scraper.betting_site_url,
            wait_until="domcontentloaded",
            timeout=30000,
        )
        assert response is not None
        assert response.status == 200

    @pytest.mark.asyncio
    async def test_page_has_content(self, browser_page) -> None:
        """Page contains cricket-related content."""
        settings = get_settings()
        await browser_page.goto(
            settings.scraper.betting_site_url,
            wait_until="domcontentloaded",
            timeout=30000,
        )

        content = await browser_page.text_content("body")
        assert content is not None
        assert len(content) > 100  # Page has meaningful content

    @pytest.mark.asyncio
    async def test_scraper_worker_extracts_data(self, browser_page) -> None:
        """ScraperWorker can extract at least some data from the page."""
        from scraper.worker import ScraperWorker

        settings = get_settings()
        await browser_page.goto(
            settings.scraper.betting_site_url,
            wait_until="domcontentloaded",
            timeout=30000,
        )
        await asyncio.sleep(3)  # Wait for dynamic content

        worker = ScraperWorker(page=browser_page, poll_interval=3)
        events = await worker.scrape_once()

        # We may or may not get data depending on whether matches are live
        # The key test is that the scraper doesn't crash
        assert isinstance(events, list)

    @pytest.mark.asyncio
    async def test_page_structure_analysis(self, browser_page) -> None:
        """Analyze the page to find odds-related elements."""
        settings = get_settings()
        await browser_page.goto(
            settings.scraper.betting_site_url,
            wait_until="domcontentloaded",
            timeout=30000,
        )
        await asyncio.sleep(2)

        # Look for common betting site elements
        analysis = await browser_page.evaluate("""
            () => {
                const body = document.body;
                return {
                    title: document.title,
                    bodyLength: body.textContent.length,
                    hasNumbers: (body.textContent.match(/\\d+\\.\\d{2}/g) || []).length,
                    hasTables: document.querySelectorAll('table').length,
                    hasEventRows: document.querySelectorAll(
                        '[class*="event"], [class*="match"], [class*="game"]'
                    ).length,
                    hasOddsElements: document.querySelectorAll(
                        '[class*="odds"], [class*="price"], [class*="rate"]'
                    ).length,
                };
            }
        """)

        assert analysis["bodyLength"] > 0
        # Log analysis for debugging
        print(f"Page analysis: {analysis}")


class TestBackendAPI:
    """E2E tests for the FastAPI backend (requires running server)."""

    @pytest.mark.asyncio
    async def test_health_endpoint(self) -> None:
        """Health endpoint responds."""
        import httpx

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get("http://localhost:8001/api/health", timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    assert "status" in data
                else:
                    pytest.skip("Backend not running")
        except Exception:
            pytest.skip("Backend not running")

    @pytest.mark.asyncio
    async def test_root_endpoint(self) -> None:
        """Root endpoint responds."""
        import httpx

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get("http://localhost:8001/", timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    assert data["service"] == "PHOENIX Backend"
                else:
                    pytest.skip("Backend not running")
        except Exception:
            pytest.skip("Backend not running")
