(function (root) {
  'use strict';

  var labels = {green:'綠燈',blue:'藍燈',yellow:'黃燈',orange:'橘燈',red:'紅燈'};

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (char) {
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char];
    });
  }

  function lamp(light) {
    var value = labels[light] ? light : 'blue';
    return '<span class="lamp '+value+'" aria-label="'+labels[value]+'"></span>';
  }

  function renderSummary(summary) {
    var rows = [
      ['流程階段', summary.workflow_stages || 0],
      ['監控層', summary.monitoring_layers || 0],
      ['追蹤模型', summary.tracked_models || 0],
      ['60日進度', (summary.validation_days || 0)+' / '+(summary.validation_target || 60)]
    ];
    return rows.map(function (row) { return '<div class="metric"><span>'+esc(row[0])+'</span><b>'+esc(row[1])+'</b></div>'; }).join('');
  }

  function renderStages(stages) {
    return stages.map(function (row) {
      var progress = row.target ? '<div class="progress"><i style="width:'+Math.min(100, Number(row.current || 0) / Number(row.target) * 100)+'%"></i></div>' : '';
      return '<article class="card stage-card"><div class="card-head"><div class="card-title"><span class="stage-index">'+esc(row.order)+'</span>'+esc(row.label)+'</div><span class="badge '+esc(row.light)+'">'+lamp(row.light)+' '+esc(row.status_label)+'</span></div><div class="detail">'+esc(row.detail)+'</div>'+progress+'</article>';
    }).join('');
  }

  function renderLayers(layers) {
    return layers.map(function (row) {
      return '<article class="card"><div class="card-head"><div class="card-title">'+lamp(row.light)+' '+esc(row.label)+'</div><span class="badge '+esc(row.light)+'">'+esc(row.status_label)+'</span></div><div class="detail">'+esc(row.detail)+'</div></article>';
    }).join('');
  }

  function renderModels(models) {
    if (!models.length) return '<div class="empty">模型畢業報告尚未產生，正式 V6 維持鎖定。</div>';
    return models.map(function (row) {
      return '<article class="card"><div class="card-head"><div class="card-title">'+esc(row.label)+'</div><span class="badge '+esc(row.light)+'">'+esc(row.phase_label)+'</span></div><div class="detail">有效進度 '+esc(row.current)+' / '+esc(row.target)+'<br>'+esc(row.reason)+'</div><div class="progress"><i style="width:'+esc(row.progress_pct)+'%"></i></div></article>';
    }).join('');
  }

  function renderActions(actions) {
    if (!actions.length) return '<div class="empty">目前沒有需要處理的事項，系統會繼續累積證據。</div>';
    return actions.map(function (row) {
      return '<article class="card '+esc(row.priority)+'"><div class="card-title">'+esc(row.title)+'</div><div class="detail">'+esc(row.detail)+'</div></article>';
    }).join('');
  }

  function render(payload, doc) {
    doc = doc || document;
    doc.getElementById('overallLamp').className = 'lamp '+esc(payload.light || 'blue');
    doc.getElementById('overallLabel').textContent = payload.status_label || '等待資料';
    doc.getElementById('updatedAt').textContent = payload.updated_at ? '更新 '+payload.updated_at : '';
    doc.getElementById('summary').innerHTML = renderSummary(payload.summary || {});
    doc.getElementById('workflow').innerHTML = renderStages(payload.workflow_stages || []);
    doc.getElementById('layers').innerHTML = renderLayers(payload.monitoring_layers || []);
    doc.getElementById('models').innerHTML = renderModels(payload.models || []);
    doc.getElementById('actions').innerHTML = renderActions(payload.action_queue || []);
    doc.getElementById('safety').textContent = ((payload.safety || {}).note || '控制塔只讀；正式 V6 與下單維持鎖定。');
  }

  function load() {
    return fetch('reports/stock_growth_control.json?ts='+Date.now(), {cache:'no-store'})
      .then(function (response) { if (!response.ok) throw new Error('HTTP '+response.status); return response.json(); })
      .then(function (payload) { render(payload); return payload; })
      .catch(function (error) {
        document.getElementById('overallLamp').className = 'lamp yellow';
        document.getElementById('overallLabel').textContent = '控制塔資料尚未更新';
        document.getElementById('workflow').innerHTML = '<div class="empty">無法讀取控制塔：'+esc(error.message)+'。正式 V6 不受影響。</div>';
      });
  }

  root.StockGrowthControl = {esc:esc, renderSummary:renderSummary, renderStages:renderStages, renderLayers:renderLayers, renderModels:renderModels, renderActions:renderActions};
  if (typeof document !== 'undefined') {
    document.getElementById('refresh').addEventListener('click', load);
    load();
  }
})(typeof window !== 'undefined' ? window : globalThis);
