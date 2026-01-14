# 🏏 TITAN Match-Wise Budget System

## Overview

The Match-Wise Budget System allows you to allocate separate budgets for each live cricket match, giving you granular control over your betting strategy and risk management.

## Key Features

### 1. **Master Budget**
- Central pool of funds (default: ₹50,000)
- Allocate portions to specific matches
- Unallocated funds remain in master budget
- View total funds across all budgets

### 2. **Match-Specific Budgets**
- Create dedicated budget for each live match
- Independent P&L tracking per match
- Separate bet history and analytics
- Automatic fund return when match ends

### 3. **Live Match Dashboards**
- Real-time match budget status
- Active bets count and total staked
- Match-specific win rate and ROI
- Budget utilization visualization

## How to Use

### Step 1: Initialize Master Budget
```
1. Navigate to TITAN dashboard
2. Click "Initialize Budget" 
3. Set initial amount (default ₹50,000)
4. Confirm initialization
```

### Step 2: Allocate Budget to a Match
```
1. Go to "Live Match Tabs" section
2. Click "+ New Match" button
3. Enter:
   - Match ID (e.g., MATCH-12345)
   - Match Name (e.g., "India vs Australia")
   - Allocation Amount (e.g., ₹5,000)
4. Click "Allocate"
```

### Step 3: Place Bets on Specific Match
```
1. Switch to the match tab
2. View match-specific dashboard
3. Place bets using match budget
4. Monitor active bets and P&L
```

### Step 4: Close Match Budget
```
1. Ensure all bets are closed
2. Use API endpoint: POST /api/match-budgets/match/{match_id}/close
3. Remaining funds return to master budget
4. Final P&L recorded
```

## API Endpoints

### Allocate Budget to Match
```http
POST /api/match-budgets/allocate
Content-Type: application/json

{
  "match_id": "MATCH-12345",
  "match_name": "India vs Australia",
  "amount": 5000
}
```

### Get Live Match Budgets
```http
GET /api/match-budgets/live-matches?user_id=default_user
```

**Response:**
```json
{
  "status": "success",
  "live_matches": [
    {
      "match_id": "MATCH-12345",
      "match_name": "India vs Australia",
      "budget": {
        "current_balance": 4500,
        "total_profit_loss": -500,
        "win_rate": 60.0,
        "roi": -10.0
      },
      "active_bets_count": 3,
      "total_staked": 1500
    }
  ],
  "total_matches": 1
}
```

### Get Match Budget Details
```http
GET /api/match-budgets/match/{match_id}?user_id=default_user
```

### Get Master Budget
```http
GET /api/match-budgets/master?user_id=default_user
```

**Response:**
```json
{
  "status": "success",
  "master_budget": {
    "current_balance": 45000,
    "total_profit_loss": 0,
    "roi": 0
  },
  "total_allocated_to_matches": 5000,
  "total_funds": 50000
}
```

### Close Match Budget
```http
POST /api/match-budgets/match/{match_id}/close?user_id=default_user
```

**Response:**
```json
{
  "status": "success",
  "message": "Match budget closed. ₹4500 returned to master budget",
  "amount_returned": 4500,
  "final_pnl": -500,
  "master_budget_new_balance": 49500
}
```

## UI Components

### 1. **LiveMatchTabs**
- Tab-based interface for switching between matches
- Master budget overview tab
- Individual match tabs with live indicators
- "+ New Match" button for allocations

### 2. **MatchDashboard**
- Match-specific budget stats
- Active bets count
- P&L and ROI metrics
- Budget utilization progress bar
- Quick action buttons

### 3. **BudgetAllocationModal**
- Master budget balance display
- Match ID and name inputs
- Amount slider with quick presets
- Percentage of master budget indicator
- Risk warnings for high allocations

## Database Schema

### Updated `betting_budgets` Table
```sql
CREATE TABLE betting_budgets (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(100) NOT NULL,
    match_id VARCHAR(100),  -- NULL = master, else match-specific
    match_name VARCHAR(200),
    current_balance FLOAT NOT NULL,
    initial_amount FLOAT NOT NULL,
    total_profit_loss FLOAT DEFAULT 0,
    total_bets_placed INTEGER DEFAULT 0,
    total_bets_won INTEGER DEFAULT 0,
    total_bets_lost INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    CONSTRAINT uq_user_match UNIQUE (user_id, match_id)
);
```

## Best Practices

### 1. **Budget Allocation Strategy**
- Allocate 10-20% of master budget per match
- Keep reserve funds in master budget
- Don't over-allocate to single match

### 2. **Risk Management**
- Set match budget based on confidence
- Monitor active bets vs available balance
- Close match budgets promptly after match ends

### 3. **Performance Tracking**
- Compare P&L across different matches
- Identify which match types are profitable
- Adjust allocation strategy based on results

## Example Workflow

```
Initial State:
- Master Budget: ₹50,000

Allocate to Match 1 (India vs Australia):
- Allocation: ₹10,000
- Master Balance: ₹40,000

Place Bets on Match 1:
- Bet 1: ₹2,000 @ 2.5 odds → WON (+₹3,000)
- Bet 2: ₹1,500 @ 1.8 odds → LOST (-₹1,500)
- Match 1 Balance: ₹11,500
- Match 1 P&L: +₹1,500

Allocate to Match 2 (England vs Pakistan):
- Allocation: ₹8,000
- Master Balance: ₹32,000

After Match 1 Ends:
- Close Match 1 Budget
- Return ₹11,500 to Master
- Master Balance: ₹43,500
- Net P&L: +₹1,500

Continue with Match 2...
```

## Troubleshooting

### Issue: Cannot allocate budget
**Solution:** Ensure master budget has sufficient balance

### Issue: Cannot close match budget
**Solution:** Close all pending bets first

### Issue: Match tab not appearing
**Solution:** Refresh page or check API connection

## Future Enhancements

- [ ] Auto-allocation based on match importance
- [ ] Budget rebalancing across matches
- [ ] Match-specific betting strategies
- [ ] Historical match performance comparison
- [ ] Budget alerts and notifications

---

**Built with:** FastAPI, PostgreSQL, React, Next.js, Tailwind CSS

**Version:** 2.0.0 - Match-Wise Budgets

