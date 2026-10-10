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
assert(js.includes("reports/trade_plan_validation.json"));
assert(html.includes('前向驗證資料載入中'));
assert(js.includes('前向驗證'));
assert(js.includes('買進期限'));
assert(js.includes('高於這裡不追'));
assert(js.includes('停損／失效'));
assert(js.includes('目標1'));
assert(js.includes('計畫品質'));
assert(js.includes('stop_too_tight'));
['資料不足', '依據與風險', '來源交易日', '評估時間', '絕對到期時間', '原始買進窗口末日（非結論有效期）',
  '僅影子驗證', '尚未正式採用', '分數不是上漲機率', '參考出場價位'].forEach(text =>
  assert(js.includes(text), 'missing safe-conclusion UI copy: ' + text));
assert(js.includes('使用哪些資料（既有模型參考，不另加權）'));
assert(js.includes('input_evidence_categories'));
['尚未評估後續進場', '原始快照條件', '報表批次時間', '影子計畫模式', '後續可進場評估'].forEach(text => assert(js.includes(text)));
assert(html.includes('尚未評估後續進場'));
assert(!html.includes('data-status="candidate"'));
assert(!html.includes('data-status="eligible"'));
assert(html.includes('id="prospectiveSummary"'));
['台股凍結計畫前瞻觀察', '後續收盤符合原買區（研究觀察，非買進建議）', '凍結原始買區',
  '不是可買數，沒有預測成效結論'].forEach(text => assert(js.includes(text)));
['待核對', '來源完整性待驗證', '價格基準待核對', '資料需更新', '證據時間待核對', '關鍵資料缺漏',
  '尚未驗證', '已完成日K收盤（參考）'].forEach(text => assert(js.includes(text)));
['事前時間資料可用', '不影響分數與結論', '證據配對前向檢查', '尚無準確率改善結論',
  '不代表正式採用就緒', '不改正式 V6、不自動晉升'].forEach(text => assert(js.includes(text)));
assert(js.includes("params.get('symbol')"));
assert(hub.includes('trade-plan-shadow.html'));
assert(hub.includes('查看交易計畫'));
assert(hubJs.includes('查看本檔交易計畫'));
assert(hubJs.includes('trade-plan-shadow.html?symbol='));
console.log('trade plan shadow UI checks passed');

// Keep behavioral regressions in the existing CI entry point as well.
require('./test_trade_plan_shadow_behavior.js');
