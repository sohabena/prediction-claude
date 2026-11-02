import asyncio
from playwright.async_api import async_playwright

async def verify():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        print("Navigating to http://localhost:3000/matches...")
        await page.goto("http://localhost:3000/matches", wait_until="networkidle")
        await page.wait_for_timeout(2000)

        # Screenshot live matches view
        await page.screenshot(path="C:/Users/sohai/.gemini/antigravity/brain/9c3605ac-c59c-4f52-96d6-9149c249055d/synced_live_matches.png")
        print("Captured synced_live_matches.png")

        # Click on Training Corpus tab
        training_tab = page.locator('button:has-text("Training Corpus")')
        await training_tab.click()
        await page.wait_for_timeout(2500)

        # Screenshot training corpus view
        await page.screenshot(path="C:/Users/sohai/.gemini/antigravity/brain/9c3605ac-c59c-4f52-96d6-9149c249055d/synced_training_corpus.png")
        print("Captured synced_training_corpus.png")

        # Click on T20 filter
        t20_btn = page.locator('button:has-text("T20 (")')
        if await t20_btn.count() > 0:
            await t20_btn.click()
            await page.wait_for_timeout(1500)
            await page.screenshot(path="C:/Users/sohai/.gemini/antigravity/brain/9c3605ac-c59c-4f52-96d6-9149c249055d/synced_t20_filter.png")
            print("Captured synced_t20_filter.png")

        # Click on ODI filter
        odi_btn = page.locator('button:has-text("ODI (")')
        if await odi_btn.count() > 0:
            await odi_btn.click()
            await page.wait_for_timeout(1500)
            await page.screenshot(path="C:/Users/sohai/.gemini/antigravity/brain/9c3605ac-c59c-4f52-96d6-9149c249055d/synced_odi_filter.png")
            print("Captured synced_odi_filter.png")

        await browser.close()
        print("Playwright verification complete!")

if __name__ == "__main__":
    asyncio.run(verify())
