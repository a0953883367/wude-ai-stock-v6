(function () {
  'use strict';

  var form = document.getElementById('researchForm');
  var input = document.getElementById('researchSymbol');
  var submit = document.getElementById('researchSubmit');
  var status = document.getElementById('requestStatus');
  var accessStatus = document.getElementById('accessStatus');
  var result = document.getElementById('researchResult');
  if (!form || !input || !submit || !status || !accessStatus || !result) return;

  var pending = null;
  var generation = 0;
  var pageActive = true;
  var hasLocalToken = false;
  var lastRequestUnauthorized = false;
  var allowedApiBase = 'https://wude-ai-stock-v6-production.up.railway.app';
  var genericError = '目前無法讀取私人研究，請稍後手動重試。';
  var unauthorizedError = '伺服器未接受現有授權，可能已失效或無法辨識。請確認是否使用原本已授權的瀏覽器與相同網站網址，再檢查既有存取授權。';
  var reasonLabels = {
    unexpected_bar_session: '日線包含不在已核對交易日內的資料。',
    invalid_ohlcv: '日線價格或成交量資料未通過檢查。',
    insufficient_contiguous_indicator_history: '連續日線不足，無法計算完整研究指標。',
    adjusted_price_discontinuity_requires_review: '調整後價格出現不連續，需人工核對。',
    insufficient_volume_history: '成交量歷史不足，無法計算完整研究指標。',
    invalid_indicator: '研究指標未通過數值檢查。',
    requested_window_incomplete: '要求的歷史期間資料尚未完整。',
    insufficient_completed_weekly_history: '已完成的週線歷史不足。',
    research_geometry_unavailable: '目前無法產生研究價位幾何。'
  };
  var featureFields = [
    ['close', '歷史收盤'], ['ma5', 'MA5'], ['ma10', 'MA10'], ['ma20', 'MA20'], ['ma60', 'MA60'],
    ['rsi14_sma', 'RSI14（SMA）'], ['atr14_true_range_sma', 'ATR14（真實區間 SMA）'],
    ['avg_volume_previous20', '前 20 日平均成交量'], ['volume_ratio_previous20', '相對前 20 日量比'],
    ['support20', '20 日支撐參考'], ['resistance20', '20 日壓力參考'],
    ['daily_k9', '日 K9'], ['daily_d9', '日 D9'], ['weekly_k9', '週 K9'], ['weekly_d9', '週 D9'],
    ['macd12_26', 'MACD（12 / 26）'], ['macd_signal9', 'MACD 訊號（9）'], ['macd_histogram', 'MACD 柱狀值']
  ];
  var planFields = [
    ['horizon_sessions', '研究期間（交易日）'], ['entry_low', '研究區間下緣'], ['entry_high', '研究區間上緣'],
    ['stop', '失效價位參考'], ['target1', '幾何目標 1'], ['target2', '幾何目標 2'],
    ['minimum_reward_risk_target1', '目標 1 最低報酬風險比'], ['minimum_reward_risk_target2', '目標 2 最低報酬風險比']
  ];
  var provenanceFields = [
    ['source', '資料來源'], ['session_date', '來源交易日'], ['observed_at', '觀測時間'],
    ['feed', '資料流'], ['interval', '資料週期'], ['adjustment', '調整方式'], ['currency', '幣別'],
    ['price_basis', '價格基準'], ['volume_basis', '成交量基準'], ['input_bar_count', '輸入日線筆數'],
    ['first_input_session', '首筆交易日'], ['symbol_mapping_asof', '代號對照日期'],
    ['historical_point_in_time', '歷史當時資訊可還原'], ['provider_finality_verified', '供應商最終性已核實'],
    ['corporate_actions_independently_verified', '公司行動已獨立核實']
  ];

  function node(tag, text) {
    var element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function record(value) {
    return value && typeof value === 'object' && !Array.isArray(value);
  }

  function display(value, numeric) {
    if (typeof value === 'number' && Number.isFinite(value)) {
      return value.toLocaleString('zh-TW', {maximumFractionDigits: 4});
    }
    if (numeric) return '資料不足';
    if (typeof value === 'boolean') return value ? '是' : '否';
    return typeof value === 'string' && value ? value.slice(0, 300) : '未提供';
  }

  function panel(title, values, fields, numeric) {
    var section = node('section');
    section.className = 'panel';
    section.appendChild(node('h2', title));
    var list = node('dl');
    fields.forEach(function (field) {
      list.appendChild(node('dt', field[1]));
      list.appendChild(node('dd', display(values[field[0]], numeric)));
    });
    section.appendChild(list);
    return section;
  }

  function clearResult() {
    result.textContent = '';
    result.hidden = true;
    status.textContent = '';
  }

  function lock(value) {
    input.disabled = value || !hasLocalToken;
    submit.disabled = value || !hasLocalToken;
    form.setAttribute('aria-busy', value ? 'true' : 'false');
  }

  function discard() {
    generation += 1;
    if (pending) pending.abort();
    pending = null;
    clearResult();
    input.value = '';
    lock(false);
  }

  function configuredApiBase() {
    return typeof window.WUDE_LIVE_API_BASE === 'string' ? window.WUDE_LIVE_API_BASE.replace(/\/+$/, '') : '';
  }

  function readLocalAccess() {
    var token = '';
    var unreadable = false;
    ['wude-live-access-token', 'wude_live_token'].forEach(function (key) {
      try {
        var value = window.localStorage.getItem(key);
        value = typeof value === 'string' ? value.trim() : '';
        if (!token && value) token = value;
      } catch (_) {
        unreadable = true;
      }
    });
    return {token: token, unreadable: unreadable};
  }

  function refreshAccess() {
    if (configuredApiBase() !== allowedApiBase) {
      hasLocalToken = false;
      discard();
      accessStatus.textContent = '私人研究連線設定無法確認；尚未讀取本機授權資料。';
      return '';
    }
    var access = readLocalAccess();
    hasLocalToken = Boolean(access.token);
    if (!hasLocalToken) {
      discard();
      lastRequestUnauthorized = false;
      accessStatus.textContent = access.unreadable ?
        '無法讀取此瀏覽器在本站的本機授權資料。查詢已停用，這不是股票資料查詢失敗。' :
        '此瀏覽器在本站沒有可用的本機授權資料。查詢已停用，這不是股票資料查詢失敗。';
    } else {
      accessStatus.textContent = lastRequestUnauthorized ?
        '本機授權資料仍存在；伺服器上次未接受此請求，可能已失效或無法辨識。' :
        '已找到此瀏覽器的本機授權資料；本機存在不代表伺服器仍接受授權。';
      lock(Boolean(pending));
    }
    return access.token;
  }

  function visible() {
    return pageActive && !document.hidden;
  }

  function render(data) {
    result.appendChild(node('h2', data.symbol + ' · ' + (data.status === 'projected' ? '未驗證研究投影' : '研究資料受阻')));
    result.appendChild(node('p', '私人研究，未經驗證；不是買進指示，不可下單'));
    if (record(data.provenance)) result.appendChild(panel('來源與時間', data.provenance, provenanceFields, false));
    if (data.status === 'projected' && record(data.features)) {
      result.appendChild(panel('歷史研究指標', data.features, featureFields, true));
    }
    if (data.status === 'projected' && record(data.plan) && data.plan.execution_eligible === false) {
      result.appendChild(panel('未驗證價位幾何（僅供研究）', data.plan, planFields, true));
    } else {
      result.appendChild(node('p', '目前沒有可顯示的研究價位幾何。'));
    }
    if (Array.isArray(data.reasons) && data.reasons.length) {
      var reasons = node('section');
      reasons.className = 'panel';
      reasons.appendChild(node('h2', '限制與原因'));
      var list = node('ul');
      data.reasons.slice(0, 20).forEach(function (reason) {
        if (typeof reason === 'string') {
          list.appendChild(node('li', typeof reasonLabels[reason] === 'string' ? reasonLabels[reason] : '研究限制尚待核對。'));
        }
      });
      reasons.appendChild(list);
      result.appendChild(reasons);
    }
    result.hidden = false;
    status.textContent = data.status === 'projected' ? '已載入私人歷史研究；未經驗證。' : '研究條件未滿足；不可用於下單。';
  }

  form.addEventListener('submit', async function (event) {
    event.preventDefault();
    if (pending || !visible()) return;
    clearResult();
    var apiBase = configuredApiBase();
    if (apiBase !== allowedApiBase || typeof AbortController !== 'function') {
      status.textContent = genericError;
      return;
    }
    var token = refreshAccess();
    if (!token) return;
    var symbol = input.value.trim().toUpperCase();
    if (!/^[A-Z][A-Z0-9.-]{0,14}$/.test(symbol)) {
      status.textContent = '請輸入有效的美股代號。';
      return;
    }
    input.value = symbol;
    lastRequestUnauthorized = false;
    var controller = new AbortController();
    var requestGeneration = ++generation;
    pending = controller;
    lock(true);
    status.textContent = '正在讀取私人歷史研究…';
    try {
      var response = await window.fetch(apiBase + '/api/private/us-research', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'Accept': 'application/json', 'X-Live-Token': token},
        body: JSON.stringify({symbol: symbol}),
        credentials: 'omit',
        cache: 'no-store',
        redirect: 'error',
        referrerPolicy: 'no-referrer',
        signal: controller.signal
      });
      if (requestGeneration !== generation || !visible()) return;
      if (response.status === 401) {
        lastRequestUnauthorized = true;
        clearResult();
        status.textContent = unauthorizedError;
        refreshAccess();
        return;
      }
      if (!response.ok) throw new Error('request_failed');
      var payload = await response.json();
      if (requestGeneration !== generation || !visible()) return;
      var data = payload && payload.data;
      if (!payload || payload.ok !== true || !record(data) || data.symbol !== symbol ||
          data.decision_eligible !== false || (data.status !== 'projected' && data.status !== 'blocked')) {
        throw new Error('invalid_response');
      }
      render(data);
    } catch (_) {
      if (requestGeneration === generation && visible()) {
        clearResult();
        status.textContent = genericError;
      }
    } finally {
      if (requestGeneration === generation) {
        pending = null;
        refreshAccess();
      }
    }
  });

  window.addEventListener('pagehide', function () {
    pageActive = false;
    discard();
  });
  window.addEventListener('pageshow', function () {
    pageActive = true;
    refreshAccess();
  });
  window.addEventListener('focus', function () {
    if (visible()) refreshAccess();
  });
  window.addEventListener('storage', function (event) {
    if (event.key === null || event.key === 'wude-live-access-token' || event.key === 'wude_live_token') {
      discard();
      refreshAccess();
    }
  });
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) discard();
    else if (pageActive) refreshAccess();
  });
  refreshAccess();
})();
