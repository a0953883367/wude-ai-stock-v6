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
// Exercise rendering with legacy and new reports, without network or page events.
const vm=require('vm');
const ui=scripts.find(source=>source.includes('function card('));
const context=vm.createContext({});
vm.runInContext(ui.slice(0,ui.indexOf("document.getElementById('tabs').addEventListener")),context);
const legacy={symbol:'2303.TW',name:'聯電',market:'TW',price:133.5,official_close_price:133,official_adjusted_close_price:133,official_session_date:'2026-10-16',chart_pattern_shadow_name:'上升楔形',chart_pattern_shadow_direction:'bearish',chart_pattern_shadow_status:'收盤確認跌破',chart_pattern_shadow_entry:172.77,chart_pattern_shadow_stop:180.17,chart_pattern_shadow_target:165.36,chart_pattern_rank_score:69.6,chart_pattern_rank_coverage_pct:100,chart_pattern_rank_components:[{key:'retest',label:'回踩／反抽',score:70,status:'隔日方向延續，尚未回測'}]};
context.row=legacy;
const card=vm.runInContext('card(row,0)',context);
assert.ok(card.includes('資料收盤已越過量度目標'));
assert.ok(card.includes('133.00'));
assert.ok(card.includes('2026-10-16'));
assert.ok(card.includes('非多單停損'));
assert.ok(card.includes('舊版證據未對期，不採用'));
assert.ok(!card.includes('69.60'));
assert.ok(!card.includes('隔日方向延續'));
assert.ok(card.includes('不是買入勝率'));
assert.strictEqual(vm.runInContext('num(null)',context),'—');
assert.strictEqual(vm.runInContext('num(undefined)',context),'—');
vm.runInContext("state.filter='confirmed'",context);
assert.strictEqual(vm.runInContext('visible(row)',context),false);
context.row={...legacy,chart_pattern_rank_evidence_version:2,chart_pattern_shadow_source_close:169,chart_pattern_shadow_session_date:'2026-10-16'};
assert.strictEqual(vm.runInContext('visible(row)',context),true);
assert.ok(vm.runInContext('card(row,0)',context).includes('69.60 分／100'));
