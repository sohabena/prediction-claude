"""
Inspect Live Data from Micro999.co
Quick script to see what data is available on the betting page
"""

import asyncio
from playwright.async_api import async_playwright
import json

async def inspect_page():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        
        print("🔍 Navigating to Micro999.co...")
        await page.goto('https://www.micro999.co/game/4', wait_until='networkidle')
        
        print("\n📊 Extracting page content...")
        
        # Get page title
        title = await page.title()
        print(f"\n📄 Page Title: {title}")
        
        # Get all text content
        text_content = await page.evaluate('''() => {
            return document.body.innerText;
        }''')
        
        print(f"\n📝 Page Text Content (first 2000 chars):")
        print("=" * 80)
        print(text_content[:2000])
        print("=" * 80)
        
        # Look for match information
        print("\n🏏 Looking for match information...")
        
        # Extract all elements with odds-like numbers
        odds_elements = await page.evaluate('''() => {
            const elements = document.querySelectorAll('button, div, span');
            const results = [];
            elements.forEach(el => {
                const text = el.textContent.trim();
                // Check if it looks like odds (number between 1.00 and 100.00)
                if (/^\\d+\\.\\d+$/.test(text)) {
                    const value = parseFloat(text);
                    if (value >= 1.0 && value <= 100.0) {
                        results.push({
                            text: text,
                            tag: el.tagName,
                            className: el.className,
                            id: el.id,
                            parent: el.parentElement ? el.parentElement.tagName : null
                        });
                    }
                }
            });
            return results;
        }''')
        
        print(f"\n💰 Found {len(odds_elements)} odds elements:")
        for i, odds in enumerate(odds_elements[:20]):  # Show first 20
            print(f"  {i+1}. {odds['text']} ({odds['tag']}.{odds['className']})")
        
        # Look for match names
        print("\n🔍 Looking for match names...")
        match_elements = await page.evaluate('''() => {
            const text = document.body.innerText;
            const matches = text.match(/[A-Z][a-z]+(?:\\s+[A-Z][a-z]+)*\\s+v\\s+[A-Z][a-z]+(?:\\s+[A-Z][a-z]+)*/g);
            return matches || [];
        }''')
        
        if match_elements:
            print(f"\n🏏 Found {len(match_elements)} potential matches:")
            for match in match_elements[:10]:
                print(f"  - {match}")
        else:
            print("  ⚠️  No matches found (might be between matches)")
        
        # Look for score elements
        print("\n📊 Looking for score information...")
        score_elements = await page.evaluate('''() => {
            const text = document.body.innerText;
            const scores = text.match(/\\d+\\/\\d+/g);  // Format: 123/4
            return scores || [];
        }''')
        
        if score_elements:
            print(f"  Found scores: {', '.join(score_elements)}")
        else:
            print("  ⚠️  No scores found (might be before match starts)")
        
        # Take a screenshot
        await page.screenshot(path='micro999_screenshot.png', full_page=True)
        print("\n📸 Screenshot saved to: micro999_screenshot.png")
        
        print("\n✅ Inspection complete!")
        print("\nTo view the screenshot: start micro999_screenshot.png")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(inspect_page())

