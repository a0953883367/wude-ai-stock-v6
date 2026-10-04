'use strict';

const assert = require('assert');
const fs = require('fs');

const html = fs.readFileSync('index.html', 'utf8');
assert.ok(html.includes('法人額外效益'));
[
  'data-view="WEIGHT"',
  'reports/tw_weight_experiment.json',
  'function showWeightExperiment()',
  '100/0、90/10、80/20',
  '最大回撤',
  '持股淨勝率',
  '資料完整度',
  '排名／次日漲幅一致性',
  '實際TOP20捕捉',
  '每組原選10檔，至少9檔取得同一交易日官方開收盤價即可結算',
  '缺少標的資金保留現金、不轉配',
  '每累積5個有效交易日凍結一個區塊',
  '完成後自動開下一區塊',
  'current_cycle_completed_days',
  '區塊紀錄',
  '影子教導判定',
  '交易日期異常排除',
  'session_validation',
  'excludedIndexes',
  '選股毛損益',
  '交易成本',
  '成本占淨損失',
  '不是三組損失相加',
  '20日產生影子初判',
  '60日才可人工審查'
].forEach((text) => assert.ok(html.includes(text), `missing weight UI contract: ${text}`));

const inlineScripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map((match) => match[1])
  .filter((source) => source.trim());
inlineScripts.forEach((source) => new Function(source));

console.log('weight experiment UI: all tests passed');

// Render real filtered observations: an excluded raw day must not shift the
// visible five-session cycle labels or reappear in the result table.
const vm = require('vm');
const source = html.slice(html.indexOf('function weightModelHtml('), html.indexOf('function showWeightExperiment('));
const context = {
  esc: String, number: String, percent: String, millionMoney: String,
  millionProfitClass: () => '', drawdownMagnitude: String,
  weightPendingHtml: () => '',
};
vm.runInNewContext(source, context);
const rawDays = Array.from({length: 27}, (_, i) => ({
  day: i + 1, cycle: Math.floor(i / 5) + 1, cycle_day: i % 5 + 1,
  session_date: i === 19 ? 'excluded-Sunday' : i === 26 ? 'final-valid-session' : 'session-' + i,
}));
const rendered = context.weightModelHtml({days: rawDays, completed_days: 26,
  session_validation: {excluded_days: [{raw_index: 19, session_date: '2026-09-20'}]},
});
assert.ok(!rendered.includes('excluded-Sunday'));
assert.ok(rendered.includes('第6區塊・第1日｜final-valid-session'));
assert.ok(rendered.includes('交易日期異常排除 1 日'));
console.log('weight session exclusion rendering: passed');
