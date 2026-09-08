'use strict';
const assert=require('assert');
const fs=require('fs');
const home=fs.readFileSync('index.html','utf8');
const html=fs.readFileSync('chart-pattern-shadow.html','utf8');
assert.ok(home.includes('chart-pattern-shadow.html'));
[
  'K線型態核對站','reports/all_analysis.json','雙重頂底','上升／下降／對稱三角形',
  '上升／下降楔形','不改正式排名','不改模型權重','不執行交易','只用收盤K線',
  'chart_pattern_shadow_pivots','至少20個交易日初評','60個交易日再人工審查','manifest.webmanifest'
].forEach(text=>assert.ok(html.includes(text),`missing chart-pattern UI contract: ${text}`));
const scripts=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(m=>m[1]).filter(x=>x.trim());
scripts.forEach(source=>new Function(source));
console.log('chart pattern shadow UI: all tests passed');
