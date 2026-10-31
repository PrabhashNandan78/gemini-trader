/**
 * TradingView Companion Content Script
 * Safely extracts the active instrument symbol from the page title or URL
 * without scraping or automating proprietary TradingView DOM structures.
 */

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'GET_ACTIVE_SYMBOL') {
    const symbol = detectTradingViewSymbol();
    sendResponse({ symbol });
  }
  return true;
});

function detectTradingViewSymbol() {
  // Strategy 1: URL path e.g. tradingview.com/chart/?symbol=NSE:RELIANCE
  const urlParams = new URLSearchParams(window.location.search);
  const paramSymbol = urlParams.get('symbol');
  if (paramSymbol) {
    return normalizeSymbol(paramSymbol);
  }

  // Strategy 2: Page Title e.g. "RELIANCE 2450.50 INR — TradingView"
  const title = document.title || '';
  const titleParts = title.split(' ');
  if (titleParts.length > 0) {
    const candidate = titleParts[0].trim();
    if (candidate && candidate.length <= 15) {
      return normalizeSymbol(candidate);
    }
  }

  return 'RELIANCE.NS';
}

function normalizeSymbol(raw) {
  let clean = raw.trim().toUpperCase();
  if (clean.startsWith('NSE:')) {
    return clean.replace('NSE:', '') + '.NS';
  }
  if (clean.startsWith('BSE:')) {
    return clean.replace('BSE:', '') + '.BO';
  }
  if (clean.startsWith('BINANCE:') || clean.startsWith('COINBASE:')) {
    clean = clean.split(':')[1];
  }
  if (clean.endsWith('USDT')) {
    return clean.replace('USDT', '-USD');
  }
  return clean;
}
