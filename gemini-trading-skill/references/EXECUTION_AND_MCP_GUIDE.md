# Execution Environment & Model Context Protocol (MCP) Guide

This document clarifies the operational differences between the Gemini Web App and local CLI execution, and explains how to integrate external tooling via MCP.

---

## 1. Reality of the Gemini Web Environment

Gemini in the web browser (`https://gemini.google.com`) is a powerful Large Language Model interface equipped with search and code interpreter capabilities. It is **not** a persistent local terminal on your desktop.

### What Gemini CAN Do Natively in Web Chat
1. **Understand Trading Architecture**: Follow all rules in `SKILL.md`, including setup triggers, 2R bracket calculations, and risk equations.
2. **Deterministic Risk Calculations**: Compute exact position sizing, share counts, and stop-loss levels mathematically using built-in arithmetic or Python code interpreter.
3. **Structured Trade Synthesis**: Convert informal user ideas (e.g., "I want to buy Reliance with ₹50,000 capital") into formal 3-part bracket tickets (Entry, Stop Loss, Target).
4. **Market Context & Web Search**: Look up current quotes, financial news, and company announcements using Gemini's live Search integration.
5. **Report & Plan Generation**: Produce comprehensive, formatted trade summaries, scenario analyses, and risk audits in Markdown or JSON.

### What Requires External Execution (Local Terminal / Server)
1. **Executing `run_trader.py`**: The web browser cannot execute shell commands on your local PC. Commands like `python run_trader.py scan --market india` must be executed in your local terminal (PowerShell, Command Prompt, or bash).
2. **Local File Persistence**: The web app cannot write to your local hard drive (`trader/state/audit.jsonl` or `trader/portfolio.json`).
3. **Automated Tool Registration**: Uploading `gemini_tools.json` to the Gemini web app provides reference schema definitions, but does not automatically wire up backend API routes inside the standard web chat interface.
4. **Broker Connection**: Direct communication with broker APIs (e.g., Alpaca, Zerodha Kite Connect, Interactive Brokers) requires running local scripts with authenticated credentials.

---

## 2. Complementary Workflow: Web Chat + Local Terminal

For the best experience, use Gemini Web and the local terminal together:

```
+------------------------------------+        +-----------------------------------+
|     Gemini Web App (Skill Mode)    |        |       Local Workstation CLI       |
|  - High-level setup analysis       |        |  - Live watchlist scanning        |
|  - Strategy evaluation             |  <-->  |  - 10,000 Monte Carlo replays     |
|  - Risk limit verification         |        |  - Paper-trade execution logging  |
|  - News catalyst synthesis         |        |  - Fast local price polling       |
+------------------------------------+        +-----------------------------------+
```

### Typical Workflow Example
1. **Local Scan**: Run `python run_trader.py scan --market india` in PowerShell to identify today's active breakout candidates.
2. **Gemini Consultation**: Paste candidate details into Gemini web app:
   *"Analyze RELIANCE.NS based on today's opening range ₹1,146 to ₹1,160 and current price ₹1,164.90. Size a 2R trade for ₹200,000 account with ₹2,000 max loss."*
3. **Gemini Verification**: Gemini verifies the 2R math, checks the session window, sizes the shares (42 shares under position cap), and outputs the complete bracket ticket.
4. **Local Execution**: You execute the paper ticket locally:
   `python run_trader.py paper-trade --symbol RELIANCE.NS --shares 42 --limit 1164.90 --stop 1153.00 --target 1186.00 --execute`
5. **Audit Logging**: The local engine logs the trade fill to `trader/state/audit.jsonl`.

---

## 3. Future MCP (Model Context Protocol) Integration

In environments supporting the Model Context Protocol (such as Antigravity IDE, Claude Desktop, or Gemini CLI sidecars), the Python functions can be registered directly as live callable tools.

### Architecture

```
+------------------------+      JSON-RPC      +----------------------------+
| AI Agent / Client      |  <------------->   | MCP Server (Python)        |
| (Gemini / Antigravity) |   (Stdio/HTTP)     | - engine/asset_model.py    |
|                        |                    | - engine/market_data.py    |
+------------------------+                    | - gemini_interface.py      |
                                              +----------------------------+
```

### Exposing Functions via MCP
The file `gemini_interface.py` is pre-engineered for MCP tool dispatching:
- `analyze_symbol(symbol, setup)`
- `scan_market(market, setup, limit)`
- `backtest_strategy(symbol, replays, seed)`
- `evaluate_risk(symbol, side, qty, entry_price, stop_loss, take_profit)`
- `paper_trade(symbol, side, budget, execute)`
- `get_market_clock(symbol)`

When an MCP host is connected, the model can execute these tools autonomously on your machine with your explicit permission.

