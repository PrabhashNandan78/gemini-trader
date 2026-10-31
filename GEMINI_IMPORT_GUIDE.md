# Gemini Web App Import Guide: Indian & Crypto Trading Skill

This guide explains step-by-step how to import and use the `indian-crypto-trading` skill in the Gemini web application at [https://gemini.google.com](https://gemini.google.com).

---

## 1. Package Overview

- **Package Archive**: `gemini-trading-skill.zip`
- **Root Instructions**: `gemini-trading-skill/SKILL.md`
- **Skill Name**: `indian-crypto-trading`
- **Total Archive Size**: ~25 KB (well below Gemini's 100 MB upload limit)
- **Included References**: Market conventions, mathematical strategy rules, deterministic risk limits, tool schemas, configuration templates, and sample analysis reports.

---

## 2. How to Import into Gemini Web App

You can use the skill in Gemini through two primary methods:

### Method A: Create a Custom Gem (Recommended for Persistent Use)

If you have a Gemini Advanced or Google Workspace account:

1. **Open Gemini**: Navigate to [https://gemini.google.com](https://gemini.google.com).
2. **Access Gem Manager**:
   - In the left sidebar, click on **Gem Manager** (or click **Explore Gems** -> **New Gem**).
3. **Configure the Gem**:
   - **Name**: `Indian & Crypto Quantitative Trader`
   - **Description**: `Quantitative day-trading assistant for Indian NSE stocks and crypto with strict 2R risk management.`
   - **Instructions**: Open [`gemini-trading-skill/SKILL.md`](gemini-trading-skill/SKILL.md), copy its entire text, and paste it into the **Instructions** box.
4. **Attach Reference Knowledge**:
   - Under **Knowledge**, click **Upload files** (or the paperclip icon).
   - Upload `gemini-trading-skill.zip` (or upload the markdown files from the `gemini-trading-skill/references/` folder).
5. **Save**: Click **Create** or **Save**.
6. Your custom Gem is now permanently available in your sidebar for one-click access.

---

### Method B: Direct Upload in Standard Gemini Chat

If you are using standard Gemini chat:

1. **Open a Chat**: Go to [https://gemini.google.com](https://gemini.google.com).
2. **Attach the Skill**:
   - Click the **+** (Upload files) button in the prompt box.
   - Select and upload `gemini-trading-skill.zip` (or upload `SKILL.md`).
3. **Activate**:
   - Type your request, e.g.:
     > *"Please read the uploaded SKILL.md and reference files. Activate the indian-crypto-trading skill. I want to analyze RELIANCE.NS today."*
4. Gemini will inspect the attached skill files and adopt the persona, mathematical rules, and risk limits for the session.

---

### Method C: Local Gemini CLI / Google Antigravity Skill

To use this skill within Google Antigravity or Gemini CLI on your machine:

1. Copy the `gemini-trading-skill` directory into your Antigravity skills directory:
   ```powershell
   Copy-Item -Recurse -Path "gemini-trading-skill" -Destination "$HOME\.gemini\antigravity\skills\gemini-trading-skill"
   ```
2. The skill will be automatically detected and registered by the Antigravity agent runtime.

---

## 3. Sample Prompts to Try

Once imported into Gemini, try these prompts:

### Prompt 1: Indian Stock Setup Analysis (NSE)
> *"Analyze RELIANCE.NS for a 15-minute Opening Range Breakout. The 15m range is ₹1,146 to ₹1,160. Current price is ₹1,164.90. My account equity is ₹200,000 INR with a ₹2,000 max loss per trade. Calculate exact position sizing, 2R target, and format the paper trading ticket."*

### Prompt 2: Cryptocurrency Setup Analysis (24/7 Market)
> *"Analyze BTC-USD. Current price is $77,100 with support at $76,300. With a $10,000 account and a $100 risk budget, what is the exact fractional unit size under a 25% position cap, and what is the 2.0R take-profit price?"*

### Prompt 3: Risk Compliance Verification
> *"I want to place an order to BUY 100 shares of TCS.NS at ₹2,500 with a stop loss at ₹2,460 and target at ₹2,580. My account is ₹200,000 with a ₹2,000 per-trade loss limit and ₹5,000 daily loss limit. Check if this order passes all risk checks."*

### Prompt 4: Market Hours & Calendar Check
> *"What are the regular trading session hours and upcoming holidays for the National Stock Exchange of India (NSE)? Does the market have pre-market sessions, and what is the mandatory square-off time?"*

### Prompt 5: Backtest Performance Evaluation
> *"A 15-minute ORB strategy on INFY.NS shows 35% target reached, 31% stop reached, and 34% open at market close over 10,000 Monte Carlo replays, with an average return of +0.14R. Provide an honest verdict on this setup."*

---

## 4. Understanding Execution Boundaries

| Action | Can Gemini Web Do It? | Requires Local Terminal? |
|---|---|---|
| **Analyze charts & setups** | Yes (Conversational) | Optional (`python run_trader.py analyze`) |
| **Calculate position sizing & 2R brackets** | Yes (Built-in Math) | Optional (`python run_trader.py paper-trade`) |
| **Verify risk ceilings & position caps** | Yes (Deterministic logic) | Optional (`python run_trader.py paper-trade`) |
| **Scan watchlists of 20+ tickers** | Conceptual | **Yes** (`python run_trader.py scan --market india`) |
| **10,000 Monte Carlo replays** | Approximate summary | **Yes** (`python run_trader.py backtest`) |
| **Write local trade logs to disk** | No (Browser sandbox) | **Yes** (Automated in `trader/state/audit.jsonl`) |
| **Connect to local broker connectors** | No (Security isolation) | **Yes** (`trader-broker/ticket.py`) |

---

## 5. Summary of Files Inside the Zip

The archive `gemini-trading-skill.zip` has `SKILL.md` directly at the root:

```
gemini-trading-skill.zip
├── SKILL.md
└── references/
    ├── ARCHITECTURE.md
    ├── CONFIG_REFERENCE.json
    ├── EXAMPLE_ANALYSIS_REPORTS.md
    ├── EXECUTION_AND_MCP_GUIDE.md
    ├── MARKET_CONVENTIONS.md
    ├── RISK_LIMITS.md
    ├── STRATEGY_RULES.md
    └── TOOL_SCHEMAS.json
```
