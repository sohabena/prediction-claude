# 🧪 TITAN Manual Testing Guide

**Tester**: The Guardian (Marcus Chen, Principal QA Engineer)  
**Date**: December 6, 2025  
**Status**: Backend Fixed - Ready for Testing

---

## ⚠️ Note on Browser Automation

Browser automation is experiencing technical issues, so I've created this comprehensive manual testing guide. Follow these steps to verify all features work correctly.

---

## 🎯 Testing Objectives

Per The Guardian's methodology:
- **Financial Accuracy**: 100% required (any calculation error is unacceptable)
- **User Experience**: Smooth, intuitive, error-free
- **Error Handling**: Graceful degradation, clear messages
- **Performance**: Fast, responsive, no lag

---

## 📋 Pre-Test Checklist

### ✅ Verify Systems Are Running:

1. **Backend Server** (Terminal 18):
   ```
   Should see: INFO: Uvicorn running on http://0.0.0.0:8001
   ```

2. **Frontend Server** (Check if running):
   ```bash
   # If not running, start it:
   cd frontend
   npm run dev
   ```

3. **Database** (Docker):
   ```bash
   docker ps
   # Should see: postgres container running
   ```

4. **Redis** (Docker):
   ```bash
   docker ps
   # Should see: redis container running
   ```

---

## 🧪 Test Suite 1: Budget Management (CRITICAL)

### Test 1.1: Initialize Budget ✅

**Objective**: Verify budget can be created with custom amount

**Steps**:
1. Open http://localhost:3000 in your browser
2. You should see "No Budget Initialized" message
3. Click **"Initialize Budget"** button
4. Modal should open with default value: 50000
5. **Test Case A**: Keep default (50000)
   - Click **"Initialize"**
   - Wait 1-2 seconds
6. **Test Case B**: Change to custom amount (e.g., 75000)
   - Change value to 75000
   - Click **"Initialize"**

**Expected Results**:
- ✅ Modal closes
- ✅ Budget panel displays:
  - Current Balance: ₹50,000 (or your custom amount)
  - P&L: ₹0 (green or gray)
  - ROI: 0.00%
  - Win Rate: 0.00%
  - Total Bets: 0
  - Bets Won: 0
  - Bets Lost: 0
- ✅ No error messages
- ✅ "Initialize Budget" button disappears

**Pass Criteria**:
- [ ] Budget initializes successfully
- [ ] All metrics display correctly
- [ ] Currency formatting is correct (₹ symbol, commas)
- [ ] No console errors

**If Failed**:
- Check browser console (F12) for errors
- Check backend terminal for API errors
- Check network tab: POST `/api/budget/initialize` should return 200

---

### Test 1.2: Budget Display Formatting ✅

**Objective**: Verify all budget metrics display correctly

**Steps**:
1. After initializing budget, examine the Budget Panel
2. Check each metric

**Expected Results**:
- ✅ **Current Balance**:
  - Format: ₹50,000 (with comma separator)
  - Color: White/light color
  - Font: Bold, prominent

- ✅ **P&L (Profit/Loss)**:
  - Format: ₹0 or +₹0
  - Color: Gray (when zero)
  - Should turn green for profit, red for loss

- ✅ **ROI**:
  - Format: 0.00%
  - Color: Gray (when zero)

- ✅ **Win Rate**:
  - Format: 0.00%
  - Color: Gray (when zero)

- ✅ **Stats Row**:
  - Total Bets: 0
  - Bets Won: 0
  - Bets Lost: 0

**Pass Criteria**:
- [ ] All numbers format correctly
- [ ] Currency symbol (₹) displays
- [ ] Percentages show 2 decimal places
- [ ] Colors are appropriate
- [ ] Text is readable

---

### Test 1.3: Reset Budget (With Active Bets) ❌

**Objective**: Verify reset is blocked when bets are active

**Steps**:
1. Initialize budget (if not already done)
2. Place a bet (see Test Suite 3)
3. Try to reset budget:
   - Click reset button (gear icon or settings)
   - Attempt to reset

**Expected Results**:
- ❌ Reset should be BLOCKED
- ✅ Error message: "Cannot reset budget with active bets"
- ✅ Budget remains unchanged

