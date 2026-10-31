/**
 * AI Trading Copilot - Popup Controller
 * Connects extension popup to local Copilot REST API (http://127.0.0.1:8765)
 */

const API_BASE = 'http://127.0.0.1:8765/api';

let currentAnalysis = null;
let systemMode = 'paper';
let newEntriesPaused = false;

document.addEventListener('DOMContentLoaded', () => {
  fetchCopilotStatus();
  bindPopupEvents();
});

function bindPopupEvents() {
  document.getElementById('btnSyncTab').addEventListener('click', syncSymbolFromActiveTab);
  document.getElementById('btnAnalyze').addEventListener('click', runCopilotAnalysis);
  document.getElementById('btnExecute').addEventListener('click', executeOrderFromPopup);
  document.getElementById('btnEmergencyStop').addEventListener('click', triggerKillSwitch);

  const checkAuto = document.getElementById('popupCheckAutoPaper');
  if (checkAuto) {
    checkAuto.addEventListener('change', async (e) => {
      try {
        await fetch(`${API_BASE}/config`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ auto_execute_paper: e.target.checked })
        });
        fetchCopilotStatus();
      } catch (err) {
        console.error('Failed to update auto paper execution:', err);
      }
    });
  }

  const btnPause = document.getElementById('popupBtnPauseEntries');
  if (btnPause) {
    btnPause.addEventListener('click', async () => {
      const endpoint = newEntriesPaused ? `${API_BASE}/resume` : `${API_BASE}/pause`;
      try {
        const res = await fetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
        const data = await res.json();
        newEntriesPaused = !!data.new_entries_paused;
        updatePauseButton();
      } catch (err) {
        alert('Failed to toggle pause: ' + err);
      }
    });
  }
}

function updatePauseButton() {
  const btnPause = document.getElementById('popupBtnPauseEntries');
  if (!btnPause) return;
  if (newEntriesPaused) {
    btnPause.textContent = '▶️ Resume';
    btnPause.style.borderColor = '#059669';
    btnPause.style.color = '#059669';
  } else {
    btnPause.textContent = '⏸️ Pause';
    btnPause.style.borderColor = '#d97706';
    btnPause.style.color = '#d97706';
  }
}

async function fetchCopilotStatus() {
  try {
    const res = await fetch(`${API_BASE}/status`);
    const data = await res.json();
    systemMode = data.mode || 'paper';
    newEntriesPaused = !!data.new_entries_paused;

    const curr = data.currency === 'INR' ? '₹' : '$';
    document.getElementById('balanceVal').textContent = `${curr}${Number(data.balance).toLocaleString()}`;
    document.getElementById('modeBadge').textContent = systemMode.toUpperCase();
    document.getElementById('modeBadge').className = systemMode === 'live' ? 'badge' : 'badge';
    if (systemMode === 'live') {
      document.getElementById('modeBadge').style.borderColor = 'var(--red)';
      document.getElementById('modeBadge').style.color = 'var(--red)';
    }

    const checkAuto = document.getElementById('popupCheckAutoPaper');
    if (checkAuto) {
      checkAuto.checked = !!data.auto_execute_paper;
    }
    updatePauseButton();
  } catch (err) {
    document.getElementById('balanceVal').textContent = 'Server Offline';
    document.getElementById('regimeVal').textContent = 'Run copilot server';
  }
}

async function syncSymbolFromActiveTab() {
  const syncStatusEl = document.getElementById('syncStatus');
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab && tab.id) {
      chrome.tabs.sendMessage(tab.id, { action: 'GET_ACTIVE_SYMBOL' }, (response) => {
        if (chrome.runtime.lastError || !response) {
          // Tab fallback: parse title if URL looks like tradingview
          if (tab.title && (tab.url || '').includes('tradingview.com')) {
            const sym = tab.title.split(' ')[0];
            document.getElementById('symbolInput').value = sym;
            if (syncStatusEl) {
              syncStatusEl.style.display = 'block';
              syncStatusEl.textContent = 'TradingView tab detected. Candles analyzed via Copilot backend (not DOM scraping).';
            }
          } else {
            if (syncStatusEl) {
              syncStatusEl.style.display = 'block';
              syncStatusEl.textContent = 'Active tab is not TradingView. Enter ticker manually.';
            }
          }
        } else if (response.symbol) {
          document.getElementById('symbolInput').value = response.symbol;
          if (response.interval && document.getElementById('intervalSelect')) {
            document.getElementById('intervalSelect').value = response.interval;
          }
          if (syncStatusEl) {
            syncStatusEl.style.display = 'block';
            syncStatusEl.textContent = response.dataNotice || 'TradingView tab synced. Evaluating via Copilot backend.';
          }
        }
      });
    }
  } catch (e) {
    console.error('Failed to sync symbol:', e);
  }
}

