/**
 * AI Trading Copilot - TradingView In-Page Companion
 * Injects an interactive, collapsible Copilot panel directly into TradingView.
 * Automatically detects the active instrument and timeframe, queries real market data
 * through the local Copilot backend, evaluates regimes, and enforces 3:1 net R:R.
 */

(function () {
  'use strict';

  // Prevent duplicate injection
  if (window.__AI_COPILOT_INJECTED__) return;
  window.__AI_COPILOT_INJECTED__ = true;

  const STATE = {
    connected: false,
    symbol: 'NSE:NIFTY1!',
    mappedSymbol: '^NSEI',
    interval: '15m',
    market: 'NSE',
    feedLabel: 'Delayed (15m NSE Feed - Nifty 50 Index Proxy)',
    isProxy: true,
    proxyNotice: null,
    regime: null,
    analysis: null,
    ticket: null,
    lastUpdate: null,
    unverified: false,
    unverifiedReason: null,
    autoRefresh: true,
    autoTimer: null,
    autoExecutePaper: false,
    newEntriesPaused: false,
    activePosition: null,
    isDragging: false,
    dragOffset: { x: 0, y: 0 }
  };

  // ---------------------------------------------------------------- Initialization
  function init() {
    if (!isTradingViewChartPage()) {
      console.log('[Copilot] Waiting for TradingView chart container...');
    }
    
    // Inject panel once DOM is ready
    if (document.body) {
      injectPanel();
    } else {
      document.addEventListener('DOMContentLoaded', injectPanel);
    }

    // Monitor for TradingView Single Page Application (SPA) navigation
    observeChartChanges();

    // Listen for extension popup queries
    if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.onMessage) {
      chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
        if (request.action === 'GET_ACTIVE_SYMBOL') {
          const ctx = detectTradingViewContext();
          sendResponse(ctx);
        }
        return true;
      });
    }
  }

  function isTradingViewChartPage() {
    const path = window.location.pathname || '';
    return path.includes('/chart') || path.includes('/symbols');
  }

  // ---------------------------------------------------------------- Context Detection
  function detectTradingViewContext() {
    const title = (document.title || '').trim();
    const urlParams = new URLSearchParams(window.location.search);
    const paramSymbol = urlParams.get('symbol');
    const paramInterval = urlParams.get('interval');

    // 0. Check for 404 / Page Not Found / Unverified state
    const titleLower = title.toLowerCase();
    if (titleLower.includes('page not found') || titleLower.includes('404')) {
      return {
        symbol: 'UNVERIFIED',
        interval: '15m',
        isTradingView: true,
        unverified: true,
        reason: 'CHART CONTEXT UNVERIFIED - TradingView page not found or layout expired.'
      };
    }

    let rawSymbol = null;
    let rawInterval = null;

    // 1. Primary: Visible Chart DOM Elements (Highest Priority)
    rawSymbol = getSymbolFromDom();

    // 2. Secondary: Document Title (e.g. "BTCUSD 77,560 ▲ +0.48%" or "NIFTY1! 23,098.9 ▲ +0.22%")
    if (!rawSymbol && title) {
      rawSymbol = parseSymbolFromTitle(title);
    }

    // Reconcile with URL query parameter for exchange qualification if consistent
    if (rawSymbol && paramSymbol) {
      const cleanParam = decodeURIComponent(paramSymbol).trim();
      const paramTicker = cleanParam.includes(':') ? cleanParam.split(':')[1].trim() : cleanParam;
      if (paramTicker.toUpperCase() === rawSymbol.toUpperCase()) {
        rawSymbol = cleanParam; // Use the verified exchange-qualified ticker
      }
    } else if (!rawSymbol && paramSymbol) {
      // 3. Fallback: URL Query parameter (only if title / DOM didn't contradict)
      rawSymbol = decodeURIComponent(paramSymbol).trim();
    }

    // 4. Fallback: URL Pathname (e.g. /symbols/NSE-NIFTY1!/ or /symbols/BTCUSD/)
    if (!rawSymbol) {
      const path = window.location.pathname || '';
      if (path.includes('/symbols/')) {
        const parts = path.split('/symbols/')[1].split('/')[0];
        if (parts) {
          rawSymbol = decodeURIComponent(parts).replace('-', ':').trim();
        }
      }
    }

    // Interval detection
    rawInterval = getIntervalFromDom();
    if (!rawInterval) {
      if (paramInterval) {
        rawInterval = normalizeInterval(paramInterval);
      } else {
        const match = title.match(/\b(1m|2m|5m|15m|30m|60m|1h|1d|1|2|5|15|30|60|D)\b/i);
        if (match) {
          rawInterval = normalizeInterval(match[1]);
        } else if (rawSymbol && rawSymbol.includes('NIFTY1!')) {
          rawInterval = '15m';
        } else {
          rawInterval = '15m';
        }
      }
    }

    if (!rawSymbol || rawSymbol.toUpperCase() === 'UNVERIFIED') {
      return {
        symbol: 'UNVERIFIED',
        interval: rawInterval || '15m',
        isTradingView: true,
        unverified: true,
        reason: 'CHART CONTEXT UNVERIFIED - NO TRADE'
      };
    }

    return {
      symbol: cleanRawSymbol(rawSymbol),
      interval: rawInterval || '15m',
      isTradingView: true,
      unverified: false,
      dataNotice: 'Active ticker detected. Candle data evaluated via authorized Copilot backend feed.'
    };
  }

  const NON_SYMBOL_WORDS = new Set(['NSE', 'BSE', 'VOL', 'VOLUME', 'SEARCH', 'COMPARE', 'INDICATORS', 'ALERT', 'REPLAY', 'BARS']);

  function getSymbolFromDom() {
    // 1. Check header symbol search toolbar button (most authoritative)
    const searchBtn = document.querySelector('#header-toolbar-symbol-search');
    if (searchBtn) {
      const text = (searchBtn.innerText || searchBtn.textContent || '').trim();
      const firstLine = text.split('\n')[0].trim();
      if (firstLine && firstLine.length >= 2 && firstLine.length <= 25 && !NON_SYMBOL_WORDS.has(firstLine.toUpperCase())) {
        return cleanRawSymbol(firstLine);
      }
    }

    // 2. Check legend series item
    const legendItem = document.querySelector('[data-name="legend-series-item"]');
    if (legendItem) {
      const titleEl = legendItem.querySelector('[class*="title-"]');
      if (titleEl) {
        const text = (titleEl.innerText || titleEl.textContent || '').trim();
        const cand = text.split('\n')[0].split(' ')[0].trim();
        if (cand && cand.length >= 2 && cand.length <= 25 && !NON_SYMBOL_WORDS.has(cand.toUpperCase())) {
          return cleanRawSymbol(cand);
        }
      }
    }

    return null;
  }

  function parseSymbolFromTitle(title) {
    if (!title) return null;
    const t = title.trim();
    if (t.toLowerCase().includes('page not found') || t.toLowerCase().includes('404')) {
      return null;
    }
    // Clean unicode arrows, dashes, commas
    const cleaned = t.replace(/[\u25b2\u25bc\u2014\u2013-]/g, ' ').trim();
    const parts = cleaned.split(/[\s,]+/);
    if (parts.length > 0) {
      const cand = parts[0].trim();
      if (cand.toLowerCase() === 'bitcoin') return 'BTCUSD';
      if (cand.toLowerCase() === 'ethereum') return 'ETHUSD';
      if (cand.toLowerCase() === 'solana') return 'SOLUSD';
      if (cand.length >= 2 && cand.length <= 20 && !cand.toLowerCase().includes('tradingview')) {
        return cleanRawSymbol(cand);
      }
    }
    return null;
  }

  function getIntervalFromDom() {
    const intBtn = document.querySelector('#header-toolbar-intervals') ||
                   document.querySelector('[data-name="time-interval"]') ||
                   document.querySelector('[data-name="intervals-menu-button"]');
    if (intBtn) {
      const text = (intBtn.innerText || intBtn.textContent || '').trim();
      if (text) {
        return normalizeInterval(text);
      }
    }
    return null;
  }

  function cleanRawSymbol(sym) {
    if (!sym) return '';
    return sym.replace(/[,;'"\r\n]/g, '').trim();
  }

  function normalizeInterval(raw) {
    if (!raw) return '15m';
    const s = String(raw).trim().toLowerCase();
    if (s === '1' || s === '1m') return '1m';
    if (s === '2' || s === '2m') return '2m';
    if (s === '5' || s === '5m') return '5m';
    if (s === '15' || s === '15m') return '15m';
    if (s === '30' || s === '30m') return '30m';
    if (s === '60' || s === '60m' || s === '1h' || s === 'h') return '60m';
    if (s === 'd' || s === '1d' || s === 'daily') return '1d';
    return '15m';
  }

  // ---------------------------------------------------------------- UI Injection
  function injectPanel() {
    if (document.getElementById('ai-copilot-tv-container')) return;

    const container = document.createElement('div');
    container.id = 'ai-copilot-tv-container';
    container.innerHTML = `
      <!-- Header -->
      <div class="tv-copilot-header" id="tvCopilotDragHeader">
        <div class="tv-copilot-title-box">
          <span class="tv-copilot-logo">⚡</span>
          <span class="tv-copilot-title">AI COPILOT</span>
          <span class="tv-copilot-version">v2.1</span>
          <span class="tv-copilot-status-dot connecting" id="tvStatusDot" title="Connecting to Copilot server..."></span>
        </div>
        <div class="tv-copilot-controls">
          <button class="tv-copilot-btn-icon" id="tvBtnCollapse" title="Collapse / Expand Panel">_</button>
          <button class="tv-copilot-btn-icon" id="tvBtnClose" title="Minimize to pill">x</button>
        </div>
      </div>

      <!-- Main Body -->
      <div class="tv-copilot-body" id="tvCopilotBody">
        <!-- Diagnostic Alert -->
        <div class="tv-copilot-alert" id="tvDiagnosticAlert">
          <strong>Backend Offline</strong>
          <div id="tvDiagnosticText">Copilot server at 127.0.0.1:8765 is unreachable.</div>
        </div>

        <!-- Context Card -->
        <div class="tv-copilot-card">
          <div class="tv-copilot-row">
            <span class="tv-copilot-label">Chart Symbol:</span>
            <span class="tv-copilot-val" id="tvSymbolVal">NSE:NIFTY1!</span>
          </div>
          <div class="tv-copilot-row">
            <span class="tv-copilot-label">Market Data Feed:</span>
            <span class="tv-copilot-val" id="tvFeedLabel" style="font-size: 10px; color: #38bdf8;">Connecting...</span>
          </div>
          <div class="tv-copilot-row">
            <span class="tv-copilot-label">Timeframe:</span>
            <select class="tv-copilot-select" id="tvIntervalSelect">
              <option value="1m">1m</option>
              <option value="5m">5m</option>
              <option value="15m" selected>15m</option>
              <option value="30m">30m</option>
              <option value="60m">1h (60m)</option>
              <option value="1d">1D (Daily)</option>
            </select>
          </div>
          <div class="tv-copilot-row">
            <span class="tv-copilot-label">Data Freshness:</span>
            <span class="tv-copilot-val" id="tvFreshnessVal" style="font-size: 10px; color: #94a3b8;">Pending initial scan</span>
          </div>
          <div class="tv-copilot-row" id="tvProxyNoticeRow" style="display: none;">
            <div id="tvProxyNoticeText" style="font-size: 10px; color: #f59e0b; background: rgba(245, 158, 11, 0.1); border: 1px solid rgba(245, 158, 11, 0.25); border-radius: 4px; padding: 4px; line-height: 1.3; width: 100%;"></div>
          </div>
        </div>

        <!-- Regime & Strategy Card -->
        <div class="tv-copilot-card">
          <div class="tv-copilot-row">
            <span class="tv-copilot-label">Detected Regime:</span>
            <span class="tv-copilot-badge tv-copilot-badge-regime" id="tvRegimeBadge">Detecting...</span>
          </div>
          <div class="tv-copilot-row">
            <span class="tv-copilot-label">Active Strategy:</span>
            <span class="tv-copilot-val" id="tvStrategyVal">None</span>
          </div>
        </div>

        <!-- Signal & Decision Box -->
        <div class="tv-copilot-decision-box notrade" id="tvDecisionBox">
          <div class="tv-copilot-decision-header">
            <span class="tv-copilot-label">Signal:</span>
            <span class="tv-copilot-badge tv-copilot-badge-notrade" id="tvDecisionBadge">SCANNING...</span>
          </div>
          <div class="tv-copilot-decision-rationale" id="tvRationaleText">
            Evaluating real market candles...
          </div>

          <!-- Trade Ticket Details Grid -->
          <div class="tv-copilot-ticket-grid" id="tvTicketGrid" style="display: none;">
            <div class="tv-copilot-ticket-item">
              <span class="tv-copilot-ticket-label">Entry</span>
              <span class="tv-copilot-ticket-val" id="tvTicketEntry">-</span>
            </div>
            <div class="tv-copilot-ticket-item">
              <span class="tv-copilot-ticket-label">Stop Loss</span>
              <span class="tv-copilot-ticket-val red" id="tvTicketSL">-</span>
            </div>
            <div class="tv-copilot-ticket-item">
              <span class="tv-copilot-ticket-label">Take Profit</span>
              <span class="tv-copilot-ticket-val green" id="tvTicketTP">-</span>
            </div>
            <div class="tv-copilot-ticket-item">
              <span class="tv-copilot-ticket-label">Planned Net R:R</span>
              <span class="tv-copilot-ticket-val gold" id="tvTicketRR">-</span>
            </div>
            <div class="tv-copilot-ticket-item">
              <span class="tv-copilot-ticket-label">Quantity</span>
              <span class="tv-copilot-ticket-val" id="tvTicketQty">-</span>
            </div>
            <div class="tv-copilot-ticket-item">
              <span class="tv-copilot-ticket-label">Risk at Stake</span>
              <span class="tv-copilot-ticket-val red" id="tvTicketRisk">-</span>
            </div>
          </div>
        </div>

        <!-- Active Position Card (if open) -->
        <div class="tv-copilot-card" id="tvActivePosCard" style="display: none; border: 1px solid #38bdf8; background: rgba(56, 189, 248, 0.08);">
          <div class="tv-copilot-row" style="margin-bottom: 4px;">
            <strong style="color: #38bdf8;">Open Paper Position:</strong>
            <span id="tvPosSideBadge" class="tv-copilot-badge tv-copilot-badge-buy">BUY</span>
          </div>
          <div class="tv-copilot-row">
            <span class="tv-copilot-label">Qty / Entry:</span>
            <span class="tv-copilot-val" id="tvPosQtyEntry">-</span>
          </div>
          <div class="tv-copilot-row">
            <span class="tv-copilot-label">Unrealized P&L:</span>
            <span class="tv-copilot-val" id="tvPosPnL" style="font-weight: bold; color: #00e676;">₹0.00</span>
          </div>
          <div class="tv-copilot-row" style="margin-top: 6px;">
            <button class="tv-copilot-btn tv-copilot-btn-danger" id="tvBtnClosePos" style="width: 100%; padding: 4px 8px; font-size: 11px;">
              Close Position
            </button>
          </div>
        </div>

        <!-- Actions -->
        <div class="tv-copilot-actions">
          <button class="tv-copilot-btn tv-copilot-btn-primary" id="tvBtnRefresh">
            <span>⚡</span> Refresh Analysis
          </button>
          <button class="tv-copilot-btn tv-copilot-btn-success" id="tvBtnExecute" disabled>
            <span>🚀</span> Execute Paper Order
          </button>
          <div style="display: flex; gap: 6px; width: 100%;">
            <button class="tv-copilot-btn tv-copilot-btn-warning" id="tvBtnPauseEntries" style="flex: 1; font-size: 10px; padding: 5px 4px;">
              ⏸️ Pause Entries
            </button>
            <button class="tv-copilot-btn tv-copilot-btn-danger" id="tvBtnKillSwitch" style="flex: 1; font-size: 10px; padding: 5px 4px;">
              🛑 Kill Switch
            </button>
          </div>
          <button class="tv-copilot-btn tv-copilot-btn-secondary" id="tvBtnDashboard">
            <span>📈</span> Open Full Dashboard
          </button>
          
          <div class="tv-copilot-toggle-row">
            <span>Auto-Scan (30s):</span>
            <input type="checkbox" id="tvCheckAuto" checked>
          </div>
          <div class="tv-copilot-toggle-row">
            <span style="color: #a78bfa; font-weight: 600;">Auto Paper Exec:</span>
            <input type="checkbox" id="tvCheckAutoPaper">
          </div>
        </div>
      </div>

      <!-- Toast -->
      <div class="tv-copilot-toast" id="tvCopilotToast"></div>
    `;

    document.body.appendChild(container);

    // Bind event listeners
    bindPanelEvents();

    // Initial context update & scan
    syncWithPageContext();
  }

  // ---------------------------------------------------------------- Panel Events
  function bindPanelEvents() {
    const container = document.getElementById('ai-copilot-tv-container');
    const header = document.getElementById('tvCopilotDragHeader');
    const btnCollapse = document.getElementById('tvBtnCollapse');
    const btnClose = document.getElementById('tvBtnClose');
    const btnRefresh = document.getElementById('tvBtnRefresh');
    const btnExecute = document.getElementById('tvBtnExecute');
    const btnDashboard = document.getElementById('tvBtnDashboard');
    const btnPauseEntries = document.getElementById('tvBtnPauseEntries');
    const btnKillSwitch = document.getElementById('tvBtnKillSwitch');
    const btnClosePos = document.getElementById('tvBtnClosePos');
    const intervalSelect = document.getElementById('tvIntervalSelect');
    const checkAuto = document.getElementById('tvCheckAuto');
    const checkAutoPaper = document.getElementById('tvCheckAutoPaper');

    // Dragging logic
    header.addEventListener('mousedown', (e) => {
      if (e.target.tagName === 'BUTTON') return;
      STATE.isDragging = true;
      const rect = container.getBoundingClientRect();
      STATE.dragOffset.x = e.clientX - rect.left;
      STATE.dragOffset.y = e.clientY - rect.top;
      e.preventDefault();
    });

    document.addEventListener('mousemove', (e) => {
      if (!STATE.isDragging) return;
      const x = Math.max(10, Math.min(window.innerWidth - container.offsetWidth - 10, e.clientX - STATE.dragOffset.x));
      const y = Math.max(10, Math.min(window.innerHeight - container.offsetHeight - 10, e.clientY - STATE.dragOffset.y));
      container.style.left = `${x}px`;
      container.style.top = `${y}px`;
      container.style.right = 'auto';
    });

    document.addEventListener('mouseup', () => {
      STATE.isDragging = false;
    });

    // Collapse toggle
    btnCollapse.addEventListener('click', () => {
      container.classList.toggle('collapsed');
      btnCollapse.textContent = container.classList.contains('collapsed') ? '+' : '_';
    });

    // Close / minimize to pill
    btnClose.addEventListener('click', () => {
      container.classList.add('collapsed');
      btnCollapse.textContent = '+';
    });

    // Timeframe selector
    intervalSelect.addEventListener('change', (e) => {
      STATE.interval = e.target.value;
      runAnalysis();
    });

    // Refresh button
    btnRefresh.addEventListener('click', () => {
      runAnalysis();
    });

    // Execute button
    btnExecute.addEventListener('click', () => {
      executePaperTrade();
    });

    // Dashboard link
    btnDashboard.addEventListener('click', () => {
      window.open('http://127.0.0.1:8765', '_blank');
    });

    // Auto-refresh toggle
    checkAuto.addEventListener('change', (e) => {
      STATE.autoRefresh = e.target.checked;
      setupAutoRefresh();
    });

    // Auto Paper Execution toggle
    checkAutoPaper.addEventListener('change', async (e) => {
      STATE.autoExecutePaper = e.target.checked;
      try {
        await apiCall('/config', 'POST', { auto_execute_paper: STATE.autoExecutePaper });
        showToast(STATE.autoExecutePaper ? 'Auto Paper Execution: ENABLED' : 'Auto Paper Execution: DISABLED');
      } catch (err) {
        console.error('Failed to update auto paper config:', err);
      }
    });

    // Pause New Entries toggle button
    btnPauseEntries.addEventListener('click', async () => {
      try {
        const endpoint = STATE.newEntriesPaused ? '/resume' : '/pause';
        const res = await apiCall(endpoint, 'POST', {});
        STATE.newEntriesPaused = !!res.new_entries_paused;
        updatePauseButtonState(btnPauseEntries, STATE.newEntriesPaused);
        showToast(STATE.newEntriesPaused ? 'New Entries PAUSED' : 'New Entries ACTIVE');
      } catch (err) {
        showToast(`Pause failed: ${err.message}`);
      }
    });

    // Kill switch button
    btnKillSwitch.addEventListener('click', async () => {
      if (!confirm('Are you sure you want to trigger the EMERGENCY KILL SWITCH? This will liquidate open paper positions.')) return;
      try {
        const res = await apiCall('/emergency_stop', 'POST', {});
        showToast('Kill Switch Activated! Orders cancelled & positions closed.');
        runAnalysis();
      } catch (err) {
        showToast(`Kill switch error: ${err.message}`);
      }
    });

    // Close current position button
    if (btnClosePos) {
      btnClosePos.addEventListener('click', async () => {
        try {
          const res = await apiCall('/close_position', 'POST', { symbol: STATE.mappedSymbol });
          if (res.success) {
            showToast(`Position closed for ${STATE.mappedSymbol}`);
            runAnalysis();
          } else {
            showToast(`Close failed: ${res.reason || 'Unknown'}`);
          }
        } catch (err) {
          showToast(`Close error: ${err.message}`);
        }
      });
    }

    setupAutoRefresh();
  }

  function updatePauseButtonState(btn, isPaused) {
    if (!btn) return;
    if (isPaused) {
      btn.textContent = '▶️ Resume Entries';
      btn.style.background = '#059669';
    } else {
      btn.textContent = '⏸️ Pause Entries';
      btn.style.background = '#d97706';
    }
  }

  function setupAutoRefresh() {
    if (STATE.autoTimer) {
      clearInterval(STATE.autoTimer);
      STATE.autoTimer = null;
    }
    if (STATE.autoRefresh) {
      STATE.autoTimer = setInterval(() => {
        runAnalysis(true);
      }, 30000); // Poll every 30 seconds
    }
  }

  // ---------------------------------------------------------------- Backend Communication
  async function apiCall(endpoint, method = 'GET', body = null, timeout = 8000) {
    if (typeof chrome === 'undefined' || !chrome.runtime || !chrome.runtime.sendMessage) {
      const err = new Error('Extension runtime unavailable. Refresh TradingView page.');
      err.errorType = 'SERVICE_WORKER_UNAVAILABLE';
      err.status = 503;
      throw err;
    }

    return new Promise((resolve, reject) => {
      try {
        chrome.runtime.sendMessage(
          { action: 'COPILOT_API_REQUEST', endpoint: endpoint, method: method, body: body, timeout: timeout },
          (response) => {
            if (chrome.runtime.lastError) {
              const lastErrMsg = chrome.runtime.lastError.message || '';
              const isInvalidated = lastErrMsg.includes('invalidated') || lastErrMsg.includes('Receiving end does not exist');
              const err = new Error(
                isInvalidated
                  ? 'Service worker unavailable: Extension reloaded. Refresh TradingView tab to reconnect.'
                  : `Extension message error: ${lastErrMsg}`
              );
              err.errorType = 'SERVICE_WORKER_UNAVAILABLE';
              err.status = 503;
              reject(err);
              return;
            }

            if (!response) {
              const err = new Error('No response from extension background service worker.');
              err.errorType = 'SERVICE_WORKER_UNAVAILABLE';
              err.status = 503;
              reject(err);
              return;
            }

            if (!response.success) {
              const err = new Error(response.error || 'Backend communication error');
              err.status = response.status || null;
              err.errorType = response.errorType || 'HTTP_ERROR';
              err.data = response.data || null;
              reject(err);
              return;
            }

            resolve(response.data);
          }
        );
      } catch (sendErr) {
        const err = new Error('Failed to send message to service worker: ' + sendErr.message);
        err.errorType = 'SERVICE_WORKER_UNAVAILABLE';
        err.status = 503;
        reject(err);
      }
    });
  }

  async function directFetch(endpoint, method, body) {
    const url = `http://127.0.0.1:8765/api${endpoint.startsWith('/') ? endpoint : '/' + endpoint}`;
    const opts = {
      method: method,
      headers: { 'Content-Type': 'application/json' }
    };
    if (body && (method === 'POST' || method === 'PUT')) {
      opts.body = typeof body === 'string' ? body : JSON.stringify(body);
    }
    const res = await fetch(url, opts);
    const json = await res.json().catch(() => ({}));
    if (!res.ok) {
      const msg = json.error || (json.analysis && json.analysis.reasons && json.analysis.reasons[0]) || `HTTP ${res.status}`;
      const err = new Error(msg);
      err.status = res.status;
      err.data = json;
      throw err;
    }
    return json;
  }

  // ---------------------------------------------------------------- Analysis Workflow
  let lastAnnouncedKey = null;
  let lastAnnounceTime = 0;
  let isSyncingContext = false;

  async function syncWithPageContext(force = false) {
    if (isSyncingContext) return;
    isSyncingContext = true;

    try {
      const ctx = detectTradingViewContext();
      STATE.symbol = ctx.symbol;
      STATE.interval = ctx.interval;
      STATE.unverified = !!ctx.unverified;
      STATE.unverifiedReason = ctx.reason || null;

      const symEl = document.getElementById('tvSymbolVal');
      const intervalSelect = document.getElementById('tvIntervalSelect');
      if (symEl) symEl.textContent = STATE.symbol;
      if (intervalSelect) intervalSelect.value = STATE.interval;

      // Deduplicate active context announcements to avoid duplicate POSTs
      const currentKey = `${ctx.symbol}|${ctx.interval}|${!!ctx.unverified}|${ctx.reason || ''}`;
      const now = Date.now();
      const shouldAnnounce = force || (currentKey !== lastAnnouncedKey) || (now - lastAnnounceTime > 25000);

      if (shouldAnnounce && ctx.symbol && ctx.symbol !== 'UNVERIFIED') {
        lastAnnouncedKey = currentKey;
        lastAnnounceTime = now;
        try {
          await apiCall('/active_context', 'POST', {
            raw_symbol: ctx.symbol,
            symbol: ctx.symbol,
            interval: ctx.interval,
            source: 'tradingview',
            unverified: !!ctx.unverified,
            reason: ctx.reason || null
          });
        } catch (e) {
          if (e.status !== 400 && e.status !== 404) {
            console.warn(`[Copilot] Active context announcement: ${e.errorType || 'ERROR'} - ${e.message}`);
          }
        }
      }

      try {
        const statusRes = await apiCall('/status', 'GET');
        if (statusRes && statusRes.config) {
          STATE.autoExecutePaper = !!statusRes.config.auto_execute_paper;
          STATE.newEntriesPaused = !!statusRes.config.new_entries_paused;
          const checkAutoPaper = document.getElementById('tvCheckAutoPaper');
          if (checkAutoPaper) checkAutoPaper.checked = STATE.autoExecutePaper;
          const btnPause = document.getElementById('tvBtnPauseEntries');
          if (btnPause) updatePauseButtonState(btnPause, STATE.newEntriesPaused);
        }
      } catch (_) {}

      await runAnalysis();
    } finally {
      isSyncingContext = false;
    }
  }

  async function runAnalysis(isBackground = false) {
    const dot = document.getElementById('tvStatusDot');
    const alertBox = document.getElementById('tvDiagnosticAlert');
    const alertText = document.getElementById('tvDiagnosticText');
    const btnRefresh = document.getElementById('tvBtnRefresh');

    if (!isBackground && btnRefresh) {
      btnRefresh.disabled = true;
      btnRefresh.textContent = 'Analyzing...';
    }

    // If context is unverified (e.g. 404 / Page Not Found), report directly without invalid fetch
    if (STATE.unverified || STATE.symbol === 'UNVERIFIED') {
      STATE.connected = true;
      if (dot) dot.className = 'tv-copilot-status-dot online';
      if (alertBox) alertBox.classList.remove('visible');
      renderUnverifiedState(STATE.unverifiedReason || 'CHART CONTEXT UNVERIFIED - NO TRADE');
      if (!isBackground && btnRefresh) {
        btnRefresh.disabled = false;
        btnRefresh.textContent = '⚡ Refresh Analysis';
      }
      return;
    }

    try {
      if (dot) dot.className = 'tv-copilot-status-dot connecting';

      const data = await apiCall('/analyze', 'POST', {
        symbol: STATE.symbol,
        interval: STATE.interval,
        auto_execute: STATE.autoExecutePaper
      });

      STATE.connected = true;
      STATE.analysis = data.analysis;
      STATE.mappedSymbol = data.mapped_symbol || STATE.symbol;
      STATE.feedLabel = data.feed_label || 'Active Data Feed';
      STATE.isProxy = !!data.is_proxy;
      STATE.proxyNotice = data.proxy_notice || null;
      STATE.lastUpdate = new Date().toLocaleTimeString();

      if (dot) dot.className = 'tv-copilot-status-dot online';
      if (alertBox) alertBox.classList.remove('visible');

      if (typeof data.auto_execute_paper === 'boolean') {
        STATE.autoExecutePaper = data.auto_execute_paper;
        const checkAutoPaper = document.getElementById('tvCheckAutoPaper');
        if (checkAutoPaper) checkAutoPaper.checked = STATE.autoExecutePaper;
      }
      if (typeof data.new_entries_paused === 'boolean') {
        STATE.newEntriesPaused = data.new_entries_paused;
        const btnPause = document.getElementById('tvBtnPauseEntries');
        if (btnPause) updatePauseButtonState(btnPause, STATE.newEntriesPaused);
      }

      renderAnalysisResult(data);

      if (data.analysis && data.analysis.auto_execution && data.analysis.auto_execution.executed) {
        showToast(`Auto Paper Executed! Order: ${data.analysis.auto_execution.order_id} @ ${formatPrice(data.analysis.auto_execution.fill_price)}`);
      }

      syncActivePositions();
    } catch (err) {
      const isHttpAppError = err && (err.status === 400 || err.status === 404 || err.status === 422 || (err.data && (err.data.status || err.data.analysis)));
      if (isHttpAppError) {
        // Backend is online and answered with structured response (e.g. 400 for unsupported ticker)
        STATE.connected = true;
        if (dot) dot.className = 'tv-copilot-status-dot online';
        if (alertBox) alertBox.classList.remove('visible');

        const rejectionReason = (err.data && err.data.analysis && err.data.analysis.reasons && err.data.analysis.reasons[0]) ||
                                (err.data && err.data.error) ||
                                err.message ||
                                'Unsupported market instrument';
        renderNoTradeState(rejectionReason);
      } else {
        // Connection, service worker, or network failure
        STATE.connected = false;
        if (dot) dot.className = 'tv-copilot-status-dot offline';
        if (alertBox) {
          alertBox.classList.add('visible');
          if (err.errorType === 'CONNECTION_REFUSED' || (err.status === 503 && !err.data)) {
            alertText.textContent = 'Backend Offline: Copilot server is not running on 127.0.0.1:8765. Start: python -m copilot.server';
          } else if (err.errorType === 'SERVICE_WORKER_UNAVAILABLE') {
            alertText.textContent = 'Service Worker Inactive: Extension reloaded. Refresh TradingView tab to reconnect.';
          } else if (err.errorType === 'TIMEOUT') {
            alertText.textContent = 'Backend Timeout: Request to 127.0.0.1:8765 timed out.';
          } else if (err.status >= 500) {
            alertText.textContent = `Backend Server Error (HTTP ${err.status}): ${err.message}`;
          } else {
            alertText.textContent = err.message || 'Cannot connect to 127.0.0.1:8765';
          }
        }
        renderErrorState(err.message || 'Backend offline on 127.0.0.1:8765');
      }
    } finally {
      if (!isBackground && btnRefresh) {
        btnRefresh.disabled = false;
        btnRefresh.textContent = '⚡ Refresh Analysis';
      }
    }
  }

  function renderAnalysisResult(data) {
    const feedLabelEl = document.getElementById('tvFeedLabel');
    const freshnessEl = document.getElementById('tvFreshnessVal');
    const regimeBadge = document.getElementById('tvRegimeBadge');
    const strategyVal = document.getElementById('tvStrategyVal');
    const decisionBox = document.getElementById('tvDecisionBox');
    const decisionBadge = document.getElementById('tvDecisionBadge');
    const rationaleText = document.getElementById('tvRationaleText');
    const ticketGrid = document.getElementById('tvTicketGrid');
    const btnExecute = document.getElementById('tvBtnExecute');
    const proxyRow = document.getElementById('tvProxyNoticeRow');
    const proxyText = document.getElementById('tvProxyNoticeText');

    if (feedLabelEl) feedLabelEl.textContent = data.feed_label || 'Delayed Feed';
    if (freshnessEl) freshnessEl.textContent = `Updated ${STATE.lastUpdate}`;

    if (proxyRow && proxyText) {
      if (data.is_proxy && data.proxy_notice) {
        proxyRow.style.display = 'block';
        proxyText.textContent = data.proxy_notice;
      } else {
        proxyRow.style.display = 'none';
      }
    }

    const analysis = data.analysis || {};
    const regime = analysis.regime || {};
    const regType = regime.regime || 'UNKNOWN';

    if (regimeBadge) {
      regimeBadge.textContent = regType;
      const regimeColors = {
        TREND_BULL: '#00e676',
        TREND_BEAR: '#ff3d71',
        VOLATILE_EXPANSION: '#ffaa00',
        RANGE_BOUND: '#00d2ff',
        LOW_VOL_CHOPPY: '#8b9bb4'
      };
      regimeBadge.style.color = regimeColors[regType] || '#38bdf8';
    }

    if (strategyVal) {
      strategyVal.textContent = analysis.selected_strategy || 'None';
    }

    const isTrade = analysis.decision === 'TRADE';
    const ticket = analysis.ticket;
    STATE.ticket = ticket;

    if (isTrade && ticket) {
      decisionBox.className = `tv-copilot-decision-box ${ticket.action.toLowerCase()}`;
      decisionBadge.className = `tv-copilot-badge tv-copilot-badge-${ticket.action.toLowerCase()}`;
      decisionBadge.textContent = `${ticket.action} (>= 3.0 R:R)`;
      rationaleText.textContent = `Valid setup: Target Net R:R ${ticket.net_rr}x. Sized to ${ticket.quantity} units within risk bounds.`;

      ticketGrid.style.display = 'grid';
      document.getElementById('tvTicketEntry').textContent = formatPrice(ticket.entry_price);
      document.getElementById('tvTicketSL').textContent = formatPrice(ticket.stop_loss);
      document.getElementById('tvTicketTP').textContent = formatPrice(ticket.take_profit);
      document.getElementById('tvTicketRR').textContent = `${ticket.net_rr} R (Net)`;
      document.getElementById('tvTicketQty').textContent = ticket.quantity;
      document.getElementById('tvTicketRisk').textContent = formatPrice(ticket.max_loss);

      if (btnExecute) btnExecute.disabled = false;
    } else {
      decisionBox.className = 'tv-copilot-decision-box notrade';
      decisionBadge.className = 'tv-copilot-badge tv-copilot-badge-notrade';
      decisionBadge.textContent = 'NO TRADE';

      const reasons = analysis.reasons || [];
      rationaleText.textContent = reasons.length > 0
        ? reasons.join(' | ')
        : 'Market conditions do not fulfill the required 3:1 net reward-to-risk threshold.';

      ticketGrid.style.display = 'none';
      if (btnExecute) btnExecute.disabled = true;
    }
  }

  function renderNoTradeState(reason) {
    const feedLabelEl = document.getElementById('tvFeedLabel');
    const freshnessEl = document.getElementById('tvFreshnessVal');
    const regimeBadge = document.getElementById('tvRegimeBadge');
    const strategyVal = document.getElementById('tvStrategyVal');
    const decisionBox = document.getElementById('tvDecisionBox');
    const decisionBadge = document.getElementById('tvDecisionBadge');
    const rationaleText = document.getElementById('tvRationaleText');
    const ticketGrid = document.getElementById('tvTicketGrid');
    const btnExecute = document.getElementById('tvBtnExecute');
    const proxyRow = document.getElementById('tvProxyNoticeRow');

    if (feedLabelEl) feedLabelEl.textContent = 'Unsupported / No Feed';
    if (freshnessEl) freshnessEl.textContent = `Updated ${new Date().toLocaleTimeString()}`;
    if (proxyRow) proxyRow.style.display = 'none';

    if (regimeBadge) {
      regimeBadge.textContent = 'UNKNOWN';
      regimeBadge.style.color = '#8b9bb4';
    }
    if (strategyVal) strategyVal.textContent = 'None';

    if (decisionBox) decisionBox.className = 'tv-copilot-decision-box notrade';
    if (decisionBadge) {
      decisionBadge.className = 'tv-copilot-badge tv-copilot-badge-notrade';
      decisionBadge.textContent = 'NO TRADE';
    }
    if (rationaleText) {
      rationaleText.textContent = reason;
    }
    if (ticketGrid) ticketGrid.style.display = 'none';
    if (btnExecute) btnExecute.disabled = true;
    STATE.ticket = null;
  }

  function renderUnverifiedState(msg) {
    renderNoTradeState(msg);
    const feedLabelEl = document.getElementById('tvFeedLabel');
    if (feedLabelEl) feedLabelEl.textContent = 'Context Unverified';
  }

  function renderErrorState(errMsg) {
    const decisionBadge = document.getElementById('tvDecisionBadge');
    const rationaleText = document.getElementById('tvRationaleText');
    const ticketGrid = document.getElementById('tvTicketGrid');
    const btnExecute = document.getElementById('tvBtnExecute');

    if (decisionBadge) {
      decisionBadge.className = 'tv-copilot-badge tv-copilot-badge-notrade';
      decisionBadge.textContent = 'OFFLINE';
    }
    if (rationaleText) {
      rationaleText.textContent = errMsg || 'Backend error occurred during market data retrieval.';
    }
    if (ticketGrid) ticketGrid.style.display = 'none';
    if (btnExecute) btnExecute.disabled = true;
    STATE.ticket = null;
  }

  function formatPrice(val) {
    if (val == null) return '-';
    return Number(val).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  async function executePaperTrade() {
    if (!STATE.ticket) return;
    const btnExecute = document.getElementById('tvBtnExecute');
    btnExecute.disabled = true;
    btnExecute.textContent = 'Placing Order...';

    try {
      const res = await apiCall('/order', 'POST', {
        ticket: STATE.ticket,
        confirm_live: false
      });
      if (res.success) {
        showToast(`Paper Order Filled! ID: ${res.order_id || 'OK'}`);
        syncActivePositions();
      } else {
        showToast(`Order Rejected: ${res.reason || 'Unknown error'}`);
      }
    } catch (err) {
      showToast(`Order Failed: ${err.message}`);
    } finally {
      btnExecute.disabled = false;
      btnExecute.textContent = '🚀 Execute Paper Order';
    }
  }

  async function syncActivePositions() {
    try {
      const res = await apiCall('/positions', 'GET');
      const positions = (res && res.positions) || [];
      const currentSym = STATE.mappedSymbol || STATE.symbol;
      const active = positions.find(p => p.symbol === currentSym || p.symbol === STATE.symbol);

      const card = document.getElementById('tvActivePosCard');
      const sideBadge = document.getElementById('tvPosSideBadge');
      const qtyEntry = document.getElementById('tvPosQtyEntry');
      const pnlEl = document.getElementById('tvPosPnL');

      if (active && card) {
        STATE.activePosition = active;
        card.style.display = 'block';
        if (sideBadge) {
          sideBadge.textContent = active.side || 'BUY';
          sideBadge.className = `tv-copilot-badge tv-copilot-badge-${(active.side || 'BUY').toLowerCase()}`;
        }
        if (qtyEntry) {
          qtyEntry.textContent = `${active.quantity} @ ${formatPrice(active.entry_price)}`;
        }
        if (pnlEl) {
          const pnl = active.unrealized_pnl || 0;
          const pnlPct = active.unrealized_pnl_pct != null ? active.unrealized_pnl_pct.toFixed(2) : '0.00';
          const sign = pnl >= 0 ? '+' : '';
          pnlEl.textContent = `${sign}${formatPrice(pnl)} (${sign}${pnlPct}%)`;
          pnlEl.style.color = pnl >= 0 ? '#00e676' : '#ff3d71';
        }
      } else if (card) {
        STATE.activePosition = null;
        card.style.display = 'none';
      }
    } catch (e) {
      console.warn('[Copilot] Failed to sync active positions:', e);
    }
  }

  function showToast(msg) {
    const toast = document.getElementById('tvCopilotToast');
    if (!toast) return;
    toast.textContent = msg;
    toast.classList.add('show');
    setTimeout(() => {
      toast.classList.remove('show');
    }, 4000);
  }

  // ---------------------------------------------------------------- Chart Change Observer
  function observeChartChanges() {
    let lastUrl = window.location.href;
    let lastTitle = document.title;

    // Check periodically for SPA URL / title changes
    setInterval(() => {
      const currentUrl = window.location.href;
      const currentTitle = document.title;

      if (currentUrl !== lastUrl || currentTitle !== lastTitle) {
        lastUrl = currentUrl;
        lastTitle = currentTitle;
        console.log('[Copilot] Chart context change detected:', currentUrl);
        syncWithPageContext();
      }
    }, 2000);

    // Patch history pushState and replaceState for instant URL detection
    const origPush = history.pushState;
    history.pushState = function () {
      origPush.apply(this, arguments);
      setTimeout(syncWithPageContext, 200);
    };

    const origReplace = history.replaceState;
    history.replaceState = function () {
      origReplace.apply(this, arguments);
      setTimeout(syncWithPageContext, 200);
    };

    window.addEventListener('popstate', () => {
      setTimeout(syncWithPageContext, 200);
    });

    // Synchronize immediately when user focuses or switches back to this tab
    window.addEventListener('focus', () => {
      syncWithPageContext();
    });
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) {
        syncWithPageContext();
      }
    });
  }

  // Run initialization
  init();
})();
