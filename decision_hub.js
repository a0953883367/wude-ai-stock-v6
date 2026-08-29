(function(){'use strict';
var state={payload:null,market:'ALL',filter:'all',query:'',choices:loadChoices()};
var labels={watch:'列入觀察',wait_entry:'等待買點',skip:'暫不考慮'};
function esc(value){return String(value==null?'':value).replace(/[&<>"']/g,function(ch){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch];});}
function num(value,digits){var n=Number(value);return Number.isFinite(n)?n.toFixed(digits==null?1:digits):'—';}
function loadChoices(){try{return JSON.parse(localStorage.getItem('decision_hub_user_choices_v1')||'{}');}catch(_){return{};}}
function saveChoices(){localStorage.setItem('decision_hub_user_choices_v1',JSON.stringify(state.choices));}
function reportAge(value){return value?String(value).replace('T',' '):'未提供';}
function metric(label,value,note){return'<div class="metric"><span>'+esc(label)+'</span><b>'+esc(value)+'</b><small>'+esc(note||'')+'</small></div>';}
function money(value){var n=Number(value);return Number.isFinite(n)?'NT$'+Math.round(n).toLocaleString('zh-TW'):'—';}
function renderStatus(){var p=state.payload||{},s=p.summary||{},r=p.readiness||{},v=r.validation_60d||r.forward_validation||{},g=r.model_graduation||{},gf=g.summary||{},tf=r.tw_official_financial||{},sq=r.stockq_market_context||{},ue=p.unified_evidence||{},pc=p.portfolio_control||{},answer=p.single_answer||{};document.getElementById('status').innerHTML=
metric('中央判斷',(s.decision_count||0)+' 檔','涵蓋台股與美股')+
metric('已裁決衝突',(s.resolved_conflict_count||s.conflict_count||0)+' 檔','未裁決 '+(s.unresolved_conflict_count||0)+' 檔')+
metric('60日向前驗證',(v.collected_trading_days||0)+' / '+(v.target_trading_days||60),'剩 '+(v.remaining_trading_days==null?'—':v.remaining_trading_days)+' 個交易日')+
metric('台股官方財報',(tf.available||0)+' / '+(tf.requested||0),(tf.coverage_pct==null?'尚待更新':num(tf.coverage_pct,2)+'% 完整'))+
metric('統一證據',(ue.evidence_count||0)+' 筆','格式錯誤 '+(ue.invalid_count||0)+' 筆')+
metric('模型畢業','可審查 '+(gf.eligible_for_manual_graduation||0),'累積中 '+(gf.collecting||0)+'｜需複核 '+(gf.review_required||0))+
metric('StockQ 市場背景',esc(((sq.market_signal||{}).regime)||'尚待更新'),'指標 '+(sq.indicator_count||0)+'｜不改正式排名')+
metric('部位控制',money(pc.suggested_invested_twd||0),'等待買點不計入已投資');
var answerBox=document.getElementById('singleAnswer');answerBox.className='single-answer '+esc(answer.code||'hold_cash');answerBox.innerHTML='<span>中央唯一答案</span><b>'+esc(answer.headline||'尚無結論')+'</b><p>'+esc(answer.detail||'等待完整資料')+'</p>';
document.getElementById('updatedChip').textContent='更新 '+reportAge(p.updated_at);
var missing=p.missing_sources||[],warnings=(((r.system_guard||{}).warnings)||[]),notice=document.getElementById('notice'),messages=[];
if(missing.length)messages.push('資料源暫時缺少：'+missing.join('、')+'；相關欄位不會推測');
if((s.news_coverage_count||0)<(s.decision_count||0))messages.push('新聞掃描已完成 '+(s.news_coverage_count||0)+' / '+(s.decision_count||0)+' 檔；其餘由排程逐輪補齊，不阻斷核心判斷');
warnings.forEach(function(w){messages.push((w.title||w.code)+'：'+w.detail);});
if(messages.length){notice.hidden=false;notice.textContent=messages.join('。')+'。';}else{notice.hidden=true;}
}
function horizon(item,key){var h=(item.horizons||{})[key]||{};return'<div class="horizon"><span>'+esc(h.label||key)+'</span><b class="'+esc(h.recommendation||'')+'">'+esc(h.action||'資料不足')+'</b><small>分數 '+num(h.score,1)+'｜信心 '+num(h.confidence,0)+'%</small></div>';}
function conflicts(item){if(!item.conflicts||!item.conflicts.length)return'';return'<div class="conflicts">'+item.conflicts.map(function(c){return'<div class="conflict-row"><b>'+esc(c.reason)+'</b><br>規則：'+esc(c.resolution_rule)+'<br>結果：'+esc(c.resolution)+'</div>';}).join('')+'</div>';}
function evidence(item){return(item.evidence||[]).map(function(e){return'<div class="evidence-row"><div><div class="evidence-title">'+esc(e.source_label)+'・'+esc(e.horizon)+'</div><div class="evidence-reason">'+esc(e.reason)+'｜'+esc(e.status)+'｜'+esc(e.as_of||'未標示日期')+'</div></div><div class="evidence-score"><span class="'+esc(e.direction)+'">'+esc(e.direction)+'</span><br>'+num(e.strength,0)+'</div></div>';}).join('');}
function choiceButtons(item){var selected=state.choices[item.symbol]||'';return'<div class="choices">'+['watch','wait_entry','skip'].map(function(code){return'<button data-choice="'+code+'" data-symbol="'+esc(item.symbol)+'" class="'+(selected===code?'selected':'')+'">'+labels[code]+'</button>';}).join('')+'</div>'+(selected?'<div class="choice-note">你的決定：'+labels[selected]+'（只存在本機）</div>':'');}
function position(item){var rows=Object.keys(item.portfolio||{}).map(function(key){var r=item.portfolio[key]||{};return esc(key)+'：'+(r.status==='allocatable'?'上限 '+money(r.maximum_twd):'等待買點，配置 0');});return rows.length?'<div class="position"><b>部位控制：</b>'+rows.join('｜')+'；不自動下單</div>':'';}
function card(item){var f=item.final||{},classes='card'+(item.conflict_count?' conflict':'')+(f.recommendation==='data_insufficient'?' blocked':'');return'<article class="'+classes+'"><div class="card-head"><div><div class="symbol">'+esc(item.market)+'・'+esc(item.symbol)+'・正式 #'+esc(item.formal_rank||'—')+'</div><div class="name">'+esc(item.name)+'</div><div class="meta">現價 '+num(item.price,2)+'｜正式分數 '+num(item.formal_score,1)+'｜'+esc(item.industry||item.asset_type||'未分類')+'</div></div><div class="decision"><b class="'+esc(f.recommendation||'')+'">'+esc(f.action||'資料不足')+'</b><small>信心 '+num(f.confidence,0)+'%</small></div></div><div class="horizons">'+horizon(item,'short')+horizon(item,'medium')+horizon(item,'long')+'</div><div class="why"><b>中央結論：</b>'+esc(f.reason||'尚無結論')+'</div>'+position(item)+conflicts(item)+(item.data_missing&&item.data_missing.length?'<div class="missing">可持續補強：'+esc(item.data_missing.join('、'))+'（不等於核心資料缺失）</div>':'')+'<details><summary>查看全部證據與日期</summary><div class="evidence">'+evidence(item)+'</div></details>'+choiceButtons(item)+'</article>';}
function isETF(item){return String(item.asset_type||'').toUpperCase().indexOf('ETF')>=0;}
function filtered(){var rows=(state.payload&&state.payload.decisions)||[],q=state.query.trim().toUpperCase();return rows.filter(function(item){if(state.market==='ETF'&&!isETF(item))return false;if(state.market!=='ALL'&&state.market!=='ETF'&&item.market!==state.market)return false;if(state.filter==='conflict'&&!item.conflict_count)return false;if(state.filter!=='all'&&state.filter!=='conflict'&&(!item.final||item.final.recommendation!==state.filter))return false;if(q&&String(item.symbol+' '+item.name+' '+(item.industry||'')).toUpperCase().indexOf(q)<0)return false;return true;});}
function render(){if(!state.payload)return;renderStatus();var rows=filtered();document.getElementById('count').textContent=rows.length+' 檔';document.getElementById('cards').innerHTML=rows.length?rows.map(card).join(''):'<div class="empty">目前篩選條件沒有資料。</div>';}
function fetchJSON(path,stamp){return fetch('reports/'+path+'?v='+stamp,{cache:'no-store'}).then(function(r){if(!r.ok)throw new Error(path+' HTTP '+r.status);return r.json();});}
function load(){document.getElementById('cards').innerHTML='<div class="empty">正在載入中央決策資料…</div>';var stamp=Date.now();fetchJSON('decision_hub.json',stamp).then(function(p){var files=p.decision_files||[];if(!files.length)throw new Error('中央個股資料清單不存在');return Promise.all(files.map(function(path){return fetchJSON(path,stamp);})).then(function(chunks){p.decisions=[];chunks.forEach(function(chunk){p.decisions=p.decisions.concat(chunk.decisions||[]);});return p;});}).then(function(p){state.payload=p;render();}).catch(function(err){document.getElementById('cards').innerHTML='<div class="empty">中央資料暫時無法讀取：'+esc(err.message)+'<br>正式排名不受影響，請稍後按更新。</div>';});}
document.getElementById('refresh').addEventListener('click',load);
document.getElementById('search').addEventListener('input',function(e){state.query=e.target.value;render();});
document.getElementById('markets').addEventListener('click',function(e){var b=e.target.closest('button[data-market]');if(!b)return;state.market=b.dataset.market;this.querySelectorAll('button').forEach(function(x){x.classList.toggle('active',x===b);});render();});
document.getElementById('filters').addEventListener('click',function(e){var b=e.target.closest('button[data-filter]');if(!b)return;state.filter=b.dataset.filter;this.querySelectorAll('button').forEach(function(x){x.classList.toggle('active',x===b);});render();});
document.getElementById('cards').addEventListener('click',function(e){var b=e.target.closest('button[data-choice]');if(!b)return;var symbol=b.dataset.symbol,choice=b.dataset.choice;if(state.choices[symbol]===choice)delete state.choices[symbol];else state.choices[symbol]=choice;saveChoices();render();});
load();
})();
