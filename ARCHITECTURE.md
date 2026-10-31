# AI Trading Copilot: System Architecture

## 1. System Overview

The **AI Trading Copilot** is a modular, multi-market quantitative trading system designed for automated and semi-automated execution across Indian Equities (NSE), Cryptocurrency spot markets, and US Equities.

The system combines:
1. Live multi-market data feeds and normalized bar structures.
2. Mathematical market regime detection (DMI/ADX, ATR, Bollinger Bands).
3. Validated quantitative strategies targeting minimum 3:1 net reward-to-risk.
4. Deterministic 12-parameter risk management and position sizing.
5. First-class persistent paper trading and trade analytics.
6. Authorized broker adapters (Zerodha Kite Connect v3, Alpaca REST API).
7. An independent Web Dashboard and Manifest V3 Chrome/Edge browser extension.

---

## 2. Seven-Layer Architecture

```
+-------------------------------------------------------------------------------+
| Layer 7: User Interface & Browser Extension                                   |
|   - Web Trading Terminal: copilot/web/ (HTML5 / Canvas / Vanilla JS / CSS)    |
|   - Manifest V3 Extension: extension/ (popup.html, background.js, content.js)  |
+-------------------------------------------------------------------------------+
                                      | HTTP REST / JSON (Port 8765)
+-------------------------------------------------------------------------------+
| Layer 6: Backend Server & Broker Adapter Layer                                |
|   - CopilotServer: copilot/server.py                                          |
|   - BaseBrokerAdapter: copilot/broker_adapter.py                              |
|       * PaperBrokerAdapter (Simulation)                                       |
|       * ZerodhaBrokerAdapter (NSE Kite Connect v3)                            |
|       * AlpacaBrokerAdapter (US Equities & Crypto)                            |
+-------------------------------------------------------------------------------+
                                      |
+-------------------------------------------------------------------------------+
| Layer 5: Paper Trading Engine & State Persistence                             |
|   - PaperTradingEngine: copilot/paper_engine.py                               |
|   - State Storage: trader/state/paper_state.json (Atomic rename updates)      |
|   - Real-time Fill Simulation & Performance Stats (Win Rate, Profit Factor)   |
+-------------------------------------------------------------------------------+
                                      |
+-------------------------------------------------------------------------------+
| Layer 4: Deterministic 12-Parameter Risk & Multi-Constraint Sizing Engine     |
|   - UserRiskConfig & RiskAndSizingEngine: copilot/risk_engine.py               |
|   - 9 Safety Gates (Emergency stop, Daily loss limit, Max concurrent, etc.)   |
|   - Multi-constraint position sizing (Smallest of risk, max alloc, cash, exp) |
+-------------------------------------------------------------------------------+
                                      |
+-------------------------------------------------------------------------------+
| Layer 3: Adaptive Strategy Engine & Transaction Cost Modeling                |
|   - AdaptiveStrategyEngine: copilot/strategies.py                             |
|       * TrendFollowing_EMA, ORB_3R, VolatilityBreakout_ATR, MeanReversion_RSI |
|   - TransactionCostModel: copilot/cost_model.py                               |
|       * Statutory NSE costs (Brokerage, STT, GST, Turnover, Stamp Duty)       |
|       * Crypto exchange fees & slippage                                       |
|       * Strict Net Reward-to-Risk >= 3.0:1 enforcement                        |
+-------------------------------------------------------------------------------+
                                      |
+-------------------------------------------------------------------------------+
| Layer 2: Quantitative Indicators & Regime Detection                           |
|   - MarketRegimeDetector & Formulas: copilot/regime.py                        |
|   - Indicators: EMA (9/21/50/200), ATR(14), ADX(14), RSI(14), Bollinger(20)  |
|   - Regimes: TREND_BULL, TREND_BEAR, VOLATILE_EXPANSION, RANGE_BOUND, CHOPPY  |
+-------------------------------------------------------------------------------+
                                      |
+-------------------------------------------------------------------------------+
| Layer 1: Market Data & Multi-Market Normalization                             |
|   - MarketDataProvider: engine/market_data.py                                 |
|   - Asset Models & Session Hours: engine/asset_model.py                       |
+-------------------------------------------------------------------------------+
```

---

## 3. Detailed Component Responsibilities

### 3.1 Layer 1: Market Data & Normalization (`engine/market_data.py`)
- Connects to market data endpoints with automatic retry on 429 and exponential backoff.
- Normalizes disparate market symbols (`RELIANCE.NS`, `BTC-USD`, `AAPL`) into uniform OHLCV candlestick records with Unix timestamp, UTC timestamp, and local market time.
- Validates calendar trading hours, sessions, and market holidays for Indian NSE (09:15-15:30 IST), US exchanges (09:30-16:00 ET), and Crypto (24/7 continuous).

