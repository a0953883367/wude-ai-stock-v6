const assert=require('node:assert/strict');
const {render,chart}=require('../stock_agent.js');
const result={symbol:'MSFT<script>',status:'limited',status_label:'有限研究',expires_at:'2026-10-17T08:00:00Z',
 chart:{label:'snapshot',session_date:'2026-10-16',unit:'USD',open:99,high:102,low:98,close:100,levels:[]},
 horizons:[{id:'short',status:'conditional_research',label:'short',bullish_evidence:[{text:'above MA20'}],bearish_evidence:[],bullish:{trigger:'bull trigger',invalidation:'bull invalidation'},bearish:{trigger:'bear trigger',invalidation:'bear invalidation'}}],sources:[],limits:{}};
const html=render(result,Date.parse('2026-10-17T07:00:00Z'));
assert(html.indexOf('<svg')<html.indexOf('<h3>'));
assert(html.includes('bear trigger')&&html.includes('bear invalidation'));
assert(!html.includes('<script>'));
const expired=render(result,Date.parse('2026-10-17T09:00:00Z'));
assert(!expired.includes('bear trigger'));
assert(expired.includes('條件已過期'));
assert(!chart(null).includes('<svg'));
assert(!chart({open:NaN,close:0,low:-1,high:100}).includes('<svg'));
console.log('stock agent UI checks passed');

(async function testInterruptedAndOverlappingRequests(){
  const {mount}=require('../stock_agent.js');
  const elements={};
  for(const id of ['stockResearchForm','stockResearchResult','stockProgress','stockCancel','stockQuestion']){
    elements[id]={innerHTML:'',textContent:'',value:'MSFT',disabled:false,handlers:{},addEventListener(event,fn){this.handlers[event]=fn;}};
  }
  let token='',pending=[];
  const win={document:{getElementById:id=>elements[id],addEventListener(){}},localStorage:{},
    WudeLiveClient:{accessToken:()=>token},WUDE_LIVE_API_BASE:'https://test.invalid',
    setTimeout:()=>1,clearTimeout(){},fetch:()=>new Promise(resolve=>pending.push(resolve))};
  mount(win);
  const submit=()=>elements.stockResearchForm.handlers.submit({preventDefault(){}});
  await submit();assert(elements.stockProgress.textContent.includes('缺少既有唯讀裝置授權'));
  token='test-only';
  const lost=submit();elements.stockCancel.handlers.click();
  pending.shift()({ok:true,json:async()=>({ok:true,data:result})});await lost;
  assert.equal(elements.stockResearchResult.innerHTML,'');
  assert(elements.stockProgress.textContent.includes('已停止等待'));
  const old=submit(),fresh=submit();
  const oldResolve=pending.shift(),freshResolve=pending.shift();
  freshResolve({ok:true,json:async()=>({ok:true,data:{...result,symbol:'NEW'}})});await fresh;
  oldResolve({ok:true,json:async()=>({ok:true,data:{...result,symbol:'OLD'}})});await old;
  assert(elements.stockResearchResult.innerHTML.includes('NEW'));
  assert(!elements.stockResearchResult.innerHTML.includes('OLD'));
  console.log('Stock request cancellation and overlap checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});

const projected={status:'frozen_forecasts_available',forecasts:[{
 key:{horizon_code:'NEXT_1D',session_date:'2026-10-16'},label:'下一交易日',model_version:'frozen-v1',
 expected_return_pct:-0.35,direction:'DOWN',event_target:'UP',event_probability_pct:35,
 trade_feasibility:'blocked',outcome:{status:'matured',session_date:'2026-10-19',realized_return_pct:-2,
 expected_direction_correct:true,return_error_pp:-1.65,legacy_event_target_correct:0},
 upward_drivers:[{label:'新聞<script>',expected_return_contribution_pct:.1}],downward_drivers:[],
 improvement_trial:{scope:'同週期試驗'},rule_status:'未保存規則',recent_outcomes:[]
}],sources:[],issues:[]};
const projection=require('../stock_agent.js').predictions(projected);
assert(projection.includes('偏空 -0.35%'));
assert(projection.includes('事件目標 UP'));
assert(projection.includes('未校準，非勝率'));
assert(projection.includes('方向判斷 正確'));
assert(projection.includes('舊事件目標命中欄 0'));
assert(projection.includes('<details>'));
assert(!projection.includes('<script>'));
