(function(){
  'use strict';
  var state={payload:null,market:'ALL',status:'ALL',horizon:'preferred',query:''};
  function esc(v){return String(v==null?'':v).replace(/[&<>"']/g,function(ch){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch];});}
  function num(v,d){var n=Number(v);return Number.isFinite(n)?n.toFixed(d==null?2:d):'—';}
  function price(v){var n=Number(v);if(!Number.isFinite(n))return '—';return n.toLocaleString('zh-TW',{maximumFractionDigits:n>=1000?1:n>=10?2:3});}
  function pct(v){var n=Number(v);return Number.isFinite(n)?n.toFixed(1)+'%':'—';}
  function fetchJSON(path){return fetch(path+'?ts='+Date.now(),{cache:'no-store'}).then(function(r){if(!r.ok)throw new Error(path+' HTTP '+r.status);return r.json();});}
  function statusLabel(status){return {candidate:'有計畫',wait:'等待',blocked:'先不買',ready:'觀察'}[status]||status;}
  function planFor(row){var h=state.horizon==='preferred'?row.preferred_horizon:state.horizon;return (row.plans||{})[h]||{};}
  function card(row){
    var p=planFor(row), summary=p||{}, noBuy=summary.no_buy_reason||'', cls=row.status||'ready';
    var sell=[];
    if(summary.target1!=null) sell.push('目標1 '+price(summary.target1)+' → 賣 '+Number(summary.target1_pct||0)+'%');
    if(summary.target2!=null) sell.push('目標2 '+price(summary.target2)+' → 再賣 '+Number(summary.target2_pct||0)+'%');
    if(Number(summary.runner_pct||0)>0) sell.push('剩餘 '+Number(summary.runner_pct||0)+'% 趨勢續抱');
    var rr=[];
    if(summary.reward_risk_1!=null)rr.push('RR1 '+num(summary.reward_risk_1,2));
    if(summary.reward_risk_2!=null)rr.push('RR2 '+num(summary.reward_risk_2,2));
    return '<article class="card '+esc(cls)+'">'+
      '<div class="head"><div><div class="symbol">'+esc(row.market)+'｜'+esc(row.symbol)+'</div><div class="name">'+esc(row.name)+'</div></div>'+
      '<div class="status"><b>'+esc(statusLabel(cls))+'</b><small>'+esc(summary.label||'')+'</small></div></div>'+
      '<div class="planline">'+
        '<div class="box"><span>現價</span><b>'+price(row.price)+'</b></div>'+
        '<div class="box"><span>買進期限</span><b class="'+(summary.buy_window_sessions?'good':'warn')+'">'+(summary.buy_window_sessions?summary.buy_window_sessions+' 個有效交易日':'目前無')+'</b></div>'+
        '<div class="box"><span>買進區</span><b>'+price(summary.entry_low)+' ～ '+price(summary.entry_high)+'</b></div>'+
        '<div class="box"><span>高於這裡不追</span><b class="warn">'+price(summary.do_not_chase_above)+'</b></div>'+
        '<div class="box"><span>停損／失效</span><b class="bad">'+price(summary.stop)+(summary.stop_sell_pct?'｜退出 '+summary.stop_sell_pct+'%':'')+'</b></div>'+
        '<div class="box"><span>最長持有</span><b>'+esc(summary.max_hold_sessions||'—')+' 個有效交易日</b></div>'+
      '</div>'+
      '<div class="sell">'+(sell.length?sell.join('｜'):'尚無完整分批賣出價')+(rr.length?'<br>'+rr.join('｜'):'')+'</div>'+
      (noBuy?'<div class="reason bad">目前不直接買：'+esc(noBuy)+'</div>':'<div class="reason">計畫有效；仍需依買進區、量價確認與最新收盤資料執行。</div>')+
      '<div class="meta">分數 '+num(summary.score,1)+'｜信心 '+pct(summary.confidence)+'｜資料品質 '+pct(summary.data_quality_pct)+'｜正式排名 '+esc(row.formal_rank||'—')+'</div>'+
    '</article>';
  }
  function filtered(){
    if(!state.payload)return [];
    var q=state.query.toUpperCase();
    return (state.payload.plans||[]).filter(function(row){
      if(state.market!=='ALL'&&row.market!==state.market)return false;
      if(state.status!=='ALL'&&row.status!==state.status)return false;
      if(q&&String(row.symbol||'').toUpperCase().indexOf(q)<0&&String(row.name||'').toUpperCase().indexOf(q)<0)return false;
      return true;
    });
  }
  function render(){
    var rows=filtered();
    document.getElementById('count').textContent='共 '+rows.length+' 檔｜目前顯示 '+(state.horizon==='preferred'?'系統建議週期':({short:'1～5日',medium:'45日',long:'約6個月'}[state.horizon]||state.horizon));
    document.getElementById('cards').innerHTML=rows.length?rows.map(card).join(''):'<div class="empty">沒有符合條件的股票。</div>';
  }
  function setActive(container,attr,value){
    document.querySelectorAll(container+' button').forEach(function(btn){btn.classList.toggle('active',btn.getAttribute(attr)===value);});
  }
  function load(){
    fetchJSON('reports/trade_plan_shadow.json').then(function(payload){
      state.payload=payload;
      var s=payload.summary||{},v=payload.validation||{};
      document.getElementById('summary').innerHTML=
        '<div class="metric"><span>全部計畫</span><b>'+Number(s.total||0)+'</b></div>'+
        '<div class="metric"><span>有計畫</span><b class="good">'+Number(s.candidate||0)+'</b></div>'+
        '<div class="metric"><span>等待</span><b class="warn">'+Number(s.wait||0)+'</b></div>'+
        '<div class="metric"><span>先不買</span><b class="bad">'+Number(s.blocked||0)+'</b></div>';
      document.getElementById('progressChip').textContent='前向驗證 '+Number(v.trading_days_collected||0)+' / '+Number(v.target_trading_days||60)+' 日';
      render();
    }).catch(function(err){
      document.getElementById('cards').innerHTML='<div class="empty">讀取失敗：'+esc(err.message)+'</div>';
      document.getElementById('count').textContent='資料尚未產生';
    });
  }
  document.getElementById('markets').addEventListener('click',function(e){var b=e.target.closest('button[data-market]');if(!b)return;state.market=b.dataset.market;setActive('#markets','data-market',state.market);render();});
  document.getElementById('statuses').addEventListener('click',function(e){var b=e.target.closest('button[data-status]');if(!b)return;state.status=b.dataset.status;setActive('#statuses','data-status',state.status);render();});
  document.getElementById('horizons').addEventListener('click',function(e){var b=e.target.closest('button[data-horizon]');if(!b)return;state.horizon=b.dataset.horizon;setActive('#horizons','data-horizon',state.horizon);render();});
  document.getElementById('search').addEventListener('input',function(){state.query=this.value.trim();render();});
  document.getElementById('refresh').addEventListener('click',function(){location.reload();});
  load();
}());