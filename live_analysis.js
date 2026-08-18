(function (root, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.WudeLiveAnalysis = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var INPUT_KEYS = ['price', 'volumeRatio', 'bid', 'ask', 'foreign', 'trust', 'margin'];
  var INPUT_LABELS = {
    price: '價格', volumeRatio: '相對量', bid: '五檔委買', ask: '五檔委賣',
    foreign: '外資', trust: '投信', margin: '融資增減'
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

  function missingLabels(inputs) {
    return INPUT_KEYS.filter(function (key) {
      return !Number.isFinite(inputs && inputs[key]);
    }).map(function (key) { return INPUT_LABELS[key]; });
  }

  function calculate(stock, inputs, timeText) {
    stock = stock || {};
    inputs = inputs || {};
    var base = finite(stock.score);
    if (base === null) base = 50;
    var delta = 0, reasons = [], price = inputs.price;
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
    if (Number.isFinite(inputs.margin) && inputs.margin > 0 && flow < 0) {
      delta -= 2; reasons.push('法人賣超而融資增加，籌碼結構較不利');
    } else if (Number.isFinite(inputs.margin) && inputs.margin < 0 && hasFlow && flow >= 0) {
      delta += 1; reasons.push('融資減少且法人未轉賣，浮額壓力下降');
    }
    delta = Math.max(-15, Math.min(15, delta));
    var adjusted = Math.max(0, Math.min(100, base + delta));
    var direction = adjusted >= 65 && delta >= 0 ? '看漲' : adjusted <= 45 || delta <= -6 ? '看跌' : '震盪／等待確認';
    var decision = '先觀察，不追價；等價格與量能確認後再進場。';
    if (direction === '看漲' && Number.isFinite(price) && support !== null && price <= support * 1.03) {
      decision = '可在支撐附近小量分批，跌破風控價立即停止加碼。';
    } else if (direction === '看漲') decision = '方向偏多，但目前不一定是低風險買點，等回測或有效突破再買。';
    else if (direction === '看跌') decision = '目前不建議新增部位；先等賣壓縮小並重新站回支撐。';
    if (!reasons.length) reasons.push('自動資料仍不足，維持原模型判斷');
    var coverage = INPUT_KEYS.filter(function (key) { return Number.isFinite(inputs[key]); }).length;
    return {
      direction: direction, adjustedScore: adjusted, decision: decision, reasons: reasons,
      coverage: coverage,
      time: timeText || new Date().toLocaleString('zh-TW', { hour12: false }),
      inputs: inputs
    };
  }

  return {
    inputKeys: INPUT_KEYS.slice(), buildInputs: buildInputs, missingLabels: missingLabels,
    quoteIsFresh: quoteIsFresh, calculate: calculate
  };
}));
