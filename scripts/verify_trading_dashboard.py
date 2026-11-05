import asyncio
from playwright.async_api import async_playwright

async def verify():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        print("Navigating to http://localhost:3000/trading...")
        await page.goto("http://localhost:3000/trading", wait_until="networkidle")
        await page.wait_for_timeout(2000)

        # Screenshot virtual trading view
        await page.screenshot(path="C:/Users/sohai/.gemini/antigravity/brain/9c3605ac-c59c-4f52-96d6-9149c249055d/fresh_virtual_trading.png")
        print("Captured fresh_virtual_trading.png")

        await browser.close()
        print("Done!")

if __name__ == "__main__":
    asyncio.run(verify())

