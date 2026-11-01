/**
 * AI Trading Copilot - Interactive Web Dashboard & Independent Chart Renderer
 */

// Application State
const state = {
  currentMarket: 'NSE',
  currentSymbol: 'NSE:NIFTY1!',
  currentInterval: '15m',
  currentAnalysis: null,
  activeBars: [],
  chartData: null,
  systemStatus: null,
  refreshTimer: null,
};

// Default Watchlists by Market
const WATCHLISTS = {
  NSE: [
    { symbol: 'NSE:NIFTY1!', name: 'NIFTY 50 Futures / Continuous' },
    { symbol: 'NSE:BANKNIFTY1!', name: 'BANK NIFTY Futures / Continuous' },
    { symbol: 'RELIANCE.NS', name: 'Reliance Industries Ltd' },
    { symbol: 'TCS.NS', name: 'Tata Consultancy Services' },
    { symbol: 'INFY.NS', name: 'Infosys Limited' },
    { symbol: 'HDFCBANK.NS', name: 'HDFC Bank Limited' },
    { symbol: 'ICICIBANK.NS', name: 'ICICI Bank Limited' },
    { symbol: 'TATAMOTORS.NS', name: 'Tata Motors Limited' },
  ],
  CRYPTO: [
    { symbol: 'BTC-USD', name: 'Bitcoin / USD' },
    { symbol: 'ETH-USD', name: 'Ethereum / USD' },
    { symbol: 'SOL-USD', name: 'Solana / USD' },
  ],
  US: [
    { symbol: 'AAPL', name: 'Apple Inc.' },
    { symbol: 'MSFT', name: 'Microsoft Corporation' },
    { symbol: 'NVDA', name: 'NVIDIA Corporation' },
    { symbol: 'SPY', name: 'SPDR S&P 500 ETF' },
  ]
};

// DOM Elements
const el = {
  regimeBadge: document.getElementById('regimeBadge'),
  regimeText: document.getElementById('regimeText'),
  modeBadge: document.getElementById('modeBadge'),
  modeText: document.getElementById('modeText'),
  valBalance: document.getElementById('valBalance'),
  valEquity: document.getElementById('valEquity'),
  valWinRate: document.getElementById('valWinRate'),
  valProfitFactor: document.getElementById('valProfitFactor'),
  valMaxDrawdown: document.getElementById('valMaxDrawdown'),
  watchlistContainer: document.getElementById('watchlistContainer'),
  chartSymbol: document.getElementById('chartSymbol'),
  chartInterval: document.getElementById('chartInterval'),
  selectInterval: document.getElementById('selectInterval'),
  chartDataBadge: document.getElementById('chartDataBadge'),
  chartCanvas: document.getElementById('chartCanvas'),
  chartTooltip: document.getElementById('chartTooltip'),
  btnAnalyzeSetup: document.getElementById('btnAnalyzeSetup'),
  btnRefreshChart: document.getElementById('btnRefreshChart'),
  btnExecuteOrder: document.getElementById('btnExecuteOrder'),
  btnEmergencyStop: document.getElementById('btnEmergencyStop'),
  btnOpenSettings: document.getElementById('btnOpenSettings'),
  btnCloseSettings: document.getElementById('btnCloseSettings'),
  btnCancelSettings: document.getElementById('btnCancelSettings'),
  btnSaveSettings: document.getElementById('btnSaveSettings'),
  modalSettings: document.getElementById('modalSettings'),
  btnResetHistory: document.getElementById('btnResetHistory'),
  modalReset: document.getElementById('modalReset'),
  btnCloseReset: document.getElementById('btnCloseReset'),
  btnCancelReset: document.getElementById('btnCancelReset'),
  btnConfirmReset: document.getElementById('btnConfirmReset'),
  signalEmptyState: document.getElementById('signalEmptyState'),
  signalDetails: document.getElementById('signalDetails'),
  signalDecision: document.getElementById('signalDecision'),
  signalStrategy: document.getElementById('signalStrategy'),
  ticketSymbol: document.getElementById('ticketSymbol'),
  ticketSide: document.getElementById('ticketSide'),
  ticketEntry: document.getElementById('ticketEntry'),
  ticketSL: document.getElementById('ticketSL'),
  ticketTP: document.getElementById('ticketTP'),
  ticketRR: document.getElementById('ticketRR'),
  ticketQty: document.getElementById('ticketQty'),
  ticketCapital: document.getElementById('ticketCapital'),
  ticketRisk: document.getElementById('ticketRisk'),
  ticketFriction: document.getElementById('ticketFriction'),
  ticketReasonsBox: document.getElementById('ticketReasonsBox'),
  ticketReasonsText: document.getElementById('ticketReasonsText'),
  positionsTableBody: document.getElementById('positionsTableBody'),
  ordersTableBody: document.getElementById('ordersTableBody'),
  tradesTableBody: document.getElementById('tradesTableBody'),
  countPositions: document.getElementById('countPositions'),
  countOrders: document.getElementById('countOrders'),
  countTrades: document.getElementById('countTrades'),
  statTotalTrades: document.getElementById('statTotalTrades'),
  statWinLoss: document.getElementById('statWinLoss'),
  statWinRatePct: document.getElementById('statWinRatePct'),
  statProfitFactor: document.getElementById('statProfitFactor'),
  statAvgR: document.getElementById('statAvgR'),
  statNetPnL: document.getElementById('statNetPnL'),
  healthStatusBadge: document.getElementById('healthStatusBadge'),
  healthTypeBadge: document.getElementById('healthTypeBadge'),
  healthInstrumentVal: document.getElementById('healthInstrumentVal'),
  healthIntervalVal: document.getElementById('healthIntervalVal'),
  healthProviderVal: document.getElementById('healthProviderVal'),
  healthUpdateVal: document.getElementById('healthUpdateVal'),
  healthAgeVal: document.getElementById('healthAgeVal'),
  healthCandleAgeVal: document.getElementById('healthCandleAgeVal'),
  healthCandlesVal: document.getElementById('healthCandlesVal'),
  healthTvSyncBadge: document.getElementById('healthTvSyncBadge'),
  diagLastEval: document.getElementById('diagLastEval'),
  diagSignalsEvaluated: document.getElementById('diagSignalsEvaluated'),
  diagSignalDecisions: document.getElementById('diagSignalDecisions'),
  diagRiskApproved: document.getElementById('diagRiskApproved'),
  diagExecutedOrders: document.getElementById('diagExecutedOrders'),
  diagRejectedOrders: document.getElementById('diagRejectedOrders'),
  diagRecentRejection: document.getElementById('diagRecentRejection'),
};

