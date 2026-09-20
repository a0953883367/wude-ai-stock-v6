const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync('trade-plan-shadow.html', 'utf8');
const js = fs.readFileSync('trade_plan_shadow.js', 'utf8');
const hub = fs.readFileSync('decision-hub.html', 'utf8');
const hubJs = fs.readFileSync('decision_hub.js', 'utf8');

[
  '交易計畫計算器',
  '可買多久、哪裡買、哪裡不追、到哪裡分批賣',
  '系統建議週期',
  '1～5日',
  '45日',
  '約6個月',
  '不連券商'
].forEach(text => assert(html.includes(text), 'missing trade plan UI copy: ' + text));

assert(js.includes("reports/trade_plan_shadow.json"));
assert(js.includes('買進期限'));
assert(js.includes('高於這裡不追'));
assert(js.includes('停損／失效'));
assert(js.includes('目標1'));
assert(js.includes('計畫品質'));
assert(js.includes('stop_adjusted'));
assert(js.includes("params.get('symbol')"));
assert(hub.includes('trade-plan-shadow.html'));
assert(hub.includes('查看交易計畫'));
assert(hubJs.includes('查看本檔交易計畫'));
assert(hubJs.includes('trade-plan-shadow.html?symbol='));
console.log('trade plan shadow UI checks passed');
