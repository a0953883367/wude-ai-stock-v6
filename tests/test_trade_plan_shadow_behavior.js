const fs = require('fs');
const assert = require('assert');
const vm = require('vm');

const source = fs.readFileSync('trade_plan_shadow.js', 'utf8');
const html = fs.readFileSync('trade-plan-shadow.html', 'utf8');
const NOW = Date.parse('2026-10-09T16:00:00Z');

function conclusion(code, overrides = {}) {
  return {
    code,
    label: {eligible: '符合影子進場條件', wait: '等待量價確認', avoid: '暫不進場', insufficient: '資料不足'}[code],
    reasons: ['來源與量價已檢查'],
    as_of: '2026-10-09',
    evaluated_at: '2026-10-09T23:59:00+08:00',
    expires_at: '2026-10-12T13:30:00+08:00',
    valid_through_session: '2026-10-12',
    gates: [],
    validation: {status: 'pending', label: '等待前向驗證', formal_adoption_ready: false},
    evidence: {used_source_ids: [], correlated_groups: [], duplicate_count: 0, additional_weight: 0},
    shadow_only: true,
    ...overrides,
  };
}
function plan(code, overrides = {}) {
  return {
    label: '1～5 日', entry_low: 98, entry_high: 101, do_not_chase_above: 101,
    stop: 95, stop_sell_pct: 100, target1: 108, target1_pct: 25, target2: 112,
    target2_pct: 25, runner_pct: 50, max_hold_sessions: 5, buy_window_sessions: 2,
    score: 80, confidence: 75, data_quality_pct: 90,
    conclusion: conclusion(code), ...overrides,
  };
}
function row(symbol, short = plan('eligible'), overrides = {}) {
  return {
    symbol, market: 'TW', name: symbol + '名稱', price: 100, session_date: '2026-10-09',
    preferred_horizon: 'short', status: 'blocked', formal_rank: 1,
    plans: {
      short,
      medium: plan('avoid', {label: '45 日', conclusion: conclusion('avoid', {reasons: ['中期風險阻擋']})}),
      long: plan('wait', {label: '6 個月', conclusion: conclusion('wait', {reasons: ['長期等待確認']})}),
    },
    ...overrides,
  };
}