// Initialization
document.addEventListener('DOMContentLoaded', () => {
  initWatchlist();
  initMarketTabs();
  initTabs();
  initModals();
  initChartCanvas();
  bindEvents();
  fetchStatus();
  fetchPositions();
  syncTradingViewContext();
  fetchChartData();
  fetchTrades();

  // Polling loop every 3 seconds for status, positions, and active TV context
  state.refreshTimer = setInterval(() => {
    fetchStatus();
    fetchPositions();
    syncTradingViewContext();
  }, 3000);

  // Periodic live chart candle refresh (every 8 seconds)
  state.chartPollTimer = setInterval(() => {
    fetchChartData(true);
  }, 8000);

  // Smooth live age ticker (every 1 second)
  setInterval(() => {
    if (state.lastHealthUpdateTs && el.healthAgeVal) {
      const nowSec = Math.floor(Date.now() / 1000);
      const age = Math.max(0, nowSec - state.lastHealthUpdateTs);
      el.healthAgeVal.textContent = `${age}s ago`;
    }
  }, 1000);
});

// Watchlist Rendering
function initWatchlist() {
  const items = WATCHLISTS[state.currentMarket] || [];
  el.watchlistContainer.innerHTML = '';
  items.forEach(item => {
    const li = document.createElement('li');
    li.className = `watchlist-item ${item.symbol === state.currentSymbol ? 'selected' : ''}`;
    li.innerHTML = `
      <div>
        <div class="watch-sym">${item.symbol}</div>
        <div class="watch-desc">${item.name}</div>
      </div>
      <button class="btn btn-sm btn-outline">Select</button>
    `;
    li.addEventListener('click', () => {
      state.currentSymbol = item.symbol;
      document.querySelectorAll('.watchlist-item').forEach(x => x.classList.remove('selected'));
      li.classList.add('selected');
      el.chartSymbol.textContent = item.symbol;
      fetchChartData();
    });
    el.watchlistContainer.appendChild(li);
  });
}

function initMarketTabs() {
  document.querySelectorAll('.market-selector-tabs .tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.market-selector-tabs .tab-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      state.currentMarket = btn.dataset.market;
      const firstItem = WATCHLISTS[state.currentMarket][0];
      state.currentSymbol = firstItem.symbol;
      el.chartSymbol.textContent = state.currentSymbol;
      initWatchlist();
      fetchChartData();
    });
  });
}

function initTabs() {
  document.querySelectorAll('.tab-link').forEach(link => {
    link.addEventListener('click', () => {
      document.querySelectorAll('.tab-link').forEach(l => l.classList.remove('active'));
      document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
      link.classList.add('active');
      const targetPane = document.getElementById(link.dataset.tab);
      if (targetPane) targetPane.classList.add('active');
    });
  });
}

function initModals() {
  // Settings Modal
  el.btnOpenSettings.addEventListener('click', openSettingsModal);
  el.btnCloseSettings.addEventListener('click', () => el.modalSettings.style.display = 'none');
  el.btnCancelSettings.addEventListener('click', () => el.modalSettings.style.display = 'none');
  el.btnSaveSettings.addEventListener('click', saveSettings);

  // Reset Modal
  el.btnResetHistory.addEventListener('click', () => el.modalReset.style.display = 'flex');
  el.btnCloseReset.addEventListener('click', () => el.modalReset.style.display = 'none');
  el.btnCancelReset.addEventListener('click', () => el.modalReset.style.display = 'none');
  el.btnConfirmReset.addEventListener('click', confirmResetAccount);
}

function bindEvents() {
  el.btnAnalyzeSetup.addEventListener('click', analyzeSetup);
  el.btnRefreshChart.addEventListener('click', fetchChartData);
  if (el.selectInterval) {
    el.selectInterval.addEventListener('change', (e) => {
      state.currentInterval = e.target.value;
      fetchChartData();
    });
  }
  el.btnExecuteOrder.addEventListener('click', executeOrder);
  el.btnEmergencyStop.addEventListener('click', triggerEmergencyStop);

  const cfgMode = document.getElementById('cfgMode');
  cfgMode.addEventListener('change', () => {
    const isLive = cfgMode.value === 'live';
    document.getElementById('liveWarningBox').style.display = isLive ? 'block' : 'none';
  });

  const headerAutoPaperToggle = document.getElementById('headerAutoPaperToggle');
  if (headerAutoPaperToggle) {
    headerAutoPaperToggle.addEventListener('change', async (e) => {
      try {
        await fetch('/api/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ auto_execute_paper: e.target.checked })
        });
        fetchStatus();
      } catch (err) {
        console.error('Failed to toggle auto paper execution:', err);
      }
    });
  }

  const btnHeaderPause = document.getElementById('btnHeaderPauseEntries');
  if (btnHeaderPause) {
    btnHeaderPause.addEventListener('click', togglePauseEntries);
  }

  const btnCloseAll = document.getElementById('btnCloseAllPositions');
  if (btnCloseAll) {
    btnCloseAll.addEventListener('click', closeAllPositions);
  }
}

// API Calls
async function fetchStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    state.systemStatus = data;
    renderStatus(data);
  } catch (err) {
    console.error('Failed to fetch status:', err);
  }
}

