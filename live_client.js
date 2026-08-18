(function (root, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.WudeLiveClient = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  function cleanBase(base) {
    return String(base || '').trim().replace(/\/+$/, '');
  }

  function buildUrl(base, symbol, market) {
    base = cleanBase(base);
    if (!base) return '';
    return base + '/api/live?symbol=' + encodeURIComponent(String(symbol || '').toUpperCase())
      + '&market=' + encodeURIComponent(String(market || '').toUpperCase()) + '&options=1';
  }

  function pollInterval(value) {
    var number = Number(value);
    if (!Number.isFinite(number)) number = 5000;
    return Math.max(3000, Math.min(30000, number));
  }

  function tokenFromHash(hash) {
    var text = String(hash || '').replace(/^#/, '');
    if (!text) return '';
    var params = new URLSearchParams(text);
    return String(params.get('live_token') || '').trim();
  }

  function saveTokenFromHash(locationLike, historyLike, storageLike) {
    var token = tokenFromHash(locationLike && locationLike.hash);
    if (!token) return '';
    storageLike.setItem('wude-live-access-token', token);
    if (historyLike && typeof historyLike.replaceState === 'function') {
      historyLike.replaceState(null, '', String(locationLike.pathname || '/') + String(locationLike.search || ''));
    }
    return token;
  }

  function accessToken(storageLike) {
    try { return String(storageLike.getItem('wude-live-access-token') || '').trim(); }
    catch (error) { return ''; }
  }

  function requestHeaders(token) {
    var headers = {'Accept': 'application/json'};
    if (String(token || '').trim()) headers['X-Live-Token'] = String(token).trim();
    return headers;
  }

  function mergeStock(stock, payload) {
    var merged = Object.assign({}, stock || {});
    var quote = payload && payload.quote || {};
    var options = payload && payload.options || {};
    if (String(merged.market || '').toUpperCase() === 'US') {
      merged.usLiveAvailable = !!(payload && payload.ok);
      merged.usLiveSource = payload && payload.source || quote.us_live_source || merged.usLiveSource;
      merged.usLiveFetchedAt = payload && payload.fetched_at || quote.us_live_fetched_at || merged.usLiveFetchedAt;
      merged.usLivePrice = quote.us_live_price !== undefined ? quote.us_live_price : merged.usLivePrice;
      merged.usLiveBid = quote.us_live_bid !== undefined ? quote.us_live_bid : merged.usLiveBid;
      merged.usLiveAsk = quote.us_live_ask !== undefined ? quote.us_live_ask : merged.usLiveAsk;
      merged.usQuoteImbalance = quote.us_live_quote_imbalance_pct !== undefined ? quote.us_live_quote_imbalance_pct : merged.usQuoteImbalance;
      merged.usVwap = quote.us_live_vwap !== undefined ? quote.us_live_vwap : merged.usVwap;
      merged.usVwapDistance = quote.us_live_vwap_distance_pct !== undefined ? quote.us_live_vwap_distance_pct : merged.usVwapDistance;
      merged.usOptionSafetyScore = options.us_option_safety_score !== undefined ? options.us_option_safety_score : merged.usOptionSafetyScore;
      merged.usOptionIv = options.us_option_iv_pct !== undefined ? options.us_option_iv_pct : merged.usOptionIv;
      return merged;
    }
    return merged;
  }

  function taiwanQuote(payload, fallback) {
    var quote = payload && payload.quote;
    if (!quote || !(payload && payload.ok)) return fallback || null;
    return {
      lastPrice: quote.lastPrice,
      bidTotal: quote.bidTotal,
      askTotal: quote.askTotal,
      fetchedAt: payload.fetched_at || quote.fetchedAt
    };
  }

  return {
    buildUrl: buildUrl,
    pollInterval: pollInterval,
    tokenFromHash: tokenFromHash,
    saveTokenFromHash: saveTokenFromHash,
    accessToken: accessToken,
    requestHeaders: requestHeaders,
    mergeStock: mergeStock,
    taiwanQuote: taiwanQuote
  };
}));
