# Market & Asset Conventions Reference

This document establishes the official market conventions, symbols, trading sessions, calendars, and currency rules across all supported asset classes.

---

## 1. Supported Asset Classes

| Asset Class | Identifier | Primary Venues | Quote Currency | Default Timezone | Daily Bars (5-min) |
|---|---|---|---|---|---|
| **Indian Equities** | `equity_in` | NSE, BSE | INR (₹) | `Asia/Kolkata` (IST) | 75 |
| **Market Indices** | `index` | NSE, US Exchanges | INR (₹) or USD ($) | `Asia/Kolkata` or `America/New_York` | 75 or 78 |
| **Cryptocurrencies** | `crypto` | Major Spot/Perp Exchanges | USD ($) or INR (₹) | `UTC` | 288 (24h) |
| **US Equities** | `equity_us` | NYSE, NASDAQ | USD ($) | `America/New_York` (ET) | 78 |

---

## 2. Symbol Syntax and Normalization

### Indian Equities & Indices (NSE / BSE)
- **Convention**: Standard ticker followed by `.NS` (National Stock Exchange of India) or `.BO` (Bombay Stock Exchange).
- **Core Large-Cap Watchlist**:
  - `RELIANCE.NS` (Reliance Industries)
  - `TCS.NS` (Tata Consultancy Services)
  - `INFY.NS` (Infosys)
  - `HDFCBANK.NS` (HDFC Bank)
  - `ICICIBANK.NS` (ICICI Bank)
  - `BHARTIARTL.NS` (Bharti Airtel)
  - `SBIN.NS` (State Bank of India)
  - `LT.NS` (Larsen & Toubro)
  - `TATAMOTORS.NS` (Tata Motors)
  - `MARUTI.NS` (Maruti Suzuki)
- **Indices**:
  - `^NSEI` (Nifty 50 Index)
  - `^NSEBANK` (Nifty Bank Index)
  - `^BSESN` (BSE Sensex)
- **Important**: Do not convert dots to hyphens for Indian equities. `RELIANCE.NS` must preserve the `.NS` extension. Changing it to `RELIANCE-NS` causes data retrieval failure.

### Cryptocurrencies
- **Convention**: Base currency separated by a hyphen from the quote currency (`BASE-QUOTE`).
- **Supported Pairs**:
  - `BTC-USD` (Bitcoin / US Dollar)
  - `ETH-USD` (Ethereum / US Dollar)
  - `SOL-USD` (Solana / US Dollar)
  - `BNB-USD` (BNB / US Dollar)
  - `BTC-INR` (Bitcoin / Indian Rupee)
  - `ETH-INR` (Ethereum / Indian Rupee)
- **Fractional Units**: Crypto orders allow fractional position sizes (e.g., 0.0486 BTC or 0.725 ETH). Standard equities enforce whole-share allocations unless specifically enabled.

### US Equities
- **Convention**: Standard ticker (1 to 5 letters), e.g., `AAPL`, `MSFT`, `NVDA`, `TSLA`.
- **Share Classes**: Dot syntax like `BRK.B` normalizes to `BRK-B` for Yahoo Finance queries.

---

## 3. Trading Sessions and Market Hours

### Indian Stock Market (NSE)
- **Timezone**: `Asia/Kolkata` (IST, UTC+05:30). No Daylight Saving Time adjustments.
- **Regular Session**: 09:15 to 15:30 IST.
  - Pre-market: 09:00 to 09:15 IST.
  - Opening Range (15-min): 09:15 to 09:30 IST. First valid breakout candle closes at 09:30 IST.
  - Earliest Entry: 09:45 IST (allows opening volatility to stabilize).
  - Last Entry Window: 14:45 IST (no new intraday setups after this time).
  - Square-Off / Flat By: 15:15 IST (all day positions flattened before market close).
- **Session Bar Count**: Exactly 75 5-minute candles per full trading session.
- **NSE Holiday Calendar**:
  - Republic Day (Jan 26)
  - Mahashivratri (Feb/Mar)
  - Holi (Mar)
  - Good Friday (Mar/Apr)
  - Dr. Ambedkar Jayanti (Apr 14)
  - Maharashtra Day (May 01)
  - Bakri Id / Eid-ul-Adha (May/Jun)
  - Muharram (Jun/Jul)
  - Independence Day (Aug 15)
  - Mahatma Gandhi Jayanti (Oct 02)
  - Dussehra (Oct)
  - Diwali Laxmi Pujan (Oct/Nov - special Muhurat trading session)
  - Gurunanak Jayanti (Nov)
  - Christmas (Dec 25)

### Cryptocurrency Markets
- **Timezone**: `UTC`.
- **Trading Schedule**: Continuous 24 hours a day, 7 days a week, 365 days a year.
- **Session Bar Count**: 288 5-minute bars per 24-hour UTC day.
- **Calendar**: No weekend or holiday closures.

### US Equities
- **Timezone**: `America/New_York` (ET, UTC-05:00 EST / UTC-04:00 EDT).
- **Regular Session**: 09:30 to 16:00 ET.
- **Session Bar Count**: 78 5-minute candles per full trading session (42 on early close days ending at 13:00 ET).

---

## 4. Currency and Unit Conventions

- **Indian Equities**: Denominated in INR (`₹`). Formatted as `₹1,164.90` or `INR 1,164.90`.
- **Cryptocurrency**: Denominated in the pair's quote currency (USD `$` or INR `₹`).
- **Rounding Rules**:
  - Equity shares: Whole integers by default (`floor` sizing to prevent capital overruns).
  - Crypto units: Up to 4 decimal places for major currencies, up to 8 for satoshis.
  - Pricing: 2 decimal places for equities; 2 to 4 decimal places for crypto.

---

## 5. Regulatory Jurisdiction Boundaries

- **US SEC EDGAR & STOCK Act**:
  - SEC 10-K, 10-Q, 8-K filings and Congressional disclosures (PTRs) are exclusive to US equities.
  - **Rule**: Never evaluate US regulatory filings for Indian NSE equities or cryptocurrency pairs. The absence of an SEC filing is expected and must never be treated as a negative signal.
- **Indian Regulations**:
  - Regulated by SEBI (Securities and Exchange Board of India).
  - Circuit limits (upper and lower price bands: typically 5%, 10%, or 20%) apply to Indian equities.
