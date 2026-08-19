(function (root, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.WudeLiveAnalysis = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var TW_INPUT_KEYS = ['price', 'volumeRatio', 'bid', 'ask', 'foreign', 'trust', 'margin'];
  var TW_INPUT_LABELS = {
    price: '價格', volumeRatio: '相對量', bid: '五檔委買', ask: '五檔委賣',
    foreign: '外資買賣超（張）', trust: '投信買賣超（張）', margin: '融資增減（張）'
  };
  var US_INPUT_KEYS = ['price', 'volumeRatio', 'vwapDistance', 'quoteImbalance', 'relativeStrength', 'optionScore', 'riskScore'];
  var US_INPUT_LABELS = {
    price: 'SIP價格', volumeRatio: '同時段相對量', vwapDistance: '距VWAP',
    quoteImbalance: 'NBBO買賣量差', relativeStrength: '相對Nasdaq強弱',
    optionScore: 'OPRA風險安全分', riskScore: '市場／事件安全分'
  };

  function finite(value) {
    if (value === null || value === undefined || value === '' || value === '—') return null;
    var number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function quoteIsFresh(quote, now) {
    if (!quote || !quote.fetchedAt) return !!quote;
    var fetched = Date.parse(quote.fetchedAt);
    var current = now === undefined ? Date.now() : Number(now);
    if (!Number.isFinite(fetched) || !Number.isFinite(current)) return false;
    return current - fetched <= 36 * 60 * 60 * 1000 && current >= fetched - 10 * 60 * 1000;
  }

  function buildInputs(stock, quote, now) {
    stock = stock || {};
    if (String(stock.market || '').toUpperCase() === 'US') return buildUsInputs(stock);
    var freshQuote = quoteIsFresh(quote, now) ? quote : null;
    return {
      price: finite(freshQuote && freshQuote.lastPrice) !== null ? finite(freshQuote.lastPrice) : finite(stock.price),
      volumeRatio: finite(stock.volume),
      bid: finite(freshQuote && freshQuote.bidTotal),
      ask: finite(freshQuote && freshQuote.askTotal),
      foreign: finite(stock.foreignNet),
      trust: finite(stock.trustNet),
      margin: finite(stock.margin1d)
    };
  }

  function buildUsInputs(stock) {
    var eventScore = null;
    if (stock.newsDataAvailable) eventScore = Math.max(0, 100 - Math.max(finite(stock.newsPenalty) || 0, 0) * 10);
    var marketScore = finite(stock.usMarketRiskScore);
    var riskScore = marketScore !== null && eventScore !== null ? (marketScore + eventScore) / 2 : marketScore !== null ? marketScore : eventScore;
    return {
      price: finite(stock.usLivePrice) !== null ? finite(stock.usLivePrice) : finite(stock.extendedPrice) !== null ? finite(stock.extendedPrice) : finite(stock.price),
      volumeRatio: finite(stock.volume),
      vwapDistance: finite(stock.usVwapDistance),
      quoteImbalance: finite(stock.usQuoteImbalance),
      relativeStrength: finite(stock.usRelativeStrength),
      optionScore: finite(stock.usOptionSafetyScore),
      riskScore: riskScore
    };
  }

  function keysAndLabels(stock) {
    return String((stock || {}).market || '').toUpperCase() === 'US'
      ? [US_INPUT_KEYS, US_INPUT_LABELS] : [TW_INPUT_KEYS, TW_INPUT_LABELS];
  }

  function missingLabels(stock, inputs) {
    var pair = keysAndLabels(stock);
    return pair[0].filter(function (key) {
      return !Number.isFinite(inputs && inputs[key]);
    }).map(function (key) { return pair[1][key]; });
  }

  function inputRows(stock, inputs) {
    var pair = keysAndLabels(stock);
    return pair[0].map(function (key) {
      var label = pair[1][key];
      if (key === 'price' && String((stock || {}).market || '').toUpperCase() === 'US' && !stock.usLiveAvailable) label = '備援價格';
      var value = inputs[key];
      if (String((stock || {}).market || '').toUpperCase() !== 'US' && (key === 'foreign' || key === 'trust') && Number.isFinite(value)) value = value / 1000;
      return { key: key, label: label, value: value };
    });
  }

  function calculateTw(stock, inputs, timeText) {
    stock = stock || {};
    inputs = inputs || {};
    var base = finite(stock.score);
    if (base === null) base = 50;
    var delta = 0, reasons = [], price = inputs.price;
    var coverage = TW_INPUT_KEYS.filter(function (key) { return Number.isFinite(inputs[key]); }).length;
    var dataComplete = coverage === TW_INPUT_KEYS.length;
    var hardBlock = false, severeBlock = false, guardReasons = [];
    var reportPrice = finite(stock.price), support = finite(stock.support1), resistance = finite(stock.resistance1);
    if (Number.isFinite(price) && support !== null && price <= support * 1.01) {
      delta += 3; reasons.push('價格已接近第一支撐，具備風險控制位置');
    }
    if (Number.isFinite(price) && resistance !== null && price >= resistance * .99) {
      delta -= 2; reasons.push('價格接近第一壓力，不宜直接追價');
    }
    if (Number.isFinite(inputs.volumeRatio)) {
      if (inputs.volumeRatio >= 1.5 && reportPrice !== null && price >= reportPrice) {
        delta += 3; reasons.push('上漲配合相對量放大，攻擊量能偏多');
      } else if (inputs.volumeRatio >= 1.5 && reportPrice !== null && price < reportPrice) {
        delta -= 3; reasons.push('下跌同時放量，賣壓風險升高');
      } else if (inputs.volumeRatio < .7) reasons.push('量能不足，突破訊號可靠度較低');
    }
    if (Number.isFinite(inputs.bid) && Number.isFinite(inputs.ask) && inputs.ask > 0) {
      var orderRatio = inputs.bid / inputs.ask;
      if (orderRatio >= 1.2) { delta += 3; reasons.push('五檔委買大於委賣，短線承接偏強'); }
      else if (orderRatio <= .83) { delta -= 3; reasons.push('五檔委賣大於委買，短線上方賣壓偏重'); }
      else reasons.push('五檔買賣接近平衡');
    }
    var hasFlow = Number.isFinite(inputs.foreign) || Number.isFinite(inputs.trust);
    var flow = (Number.isFinite(inputs.foreign) ? inputs.foreign : 0) + (Number.isFinite(inputs.trust) ? inputs.trust : 0);
    if (hasFlow && flow > 0) { delta += 3; reasons.push('外資與投信合計買超，法人籌碼偏多'); }
    else if (hasFlow && flow < 0) { delta -= 3; reasons.push('外資與投信合計賣超，法人籌碼偏空'); }
    var avgVolume = finite(stock.avgVolume20);
    var flowRatio = hasFlow && avgVolume !== null && avgVolume > 0 ? flow / avgVolume * 100 : null;
    if (flowRatio !== null && flowRatio <= -5) {
      hardBlock = true;
      severeBlock = flowRatio <= -10;
      delta -= 3;
      guardReasons.push('法人單日賣超約占20日均量 ' + Math.abs(flowRatio).toFixed(1) + '%，禁止短線亮綠燈');
    }
    if (Number.isFinite(inputs.margin) && inputs.margin > 0 && flow < 0) {
      delta -= 2; reasons.push('法人賣超而融資增加，籌碼結構較不利');
    } else if (Number.isFinite(inputs.margin) && inputs.margin < 0 && hasFlow && flow >= 0) {
      delta += 1; reasons.push('融資減少且法人未轉賣，浮額壓力下降');
    }
    delta = Math.max(-15, Math.min(15, delta));
    var adjusted = Math.max(0, Math.min(100, base + delta));
    if (!dataComplete || hardBlock) adjusted = Math.min(adjusted, 64.9);
    var direction = dataComplete && !hardBlock && adjusted >= 75 && delta > 0
      ? '看漲' : adjusted <= 45 || delta <= -6 || severeBlock ? '看跌' : '震盪／等待確認';
    var decision = '先觀察，不追價；等價格與量能確認後再進場。';
    if (direction === '看漲' && Number.isFinite(price) && support !== null && price <= support * 1.03) {
      decision = '可在支撐附近小量分批，跌破風控價立即停止加碼。';
    } else if (direction === '看漲') decision = '方向偏多，但目前不一定是低風險買點，等回測或有效突破再買。';
    else if (direction === '看跌') decision = '目前不建議新增部位；先等賣壓縮小並重新站回支撐。';
    if (!dataComplete) {
      decision = '資料未達7/7，禁止顯示短線可買；先等即時資料補齊。';
      reasons.unshift('資料只有 ' + coverage + '/7，原AI分數不能代替缺少的即時證據');
    }
    if (hardBlock) {
      decision = '目前不建議新增部位；等待法人賣壓縮小並重新確認量價。';
      reasons = guardReasons.concat(reasons);
    }
    if (!reasons.length) reasons.push('自動資料仍不足，維持原模型判斷');
    return {
      direction: direction, adjustedScore: adjusted, decision: decision, reasons: reasons,
      coverage: coverage, coverageRequired: TW_INPUT_KEYS.length,
      guardBlocked: hardBlock, guardReason: guardReasons.join('；'), flowRatio: flowRatio,
      time: timeText || new Date().toLocaleString('zh-TW', { hour12: false }),
      inputs: inputs
    };
  }

  function calculateUs(stock, inputs, timeText) {
    var base = finite(stock.score);
    if (base === null) base = 50;
    var delta = 0, reasons = [], price = inputs.price;
    var coverage = US_INPUT_KEYS.filter(function (key) { return Number.isFinite(inputs[key]); }).length;
    var dataComplete = coverage === US_INPUT_KEYS.length;
    var hardBlock = false, severeBlock = false, guardReasons = [];
    var support = finite(stock.support1), resistance = finite(stock.resistance1), reportPrice = finite(stock.price);
    if (Number.isFinite(price) && support !== null && price <= support * 1.01) {
      delta += 3; reasons.push('價格接近第一支撐，風險報酬位置改善');
    }
    if (Number.isFinite(price) && resistance !== null && price >= resistance * .99) {
      delta -= 2; reasons.push('價格接近第一壓力，不宜直接追價');
    }
    if (Number.isFinite(inputs.volumeRatio)) {
      if (inputs.volumeRatio >= 1.5 && reportPrice !== null && price >= reportPrice) {
        delta += 3; reasons.push('同時段相對量放大且價格偏強');
      } else if (inputs.volumeRatio >= 1.5 && reportPrice !== null && price < reportPrice) {
        delta -= 3; reasons.push('下跌放量，短線賣壓提高');
      } else if (inputs.volumeRatio < .7) reasons.push('同時段量能不足，突破可靠度較低');
    }
    if (Number.isFinite(inputs.vwapDistance)) {
      if (inputs.vwapDistance >= .5) { delta += 2; reasons.push('SIP價格位於當日VWAP上方'); }
      else if (inputs.vwapDistance <= -.5) { delta -= 2; reasons.push('SIP價格位於當日VWAP下方'); }
    }
    if (Number.isFinite(inputs.quoteImbalance)) {
      if (inputs.quoteImbalance >= 20) { delta += 2; reasons.push('NBBO委買量明顯高於委賣量'); }
      else if (inputs.quoteImbalance <= -20) { delta -= 2; reasons.push('NBBO委賣量明顯高於委買量'); }
    }
    if (Number.isFinite(inputs.relativeStrength)) {
      if (inputs.relativeStrength >= 1) { delta += 3; reasons.push('個股表現明顯強於Nasdaq'); }
      else if (inputs.relativeStrength <= -1) { delta -= 3; reasons.push('個股表現明顯弱於Nasdaq'); }
    }
    if (Number.isFinite(inputs.optionScore)) {
      if (inputs.optionScore >= 65) { delta += 2; reasons.push('OPRA近月選擇權風險偏低'); }
      else if (inputs.optionScore <= 40) { delta -= 3; reasons.push('OPRA隱含波動或Put偏斜顯示風險升高'); }
    }
    if (Number.isFinite(inputs.riskScore)) {
      if (inputs.riskScore <= 40) { delta -= 4; reasons.push('VIX或事件風險偏高，降低追價評分'); }
      else if (inputs.riskScore >= 65) { delta += 1; reasons.push('市場與事件風險目前可控'); }
    }
    if (Number.isFinite(inputs.quoteImbalance) && Number.isFinite(inputs.vwapDistance)
        && inputs.quoteImbalance <= -20 && inputs.vwapDistance <= -.5) {
      hardBlock = true;
      guardReasons.push('NBBO賣壓偏重且價格位於VWAP下方，禁止短線亮綠燈');
    }
    if (Number.isFinite(inputs.riskScore) && inputs.riskScore <= 40) {
      hardBlock = true;
      severeBlock = inputs.riskScore <= 30;
      guardReasons.push('市場／事件安全分偏低');
    }
    delta = Math.max(-15, Math.min(15, delta));
    var adjusted = Math.max(0, Math.min(100, base + delta));
    if (!dataComplete || hardBlock) adjusted = Math.min(adjusted, 64.9);
    var direction = dataComplete && !hardBlock && adjusted >= 75 && delta > 0
      ? '看漲' : adjusted <= 45 || delta <= -6 || severeBlock ? '看跌' : '震盪／等待確認';
    var decision = direction === '看跌' ? '目前不建議新增部位；等待賣壓與事件風險下降。'
      : direction === '看漲' ? '方向偏多，但仍應等回測VWAP、支撐或有效突破後分批。'
      : '先觀察，不追價；等待SIP量價與市場風險共同確認。';
    if (!dataComplete) {
      decision = '資料未達7/7，禁止顯示短線可買；等待SIP、OPRA與市場風險資料補齊。';
      reasons.unshift('資料只有 ' + coverage + '/7，原AI分數不能代替缺少的即時證據');
    }
    if (hardBlock) {
      decision = '目前不建議新增部位；等待VWAP、委買賣與市場風險重新轉強。';
      reasons = guardReasons.concat(reasons);
    }
    if (!reasons.length) reasons.push('美股權威即時資料仍不足，維持原模型判斷');
    return {
      direction: direction, adjustedScore: adjusted, decision: decision, reasons: reasons,
      coverage: coverage, coverageRequired: US_INPUT_KEYS.length,
      guardBlocked: hardBlock, guardReason: guardReasons.join('；'),
      time: timeText || new Date().toLocaleString('zh-TW', { hour12: false }), inputs: inputs
    };
  }

  function calculate(stock, inputs, timeText) {
    return String((stock || {}).market || '').toUpperCase() === 'US'
      ? calculateUs(stock || {}, inputs || {}, timeText)
      : calculateTw(stock || {}, inputs || {}, timeText);
  }

  return {
    inputKeys: TW_INPUT_KEYS.slice(), buildInputs: buildInputs, missingLabels: missingLabels,
    inputRows: inputRows, quoteIsFresh: quoteIsFresh, calculate: calculate
  };
}));
