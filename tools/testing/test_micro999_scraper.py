"""
Test Micro999 Scraper - Find the right selectors
"""

import asyncio
from playwright.async_api import async_playwright

async def test_selectors():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        
        print("🔍 Loading Micro999...")
        await page.goto('https://www.micro999.co/game/4', wait_until='networkidle')
        
        print("\n📊 Testing different selectors...\n")
        
        # Test 1: Find all divs with odds-like numbers
        result1 = await page.evaluate("""
            () => {
                const divs = document.querySelectorAll('div');
                const odds = [];
                divs.forEach(div => {
                    const text = div.textContent?.trim();
                    // Match odds pattern: 1.01 to 99.99
                    if (/^\\d{1,2}\\.\\d{1,2}$/.test(text)) {
                        odds.push({
                            text: text,
                            className: div.className,
                            parent: div.parentElement?.className || 'none'
                        });
                    }
                });
                return odds;
            }
        """)
        
        print(f"✅ Found {len(result1)} odds in DIVs:")
        for i, odds in enumerate(result1[:10]):
            print(f"  {i+1}. {odds['text']} (class: {odds['className'][:50]})")
        
        # Test 2: Get match structure
        result2 = await page.evaluate("""
            () => {
                const matches = [];
                const text = document.body.innerText;
                const lines = text.split('\\n');
                
                for (let i = 0; i < lines.length; i++) {
                    const line = lines[i].trim();
                    // Look for match names (Team v Team)
                    if (line.includes(' v ') && line.includes('|')) {
                        matches.push({
                            line: line,
                            nextLines: [lines[i+1], lines[i+2], lines[i+3], lines[i+4]].filter(l => l)
                        });
                    }
                }
                
                return matches;
            }
        """)
        
        print(f"\n✅ Found {len(result2)} match structures:")
        for i, match in enumerate(result2[:3]):
            print(f"\n  Match {i+1}:")
            print(f"    Line: {match['line']}")
            print(f"    Next lines: {match['nextLines']}")
        
        print("\n✅ Test complete!")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(test_selectors())