**Pass Criteria**:
- [ ] Reset is prevented
- [ ] Clear error message shown
- [ ] Budget data unchanged

---

### Test 1.4: Reset Budget (Without Active Bets) ✅

**Objective**: Verify reset works when no active bets

**Steps**:
1. Ensure no active bets (close all bets first)
2. Click reset budget button
3. Enter new amount (e.g., 100000)
4. Confirm reset

**Expected Results**:
- ✅ Budget resets to new amount
- ✅ All stats reset to zero:
  - P&L: ₹0
  - ROI: 0.00%
  - Win Rate: 0.00%
  - Total Bets: 0
- ✅ Bet history is preserved (optional check)

**Pass Criteria**:
- [ ] Reset succeeds
- [ ] New balance displays correctly
- [ ] All metrics reset
- [ ] No errors

---

## 🧪 Test Suite 2: Signal Cards (IMPORTANT)

### Test 2.1: Generate Test Signals

**Objective**: Create signals to test with

**Steps**:
1. Open a new terminal
2. Run:
   ```bash
   cd backend
   python test_signal_generator.py
   ```
3. Wait 5-10 seconds
4. Check frontend for signals

**Expected Results**:
- ✅ Signals appear in the UI
- ✅ Each signal card shows:
  - Strategy name (e.g., "Panic Rebound")
  - Team name
  - Odds (e.g., 1.85)
  - Confidence (e.g., 82%)
  - Edge (e.g., 5.2%)
  - Reasoning text
  - "Place Bet" button (if confidence > threshold)

**Pass Criteria**:
- [ ] Signals appear within 10 seconds
- [ ] All signal data displays
- [ ] Cards are visually distinct
- [ ] Readable text

---

### Test 2.2: Signal Card Content Validation

**Objective**: Verify signal data is complete and accurate

**Steps**:
1. Examine each signal card
2. Check all fields are present

**Expected Results**:
For each signal card:
- ✅ **Strategy Badge**: Colored, uppercase (e.g., "PANIC REBOUND")
- ✅ **Team Name**: Clear, readable
- ✅ **Odds**: Decimal format (e.g., 1.85, 2.10)
- ✅ **Confidence**: Percentage (e.g., 82%)
- ✅ **Edge**: Percentage (e.g., 5.2%)
- ✅ **Reasoning**: Multi-line text explaining the signal
- ✅ **Action Button**: "Place Bet" or disabled if low confidence

**Pass Criteria**:
- [ ] All fields present
- [ ] Data makes sense (odds > 1.0, confidence 0-100%)
- [ ] Reasoning is readable
- [ ] Button state correct

---

## 🧪 Test Suite 3: Bet Placement (MOST CRITICAL)

### Test 3.1: Open Bet Placement Modal

**Objective**: Verify modal opens with Kelly analysis

**Steps**:
1. Find a signal with "Place Bet" button
2. Click **"Place Bet"**
3. Modal should open

**Expected Results**:
- ✅ Modal opens (overlay darkens background)
- ✅ **Signal Summary** section shows:
  - Strategy
  - Team
  - Odds
  - Confidence
- ✅ **Kelly Criterion Analysis** section shows:
  - Recommended Stake (₹ amount)
  - Edge (percentage)
  - Risk Level (color-coded)
  - % of Bankroll
- ✅ **Stake Input** field
- ✅ **Potential Profit** calculation
- ✅ **Place Bet** button
- ✅ **Cancel** button

**Pass Criteria**:
- [ ] Modal opens smoothly
- [ ] All sections visible
- [ ] Kelly recommendation displays
- [ ] Numbers are formatted

---

### Test 3.2: Kelly Criterion Calculation Verification

**Objective**: Verify Kelly math is correct

**Steps**:
1. Open bet placement modal
2. Note the values:
   - Recommended Stake: ₹X
   - Edge: Y%
   - Odds: Z
   - Current Balance: B
3. **Manual Calculation**:
   ```
   Kelly Fraction = Edge / (Odds - 1)
   Quarter Kelly = Kelly Fraction * 0.25
   Recommended Stake = Balance * Quarter Kelly
   ```

