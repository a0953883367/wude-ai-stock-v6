const fs=require('fs');
const assert=require('assert');
const html=fs.readFileSync('decision-hub.html','utf8');
const js=fs.readFileSync('decision_hub.js','utf8');
const ui=html+js;
const report=JSON.parse(fs.readFileSync('reports/decision_hub.json','utf8'));
const decisions=report.decision_files.flatMap(path=>JSON.parse(fs.readFileSync('reports/'+path,'utf8')).decisions);
const unified=JSON.parse(fs.readFileSync('reports/unified_evidence.json','utf8'));
const evidence=unified.evidence_files.flatMap(path=>JSON.parse(fs.readFileSync('reports/'+path,'utf8')).evidence);

[
  '中央 AI 決策中樞','正式排名鎖定','不自動改權重','不連券商下單',
  '中央唯一答案','台股官方財報','統一證據','模型畢業','部位控制',
  '1～5 日','45 日','6 個月','有衝突','資料不足','查看全部證據與日期'
].forEach(text=>assert(ui.includes(text),`missing UI copy: ${text}`));
assert(html.includes('decision_hub.js'));
assert(html.includes('live-flow.html'));
assert(html.includes('inverse-etf-shadow.html'));
assert(js.includes("fetchJSON('decision_hub.json'"));
assert(js.includes('decision_hub_user_choices_v1'));
assert(js.includes('localStorage.setItem'));
assert(!js.includes('/orders'));
assert(!js.includes('placeOrder'));
assert.strictEqual(report.summary.decision_count,374);
assert.strictEqual(report.policy.formal_ranking_locked,true);
assert.strictEqual(report.policy.automatic_orders,false);
assert.strictEqual(report.policy.horizons_separate,true);
assert.strictEqual(report.unified_evidence.invalid_count,0);
assert.strictEqual(unified.invalid_count,0);
assert.strictEqual(evidence.length,unified.evidence_count);
assert.strictEqual(report.readiness.validation_60d.collected_trading_days,5);
assert.strictEqual(report.portfolio_control.risk_controls.orders,'不連券商、不自動下單');
assert(report.single_answer.headline);
assert.strictEqual(decisions.length,374);
assert(decisions.every(row=>row.formal_ranking_unchanged===true));
assert(decisions.every(row=>['short','medium','long'].every(key=>row.horizons[key])));
console.log('decision hub UI checks passed');
