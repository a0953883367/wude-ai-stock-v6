(function(){'use strict';
var state={payload:null,market:'ALL',filter:'all',query:'',choices:loadChoices()};
var labels={watch:'列入觀察',wait_entry:'等待買點',skip:'暫不考慮'};
function esc(value){return String(value==null?'':value).replace(/[&<>"']/g,function(ch){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch];});}
function num(value,digits){var n=Number(value);return Number.isFinite(n)?n.toFixed(digits==null?1:digits):'—';}
function loadChoices(){try{return JSON.parse(localStorage.getItem('decision_hub_user_choices_v1')||'{}');}catch(_){return{};}}
function saveChoices(){localStorage.setItem('decision_hub_user_choices_v1',JSON.stringify(state.choices));}
function reportAge(value){return value?String(value).replace('T',' '):'未提供';}
function metric(label,value,note){return'<div class="metric"><span>'+esc(label)+'</span><b>'+esc(value)+'</b><small>'+esc(note||'')+'</small></div>';}
function renderStatus(){var p=state.payload||{},s=p.summary||{},r=p.readiness||{},v=r.forward_validation||{};document.getElementById('status').innerHTML=
metric('中央判斷',(s.decision_count||0)+' 檔','涵蓋台股與美股')+
metric('已辨識衝突',(s.conflict_count||0)+' 檔','點開卡片可看解法')+
metric('60日向前驗證',(v.collected_trading_days||0)+' / '+(v.target_trading_days||60),'剩 '+(v.remaining_trading_days==null?'—':v.remaining_trading_days)+' 個交易日')+
metric('資料不足',(s.data_insufficient_count||0)+' 檔','不使用猜測值補齊');
document.getElementById('updatedChip').textContent='更新 '+reportAge(p.updated_at);
var missing=p.missing_sources||[],warnings=(((r.system_guard||{}).warnings)||[]),notice=document.getElementById('notice'),messages=[];
if(missing.length)messages.push('資料源暫時缺少：'+missing.join('、')+'；相關欄位不會推測');
warnings.forEach(function(w){messages.push((w.title||w.code)+'：'+w.detail);});
if(messages.length){notice.hidden=false;notice.textContent=messages.join('。')+'。';}else{notice.hidden=true;}
}
function horizon(item,key){var h=(item.horizons||{})[key]||{};return'<div class="horizon"><span>'+esc(h.label||key)+'</span><b class="'+esc(h.recommendation||'')+'">'+esc(h.action||'資料不足')+'</b><small>分數 '+num(h.score,1)+'｜信心 '+num(h.confidence,0)+'%</small></div>';}
function conflicts(item){if(!item.conflicts||!item.conflicts.length)return'';return'<div class="conflicts">'+item.conflicts.map(function(c){return'<div class="conflict-row"><b>'+esc(c.reason)+'</b><br>規則：'+esc(c.resolution_rule)+'<br>結果：'+esc(c.resolution)+'</div>';}).join('')+'</div>';}
function evidence(item){return(item.evidence||[]).map(function(e){return'<div class="evidence-row"><div><div class="evidence-title">'+esc(e.source_label)+'・'+esc(e.horizon)+'</div><div class="evidence-reason">'+esc(e.reason)+'｜'+esc(e.status)+'｜'+esc(e.as_of||'未標示日期')+'</div></div><div class="evidence-score"><span class="'+esc(e.direction)+'">'+esc(e.direction)+'</span><br>'+num(e.strength,0)+'</div></div>';}).join('');}
function choiceButtons(item){var selected=state.choices[item.symbol]||'';return'<div class="choices">'+['watch','wait_entry','skip'].map(function(code){return'<button data-choice="'+code+'" data-symbol="'+esc(item.symbol)+'" class="'+(selected===code?'selected':'')+'">'+labels[code]+'</button>';}).join('')+'</div>'+(selected?'<div class="choice-note">你的決定：'+labels[selected]+'（只存在本機）</div>':'');}
function card(item){var f=item.final||{},classes='card'+(item.conflict_count?' conflict':'')+(f.recommendation==='data_insufficient'?' blocked':'');return'<article class="'+classes+'"><div class="card-head"><div><div class="symbol">'+esc(item.market)+'・'+esc(item.symbol)+'・正式 #'+esc(item.formal_rank||'—')+'</div><div class="name">'+esc(item.name)+'</div><div class="meta">現價 '+num(item.price,2)+'｜正式分數 '+num(item.formal_score,1)+'｜'+esc(item.industry||item.asset_type||'未分類')+'</div></div><div class="decision"><b class="'+esc(f.recommendation||'')+'">'+esc(f.action||'資料不足')+'</b><small>信心 '+num(f.confidence,0)+'%</small></div></div><div class="horizons">'+horizon(item,'short')+horizon(item,'medium')+horizon(item,'long')+'</div><div class="why"><b>中央結論：</b>'+esc(f.reason||'尚無結論')+'</div>'+conflicts(item)+(item.data_missing&&item.data_missing.length?'<div class="missing">缺少：'+esc(item.data_missing.join('、'))+'</div>':'')+'<details><summary>查看全部證據與日期</summary><div class="evidence">'+evidence(item)+'</div></details>'+choiceButtons(item)+'</article>';}
function isETF(item){return String(item.asset_type||'').toUpperCase().indexOf('ETF')>=0;}
function filtered(){var rows=(state.payload&&state.payload.decisions)||[],q=state.query.trim().toUpperCase();return rows.filter(function(item){if(state.market==='ETF'&&!isETF(item))return false;if(state.market!=='ALL'&&state.market!=='ETF'&&item.market!==state.market)return false;if(state.filter==='conflict'&&!item.conflict_count)return false;if(state.filter!=='all'&&state.filter!=='conflict'&&(!item.final||item.final.recommendation!==state.filter))return false;if(q&&String(item.symbol+' '+item.name+' '+(item.industry||'')).toUpperCase().indexOf(q)<0)return false;return true;});}
function render(){if(!state.payload)return;renderStatus();var rows=filtered();document.getElementById('count').textContent=rows.length+' 檔';document.getElementById('cards').innerHTML=rows.length?rows.map(card).join(''):'<div class="empty">目前篩選條件沒有資料。</div>';}
function load(){document.getElementById('cards').innerHTML='<div class="empty">正在載入中央決策資料…</div>';fetch('reports/decision_hub.json?v='+Date.now(),{cache:'no-store'}).then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.json();}).then(function(p){state.payload=p;render();}).catch(function(err){document.getElementById('cards').innerHTML='<div class="empty">中央資料暫時無法讀取：'+esc(err.message)+'<br>正式排名不受影響，請稍後按更新。</div>';});}
document.getElementById('refresh').addEventListener('click',load);
document.getElementById('search').addEventListener('input',function(e){state.query=e.target.value;render();});
document.getElementById('markets').addEventListener('click',function(e){var b=e.target.closest('button[data-market]');if(!b)return;state.market=b.dataset.market;this.querySelectorAll('button').forEach(function(x){x.classList.toggle('active',x===b);});render();});
document.getElementById('filters').addEventListener('click',function(e){var b=e.target.closest('button[data-filter]');if(!b)return;state.filter=b.dataset.filter;this.querySelectorAll('button').forEach(function(x){x.classList.toggle('active',x===b);});render();});
document.getElementById('cards').addEventListener('click',function(e){var b=e.target.closest('button[data-choice]');if(!b)return;var symbol=b.dataset.symbol,choice=b.dataset.choice;if(state.choices[symbol]===choice)delete state.choices[symbol];else state.choices[symbol]=choice;saveChoices();render();});
load();
})();
