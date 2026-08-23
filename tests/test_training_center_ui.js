'use strict';

const assert = require('assert');
const fs = require('fs');

const html = fs.readFileSync('index.html', 'utf8');
[
  'data-view="TRAINING"',
  'AI 模型訓練中心',
  'function showTrainingCenter()',
  '全自動驗證模式',
  '每日收盤自動凍結快照',
  '自動 A/B 測試＋法人權重校準',
  '錯誤案例與缺資料隔離',
  'function renderAutomaticTraining(a)',
  '最大回撤',
  '5 日前向影子試走',
  '1／2／3／5 日出場比較',
  '法人權重校準',
  '中期45天＋長期6個月',
  '60個交易日前禁止修改正式排名',
  '自動 Merge：關閉',
  '券商下單：關閉',
  "fetchJSON('reports/accuracy.json')",
  'data-training-view'
].forEach((text) => assert.ok(html.includes(text), `missing training center UI contract: ${text}`));

const inlineScripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map((match) => match[1])
  .filter((source) => source.trim());
inlineScripts.forEach((source) => new Function(source));

console.log('AI model training center UI: all tests passed');
