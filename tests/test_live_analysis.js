'use strict';

const assert = require('assert');
const live = require('../live_analysis.js');

const stock = {
  symbol: '6488.TWO', score: 70.5, price: 1060, volume: 1.58,
  avgVolume20: 3000000,
  support1: 944, resistance1: 778,
  foreignNet: 303643, trustNet: 1220677, margin1d: 825
};

let inputs = live.buildInputs(stock, null, Date.parse('2026-08-18T18:48:00+08:00'));
assert.deepStrictEqual(inputs, {
  price: 1060, volumeRatio: 1.58, bid: null, ask: null,
  foreign: 303643, trust: 1220677, margin: 825
});
const twRows = live.inputRows(stock, inputs);
assert.deepStrictEqual(twRows.slice(4).map((row) => row.label), [
  '外資買賣超（張）', '投信買賣超（張）', '融資增減（張）'
]);
assert.deepStrictEqual(twRows.slice(4).map((row) => row.value), [303.643, 1220.677, 825]);
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

const hannstar = {
  symbol: '6116.TW', market: 'TW', score: 82, price: 14.4, volume: 1.8,
  avgVolume20: 34597099, support1: 13.8, resistance1: 15.0,
  foreignNet: -2292868, trustNet: 0, margin1d: -387
};
let blockedInputs = live.buildInputs(hannstar);
let blocked = live.calculate(hannstar, blockedInputs, '2026/8/19 19:09:46');
assert.strictEqual(blocked.coverage, 5);
assert.strictEqual(blocked.guardBlocked, true);
assert.notStrictEqual(blocked.direction, '看漲');
assert.ok(blocked.decision.indexOf('不建議新增部位') >= 0);
blockedInputs = live.buildInputs(hannstar, {
  lastPrice: 14.4, bidTotal: 1000, askTotal: 900,
  fetchedAt: '2026-08-19T19:00:00+08:00'
}, Date.parse('2026-08-19T19:09:46+08:00'));
blocked = live.calculate(hannstar, blockedInputs, '2026/8/19 19:09:46');
assert.strictEqual(blocked.coverage, 7);
assert.strictEqual(blocked.guardBlocked, true);
assert.notStrictEqual(blocked.direction, '看漲');

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

const incompleteUs = Object.assign({}, usStock, {
  score: 90, usVwapDistance: null, usQuoteImbalance: null,
  usRelativeStrength: null, usOptionSafetyScore: null
});
inputs = live.buildInputs(incompleteUs);
result = live.calculate(incompleteUs, inputs);
assert.notStrictEqual(result.direction, '看漲');
assert.ok(result.decision.indexOf('資料未達7/7') >= 0);

const riskyUs = Object.assign({}, usStock, {
  score: 90, usVwapDistance: -1.1, usQuoteImbalance: -35,
  usRelativeStrength: -2, usOptionSafetyScore: 65, usMarketRiskScore: 35
});
inputs = live.buildInputs(riskyUs);
result = live.calculate(riskyUs, inputs);
assert.strictEqual(result.coverage, 7);
assert.strictEqual(result.guardBlocked, true);
assert.notStrictEqual(result.direction, '看漲');

console.log('live_analysis: all tests passed');
