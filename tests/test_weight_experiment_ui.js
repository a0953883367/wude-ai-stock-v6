'use strict';

const assert = require('assert');
const fs = require('fs');

const html = fs.readFileSync('index.html', 'utf8');
[
  'data-view="WEIGHT"',
  'reports/tw_weight_experiment.json',
  'function showWeightExperiment()',
  '100/0、90/10、80/20',
  '最大回撤',
  '排名換手率',
  '不會自動把勝出權重套回正式排名'
].forEach((text) => assert.ok(html.includes(text), `missing weight UI contract: ${text}`));

const inlineScripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map((match) => match[1])
  .filter((source) => source.trim());
inlineScripts.forEach((source) => new Function(source));

console.log('weight experiment UI: all tests passed');