function renderStatus(data) {
  const curr = data.currency === 'INR' ? '₹' : '$';
  el.valBalance.textContent = `${curr}${formatNumber(data.balance)}`;
  el.valEquity.textContent = `${curr}${formatNumber(data.equity)}`;

  const stats = data.stats || {};
  el.valWinRate.textContent = `${stats.win_rate_pct || 0}%`;
  el.valProfitFactor.textContent = (stats.profit_factor || 0).toFixed(2);
  el.valMaxDrawdown.textContent = `${stats.max_drawdown_pct || 0}%`;

  if (data.mode === 'live') {
    el.modeBadge.className = 'mode-pill live-mode';
    el.modeText.textContent = 'LIVE TRADING ⚠️';
    el.btnExecuteOrder.textContent = '🚀 Execute Live Order';
    el.btnExecuteOrder.className = 'btn btn-danger btn-block';
  } else {
    el.modeBadge.className = 'mode-pill paper-mode';
    el.modeText.textContent = 'PAPER TRADING';
    el.btnExecuteOrder.textContent = '🚀 Execute Order (Paper)';
    el.btnExecuteOrder.className = 'btn btn-success btn-block';
  }

  const autoToggle = document.getElementById('headerAutoPaperToggle');
  if (autoToggle) {
    autoToggle.checked = !!data.auto_execute_paper;
  }
  const btnHeaderPause = document.getElementById('btnHeaderPauseEntries');
  if (btnHeaderPause) {
    if (data.new_entries_paused) {
      btnHeaderPause.textContent = '▶️ Resume';
      btnHeaderPause.style.background = '#059669';
    } else {
      btnHeaderPause.textContent = '⏸️ Pause';
      btnHeaderPause.style.background = '#d97706';
    }
  }

  const diag = data.signal_diagnostics || data.diagnostics || {};
  renderDiagnostics(diag);
}

function renderDiagnostics(diag) {
  if (!diag) return;
  if (el.diagLastEval) {
    if (diag.last_evaluation_time) {
      const dt = new Date(diag.last_evaluation_time);
      el.diagLastEval.textContent = isNaN(dt.getTime()) ? diag.last_evaluation_time : dt.toLocaleTimeString();
    } else {
      el.diagLastEval.textContent = 'Idle';
    }
  }
  if (el.diagSignalsEvaluated) {
    el.diagSignalsEvaluated.textContent = diag.signals_evaluated || 0;
  }
  if (el.diagSignalDecisions) {
    el.diagSignalDecisions.textContent = `${diag.buy_signals || 0} / ${diag.sell_signals || 0} / ${diag.no_trade_decisions || 0}`;
  }
  if (el.diagRiskApproved) {
    el.diagRiskApproved.textContent = diag.risk_approved_signals || 0;
  }
  if (el.diagExecutedOrders) {
    el.diagExecutedOrders.textContent = diag.executed_paper_orders || 0;
  }
  if (el.diagRejectedOrders) {
    el.diagRejectedOrders.textContent = diag.rejected_orders || 0;
  }
  if (el.diagRecentRejection) {
    el.diagRecentRejection.textContent = diag.most_recent_rejection_reason || 'None';
  }
}

async function syncTradingViewContext() {
  try {
    const res = await fetch('/api/active_context');
    if (!res.ok) return;
    const data = await res.json();
    const ctx = data.active_context || {};

    if (data.synced && ctx.symbol && ctx.symbol !== 'UNVERIFIED') {
      if (el.healthTvSyncBadge) {
        el.healthTvSyncBadge.textContent = 'SYNCHRONIZED';
        el.healthTvSyncBadge.className = 'health-badge sync-active';
        el.healthTvSyncBadge.title = `Synced with TradingView active chart (${ctx.raw_symbol || ctx.symbol})`;
      }

      // Check if active TV chart differs from dashboard current selection
      const targetSymbol = ctx.raw_symbol || ctx.symbol;
      const targetInterval = ctx.interval || state.currentInterval;
      const targetMarket = ctx.market || state.currentMarket;

      if (targetSymbol !== state.currentSymbol || targetInterval !== state.currentInterval) {
        console.log(`[Dashboard] Syncing with active TradingView chart: ${targetSymbol} (${targetInterval})`);
        state.currentSymbol = targetSymbol;
        state.currentInterval = targetInterval;
        if (targetMarket) state.currentMarket = targetMarket;

        if (el.selectInterval) el.selectInterval.value = targetInterval;
        initWatchlist();
        fetchChartData();
      }
    } else {
      if (el.healthTvSyncBadge) {
        if (data.status === 'NO_VERIFIED_FEED' || ctx.status === 'NO_VERIFIED_FEED') {
          el.healthTvSyncBadge.textContent = 'NO VERIFIED FEED';
          el.healthTvSyncBadge.className = 'health-badge status-nofeed';
          el.healthTvSyncBadge.title = ctx.reason || 'No verified live feed available for instrument';
          state.activeBars = [];
          state.chartErrorMessage = `NO VERIFIED FEED: ${ctx.raw_symbol || ctx.symbol}\n${ctx.reason || 'No verified live feed available.'}\nAutomatic paper trading blocked.`;
          if (el.chartSymbol) el.chartSymbol.textContent = ctx.raw_symbol || ctx.symbol;
          if (el.chartDataBadge) {
            el.chartDataBadge.textContent = 'NO VERIFIED FEED';
            el.chartDataBadge.className = 'chart-badge error';
          }
          drawChart();
        } else if (data.status === 'UNSUPPORTED_INSTRUMENT' || ctx.status === 'UNSUPPORTED_INSTRUMENT') {
          el.healthTvSyncBadge.textContent = 'UNSUPPORTED INSTRUMENT';
          el.healthTvSyncBadge.className = 'health-badge status-unsupported';
          el.healthTvSyncBadge.title = ctx.reason || 'Instrument not supported';
          state.activeBars = [];
          state.chartErrorMessage = `UNSUPPORTED INSTRUMENT: ${ctx.raw_symbol || ctx.symbol}\n${ctx.reason || 'Instrument not supported'}\nAutomatic paper trading blocked.`;
          if (el.chartSymbol) el.chartSymbol.textContent = ctx.raw_symbol || ctx.symbol;
          if (el.chartDataBadge) {
            el.chartDataBadge.textContent = 'UNSUPPORTED';
            el.chartDataBadge.className = 'chart-badge error';
          }
          drawChart();
        } else if (data.status === 'UNVERIFIED') {
          el.healthTvSyncBadge.textContent = 'TV UNVERIFIED';
          el.healthTvSyncBadge.className = 'health-badge status-stale';
          el.healthTvSyncBadge.title = ctx.reason || 'TradingView chart context unverified';
        } else {
          el.healthTvSyncBadge.textContent = 'TRADINGVIEW NOT SYNCHRONIZED';
          el.healthTvSyncBadge.className = 'health-badge sync-off';
          el.healthTvSyncBadge.title = 'No active TradingView tab connected';
        }
      }
    }
  } catch (e) {
    console.warn('Failed to sync TV context:', e);
  }
}

