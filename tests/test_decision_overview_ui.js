const fs=require('fs'),vm=require('vm'),assert=require('assert');
const context={};vm.createContext(context);vm.runInContext(fs.readFileSync('decision_overview.js','utf8'),context);
const ui=context.WudeDecisionOverview;
const report=JSON.parse(fs.readFileSync('reports/practical_weighted_ranking.json'));
const main=JSON.parse(fs.readFileSync('reports/decision_hub.json'));
const items=main.decision_files.flatMap(f=>JSON.parse(fs.readFileSync('reports/'+f)).decisions);
const lookup=ui.index(report);
let matches=0;
for(const item of items)for(const period of Object.keys(ui.periods)){
 const row=lookup[item.market+'_'+(/ETF/i.test(item.asset_type||'')?'ETF':'STOCK')+'|'+item.symbol+'|'+period];
 const html=ui.render(item,report,lookup,period);
 assert(html.includes(ui.periods[period].label+'整合分析'));
 assert(html.includes('專屬買進區／停損／目標尚未提供'));
 if(row&&row.session_date===item.session_date){matches++;assert(html.includes(row.decision_usage_label));}
}
assert(matches>0);
const item={market:'US',symbol:'TEST',asset_type:'個股',session_date:'2026-10-02',prediction_engine:{horizons:{UP_10D:{probability_pct:61,expected_return_pct:4},UP_21D:{probability_pct:72,expected_return_pct:8}}}};
let html=ui.render(item,null,{},'21d');
assert(html.includes('72%'));assert(!html.includes('61%'));assert(html.includes('等待本檔期間排名'));assert(html.includes('等待驗證資料'));
html=ui.render({...item,prediction_engine:{horizons:{UP_10D:{probability_pct:null,expected_return_pct:0}}}},null,{},'10d');
assert(!html.includes('0%</b></div><div><span>模型預期報酬'));assert(html.includes('0%'));assert(html.includes('等待資料'));
const stale={'US_STOCK|TEST|10d':{session_date:'2026-10-01',practical_rank:999,status_label:'stale status'}};
html=ui.render(item,report,stale,'10d');assert(html.includes('資料日期不一致'));assert(!html.includes('999'));assert(!html.includes('stale status'));
html=ui.render({...item,prediction_engine:{horizons:{UP_10D:{factor_explanation:{upward_drivers:[{label:'<script>alert(1)</script>'}]}}}}},null,{},'10d');assert(!html.includes('<script>'));assert(html.includes('&lt;script&gt;'));
console.log('four-period overview: '+matches+' real-data joins; missing, stale, period isolation and escaping passed');
