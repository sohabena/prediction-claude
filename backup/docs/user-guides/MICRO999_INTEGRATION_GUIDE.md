# Micro999.co Integration Guide

**Site:** https://www.micro999.co/game/4  
**Date:** December 6, 2025  
**Status:** ✅ Validated - Site accessible and showing live cricket odds

---

## 🎯 Site Analysis

### Page Structure (Verified)

**URL:** https://www.micro999.co/game/4

**Content Observed:**
- ✅ Multiple live cricket matches displayed
- ✅ Clear decimal odds format (1.07, 1.08, 1.8, 1.86, etc.)
- ✅ Three betting columns: "1" (Team 1), "x" (Draw), "2" (Team 2)
- ✅ "Live Now" indicators for active matches
- ✅ Match details: Teams, Date/Time
- ✅ BM (Bookmaker) and F (Fancy) betting options

**Example Matches Seen:**
1. India v South Africa - Live Now (1.07 / 1.08)
2. Railways v Vidarbha - Live Now (1.8 / 1.86)
3. Tripura v Uttarakhand - Live Now (1.7 / 2.06)
4. WBBL match (1.61 / 1.98)
5. Australia v England (1.02 / 1.03)

---

## 📊 Data Structure

### Odds Display Format

Each match shows odds in this pattern:

```
Match Name          | 1 (Team 1) | x (Draw) | 2 (Team 2)
--------------------|------------|----------|------------
India v South Africa| 1.07/1.08  | -/- | 14/15
Railways v Vidarbha | 1.8/1.86   | -/- | 2.16/2.24
```

**Pattern:**
- Each cell shows: **Back odds** / **Lay odds**
- Blue boxes = Back (betting FOR)
- Pink boxes = Lay (betting AGAINST)
- Numbers are decimal odds

### HTML Structure (To Extract)

**Odds Elements:**
- Tags: `<button>` or `<span>` elements
- Classes: Contain "odd", "price", "rate", or are betting buttons
- Values: Decimal numbers (1.01 to 100 range)

**Match Info:**
- Match names: "Team1 v Team2" format
- Status: "Live Now" indicator
- Time: Match start times displayed

---

## 🛠️ Scraper Configuration

### Updated Files

1. **scraper/worker.py**
   - Changed base URL to micro999.co
   - Enhanced DOM selectors for micro999 structure
   - Added tag name extraction

2. **scraper/parsers/micro999.py**
   - New parser for micro999.co format
   - Validates odds range (1.01-100)
   - Extracts match IDs from URL

3. **scraper/parsers/micro999_optimized.py** (NEW!)
   - Optimized for observed page structure
   - Handles multiple matches per page
   - Groups odds by market type
   - Returns list of match records

### Environment Variables

```env
# Use micro999.co as data source
BETTING_SITE_URL=https://www.micro999.co/game/4

# Scraper settings
SCRAPER_WORKERS=5
SCRAPER_HEADLESS=true
SCRAPER_TIMEOUT=30000
```

---

## 🚀 Testing the Integration

### Step 1: Install Playwright Browsers

```bash
python -m playwright install chromium
```

### Step 2: Test Scraper

```bash
# Test with a single worker
python -m scraper.worker
```

**Expected Behavior:**
- Browser opens (or runs headless)
- Navigates to https://www.micro999.co/game/4
- Extracts odds from page (1.07, 1.08, 1.8, 1.86, etc.)
- Publishes to Redis `match_events` channel
- No errors in logs

### Step 3: Monitor Redis

```bash
# Terminal 1: Subscribe to match events
docker exec -it titan_redis redis-cli
SUBSCRIBE match_events

# Terminal 2: Start scraper
python -m scraper.worker
```

**Expected Output in Terminal 1:**
```json
{
  "timestamp": "2025-12-06T...",
  "worker_id": 1,
  "source": "dom",
  "data": {
    "matches": [
      {"odds": "1.07", "element": "...", "tag": "BUTTON"},
      {"odds": "1.08", "element": "...", "tag": "BUTTON"},
      {"odds": "1.8", "element": "...", "tag": "BUTTON"}
    ],
    "url": "https://www.micro999.co/game/4"
  }
}
```

### Step 4: Verify Parser

```python
# Test the optimized parser
from scraper.parsers.micro999_optimized import Micro999OptimizedParser

parser = Micro999OptimizedParser()

# Sample data matching observed structure
test_data = {
    'source': 'dom',
    'timestamp': '2025-12-06T13:20:00Z',
    'data': {
        'matches': [
            {'odds': '1.07', 'tag': 'BUTTON'},
            {'odds': '1.08', 'tag': 'BUTTON'},
            {'odds': '1.8', 'tag': 'BUTTON'},
            {'odds': '1.86', 'tag': 'BUTTON'}
        ],
        'url': 'https://www.micro999.co/game/4'
    }
}

result = parser.parse(test_data)
print(f"Parsed {len(result)} matches")
for match in result:
    print(f"Match: {match['match_id']}, Odds: {match['odds']}")
```

---

## 🎯 Next Steps for Production

### 1. Enhanced Match Extraction

Currently, the parser extracts odds but doesn't correlate them with specific match names. You need to:

**Add JavaScript extraction for match details:**

