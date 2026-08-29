'use strict';

const assert = require('assert');
const fs = require('fs');

const home = fs.readFileSync('index.html', 'utf8');
const html = fs.readFileSync('valuation-risk-shadow.html', 'utf8');

assert.ok(home.includes('valuation-risk-shadow.html'));
[
  '獨立估值風險雷達',
  'reports/valuation_risk_shadow.json',
  '不改正式排名',
  '不改模型權重',
  '不是確定泡沫',
  '幾年營收',
  '幾年獲利',
  '幾年自由現金流',
  '同業溢價估計',
  '至少10個交易日且100筆有效結果'
].forEach((text) => assert.ok(html.includes(text), `missing valuation UI contract: ${text}`));

const inlineScripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map((match) => match[1])
  .filter((source) => source.trim());
inlineScripts.forEach((source) => new Function(source));

console.log('valuation risk shadow UI: all tests passed');