**Example**:
```
Balance: ₹50,000
Odds: 2.00
Confidence: 60% (0.60)
Implied Probability: 1/2.00 = 50% (0.50)
Edge: 60% - 50% = 10% (0.10)

Kelly Fraction = 0.10 / (2.00 - 1) = 0.10 / 1.00 = 0.10 (10%)
Quarter Kelly = 0.10 * 0.25 = 0.025 (2.5%)
Recommended Stake = 50,000 * 0.025 = ₹1,250
```

**Pass Criteria**:
- [ ] Recommended stake matches manual calculation (±₹10)
- [ ] Edge calculation is correct
- [ ] Risk level makes sense (low/moderate/high)

---

### Test 3.3: Stake Input Validation

**Objective**: Verify input validation works

**Test Cases**:

**A. Negative Stake**:
- Enter: -1000
- Expected: Error message "Stake must be positive"

**B. Zero Stake**:
- Enter: 0
- Expected: Error message "Stake must be greater than 0"

**C. Exceeds Balance**:
- Enter: 999999 (more than balance)
- Expected: Error message "Insufficient funds"

**D. Below Minimum** (if applicable):
- Enter: 50
- Expected: May show warning about minimum stake

**E. Valid Stake**:
- Enter: 2000
- Expected: No error, potential profit updates

**Pass Criteria**:
- [ ] All validation rules work
- [ ] Error messages are clear
- [ ] Valid input is accepted

---

### Test 3.4: Potential Profit Calculation

**Objective**: Verify profit calculation is correct

**Steps**:
1. Enter stake amount (e.g., 2000)
2. Check "Potential Profit" display
3. **Manual Calculation**:
   ```
   Potential Profit = Stake * (Odds - 1)
   ```

**Example**:
```
Stake: ₹2,000
Odds: 1.85

Potential Profit = 2,000 * (1.85 - 1)
                 = 2,000 * 0.85
                 = ₹1,700
```

**Pass Criteria**:
- [ ] Profit calculation matches manual calc (±₹1)
- [ ] Updates in real-time as stake changes
- [ ] Formatted correctly (₹ symbol, commas)

---

### Test 3.5: Place Bet Submission

**Objective**: Verify bet is created successfully

**Steps**:
1. Enter valid stake (e.g., 2000)
2. Click **"Place Bet ₹2,000"**
3. Wait for response

**Expected Results**:
- ✅ Modal closes
- ✅ Bet appears in **"Active Bets"** section
- ✅ Budget balance decreases by stake amount
- ✅ Active bet shows:
  - Match info
  - Strategy
  - Team
  - Stake: ₹2,000
  - Odds: X.XX
  - Potential Profit: ₹X,XXX
  - "Close Bet" button

**Pass Criteria**:
- [ ] Bet created successfully
- [ ] Balance updated correctly
- [ ] Bet displays in active list
- [ ] All bet details correct

---

## 🧪 Test Suite 4: Active Bets & P&L (CRITICAL)

### Test 4.1: Close Bet - WIN Scenario

**Objective**: Verify profit calculation on winning bet

**Setup**:
- Have an active bet (from Test 3.5)
- Note: Stake = ₹2,000, Odds = 1.85

**Steps**:
1. In "Active Bets" section, find your bet
2. Click **"Close Bet"**
3. Select outcome: **"WON"**
4. Confirm

**Expected Results**:
```
Profit = Stake * (Odds - 1)
       = 2,000 * (1.85 - 1)
       = 2,000 * 0.85
       = ₹1,700

New Balance = Old Balance + Profit
            = (50,000 - 2,000) + 1,700
            = 48,000 + 1,700
            = ₹49,700
```

- ✅ Bet removed from "Active Bets"
- ✅ Bet appears in "Betting History"
- ✅ **Budget Updates**:
  - Current Balance: ₹49,700
  - P&L: +₹1,700 (GREEN)
  - Total Bets: 1
  - Bets Won: 1
  - Win Rate: 100.00%
  - ROI: +3.40% (1,700 / 50,000)

**Pass Criteria**:
- [ ] Profit calculated correctly (₹1,700)
- [ ] Balance updated correctly (₹49,700)
- [ ] P&L is positive and green
- [ ] Win rate is 100%
- [ ] ROI calculated correctly

---