function updateDataHealth(data) {
  const health = data.data_health || {};
  const status = health.feed_status || health.status || 'LIVE';
  state.lastHealthUpdateTs = health.last_updated_ts || Math.floor(Date.now() / 1000);

  if (el.healthStatusBadge) {
    el.healthStatusBadge.textContent = status;
    el.healthStatusBadge.className = 'health-badge';
    if (status === 'LIVE') {
      el.healthStatusBadge.classList.add('status-live');
    } else if (status.startsWith('STALE DATA')) {
      el.healthStatusBadge.classList.add('status-stale');
    } else if (status.includes('FALLBACK')) {
      el.healthStatusBadge.classList.add('status-fallback');
    } else if (status === 'DATA DISCONNECTED') {
      el.healthStatusBadge.classList.add('status-disconnected');
    } else {
      el.healthStatusBadge.classList.add('status-unsupported');
    }
  }

  if (el.healthTypeBadge) {
    const rawType = health.instrument_type || data.instrument_type || (data.market === 'CRYPTO' ? 'SPOT' : data.market || 'EQUITY');
    el.healthTypeBadge.textContent = String(rawType).replace('_', ' ').toUpperCase();
  }

  if (el.healthInstrumentVal) {
    el.healthInstrumentVal.textContent = data.display_name || data.symbol || state.currentSymbol;
  }
  if (el.healthIntervalVal) {
    el.healthIntervalVal.textContent = data.interval || state.currentInterval || '15m';
  }
  if (el.healthProviderVal) {
    el.healthProviderVal.textContent = health.provider || data.source || 'Live Feed';
  }
  if (el.healthUpdateVal) {
    if (health.last_updated_iso) {
      const dt = new Date(health.last_updated_iso);
      el.healthUpdateVal.textContent = isNaN(dt.getTime()) ? health.last_updated_iso : dt.toLocaleTimeString();
    } else {
      el.healthUpdateVal.textContent = new Date().toLocaleTimeString();
    }
  }
  if (el.healthAgeVal) {
    const age = typeof health.data_age_sec === 'number' ? health.data_age_sec : 0;
    el.healthAgeVal.textContent = `${age}s (feed)`;
  }
  if (el.healthCandleAgeVal) {
    const cAge = typeof health.candle_age_sec === 'number' ? health.candle_age_sec : 0;
    el.healthCandleAgeVal.textContent = `${cAge}s (bar)`;
  }
  if (el.healthCandlesVal) {
    const count = (data.bars && data.bars.length) || health.candle_count || 0;
    el.healthCandlesVal.textContent = `${count} / 80`;
  }
}

async function fetchChartData(isBackground = false) {
  state.chartErrorMessage = null;
  if (!isBackground) {
    el.chartDataBadge.textContent = 'Loading...';
    el.chartDataBadge.className = 'chart-badge';
  }
  const interval = state.currentInterval || '15m';
  if (el.chartInterval) {
    el.chartInterval.textContent = `${interval} Intraday`;
  }
  if (el.selectInterval) {
    el.selectInterval.value = interval;
  }

  try {
    const res = await fetch(`/api/chart?symbol=${encodeURIComponent(state.currentSymbol)}&market=${state.currentMarket}&interval=${interval}&count=80`);
    if (!res.ok) {
      let errMsg = `HTTP ${res.status}`;
      try {
        const errJson = await res.json();
        errMsg = errJson.error || errMsg;
      } catch (e) { }
      throw new Error(errMsg);
    }
    const data = await res.json();
    state.chartData = data;
    state.activeBars = data.bars || [];

    if (el.chartSymbol) {
      el.chartSymbol.textContent = data.display_name || data.symbol || state.currentSymbol;
    }
    el.chartDataBadge.textContent = data.source || data.feed_label || 'Live Data';
    el.chartDataBadge.className = 'chart-badge';
    if (data.regime) {
      updateRegimeBadge(data.regime);
    }
    updateDataHealth(data);
    drawChart();
  } catch (err) {
    state.activeBars = [];
    state.chartErrorMessage = err.message || 'Data Error';
    el.chartDataBadge.textContent = 'Data Error';
    el.chartDataBadge.className = 'chart-badge error';
    if (el.healthStatusBadge) {
      el.healthStatusBadge.textContent = 'DATA DISCONNECTED';
      el.healthStatusBadge.className = 'health-badge status-disconnected';
    }
    console.error('Failed to fetch chart:', err);
    drawChart();
  }
}

function updateRegimeBadge(regime) {
  const regimeColors = {
    TREND_BULL: '#00e676',
    TREND_BEAR: '#ff3d71',
    VOLATILE_EXPANSION: '#ffaa00',
    RANGE_BOUND: '#00d2ff',
    LOW_VOL_CHOPPY: '#8b9bb4',
  };
  const regType = regime.regime || 'UNKNOWN';
  el.regimeText.textContent = `${regType} (${Math.round(regime.confidence * 100)}%)`;
  el.regimeText.style.color = regimeColors[regType] || '#82b1ff';
}

