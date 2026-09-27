'use strict';
var REPORT=null,GROUP='TW_STOCK',HORIZON='10d';
function esc(value){var div=document.createElement('div');div.textContent=value==null?'—':String(value);return div.innerHTML;}
function number(value,digits){var n=Number(value);return Number.isFinite(n)?n.toFixed(digits==null?1:digits):'—';}
async function load(){
  var response=await fetch('reports/practical_weighted_ranking.json?ts='+Date.now(),{cache:'no-store'});
  if(!response.ok)throw new Error('等待下一次固定報表建立排名');
  REPORT=await response.json();render();
}
function render(){
  var rows=((((REPORT||{}).rankings||{})[GROUP]||{})[HORIZON])||[],validation=((((REPORT||{}).validation||{}).groups||{})[GROUP])||{},horizonValidation=(validation.horizons||{})[HORIZON]||{},horizonInfo=((REPORT||{}).horizons||{})[HORIZON]||{};
  var status={collecting:'累積中',preliminary_only:'20組初評',manual_review_available:'60組人工審查'}[horizonValidation.status]||'累積中';
  document.getElementById('validation').innerHTML='<b>'+esc(horizonInfo.label||HORIZON)+'獨立排名｜向前驗證：'+esc(status)+'</b><div class="note">已完成 '+esc(horizonValidation.completed_comparisons||0)+' 組同期間比較。這一頁只回答「持有 '+esc(horizonInfo.label||HORIZON)+' 哪些股票較適合」；20組前不可使用，60組後才可人工決定是否整合。正式V6維持不變。</div>';
  document.getElementById('results').innerHTML=rows.length?rows.map(function(row){
    var components=Object.keys(row.components||{}).map(function(key){var item=row.components[key]||{};return '<div class="component">'+esc(item.label)+'（'+esc(item.weight_pct)+'%）<b>'+number(item.score,1)+'分／貢獻 '+number(item.weighted_points,2)+'</b></div>';}).join('');
    return '<article class="card '+(row.blocked?'blocked':'')+'"><div class="rank"><h2>'+esc(row.practical_rank)+'. '+esc(row.name)+'（'+esc(row.symbol)+'）</h2><div class="score">'+number(row.practical_shadow_score,2)+'</div></div><div class="meta">正式V6名次 '+esc(row.formal_rank||'—')+'｜現價 '+esc(row.price||'—')+'｜'+esc(row.decision_usage_label)+'</div><span class="status">'+esc(row.status_label)+'</span><div class="components">'+components+'</div></article>';
  }).join(''):'<div class="empty">目前沒有可顯示資料，等待下一次固定收盤報表。</div>';
}
document.getElementById('tabs').addEventListener('click',function(event){var button=event.target.closest('button[data-group]');if(!button)return;GROUP=button.dataset.group;document.querySelectorAll('#tabs button').forEach(function(item){item.classList.toggle('active',item===button);});render();});
document.getElementById('horizonTabs').addEventListener('click',function(event){var button=event.target.closest('button[data-horizon]');if(!button)return;HORIZON=button.dataset.horizon;document.querySelectorAll('#horizonTabs button').forEach(function(item){item.classList.toggle('active',item===button);});render();});
document.getElementById('refresh').addEventListener('click',function(){load().catch(showError);});
function showError(error){document.getElementById('results').innerHTML='<div class="empty">'+esc(error.message||error)+'</div>';}
load().catch(showError);