### Test 4.2: Close Bet - LOSS Scenario

**Objective**: Verify loss calculation on losing bet

**Setup**:
- Place another bet (Stake = ₹1,000, any odds)

**Steps**:
1. Click **"Close Bet"**
2. Select outcome: **"LOST"**
3. Confirm

**Expected Results**:
```
Loss = -Stake
     = -₹1,000

New Balance = Old Balance - Stake
            = 49,700 - 1,000
            = ₹48,700
```

- ✅ **Budget Updates**:
  - Current Balance: ₹48,700
  - P&L: +₹700 (still green, 1700 - 1000)
  - Total Bets: 2
  - Bets Won: 1
  - Bets Lost: 1
  - Win Rate: 50.00%
  - ROI: +1.40% (700 / 50,000)

**Pass Criteria**:
- [ ] Loss calculated correctly (-₹1,000)
- [ ] Balance updated correctly (₹48,700)
- [ ] P&L updated (₹700)
- [ ] Win rate recalculated (50%)
- [ ] Stats accurate (1 won, 1 lost)

---

### Test 4.3: Multiple Bets P&L Tracking

**Objective**: Verify cumulative P&L across multiple bets

**Steps**:
1. Place and close 5 bets with mixed outcomes:
   - Bet 1: ₹1,000 @ 2.00 odds → WON (+₹1,000)
   - Bet 2: ₹2,000 @ 1.50 odds → LOST (-₹2,000)
   - Bet 3: ₹1,500 @ 1.80 odds → WON (+₹1,200)
   - Bet 4: ₹1,000 @ 2.20 odds → WON (+₹1,200)
   - Bet 5: ₹3,000 @ 1.60 odds → LOST (-₹3,000)

**Expected Cumulative P&L**:
```
Total Profit: 1,000 + 1,200 + 1,200 = +₹3,400
Total Loss: 2,000 + 3,000 = -₹5,000
Net P&L: 3,400 - 5,000 = -₹1,600
Win Rate: 3/5 = 60%
```

**Pass Criteria**:
- [ ] P&L matches expected (-₹1,600)
- [ ] Win rate is 60%
- [ ] All bets tracked in history
- [ ] Balance is accurate

---

## 🧪 Test Suite 5: Match-Specific Budgets (ADVANCED)

### Test 5.1: Allocate Budget to Match

**Objective**: Create match-specific budget

**Steps**:
1. Click **"+ New Match"** button
2. Enter:
   - Match ID: TEST-001
   - Match Name: India vs Australia
   - Amount: ₹10,000
3. Click **"Allocate"**

**Expected Results**:
- ✅ New tab appears: "India vs Australia"
- ✅ Master budget decreases by ₹10,000
- ✅ Match dashboard shows:
  - Match Budget: ₹10,000
  - Available: ₹10,000
  - Active Bets: 0

**Pass Criteria**:
- [ ] Match tab created
- [ ] Master budget updated
- [ ] Match budget displays correctly

---

### Test 5.2: Place Bet from Match Budget

**Objective**: Verify bets deduct from match budget, not master

**Steps**:
1. Switch to match tab ("India vs Australia")
2. Place a bet (₹2,000)
3. Check both budgets

**Expected Results**:
- ✅ Match budget decreases: ₹10,000 → ₹8,000
- ✅ Master budget unchanged
- ✅ Bet shows in match-specific active bets

**Pass Criteria**:
- [ ] Match budget decreases
- [ ] Master budget unchanged
- [ ] Bet tracked per match

---

## 🧪 Test Suite 6: Real-Time Updates (WEBSOCKET)

### Test 6.1: WebSocket Connection

**Objective**: Verify real-time signal reception

**Steps**:
1. Check connection status indicator
2. Should show "Connected" (green)
3. Generate signals: `python backend/test_signal_generator.py`
4. Watch for signals to appear

**Expected Results**:
- ✅ Connection status: "Connected"
- ✅ Signals appear automatically (no refresh needed)
- ✅ Statistics update in real-time

**Pass Criteria**:
- [ ] WebSocket connected
- [ ] Signals appear live
- [ ] No page refresh needed

---

## 📊 Test Results Template

Use this template to record your findings:

