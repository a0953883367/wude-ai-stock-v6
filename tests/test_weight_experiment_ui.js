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
  '10檔必須全部取得同一交易日官方開收盤價才算有效',
  '不會自動把勝出權重套回正式排名'
].forEach((text) => assert.ok(html.includes(text), `missing weight UI contract: ${text}`));

const inlineScripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map((match) => match[1])
  .filter((source) => source.trim());
inlineScripts.forEach((source) => new Function(source));

console.log('weight experiment UI: all tests passed');
