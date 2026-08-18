'use strict';

const assert = require('assert');
const live = require('../live_analysis.js');

const stock = {
  symbol: '6488.TWO', score: 66.5, price: 1060, volume: 1.58,
  support1: 944, resistance1: 778,
  foreignNet: 303643, trustNet: 1220677, margin1d: 825
};

let inputs = live.buildInputs(stock, null, Date.parse('2026-08-18T18:48:00+08:00'));
assert.deepStrictEqual(inputs, {
  price: 1060, volumeRatio: 1.58, bid: null, ask: null,
  foreign: 303643, trust: 1220677, margin: 825
});
assert.deepStrictEqual(live.missingLabels(stock, inputs), ['五檔委買', '五檔委賣']);

const quote = {
  lastPrice: 1065, bidTotal: 1580, askTotal: 1000,
  fetchedAt: '2026-08-18T18:40:00+08:00'
};
inputs = live.buildInputs(stock, quote, Date.parse('2026-08-18T18:48:00+08:00'));
assert.strictEqual(inputs.price, 1065);
assert.strictEqual(inputs.bid, 1580);
assert.strictEqual(inputs.ask, 1000);
assert.deepStrictEqual(live.missingLabels(stock, inputs), []);

let result = live.calculate(stock, inputs, '2026/8/18 18:48:01');
assert.strictEqual(result.coverage, 7);
assert.strictEqual(result.direction, '看漲');
assert.ok(result.reasons.some((reason) => reason.indexOf('法人籌碼偏多') >= 0));
assert.ok(result.reasons.some((reason) => reason.indexOf('短線承接偏強') >= 0));

inputs = live.buildInputs(stock, quote, Date.parse('2026-08-21T08:00:00+08:00'));
assert.strictEqual(inputs.price, 1060);
assert.strictEqual(inputs.bid, null);
assert.strictEqual(inputs.ask, null);

const usStock = {
  symbol: 'NVDA', market: 'US', score: 72, price: 180, volume: 1.7, usLiveAvailable: true,
  support1: 175, resistance1: 190, usLivePrice: 183,
  usVwapDistance: 1.2, usQuoteImbalance: 35, usRelativeStrength: 2.1,
  usOptionSafetyScore: 68, usMarketRiskScore: 70,
  newsDataAvailable: true, newsPenalty: 0
};
inputs = live.buildInputs(usStock);
assert.deepStrictEqual(live.missingLabels(usStock, inputs), []);
assert.deepStrictEqual(live.inputRows(usStock, inputs).map((row) => row.label), [
  'SIP價格', '同時段相對量', '距VWAP', 'NBBO買賣量差',
  '相對Nasdaq強弱', 'OPRA風險安全分', '市場／事件安全分'
]);
result = live.calculate(usStock, inputs, '2026/8/18 18:48:01');
assert.strictEqual(result.coverage, 7);
assert.strictEqual(result.direction, '看漲');
assert.ok(result.reasons.some((reason) => reason.indexOf('NBBO') >= 0));
assert.ok(result.reasons.some((reason) => reason.indexOf('OPRA') >= 0));

console.log('live_analysis: all tests passed');
