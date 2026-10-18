(function(root, factory){
  'use strict';
  var api=factory();
  if(typeof module==='object' && module.exports) module.exports=api;
  else {root.WudeStockAgent=api; api.mount(root);}
}(typeof self!=='undefined'?self:this,function(){
  'use strict';
  function esc(v){return String(v==null?'—':v).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
  function list(items){if(!items || !items.length)return '<p>未取得可用證據；不代表沒有反對因素。</p>';return '<ul>'+(items||[]).map(function(x){return '<li>'+esc(typeof x==='string'?x:x.text)+'</li>';}).join('')+'</ul>';}
  function chart(c){
    if(!c)return '<p>缺少可驗證 OHLC，未繪製價格圖。</p>';
    var rows=[{label:'開盤',value:c.open},{label:'最高',value:c.high},{label:'最低',value:c.low},{label:'收盤',value:c.close}].concat(c.levels||[]);
    rows=rows.filter(function(r){return typeof r.value==='number' && Number.isFinite(r.value) && r.value>0;});
    if(rows.length<4)return '<p>圖表資料不足。</p>';
    var values=rows.map(function(r){return r.value;}),min=Math.min.apply(null,values),max=Math.max.apply(null,values),span=max-min||max*0.01;
    var svg=rows.map(function(r,i){var x=150+(r.value-min)/span*210,y=30+i*36;return '<text x="8" y="'+(y+5)+'" fill="currentColor">'+esc(r.label)+'</text><line x1="150" y1="'+y+'" x2="360" y2="'+y+'" stroke="#52647b"/><circle cx="'+x+'" cy="'+y+'" r="5" fill="#60d5bf"/><text x="380" y="'+(y+5)+'" fill="currentColor">'+esc(r.value)+'</text>';}).join('');
    return '<figure><figcaption>'+esc(c.label)+'｜'+esc(c.session_date)+'｜'+esc(c.unit)+(c.stale_or_unverified?'｜過期／未驗證，僅顯示來源快照':'')+'</figcaption><svg role="img" aria-label="單一交易日收盤與均線價位比較" viewBox="0 0 520 '+(rows.length*36+30)+'" style="width:100%;min-width:280px">'+svg+'</svg></figure>';
  }
  function predictions(p){
    if(!p || !(p.forecasts||[]).length)return '<p>未取得可對應的凍結模型判斷；下方僅為報表條件。</p>';
    var feasibility={blocked:'阻擋',not_blocked_not_order_authorization:'凍結紀錄未阻擋；非下單授權',unknown_frozen_guard_evidence:'凍結阻擋證據未知'};
    var labels={UP:'偏多',DOWN:'偏空',FLAT:'持平',UNKNOWN:'未知'},rows=p.forecasts;
    var max=Math.max.apply(null,rows.map(function(r){return Math.abs(r.expected_return_pct)||0;}))||1;
    var bars=rows.map(function(r,i){var value=r.expected_return_pct,width=Math.abs(value)/max*130,y=28+i*28;return '<text x="5" y="'+(y+4)+'" fill="currentColor">'+esc(r.key.horizon_code)+'</text><rect x="'+(value<0?290-width:290)+'" y="'+(y-8)+'" width="'+width+'" height="14" fill="'+(value<0?'#f28b82':'#60d5bf')+'"/><text x="435" y="'+(y+4)+'" fill="currentColor">'+esc(value)+'%</text>';}).join('');
    var scope=p.investment_scope||{};
    var html='<p>'+esc(scope.quarter_note)+' '+esc(scope.long_term_note)+'</p><figure><figcaption>既有模型凍結預期報酬（不是實際績效或現在報價）</figcaption><svg role="img" aria-label="各週期凍結預期報酬" viewBox="0 0 530 '+(rows.length*28+30)+'" style="width:100%">'+bars+'</svg></figure><p>'+esc(p.note)+'</p>';
    function drivers(items){return list((items||[]).map(function(x){return x.label+'：'+x.expected_return_contribution_pct+' 個百分點';}));}
    function outcome(x){var o=x.outcome||{};return o.status==='matured'?'到期 '+esc(o.session_date)+'｜實際 '+esc(o.realized_return_pct)+'%｜方向判斷 '+(o.expected_direction_correct?'正確':'錯誤')+'｜實際減預期 '+esc(o.return_error_pp)+' 個百分點｜舊事件目標命中欄 '+esc(o.legacy_event_target_correct):'尚無可對應的已結算結果；不借用不同期間績效';}
    html+=rows.map(function(r){var trial=r.improvement_trial||{};return '<article class="task-card"><h4>'+esc(r.label)+'（'+esc(r.horizon_sessions)+'交易日）：'+esc(labels[r.direction])+' '+esc(r.expected_return_pct)+'%</h4><p>固定日 '+esc(r.key.session_date)+'｜'+esc(r.model_version)+'<br>事件目標 '+esc(r.event_target)+'：'+esc(r.event_probability_pct)+'%（未校準，非勝率）｜交易 '+esc(feasibility[r.trade_feasibility]||r.trade_feasibility)+'</p><p>'+outcome(r)+'</p><details><summary>展開支持／反對因素、結果與改善試驗</summary><p>'+esc(r.factor_scope)+'</p><b>向上貢獻（支持偏多／反對偏空）</b>'+drivers(r.upward_drivers)+'<b>向下貢獻（支持偏空／反對偏多）</b>'+drivers(r.downward_drivers)+'<p>'+esc(r.rule_status)+'</p><p>凍結時間 '+esc(r.created_at)+'</p>'+((r.recent_outcomes||[]).map(function(old){return '<p>歷史 '+esc(old.key.session_date)+'｜'+esc(old.model_version)+'｜原判斷 '+esc(labels[old.direction])+' '+esc(old.expected_return_pct)+'%<br>'+outcome(old)+'</p>';}).join('')||'<p>尚無已發布的同股票同週期歷史結算。</p>')+'<p>'+esc(trial.scope)+'｜'+esc(trial.status)+'｜訓練至 '+esc(trial.trained_through)+'<br>候選 '+esc(trial.candidate_model_version)+'｜'+esc((trial.control||{}).reason)+'</p></details></article>';}).join('');
    html+='<details><summary>期限支持範圍與後續研究缺項</summary><p>本檔已載入季度模型：'+esc((scope.quarter_model_codes||[]).join('、')||'無')+'</p>'+list(scope.reusable_inputs)+list(scope.separate_planning_required)+'</details>';
    html+='<details><summary>來源指紋、缺口與其他歷史錯題</summary>'+list((p.sources||[]).map(function(x){return x.path+'｜'+x.updated_at+'｜SHA256 '+x.sha256;}))+list(p.issues)+(p.related_historical_diagnostics||[]).map(function(d){return '<p>'+esc(d.scope)+'<br>'+esc(d.event_id)+'｜'+esc(d.source_start_date)+'～'+esc(d.evaluated_end_date)+'<br>'+esc(d.diagnosis)+'<br>'+esc(d.learning_action)+'｜'+esc(d.classification_status)+'<br>相關試驗：'+esc((d.candidate||{}).status)+'｜'+esc((d.candidate||{}).limitation)+'</p>';}).join('')+'</details>';
    return html;
  }
  function render(r,now){
    now=now||Date.now();
    var expiry=Date.parse(r.expires_at),expired=!Number.isFinite(expiry)||expiry<=now;
    var html=(r.frozen_predictions?predictions(r.frozen_predictions):'')+chart(r.chart)+'<h3>'+esc(r.symbol)+'｜'+esc(r.status_label||r.status)+'</h3><p>'+esc(r.summary)+'</p>';
    if(expired)html+='<p role="alert">條件已過期或沒有有效期限。請重新提交；下方不顯示可用觸發條件。</p>';
    html+='<details><summary>展開目前報表的技術條件（與凍結模型分開）</summary>';
    html+=(r.horizons||[]).map(function(h){
      var safe=!expired && h.status==='conditional_research',reference=!expired && h.id==='longer';
      return '<article class="task-card"><h4>'+esc(h.label)+'｜'+esc(h.status)+'</h4><p>'+esc(h.scope)+'</p><b>支持偏多／反對偏空</b>'+list(h.bullish_evidence)+'<b>支持偏空／反對偏多</b>'+list(h.bearish_evidence)+['bullish','bearish'].map(function(k){var s=h[k]||{};return '<p><b>'+(k==='bullish'?'偏多':'偏空')+'</b>｜'+((safe || reference && s.trigger)?(safe?'觸發：':'僅供後續核對的參考條件（資料不足）：')+esc(s.trigger)+'<br>失效：'+esc(s.invalidation):'資料不足／過期，無可用方向結論')+'</p>';}).join('')+list(h.missing_data)+'</article>';
    }).join('');
    html+='</details>';
    html+='<h4>資料與風險缺口</h4>'+list(r.missing_data);
    if(r.hedging)html+='<h4>反向ETF／放空與避險：資料不足</h4><p>'+esc(r.hedging.summary)+'</p>'+list(r.hedging.risks);
    html+='<h4>來源與時間</h4>'+(r.sources||[]).map(function(s){return '<p>'+esc(s.source)+'｜'+esc(s.status)+'｜來源交易日／掃描：'+esc(s.source_session_date||s.as_of)+'｜報表發布：'+esc(s.published_at)+'｜讀取：'+esc(s.retrieved_at)+'</p>'+(s.summary?'<p>'+esc(s.summary)+' '+esc(s.note)+'</p>':'')+list((s.articles||[]).map(function(a){return (a.title||'')+'｜'+(a.publisher||a.source||'來源未標示')+'｜'+(a.published_at||'未提供文章時間')+'｜'+(a.url||'');}));}).join('');
    html+='<p>任務：'+esc(r.task_key)+'｜結果驗證：'+(r.verified?'已檢查輸出契約':'未通過／尚未執行')+'｜有效至：'+esc(r.expires_at)+'</p>';
    html+='<p>既有模型研究；事件機率未校準，沒有目標價或保證獲利。總經預警未在此版本實作。</p><p>'+esc((r.limits||{}).decision_needed)+'</p>';
    return html;
  }
  function mount(win){
    var form=win.document.getElementById('stockResearchForm');if(!form)return;
    var output=win.document.getElementById('stockResearchResult'),progress=win.document.getElementById('stockProgress'),cancel=win.document.getElementById('stockCancel');
    var controller=null,sequence=0,last=null,timer=null;
    function refresh(){if(last)output.innerHTML=render(last);}
    cancel.addEventListener('click',function(){sequence++;if(controller)controller.abort();controller=null;cancel.disabled=true;progress.textContent='已停止等待，未收到完成證據；可重新提交安全重讀。';});
    form.addEventListener('submit',async function(event){
      event.preventDefault();var seq=++sequence;if(controller)controller.abort();controller=new AbortController();
      last=null;output.innerHTML='';if(timer)win.clearTimeout(timer);
      var token=win.WudeLiveClient.accessToken(win.localStorage);
      if(!token){progress.textContent='受阻：缺少既有唯讀裝置授權，未執行分析；請由同網站即時資金流頁面登入後返回。';cancel.disabled=true;return;}
      cancel.disabled=false;progress.textContent='讀取報表、驗證來源並產出條件中；尚未完成。';
      var timeout=win.setTimeout(function(){if(seq===sequence && controller)controller.abort();},30000);
      try{
        var response=await win.fetch(String(win.WUDE_LIVE_API_BASE||'').replace(/\/$/,'')+'/api/agent/stock-analysis',{
          method:'POST',headers:{'Content-Type':'application/json','X-Live-Token':token},cache:'no-store',credentials:'omit',
          signal:controller.signal,body:JSON.stringify({question:win.document.getElementById('stockQuestion').value})});
        if(!response.ok)throw new Error('HTTP '+response.status);
        var payload=await response.json();if(!payload.ok||!payload.data)throw new Error('invalid response');
        if(seq!==sequence)return;last=payload.data;refresh();progress.textContent=last.status_label||last.status;
        timer=win.setTimeout(refresh,Math.max(0,Math.min(2147483647,Date.parse(last.expires_at)-Date.now()+20)));
      }catch(error){if(seq===sequence)progress.textContent='未完成：'+(error.name==='AbortError'?'等待中斷／逾時，可重新提交。':error.message+'；請確認現有授權與服務部署。');}
      finally{win.clearTimeout(timeout);if(seq===sequence){controller=null;cancel.disabled=true;}}
    });
    win.document.addEventListener('visibilitychange',refresh);
  }
  return {render:render,chart:chart,predictions:predictions,mount:mount};
}));
