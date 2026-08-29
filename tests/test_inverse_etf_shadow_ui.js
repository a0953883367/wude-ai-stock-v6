const fs=require('fs'),vm=require('vm');
const html=fs.readFileSync('inverse-etf-shadow.html','utf8');
const script=fs.readFileSync('inverse_etf_shadow.js','utf8');
[
  '反向 ETF 影子','不改正式排名','不改 45 日與 6 個月統計',
  'reports/inverse_etf_database.json','reports/inverse_etf_shadow.json',
  '商品自己的 OHLC','哪些股票可以怎麼反'
  ,'即時反向可用分數','實測配對','期貨／選擇權尚未接入'
  ,'inverse_etf_live_shadow','wude-live-access-token'
].forEach(text=>{if(!(html+script).includes(text))throw new Error('missing inverse ETF UI: '+text);});
new vm.Script(script,{filename:'inverse_etf_shadow.js'});
const db=JSON.parse(fs.readFileSync('reports/inverse_etf_database.json','utf8'));
if(db.universe_count!==374||db.mappings.length!==374)throw new Error('374 mappings are incomplete');
if(!db.policy.formal_ranking_locked||!db.policy.flow_weight_shadow_unchanged)throw new Error('isolation locks missing');
console.log('inverse ETF shadow UI: all tests passed');
