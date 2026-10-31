/**
 * AI Trading Copilot - Service Worker (Manifest V3)
 * Proxies backend requests to local Copilot server (http://127.0.0.1:8765)
 * to avoid any mixed-content or CORS restrictions in page contexts.
 */

const SERVER_BASE = 'http://127.0.0.1:8765/api';

chrome.runtime.onInstalled.addListener(() => {
  console.log('AI Trading Copilot Extension v2.1.0 installed.');
});

// Message listener for content scripts and popup
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'COPILOT_API_REQUEST') {
    handleApiRequest(request)
      .then((data) => sendResponse({ success: true, data: data }))
      .catch((err) => sendResponse({
        success: false,
        error: err.message || String(err),
        status: err.status || null,
        data: err.data || null
      }));
    return true; // Keep message channel open for async response
  }

  if (request.action === 'COPILOT_HEALTH_CHECK') {
    fetch(`${SERVER_BASE}/health`, { method: 'GET' })
      .then((res) => res.json())
      .then((data) => sendResponse({ success: true, data: data }))
      .catch((err) => sendResponse({
        success: false,
        error: err.message || String(err),
        status: null,
        data: null
      }));
    return true;
  }
});

async function handleApiRequest(req) {
  const endpoint = req.endpoint || '/status';
  const url = `${SERVER_BASE}${endpoint.startsWith('/') ? endpoint : '/' + endpoint}`;
  const method = req.method || 'GET';
  
  const options = {
    method: method,
    headers: {
      'Content-Type': 'application/json',
      ...(req.headers || {})
    }
  };

  if (req.body && (method === 'POST' || method === 'PUT')) {
    options.body = typeof req.body === 'string' ? req.body : JSON.stringify(req.body);
  }

  const response = await fetch(url, options);
  const json = await response.json().catch(() => ({}));

  if (!response.ok) {
    const errorMsg = json.error || (json.analysis && json.analysis.reasons && json.analysis.reasons[0]) || `HTTP ${response.status} from backend`;
    const err = new Error(errorMsg);
    err.status = response.status;
    err.data = json;
    throw err;
  }

  return json;
}
