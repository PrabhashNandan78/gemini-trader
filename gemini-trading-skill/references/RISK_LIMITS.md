# Deterministic Risk Management & Sizing Rules

Risk management is the central pillar of the trading system. Every order proposal must satisfy strict mathematical risk limits before it can be approved or executed.

---

## 1. Position Sizing Equations

Position size is determined strictly by risk budget, never by emotional conviction or arbitrary share counts.

### A. Equities (Whole Shares)
For Indian NSE equities and US stocks, share count is rounded **down** (floor) to whole units:

$$\text{Shares} = \left\lfloor \frac{\text{Max Loss Allowed}}{\text{Entry Price} - \text{Stop Loss}} \right\rfloor$$

$$\text{Total Position Value} = \text{Shares} \times \text{Entry Price}$$

$$\text{Total Capital at Risk} = \text{Shares} \times (\text{Entry Price} - \text{Stop Loss})$$

### B. Cryptocurrencies (Fractional Units)
For cryptocurrencies, fractional quantities are supported, rounded to 4 decimal places:

$$\text{Crypto Units} = \text{round}\left( \frac{\text{Max Loss Allowed}}{\text{Entry Price} - \text{Stop Loss}}, 4 \right)$$

$$\text{Total Position Value} = \text{Crypto Units} \times \text{Entry Price}$$

$$\text{Total Capital at Risk} = \text{Crypto Units} \times (\text{Entry Price} - \text{Stop Loss})$$

---

## 2. Risk Limit Parameters

The default risk thresholds are defined in `config.example.json`:

| Parameter | Indian NSE Value | Crypto / US Value | Purpose |
|---|---|---|---|
| `max_loss_per_trade` | ₹2,000 INR | $50 to $100 USD | Maximum capital allowed to be lost if stop is hit |
| `max_daily_loss` | ₹5,000 INR | $150 to $250 USD | Circuit breaker: all trading halts for the day upon hitting |
| `max_position_pct` | 10% to 25% | 10% to 25% | Maximum portfolio allocation in a single ticker |
| `cash_buffer_pct` | 5% to 10% | 5% to 10% | Cash reserve that must remain uninvested |
| `max_trades_per_day` | 3 | 3 to 5 | Prevents overtrading and revenge trading |
| `min_reward_risk` | 2.0R | 2.0R | Minimum acceptable reward relative to risk |
| `require_stop` | `true` | `true` | Orders lacking a stop loss are rejected |
| `allow_margin` | `false` | `false` | Zero leverage; cash accounts only |
| `allow_short` | `false` | `false` | Long-only day trades in standard mode |

---

## 3. Pre-Trade Validation Checklist

Before an order can receive a `PASS` verdict, it must pass 8 independent checks:

1. **Mandatory Stop-Loss Check**:
   - The ticket must contain an explicit `stop_loss` price.
   - For a Long trade, the stop loss must be strictly below the entry price: $S < E$.
2. **Per-Trade Loss Ceiling**:
   - Calculated loss at the stop price must not exceed `max_loss_per_trade`.
   $$\text{Shares} \times (E - S) \le \text{max\_loss\_per\_trade}$$
3. **Daily Cumulative Loss Ceiling**:
   - Sum of closed losses today plus the maximum potential loss on all currently open trades must not exceed `max_daily_loss`.
4. **Position Value Ceiling**:
   - Total capital allocated to this single ticker must not exceed `max_position_pct` of the account equity.
5. **Cash and Margin Check**:
   - Account must have sufficient uncommitted settled cash to cover the purchase value while preserving the `cash_buffer_pct`.
6. **Reward-to-Risk Requirement**:
   - The bracket target must offer at least $2\times$ the risk:
   $$\frac{\text{Target} - \text{Entry}}{\text{Entry} - \text{Stop}} \ge 2.0$$
7. **Exchange Session Window**:
   - For NSE: Orders are permitted between 09:45 and 14:45 IST on non-holiday weekdays.
   - Outside these hours, orders are rejected by the window check (unless explicit off-hours simulation bypass is requested).
   - Crypto is 24/7 continuous.
8. **Daily Trade Count**:
   - Number of executed orders today must be strictly below `max_trades_per_day`.

---

## 4. Worked Sizing Examples

### Example 1: Indian Equity (RELIANCE.NS)
- Account Equity: ₹200,000 INR
- Max Loss Budget: ₹2,000 INR
- Candidate Entry: ₹1,164.90
- Proposed Stop Loss: ₹1,153.25
- Risk per Share: $1164.90 - 1153.25 = \text{₹}11.65$
- Position Sizing:
  $$\text{Shares} = \left\lfloor \frac{2000}{11.65} \right\rfloor = \lfloor 171.67 \rfloor = 171 \text{ shares}$$
- Total Position Cost: $171 \times 1164.90 = \text{₹}199,197.90$
- Position Cap Check (e.g. 25% max position = ₹50,000):
  - Since ₹199,197.90 exceeds ₹50,000, the position cap binds!
  - Capped Shares: $\lfloor 50,000 / 1164.90 \rfloor = 42$ shares.
  - Sized Position: **42 shares** (Cost: ₹48,925.80, Actual risk at stop: $42 \times 11.65 = \text{₹}489.30$).
  - Target (2R): ₹1,188.20.

### Example 2: Cryptocurrency (BTC-USD)
- Account Budget: $10,000 USD
- Max Loss Budget: $100 USD
- Candidate Entry: $77,100.00
- Proposed Stop Loss: $76,300.00
- Risk per BTC: $77100 - 76300 = \$800.00$
- Position Sizing:
  $$\text{BTC Units} = \frac{100}{800} = 0.125 \text{ BTC}$$
- Position Value: $0.125 \times 77100 = \$9,637.50$
- Position Cap Check (e.g. 25% max position = $2,500):
  - Since $9,637.50 exceeds $2,500, the 25% cap binds!
  - Capped Units: $2500 / 77100 = 0.0324$ BTC.
  - Sized Position: **0.0324 BTC** (Value: $2,498.04, Actual risk at stop: $0.0324 \times 800 = \$25.92$).
  - Target (2R): $77100 + (2 \times 800) = \$78,700.00$.

