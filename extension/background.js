/**
 * AI Trading Copilot: Service Worker (Manifest V3)
 * Proxies backend requests to local Copilot server (http://127.0.0.1:8765)
 * to avoid any mixed-content or CORS restrictions in page contexts.
 */

const SERVER_BASE = 'http://127.0.0.1:8765/api';

chrome.runtime.onInstalled.addListener(() => {
  console.log('[Copilot ServiceWorker] Extension v2.1.0 installed and ready.');
});

// Message listener for content scripts and popup
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'COPILOT_API_REQUEST') {
    handleApiRequest(request)
      .then((data) => sendResponse({
        success: true,
        data: data,
        status: 200
      }))
      .catch((err) => sendResponse({
        success: false,
        error: err.message || String(err),
        errorType: err.errorType || 'NETWORK_ERROR',
        status: err.status || null,
        data: err.data || null
      }));
    return true; // Keep message channel open for async response
  }

  if (request.action === 'COPILOT_HEALTH_CHECK') {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 4000);
    fetch(`${SERVER_BASE}/health`, { method: 'GET', signal: controller.signal })
      .then((res) => {
        clearTimeout(timeoutId);
        return res.json();
      })
      .then((data) => sendResponse({ success: true, data: data, status: 200 }))
      .catch((err) => {
        clearTimeout(timeoutId);
        const isTimeout = err.name === 'AbortError';
        sendResponse({
          success: false,
          error: isTimeout ? 'Health check timed out' : 'Backend offline (http://127.0.0.1:8765)',
          errorType: isTimeout ? 'TIMEOUT' : 'CONNECTION_REFUSED',
          status: isTimeout ? 408 : 503,
          data: null
        });
      });
    return true;
  }

  if (request.action === 'COPILOT_PING') {
    sendResponse({ success: true, pong: true, timestamp: Date.now() });
    return false;
  }
});

async function handleApiRequest(req) {
  const endpoint = req.endpoint || '/status';
  const url = `${SERVER_BASE}${endpoint.startsWith('/') ? endpoint : '/' + endpoint}`;
  const method = req.method || 'GET';
  const timeoutMs = req.timeout || 8000;

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  const options = {
    method: method,
    headers: {
      'Content-Type': 'application/json',
      ...(req.headers || {})
    },
    signal: controller.signal
  };

  if (req.body && (method === 'POST' || method === 'PUT')) {
    options.body = typeof req.body === 'string' ? req.body : JSON.stringify(req.body);
  }

  try {
    const response = await fetch(url, options);
    clearTimeout(timeoutId);

    let json = {};
    try {
      json = await response.json();
    } catch (parseErr) {
      if (!response.ok) {
        const err = new Error(`HTTP ${response.status}: Non-JSON response from backend`);
        err.status = response.status;
        err.errorType = 'INVALID_JSON';
        err.data = null;
        throw err;
      }
    }

    if (!response.ok) {
      const errorMsg = json.error || (json.analysis && json.analysis.reasons && json.analysis.reasons[0]) || `HTTP ${response.status} from backend`;
      const err = new Error(errorMsg);
      err.status = response.status;
      err.data = json;
      err.errorType = 'HTTP_ERROR';
      throw err;
    }

    return json;
  } catch (err) {
    clearTimeout(timeoutId);
    if (err.name === 'AbortError') {
      const timeoutErr = new Error(`Request timed out after ${timeoutMs / 1000}s`);
      timeoutErr.errorType = 'TIMEOUT';
      timeoutErr.status = 408;
      throw timeoutErr;
    }
    if (err.message && (err.message.includes('Failed to fetch') || err.message.includes('NetworkError'))) {
      const netErr = new Error('Backend process not running or connection refused (http://127.0.0.1:8765)');
      netErr.errorType = 'CONNECTION_REFUSED';
      netErr.status = 503;
      throw netErr;
    }
    throw err;
  }
}