async function analyzeSetup() {
  el.btnAnalyzeSetup.disabled = true;
  el.btnAnalyzeSetup.textContent = 'Analyzing...';
  const interval = state.currentInterval || '15m';
  try {
    const res = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        symbol: state.currentSymbol,
        market: state.currentMarket,
        interval: interval,
      })
    });
    const result = await res.json();
    state.currentAnalysis = result.analysis;
    renderAnalysisResult(result.analysis);
    fetchPositions();
  } catch (err) {
    console.error('Analysis failed:', err);
  } finally {
    el.btnAnalyzeSetup.disabled = false;
    el.btnAnalyzeSetup.textContent = '⚡ Analyze Setup';
  }
}

function renderAnalysisResult(analysis) {
  el.signalEmptyState.style.display = 'none';
  el.signalDetails.style.display = 'block';

  const isTrade = analysis.decision === 'TRADE';
  el.signalDecision.textContent = analysis.decision;
  el.signalDecision.className = isTrade ? 'badge badge-trade' : 'badge badge-no-trade';
  el.signalStrategy.textContent = analysis.selected_strategy || 'None';

  if (analysis.regime) {
    updateRegimeBadge(analysis.regime);
  }

  const curr = (state.systemStatus && state.systemStatus.currency === 'USD') ? '$' : '₹';
  const ticket = analysis.ticket;

  if (ticket) {
    el.ticketSymbol.textContent = ticket.symbol;
    el.ticketSide.textContent = ticket.side;
    el.ticketSide.style.color = ticket.side === 'BUY' ? 'var(--green)' : 'var(--red)';
    el.ticketEntry.textContent = `${curr}${formatNumber(ticket.entry_price)}`;
    el.ticketSL.textContent = `${curr}${formatNumber(ticket.stop_loss)}`;
    el.ticketTP.textContent = `${curr}${formatNumber(ticket.take_profit)}`;
    el.ticketRR.textContent = `${ticket.planned_rr} R (Net)`;
    el.ticketQty.textContent = formatNumber(ticket.quantity);
    el.ticketCapital.textContent = `${curr}${formatNumber(ticket.allocated_capital)}`;
    el.ticketRisk.textContent = `${curr}${formatNumber(ticket.risk_amount)}`;

    const friction = ticket.cost_breakdown ? ticket.cost_breakdown.total_friction : 0;
    el.ticketFriction.textContent = `${curr}${formatNumber(friction)}`;

    el.btnExecuteOrder.disabled = !isTrade;
    el.ticketReasonsBox.style.display = 'none';
  } else {
    el.ticketSymbol.textContent = state.currentSymbol;
    el.ticketSide.textContent = '-';
    el.ticketEntry.textContent = '-';
    el.ticketSL.textContent = '-';
    el.ticketTP.textContent = '-';
    el.ticketRR.textContent = 'NO TRADE';
    el.ticketQty.textContent = '0';
    el.ticketCapital.textContent = `${curr}0.00`;
    el.ticketRisk.textContent = `${curr}0.00`;
    el.ticketFriction.textContent = `${curr}0.00`;

    el.btnExecuteOrder.disabled = true;

    // Show reasons
    if (analysis.reasons && analysis.reasons.length > 0) {
      el.ticketReasonsBox.style.display = 'block';
      el.ticketReasonsText.textContent = analysis.reasons.join(' | ');
    } else {
      el.ticketReasonsBox.style.display = 'none';
    }
  }

  // Redraw chart with planned target lines
  drawChart();
}

async function executeOrder() {
  if (!state.currentAnalysis || !state.currentAnalysis.ticket) return;

  const isLive = state.systemStatus && state.systemStatus.mode === 'live';
  if (isLive) {
    const confirmed = confirm('CRITICAL: You are in LIVE TRADING mode. Confirm order execution with authorized broker?');
    if (!confirmed) return;
  }

  el.btnExecuteOrder.disabled = true;
  el.btnExecuteOrder.textContent = 'Executing...';

  try {
    const res = await fetch('/api/order', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ticket: state.currentAnalysis.ticket,
        confirm_live: isLive,
      })
    });
    const orderResult = await res.json();
    if (orderResult.success) {
      alert(`Order Placed Successfully!\nID: ${orderResult.order_id}\nFill Price: ${orderResult.fill_price}`);
      fetchPositions();
      fetchStatus();
    } else {
      alert(`Order Rejected: ${orderResult.reason || 'Safety gate violation'}`);
    }
  } catch (err) {
    alert(`Failed to place order: ${err}`);
  } finally {
    el.btnExecuteOrder.disabled = false;
    el.btnExecuteOrder.textContent = isLive ? '🚀 Execute Live Order' : '🚀 Execute Order (Paper)';
  }
}

async function fetchPositions() {
  try {
    const res = await fetch('/api/positions');
    const data = await res.json();
    const positions = data.positions || [];
    el.countPositions.textContent = positions.length;

    if (positions.length === 0) {
      el.positionsTableBody.innerHTML = '<tr><td colspan="11" class="text-center text-muted">No open positions.</td></tr>';
      return;
    }

    const curr = (state.systemStatus && state.systemStatus.currency === 'USD') ? '$' : '₹';
    el.positionsTableBody.innerHTML = positions.map(p => {
      const pnl = p.unrealized_pnl || 0;
      const pnlClass = pnl >= 0 ? 'text-success' : 'text-danger';
      const sideClass = p.side === 'BUY' ? 'text-success' : 'text-danger';
      return `
        <tr>
          <td><strong>${p.symbol}</strong></td>
          <td class="${sideClass}">${p.side}</td>
          <td>${formatNumber(p.quantity)}</td>
          <td>${curr}${formatNumber(p.entry_price)}</td>
          <td>${curr}${formatNumber(p.current_price)}</td>
          <td class="text-danger">${curr}${formatNumber(p.stop_loss)}</td>
          <td class="text-success">${curr}${formatNumber(p.take_profit)}</td>
          <td class="${pnlClass} font-bold">${curr}${formatNumber(pnl)}</td>
          <td class="${pnlClass}">${p.unrealized_pnl_pct || 0}%</td>
          <td>${p.strategy || 'Trend'}</td>
          <td>
            <button class="btn btn-sm btn-outline-danger" onclick="closePosition('${p.symbol}')">Close</button>
          </td>
        </tr>
      `;
    }).join('');
  } catch (err) {
    console.error('Failed to fetch positions:', err);
  }
}