class Element {
  constructor() {
    this.listeners = {};
    this._html = '';
    this.textContent = '';
    this.value = '';
    this.buttons = [];
  }
  set innerHTML(value) {
    this._html = value;
    this.buttons = [...value.matchAll(/<button([^>]*)>([^<]*)<\/button>/g)].map(match => {
      const attrs = Object.fromEntries([...match[1].matchAll(/([\w-]+)="([^"]*)"/g)].map(a => [a[1], a[2]]));
      const active = new Set((attrs.class || '').split(/\s+/));
      return {
        textContent: match[2],
        dataset: Object.fromEntries(Object.entries(attrs).filter(([k]) => k.startsWith('data-')).map(([k, v]) => [k.slice(5), v])),
        getAttribute: name => attrs[name],
        classList: {toggle: (name, value) => value ? active.add(name) : active.delete(name), contains: name => active.has(name)},
      };
    });
  }
  get innerHTML() { return this._html; }
  set textContent(value) {
    this._text = String(value);
    this._html = this._text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    this.buttons = [];
  }
  get textContent() { return this._text; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
}

async function boot(rows, options = {}) {
  let now = options.now == null ? NOW : options.now;
  let nextTimerId = 1;
  const timers = new Map();
  const nodes = Object.fromEntries(['count', 'cards', 'summary', 'validationSummary', 'progressChip', 'markets', 'statuses', 'horizons', 'search', 'refresh'].map(id => [id, new Element()]));
  ['markets', 'statuses', 'horizons'].forEach(id => {
    nodes[id].innerHTML = html.match(new RegExp('<div class="row" id="' + id + '">([\\s\\S]*?)</div>'))[1];
  });
  const documentListeners = {}, windowListeners = {};
  const document = {
    hidden: false,
    getElementById: id => nodes[id],
    querySelectorAll: selector => nodes[selector.match(/^#(\w+) button$/)[1]].buttons,
    addEventListener: (name, fn) => { documentListeners[name] = fn; },
  };
  const window = {
    location: {search: options.search || ''},
    setTimeout: (fn, delay) => { const id = nextTimerId++; timers.set(id, {fn, delay, at: now + delay}); return id; },
    clearTimeout: id => timers.delete(id),
    addEventListener: (name, fn) => { windowListeners[name] = fn; },
  };
  class ClockDate extends Date { static now() { return now; } }
  const payload = {plans: rows, summary: {candidate: 999, blocked: 999}, validation: {trading_days_collected: 12, target_trading_days: 60}, evidence_ablation: options.ablation};
  const original = JSON.stringify(payload);
  vm.runInNewContext(source, {
    document, window, URLSearchParams, Date: ClockDate,
    location: {reload() {}},
    fetch: async url => {
      if (url.startsWith('reports/trade_plan_validation.json') && options.failValidation) throw new Error('missing validation');
      return {ok: true, json: async () => url.startsWith('reports/trade_plan_shadow.json') ? payload : (options.validation || {})};
    },
  });
  // Finish the fetch, json, Promise.all, and render continuations.
  await new Promise(resolve => setImmediate(resolve));
  assert.strictEqual(JSON.stringify(payload), original, 'rendering must not mutate backend or legacy status');
  assert(!nodes.cards.innerHTML.includes('讀取失敗'), nodes.cards.innerHTML);
  return {
    nodes, timers,
    click(container, key, value) {
      const button = nodes[container].buttons.find(b => b.dataset[key] === value);
      assert(button, 'missing control ' + key + '=' + value);
      nodes[container].listeners.click({target: {closest: () => button}});
      assert(button.classList.contains('active'), 'selected control must be active');
    },
    search(query) { nodes.search.value = query; nodes.search.listeners.input.call(nodes.search); },
    advanceTo(value, runTimers = true) {
      now = value;
      if (runTimers) [...timers.entries()].filter(([, timer]) => timer.at <= now).forEach(([id, timer]) => { timers.delete(id); timer.fn(); });
    },
    focus() { windowListeners.focus(); },
    becomeVisible() { document.hidden = false; documentListeners.visibilitychange(); },
  };
}
function countFor(app, code) {
  const match = app.nodes.summary.innerHTML.match(new RegExp('data-conclusion="' + code + '"><span>[^<]+</span><b class="[^"]*">(\\d+)</b>'));
  assert(match, 'missing summary for ' + code);
  return Number(match[1]);
}
function mainLabels(app) {
  return [...app.nodes.cards.innerHTML.matchAll(/<div class="status"><b>([^<]*)<\/b>/g)].map(m => m[1]);
}

(async () => {
  const app = await boot([row('SWITCH')]);
  assert.deepStrictEqual(mainLabels(app), ['符合影子進場條件']);
  assert.strictEqual(countFor(app, 'eligible'), 1, 'summary must ignore legacy blocked status and stale totals');
  assert(!app.nodes.cards.innerHTML.includes('買進區（參考）'));
  assert(app.nodes.cards.innerHTML.includes('來源交易日 2026-10-09'));
  assert(app.nodes.cards.innerHTML.includes('評估時間 2026-10-09T23:59:00+08:00'));
  assert(app.nodes.cards.innerHTML.includes('絕對到期時間 2026-10-12T13:30:00+08:00'));
  assert(app.nodes.cards.innerHTML.includes('原始買進窗口末日（非結論有效期） 2026-10-12'));
  assert(app.nodes.cards.innerHTML.includes('等待前向驗證'));
  assert(app.nodes.cards.innerHTML.includes('尚未正式採用'));
  assert(app.nodes.cards.innerHTML.includes('分數不是上漲機率'));
  assert(app.nodes.statuses.innerHTML.includes('data-status="insufficient"'));
  assert(!app.nodes.statuses.innerHTML.includes('data-status="candidate"'));

  app.click('horizons', 'horizon', 'medium');
  assert.deepStrictEqual(mainLabels(app), ['暫不進場']);
  assert.strictEqual(countFor(app, 'eligible'), 0);
  assert.strictEqual(countFor(app, 'avoid'), 1);
  assert(app.nodes.cards.innerHTML.includes('買進區（參考）'));
  assert(app.nodes.cards.innerHTML.includes('參考出場價位：目標1 108'));
  assert(app.nodes.cards.innerHTML.includes('中期風險阻擋'));
  assert(!app.nodes.cards.innerHTML.includes('符合影子進場條件'));
  app.click('statuses', 'status', 'eligible');
  assert(app.nodes.cards.innerHTML.includes('沒有符合條件'));
  app.click('statuses', 'status', 'avoid');
  assert.deepStrictEqual(mainLabels(app), ['暫不進場']);
  app.click('horizons', 'horizon', 'long');
  assert(app.nodes.cards.innerHTML.includes('沒有符合條件'));
  app.click('statuses', 'status', 'wait');
  assert.deepStrictEqual(mainLabels(app), ['等待量價確認']);
  app.click('horizons', 'horizon', 'preferred');
  assert(app.nodes.cards.innerHTML.includes('沒有符合條件'));
  app.click('statuses', 'status', 'eligible');
  assert.deepStrictEqual(mainLabels(app), ['符合影子進場條件']);
  app.click('horizons', 'horizon', 'short');
  app.click('horizons', 'horizon', 'short');
  assert.deepStrictEqual(mainLabels(app), ['符合影子進場條件'], 'repeated selection remains stable');

  const invalids = [
    row('MISSING', plan('eligible', {conclusion: undefined})),
    row('EXPIRED', plan('eligible', {conclusion: conclusion('eligible', {expires_at: '2026-10-09T15:59:59Z'})})),
    row('BOUNDARY', plan('eligible', {conclusion: conclusion('eligible', {expires_at: '2026-10-09T16:00:00Z'})})),
    row('NOEXPIRY', plan('eligible', {conclusion: conclusion('eligible', {expires_at: null})})),
    row('INVALID', plan('eligible', {conclusion: conclusion('eligible', {expires_at: 'invalidZ'})})),
    row('NOZONE', plan('eligible', {conclusion: conclusion('eligible', {expires_at: '2026-10-12T13:30:00'})})),
    row('UNKNOWN', plan('eligible', {conclusion: conclusion('__proto__')})),
    row('MISSINGPLAN', plan('eligible'), {plans: {}}),
  ];
  const invalidApp = await boot(invalids, {failValidation: true});
  assert.deepStrictEqual(mainLabels(invalidApp), invalids.map(() => '資料不足'));
  assert.strictEqual(countFor(invalidApp, 'insufficient'), invalids.length);
  assert.strictEqual(countFor(invalidApp, 'eligible'), 0);
  assert(!invalidApp.nodes.cards.innerHTML.includes('符合影子進場條件'));
  assert(invalidApp.nodes.cards.innerHTML.includes('結論已到期'));
  assert(invalidApp.nodes.cards.innerHTML.includes('缺少有效結論'));
  assert(invalidApp.nodes.cards.innerHTML.includes('有效期限尚未驗證'));
  assert(invalidApp.nodes.validationSummary.textContent.includes('尚未正式採用'));
  invalidApp.click('statuses', 'status', 'eligible');
  assert(invalidApp.nodes.cards.innerHTML.includes('沒有符合條件'));
  invalidApp.click('statuses', 'status', 'insufficient');
  assert.strictEqual(mainLabels(invalidApp).length, invalids.length);

  const expiry = NOW + 1000;
  const clockApp = await boot([row('TIMER', plan('eligible', {conclusion: conclusion('eligible', {expires_at: new Date(expiry).toISOString()})}))]);
  clockApp.click('statuses', 'status', 'eligible');
  assert.strictEqual(clockApp.timers.size, 1, 'rerender must replace, not accumulate expiry timers');
  clockApp.advanceTo(expiry - 1);
  assert.deepStrictEqual(mainLabels(clockApp), ['符合影子進場條件']);
  clockApp.advanceTo(expiry);
  assert(clockApp.nodes.cards.innerHTML.includes('沒有符合條件'), 'expiry must update active filters without clicks');
  assert.strictEqual(countFor(clockApp, 'insufficient'), 1);
  clockApp.click('statuses', 'status', 'insufficient');
  assert.deepStrictEqual(mainLabels(clockApp), ['資料不足']);
  assert.strictEqual(clockApp.timers.size, 0);
  for (const event of ['focus', 'becomeVisible']) {
    const resumed = await boot([row('RESUME', plan('eligible', {conclusion: conclusion('eligible', {expires_at: new Date(expiry).toISOString()})}))]);
    resumed.advanceTo(expiry, false);
    resumed[event]();
    assert.deepStrictEqual(mainLabels(resumed), ['資料不足'], event + ' must catch suspended timers');
  }

  const unsafe = '<img src=x onerror="alert(1)">';
  const nullApp = await boot([row('NULLS', plan('avoid', {
    entry_low: null, entry_high: undefined, stop: null, do_not_chase_above: '', target1_pct: null,
    score: null, confidence: undefined, data_quality_pct: null, buy_window_sessions: null, max_hold_sessions: null,
    no_buy_reason: unsafe,
    conclusion: conclusion('avoid', {reasons: [unsafe], gates: [{passed: false, reason: '禁止 <script>oops</script>'}], validation: {label: unsafe}}),
  }), {price: null, name: unsafe, formal_rank: null, risk_blocks: ['風險 "quotes" & <x>']})]);
  const rendered = nullApp.nodes.cards.innerHTML;
  assert(!rendered.includes('<img'));
  assert(!rendered.includes('<script>'));
  assert(rendered.includes('&lt;img src=x onerror=&quot;alert(1)&quot;&gt;'));
  assert(rendered.includes('禁止 &lt;script&gt;oops&lt;/script&gt;'));
  assert(rendered.includes('風險 &quot;quotes&quot; &amp; &lt;x&gt;'));
  assert(rendered.includes('<span>來源快照價格</span><b>—</b>'));
  assert(rendered.includes('<span>買進區（參考）</span><b>— ～ —</b>'));
  assert(rendered.includes('<span>高於這裡不追（參考）</span><b class="warn">—</b>'));
  assert(rendered.includes('<span>停損／失效（參考）</span><b class="bad">—'));
  assert(rendered.includes('模型分數 —｜信心分數 —｜資料品質 —｜正式排名 —'));
  assert(rendered.includes('目標1 108 → 賣 —%'));
  assert(!rendered.includes('模型分數 0'));
  const zeroApp = await boot([row('ZEROS', plan('avoid', {score: 0, confidence: 0, data_quality_pct: 0, entry_low: 0}), {price: 0})]);
  assert(zeroApp.nodes.cards.innerHTML.includes('<span>來源快照價格</span><b>0</b>'), 'real zero must stay zero');
  assert(zeroApp.nodes.cards.innerHTML.includes('模型分數 0.0｜信心分數 0.0｜資料品質 0.0%'));

  const categories = [
    {id: 'technical', label: 'K線與技術指標', applicable: true, status: 'reference', as_of: '2026-10-09', source: 'decision_hub.json',
      note: '沿用既有模型輸入，不另加權。', additional_weight: 0,
      items: [{key: 'close', label: '收盤', value: 100.25}, {key: 'rsi', label: 'RSI', value: 0}, {key: 'ma20', label: '20日均線', value: null}, {key: 'pattern', label: 'K線型態', value: '十字線'}]},
    {id: 'volume', label: '成交量與量比', applicable: true, status: 'reference', as_of: '2026-10-09', source: 'snapshot',
      note: '僅顯示原始量價資料。', additional_weight: 0, items: [{key: 'volume', label: '成交量', value: 125000}, {key: 'volume_ratio', label: '量比', value: 1.25}]},
    {id: 'financial', label: '財報與營運', applicable: false, status: 'not_applicable', as_of: null, source: null,
      note: 'ETF 不適用個股財報。', additional_weight: 0, items: [{key: 'eps', label: 'EPS', value: null}]},
    {id: 'institutional', label: '台股法人與資金流', applicable: false, status: 'not_applicable', as_of: null, source: null,
      note: '美股不適用台股法人資料。', additional_weight: 0, items: []},
    {id: 'macro', label: '總經與事件風險', applicable: true, status: 'missing', as_of: null, source: null,
      note: '尚未提供對應資料。', additional_weight: 0, items: [{key: 'macro_score', label: '總經值', value: null}]},
    {id: 'daily_research', label: '日 KD／MACD 研究', applicable: true, status: 'reference', as_of: '2026-10-09', source: 'isolated_research',
      note: '獨立隔離研究，不影響目前結論。', additional_weight: 0,
      items: [{key: 'k', label: '日K', value: 42.56}, {key: 'd', label: '日D', value: 45.01}, {key: 'macd', label: '日MACD', value: -0.25}]},
    {id: 'weekly_research', label: '週 KD／MACD 研究', applicable: true, status: 'reference', as_of: null, source: null,
      note: '週線可能尚未完成；只供獨立隔離研究。', additional_weight: 0, items: [{key: 'week_k', label: '週K', value: null}]},
  ];
  const evidenceApp = await boot([row('EVIDENCE', plan('avoid'), {market: 'US', asset_type: 'ETF', input_evidence_categories: categories})]);
  const evidenceHtml = evidenceApp.nodes.cards.innerHTML;
  const details = evidenceHtml.match(/<details class="input-evidence reason">([\s\S]*?)<\/details>/)[1];
  assert(details.startsWith('<summary>使用哪些資料（既有模型參考，不另加權）</summary>'));
  assert(!evidenceHtml.includes('<details open'), 'input evidence starts collapsed');
  categories.forEach(category => assert(details.includes('<b>' + category.label + '</b>'), category.label));
  ['收盤：100.25', 'RSI：0', '20日均線：未提供', 'K線型態：十字線', '成交量：125000', '量比：1.25', '日K：42.56', '日D：45.01', '日MACD：-0.25', '週K：未提供'].forEach(text => assert(details.includes(text), text));
  assert(details.includes('data-evidence-id="financial" data-evidence-status="not_applicable"><b>財報與營運</b>｜不適用'));
  assert(details.includes('data-evidence-id="institutional" data-evidence-status="not_applicable"><b>台股法人與資金流</b>｜不適用'));
  assert(details.includes('data-evidence-id="macro" data-evidence-status="missing"><b>總經與事件風險</b>｜未提供'));
  assert(details.includes('資料日期 2026-10-09｜來源 decision_hub.json'));
  assert(details.includes('資料日期 未知｜來源 未知'));
  assert(details.includes('獨立隔離研究，不影響目前結論。'));
  assert(details.includes('週線可能尚未完成；只供獨立隔離研究。'));
  assert.deepStrictEqual(mainLabels(evidenceApp), ['暫不進場'], 'evidence must not override the selected conclusion');
  assert.strictEqual(countFor(evidenceApp, 'avoid'), 1);
  assert.strictEqual(countFor(evidenceApp, 'eligible'), 0);
  assert(!/可買|買進訊號|上漲機率|看多|加分|進場/.test(details), 'evidence presentation must not create bullish interpretation');
  evidenceApp.click('statuses', 'status', 'eligible');
  assert(evidenceApp.nodes.cards.innerHTML.includes('沒有符合條件'));
  evidenceApp.click('statuses', 'status', 'avoid');
  evidenceApp.click('horizons', 'horizon', 'long');
  assert(evidenceApp.nodes.cards.innerHTML.includes('沒有符合條件'));
  evidenceApp.click('statuses', 'status', 'wait');
  assert.deepStrictEqual(mainLabels(evidenceApp), ['等待量價確認']);
  assert(evidenceApp.nodes.cards.innerHTML.includes('日K：42.56'), 'category references survive horizon changes without driving the conclusion');

  const escapedEvidence = await boot([row('ESCAPED_EVIDENCE', plan('avoid'), {input_evidence_categories: [
    {id: 'x" onmouseover="alert(1)', label: unsafe, applicable: true, status: 'reference', as_of: '<time>', source: '<a href="javascript:alert(1)">source</a>',
      note: '<script>note</script>', additional_weight: 0, items: [{key: 'fallback<key>', value: '"quote" & <value>'}, {label: '<label>', value: unsafe}, {label: 'Infinity', value: Infinity}, {label: '空字串', value: ''}]},
    {id: 'false_applicable', label: '不適用優先', applicable: false, status: 'reference', items: [{label: '錯誤輸入', value: 12345}]},
    {id: 'empty_reference', label: '空參考', applicable: true, status: 'reference', items: []},
    {id: 'missing_status', label: '狀態未知', applicable: true, items: []},
  ]})]);
  const escapedDetails = escapedEvidence.nodes.cards.innerHTML.match(/<details class="input-evidence reason">([\s\S]*?)<\/details>/)[1];
  assert(!escapedDetails.includes('<img'));
  assert(!escapedDetails.includes('<script>'));
  assert(!escapedDetails.includes('<a href'));
  assert(escapedDetails.includes('data-evidence-id="x&quot; onmouseover=&quot;alert(1)"'));
  assert(escapedDetails.includes('fallback&lt;key&gt;：&quot;quote&quot; &amp; &lt;value&gt;'));
  assert(escapedDetails.includes('&lt;label&gt;：&lt;img src=x onerror=&quot;alert(1)&quot;&gt;'));
  assert(escapedDetails.includes('資料日期 &lt;time&gt;｜來源 &lt;a href=&quot;javascript:alert(1)&quot;&gt;source&lt;/a&gt;'));
  assert(escapedDetails.includes('&lt;script&gt;note&lt;/script&gt;'));
  assert(escapedDetails.includes('Infinity：未提供'));
  assert(escapedDetails.includes('空字串：未提供'));
  assert(escapedDetails.includes('<b>不適用優先</b>｜不適用'));
  assert(!escapedDetails.includes('12345'));
  assert(escapedDetails.includes('<b>空參考</b>｜既有模型參考<div>未提供</div>'));
  assert(escapedDetails.includes('<b>狀態未知</b>｜未提供'));
  assert(app.nodes.cards.innerHTML.includes('未提供分類資料；無法確認各類輸入。'), 'legacy reports must not imply categories were supplied');

  assert(app.nodes.cards.innerHTML.includes('未提供事前事件時間資料。'));
  assert(app.nodes.validationSummary.textContent.includes('尚未提供配對前向驗證資料'));
  assert(app.nodes.validationSummary.textContent.includes('已登記 — 組｜已完成配對 — 組'));
  assert(app.nodes.validationSummary.textContent.includes('尚無準確率改善結論'));

  const shadowEvents = {
    counts: {input: 7, eligible_events: 2, duplicates: 1, exclusion_reasons: {missing_or_imprecise_publication_time: 3, not_available_at_cutoff: 1}},
    label: '事件時間資料稽核', cutoff: '2026-10-09T15:00:00Z', shadow_only: true, affects_scores: false,
    events: [{event_id: 'DO_NOT_RENDER_EVENT_PAYLOAD', source: 'source', payload: {title: 'DO_NOT_RENDER_EVENT_TITLE'}}],
  };
  const eventApp = await boot([row('EVENTS', plan('avoid'), {shadow_events: shadowEvents})]);
  const eventDetails = eventApp.nodes.cards.innerHTML.match(/<details class="shadow-events reason">([\s\S]*?)<\/details>/)[1];
  assert(eventDetails.startsWith('<summary>事前事件時間檢查（影子診斷）</summary>'));
  assert(eventDetails.includes('事件時間資料稽核'));
  assert(eventDetails.includes('輸入事件 7｜事前時間資料可用 2｜重複 1'));
  assert(eventDetails.includes('檢查截止時間 2026-10-09T15:00:00Z'));
  assert(eventDetails.includes('排除原因：發布時間缺失或僅有日期 3；截至當時尚不可取得 1'));
  assert(eventDetails.includes('不影響分數與結論'));
  assert(!eventDetails.includes('DO_NOT_RENDER'));
  assert(!eventDetails.includes('可評估進場'));
  assert.deepStrictEqual(mainLabels(eventApp), ['暫不進場']);
  assert.strictEqual(countFor(eventApp, 'eligible'), 0);
  eventApp.click('statuses', 'status', 'eligible');
  assert(eventApp.nodes.cards.innerHTML.includes('沒有符合條件'));
  const noTimeEvents = await boot([row('NO_TIME', plan('eligible'), {shadow_events: {...shadowEvents, counts: {input: 2, eligible_events: 0, duplicates: 0, exclusion_reasons: {missing_or_imprecise_publication_time: 2}}}})]);
  assert(noTimeEvents.nodes.cards.innerHTML.includes('事前時間資料可用 0'));
  assert.deepStrictEqual(mainLabels(noTimeEvents), ['符合影子進場條件'], 'diagnostics do not change existing conclusions in either direction');
  const absentConclusionEvents = await boot([row('NO_CONCLUSION', plan('eligible', {conclusion: null}), {shadow_events: shadowEvents})]);
  assert.deepStrictEqual(mainLabels(absentConclusionEvents), ['資料不足'], 'available events cannot rescue absent conclusions');
  const escapedEvents = await boot([row('ESCAPED_EVENTS', plan('avoid'), {shadow_events: {
    ...shadowEvents, label: unsafe, cutoff: '<time onmouseover="bad">',
    counts: {input: null, eligible_events: null, duplicates: null, exclusion_reasons: {'<script>bad</script>': 1}},
  }})]);
  const escapedEventHtml = escapedEvents.nodes.cards.innerHTML.match(/<details class="shadow-events reason">([\s\S]*?)<\/details>/)[1];
  assert(!escapedEventHtml.includes('<img'));
  assert(!escapedEventHtml.includes('<script>'));
  assert(escapedEventHtml.includes('&lt;img src=x onerror=&quot;alert(1)&quot;&gt;'));
  assert(escapedEventHtml.includes('&lt;time onmouseover=&quot;bad&quot;&gt;'));
  assert(escapedEventHtml.includes('&lt;script&gt;bad&lt;/script&gt; 1'));
  assert(escapedEventHtml.includes('輸入事件 —｜事前時間資料可用 —｜重複 —'));

  for (const auditStatus of ['insufficient', 'descriptive_only']) {
    for (const hasValidation of [false, true]) {
      const audit = {
        status: auditStatus, label: auditStatus === 'insufficient' ? '資料不足：尚无可用配對前向驗證' : '前向配對描述統計：尚無改善或晉升結論',
        blocked_reasons: auditStatus === 'insufficient' ? ['no_valid_matched_forward_outcomes', 'cost_assumption_not_configured'] : [],
        registered_pairs: 12, matched_completed_pairs: auditStatus === 'insufficient' ? 0 : 7,
        cohorts: {DO_NOT_RENDER_COHORT_DETAIL: {candidate: {win_rate_pct: 99}}},
        shadow_only: true, affects_formal_v6: false, automatic_promotion: false, accuracy_improvement: null,
      };
      const auditApp = await boot([row('AUDIT', plan('avoid'), {shadow_events: shadowEvents})], {
        ablation: audit,
        validation: hasValidation ? {status: 'ready', summary: {waiting_entry: 1, active: 2, matured: 3}} : {},
      });
      const text = auditApp.nodes.validationSummary.textContent;
      assert(text.includes(audit.label));
      assert(text.includes('已登記 12 組｜已完成配對 ' + audit.matched_completed_pairs + ' 組'));
      assert(text.includes('尚無準確率改善結論'));
      assert(text.includes('不代表正式採用就緒'));
      assert(text.includes('不改正式 V6、不自動晉升'));
      assert(!text.includes('99'));
      assert(!text.includes('DO_NOT_RENDER_COHORT_DETAIL'));
      if (hasValidation) assert(text.includes('等待進場 1｜進行中 2｜已成熟 3'));
      else assert(text.includes('前向驗證尚未建立'));
      if (auditStatus === 'insufficient') {
        assert(text.includes('尚無有效且已完成的前向配對'));
        assert(text.includes('尚未設定交易成本假設'));
      } else assert(text.includes('證據配對前向檢查｜僅描述統計'));
      assert.deepStrictEqual(mainLabels(auditApp), ['暫不進場']);
      assert.strictEqual(countFor(auditApp, 'eligible'), 0);
      auditApp.click('statuses', 'status', 'eligible');
      assert(auditApp.nodes.cards.innerHTML.includes('沒有符合條件'));
    }
  }
  const escapedAudit = await boot([row('ESCAPED_AUDIT', plan('avoid'))], {ablation: {
    status: 'descriptive_only', label: unsafe, blocked_reasons: ['<script>bad</script>'], registered_pairs: null, matched_completed_pairs: null,
    accuracy_improvement: 88.7654321,
  }});
  assert(escapedAudit.nodes.validationSummary.textContent.includes(unsafe), 'textContent preserves the literal label');
  assert(!escapedAudit.nodes.validationSummary.innerHTML.includes('<img'));
  assert(!escapedAudit.nodes.validationSummary.innerHTML.includes('<script>'));
  assert(escapedAudit.nodes.validationSummary.innerHTML.includes('&lt;img'));
  assert(escapedAudit.nodes.validationSummary.innerHTML.includes('&lt;script&gt;bad&lt;/script&gt;'));
  assert(escapedAudit.nodes.validationSummary.textContent.includes('已登記 — 組｜已完成配對 — 組'));
  assert(!escapedAudit.nodes.validationSummary.textContent.includes('88.7654321'), 'never publish an accuracy gain even if an unexpected field contains one');
  assert.deepStrictEqual(mainLabels(escapedAudit), ['暫不進場']);

  const filters = await boot([row('TW_MATCH'), row('US_OTHER', plan('eligible'), {market: 'US'})], {search: '?symbol=TW_MATCH'});
  assert.strictEqual(filters.nodes.search.value, 'TW_MATCH');
  assert.strictEqual(mainLabels(filters).length, 1);
  filters.search('');
  assert.strictEqual(mainLabels(filters).length, 2);
  filters.click('markets', 'market', 'US');
  assert(filters.nodes.cards.innerHTML.includes('US_OTHER'));
  assert(!filters.nodes.cards.innerHTML.includes('TW_MATCH'));
  filters.search('other');
  assert.strictEqual(mainLabels(filters).length, 1);
  filters.search('missing');
  assert(filters.nodes.cards.innerHTML.includes('沒有符合條件'));

  console.log('trade plan shadow behavior passed: safe conclusions, selected horizons, filters, expiry, escaping, nulls, input evidence, event diagnostics, paired audit');
})().catch(error => { console.error(error); process.exitCode = 1; });
