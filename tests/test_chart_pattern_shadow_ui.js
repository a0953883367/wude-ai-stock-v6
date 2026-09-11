'use strict';
const assert=require('assert');
const fs=require('fs');
const home=fs.readFileSync('index.html','utf8');
const html=fs.readFileSync('chart-pattern-shadow.html','utf8');
assert.ok(home.includes('chart-pattern-shadow.html'));
[
  'K線型態核對站','reports/all_analysis.json','雙重頂底','上升／下降／對稱三角形','型態可信度',
  '上升／下降楔形','不改正式排名','不改模型權重','不執行交易','只用收盤K線',
  'chart_pattern_shadow_pivots','chart_pattern_rank_components','chart_pattern_rank_score','chart_pattern_holding_1m_score','日週KD','RSI／MACD','回踩／反抽','至少20個交易日初評','60個交易日再人工審查','chart_pattern_validation.json','第1／3／5交易日自動結算','全部市場','data-market="TW"','data-market="US"','data-market="ETF"','完整名單','已分析','有型態','無明顯型態','pending_candidates','未滿20日不進正式排名','未滿25根收盤K線不做型態判斷','manifest.webmanifest'
].forEach(text=>assert.ok(html.includes(text),`missing chart-pattern UI contract: ${text}`));
assert.ok(html.includes("state.market==='ETF'"));
assert.ok(html.includes("getElementById('markets').addEventListener"));
assert.ok(html.includes('class="direction-up"'));
assert.ok(html.includes('class="direction-down"'));
assert.ok(html.includes("item.key==='kd'"));
assert.ok(html.includes("item.key==='rsi_macd'"));
const scripts=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(m=>m[1]).filter(x=>x.trim());
scripts.forEach(source=>new Function(source));
console.log('chart pattern shadow UI: all tests passed');
