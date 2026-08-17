'use strict';

const assert = require('assert');
const capitalTest = require('../capital_test.js');

function eligibleStock(overrides) {
  return Object.assign({
    name: '測試股票', symbol: '1234.TW', market: 'TW', type: '個股',
    score: 82, entryScore: 70, rsi: 62, buy: '100 ～ 105', stop: 100,
    advice: '🟢 可小額分批', risk: '正常波動，依風控執行',
    newsDataAvailable: true, newsRiskLevel: '🟢 低風險', newsPenalty: 0
  }, overrides || {});
}

function eligibleLive(overrides) {
  return Object.assign({
    direction: '看漲', adjustedScore: 80, coverage: 7,
    inputs: { price: 102, volumeRatio: 1.2, bid: 1000, ask: 800, foreign: 20, trust: 10, margin: -5 }
  }, overrides || {});
}

let result = capitalTest.evaluate(eligibleStock(), eligibleLive());
assert.strictEqual(result.eligible, true, result.reasons.join('；'));
assert.strictEqual(result.shares, 58);
assert.strictEqual(result.estimatedCost, 5916);
assert.ok(result.estimatedLoss <= 200);

result = capitalTest.evaluate(eligibleStock({
  name: '南亞科', symbol: '2408.TW', score: 75.4, entryScore: 54.2,
  advice: '🔴 暫不買', risk: '乖離月線過大，勿追高', rsi: 71.4
}), eligibleLive({ coverage: 2, adjustedScore: 78.5 }));
assert.strictEqual(result.eligible, false);
assert.ok(result.reasons.some((reason) => reason.indexOf('進場分數') >= 0));
assert.ok(result.reasons.some((reason) => reason.indexOf('綠燈') >= 0));
assert.ok(result.reasons.some((reason) => reason.indexOf('6/7') >= 0));

result = capitalTest.evaluate(eligibleStock({ market: 'US', symbol: 'NVDA' }), eligibleLive());
assert.strictEqual(result.eligible, false);
assert.ok(result.reasons.some((reason) => reason.indexOf('台股個股') >= 0));

result = capitalTest.evaluate(eligibleStock({ stop: 90 }), eligibleLive());
assert.strictEqual(result.eligible, false);
assert.ok(result.reasons.some((reason) => reason.indexOf('部位不足') >= 0));

console.log('capital_test: all tests passed');
