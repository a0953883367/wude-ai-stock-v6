(function(root){
  'use strict';
  var periods={'10d':{label:'10日',code:'UP_10D'},'21d':{label:'1個月',code:'UP_21D'},'63d':{label:'3個月',code:'UP_63D'},'126d':{label:'半年',code:'UP_126D'}};
  function esc(v){return String(v==null?'':v).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
  function number(v){return v==null||v===''||typeof v==='boolean'?null:Number.isFinite(Number(v))?Number(v):null;}
  function fmt(v,suffix){var n=number(v);return n==null?'等待資料':n.toLocaleString('zh-TW',{maximumFractionDigits:2})+(suffix||'');}
  function index(report){var result={};Object.keys((report||{}).rankings||{}).forEach(function(group){Object.keys(report.rankings[group]).forEach(function(period){report.rankings[group][period].forEach(function(row){result[group+'|'+row.symbol+'|'+period]=row;});});});return result;}
  function group(item){return item.market+'_'+(/ETF/i.test(item.asset_type||'')?'ETF':'STOCK');}
  function render(item,report,lookup,period){
    var chosen=periods[period]||periods['10d'],key=periods[period]?period:'10d',g=group(item),row=(lookup||{})[g+'|'+item.symbol+'|'+key],validation=(((report||{}).validation||{}).groups||{})[g]||{},v=(validation.horizons||{})[key]||{},p=(((item.prediction_engine||{}).horizons)||{})[chosen.code]||{},f=p.factor_explanation||{};
    // Never reuse another period's forecast or the legacy execution mapping as a four-period price plan.
    var matching=!!row&&!!item.session_date&&row.session_date===item.session_date;
    var progress=number(v.completed_comparisons),minimum=number(validation.minimum_preliminary),review=number(validation.minimum_manual_review);
    var reasons=(f.upward_drivers||[]).map(function(x){return x.label;}).filter(Boolean),risks=(f.downward_drivers||[]).map(function(x){return x.label;}).filter(Boolean);
    var html='<section class="decision-overview" aria-label="'+esc(chosen.label)+'整合分析"><div class="overview-heading"><b>'+esc(chosen.label)+'整合分析</b><span>獨立期間・影子驗證</span></div>';
    html+='<p class="overview-date">中央資料日 '+esc(item.session_date||'等待資料')+'｜期間排名日 '+esc(row?row.session_date:'等待資料')+'</p>';
    if(!matching)html+='<p class="overview-wait">'+(row?'資料日期不一致，等待同日報表':'等待本檔期間排名')+'；不沿用其他期間答案。</p>';
    html+='<div class="overview-grid"><div><span>期間影子名次</span><b>'+esc(matching?fmt(row.practical_rank):'等待資料')+'</b></div><div><span>模型上漲機率估計</span><b>'+esc(fmt(p.probability_pct,'%'))+'</b></div><div><span>模型預期報酬</span><b>'+esc(fmt(p.expected_return_pct,'%'))+'</b></div><div><span>模型下行風險估計</span><b>'+esc(fmt(p.downside_risk_pct,'%'))+'</b></div></div>';
    if(p.trade_blocked)html+='<p class="overview-wait">該期間模型已阻擋交易；機率與報酬僅供隔離觀察。</p>';
    html+='<p><b>狀態：</b>'+esc(matching?(row.decision_usage_label||'等待驗證狀態'):'等待資料')+'。'+esc(matching?(row.status_label||''): '')+'</p>';
    html+='<p><b>支持依據：</b>'+esc(reasons.length?reasons.join('、'):'等待該期間解釋')+'<br><b>反向依據：</b>'+esc(risks.length?risks.join('、'):'未提供反向依據，不等於沒有風險')+'</p>';
    html+='<p><b>本期間驗證：</b>'+esc(progress==null?'等待驗證資料':'已完成 '+progress+' 組同期間比較')+'｜初評 '+esc(fmt(minimum))+' 組｜人工審查 '+esc(fmt(review))+' 組。模型估計不等於已驗證勝率。</p>';
    html+='<p class="overview-wait">'+esc(chosen.label)+'專屬買進區／停損／目標尚未提供，等待同期間計畫。下方舊制價格計畫另行標示，不能當成本期間訊號。</p>';
    if(matching)html+='<details><summary>本期間評分依據</summary>'+Object.keys(row.components||{}).map(function(k){var c=row.components[k];return '<p>'+esc(c.label)+'：'+esc(fmt(c.score))+' 分｜既有權重 '+esc(fmt(c.weight_pct,'%'))+'</p>';}).join('')+'</details>';
    return html+'</section>';
  }
  root.WudeDecisionOverview={index:index,render:render,periods:periods};
}(typeof window==='undefined'?globalThis:window));
