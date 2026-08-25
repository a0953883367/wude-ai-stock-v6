'use strict';

const assert = require('assert');
const fs = require('fs');

const html = fs.readFileSync('index.html', 'utf8');
const required = [
  'data-view="MILLION"',
  '100萬元5日試走',
  'reports/million_simulation.json',
  'function showMillionSimulation()',
  '綜合排名前10名',
  '短線排名前10名',
  '嚴格達標才買組',
  '台股0050／美股VOO',
  '人工暫時交易不列入正式成績',
  '未成交',
  '待補資料',
  '不列損益',
  '資料不足不是0元',
  '20筆凍結標的與大盤基準必須全部取得',
  '資料不足隔離',
  '2026-08-24',
  '全程不送券商訂單'
];
required.forEach((text) => assert.ok(html.includes(text), `missing UI contract: ${text}`));

const inlineScripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map((match) => match[1])
  .filter((source) => source.trim());
assert.ok(inlineScripts.length > 0, 'expected an inline application script');
inlineScripts.forEach((source) => new Function(source));

const state = JSON.parse(fs.readFileSync('reports/million_simulation.json', 'utf8'));
assert.strictEqual(state.mode, 'web_shadow_only');
assert.strictEqual(state.policy.start_date, '2026-08-24');
assert.strictEqual(state.policy.capital_per_market_twd, 1000000);
for (const market of ['TW', 'US']) {
  const pending = state.markets[market].pending;
  if (pending) {
    assert.strictEqual(pending.strategies.overall.length, 10);
    assert.strictEqual(pending.strategies.short.length, 10);
    assert.ok(pending.strategies.overall.every((pick) => pick.allocation_twd === 50000));
    assert.ok(pending.strategies.short.every((pick) => pick.allocation_twd === 50000));
  } else {
    assert.strictEqual(state.markets[market].status, 'complete');
    assert.strictEqual(state.markets[market].days.length, 5);
  }
}

console.log('million simulation UI: all tests passed');