window.closePosition = async function (symbol) {
  try {
    const res = await fetch('/api/close_position', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol })
    });
    const result = await res.json();
    if (result.success) {
      fetchPositions();
      fetchStatus();
      fetchTrades();
    } else {
      alert(`Close failed: ${result.reason}`);
    }
  } catch (err) {
    alert(`Error closing position: ${err}`);
  }
};

async function fetchTrades() {
  try {
    const res = await fetch('/api/trades');
    const data = await res.json();
    const trades = data.trade_history || [];
    const stats = data.stats || {};

    el.countTrades.textContent = trades.length;
    el.statTotalTrades.textContent = stats.total_trades || 0;
    el.statWinLoss.textContent = `${stats.winning_trades || 0} / ${stats.losing_trades || 0}`;
    el.statWinRatePct.textContent = `${stats.win_rate_pct || 0}%`;
    el.statProfitFactor.textContent = (stats.profit_factor || 0).toFixed(2);
    el.statAvgR.textContent = `${(stats.average_r || 0).toFixed(2)} R`;

    const curr = (state.systemStatus && state.systemStatus.currency === 'USD') ? '$' : '₹';
    const netPnl = stats.net_pnl || 0;
    el.statNetPnL.textContent = `${curr}${formatNumber(netPnl)}`;
    el.statNetPnL.className = netPnl >= 0 ? 'text-success font-bold' : 'text-danger font-bold';

    if (trades.length === 0) {
      el.tradesTableBody.innerHTML = '<tr><td colspan="11" class="text-center text-muted">No trade history yet.</td></tr>';
      return;
    }

    el.tradesTableBody.innerHTML = trades.slice(-30).reverse().map(t => {
      const net = t.net_pnl || 0;
      const netClass = net >= 0 ? 'text-success' : 'text-danger';
      return `
        <tr>
          <td><small>${t.trade_id}</small></td>
          <td><strong>${t.symbol}</strong></td>
          <td>${t.side}</td>
          <td>${formatNumber(t.quantity)}</td>
          <td>${curr}${formatNumber(t.entry_price)}</td>
          <td>${curr}${formatNumber(t.exit_price)}</td>
          <td>${curr}${formatNumber(t.gross_pnl)}</td>
          <td class="text-muted">${curr}${formatNumber(t.total_friction)}</td>
          <td class="${netClass} font-bold">${curr}${formatNumber(net)}</td>
          <td class="${netClass}">${(t.r_multiple || 0).toFixed(2)} R</td>
          <td><span class="badge ${t.exit_reason === 'TAKE_PROFIT_3R' ? 'badge-trade' : 'badge-no-trade'}">${t.exit_reason}</span></td>
        </tr>
      `;
    }).join('');
  } catch (err) {
    console.error('Failed to fetch trades:', err);
  }
}

async function triggerEmergencyStop() {
  const confirmed = confirm('CRITICAL: Trigger Emergency Stop? All open positions will be immediately liquidated and pending orders cancelled!');
  if (!confirmed) return;

  try {
    const res = await fetch('/api/emergency_stop', { method: 'POST' });
    const result = await res.json();
    alert(`EMERGENCY STOP TRIGGERED!\nCancelled Orders: ${result.cancelled_orders}\nLiquidated Positions: ${result.liquidated_positions ? result.liquidated_positions.length : 0}`);
    fetchStatus();
    fetchPositions();
    fetchTrades();
  } catch (err) {
    alert(`Emergency Stop failed: ${err}`);
  }
}

async function openSettingsModal() {
  try {
    const res = await fetch('/api/config');
    const cfg = await res.json();
    document.getElementById('cfgMode').value = cfg.mode || 'paper';
    document.getElementById('cfgBroker').value = cfg.broker || 'PAPER';
    document.getElementById('cfgBalance').value = cfg.balance || 100000;
    document.getElementById('cfgCurrency').value = cfg.currency || 'INR';
    document.getElementById('cfgMaxRiskPct').value = cfg.max_risk_pct || 1.0;
    document.getElementById('cfgMaxDailyLoss').value = cfg.max_daily_loss || 3000;
    document.getElementById('cfgMinAllocation').value = cfg.min_allocation || 1000;
    document.getElementById('cfgMaxAllocation').value = cfg.max_allocation || 50000;
    document.getElementById('cfgMaxExposure').value = cfg.max_open_exposure || 75000;
    document.getElementById('cfgMaxPositions').value = cfg.max_concurrent_positions || 3;
    const cfgAuto = document.getElementById('cfgAutoPaperExecution');
    if (cfgAuto) cfgAuto.value = String(!!cfg.auto_execute_paper);
    const cfgPause = document.getElementById('cfgPauseNewEntries');
    if (cfgPause) cfgPause.value = String(!!cfg.new_entries_paused);
    document.getElementById('liveWarningBox').style.display = cfg.mode === 'live' ? 'block' : 'none';
    el.modalSettings.style.display = 'flex';
  } catch (err) {
    alert('Failed to load settings: ' + err);
  }
}

