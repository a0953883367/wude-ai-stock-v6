(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.WudeCapitalTest = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var POLICY = {
    capital: 20000,
    minPosition: 4000,
    maxPosition: 6000,
    maxPositions: 3,
    cashReserve: 5000,
    maxLossPerTrade: 200,
    accountStop: 2000,
    minAiScore: 75,
    minEntryScore: 65,
    minLiveScore: 75,
    minCoverage: 6,
    maxCoverage: 7,
    minVolumeRatio: 0.7,
    maxVolumeRatio: 3,
    maxRsi: 75,
    buyZoneTolerance: 0.01
  };

  function finite(value) {
    var n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function parseRange(value) {
    if (Array.isArray(value)) {
      var arrayValues = value.map(finite).filter(function (v) { return v !== null; });
      if (arrayValues.length) return [Math.min.apply(Math, arrayValues), Math.max.apply(Math, arrayValues)];
    }
    var values = String(value === undefined || value === null ? '' : value)
      .replace(/,/g, '')
      .match(/-?\d+(?:\.\d+)?/g);
    if (!values || !values.length) return null;
    values = values.map(Number).filter(Number.isFinite);
    return values.length ? [Math.min.apply(Math, values), Math.max.apply(Math, values)] : null;
  }

  function includesAny(text, terms) {
    text = String(text || '').toLowerCase();
    return terms.some(function (term) { return text.indexOf(String(term).toLowerCase()) >= 0; });
  }

  function evaluate(stock, live) {
    stock = stock || {};
    var reasons = [];
    var checks = [];
    function requireCheck(ok, reason) {
      checks.push(Boolean(ok));
      if (!ok) reasons.push(reason);
    }

    var score = finite(stock.score);
    var entryScore = finite(stock.entryScore);
    var rsi = finite(stock.rsi);
    var stop = finite(stock.stop);
    var action = String(stock.advice || '');
    var riskText = [stock.risk, stock.entryNote, stock.pricePlanNote].join(' ');
    var newsLevel = String(stock.newsRiskLevel || '');
    var newsPenalty = finite(stock.newsPenalty);

    requireCheck(stock.market === 'TW' && String(stock.type || '').toUpperCase().indexOf('ETF') < 0, '首輪2萬元測試只做台股個股');
    requireCheck(score !== null && score >= POLICY.minAiScore, 'AI總分未達75分');
    requireCheck(entryScore !== null && entryScore >= POLICY.minEntryScore, '現在進場分數未達65分');
    requireCheck(action.indexOf('🟢') >= 0 && !includesAny(action, ['暫不買', '觀察', '等待', '勿追']), '正式操作訊號尚未轉為綠燈可買');
    requireCheck(!includesAny(riskText, ['勿追', '追高', '乖離', '高風險', '重大']), '風險文字仍提示勿追、高乖離或重大風險');
    requireCheck(rsi !== null && rsi <= POLICY.maxRsi, 'RSI高於75或資料不足');
    requireCheck(stock.newsDataAvailable === true && !includesAny(newsLevel, ['🔴', '高', '重大']) && (newsPenalty === null || newsPenalty < 8), '負面新聞資料未完整或風險過高');

    requireCheck(Boolean(live), '尚未補充券商即時資料');
    var coverage = live ? finite(live.coverage) : null;
    var adjustedScore = live ? finite(live.adjustedScore) : null;
    requireCheck(Boolean(live) && coverage !== null && coverage >= POLICY.minCoverage, '即時資料完整度未達6/7');
    requireCheck(Boolean(live) && live.direction === '看漲' && adjustedScore !== null && adjustedScore >= POLICY.minLiveScore, '即時補充判斷未達看漲75分');

    var price = live && live.inputs ? finite(live.inputs.price) : null;
    var volumeRatio = live && live.inputs ? finite(live.inputs.volumeRatio) : null;
    var buyRange = parseRange(stock.buy);
    var inBuyZone = price !== null && buyRange && price >= buyRange[0] * (1 - POLICY.buyZoneTolerance) && price <= buyRange[1] * (1 + POLICY.buyZoneTolerance);
    requireCheck(Boolean(inBuyZone), '即時價格不在第一買進區附近');
    requireCheck(volumeRatio !== null && volumeRatio >= POLICY.minVolumeRatio && volumeRatio <= POLICY.maxVolumeRatio, '即時相對量需在0.7～3.0倍之間');
    requireCheck(price !== null && stop !== null && stop > 0 && stop < price, '風控價無效或未低於即時價格');

    var shares = 0;
    var estimatedCost = 0;
    var estimatedLoss = 0;
    if (price !== null && stop !== null && price > stop) {
      var byCapital = Math.floor(POLICY.maxPosition / price);
      var byRisk = Math.floor(POLICY.maxLossPerTrade / (price - stop));
      shares = Math.max(0, Math.min(byCapital, byRisk));
      estimatedCost = shares * price;
      estimatedLoss = shares * (price - stop);
    }
    requireCheck(shares > 0 && estimatedCost >= POLICY.minPosition && estimatedCost <= POLICY.maxPosition && estimatedLoss <= POLICY.maxLossPerTrade, '依風控試算後部位不足4,000元或風險超過200元');

    return {
      eligible: reasons.length === 0,
      reasons: reasons,
      checksPassed: checks.filter(Boolean).length,
      totalChecks: checks.length,
      price: price,
      stop: stop,
      shares: shares,
      estimatedCost: estimatedCost,
      estimatedLoss: estimatedLoss,
      capital: POLICY.capital,
      maxPositions: POLICY.maxPositions,
      cashReserve: POLICY.cashReserve,
      accountStop: POLICY.accountStop
    };
  }

  return { POLICY: POLICY, evaluate: evaluate, parseRange: parseRange };
}));
