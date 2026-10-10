(function(){
  'use strict';
  var params=new URLSearchParams(window.location.search);
  var state={payload:null,validation:null,market:'ALL',status:'ALL',horizon:'preferred',query:String(params.get('symbol')||'').trim()};
  var expiryTimer=null;
  var conclusionLabels={eligible:'後續可進場評估',wait:'計畫等待條件',avoid:'原始計畫風險阻擋',insufficient:'待核對'};
  var entryEvaluationLabel='尚未評估後續進場';
  var dataStatusLabels={ready:'已核對',market_not_closed:'市場未收盤',source_attestation_pending:'來源完整性待驗證',price_basis_mismatch:'價格基準待核對',stale_snapshot:'資料需更新',evidence_review_pending:'證據時間待核對',data_missing:'關鍵資料缺漏',validation_pending:'待前向驗證'};
  function esc(v){return String(v==null?'':v).replace(/[&<>"']/g,function(ch){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch];});}
  function numeric(v){if(v==null||typeof v==='boolean'||String(v).trim()==='')return null;var n=Number(v);return Number.isFinite(n)?n:null;}
  function num(v,d){var n=numeric(v);return n==null?'—':n.toFixed(d==null?2:d);}
  function price(v){var n=numeric(v);if(n==null)return '—';return n.toLocaleString('zh-TW',{maximumFractionDigits:n>=1000?1:n>=10?2:3});}
  function pct(v){var n=numeric(v);return n==null?'—':n.toFixed(1)+'%';}
  function fetchJSON(path){return fetch(path+'?ts='+Date.now(),{cache:'no-store'}).then(function(r){if(!r.ok)throw new Error(path+' HTTP '+r.status);return r.json();});}
  function planFor(row){var h=state.horizon==='preferred'?row.preferred_horizon:state.horizon;return (row.plans||{})[h]||{};}
  function expiryTime(conclusion){
    var value=conclusion&&conclusion.expires_at;
    // An absolute, timezone-qualified expiry is required even for cached reports.
    return typeof value==='string'&&/(Z|[+-]\d{2}:\d{2})$/i.test(value)?Date.parse(value):NaN;
  }
  function conclusionFor(plan,now){
    var raw=plan.conclusion, reasons=raw&&Array.isArray(raw.reasons)?raw.reasons.slice():[];
    var code=raw&&raw.code, expiry=expiryTime(raw), invalid='', status=raw&&raw.data_status;
    var statusKnown=status&&Object.prototype.hasOwnProperty.call(dataStatusLabels,status.code);
    var expired=Number.isFinite(expiry)&&now>=expiry;
    if(!raw||!Object.prototype.hasOwnProperty.call(conclusionLabels,code))invalid='缺少有效結論，請更新資料後重新計算。';
    else if(!Number.isFinite(expiry))invalid='有效期限尚未驗證，請更新資料後重新計算。';
    else if(expired)invalid='結論已到期，請更新最新收盤資料並重新計算。';
    else if(code==='eligible'&&((statusKnown&&status.code!=='ready')||(Array.isArray(raw.gates)&&raw.gates.some(function(gate){return gate&&gate.passed===false;}))))invalid='進場檢查尚未全部通過，完成核對前保留參考。';
    if(invalid){code='insufficient';reasons.unshift(invalid);}
    // This page has no later-quote evaluation. Old eligible codes are plan references too.
    var legacyEligible=code==='eligible';
    if(legacyEligible)code='wait';
    var label=legacyEligible?'原始計畫待後續核對':conclusionLabels[code];
    if(code==='insufficient'&&statusKnown&&status.code!=='ready')label=status.label||dataStatusLabels[status.code];
    if(code==='insufficient'&&expired)label=dataStatusLabels.stale_snapshot;
    return {code:code,label:label,reasons:reasons,raw:raw||{},dataStatus:statusKnown?status:null,expired:expired};
  }
  function riskReasons(row,plan,conclusion){
    var reasons=conclusion.reasons.slice();
    var gates=Array.isArray(conclusion.raw.gates)?conclusion.raw.gates:[];
    gates.forEach(function(gate){if(gate&&gate.passed===false&&gate.reason)reasons.push(gate.reason);});
    (Array.isArray(row.risk_blocks)?row.risk_blocks:[]).forEach(function(risk){
      if(typeof risk==='string')reasons.push(risk);
      else if(risk&&(risk.reason||risk.label))reasons.push(risk.reason||risk.label);
    });
    if(plan.no_buy_reason)reasons.push(plan.no_buy_reason);
    return reasons.filter(function(reason,i,all){return typeof reason==='string'&&reason.trim()&&all.indexOf(reason)===i;});
  }
  function evidenceValue(value){
    if(typeof value==='number'&&Number.isFinite(value))return esc(value);
    if(typeof value==='string'&&value.trim())return esc(value);
    return '未提供';
  }
  function evidenceDetails(row){
    var categories=Array.isArray(row.input_evidence_categories)?row.input_evidence_categories:[];
    var body=categories.filter(function(category){return category&&typeof category==='object';}).map(function(category){
      var status=category.applicable===false||category.status==='not_applicable'?'not_applicable':category.status==='reference'?'reference':category.status==='unverified'?'unverified':'missing';
      var label={reference:'既有模型參考',unverified:'尚未驗證',missing:'未提供',not_applicable:'不適用'}[status];
      var items=Array.isArray(category.items)?category.items:[];
      var values=status==='reference'||status==='unverified'?items.filter(function(item){return item&&typeof item==='object';}).map(function(item){
        return '<div>'+esc(item.label||item.key||'未命名欄位')+'：'+evidenceValue(item.value)+'</div>';
      }).join(''):'';
      return '<div class="sell" data-evidence-id="'+esc(category.id||'')+'" data-evidence-status="'+status+'">'+
        '<b>'+esc(category.label||'未命名資料類別')+'</b>｜'+label+
        '<div>'+(values||(status==='reference'||status==='unverified'?'未提供':label))+'</div>'+
        '<div class="meta">資料日期 '+esc(category.as_of||'未知')+'｜來源 '+esc(category.source||'未知')+'</div>'+
        (category.note?'<div class="reason">'+esc(category.note)+'</div>':'')+
      '</div>';
    }).join('');
    return '<details class="input-evidence reason"><summary>使用哪些資料（既有模型參考，不另加權）</summary>'+
      (body||'<div class="reason">未提供分類資料；無法確認各類輸入。</div>')+'</details>';
  }
  function eventDetails(row){
    var snapshot=row.shadow_events, counts=(snapshot||{}).counts||{}, exclusions=counts.exclusion_reasons||{};
    var reasonLabels={missing_or_imprecise_publication_time:'發布時間缺失或僅有日期',missing_or_imprecise_first_seen:'首次觀測時間缺失或不精確',missing_or_imprecise_revision_time:'修訂時間缺失或不精確',not_available_at_cutoff:'截至當時尚不可取得',missing_identity_or_revision:'缺少事件身分或版本',unsupported_event_type:'未支援的事件類型',invalid_record:'事件格式無效',revision_precedes_publication:'修訂早於發布',first_seen_precedes_publication:'觀測早於發布',conflicting_revision:'同版內容衝突',ambiguous_latest_revision:'最新版本不明確'};
    var excluded=Object.keys(exclusions).map(function(reason){
      return esc(Object.prototype.hasOwnProperty.call(reasonLabels,reason)?reasonLabels[reason]:reason)+' '+num(exclusions[reason],0);
    }).join('；');
    var body=snapshot?'<div>'+esc(snapshot.label||'事前事件時間檢查')+'</div>'+
      '<div>輸入事件 '+num(counts.input,0)+'｜事前時間資料可用 '+num(counts.eligible_events,0)+'｜重複 '+num(counts.duplicates,0)+'</div>'+
      '<div class="meta">檢查截止時間 '+esc(snapshot.cutoff||'未知')+'</div>'+
      (excluded?'<div>排除原因：'+excluded+'</div>':''):'<div>未提供事前事件時間資料。</div>';
    return '<details class="shadow-events reason"><summary>事前事件時間檢查（影子診斷）</summary><div class="sell">'+body+
      '<div>僅有日期或缺少精確時間的資料可能被排除；資料可用不代表可進場，不影響分數與結論。</div></div></details>';
  }
  function card(row,now){
    var summary=planFor(row), conclusion=conclusionFor(summary,now), raw=conclusion.raw;
    var basis=raw.price_basis||{}, mismatch=basis.aligned===false;
    var cls={eligible:'candidate',wait:'wait',avoid:'blocked',insufficient:'wait'}[conclusion.code];
    var reference=true, ref='（參考）', reasons=riskReasons(row,summary,conclusion);
    var reasonClass=conclusion.code==='avoid'?' bad':reference?' warn':'';
    var guidance={wait:'原始快照用於建立計畫；尚未評估後續進場，下列價位僅供參考。',avoid:'原始快照條件有風險阻擋；尚未評估後續進場，下列價位僅供參考。',insufficient:'完成資料核對前，不提供可進場判定；下列價位僅供參考。'}[conclusion.code];
    var sell=[];
    if(summary.target1!=null)sell.push('目標1 '+price(summary.target1)+' → 賣 '+num(summary.target1_pct,0)+'%');
    if(summary.target2!=null)sell.push('目標2 '+price(summary.target2)+' → 再賣 '+num(summary.target2_pct,0)+'%');
    if(numeric(summary.runner_pct)>0)sell.push('剩餘 '+num(summary.runner_pct,0)+'% 趨勢續抱');
    var rr=[];
    if(summary.reward_risk_1!=null)rr.push('RR1 '+num(summary.reward_risk_1,2));
    if(summary.reward_risk_2!=null)rr.push('RR2 '+num(summary.reward_risk_2,2));
    return '<article class="card '+cls+'" data-conclusion="'+conclusion.code+'">'+
      '<div class="head"><div><div class="symbol">'+esc(row.market)+'｜'+esc(row.symbol)+'</div><div class="name">'+esc(row.name)+'</div></div>'+
      '<div class="status"><b>'+esc(conclusion.label)+'</b><small>'+esc(summary.label||'')+'｜影子計畫｜'+entryEvaluationLabel+'</small></div></div>'+
      '<div class="reason'+reasonClass+'">'+guidance+'</div>'+
      (conclusion.dataStatus?'<div class="reason'+reasonClass+'" data-source-status="'+(conclusion.expired?'stale_snapshot':conclusion.dataStatus.code)+'">資料狀態：'+esc(conclusion.expired?dataStatusLabels.stale_snapshot:(conclusion.dataStatus.label||dataStatusLabels[conclusion.dataStatus.code]))+(conclusion.expired?'<br>結論有效期限已到，需重新核對最新資料。':conclusion.dataStatus.detail?'<br>'+esc(conclusion.dataStatus.detail):'')+'</div>':'')+
      '<div class="planline">'+
        '<div class="box"><span>來源快照價格'+(mismatch?'（待對齊）':'')+'</span><b>'+price(mismatch?basis.reported_quote:row.price)+'</b></div>'+
        (mismatch?'<div class="box"><span>已完成日K收盤（參考）</span><b>'+price(basis.completed_close)+'</b></div>':'')+
        '<div class="box"><span>買進期限（原始計畫）</span><b class="warn">'+(numeric(summary.buy_window_sessions)>0?num(summary.buy_window_sessions,0)+' 個有效交易日（僅原始窗口）':'未提供有效原始窗口')+'</b></div>'+
        '<div class="box"><span>買進區'+ref+'</span><b>'+price(summary.entry_low)+' ～ '+price(summary.entry_high)+'</b></div>'+
        '<div class="box"><span>高於這裡不追'+ref+'</span><b class="warn">'+price(summary.do_not_chase_above)+'</b></div>'+
        '<div class="box"><span>停損／失效'+ref+'</span><b class="bad">'+price(summary.stop)+(numeric(summary.stop_sell_pct)>0?'｜退出 '+num(summary.stop_sell_pct,0)+'%':'')+'</b></div>'+
        '<div class="box"><span>計畫品質（結構參考）</span><b>'+esc((summary.plan_quality||{}).label||'—')+'</b></div>'+
        '<div class="box"><span>最長持有'+ref+'</span><b>'+num(summary.max_hold_sessions,0)+' 個有效交易日</b></div>'+
      '</div>'+
      (mismatch?'<div class="reason warn">'+esc(basis.note||'行情快照與已完成日K收盤尚未對齊；兩者分開顯示，價格計畫暫供參考。')+'</div>':'')+
      '<div class="sell">'+(reference?'參考出場價位：':'')+(sell.length?sell.join('｜'):'尚無完整分批賣出價')+(rr.length?'<br>'+rr.join('｜'):'')+
      (summary.stop_too_tight?'<br>🛡️ 停損距離過窄：系統不自動放寬，先等待重算。參考安全距離價 '+price(summary.reference_stop_floor):'')+'</div>'+
      '<div class="reason'+reasonClass+'">原始快照條件（依據與風險）：'+(reasons.length?reasons.map(esc).join('<br>'):'未提供完整原始條件；後續進場仍待另行評估。')+'</div>'+
      '<div class="meta">來源交易日 '+esc(raw.as_of||row.session_date||'—')+'<br>報表批次時間 '+esc((state.payload||{}).updated_at||'—')+'<br>評估時間 '+esc(raw.evaluated_at||(state.payload||{}).evaluated_at||'—')+'<br>絕對到期時間 '+esc(raw.expires_at||'未驗證')+'<br>原始買進窗口末日（非結論有效期） '+esc(raw.valid_through_session||'—')+'</div>'+
      '<div class="reason">'+esc((raw.validation||{}).label||'前向驗證待累積')+'｜僅影子驗證，尚未正式採用</div>'+
      '<div class="meta">模型分數 '+num(summary.score,1)+'｜信心分數 '+num(summary.confidence,1)+'｜資料品質 '+pct(summary.data_quality_pct)+'｜正式排名 '+num(row.formal_rank,0)+'<br>分數不是上漲機率；不自動下單。</div>'+
      evidenceDetails(row)+
      eventDetails(row)+
    '</article>';
  }
  function filtered(now){
    if(!state.payload)return [];
    var q=state.query.toUpperCase();
    return (state.payload.plans||[]).filter(function(row){
      if(state.market!=='ALL'&&row.market!==state.market)return false;
      if(state.status!=='ALL'&&conclusionFor(planFor(row),now).code!==state.status)return false;
      if(q&&String(row.symbol||'').toUpperCase().indexOf(q)<0&&String(row.name||'').toUpperCase().indexOf(q)<0)return false;
      return true;
    });
  }
  function renderSummary(now){
    var counts={eligible:0,wait:0,avoid:0,insufficient:0};
    ((state.payload||{}).plans||[]).forEach(function(row){counts[conclusionFor(planFor(row),now).code]+=1;});
    document.getElementById('summary').innerHTML=Object.keys(counts).map(function(code){
      var cls=code==='avoid'?'bad':'warn';
      return '<div class="metric" data-conclusion="'+code+'"><span>'+conclusionLabels[code]+'</span><b class="'+cls+'">'+(code==='eligible'?'尚未評估':counts[code])+'</b></div>';
    }).join('');
  }
  function scheduleExpiry(now){
    if(expiryTimer!=null)window.clearTimeout(expiryTimer);
    expiryTimer=null;
    var next=Infinity;
    ((state.payload||{}).plans||[]).forEach(function(row){
      var expiry=expiryTime(planFor(row).conclusion);
      if(Number.isFinite(expiry)&&expiry>now)next=Math.min(next,expiry);
    });
    if(Number.isFinite(next))expiryTimer=window.setTimeout(render,Math.min(2147483647,Math.max(1,next-now)));
  }
  function render(){
    var now=Date.now(),rows=filtered(now);
    renderSummary(now);
    document.getElementById('count').textContent='影子計畫模式｜'+entryEvaluationLabel+'｜共 '+rows.length+' 檔原始計畫｜目前顯示 '+(state.horizon==='preferred'?'系統建議週期':({short:'1～5日',medium:'45日',long:'約6個月'}[state.horizon]||state.horizon));
    document.getElementById('cards').innerHTML=rows.length?rows.map(function(row){return card(row,now);}).join(''):'<div class="empty">沒有符合條件的股票。</div>';
    scheduleExpiry(now);
  }
  function setActive(container,attr,value){
    document.querySelectorAll(container+' button').forEach(function(btn){btn.classList.toggle('active',btn.getAttribute(attr)===value);});
  }
  function ablationSummary(){
    var audit=(state.payload||{}).evidence_ablation||{};
    var status=audit.status==='descriptive_only'?'僅描述統計':'資料不足';
    var reasonLabels={missing_baseline_candidate_ledger:'缺少基準／候選策略配對紀錄',no_valid_matched_forward_outcomes:'尚無有效且已完成的前向配對',cost_assumption_not_configured:'尚未設定交易成本假設',invalid_registration_or_changed_cost_policy:'登記資料無效或成本規則變更'};
    var reasons=Array.isArray(audit.blocked_reasons)?audit.blocked_reasons.filter(function(reason){return typeof reason==='string'&&reason.trim();}):[];
    return ' 證據配對前向檢查｜'+status+'｜'+String(audit.label||'尚未提供配對前向驗證資料')+
      '｜已登記 '+num(audit.registered_pairs,0)+' 組｜已完成配對 '+num(audit.matched_completed_pairs,0)+' 組。'+
      (reasons.length?'限制：'+reasons.map(function(reason){return Object.prototype.hasOwnProperty.call(reasonLabels,reason)?reasonLabels[reason]:reason;}).join('；')+'。':'')+
      '尚無準確率改善結論；僅影子檢查，不代表正式採用就緒，不改正式 V6、不自動晉升。';
  }
  function renderValidation(){
    var box=document.getElementById('validationSummary');
    var v=state.validation||{},s=v.summary||{},by=s.by_horizon||{};
    if(!v.status){box.textContent='前向驗證尚未建立；交易計畫仍維持影子模式，尚未正式採用。'+ablationSummary();return;}
    function h(key,label){
      var row=by[key]||{};
      return label+'：訊號 '+num(row.signals,0)+'｜觸發 '+num(row.triggered,0)+'｜成熟 '+num(row.matured_triggered,0)+'｜目標1命中 '+pct(row.target1_hit_rate_pct);
    }
    box.textContent='前向驗證｜等待進場 '+num(s.waiting_entry,0)+'｜進行中 '+num(s.active,0)+'｜已成熟 '+num(s.matured,0)+'。'+h('short','短線')+'；'+h('medium','45日')+'；'+h('long','6個月')+'。僅影子驗證，尚未正式採用。'+ablationSummary();
  }
  function load(){
    Promise.all([
      fetchJSON('reports/trade_plan_shadow.json'),
      fetchJSON('reports/trade_plan_validation.json').catch(function(){return {};})
    ]).then(function(pair){
      var payload=pair[0];
      state.validation=pair[1]||{};
      state.payload=payload;
      var v=payload.validation||{};
      document.getElementById('progressChip').textContent='前向驗證 '+num(v.trading_days_collected,0)+' / '+num(v.target_trading_days==null?60:v.target_trading_days,0)+' 日';
      renderValidation();
      if(state.query){
        var input=document.getElementById('search');
        if(input)input.value=state.query;
      }
      render();
    }).catch(function(err){
      document.getElementById('cards').innerHTML='<div class="empty">讀取失敗：'+esc(err.message)+'</div>';
      document.getElementById('count').textContent='資料尚未產生';
    });
  }
  // Replace legacy status controls too, so cached HTML uses the selected conclusion.
  document.getElementById('statuses').innerHTML='<button class="active" data-status="ALL">全部計畫</button>'+['wait','avoid','insufficient'].map(function(code){return '<button data-status="'+code+'">'+conclusionLabels[code]+'</button>';}).join('');
  document.getElementById('markets').addEventListener('click',function(e){var b=e.target.closest('button[data-market]');if(!b)return;state.market=b.dataset.market;setActive('#markets','data-market',state.market);render();});
  document.getElementById('statuses').addEventListener('click',function(e){var b=e.target.closest('button[data-status]');if(!b||['ALL','wait','avoid','insufficient'].indexOf(b.dataset.status)<0)return;state.status=b.dataset.status;setActive('#statuses','data-status',state.status);render();});
  document.getElementById('horizons').addEventListener('click',function(e){var b=e.target.closest('button[data-horizon]');if(!b)return;state.horizon=b.dataset.horizon;setActive('#horizons','data-horizon',state.horizon);render();});
  document.getElementById('search').addEventListener('input',function(){state.query=this.value.trim();render();});
  document.getElementById('refresh').addEventListener('click',function(){location.reload();});
  window.addEventListener('focus',function(){if(state.payload)render();});
  document.addEventListener('visibilitychange',function(){if(!document.hidden&&state.payload)render();});
  load();
}());
