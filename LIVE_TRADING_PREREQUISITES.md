# Live Trading Prerequisites and Regulatory Safety Checklist

## 1. Important Regulatory and Risk Notice

**Automated live execution involves financial risk.** While the AI Trading Copilot enforces strict mathematical stop-losses, daily loss limits, and statutory cost calculations, market conditions (including gap openings, circuit freezes, liquidity droughts, and broker connectivity interruptions) can cause execution slippage beyond theoretical limits.

Live trading must only be activated after:
1. Thorough paper trading verification.
2. Compliance with local regulatory frameworks (such as SEBI in India or FINRA/SEC in the US).
3. Explicit manual confirmation of broker credentials and session tokens.

---

## 2. Indian Equities (NSE) Live Trading Prerequisites

### 2.1 Authorized Broker Account
- An active trading and demat account with a SEBI-registered broker offering official API access (e.g. Zerodha Kite Connect).
- Subscription to the broker's developer API portal (e.g. Kite Connect Developer App).

### 2.2 Environment Variables Configuration
To prevent credential leakage, **never hardcode API keys or secrets in source code or browser storage**. Set the following environment variables in your server environment:

```powershell
# Windows PowerShell
$env:ZERODHA_API_KEY = "your_kite_api_key_here"
$env:ZERODHA_API_SECRET = "your_kite_api_secret_here"
$env:ZERODHA_ACCESS_TOKEN = "your_generated_daily_access_token_here"
```

### 2.3 Daily Session Authentication (Kite Connect v3)
- Under Indian exchange guidelines, broker API sessions expire daily at midnight.
- Each morning before market open (09:15 IST):
  1. Generate a request token via your broker's login redirect URL with TOTP / 2FA.
  2. Exchange the request token for a daily access token using your API secret.
  3. Export the access token into `$env:ZERODHA_ACCESS_TOKEN`.
- The Copilot's `ZerodhaBrokerAdapter` automatically verifies connectivity via `GET /user/profile`. If the token is expired or missing, live orders are strictly blocked.

### 2.4 Statutory Intraday Square-off Rules
- **Product Code**: All intraday equity trades are placed under `MIS` (Margin Intraday Square-off).
- **Auto Square-off Window**: Brokers automatically liquidate open MIS positions between 15:15 and 15:20 IST. Ensure all open copilot positions are closed prior to this window to avoid broker square-off penalty charges.
- **Circuit Halts**: The system does not place orders on instruments that have breached upper or lower circuit limits.

---

## 3. Cryptocurrency & US Equities Prerequisites (Alpaca)

### 3.1 Account & API Keys
- An active Alpaca account (Paper or Live).
- API Key ID and Secret Key obtained from the Alpaca dashboard.

### 3.2 Environment Variables
```powershell
# Windows PowerShell
$env:APCA_API_KEY_ID = "your_alpaca_key_id_here"
$env:APCA_API_SECRET_KEY = "your_alpaca_secret_key_here"
```

### 3.3 Endpoint Isolation
- For Paper Trading: `https://paper-api.alpaca.markets`
- For Live Trading: `https://api.alpaca.markets`
- The `AlpacaBrokerAdapter` isolates these two environments and rejects live orders unless `mode` is explicitly set to `live` and `confirm_live=True`.

---

## 4. Live Trading Safety Gates Checklist

Before placing any order in Live mode, the system automatically verifies all 9 safety gates:

- [ ] **Gate 1 (Kill Switch)**: Global `emergency_stop` is FALSE.
- [ ] **Gate 2 (Daily Loss)**: Realized loss today is below `max_daily_loss`.
- [ ] **Gate 3 (Positions Cap)**: Active positions are below `max_concurrent_positions`.
- [ ] **Gate 4 (Exchange Hours)**: Local market time is within valid trading hours.
- [ ] **Gate 5 (Stop Loss Sanity)**: Stop loss is placed on the defensive side of entry.
- [ ] **Gate 6 (Net 3:1 R:R)**: Net reward after all taxes, fees, and slippage is $\ge 3.0$ times net risk.
- [ ] **Gate 7 (Position Sizing)**: Quantity is bounded by the smallest of risk budget, max allocation, cash buffer, and portfolio exposure.
- [ ] **Gate 8 (Minimum Order)**: Sized quantity meets exchange minimums (minimum 1 whole share on NSE).
- [ ] **Gate 9 (Explicit Confirmation)**: Order payload includes `confirm_live=True` with broker authentication verified.

