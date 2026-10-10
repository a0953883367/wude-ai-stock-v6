const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'private-research.html'), 'utf8');
const source = fs.readFileSync(path.join(root, 'private_research.js'), 'utf8');
const liveConfig = fs.readFileSync(path.join(root, 'live_config.js'), 'utf8');
const allowedApiBase = 'https://wude-ai-stock-v6-production.up.railway.app';

class Element {
  constructor(tag = 'div') {
    this.tagName = tag;
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.value = '';
    this.disabled = true;
    this.hidden = false;
    this._text = '';
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set innerHTML(_) { throw new Error('HTML injection is forbidden'); }
  appendChild(child) { this.children.push(child); return child; }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  setAttribute(name, value) { this.attributes[name] = value; }
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

function boot(options = {}) {
  const ids = ['researchForm', 'researchSymbol', 'researchSubmit', 'requestStatus', 'researchResult'];
  const nodes = Object.fromEntries(ids.map(id => [id, new Element()]));
  nodes.researchResult.hidden = true;
  const documentEvents = {}, windowEvents = {}, calls = [], storageReads = [];
  let writes = 0;
  const storage = options.storage || {'wude-live-access-token': 'existing-device-token'};
  const forbidden = () => { throw new Error('Unexpected background activity'); };
  const document = {
    hidden: false,
    getElementById: id => nodes[id],
    createElement: tag => new Element(tag),
    addEventListener: (name, fn) => { documentEvents[name] = fn; },
  };
  const window = {
    WUDE_LIVE_API_BASE: options.apiBase === undefined ? allowedApiBase + '/' : options.apiBase,
    localStorage: {
      getItem(key) {
        storageReads.push(key);
        if (options.storageError) throw new Error('Storage denied');
        return storage[key] || null;
      },
      setItem() { writes++; forbidden(); },
      removeItem() { writes++; forbidden(); },
      clear() { writes++; forbidden(); },
    },
    sessionStorage: {getItem: forbidden, setItem: forbidden},
    fetch(url, init) {
      const response = deferred();
      calls.push({url, init, ...response});
      return response.promise;
    },
    addEventListener: (name, fn) => { windowEvents[name] = fn; },
    setInterval: forbidden,
    setTimeout: forbidden,
    open: forbidden,
  };
  const sandbox = {
    document, window, AbortController, setTimeout: forbidden, setInterval: forbidden,
    console: {log: forbidden, warn: forbidden, error: forbidden},
    navigator: {sendBeacon: forbidden},
  };
  const context = vm.createContext(sandbox);
  if (options.withLiveConfig) vm.runInContext(liveConfig, context);
  vm.runInContext(source, context);
  return {
    nodes, calls, storageReads, storage,
    writes: () => writes,
    submit(symbol = 'aapl') {
      nodes.researchSymbol.value = symbol;
      let prevented = false;
      const promise = nodes.researchForm.listeners.submit({preventDefault() { prevented = true; }});
      assert(prevented, 'form submission must never navigate or put a symbol in the URL');
      return promise;
    },
    respond(index, payload, overrides = {}) {
      calls[index].resolve({ok: true, json: async () => payload, ...overrides});
    },
    hide() { document.hidden = true; documentEvents.visibilitychange(); },
    show() { document.hidden = false; documentEvents.visibilitychange(); },
    pagehide() { windowEvents.pagehide(); },
    pageshow() { windowEvents.pageshow(); },
  };
}

function payload(overrides = {}) {
  return {
    ok: true,
    data: {
      symbol: 'AAPL', status: 'projected', decision_eligible: false,
      features: {close: 200, ma5: 199, ma10: 198, ma20: 197, ma60: 190, rsi14_sma: 55, atr14_true_range_sma: 4},
      plan: {entry_low: 195, entry_high: 198, stop: 190, target1: 215, target2: 225, horizon_sessions: 5, execution_eligible: false},
      provenance: {source: 'Alpaca SIP historical daily bars', session_date: '2026-10-09', observed_at: '2026-10-10T09:00:00Z', adjustment: 'split', historical_point_in_time: false},
      reasons: ['unvalidated_research'],
      ...overrides,
    },
  };
}

function assertEmpty(app) {
  assert.strictEqual(app.nodes.researchResult.textContent, '');
  assert.strictEqual(app.nodes.researchResult.children.length, 0);
  assert.strictEqual(app.nodes.researchResult.hidden, true);
}

async function run() {
  for (const text of ['私人研究，未經驗證；不是買進指示，不可下單', 'decision-hub.html', 'no-referrer', 'noindex, nofollow, noarchive']) {
    assert(html.includes(text), 'missing safety text: ' + text);
  }
  assert.strictEqual((html.match(/<input\b/g) || []).length, 1);
  assert.strictEqual((html.match(/<button\b/g) || []).length, 1);
  assert(/id="researchSubmit"[^>]*disabled/.test(html));
  assert(/id="researchSymbol"[^>]*disabled/.test(html));
  assert.deepStrictEqual([...html.matchAll(/<script src="([^"]+)"/g)].map(match => match[1]), ['live_config.js', 'private_research.js']);
  assert(!/https?:\/\//.test(html), 'page must not add external analytics or remote assets');
  assert(!/innerHTML|insertAdjacentHTML|document\.write|setItem|sessionStorage|setInterval|console\./.test(source));
  assert(!/reports\/|stock_data|Yahoo|formal_rank|Notification|download\s*=/.test(source));

  // No loading, token read, or requests before an explicit form submission.
  const idle = boot();
  assert.strictEqual(idle.calls.length, 0);
  assert.deepStrictEqual(idle.storageReads, []);
  assert.strictEqual(idle.nodes.researchSubmit.disabled, false);
  idle.pageshow(); idle.hide(); idle.show();
  assert.strictEqual(idle.calls.length, 0);
  assertEmpty(idle);

  // Existing config's fetch wrapper must leave the private endpoint untouched.
  const app = boot({withLiveConfig: true});
  const first = app.submit(' aapl ');
  assert.strictEqual(app.calls.length, 1);
  const request = app.calls[0];
  assert.strictEqual(request.url, allowedApiBase + '/api/private/us-research');
  assert.strictEqual(request.init.method, 'POST');
  assert.deepStrictEqual(JSON.parse(request.init.body), {symbol: 'AAPL'});
  assert.strictEqual(request.init.headers['X-Live-Token'], 'existing-device-token');
  assert.strictEqual(request.init.headers['Content-Type'], 'application/json');
  assert.strictEqual(request.init.credentials, 'omit');
  assert.strictEqual(request.init.cache, 'no-store');
  assert.strictEqual(request.init.redirect, 'error');
  assert.strictEqual(request.init.referrerPolicy, 'no-referrer');
  assert(request.init.signal instanceof AbortSignal);
  assert.strictEqual(app.nodes.researchSubmit.disabled, true);
  assert.strictEqual(app.nodes.researchSymbol.disabled, true);
  await app.submit('MSFT');
  assert.strictEqual(app.calls.length, 1, 'duplicate submissions while pending must be ignored');
  const data = payload();
  const original = JSON.stringify(data);
  app.respond(0, data);
  await first;
  assert.strictEqual(JSON.stringify(data), original, 'rendering must not change server research data');
  assert.strictEqual(app.nodes.researchSubmit.disabled, false);
  assert.strictEqual(app.nodes.researchResult.hidden, false);
  assert(app.nodes.researchResult.textContent.includes('歷史研究指標'));
  assert(app.nodes.researchResult.textContent.includes('未驗證價位幾何（僅供研究）'));
  assert(app.nodes.researchResult.textContent.includes('2026-10-09'));
  assert(app.nodes.researchResult.textContent.includes('私人研究，未經驗證；不是買進指示，不可下單'));
  assert(!app.nodes.researchResult.textContent.includes('existing-device-token'));
  assert.strictEqual(app.writes(), 0);

  // A new query clears all previous research immediately; API error content is never reflected.
  const second = app.submit('AAPL');
  assertEmpty(app);
  app.respond(1, {error: 'private server details'}, {ok: false, status: 403});
  await second;
  assertEmpty(app);
  assert.strictEqual(app.nodes.requestStatus.textContent, '目前無法讀取私人研究，請稍後手動重試。');
  assert(!app.nodes.requestStatus.textContent.includes('private server details'));

  // The fallback key is read-only, and the primary key takes precedence.
  const fallback = boot({storage: {wude_live_token: 'legacy-device-token'}});
  const fallbackRun = fallback.submit();
  assert.strictEqual(fallback.calls[0].init.headers['X-Live-Token'], 'legacy-device-token');
  fallback.respond(0, payload()); await fallbackRun;
  assert.strictEqual(fallback.writes(), 0);
  const primary = boot({storage: {'wude-live-access-token': 'primary', wude_live_token: 'fallback'}});
  const primaryRun = primary.submit();
  assert.strictEqual(primary.calls[0].init.headers['X-Live-Token'], 'primary');
  primary.respond(0, payload()); await primaryRun;
  assert.deepStrictEqual(primary.storageReads, ['wude-live-access-token']);

  for (const settings of [{storage: {}}, {storageError: true}, {apiBase: ''}]) {
    const denied = boot(settings);
    await denied.submit();
    assert.strictEqual(denied.calls.length, 0);
    assert.strictEqual(denied.writes(), 0);
    assertEmpty(denied);
  }
  // Never even read the device token for any unexpected API destination.
  for (const apiBase of [
    'https://private.example.test',
    'http://wude-ai-stock-v6-production.up.railway.app',
    allowedApiBase + '.unexpected.test',
    allowedApiBase + '@unexpected.test',
    allowedApiBase + '/unexpected',
    allowedApiBase + '?redirect=unexpected',
    allowedApiBase + '#unexpected',
    ' ' + allowedApiBase,
  ]) {
    const wrongHost = boot({apiBase, withLiveConfig: true});
    await wrongHost.submit();
    assert.strictEqual(wrongHost.calls.length, 0);
    assert.deepStrictEqual(wrongHost.storageReads, []);
    assertEmpty(wrongHost);
    assert.strictEqual(wrongHost.nodes.requestStatus.textContent, '目前無法讀取私人研究，請稍後手動重試。');
  }
  for (const invalid of ['', ' ', '<script>', 'AAPL?token=x', 'AAPL/MSFT', 'AAPL MSFT', 'A'.repeat(16)]) {
    const invalidApp = boot();
    await invalidApp.submit(invalid);
    assert.strictEqual(invalidApp.calls.length, 0);
    assert.deepStrictEqual(invalidApp.storageReads, []);
  }

  // Hide or navigation cancels pending fetches, erases the query, and ignores late responses.
  for (const hideMethod of ['hide', 'pagehide']) {
    const interrupted = boot();
    const oldRun = interrupted.submit();
    interrupted[hideMethod]();
    assert.strictEqual(interrupted.calls[0].init.signal.aborted, true);
    assert.strictEqual(interrupted.nodes.researchSymbol.value, '');
    assertEmpty(interrupted);
    interrupted.show(); interrupted.pageshow();
    assert.strictEqual(interrupted.calls.length, 1, 'returning to the page must not refetch');
    const newerRun = interrupted.submit('MSFT');
    interrupted.respond(0, payload());
    await oldRun;
    assertEmpty(interrupted);
    assert.strictEqual(interrupted.nodes.researchSubmit.disabled, true, 'stale completion must not unlock a newer request');
    interrupted.respond(1, payload({symbol: 'MSFT'}));
    await newerRun;
    assert(interrupted.nodes.researchResult.textContent.startsWith('MSFT'));
    interrupted[hideMethod]();
    assertEmpty(interrupted);
    assert.strictEqual(interrupted.nodes.requestStatus.textContent, '');
  }

  // Also guard the asynchronous response-body boundary, even if fetch ignores abort.
  const bodyApp = boot();
  const bodyRun = bodyApp.submit();
  const body = deferred();
  bodyApp.respond(0, null, {json: () => body.promise});
  await Promise.resolve();
  bodyApp.hide(); bodyApp.show();
  body.resolve(payload()); await bodyRun;
  assertEmpty(bodyApp);
  assert.strictEqual(bodyApp.nodes.requestStatus.textContent, '');

  // No request can begin while the page is hidden or in its pagehide state.
  const hidden = boot();
  hidden.hide(); await hidden.submit();
  hidden.show(); hidden.pagehide(); await hidden.submit();
  assert.strictEqual(hidden.calls.length, 0);

  // Contract violations, wrong symbols, malformed JSON and network failures fail closed.
  for (const bad of [
    {ok: false, data: payload().data}, {}, {ok: true, data: null},
    payload({decision_eligible: true}), payload({decision_eligible: undefined}),
    payload({status: 'eligible'}), payload({symbol: 'MSFT'}),
  ]) {
    const badApp = boot();
    const badRun = badApp.submit();
    badApp.respond(0, bad); await badRun;
    assertEmpty(badApp);
    assert.strictEqual(badApp.nodes.requestStatus.textContent, '目前無法讀取私人研究，請稍後手動重試。');
  }
  const brokenJson = boot();
  const brokenRun = brokenJson.submit();
  brokenJson.respond(0, null, {json: async () => { throw new Error('private malformed body'); }});
  await brokenRun; assertEmpty(brokenJson);
  const brokenNetwork = boot();
  const networkRun = brokenNetwork.submit();
  brokenNetwork.calls[0].reject(new Error('private network detail'));
  await networkRun; assertEmpty(brokenNetwork);
  assert(!brokenNetwork.nodes.requestStatus.textContent.includes('private network detail'));

  // Blocked payloads do not expose geometry or features, even if incorrectly populated.
  const blocked = boot();
  const blockedRun = blocked.submit();
  blocked.respond(0, payload({status: 'blocked'})); await blockedRun;
  assert(blocked.nodes.researchResult.textContent.includes('研究資料受阻'));
  assert(!blocked.nodes.researchResult.textContent.includes('歷史研究指標'));
  assert(!blocked.nodes.researchResult.textContent.includes('幾何目標 1'));
  assert(blocked.nodes.researchResult.textContent.includes('unvalidated_research'));

  // All displayed values use text nodes; only whitelisted data fields are shown.
  const xss = boot();
  const xssRun = xss.submit();
  const attack = '<img src=x onerror=alert(1)>';
  xss.respond(0, payload({
    provenance: {source: attack}, reasons: [attack, {unexpected: attack}],
    features: {close: attack, ma5: NaN, ma10: Infinity, unapproved_field: 'HIDDEN_FIELD'},
    plan: null, formal_rank: 1, unapproved_field: 'HIDDEN_FIELD',
  }));
  await xssRun;
  assert(xss.nodes.researchResult.textContent.includes(attack));
  assert(xss.nodes.researchResult.textContent.includes('資料不足'));
  assert(!xss.nodes.researchResult.textContent.includes('HIDDEN_FIELD'));
  assert(!xss.nodes.researchResult.textContent.includes('NaN'));
  const tags = [];
  function visit(element) { tags.push(element.tagName); element.children.forEach(visit); }
  visit(xss.nodes.researchResult);
  assert(!tags.includes('img'));
  assert(!tags.includes('script'));
  assert.strictEqual(xss.writes(), 0);

  for (const execution_eligible of [true, undefined, null, 0, 'false']) {
    const actionable = boot();
    const actionableRun = actionable.submit();
    actionable.respond(0, payload({plan: {...payload().data.plan, execution_eligible}}));
    await actionableRun;
    assert(!actionable.nodes.researchResult.textContent.includes('幾何目標 1'));
  }

  console.log('private research UI checks passed (offline behavior, privacy, lifecycle, and safe rendering)');
}

run().catch(error => { console.error(error); process.exitCode = 1; });
