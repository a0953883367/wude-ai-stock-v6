(function (root, factory) {
  var api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.WudeTradingClient = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
  'use strict';

  var STORAGE_KEY = 'wude-trading-selection-v1';
  var MAX_SELECTIONS = 3;
  var HARD_CAP = 20000;

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (ch) {
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch];
    });
  }
  function normalize(symbol) {
    var value = String(symbol || '').trim().toUpperCase();
    if (/^\d{4,6}$/.test(value)) return value + '.TW';
    return value;
  }
  function load(storage) {
    try {
      var rows = JSON.parse((storage || root.localStorage).getItem(STORAGE_KEY) || '[]');
      return Array.isArray(rows) ? rows.slice(0, MAX_SELECTIONS) : [];
    } catch (error) { return []; }
  }
  function save(rows, storage) {
    (storage || root.localStorage).setItem(STORAGE_KEY, JSON.stringify(rows.slice(0, MAX_SELECTIONS)));
    return rows;
  }
  function toggle(row, storage) {
    var rows = load(storage), symbol = normalize(row.symbol);
    var index = rows.findIndex(function (item) { return normalize(item.symbol) === symbol; });
    if (index >= 0) rows.splice(index, 1);
    else {
      if (rows.length >= MAX_SELECTIONS) throw new Error('最多只能勾選3檔');
      rows.push({symbol:symbol,name:String(row.name||symbol),market:String(row.market||'TW').toUpperCase()});
    }
    return save(rows, storage);
  }
  function allocation(cash, count) {
    cash = Math.min(HARD_CAP, Math.max(1000, Number(cash) || HARD_CAP));
    var reserve = Math.max(500, cash * 0.1);
    return {cash:cash,reserve:reserve,perSymbol:count ? (cash-reserve)/count : 0};
  }
  function apiUrl(path) {
    var base = String(root.WUDE_LIVE_API_BASE || '').replace(/\/$/, '');
    if (!base) throw new Error('雲端交易服務尚未設定');
    return base + path;
  }
  function token() {
    return root.WudeLiveClient ? root.WudeLiveClient.accessToken(root.localStorage) : '';
  }
  async function request(path, options) {
    var headers = root.WudeLiveClient ? root.WudeLiveClient.requestHeaders(token()) : {};
    headers['Content-Type'] = 'application/json';
    var response = await root.fetch(apiUrl(path), Object.assign({cache:'no-store',credentials:'omit',headers:headers}, options || {}));
    var payload = await response.json().catch(function () { return {}; });
    if (!response.ok || !payload.ok) throw new Error(payload.error || ('HTTP ' + response.status));
    return payload;
  }
  function selected(symbol) {
    symbol = normalize(symbol);
    return load().some(function (item) { return normalize(item.symbol) === symbol; });
  }
  function pickerRows(candidates, query, selectedRows) {
    var selectedSymbols = new Set((Array.isArray(selectedRows) ? selectedRows : []).map(function (row) {
      return normalize(row.symbol);
    }));
    var needle = String(query || '').trim().toUpperCase();
    return (Array.isArray(candidates) ? candidates : []).filter(function (row) {
      if (!needle) return true;
      return String((row.symbol || '')+' '+(row.name || '')).toUpperCase().indexOf(needle) >= 0;
    }).map(function (row, index) {
      return {row:row,index:index,selected:selectedSymbols.has(normalize(row.symbol))};
    }).sort(function (left, right) {
      return Number(right.selected)-Number(left.selected) || left.index-right.index;
    }).map(function (item) { return item.row; });
  }
  function refreshButtons() {
    var rows = load();
    root.document.querySelectorAll('[data-trade-symbol]').forEach(function (button) {
      var active = rows.some(function (row) { return normalize(row.symbol) === normalize(button.getAttribute('data-trade-symbol')); });
      button.classList.toggle('selected', active);
      button.textContent = active ? '✅ 已加入交易清單' : '＋ 加入2萬元交易清單';
    });
    var count = root.document.getElementById('tradeSelectionCount');
    if (count) count.textContent = String(rows.length);
  }
  function money(value) { return Number(value || 0).toLocaleString('zh-TW',{maximumFractionDigits:0}); }
  function renderSelection() {
    var rows = load(), box = root.document.getElementById('tradeSelections');
    if (!box) return;
    box.innerHTML = rows.length ? rows.map(function (row) {
      return '<div class="trade-chip"><b>'+esc(row.name)+'</b><span>'+esc(row.symbol)+'｜'+esc(row.market)+'</span></div>';
    }).join('') : '<div class="trade-empty">尚未勾選股票；可在台股或美股卡片按「加入交易清單」。</div>';
    var input = root.document.getElementById('tradeCash');
    var split = allocation(input ? input.value : HARD_CAP, rows.length);
    root.document.getElementById('tradeAllocation').textContent = rows.length
      ? '總上限 '+money(split.cash)+' 元｜預留 '+money(split.reserve)+' 元｜每檔上限約 '+money(split.perSymbol)+' 元'
      : '總資金硬上限20,000元；最多勾選3檔。';
    refreshButtons();
  }
  function renderPreview(payload) {
    var box = root.document.getElementById('tradePreview');
    if (!box) return;
    var preview = payload.preview || payload;
    var plans = preview.plans || [];
    box.innerHTML = plans.length ? plans.map(function (plan) {
      var reason = plan.eligible ? '可進行模擬撮合' : (plan.reasons || []).join('、');
      return '<div class="trade-plan '+(plan.eligible?'ok':'blocked')+'"><b>'+esc(plan.name)+' '+esc(plan.symbol)+'</b><br>'+
        '分配 '+money(plan.budget)+' 元｜試算 '+esc(plan.quantity)+' 股｜'+esc(plan.currency)+' '+esc(plan.price)+'<br><span>'+esc(reason)+'</span></div>';
    }).join('') : '<div class="trade-empty">尚無預覽資料。</div>';
  }
  async function submitConfig(enabled, emergency) {
    var rows = load(), cash = Math.min(HARD_CAP, Math.max(1000, Number(root.document.getElementById('tradeCash').value) || HARD_CAP));
    var body = {selected:rows.map(function(row){return row.symbol;}),cash_limit:cash};
    if (enabled !== undefined) body.enabled = enabled;
    if (emergency !== undefined) body.emergency_stop = emergency;
    return request('/api/trading/config',{method:'POST',body:JSON.stringify(body)});
  }
  function message(text, bad) {
    var node = root.document.getElementById('tradeMessage');
    if (!node) return;
    node.textContent = text;
    node.classList.toggle('bad', Boolean(bad));
  }
  async function preview() {
    try { message('正在計算…'); var payload = await submitConfig(); renderPreview(payload); message('模擬預覽已更新；尚未送出任何真實委託。'); }
    catch (error) { message(error.message, true); }
  }
  async function start() {
    if (!load().length) { message('請先勾選至少1檔股票。', true); return; }
    try {
      message('正在啟用模擬監控…');
      var payload = await submitConfig(true, false); renderPreview(payload);
      var run = await request('/api/trading/run',{method:'POST',body:'{}'});
      message(run.message || '模擬監控已啟用；真實委託仍為0筆。');
    } catch (error) { message(error.message, true); }
  }
  async function stop() {
    try { await submitConfig(false, true); message('緊急停止已開啟；不會新增任何委託。'); }
    catch (error) { message(error.message, true); }
  }
  async function open() {
    renderSelection();
    root.document.getElementById('tradingModal').classList.add('open');
    root.document.body.style.overflow = 'hidden';
    try {
      var payload = await request('/api/trading/status');
      var positions = Object.keys(payload.positions || {}).length;
      message('目前：模擬模式｜持倉 '+positions+' 檔｜真實委託 '+Number(payload.real_orders_sent || 0)+' 筆');
    } catch (error) { message(error.message, true); }
  }
  function close() { root.document.getElementById('tradingModal').classList.remove('open'); root.document.body.style.overflow=''; }
  function init() {
    if (!root.document) return;
    root.document.addEventListener('click', function (event) {
      var button = event.target.closest('[data-trade-symbol]');
      if (!button) return;
      try {
        toggle({symbol:button.getAttribute('data-trade-symbol'),name:button.getAttribute('data-trade-name'),market:button.getAttribute('data-trade-market')});
        renderSelection();
      } catch (error) { root.alert(error.message); }
    });
    root.document.getElementById('openTrading').addEventListener('click', open);
    root.document.getElementById('tradeClose').addEventListener('click', close);
    root.document.getElementById('tradePreviewButton').addEventListener('click', preview);
    root.document.getElementById('tradeStartButton').addEventListener('click', start);
    root.document.getElementById('tradeStopButton').addEventListener('click', stop);
    root.document.getElementById('tradeCash').addEventListener('input', renderSelection);
    root.document.getElementById('tradingModal').addEventListener('click', function(event){if(event.target===this)close();});
    new MutationObserver(refreshButtons).observe(root.document.getElementById('results'),{childList:true,subtree:true});
    refreshButtons();
  }
  if (root.document) root.document.addEventListener('DOMContentLoaded', init);
  return {HARD_CAP:HARD_CAP,MAX_SELECTIONS:MAX_SELECTIONS,normalize:normalize,load:load,toggle:toggle,allocation:allocation,selected:selected,pickerRows:pickerRows};
}));
