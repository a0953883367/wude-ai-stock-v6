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
  function render(r,now){
    now=now||Date.now();
    var expiry=Date.parse(r.expires_at),expired=!Number.isFinite(expiry)||expiry<=now;
    var html=chart(r.chart)+'<h3>'+esc(r.symbol)+'｜'+esc(r.status_label||r.status)+'</h3><p>'+esc(r.summary)+'</p>';
    if(expired)html+='<p role="alert">條件已過期或沒有有效期限。請重新提交；下方不顯示可用觸發條件。</p>';
    html+=(r.horizons||[]).map(function(h){
      var safe=!expired && h.status==='conditional_research',reference=!expired && h.id==='longer';
      return '<article class="task-card"><h4>'+esc(h.label)+'｜'+esc(h.status)+'</h4><p>'+esc(h.scope)+'</p><b>支持偏多／反對偏空</b>'+list(h.bullish_evidence)+'<b>支持偏空／反對偏多</b>'+list(h.bearish_evidence)+['bullish','bearish'].map(function(k){var s=h[k]||{};return '<p><b>'+(k==='bullish'?'偏多':'偏空')+'</b>｜'+((safe || reference && s.trigger)?(safe?'觸發：':'僅供後續核對的參考條件（資料不足）：')+esc(s.trigger)+'<br>失效：'+esc(s.invalidation):'資料不足／過期，無可用方向結論')+'</p>';}).join('')+list(h.missing_data)+'</article>';
    }).join('');
    html+='<h4>資料與風險缺口</h4>'+list(r.missing_data);
    if(r.hedging)html+='<h4>反向ETF／放空與避險：資料不足</h4><p>'+esc(r.hedging.summary)+'</p>'+list(r.hedging.risks);
    html+='<h4>來源與時間</h4>'+(r.sources||[]).map(function(s){return '<p>'+esc(s.source)+'｜'+esc(s.status)+'｜來源交易日／掃描：'+esc(s.source_session_date||s.as_of)+'｜報表發布：'+esc(s.published_at)+'｜讀取：'+esc(s.retrieved_at)+'</p>'+(s.summary?'<p>'+esc(s.summary)+' '+esc(s.note)+'</p>':'')+list((s.articles||[]).map(function(a){return (a.title||'')+'｜'+(a.publisher||a.source||'來源未標示')+'｜'+(a.published_at||'未提供文章時間')+'｜'+(a.url||'');}));}).join('');
    html+='<p>任務：'+esc(r.task_key)+'｜結果驗證：'+(r.verified?'已檢查輸出契約':'未通過／尚未執行')+'｜有效至：'+esc(r.expires_at)+'</p>';
    html+='<p>有限規則式研究，沒有校準機率、目標價或保證獲利。總經預警未在此版本實作。</p><p>'+esc((r.limits||{}).decision_needed)+'</p>';
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
  return {render:render,chart:chart,mount:mount};
}));
