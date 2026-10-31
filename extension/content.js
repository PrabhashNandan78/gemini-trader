/**
 * TradingView Companion Content Script
 * Safely extracts the active instrument symbol from the page title or URL
 * without scraping or automating proprietary TradingView DOM structures.
 */

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'GET_ACTIVE_SYMBOL') {
    const data = detectTradingViewContext();
    sendResponse(data);
  }
  return true;
});

function detectTradingViewContext() {
  const urlParams = new URLSearchParams(window.location.search);
  const paramSymbol = urlParams.get('symbol');
  const paramInterval = urlParams.get('interval');

  let symbol = 'RELIANCE.NS';
  if (paramSymbol) {
    symbol = normalizeSymbol(paramSymbol);
  } else {
    // Strategy 2: URL pathname e.g. /symbols/BTCUSD/ or /symbols/NSE-RELIANCE/
    const path = window.location.pathname || '';
    if (path.includes('/symbols/')) {
      const parts = path.split('/symbols/')[1].split('/')[0];
      if (parts) {
        symbol = normalizeSymbol(parts);
      }
    } else {
      // Strategy 3: Page Title e.g. "RELIANCE 2450.50 INR - TradingView"
      const title = document.title || '';
      const titleParts = title.split(' ');
      if (titleParts.length > 0) {
        const candidate = titleParts[0].trim();
        if (candidate && candidate.length <= 15) {
          symbol = normalizeSymbol(candidate);
        }
      }
    }
  }

  return {
    symbol: symbol,
    interval: paramInterval || '5m',
    isTradingView: true,
    dataNotice: 'Active ticker detected. Candle data evaluated via authorized Copilot backend feed (not DOM scraping).'
  };
}

function normalizeSymbol(raw) {
  let clean = raw.trim().toUpperCase();
  clean = clean.replace('NSE-', 'NSE:').replace('BSE-', 'BSE:');
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
  if (clean === 'BTCUSD') return 'BTC-USD';
  if (clean === 'ETHUSD') return 'ETH-USD';
  if (clean === 'SOLUSD') return 'SOL-USD';
  return clean;
}