```javascript
// In scraper/worker.py _extract_page_data method
const matches = [];
const matchRows = document.querySelectorAll('tr'); // Adjust selector

matchRows.forEach(row => {
    const matchName = row.querySelector('.match-name')?.textContent;
    const oddsButtons = row.querySelectorAll('button'); // Or appropriate selector
    
    matches.push({
        match_name: matchName,
        odds: Array.from(oddsButtons).map(b => b.textContent)
    });
});

return matches;
```

### 2. Live Match Detection

Extract "Live Now" indicators:

```javascript
const isLive = row.textContent.includes('Live Now');
```

### 3. Match State Extraction

If the page shows scores/overs/wickets, extract them:

```javascript
const score = row.querySelector('.score')?.textContent;
const overs = row.querySelector('.overs')?.textContent;
```

### 4. Market Type Identification

Distinguish between different betting markets:
- Match Winner (1/x/2)
- Bookmaker (BM)
- Fancy (F)
- Session betting

### 5. WebSocket Monitoring

Micro999.co likely uses WebSocket for live odds updates. Monitor network traffic:

1. Open DevTools → Network → WS
2. Watch for WebSocket connections
3. Capture message format
4. Implement WebSocket parser

---

## 🔍 Advanced Scraping Strategies

### 1. Smart Polling

Instead of scraping every 3 seconds:

```python
# Adaptive polling based on match state
if match.is_live and match.phase == 'critical':
    poll_interval = 1  # 1 second
elif match.is_live:
    poll_interval = 3  # 3 seconds
else:
    poll_interval = 10  # 10 seconds
```

### 2. Event-Driven Scraping

Watch for specific events:
- Wicket falls → Scrape immediately
- Over completed → Scrape
- Odds change detected → Scrape

### 3. Multi-Page Monitoring

If cricket is on multiple pages:

```python
urls = [
    'https://www.micro999.co/game/4',  # Cricket page 1
    'https://www.micro999.co/game/5',  # Cricket page 2
    # Add more as needed
]

# Distribute across workers
for i, url in enumerate(urls):
    worker = ScraperWorker(worker_id=i, redis_client=redis_client)
    await worker.start(match_url=url)
```

---

## ⚠️ Important Considerations

### 1. Authentication

**Currently:** Site is accessible without login  
**If login required:**
- Add authentication logic to scraper
- Store credentials in environment variables
- Handle session management
- Implement cookie persistence

### 2. Rate Limiting

**Recommendations:**
- Start with 1 worker, 5-second intervals
- Monitor for blocks or errors
- Increase gradually if no issues
- Consider using proxies for high-frequency scraping

### 3. Anti-Detection

The scraper already includes:
- ✅ Realistic user agent
- ✅ Human-like viewport size
- ✅ Disabled automation flags
- ✅ Random delays (can add)

**Additional measures:**
- Rotate proxies (if needed)
- Add mouse movements (if detected)
- Vary request timing

### 4. Legal & Ethical

- ✅ Ensure scraping complies with site's Terms of Service
- ✅ Don't overload their servers
- ✅ Respect robots.txt
- ✅ Use data responsibly

---

## 📈 Performance Expectations

### Scraping Metrics

| Metric | Expected | Notes |
|--------|----------|-------|
| **Page Load Time** | 2-5 seconds | Initial page load |
| **Odds Extraction** | <1 second | DOM parsing |
| **Processing Time** | <100ms | Parser execution |
| **Total Latency** | 3-6 seconds | End-to-end per cycle |

### Data Volume

- **Matches per page:** 5-10 cricket matches
- **Odds per match:** 6-12 odds (3 markets x 2-4 options)
- **Updates per minute:** 12-20 (5-second intervals)
- **Daily volume:** ~20,000-30,000 odds updates

---

## ✅ Integration Checklist

- [x] Micro999.co site accessible
- [x] Page structure analyzed
- [x] Scraper configuration updated
- [x] Parser created and optimized
- [x] Environment variables set
- [ ] Test scraper on live site
- [ ] Verify odds extraction
- [ ] Monitor Redis data flow
- [ ] Validate parser output
- [ ] Test with multiple matches
- [ ] Implement error handling
- [ ] Set up monitoring alerts
- [ ] Document any issues found

---

## 🆘 Troubleshooting

### Issue: Scraper can't load page

```bash
# Check if site is accessible
curl -I https://www.micro999.co/game/4

# Check for network issues
ping micro999.co
```

### Issue: No odds extracted

```python
# Add debug logging to worker.py
logger.debug(f"Page HTML: {await page.content()}")
logger.debug(f"Extracted matches: {page_data}")
```

### Issue: Parser returns None

```python
# Test parser with actual data
from scraper.parsers.micro999_optimized import Micro999OptimizedParser

parser = Micro999OptimizedParser()
# Use actual data from scraper logs
result = parser.parse(your_data_here)
print(result)
```

---

## 📞 Support

If you encounter issues:

1. **Check scraper logs:** Look for errors in terminal output
2. **Inspect page:** Use browser DevTools to verify selectors
3. **Test parser:** Run unit tests with sample data
4. **Monitor Redis:** Verify data is flowing
5. **Review documentation:** Check BUILD_PLAN.md and other docs

---

**Status:** ✅ READY FOR TESTING  
**Confidence:** 🟢 HIGH  
**Action:** Start scraper and monitor output

---

*Last Updated: December 6, 2025 18:51 IST*  
*Site Verified: December 6, 2025*