async function runCopilotAnalysis() {
  const sym = document.getElementById('symbolInput').value.trim();
  const intervalEl = document.getElementById('intervalSelect');
  const interval = intervalEl ? intervalEl.value : '15m';
  const btn = document.getElementById('btnAnalyze');
  btn.disabled = true;
  btn.textContent = 'Analyzing...';

  try {
    const res = await fetch(`${API_BASE}/analyze`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol: sym, interval: interval })
    });
    const data = await res.json();
    currentAnalysis = data.analysis;
    renderAnalysis(data.analysis);
  } catch (err) {
    alert('Analysis failed: Server offline or network error.');
  } finally {
    btn.disabled = false;
    btn.textContent = '⚡ Analyze Setup (3:1 R:R)';
  }
}

function renderAnalysis(analysis) {
  const container = document.getElementById('signalContainer');
  container.style.display = 'flex';

  if (analysis.regime) {
    document.getElementById('regimeVal').textContent = `${analysis.regime.regime}`;
  }

  const isTrade = analysis.decision === 'TRADE';
  const decBadge = document.getElementById('decisionBadge');
  decBadge.textContent = analysis.decision;
  decBadge.style.color = isTrade ? 'var(--green)' : 'var(--red)';
  decBadge.style.borderColor = isTrade ? 'var(--green)' : 'var(--red)';

  document.getElementById('strategyName').textContent = analysis.selected_strategy || 'None';

  const ticket = analysis.ticket;
  if (ticket) {
    document.getElementById('tEntry').textContent = ticket.entry_price;
    document.getElementById('tSL').textContent = ticket.stop_loss;
    document.getElementById('tTP').textContent = ticket.take_profit;
    document.getElementById('tRR').textContent = `${ticket.planned_rr} R (Net)`;
    document.getElementById('tQty').textContent = ticket.quantity;

    document.getElementById('btnExecute').disabled = !isTrade;
    document.getElementById('reasonsBox').style.display = 'none';
  } else {
    document.getElementById('tEntry').textContent = '-';
    document.getElementById('tSL').textContent = '-';
    document.getElementById('tTP').textContent = '-';
    document.getElementById('tRR').textContent = 'NO TRADE';
    document.getElementById('tQty').textContent = '0';

    document.getElementById('btnExecute').disabled = true;
    if (analysis.reasons && analysis.reasons.length > 0) {
      document.getElementById('reasonsBox').style.display = 'block';
      document.getElementById('reasonsBox').textContent = analysis.reasons.join(' | ');
    }
  }
}

async function executeOrderFromPopup() {
  if (!currentAnalysis || !currentAnalysis.ticket) return;

  const isLive = systemMode === 'live';
  if (isLive) {
    const ok = confirm('CRITICAL: Execute live order with authorized broker?');
    if (!ok) return;
  }

  try {
    const res = await fetch(`${API_BASE}/order`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ticket: currentAnalysis.ticket,
        confirm_live: isLive,
      })
    });
    const result = await res.json();
    if (result.success) {
      alert(`Order placed successfully!\nID: ${result.order_id}`);
      fetchCopilotStatus();
    } else {
      alert(`Order rejected: ${result.reason}`);
    }
  } catch (err) {
    alert(`Order failed: ${err}`);
  }
}

async function triggerKillSwitch() {
  const ok = confirm('CRITICAL: Trigger Emergency Kill Switch? Liquidates all open positions and cancels all pending orders!');
  if (!ok) return;
  try {
    const res = await fetch(`${API_BASE}/emergency_stop`, { method: 'POST' });
    const result = await res.json();
    alert(`Emergency Stop triggered!\nOrders Cancelled: ${result.cancelled_orders}`);
    fetchCopilotStatus();
  } catch (err) {
    alert(`Emergency stop failed: ${err}`);
  }
}

