(function(){
  'use strict';
  function esc(value){return String(value===undefined||value===null?'—':value).replace(/[&<>"']/g,function(ch){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch];});}
  function statusClass(status){return status==='running_shadow'||status==='sandbox_ready'?'running':'waiting';}
  function healthLabel(status){return {ok:'正常',warning:'注意',critical:'異常'}[status]||status||'尚無紀錄';}
  function statusFacts(agent){
    var runtime=agent.runtime||{},facts=[];
    if(agent.id==='stock_shadow'){
      facts.push(['向前驗證',esc(runtime.trading_days_collected||0)+' / '+esc(runtime.target_trading_days||60)+' 日']);
      facts.push(['正式 V6',runtime.formal_v6_locked?'保持鎖定':'需核對']);
      facts.push(['GPT 教導',runtime.weekly_coach_last_run?'已有紀錄':'尚無紀錄']);
      facts.push(['系統值班員',esc(healthLabel(runtime.system_guard))]);
    }else{
      var available=Array.isArray(runtime.available_now)?runtime.available_now:[];
      var blocked=Array.isArray(runtime.blocked)?runtime.blocked:[];
      facts.push(['現在可做',available.length?esc(available.join('、')):'等待資料']);
      facts.push(['仍需同意',blocked.length?esc(blocked.join('、')):'無']);
    }
    return facts.map(function(item){return '<div class="fact"><span>'+item[0]+'</span><b>'+item[1]+'</b></div>';}).join('');
  }
  function renderAgent(agent){
    var runtime=agent.runtime||{},label=runtime.status_label||agent.mode;
    return '<article class="agent-card" data-agent-id="'+esc(agent.id)+'"><div class="agent-head"><h3>'+esc(agent.name)+'</h3><span class="agent-chip '+statusClass(runtime.status)+'">'+esc(label)+'</span></div><p class="agent-description">'+esc(agent.description)+'</p><div class="facts">'+statusFacts(agent)+'</div><div class="agent-note">資料空間：'+esc(agent.namespace)+'｜每日最多 '+esc((agent.limits||{}).api_calls_per_day)+' 次呼叫｜失敗最多重試 '+esc((agent.limits||{}).retries_per_task)+' 次</div></article>';
  }
  function fetchJSON(path){return fetch(path+'?ts='+Date.now(),{cache:'no-store'}).then(function(response){if(!response.ok)throw new Error(path+' HTTP '+response.status);return response.json();});}
  function renderRuntime(report){
    var summary=report.summary||{},tasks=Array.isArray(report.validations)?report.validations:[];
    document.getElementById('runtimeHeadline').textContent=(report.status_label||'等待驗收')+'｜安全任務 '+(summary.validation_completed||0)+'／'+(summary.validation_total||0)+'｜授權閘門 '+(summary.approval_gates_protected||0)+'／'+(summary.approval_gates_total||0)+' 已保護｜付費模型呼叫 '+(summary.paid_model_calls||0)+' 次';
    document.getElementById('runtimeUpdatedAt').textContent='更新：'+new Date(report.generated_at).toLocaleString('zh-TW',{hour12:false});
    document.getElementById('runtimeTasks').innerHTML=tasks.map(function(task){return '<div class="runtime-task" data-runtime-agent="'+esc(task.agent_id)+'"><b>✅ '+esc(task.agent_name)+'</b><span>'+esc(task.title)+'：'+esc(task.status_label)+'</span></div>';}).join('')||'<div class="empty">尚無安全任務執行紀錄。</div>';
  }
  Promise.all([fetchJSON('reports/agent_control.json'),fetchJSON('reports/agent_runtime.json')]).then(function(pair){
    var report=pair[0],runtime=pair[1];
    document.getElementById('overallStatus').textContent=report.status_label||'控制層可用';
    document.getElementById('updatedAt').textContent='更新：'+new Date(report.generated_at).toLocaleString('zh-TW',{hour12:false});
    document.getElementById('agentCards').innerHTML=(report.agents||[]).map(renderAgent).join('')||'<div class="empty">尚無 Agent 狀態。</div>';
    var labels={formal_v6_locked:'正式 V6 權重',formal_rankings_locked:'正式股票排名',automatic_orders:'券商下單',automatic_payments:'付款與採購',automatic_external_messages:'對外寄送與發布',erp_plc_device_writes:'ERP／PLC／設備寫入'};
    document.getElementById('safetyItems').innerHTML=Object.keys(labels).map(function(key){return '<div class="safety-item">🔒 '+labels[key]+'</div>';}).join('');
    renderRuntime(runtime);
  }).catch(function(error){
    document.getElementById('overallStatus').textContent='狀態讀取失敗';
    var box=document.getElementById('loadError');box.hidden=false;box.textContent='無法讀取 Agent 狀態：'+error.message;
  });
}());
