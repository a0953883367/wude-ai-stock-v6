'use strict';
const fs=require('fs');
const assert=require('assert');
const html=fs.readFileSync('practical-ranking.html','utf8');
const js=fs.readFileSync('practical_ranking.js','utf8');
const index=fs.readFileSync('index.html','utf8');
const central=fs.readFileSync('decision-hub.html','utf8');
[
  '實用權重排名','10日、1個月、3個月、半年、資金流、估值、買進位置、大盤風險與資料品質',
  'TW_STOCK','US_STOCK','TW_ETF','US_ETF','20組前不可使用','60組後才可人工決定是否整合'
].forEach(text=>assert(html.includes(text),`missing practical ranking UI: ${text}`));
assert(js.includes('reports/practical_weighted_ranking.json'));
assert(js.includes('practical_shadow_score'));
assert(index.includes('practical-ranking.html'));
assert(central.includes('practical-ranking.html'));
new Function(js);
console.log('practical weighted ranking UI checks passed');