async function saveSettings() {
  const newConfig = {
    mode: document.getElementById('cfgMode').value,
    broker: document.getElementById('cfgBroker').value,
    balance: parseFloat(document.getElementById('cfgBalance').value),
    currency: document.getElementById('cfgCurrency').value,
    max_risk_pct: parseFloat(document.getElementById('cfgMaxRiskPct').value),
    max_daily_loss: parseFloat(document.getElementById('cfgMaxDailyLoss').value),
    min_allocation: parseFloat(document.getElementById('cfgMinAllocation').value),
    max_allocation: parseFloat(document.getElementById('cfgMaxAllocation').value),
    max_open_exposure: parseFloat(document.getElementById('cfgMaxExposure').value),
    max_concurrent_positions: parseInt(document.getElementById('cfgMaxPositions').value, 10),
    auto_execute_paper: document.getElementById('cfgAutoPaperExecution') ? document.getElementById('cfgAutoPaperExecution').value === 'true' : false,
    new_entries_paused: document.getElementById('cfgPauseNewEntries') ? document.getElementById('cfgPauseNewEntries').value === 'true' : false,
  };

  try {
    const res = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(newConfig)
    });
    const result = await res.json();
    if (result.success) {
      el.modalSettings.style.display = 'none';
      fetchStatus();
    } else {
      alert('Save failed: ' + result.reason);
    }
  } catch (err) {
    alert('Error saving settings: ' + err);
  }
}

async function togglePauseEntries() {
  const isPaused = state.systemStatus && state.systemStatus.new_entries_paused;
  const endpoint = isPaused ? '/api/resume' : '/api/pause';
  try {
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}'
    });
    const data = await res.json();
    if (state.systemStatus) {
      state.systemStatus.new_entries_paused = !!data.new_entries_paused;
    }
    await fetchStatus();
  } catch (err) {
    alert(`Failed to update pause state: ${err}`);
  }
}

async function closeAllPositions() {
  const confirmed = confirm('Close ALL open paper positions immediately?');
  if (!confirmed) return;
  try {
    const res = await fetch('/api/close_all', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}'
    });
    const data = await res.json();
    await fetchPositions();
    await fetchStatus();
    await fetchTrades();
  } catch (err) {
    console.error(`Failed to close positions: ${err}`);
  }
}

async function confirmResetAccount() {
  try {
    const res = await fetch('/api/reset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm: true })
    });
    const result = await res.json();
    if (result.success) {
      el.modalReset.style.display = 'none';
      alert('Account reset successfully!');
      fetchStatus();
      fetchPositions();
      fetchTrades();
    } else {
      alert('Reset failed: ' + result.reason);
    }
  } catch (err) {
    alert('Reset error: ' + err);
  }
}

// Independent Chart Canvas Renderer
let ctx = null;

function initChartCanvas() {
  ctx = el.chartCanvas.getContext('2d');
  window.addEventListener('resize', drawChart);

  // Crosshair & Tooltip
  el.chartCanvas.addEventListener('mousemove', (e) => {
    if (!state.activeBars || state.activeBars.length === 0) return;
    const rect = el.chartCanvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;
    renderCrosshair(mouseX, mouseY);
  });

  el.chartCanvas.addEventListener('mouseleave', () => {
    el.chartTooltip.style.display = 'none';
    drawChart();
  });
}