```
=== TITAN MANUAL TEST RESULTS ===
Date: December 6, 2025
Tester: [Your Name]

SYSTEM STATUS:
[ ] Backend Running (port 8001)
[ ] Frontend Running (port 3000)
[ ] Database Running
[ ] Redis Running

TEST SUITE 1: BUDGET MANAGEMENT
[ ] Test 1.1: Initialize Budget - PASS/FAIL
    Notes: ___________________________
[ ] Test 1.2: Budget Display - PASS/FAIL
    Notes: ___________________________
[ ] Test 1.3: Reset (With Bets) - PASS/FAIL
    Notes: ___________________________
[ ] Test 1.4: Reset (Without Bets) - PASS/FAIL
    Notes: ___________________________

TEST SUITE 2: SIGNAL CARDS
[ ] Test 2.1: Generate Signals - PASS/FAIL
    Notes: ___________________________
[ ] Test 2.2: Signal Content - PASS/FAIL
    Notes: ___________________________

TEST SUITE 3: BET PLACEMENT
[ ] Test 3.1: Open Modal - PASS/FAIL
    Notes: ___________________________
[ ] Test 3.2: Kelly Calculation - PASS/FAIL
    Calculated: ₹_____ Expected: ₹_____
[ ] Test 3.3: Input Validation - PASS/FAIL
    Notes: ___________________________
[ ] Test 3.4: Profit Calculation - PASS/FAIL
    Calculated: ₹_____ Expected: ₹_____
[ ] Test 3.5: Place Bet - PASS/FAIL
    Notes: ___________________________

TEST SUITE 4: ACTIVE BETS & P&L
[ ] Test 4.1: Close Bet (WIN) - PASS/FAIL
    Profit: ₹_____ Expected: ₹_____
    Balance: ₹_____ Expected: ₹_____
[ ] Test 4.2: Close Bet (LOSS) - PASS/FAIL
    Loss: ₹_____ Expected: ₹_____
    Balance: ₹_____ Expected: ₹_____
[ ] Test 4.3: Multiple Bets - PASS/FAIL
    P&L: ₹_____ Expected: ₹_____

TEST SUITE 5: MATCH BUDGETS
[ ] Test 5.1: Allocate Budget - PASS/FAIL
    Notes: ___________________________
[ ] Test 5.2: Place from Match - PASS/FAIL
    Notes: ___________________________

TEST SUITE 6: REAL-TIME
[ ] Test 6.1: WebSocket - PASS/FAIL
    Notes: ___________________________

BUGS FOUND:
1. ___________________________
2. ___________________________
3. ___________________________

OVERALL ASSESSMENT:
[ ] PASS - Ready for Production
[ ] FAIL - Critical Issues Found
[ ] PARTIAL - Minor Issues, Acceptable

TESTER SIGNATURE: _______________
```

---

## 🎯 Success Criteria

Per The Guardian's standards:

### MUST PASS (Critical):
- ✅ Budget initialization works
- ✅ Kelly calculations are mathematically correct (±₹10)
- ✅ P&L calculations are 100% accurate
- ✅ Win rate calculates correctly
- ✅ ROI calculates correctly
- ✅ Bet placement succeeds
- ✅ Balance updates correctly

### SHOULD PASS (Important):
- ✅ All input validation works
- ✅ Error messages are clear
- ✅ UI is responsive
- ✅ WebSocket connects
- ✅ Signals display correctly

### NICE TO HAVE (Optional):
- ✅ Match budgets work
- ✅ History is preserved
- ✅ Analytics display

---

## 🐛 Bug Reporting Template

If you find bugs, report them like this:

```
BUG #1: [Short Description]
Severity: CRITICAL / HIGH / MEDIUM / LOW
Steps to Reproduce:
1. ___________________________
2. ___________________________
3. ___________________________

Expected: ___________________________
Actual: ___________________________
Screenshot: [If applicable]
Console Errors: [If any]
```

---

## 📞 Need Help?

**Backend Logs**: Terminal 18 or `terminals/18.txt`  
**Frontend Logs**: Browser console (F12)  
**Database**: Check Docker logs  

---

**🎯 Start testing and report your findings!**

**Remember**: Financial accuracy is CRITICAL. Any calculation error = FAIL.

