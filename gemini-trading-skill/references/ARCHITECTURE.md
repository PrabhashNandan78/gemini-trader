# 7-Layer Trading Engine Architecture

The trading engine is built as a modular, provider-independent pipeline with seven distinct layers.

---

## Architecture Diagram

```
+-------------------------------------------------------------------------+
| Layer 1: Interface Layer                                                |
| - Gemini Web Skill (Reasoning, Guidance, Synthesis)                     |
| - Terminal CLI: run_trader.py (scan, analyze, backtest, paper-trade)   |
+-------------------------------------------------------------------------+
                                    |
                                    v
+-------------------------------------------------------------------------+
| Layer 2: Trading Orchestrator (gemini_interface.py)                     |
| - Routes structured function calls to specific domain modules           |
| - Translates high-level tasks into validated parameter payloads         |
+-------------------------------------------------------------------------+
                                    |
                                    v
+-------------------------------------------------------------------------+
| Layer 3: Market Data Provider (engine/market_data.py)                   |
| - Yahoo Finance query2/query1 dual-endpoint failover                   |
| - Normalizes OHLCV bars across timezones (Asia/Kolkata, UTC, US ET)     |
| - Exponential backoff on HTTP 429 rate limits                           |
| - Offline fixture fallback for deterministic testing                   |
+-------------------------------------------------------------------------+
                                    |
                                    v
+-------------------------------------------------------------------------+
| Layer 4: Trading Analysis Engine                                        |
| - trader-scanner: Watchlist scanning & 15m ORB screening                |
| - trader-entry: Bar-by-bar trigger confirmation                         |
| - trader-exit: 2R bracket planning (Entry, Stop Loss, Take Profit)      |
| - trader-timemachine: 10,000 Monte Carlo replays from historical bars   |
| - trader-news: Headline monitoring & catalyst tagging                   |
+-------------------------------------------------------------------------+
                                    |
                                    v
+-------------------------------------------------------------------------+
| Layer 5: Risk Manager (trader-risk & trader-budget)                     |
| - Mathematical position sizing based on risk-per-share                  |
| - Enforces per-trade loss cap, daily loss limit, position cap           |
| - Session window check (NSE 09:45-14:45 IST, Crypto 24/7)              |
+-------------------------------------------------------------------------+
                                    |
                                    v
+-------------------------------------------------------------------------+
| Layer 6: Execution Adapter (trader-broker)                              |
| - Paper trading by default (zero real money at risk)                    |
| - Formats bracket tickets (BUY/SELL, shares, limit, stop, target)       |
| - Simulates fill execution and updates portfolio state                  |
+-------------------------------------------------------------------------+
                                    |
                                    v
+-------------------------------------------------------------------------+
| Layer 7: Audit Logger (trader/state/audit.jsonl)                        |
| - Immutable append-only event ledger                                    |
| - Records timestamps, exact inputs, risk verdicts, and fill details     |
+-------------------------------------------------------------------------+
```

---

## Detailed Layer Responsibilities

### Layer 1: Interface Layer
Provides human-facing and AI-facing entry points:
- **Gemini Web Skill**: Reads `SKILL.md` and reference files, enabling conversational quantitative analysis, prompt-based sizing, and strategy evaluation.
- **Terminal CLI (`run_trader.py`)**: Runs without an AI API key or network fees. Provides subcommands:
  - `scan`: Watchlist screening
  - `analyze`: Multi-step technical setup analysis
  - `backtest`: Time Machine Monte Carlo simulation
  - `paper-trade`: Position sizing and simulated fill execution
  - `clock`: Exchange session check
  - `gemini-tools`: Tool schema export

### Layer 2: Trading Orchestrator (`gemini_interface.py`)
Converts structured user requests into domain module invocations:
- `analyze_symbol(symbol, setup)`
- `scan_market(market, setup, limit)`
- `backtest_strategy(symbol, replays, seed)`
- `evaluate_risk(symbol, side, qty, entry_price, stop_loss, take_profit)`
- `paper_trade(symbol, side, budget, execute)`
- `get_market_clock(symbol)`

### Layer 3: Market Data Provider (`engine/market_data.py`)
Encapsulates all external data communication:
- Standardizes data into the Unified Asset Model (`engine/asset_model.py`).
- Automatic failover between Yahoo query endpoints (`query2.finance.yahoo.com` -> `query1.finance.yahoo.com`).
- Offline fallback to historical JSON fixtures when network is disabled or during automated testing.

### Layer 4: Trading Analysis Engine
Evaluates market state through deterministic mathematical rules:
- **`trader-scanner/scan.py`**: Filters universe by 15-minute opening range breakout, 20-day high, or oversold conditions.
- **`trader-entry/entry.py`**: Watches 5-minute bars to confirm a full bar close outside the range.
- **`trader-exit/exit.py`**: Computes 2.0R bracket parameters (Entry, Stop at OR midpoint, Target at $2\times$ risk).
- **`trader-timemachine/timemachine.py`**: Replays historical sessions with 10,000 Monte Carlo paths using historical 5-minute bars without lookahead bias.
- **`trader-news/news.py`**: Pulls timestamped news headlines and filters by catalyst tags (earnings, guidance, mergers).

### Layer 5: Risk Manager (`trader-risk/check.py` & `trader-budget/size.py`)
Guarantees portfolio safety:
- Verifies that risk is capped at user-defined boundaries (`max_loss_per_trade_inr` / `max_loss_per_trade_usd`).
- Stops trading if `max_daily_loss` is exceeded.
- Rejects orders submitted outside regular exchange session hours.

### Layer 6: Execution Adapter (`trader-broker/ticket.py`)
Simulated execution environment:
- Generates standardized bracket order tickets.
- Tracks cash balance, open positions, and simulated fills in `trader/portfolio.json`.
- Enforces simulated paper trading unless live trading is explicitly enabled.

### Layer 7: Audit Logger
- Every tool invocation and paper trade appends a timestamped JSON line to `trader/state/audit.jsonl`.
- Ensures transparency, auditability, and post-trade performance review.
