'use strict';

const assert = require('assert');
const fs = require('fs');

const html = fs.readFileSync('index.html', 'utf8');
[
  'data-view="HOLDING"',
  '中長期試走',
  'reports/holding_simulation.json',
  'function showHoldingSimulation()',
  '中期45天',
  '長期6個月',
  '前5名每檔20萬元',
  '台股第1名與美股第1名，各50萬元',
  '同期基準',
  '台股0050、美股VOO',
  '全程不送券商訂單'
].forEach((text) => assert.ok(html.includes(text), `missing holding UI contract: ${text}`));
['等待官方價格補齊','不以部分股票冒充完整組合','資料不足進場已隔離','5檔與同期基準的官方開盤價完整後才建立持倉'].forEach((text) => assert.ok(html.includes(text), `missing holding coverage UI: ${text}`));
['等待下一完成交易日買入','部分持有｜','實際持股數','檔／目標2檔','大盤同期淨損益（已建立部分）','尚未建立的市場不列持股、基準與損益'].forEach((text) => assert.ok(html.includes(text), `missing accurate holding status UI: ${text}`));
['估值日','新交易日資料未齊時保留前一完整估值','不混用日期','本次持有損益不列有效'].forEach((text) => assert.ok(html.includes(text), `missing atomic valuation UI: ${text}`));
assert.ok(!html.includes("pending:'等待下週一買入'"), 'pending label must not assume a fixed weekday');

const inlineScripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map((match) => match[1])
  .filter((source) => source.trim());
inlineScripts.forEach((source) => new Function(source));

const state = JSON.parse(fs.readFileSync('reports/holding_simulation.json', 'utf8'));
assert.strictEqual(state.mode, 'web_shadow_only');
assert.strictEqual(state.policy.start_date, '2026-08-24');
assert.strictEqual(state.policy.medium.capital_per_market_twd, 1000000);
assert.strictEqual(state.policy.long.total_capital_twd, 1000000);
for (const market of ['TW', 'US']) {
  const medium = state.medium[market];
  if (medium.pending) {
    assert.strictEqual(medium.pending.picks.length, 5);
    assert.ok(medium.pending.picks.every((pick) => pick.allocation_twd === 200000));
  } else {
    assert.strictEqual(medium.positions.length, 5);
  }
}
const longPending = state.long.pending || {};
const longPickCount = ['TW', 'US'].reduce((count, market) => {
  const pending = longPending[market];
  if (!pending) return count;
  assert.strictEqual(pending.picks.length, 1);
  assert.strictEqual(pending.picks[0].allocation_twd, 500000);
  return count + 1;
}, 0);
assert.strictEqual(longPickCount + state.long.positions.length, 2);

console.log('holding simulation UI: all tests passed');
