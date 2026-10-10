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
  const nodes = Object.fromEntries(['count', 'cards', 'summary', 'validationSummary', 'prospectiveSummary', 'progressChip', 'markets', 'statuses', 'horizons', 'search', 'refresh'].map(id => [id, new Element()]));
  if(options.cachedWithoutProspective)delete nodes.prospectiveSummary;
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
  const payload = {plans: rows, summary: {candidate: 999, blocked: 999}, validation: {trading_days_collected: 12, target_trading_days: 60}, evidence_ablation: options.ablation, ...options.payload};
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
  const match = app.nodes.summary.innerHTML.match(new RegExp('data-conclusion="' + code + '"><span>[^<]+</span><b class="[^"]*">([^<]+)</b>'));
  assert(match, 'missing summary for ' + code);
  if (code === 'eligible') { assert.strictEqual(match[1], '尚未評估'); return null; }
  return Number(match[1]);
}
function mainLabels(app) {
  return [...app.nodes.cards.innerHTML.matchAll(/<div class="status"><b>([^<]*)<\/b>/g)].map(m => m[1]);
}

(async () => {
  const app = await boot([row('SWITCH')]);
  assert.deepStrictEqual(mainLabels(app), ['原始計畫待後續核對']);
  assert.strictEqual(countFor(app, 'eligible'), null, 'summary must ignore legacy blocked status and stale totals');
  assert(app.nodes.cards.innerHTML.includes('買進區（參考）'));
  assert(app.nodes.cards.innerHTML.includes('來源交易日 2026-10-09'));
  assert(app.nodes.cards.innerHTML.includes('評估時間 2026-10-09T23:59:00+08:00'));
  assert(app.nodes.cards.innerHTML.includes('絕對到期時間 2026-10-12T13:30:00+08:00'));
  assert(app.nodes.cards.innerHTML.includes('原始買進窗口末日（非結論有效期） 2026-10-12'));
  assert(app.nodes.cards.innerHTML.includes('等待前向驗證'));
  assert(app.nodes.cards.innerHTML.includes('尚未正式採用'));
  assert(app.nodes.cards.innerHTML.includes('分數不是上漲機率'));
  assert(app.nodes.statuses.innerHTML.includes('data-status="insufficient"'));
  assert(!app.nodes.statuses.innerHTML.includes('data-status="candidate"'));
  assert(!app.nodes.statuses.innerHTML.includes('data-status="eligible"'));
  assert(app.nodes.count.textContent.includes('影子計畫模式｜尚未評估後續進場'));
  assert(app.nodes.cards.innerHTML.includes('原始快照條件（依據與風險）'));
  assert(!app.nodes.cards.innerHTML.includes('符合影子進場條件'), 'legacy eligible labels cannot claim current eligibility');

  const sameBatchPlan = plan('wait', {
    entry_low: 97, entry_high: 99, do_not_chase_above: 99,
    conclusion: conclusion('wait', {
      mode: 'plan_only', label: '計畫等待條件',
      entry_evaluation: {status: 'not_evaluated', label: '尚未評估後續進場'},
      plan_assessment: {status: 'wait', label: '原始計畫等待條件'},
      gates: [{code: 'price_in_entry_band', passed: false, reason: '原始快照高於計畫買進区'}],
    }),
  });
  const planOnlyApp = await boot([row('SAME_BATCH', sameBatchPlan, {price: 100})], {payload: {
    mode: 'shadow_plan_only', updated_at: '2026-10-09T23:50:00+08:00', evaluated_at: '2026-10-09T23:59:00+08:00',
    entry_evaluation: {status: 'not_evaluated', label: '尚未評估後續進場', candidate_count: null},
    summary: {total: 1, candidate: null, entry_not_evaluated: 1, wait: 1, blocked: 0, insufficient: 0},
  }});
  assert.deepStrictEqual(mainLabels(planOnlyApp), ['計畫等待條件']);
  assert.strictEqual(countFor(planOnlyApp, 'eligible'), null);
  assert.strictEqual(countFor(planOnlyApp, 'wait'), 1);
  assert(planOnlyApp.nodes.summary.innerHTML.includes('<span>後續可進場評估</span><b class="warn">尚未評估</b>'));
  assert(!planOnlyApp.nodes.summary.innerHTML.includes('<span>後續可進場評估</span><b class="warn">0</b>'));
  assert(!planOnlyApp.nodes.cards.innerHTML.includes('class="card candidate"'));
  assert(planOnlyApp.nodes.cards.innerHTML.includes('原始快照用於建立計畫；尚未評估後續進場'));
  assert(planOnlyApp.nodes.cards.innerHTML.includes('報表批次時間 2026-10-09T23:50:00+08:00'));
  assert(planOnlyApp.nodes.cards.innerHTML.includes('評估時間 2026-10-09T23:59:00+08:00'));
  assert(planOnlyApp.nodes.cards.innerHTML.includes('<span>買進區（參考）</span><b>97 ～ 99</b>'));
  assert(planOnlyApp.nodes.cards.innerHTML.includes('買進期限（原始計畫）'));
  assert(!planOnlyApp.nodes.statuses.innerHTML.includes('data-status="eligible"'));
  const beforeCandidateClick = planOnlyApp.nodes.cards.innerHTML;
  planOnlyApp.nodes.statuses.listeners.click({target: {closest: () => ({dataset: {status: 'eligible'}})}});
  assert.strictEqual(planOnlyApp.nodes.cards.innerHTML, beforeCandidateClick, 'synthetic old candidate controls cannot reactivate entry filtering');
  planOnlyApp.click('horizons', 'horizon', 'medium');
  assert.deepStrictEqual(mainLabels(planOnlyApp), ['原始計畫風險阻擋']);
  planOnlyApp.click('horizons', 'horizon', 'short');
  assert.deepStrictEqual(mainLabels(planOnlyApp), ['計畫等待條件']);
  planOnlyApp.advanceTo(Date.parse('2026-10-12T13:30:00+08:00'));
  assert.deepStrictEqual(mainLabels(planOnlyApp), ['資料需更新']);
  assert.strictEqual(countFor(planOnlyApp, 'eligible'), null, 'expiry is not a zero-opportunity inference');
  const legacyClaim = await boot([row('LEGACY_CLAIM', plan('eligible', {
    conclusion: conclusion('eligible', {label: '現在可進場', evaluated_at: null, entry_evaluation: {status: 'evaluated'}, plan_assessment: {status: 'eligible'}}),
  }))], {payload: {updated_at: '<img src=x>', evaluated_at: '<script>time</script>'}});
  assert.deepStrictEqual(mainLabels(legacyClaim), ['原始計畫待後續核對']);
  assert(!legacyClaim.nodes.cards.innerHTML.includes('現在可進場'));
  assert(!legacyClaim.nodes.cards.innerHTML.includes('<img'));
  assert(!legacyClaim.nodes.cards.innerHTML.includes('<script>'));
  assert(legacyClaim.nodes.cards.innerHTML.includes('報表批次時間 &lt;img src=x&gt;'));
  assert(legacyClaim.nodes.cards.innerHTML.includes('評估時間 &lt;script&gt;time&lt;/script&gt;'));
  assert.strictEqual(countFor(legacyClaim, 'eligible'), null);

  app.click('horizons', 'horizon', 'medium');
  assert.deepStrictEqual(mainLabels(app), ['原始計畫風險阻擋']);
  assert.strictEqual(countFor(app, 'eligible'), null);
  assert.strictEqual(countFor(app, 'avoid'), 1);
  assert(app.nodes.cards.innerHTML.includes('買進區（參考）'));
  assert(app.nodes.cards.innerHTML.includes('參考出場價位：目標1 108'));
  assert(app.nodes.cards.innerHTML.includes('中期風險阻擋'));
  assert(!app.nodes.cards.innerHTML.includes('原始計畫待後續核對'));
  app.click('statuses', 'status', 'wait');
  assert(app.nodes.cards.innerHTML.includes('沒有符合條件'));
  app.click('statuses', 'status', 'avoid');
  assert.deepStrictEqual(mainLabels(app), ['原始計畫風險阻擋']);
  app.click('horizons', 'horizon', 'long');
  assert(app.nodes.cards.innerHTML.includes('沒有符合條件'));
  app.click('statuses', 'status', 'wait');
  assert.deepStrictEqual(mainLabels(app), ['計畫等待條件']);
  app.click('horizons', 'horizon', 'preferred');
  assert.deepStrictEqual(mainLabels(app), ['原始計畫待後續核對']);
  app.click('statuses', 'status', 'wait');
  assert.deepStrictEqual(mainLabels(app), ['原始計畫待後續核對']);
  app.click('horizons', 'horizon', 'short');
  app.click('horizons', 'horizon', 'short');
  assert.deepStrictEqual(mainLabels(app), ['原始計畫待後續核對'], 'repeated selection remains stable');

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
  assert.deepStrictEqual(mainLabels(invalidApp), ['待核對', '資料需更新', '資料需更新', '待核對', '待核對', '待核對', '待核對', '待核對']);
  assert.strictEqual(countFor(invalidApp, 'insufficient'), invalids.length);
  assert.strictEqual(countFor(invalidApp, 'eligible'), null);
  assert(!invalidApp.nodes.cards.innerHTML.includes('原始計畫待後續核對'));
  assert(invalidApp.nodes.cards.innerHTML.includes('結論已到期'));
  assert(invalidApp.nodes.cards.innerHTML.includes('缺少有效結論'));
  assert(invalidApp.nodes.cards.innerHTML.includes('有效期限尚未驗證'));
  assert(invalidApp.nodes.validationSummary.textContent.includes('尚未正式採用'));
  invalidApp.click('statuses', 'status', 'wait');
  assert(invalidApp.nodes.cards.innerHTML.includes('沒有符合條件'));
  invalidApp.click('statuses', 'status', 'insufficient');
  assert.strictEqual(mainLabels(invalidApp).length, invalids.length);

  const dataStates = [
    ['market_not_closed', '市場未收盤', '當日市場尚未收盤，待完整日K確認。'],
    ['source_attestation_pending', '來源完整性待驗證', '已有行情資料，來源完整性仍待驗證。'],
    ['price_basis_mismatch', '價格基準待核對', '行情快照與已完成日K收盤不一致。'],
    ['stale_snapshot', '資料需更新', '來源快照已過期。'],
    ['evidence_review_pending', '證據時間待核對', '消息證據的可用時間仍待核對。'],
    ['data_missing', '關鍵資料缺漏', '必要價格欄位未提供。'],
    ['validation_pending', '研究待驗證', '研究驗證仍在累積，不表示行情資料缺漏。'],
  ];
  const statusRows = dataStates.map(([code, label, detail]) => row(code, plan('insufficient', {
    conclusion: conclusion('insufficient', {expires_at: null, data_status: {code, label, detail}}),
  })));
  const dataApp = await boot(statusRows.concat([
    row('ACTUAL_AVOID', plan('avoid')), row('ACTUAL_WAIT', plan('wait')),
    row('READY', plan('eligible', {conclusion: conclusion('eligible', {data_status: {code: 'ready', label: '來源已核對', detail: '來源驗證已完成。'}, gates: [{code: 'source', passed: true}]})})),
  ]));
  assert.deepStrictEqual(mainLabels(dataApp), dataStates.map(item => item[1]).concat(['原始計畫風險阻擋', '計畫等待條件', '原始計畫待後續核對']));
  dataStates.forEach(([code, label, detail]) => {
    assert(dataApp.nodes.cards.innerHTML.includes('data-source-status="' + code + '"'));
    assert(dataApp.nodes.cards.innerHTML.includes('資料狀態：' + label));
    assert(dataApp.nodes.cards.innerHTML.includes(detail));
  });
  assert.strictEqual(countFor(dataApp, 'insufficient'), dataStates.length);
  assert.strictEqual(countFor(dataApp, 'avoid'), 1, 'only an actual avoid conclusion contributes to avoid');
  assert.strictEqual(countFor(dataApp, 'wait'), 2);
  assert.strictEqual(countFor(dataApp, 'eligible'), null);
  assert(dataApp.nodes.summary.innerHTML.includes('data-conclusion="insufficient"><span>待核對</span><b class="warn">7</b>'));
  assert(dataApp.nodes.statuses.innerHTML.includes('data-status="insufficient">待核對</button>'));
  assert.strictEqual((dataApp.nodes.cards.innerHTML.match(/class="card blocked"/g) || []).length, 1, 'unverified data must not get the red avoid card styling');
  assert.strictEqual((dataApp.nodes.cards.innerHTML.match(/class="card wait" data-conclusion="insufficient"/g) || []).length, dataStates.length);
  assert(dataApp.nodes.cards.innerHTML.includes('完成資料核對前，不提供可進場判定；下列價位僅供參考。'));
  dataApp.click('statuses', 'status', 'avoid');
  assert.deepStrictEqual(mainLabels(dataApp), ['原始計畫風險阻擋']);
  dataApp.click('statuses', 'status', 'insufficient');
  assert.deepStrictEqual(mainLabels(dataApp), dataStates.map(item => item[1]));
  dataApp.click('statuses', 'status', 'wait');
  assert.deepStrictEqual(mainLabels(dataApp), ['計畫等待條件', '原始計畫待後續核對']);

  const unverifiedCandle = {id: 'daily_candle', label: '每日K線', applicable: true, status: 'unverified', as_of: '2026-10-09', source: 'reported_candle',
    note: '已有 O/H/L/C 參考資料；完整性尚未驗證，成交量與衍生指標待核對。', items: [
      {label: '開盤', value: 100}, {label: '最高', value: 102}, {label: '最低', value: 99}, {label: '收盤', value: 101},
      {label: '成交量', value: null}, {label: 'K線衍生指標', value: null},
    ]};
  const basisApp = await boot([row('BASIS', plan('insufficient', {conclusion: conclusion('insufficient', {
    expires_at: null,
    data_status: {code: 'price_basis_mismatch', label: '價格基準待核對', detail: '來源價格須先核對。'},
    price_basis: {reported_quote: 103.5, completed_close: 101, aligned: false, note: '行情快照與已完成日K分開顯示，尚未對齊。'},
  })}), {price: 103.5, input_evidence_categories: [unverifiedCandle]})]);
  const basisHtml = basisApp.nodes.cards.innerHTML;
  assert.deepStrictEqual(mainLabels(basisApp), ['價格基準待核對']);
  assert(basisHtml.includes('<span>來源快照價格（待對齊）</span><b>103.5</b>'));
  assert(basisHtml.includes('<span>已完成日K收盤（參考）</span><b>101</b>'));
  assert(basisHtml.includes('行情快照與已完成日K分開顯示，尚未對齊。'));
  assert(basisHtml.includes('data-evidence-id="daily_candle" data-evidence-status="unverified"><b>每日K線</b>｜尚未驗證'));
  ['開盤：100', '最高：102', '最低：99', '收盤：101', '成交量：未提供', 'K線衍生指標：未提供'].forEach(text => assert(basisHtml.includes(text), text));
  assert(!basisHtml.includes('成交量：0'));
  assert(!basisHtml.includes('資料不存在'));
  assert(basisHtml.includes('買進區（參考）'));

  const inconsistent = await boot([
    row('FAILED_GATE', plan('eligible', {conclusion: conclusion('eligible', {gates: [{code: 'source', passed: false, reason: '來源待核對'}]})})),
    row('PENDING_SOURCE', plan('eligible', {conclusion: conclusion('eligible', {data_status: {code: 'source_attestation_pending', label: '來源完整性待驗證'}})})),
    row('LEGACY_INSUFFICIENT', plan('insufficient')),
  ]);
  assert.deepStrictEqual(mainLabels(inconsistent), ['待核對', '來源完整性待驗證', '待核對']);
  assert.strictEqual(countFor(inconsistent, 'eligible'), null, 'copy changes cannot bypass failed gates or pending attestations');
  assert(!inconsistent.nodes.cards.innerHTML.includes('原始計畫待後續核對'));
  const statusAttack = '<img src=x onerror="alert(1)">';
  const escapedStatus = await boot([row('ESCAPED_STATUS', plan('insufficient', {conclusion: conclusion('insufficient', {
    data_status: {code: 'source_attestation_pending', label: statusAttack, detail: '<script>details</script>'},
    price_basis: {reported_quote: null, completed_close: null, aligned: false, note: '<script>basis</script>'},
  })}))]);
  assert(!escapedStatus.nodes.cards.innerHTML.includes('<img'));
  assert(!escapedStatus.nodes.cards.innerHTML.includes('<script>'));
  assert(escapedStatus.nodes.cards.innerHTML.includes('&lt;img src=x onerror=&quot;alert(1)&quot;&gt;'));
  assert(escapedStatus.nodes.cards.innerHTML.includes('&lt;script&gt;details&lt;/script&gt;'));
  assert(escapedStatus.nodes.cards.innerHTML.includes('&lt;script&gt;basis&lt;/script&gt;'));
  assert(escapedStatus.nodes.cards.innerHTML.includes('<span>已完成日K收盤（參考）</span><b>—</b>'));
  const expiredStatus = await boot([row('EXPIRED_STATUS', plan('eligible', {conclusion: conclusion('eligible', {
    expires_at: '2026-10-09T15:59:59Z', data_status: {code: 'ready', label: '來源已核對', detail: '先前來源驗證已完成。'},
  })}))]);
  assert.deepStrictEqual(mainLabels(expiredStatus), ['資料需更新']);
  assert(expiredStatus.nodes.cards.innerHTML.includes('data-source-status="stale_snapshot"'));
  assert(!expiredStatus.nodes.cards.innerHTML.includes('先前來源驗證已完成'));

  const expiry = NOW + 1000;
  const clockApp = await boot([row('TIMER', plan('eligible', {conclusion: conclusion('eligible', {expires_at: new Date(expiry).toISOString()})}))]);
  clockApp.click('statuses', 'status', 'wait');
  assert.strictEqual(clockApp.timers.size, 1, 'rerender must replace, not accumulate expiry timers');
  clockApp.advanceTo(expiry - 1);
  assert.deepStrictEqual(mainLabels(clockApp), ['原始計畫待後續核對']);
  clockApp.advanceTo(expiry);
  assert(clockApp.nodes.cards.innerHTML.includes('沒有符合條件'), 'expiry must update active filters without clicks');
  assert.strictEqual(countFor(clockApp, 'insufficient'), 1);
  clockApp.click('statuses', 'status', 'insufficient');
  assert.deepStrictEqual(mainLabels(clockApp), ['資料需更新']);
  assert.strictEqual(clockApp.timers.size, 0);
  for (const event of ['focus', 'becomeVisible']) {
    const resumed = await boot([row('RESUME', plan('eligible', {conclusion: conclusion('eligible', {expires_at: new Date(expiry).toISOString()})}))]);
    resumed.advanceTo(expiry, false);
    resumed[event]();
    assert.deepStrictEqual(mainLabels(resumed), ['資料需更新'], event + ' must catch suspended timers');
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
  assert.deepStrictEqual(mainLabels(evidenceApp), ['原始計畫風險阻擋'], 'evidence must not override the selected conclusion');
  assert.strictEqual(countFor(evidenceApp, 'avoid'), 1);
  assert.strictEqual(countFor(evidenceApp, 'eligible'), null);
  assert(!/可買|買進訊號|上漲機率|看多|加分|進場/.test(details), 'evidence presentation must not create bullish interpretation');
  evidenceApp.click('statuses', 'status', 'wait');
  assert(evidenceApp.nodes.cards.innerHTML.includes('沒有符合條件'));
  evidenceApp.click('statuses', 'status', 'avoid');
  evidenceApp.click('horizons', 'horizon', 'long');
  assert(evidenceApp.nodes.cards.innerHTML.includes('沒有符合條件'));
  evidenceApp.click('statuses', 'status', 'wait');
  assert.deepStrictEqual(mainLabels(evidenceApp), ['計畫等待條件']);
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
  assert.deepStrictEqual(mainLabels(eventApp), ['原始計畫風險阻擋']);
  assert.strictEqual(countFor(eventApp, 'eligible'), null);
  eventApp.click('statuses', 'status', 'wait');
  assert(eventApp.nodes.cards.innerHTML.includes('沒有符合條件'));
  const noTimeEvents = await boot([row('NO_TIME', plan('eligible'), {shadow_events: {...shadowEvents, counts: {input: 2, eligible_events: 0, duplicates: 0, exclusion_reasons: {missing_or_imprecise_publication_time: 2}}}})]);
  assert(noTimeEvents.nodes.cards.innerHTML.includes('事前時間資料可用 0'));
  assert.deepStrictEqual(mainLabels(noTimeEvents), ['原始計畫待後續核對'], 'diagnostics do not change existing conclusions in either direction');
  const absentConclusionEvents = await boot([row('NO_CONCLUSION', plan('eligible', {conclusion: null}), {shadow_events: shadowEvents})]);
  assert.deepStrictEqual(mainLabels(absentConclusionEvents), ['待核對'], 'available events cannot rescue absent conclusions');
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
      assert.deepStrictEqual(mainLabels(auditApp), ['原始計畫風險阻擋']);
      assert.strictEqual(countFor(auditApp, 'eligible'), null);
      auditApp.click('statuses', 'status', 'wait');
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
  assert.deepStrictEqual(mainLabels(escapedAudit), ['原始計畫風險阻擋']);

  assert(app.nodes.prospectiveSummary.textContent.includes('本報表尚未提供登錄紀錄'));
  assert(!app.nodes.cards.innerHTML.includes('class="prospective-entry'));
  const prospectiveRegistry = {
    status: 'ready', label: '台股凍結計畫前瞻觀察',
    summary: {registered_total: 21, status_counts: {enrolled_pending: 10, observed_wait: 5, triggered_close_only: 3, invalidated: 1, expired: 1, quarantined: 1}, next_observation_session: '2026-10-12'},
    raw_provider_records: [{value: 'DO_NOT_PUBLISH_RAW_REGISTRY'}], probability_pct: 99,
  };
  const prospectiveEntry = {
    horizon: 'short', status: 'enrolled_pending', registered_at: '2026-10-09T14:00:00Z', valid_through_session: '2026-10-14',
    last_reason: 'registered_now_awaiting_later_close', latest_evaluated_at: '2026-10-09T15:00:00Z', next_observation_session: '2026-10-12', evaluation_expires_at: '2026-10-15T13:30:00+08:00',
    plan_id: 'frozen-id', frozen_levels: {entry_low: 91, entry_high: 93, stop: 88, target1: 104, target2: null},
    evaluations: [{payload: 'DO_NOT_PUBLISH_RAW_ENTRY'}],
  };
  const prospectRows = [row('TW_PROSPECT', plan('wait'), {prospective_entries: [
    {...prospectiveEntry, registered_at: '2026-10-08T14:00:00Z', status: 'triggered_close_only', frozen_levels: {entry_low: 81, entry_high: 83, stop: 78}},
    {...prospectiveEntry, horizon: 'medium', registered_at: '2026-10-09T15:00:00Z', status: 'observed_wait', last_reason: 'close_outside_frozen_entry_range', frozen_levels: {entry_low: 85, entry_high: 87, stop: 80, target1: 102, target2: 108}},
    prospectiveEntry,
  ]}), row('US_NO_PROSPECT', plan('wait'), {market: 'US', prospective_entries: [prospectiveEntry]})];
  const prospectApp = await boot(prospectRows, {payload: {tw_prospective_registry: prospectiveRegistry}});
  const prospectiveText = prospectApp.nodes.prospectiveSummary.textContent;
  assert(prospectiveText.includes('已登錄 21 筆歷史研究計畫'));
  assert(prospectiveText.includes('待正式收盤 10｜等待後續條件 5｜僅收盤條件研究紀錄 3｜失效 1｜到期 1｜隔離 1'));
  assert(prospectiveText.includes('下一觀察交易日 2026-10-12'));
  assert(prospectiveText.includes('不是可買數，沒有預測成效結論'));
  assert(!prospectiveText.includes('99'));
  assert(!prospectiveText.includes('DO_NOT_PUBLISH_RAW_REGISTRY'));
  assert.strictEqual((prospectApp.nodes.cards.innerHTML.match(/class="prospective-entry sell"/g)||[]).length, 1, 'TW registry must not be presented for US rows');
  const prospectHtml = prospectApp.nodes.cards.innerHTML;
  assert(prospectHtml.includes('已登錄，等待後續正式收盤'));
  assert(prospectHtml.includes('所選週期最新登錄的歷史研究紀錄｜此週期共 2 筆'));
  assert(prospectHtml.includes('登錄時間 2026-10-09T14:00:00Z'));
  assert(!prospectHtml.includes('登錄時間 2026-10-08T14:00:00Z'));
  assert(prospectHtml.includes('凍結窗口末日 2026-10-14'));
  assert(prospectHtml.includes('觀察判定到期時間 2026-10-15T13:30:00+08:00'));
  assert(prospectHtml.includes('最新研究核對時間 2026-10-09T15:00:00Z'));
  assert(prospectHtml.includes('凍結原始買區 91 ～ 93｜凍結停損 88'));
  assert(prospectHtml.includes('凍結目標1 104｜凍結目標2 —'));
  assert(prospectHtml.includes('<span>買進區（參考）</span><b>98 ～ 101</b>'), 'current plan remains separate from frozen registry levels');
  assert(!prospectHtml.includes('凍結原始買區 98 ～ 101'));
  assert(!prospectHtml.includes('DO_NOT_PUBLISH_RAW_ENTRY'));
  assert.strictEqual(countFor(prospectApp, 'eligible'), null);
  assert.deepStrictEqual(mainLabels(prospectApp), ['計畫等待條件', '計畫等待條件']);
  prospectApp.click('horizons', 'horizon', 'medium');
  assert(prospectApp.nodes.cards.innerHTML.includes('後續收盤尚未進入原買區'));
  assert(prospectApp.nodes.cards.innerHTML.includes('凍結原始買區 85 ～ 87'));
  assert(!prospectApp.nodes.cards.innerHTML.includes('凍結原始買區 91 ～ 93'));
  prospectApp.click('horizons', 'horizon', 'long');
  assert(prospectApp.nodes.cards.innerHTML.includes('本報表未提供此週期的登錄紀錄'));
  prospectApp.click('horizons', 'horizon', 'preferred');
  assert(prospectApp.nodes.cards.innerHTML.includes('凍結原始買區 91 ～ 93'));

  const prospectiveStatusLabels = {
    enrolled_pending: '已登錄，等待後續正式收盤', observed_wait: '後續收盤尚未進入原買區',
    triggered_close_only: '後續收盤符合原買區（研究觀察，非買進建議）', invalidated: '原凍結計畫已失效',
    expired: '原凍結計畫窗口已到期', quarantined: '紀錄已隔離，等待來源或風險核對',
  };
  for (const [status, label] of Object.entries(prospectiveStatusLabels)) {
    const statusApp = await boot([row('REGISTRY_STATUS', plan('avoid'), {prospective_entries: [{...prospectiveEntry, status}]})], {payload: {tw_prospective_registry: prospectiveRegistry}});
    assert(statusApp.nodes.cards.innerHTML.includes(label), status);
    assert(statusApp.nodes.cards.innerHTML.includes('僅正式收盤研究觀察，不代表盤中觸價、可成交或買進建議'));
    if(['triggered_close_only', 'invalidated', 'expired'].includes(status))assert(statusApp.nodes.cards.innerHTML.includes('下一觀察交易日 此紀錄已結束'));
    assert.deepStrictEqual(mainLabels(statusApp), ['原始計畫風險阻擋'], 'registry status cannot overwrite original plan assessment');
    assert.strictEqual(countFor(statusApp, 'eligible'), null);
    assert(!statusApp.nodes.statuses.innerHTML.includes('data-status="eligible"'));
  }
  const pendingNews = await boot([row('NEWS_WAIT', plan('wait'), {prospective_entries: [{...prospectiveEntry, status: 'observed_wait', last_reason: 'news_refresh_required'}]})], {payload: {tw_prospective_registry: prospectiveRegistry}});
  assert(pendingNews.nodes.cards.innerHTML.includes('後續觀察等待消息更新'));
  assert(!pendingNews.nodes.cards.innerHTML.includes('後續收盤尚未進入原買區'), 'pending news must not invent an outside-band price observation');
  const unavailableRegistry = await boot([row('UNAVAILABLE', plan('wait'), {prospective_entries: [{...prospectiveEntry, status: 'triggered_close_only'}]})], {payload: {tw_prospective_registry: {status: 'unavailable', label: '<img src=x>', summary: {registered_total: 0}}}});
  assert(unavailableRegistry.nodes.prospectiveSummary.textContent.includes('研究紀錄不可用'));
  assert(!unavailableRegistry.nodes.prospectiveSummary.innerHTML.includes('<img'));
  assert(!unavailableRegistry.nodes.prospectiveSummary.textContent.includes('已登錄 0'));
  assert(unavailableRegistry.nodes.cards.innerHTML.includes('暫不顯示紀錄判定'));
  assert(!unavailableRegistry.nodes.cards.innerHTML.includes('後續收盤符合原買區'));
  const escapedProspect = await boot([row('ESCAPED_PROSPECT', plan('wait'), {prospective_entries: [{
    ...prospectiveEntry, status: '<img src=x>', registered_at: '<script>registered</script>', valid_through_session: '<time>end</time>',
    latest_evaluated_at: '<script>evaluated</script>', next_observation_session: '<img src=x>', last_reason: '<script>reason</script>', frozen_levels: null,
  }]})], {payload: {tw_prospective_registry: {...prospectiveRegistry, summary: {registered_total: null, status_counts: {}, next_observation_session: '<script>next</script>'}}}});
  assert(!escapedProspect.nodes.cards.innerHTML.includes('<script>'));
  assert(!escapedProspect.nodes.cards.innerHTML.includes('<img'));
  assert(escapedProspect.nodes.cards.innerHTML.includes('登錄時間待核對'));
  assert(escapedProspect.nodes.cards.innerHTML.includes('&lt;script&gt;reason&lt;/script&gt;'));
  assert(escapedProspect.nodes.cards.innerHTML.includes('凍結原始買區 — ～ —'));
  assert(escapedProspect.nodes.prospectiveSummary.textContent.includes('已登錄 —'));
  assert(!escapedProspect.nodes.prospectiveSummary.innerHTML.includes('<script>'));
  const cachedProspect = await boot([row('CACHED_PROSPECT', plan('wait'), {prospective_entries: [prospectiveEntry]})], {cachedWithoutProspective: true, payload: {tw_prospective_registry: prospectiveRegistry}});
  assert(cachedProspect.nodes.cards.innerHTML.includes('凍結原始買區 91 ～ 93'), 'cached HTML without global panel must still render cards');
  assert.strictEqual(countFor(cachedProspect, 'eligible'), null);

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

  console.log('trade plan shadow behavior passed: safe conclusions, selected horizons, filters, expiry, escaping, nulls, input evidence, event diagnostics, paired audit, data status, price basis, prospective registry');
})().catch(error => { console.error(error); process.exitCode = 1; });
