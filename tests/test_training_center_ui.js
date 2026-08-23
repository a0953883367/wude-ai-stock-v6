'use strict';

const assert = require('assert');
const fs = require('fs');

const html = fs.readFileSync('index.html', 'utf8');
[
  'data-view="TRAINING"',
  'AI 模型訓練中心',
  'function showTrainingCenter()',
  '訓練＝歷史驗證＋前向測試＋權重校準',
  '完整 V6 凍結快照',
  '5 日前向影子試走',
  '1／2／3／5 日出場比較',
  '法人權重校準',
  '中期45天＋長期6個月',
  '禁止自動改正式排名',
  "fetchJSON('reports/accuracy.json')",
  'data-training-view'
].forEach((text) => assert.ok(html.includes(text), `missing training center UI contract: ${text}`));

const inlineScripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map((match) => match[1])
  .filter((source) => source.trim());
inlineScripts.forEach((source) => new Function(source));

console.log('AI model training center UI: all tests passed');
