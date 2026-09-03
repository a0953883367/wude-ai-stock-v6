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
  '明日預判隔離','下一交易日預判','上漲機率估計','可買性','趨勢、量價與資金流若支持續漲仍可入榜',
  '中央唯一答案','台股官方財報','台股法人連動','綜合影子試走','統一證據','模型畢業','部位控制',
  '中央決策','綜合影子排名','正式基準／影子比較','20日初評','60日後才可人工決定是否整合',
  '時段基準（未加影子）','影子試跑結果','影子分數調整','同時段名次變化','影子上調最多','影子下調最多','名次不變',
  '價格到買進區','高於買進區','停損／獲利出場','可進場','等進場價','不進場／退出','買進區','停損','目標一','目標二','進出場規則',
  '1～5 日','45 日','6 個月','有衝突','資料不足','查看全部證據與日期'
].forEach(text=>assert(ui.includes(text),`missing UI copy: ${text}`));
assert(html.includes('decision_hub.js'));
assert(html.includes('app_shell.js'));
const appShell=fs.readFileSync('app_shell.js','utf8');
assert(appShell.includes('live-flow.html'));
assert(appShell.includes('inverse-etf-shadow.html'));
assert(html.includes('id="liveLink"'));
assert(html.includes('live_config.js'));
assert(js.includes('前往大量買賣頁完成一次性手機授權'));
assert(js.includes('/api/large-buy-alerts?after=0&limit=100'));
assert(js.includes('inverse_etf_live_shadow'));
assert(js.includes('中央連動：買方支持'));
assert(js.includes('institutional_snapshot'));
assert(js.includes('institutional_link'));
assert(js.includes('官方法人已連動中央 AI'));
assert(js.includes("item.market!=='TW'"));
assert(js.includes("fetchJSON('comprehensive_shadow_ranking.json'"));
assert(js.includes('ranking_files'));
assert(js.includes('loadShadowSelection'));
assert(js.includes("state.mode==='compare'"));
assert(js.includes('影子覆蓋'));
assert(html.includes('decision_hub.js?v=8'));
assert(html.includes('.compare-filters[hidden]'));
assert(js.includes("if(state.mode!=='decision')return;"));
assert(js.includes("compareFilter:'up'"));
assert(js.includes("state.compareFilter==='up'?change>0:state.compareFilter==='down'?change<0:change===0"));
assert(js.includes("state.compareFilter==='down'?first-second:second-first"));
assert(js.includes("compareFilters.hidden=state.mode!=='compare'"));
assert(html.includes('next-session-shadow.html'));
assert(js.includes('next_session_prediction'));
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
assert(report.readiness.validation_60d.collected_trading_days>=5);
assert.strictEqual(report.portfolio_control.risk_controls.orders,'不連券商、不自動下單');
assert(report.single_answer.headline);
assert.strictEqual(decisions.length,374);
assert(decisions.every(row=>row.formal_ranking_unchanged===true));
assert(decisions.every(row=>['short','medium','long'].every(key=>row.horizons[key])));
assert(decisions.every(row=>['short','medium','long'].every(key=>row.horizons[key].execution)));
const shadowIndex=JSON.parse(fs.readFileSync('reports/comprehensive_shadow_ranking.json','utf8'));
const shadowRows=Object.values(shadowIndex.ranking_files).flatMap(group=>Object.values(group)).flatMap(path=>JSON.parse(fs.readFileSync('reports/'+path,'utf8')).rankings);
assert(shadowRows.some(row=>row.rank_change!==0));
assert(shadowRows.some(row=>row.rank_change>0));
assert(shadowRows.some(row=>row.rank_change<0));
assert(shadowRows.some(row=>row.rank_change===0));
assert(shadowRows.every(row=>row.rank_change===row.baseline_rank-row.shadow_rank));
assert(shadowRows.every(row=>row.formal_ranking_unchanged===true));
console.log('decision hub UI checks passed');
