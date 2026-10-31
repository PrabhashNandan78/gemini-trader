# Quantitative Strategy & Setup Rules

This guide defines the mathematical formulations, triggers, invalidation rules, and bracket order calculations for the four supported day-trading setups.

---

## 1. Opening Range Breakout (ORB - 15 Minute)

The 15-minute Opening Range Breakout is the primary intraday setup used by the system.

### A. Range Formation
- **NSE Session**: The opening range is calculated between 09:15 and 09:30 IST.
  $$\text{OR}_{\text{high}} = \max_{09:15 \le t \le 09:30}(\text{High}_t)$$
  $$\text{OR}_{\text{low}} = \min_{09:15 \le t \le 09:30}(\text{Low}_t)$$
  $$\text{OR}_{\text{mid}} = \frac{\text{OR}_{\text{high}} + \text{OR}_{\text{low}}}{2}$$
  $$\text{OR}_{\text{span}} = \text{OR}_{\text{high}} - \text{OR}_{\text{low}}$$
- **US Session**: Calculated between 09:30 and 09:45 ET.
- **Crypto Session**: Evaluated over the first 15 minutes of any monitored hourly candle block or daily UTC open.

### B. Trigger Criteria (Long Setup)
A valid long breakout requires:
1. **Candle Close Above Range**: A completed 5-minute candle whose close is strictly above $\text{OR}_{\text{high}}$:
   $$\text{Close}_{5m} > \text{OR}_{\text{high}}$$
2. **Buffer Filter**: The close must be within 0.1% to 1.5% above $\text{OR}_{\text{high}}$ (chasing extended candles beyond 1.5% increases risk of mean reversion).
3. **Volume Confirmation**: Volume on the breakout candle should exceed $1.2\times$ the 20-period average 5-minute volume.
4. **Time Window**:
   - Earliest valid entry: 09:45 IST (NSE) or 09:45 ET (US).
   - Latest valid entry: 14:45 IST (NSE) or 15:30 ET (US).

### C. False Breakout Invalidation
- **Wick Rejection**: If a candle reaches above $\text{OR}_{\text{high}}$ during the bar but closes back below $\text{OR}_{\text{high}}$, it is treated as a failed breakout. Do not enter.
- **Immediate Invalidation**: If filled and subsequent candles close back below $\text{OR}_{\text{high}}$ or cross $\text{OR}_{\text{mid}}$, close the position immediately.

---

## 2. Bracket Order Mathematics (2R Framework)

Every trade proposal must be structured as a 3-part bracket order containing entry, stop-loss, and take-profit.

### A. Core Formulas
1. **Entry Price ($E$)**: The breakout candle closing price or limit order at retest level.
2. **Stop Loss ($S$)**: Standardized at the opening range midpoint ($\text{OR}_{\text{mid}}$) or recent swing low:
   $$S = \text{OR}_{\text{mid}}$$
3. **Risk per Share ($R$)**:
   $$R = E - S$$
4. **Take Profit Target ($T$)**: Fixed at a minimum of $2\times$ the initial risk distance (2.0R):
   $$T = E + (2.0 \times R) = E + 2(E - S)$$
5. **Reward-to-Risk Ratio ($\text{RR}$)**:
   $$\text{RR} = \frac{T - E}{E - S} \ge 2.0$$

### B. Worked Example (RELIANCE.NS)
- 15-minute Opening Range: High = ₹1,160.00, Low = ₹1,146.00
- Midpoint: $\text{OR}_{\text{mid}} = \frac{1160 + 1146}{2} = \text{₹}1,153.00$
- Trigger: 5-minute candle closes at $E = \text{₹}1,164.00$
- Stop Loss: $S = \text{₹}1,153.00$ (Risk per share $R = 1164 - 1153 = \text{₹}11.00$)
- Target (2R): $T = 1164 + (2 \times 11) = \text{₹}1,186.00$
- Reward/Risk: $\frac{1186 - 1164}{11} = 2.0\text{R}$

---

## 3. Secondary Setups

### A. 20-Day High Breakout
- **Universe**: Monitored watchlists.
- **Condition**: Daily closing price exceeds the maximum high of the previous 20 trading days:
  $$\text{Close}_{\text{today}} > \max_{t=1\dots 20}(\text{High}_t)$$
- **Stop**: Placed at the 5-day low or 1.5% below entry.
- **Target**: 2.0R minimum.

### B. Bull Flag Consolidation
- **Pole**: Upward advance of $\ge 3\%$ over 2 to 5 candles.
- **Flag**: 3 to 7 consolidating candles with downward drift, staying above the 38.2% Fibonacci retracement of the pole and above the 20-period EMA.
- **Trigger**: Breakout candle closing above the flag's upper descending trendline.

### C. Oversold Bounce
- **Condition**: Intraday RSI(14) drops below 30 while price touches major support (e.g., prior day high, VWAP, or 200-period EMA).
- **Trigger**: Bullish reversal candle (hammer or engulfing) closing back above the indicator level with rising volume.
- **Stop**: Immediately below the low of the reversal candle.
- **Target**: Retest of the session VWAP or 2.0R.

---

## 4. Exit Rules & Trade Management

1. **Take-Profit Fill**: Position closes when price touches or exceeds target $T$.
2. **Stop-Loss Fill**: Position closes when price touches or falls below stop $S$.
3. **Time-Based Square-Off (Intraday Rules)**:
   - For Indian NSE equities, all open day-trade positions must be closed by 15:15 IST regardless of profit or loss to avoid broker auto-square-off penalties.
   - For US equities, flatten open day-trade positions by 15:55 ET.
   - For Cryptocurrencies, trades may stay open across 24/7 boundaries, but daily risk tracking resets at 00:00 UTC.
4. **Trailing Stops**: After price reaches 1.0R in profit, the stop loss may be trailed to breakeven ($E$) to protect principal.