### 3.2 Layer 2: Quantitative Indicators & Regime Detection (`copilot/regime.py`)
- Standard library mathematical formulas for technical indicators:
  - `calc_sma`: Simple Moving Average over arbitrary period.
  - `calc_ema`: Exponential Moving Average with smoothing multiplier $k = 2 / (N + 1)$.
  - `calc_atr`: Wilder's Average True Range measuring true market volatility.
  - `calc_adx`: Directional Movement Index (+DI, -DI, ADX) quantifying trend strength.
  - `calc_rsi`: Wilder's Relative Strength Index measuring momentum.
  - `calc_bollinger_bands`: 20-period SMA with +/- 2 standard deviation envelopes and bandwidth.
- Evaluates bar sequences into distinct regime states:
  - `TREND_BULL`: ADX $\ge 25$, EMA9 > EMA21, +DI > -DI, RSI > 48.
  - `TREND_BEAR`: ADX $\ge 25$, EMA9 < EMA21, -DI > +DI, RSI < 52.
  - `VOLATILE_EXPANSION`: ATR expansion ratio $\ge 1.30$, RVOL $\ge 1.25$.
  - `LOW_VOL_CHOPPY`: ADX < 22, Bollinger Bandwidth < 0.035. (Strict `NO TRADE` state).
  - `RANGE_BOUND`: Horizontal consolidation with mean-reversion opportunity at boundaries.

### 3.3 Layer 3: Adaptive Strategy Engine & Cost Modeling (`copilot/strategies.py`, `copilot/cost_model.py`)
- Matches the active regime to validated candidate strategies.
- Evaluates candidate entry triggers:
  - EMA pullback continuation.
  - 15-minute Opening Range Breakout (ORB).
  - Volatility squeeze expansion.
  - Oversold/overbought mean reversion.
- Evaluates roundtrip transaction costs (brokerage, STT, turnover fees, GST, SEBI fees, stamp duty, and slippage) using `TransactionCostModel`.
- Enforces Net Reward-to-Risk $\ge 3.0:1$. If net R:R is below 3.0, the signal is discarded and `NO TRADE` is returned.

### 3.4 Layer 4: Deterministic Risk Management Engine (`copilot/risk_engine.py`)
- Enforces the user's 12 configuration parameters:
  - `market`, `broker`, `currency`, `balance`, `min_allocation`, `max_allocation`, `max_risk_pct`, `max_daily_loss`, `max_open_exposure`, `max_concurrent_positions`, `leverage`, `mode`.
- Executes 9 Pre-Trade Safety Gates:
  1. Emergency kill switch check.
  2. Max daily loss limit circuit breaker.
  3. Max concurrent positions limit.
  4. Market session trading window.
  5. Stop loss defensive placement check.
  6. Strict 3:1 Net R:R verification.
  7. Multi-constraint position sizing.
  8. Minimum feasible order validation.
  9. Minimum allocation feasibility check (rejects trades rather than silently inflating risk).

### 3.5 Layer 5: Paper Trading Simulation (`copilot/paper_engine.py`)
- Provides realistic local simulation without broker risk:
  - Immediate market order fills with simulated slippage and statutory costs.
  - Persistent state saved to `trader/state/paper_state.json` via atomic rename operations.
  - Bar-by-bar tick processing testing for stop-loss and take-profit triggers.
  - Complete performance tracking: Total Trades, Wins, Losses, Win Rate %, Profit Factor, Max Drawdown %, and Average R achieved.
  - Explicit confirmation gate required for account resets (`confirm=True`).

### 3.6 Layer 6: Broker Adapter Layer (`copilot/broker_adapter.py`)
- Standardized abstract protocol (`BaseBrokerAdapter`):
  - `authenticate()`, `get_account_balance()`, `get_positions()`, `get_orders()`, `place_order()`, `cancel_order()`, `emergency_stop()`, `reconcile_orders()`.
- Implementations:
  - `PaperBrokerAdapter`: Wraps `PaperTradingEngine`.
  - `ZerodhaBrokerAdapter`: Implements Kite Connect v3 protocol (`POST /orders/regular`, `GET /portfolio/positions`). Enforces mandatory `confirm_live=True` gate.
  - `AlpacaBrokerAdapter`: Implements Alpaca REST API for US Equities and Crypto spot. Supports native bracket orders with isolated Paper vs Live URLs.

### 3.7 Layer 7: Web Dashboard & Companion Browser Extension (`copilot/web/`, `extension/`, `copilot/server.py`)
- **Backend Server (`copilot/server.py`)**:
  - Pure Python HTTP server providing CORS-enabled REST API on `http://127.0.0.1:8765`.
  - Serves static dashboard assets and responds to UI actions.
- **Web Dashboard (`copilot/web/`)**:
  - Responsive dark-theme trading terminal.
  - Independent HTML5 Canvas chart renderer showing Candlesticks, EMAs (9, 21, 50), Bollinger Bands, and Entry/SL/TP target lines.
  - Live watchlist, 3:1 ticket inspection, positions table, pending orders table, and trade analytics log.
- **Manifest V3 Extension (`extension/`)**:
  - Chrome/Edge browser toolbar companion.
  - Synchronizes active symbol with TradingView chart tabs via title inspection without DOM scraping or platform violations.
  - Allows 1-click 3:1 setup analysis and emergency kill switch execution.

