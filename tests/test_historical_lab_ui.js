'use strict';

const assert = require('assert');
const fs = require('fs');

const html = fs.readFileSync('index.html', 'utf8');
[
  'data-view="HISTORY"',
  '歷史驗證',
  'reports/historical_lab.json',
  'function showHistoricalLab()',
  '不影響正式排名',
  '不是完整V6歷史回測',
  '存活者偏差',
  '5日獲利機率',
  '每輪100萬元',
  'A/B/C/D出場時間比較',
  '持有1／2／3／5日',
  '不會疊加成四倍資金',
  "['TW_STOCK','US_STOCK','TW_ETF','US_ETF']",
  '多頭',
  '空頭',
  '盤整'
].forEach((text) => assert.ok(html.includes(text), `missing historical lab UI contract: ${text}`));

const inlineScripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map((match) => match[1])
  .filter((source) => source.trim());
inlineScripts.forEach((source) => new Function(source));

const state = JSON.parse(fs.readFileSync('reports/historical_lab.json', 'utf8'));
assert.strictEqual(state.mode, 'historical_lab_only');
assert.strictEqual(state.is_exact_v6_backtest, false);
assert.strictEqual(state.official_ranking_affected, false);
assert.strictEqual(state.official_ledgers_affected, false);

console.log('historical lab UI: all tests passed');
