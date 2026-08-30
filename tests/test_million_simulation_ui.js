'use strict';

const assert = require('assert');
const fs = require('fs');

const html = fs.readFileSync('index.html', 'utf8');
const required = [
  'data-view="MILLION"',
  '100萬元試走',
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
  '每組原選10檔，至少9檔取得',
  '缺少標的資金保留現金、不轉配',
  '資料不足隔離',
  '2026-08-24',
  '全程不送券商訂單',
  '60日自動收集',
  '第6日只做首週完整檢查'
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
assert.deepStrictEqual(state.policy.target_trading_days_by_market, {TW: 60, US: 60});
assert.strictEqual(state.markets.TW.completed_days, 5);
assert.strictEqual(state.markets.TW.target_trading_days, 60);
assert.strictEqual(state.markets.US.completed_days, 5);
assert.strictEqual(state.markets.US.target_trading_days, 60);
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
