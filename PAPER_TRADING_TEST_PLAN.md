# Paper Trading Verification and Test Plan

This document outlines the step-by-step test plan for evaluating the **AI Trading Copilot** in paper trading mode before considering live capital deployment.

---

## 1. Test Environment Setup

### 1.1 Start the Copilot Backend Server
Open a terminal in the repository root directory and start the server:

```powershell
# Launch Copilot REST API & Web Dashboard Server
python -m copilot.server --host 127.0.0.1 --port 8765
```

Expected output:
```
================================================================
AI Trading Copilot Server running on http://127.0.0.1:8765
Web Dashboard: http://127.0.0.1:8765/
API Base:      http://127.0.0.1:8765/api/status
Mode:          PAPER
Currency:      INR
Planned R:R:   Minimum 3.0:1 NET of statutory fees and slippage
================================================================
```

### 1.2 Access the Web Dashboard
Open your browser and navigate to:
```
http://127.0.0.1:8765/
```
Verify that:
- Header loads with `PAPER TRADING` mode badge (green).
- Account balance displays initial balance (e.g. INR 100,000.00).
- Chart canvas displays candlestick bars with EMA 9, EMA 21, EMA 50, and Bollinger Bands.

### 1.3 Install Companion Browser Extension (Chrome / Edge)
1. Open Google Chrome or Microsoft Edge and navigate to `chrome://extensions` or `edge://extensions`.
2. Enable **Developer mode** toggle in the top-right corner.
3. Click **Load unpacked** and select the `extension/` folder in this repository:
   `C:\Users\prabh\.gemini\antigravity\scratch\day-trader-skill\extension`
4. The **AI Trading Copilot** extension icon will appear in your browser toolbar.
5. Click the extension icon to verify connection with the local backend server.

---

## 2. Test Procedures

### Test Case 1: Market Regime Detection & Watchlist Switching
- **Action**: In the Web Dashboard, switch between **NSE**, **Crypto**, and **US** market tabs. Select `RELIANCE.NS`, then `BTC-USD`.
- **Expected Outcome**:
  - The chart dynamically reloads historical/simulated bars for the selected instrument.
  - The Regime badge updates showing detected regime (e.g. `TREND_BULL (85%)` or `RANGE_BOUND (60%)`).

### Test Case 2: 3:1 Net Reward-to-Risk Analysis
- **Action**: Click the **Analyze Setup** button on `RELIANCE.NS`.
- **Expected Outcome**:
  - The system detects the market regime and evaluates eligible strategies.
  - If a valid setup exists, the order ticket populates with:
    - Side (`BUY` or `SELL`).
    - Entry price.
    - Stop loss (defensive swing level).
    - Take profit (engineered to guarantee $\ge 3.0$ Net R:R).
    - Planned Net R:R (e.g. `3.20 R (Net)`).
    - Capital allocated and estimated statutory friction.
    - Blue dashed Entry, Red dashed Stop Loss, and Green dashed Take Profit target lines render on the chart.
  - If conditions are choppy (`LOW_VOL_CHOPPY`) or sub-3:1, `NO TRADE` is returned with an explicit reason explanation.

### Test Case 3: Paper Order Execution & Sizing Verification
- **Action**: Click **Execute Order (Paper)** on an approved 3:1 ticket.
- **Expected Outcome**:
  - Sizing is verified against the 12 user parameters.
  - Order executes immediately with realistic simulated slippage.
  - Entry friction and capital are deducted from cash balance.
  - Position appears in the **Open Positions** table with live unrealized P&L.
  - State is saved atomically to `trader/state/paper_state.json`.

### Test Case 4: Stop-Loss and Take-Profit Simulated Triggers
- **Action**: Let market ticks arrive or click **Refresh / Analyze Setup** across subsequent candles.
- **Expected Outcome**:
  - If a bar's Low reaches or breaches the Stop Loss, the position is automatically closed with exit reason `STOP_LOSS`.
  - If a bar's High reaches or exceeds the Take Profit, the position is automatically closed with exit reason `TAKE_PROFIT_3R`.
  - Closed trades move to the **Closed Trades & Performance** tab.
  - Trade metrics update automatically:
    - Win Rate % updates.
    - Profit Factor recalculates.
    - Net Realized P&L reflects gross P&L minus all roundtrip fees.

### Test Case 5: Emergency Stop Circuit Breaker
- **Action**: While a position is open, click the red **KILL SWITCH** button in the header.
- **Expected Outcome**:
  - Confirmation alert appears.
  - All open positions are immediately liquidated at current market price.
  - All pending orders are cancelled.
  - Trading status locks into `HALTED`.

### Test Case 6: Account Reset
- **Action**: Click **Reset Account** in the trades tab, and confirm the modal dialog.
- **Expected Outcome**:
  - State file is safely cleared back to initial balance.
  - Trade history and position tables return to empty state.

### Test Case 7: TradingView Companion Tab Sync
- **Action**: Open any public chart on `tradingview.com` (e.g. TCS or BTCUSD). Click the Copilot browser extension icon and click **Tab**.
- **Expected Outcome**:
  - The extension safely reads the active ticker from the page title without DOM scraping.
  - The symbol input auto-fills with the corresponding normalized symbol (e.g. `TCS.NS` or `BTC-USD`).
  - Clicking **Analyze Setup** retrieves the 3:1 ticket directly inside the toolbar popup.

---

## 3. Automated Test Suite Execution

You can run the full automated test suite anytime using Python's standard unittest runner:

```powershell
# Run all 378 unit tests (both baseline repository and copilot test suites)
python -m unittest discover -s tests
```

Expected result:
```
Ran 378 tests in ~20s
OK
```