function drawChart() {
  if (!ctx) return;

  const canvas = el.chartCanvas;
  if (!canvas || !canvas.parentElement) return;

  const rect = canvas.parentElement.getBoundingClientRect();
  const width = Math.max(200, rect.width || 800);
  const height = Math.max(150, rect.height || 450);
  canvas.width = width * window.devicePixelRatio;
  canvas.height = height * window.devicePixelRatio;
  ctx.scale(window.devicePixelRatio, window.devicePixelRatio);

  // Clear background
  ctx.fillStyle = '#0b0e14';
  ctx.fillRect(0, 0, width, height);

  if (!state.activeBars || state.activeBars.length === 0) {
    ctx.fillStyle = state.chartErrorMessage ? '#f87171' : '#8b9bb4';
    ctx.font = '13px SF Mono, monospace';
    ctx.textAlign = 'center';
    const msg = state.chartErrorMessage || 'Select an instrument or click Refresh to load real market candles.';
    const lines = msg.split('\n');
    const startY = (height / 2) - ((lines.length - 1) * 11);
    lines.forEach((line, idx) => {
      ctx.fillText(line, width / 2, startY + (idx * 22));
    });
    return;
  }

  const bars = state.activeBars;
  const n = bars.length;
  const paddingRight = 60;
  const paddingBottom = 25;
  const chartW = width - paddingRight;
  const chartH = height - paddingBottom;

  // Min and max prices
  let minPrice = Infinity;
  let maxPrice = -Infinity;

  bars.forEach(b => {
    if (b.low != null && b.low < minPrice) minPrice = b.low;
    if (b.high != null && b.high > maxPrice) maxPrice = b.high;
    if (b.bb_lower != null && !isNaN(b.bb_lower) && b.bb_lower < minPrice) minPrice = b.bb_lower;
    if (b.bb_upper != null && !isNaN(b.bb_upper) && b.bb_upper > maxPrice) maxPrice = b.bb_upper;
  });

  // Include planned ticket lines in price bounds
  if (state.currentAnalysis && state.currentAnalysis.ticket) {
    const t = state.currentAnalysis.ticket;
    minPrice = Math.min(minPrice, t.stop_loss, t.entry_price, t.take_profit);
    maxPrice = Math.max(maxPrice, t.stop_loss, t.entry_price, t.take_profit);
  }

  const priceRange = (maxPrice - minPrice) || 1.0;
  minPrice -= priceRange * 0.05;
  maxPrice += priceRange * 0.05;
  const finalRange = maxPrice - minPrice;

  const getY = (val) => chartH - ((val - minPrice) / finalRange) * chartH;
  const getX = (i) => (i / Math.max(1, n - 1)) * (chartW - 20) + 10;
  const barWidth = Math.max(2, (chartW / n) * 0.65);

  // Draw Grid lines
  ctx.strokeStyle = '#1b2230';
  ctx.lineWidth = 1;
  const gridSteps = 6;
  ctx.fillStyle = '#8b9bb4';
  ctx.font = '10px SF Mono, monospace';
  ctx.textAlign = 'left';

  for (let i = 0; i <= gridSteps; i++) {
    const yPrice = minPrice + (finalRange * (i / gridSteps));
    const yPos = getY(yPrice);
    ctx.beginPath();
    ctx.moveTo(0, yPos);
    ctx.lineTo(chartW, yPos);
    ctx.stroke();

    // Price label on right
    ctx.fillText(formatNumber(yPrice), chartW + 6, yPos + 3);
  }

  // Draw X-axis time labels along the bottom
  ctx.fillStyle = '#8b9bb4';
  ctx.font = '9px SF Mono, monospace';
  ctx.textAlign = 'center';
  const timeStep = Math.max(1, Math.floor(n / 6));
  for (let i = 0; i < n; i += timeStep) {
    const xPos = getX(i);
    const b = bars[i];
    let timeStr = '';
    if (b.datetime) {
      const parts = b.datetime.split(' ');
      timeStr = parts.length > 1 ? parts[1].substring(0, 5) : parts[0];
    } else if (b.time) {
      const d = new Date(b.time * 1000);
      timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
    }
    ctx.fillText(timeStr, xPos, height - 8);
  }

  // Draw Bollinger Bands cloud
  ctx.fillStyle = 'rgba(64, 120, 255, 0.06)';
  ctx.beginPath();
  let firstBB = true;
  for (let i = 0; i < n; i++) {
    if (bars[i].bb_upper != null) {
      const x = getX(i);
      const y = getY(bars[i].bb_upper);
      if (firstBB) { ctx.moveTo(x, y); firstBB = false; } else { ctx.lineTo(x, y); }
    }
  }
  for (let i = n - 1; i >= 0; i--) {
    if (bars[i].bb_lower != null) {
      const x = getX(i);
      const y = getY(bars[i].bb_lower);
      ctx.lineTo(x, y);
    }
  }
  ctx.closePath();
  ctx.fill();

  // Draw EMAs
  drawLineIndicator(bars, 'ema50', '#bc8cff', 1.2, getX, getY);
  drawLineIndicator(bars, 'ema21', '#ffaa00', 1.5, getX, getY);
  drawLineIndicator(bars, 'ema9', '#00d2ff', 1.5, getX, getY);

  // Draw Candlesticks
  for (let i = 0; i < n; i++) {
    const b = bars[i];
    const x = getX(i);
    const isBull = b.close >= b.open;
    const candleColor = isBull ? '#00e676' : '#ff3d71';

    ctx.strokeStyle = candleColor;
    ctx.lineWidth = 1;

    // Wick
    ctx.beginPath();
    ctx.moveTo(x, getY(b.high));
    ctx.lineTo(x, getY(b.low));
    ctx.stroke();

    // Body
    ctx.fillStyle = candleColor;
    const bodyTop = getY(Math.max(b.open, b.close));
    const bodyBottom = getY(Math.min(b.open, b.close));
    const bodyHeight = Math.max(1, bodyBottom - bodyTop);

    ctx.fillRect(x - (barWidth / 2), bodyTop, barWidth, bodyHeight);
  }

  // Draw Target Lines if Ticket Exists
  if (state.currentAnalysis && state.currentAnalysis.ticket) {
    const t = state.currentAnalysis.ticket;
    drawTargetLine(t.entry_price, '#00d2ff', 'ENTRY', chartW, getY);
    drawTargetLine(t.stop_loss, '#ff3d71', 'STOP LOSS', chartW, getY);
    drawTargetLine(t.take_profit, '#00e676', 'TAKE PROFIT (3R)', chartW, getY);
  }
}

function drawLineIndicator(bars, prop, color, lineWidth, getX, getY) {
  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
  ctx.beginPath();
  let started = false;
  for (let i = 0; i < bars.length; i++) {
    if (bars[i][prop] != null) {
      const x = getX(i);
      const y = getY(bars[i][prop]);
      if (!started) {
        ctx.moveTo(x, y);
        started = true;
      } else {
        ctx.lineTo(x, y);
      }
    }
  }
  ctx.stroke();
}

function drawTargetLine(price, color, label, chartW, getY) {
  const y = getY(price);
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.setLineDash([6, 4]);

  ctx.beginPath();
  ctx.moveTo(0, y);
  ctx.lineTo(chartW, y);
  ctx.stroke();

  // Label badge
  ctx.setLineDash([]);
  ctx.fillStyle = color;
  ctx.font = 'bold 9px SF Mono, monospace';
  ctx.fillText(`◄ ${label} ${formatNumber(price)}`, chartW - 140, y - 4);
  ctx.restore();
}

function renderCrosshair(mouseX, mouseY) {
  drawChart(); // Redraw base
  const bars = state.activeBars;
  const n = bars.length;
  const chartW = el.chartCanvas.getBoundingClientRect().width - 60;
  const index = Math.round((mouseX / chartW) * (n - 1));

  if (index >= 0 && index < n) {
    const b = bars[index];
    ctx.save();
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.3)';
    ctx.setLineDash([4, 4]);

    // Vertical line
    ctx.beginPath();
    ctx.moveTo(mouseX, 0);
    ctx.lineTo(mouseX, el.chartCanvas.height);
    ctx.stroke();

    // Horizontal line
    ctx.beginPath();
    ctx.moveTo(0, mouseY);
    ctx.lineTo(chartW, mouseY);
    ctx.stroke();
    ctx.restore();

    // Tooltip
    el.chartTooltip.style.display = 'block';
    el.chartTooltip.innerHTML = `
      <div><strong>${b.datetime || 'Time'}</strong></div>
      <div>O: ${b.open} | H: ${b.high} | L: ${b.low} | C: <strong>${b.close}</strong></div>
      <div style="color: #00d2ff;">EMA9: ${b.ema9 || '-'} | EMA21: ${b.ema21 || '-'}</div>
      <div style="color: #bc8cff;">RSI: ${b.rsi || '-'} | ADX: ${b.adx || '-'}</div>
    `;
  }
}

// Utilities
function formatNumber(num) {
  if (num === null || num === undefined || isNaN(num)) return '0.00';
  return Number(num).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

