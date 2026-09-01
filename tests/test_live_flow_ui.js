const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'live-flow.html'), 'utf8');
const script = fs.readFileSync(path.join(root, 'live_flow.js'), 'utf8');
const index = fs.readFileSync(path.join(root, 'index.html'), 'utf8');

function loadRenderRow() {
  const element = () => ({
    addEventListener() {},
    classList: { toggle() {} },
    focus() {},
    querySelectorAll() { return []; },
    hidden: false,
    innerHTML: '',
    style: {},
    textContent: '',
    value: '',
  });
  const elements = new Map();
  const context = {
    clearTimeout() {},
    document: {
      addEventListener() {},
      getElementById(id) {
        if (!elements.has(id)) elements.set(id, element());
        return elements.get(id);
      },
      visibilityState: 'hidden',
    },
    history: { replaceState() {} },
    localStorage: {
      getItem() { return ''; },
      removeItem() {},
      setItem() {},
    },
    location: { hash: '', pathname: '/', search: '' },
    navigator: {},
    setTimeout() { return 0; },
    URLSearchParams,
    window: { addEventListener() {}, WUDE_LIVE_API_BASE: '' },
  };
  const exposed = script.replace(/\}\)\(\);\s*$/, 'globalThis.__testRenderRow=renderRow;})();');
  vm.runInNewContext(exposed, context);
  return context.__testRenderRow;
}

test('public report links to a standalone capital-flow page', () => {
  assert.match(index, /href="live-flow\.html"/);
  assert.match(html, /大量買賣／資金流/);
  assert.match(html, /不讀取個別股票即時股價/);
});

test('report and capital-flow pages share the arrow app icon', () => {
  for (const page of [index, html]) {
    assert.match(page, /manifest\.webmanifest\?v=3/);
    assert.match(page, /trend-arrow-icon-v2\.svg/);
    assert.match(page, /trend-arrow-apple-v2\.png/);
  }
});

test('standalone page reads only the aggregate alert endpoint', () => {
  assert.match(script, /\/api\/large-buy-alerts\?after=/);
  assert.doesNotMatch(script, /\/api\/live\?symbol=/);
  assert.match(script, /X-Live-Token/);
});

test('phone authorization uses a one-time Telegram code and stores only a read token', () => {
  assert.match(html, /id="pairingBox"/);
  assert.match(html, /autocomplete="one-time-code"/);
  assert.match(html, /180天/);
  assert.match(script, /\/api\/device-auth\/request/);
  assert.match(script, /\/api\/device-auth\/verify/);
  assert.match(script, /localStorage\.setItem\('wude-live-access-token'/);
  assert.doesNotMatch(script, /\/api\/trading\//);
});

test('polling is throttled and pauses in the background', () => {
  assert.match(script, /success\?10000:30000/);
  assert.match(script, /document\.visibilityState/);
  assert.match(script, /visibilitychange/);
  assert.doesNotMatch(script, /setInterval/);
});

test('closed streams and zero-flow sessions use neutral Chinese labels', () => {
  assert.match(script, /waiting_market_open.*等待下次開盤/);
  assert.doesNotMatch(script, /waiting_market_open.*資金偏空/);
  assert.match(script, /function flowDirection\(flow\)/);
  assert.match(script, /directional>0\?\(flow\.direction\|\|'多空拉鋸'\):'暫無資金方向'/);
});

test('US premarket status and saved summary are visible and separated', () => {
  assert.match(html, /美股自盤前開始監控/);
  assert.match(script, /premarket_connected.*盤前監控中/);
  assert.match(script, /connected.*正式盤監控中/);
  assert.match(script, /premarket_summary/);
  assert.match(script, /盤前摘要/);
  assert.match(script, /美股.*盤前/);
});

test('capital flow keeps buy and sell analysis', () => {
  assert.match(html, /買進與賣出都計算/);
  assert.match(script, /theme_inflows/);
  assert.match(script, /theme_outflows/);
  assert.match(script, /top_inflows/);
  assert.match(script, /top_outflows/);
});

test('theme outflows keep theme labels and never render raw undefined names', () => {
  assert.match(script, /function renderRow\(row,index,market,rowType,direction\)/);
  assert.match(script, /theme_outflows\|\|\[\],state\.market,'theme','out'/);
  assert.match(script, /top_outflows\|\|\[\],state\.market,'stock','out'/);
  assert.match(script, /row\.theme\|\|'未分類族群'/);
  assert.match(script, /row\.name\|\|row\.symbol\|\|'未命名'/);
  assert.doesNotMatch(script, /var title=isTheme\?row\.theme:\(row\.name\|\|row\.symbol\)\+'・'\+row\.symbol/);
  assert.match(html, /live_flow\.js\?v=647/);

  const renderRow = loadRenderRow();
  const output = renderRow({
    theme: '公用電力 / 電網',
    net_flow: -3046000,
    buy_ratio_pct: 31.3,
    negative_symbols: 2,
    member_count: 2,
  }, 0, 'US', 'theme', 'out');
  assert.match(output, /公用電力 \/ 電網/);
  assert.match(output, /賣出68\.7%/);
  assert.doesNotMatch(output, /undefined/);
  assert.match(renderRow({}, 0, 'US', 'theme', 'out'), /未分類族群/);
});

test('single-trade thresholds and block labels stay linked to the API policy', () => {
  assert.match(html, /id="alertPolicy"/);
  assert.match(script, /general_single_thresholds/);
  assert.match(script, /major_single_thresholds/);
  assert.match(script, /block_single_thresholds/);
  assert.match(script, /美股巨額單筆/);
  assert.match(script, /row\.is_block_trade&&row\.block_trade_label/);
});

test('five-day flow weight experiment is isolated and visible', () => {
  assert.match(html, /資金流影子權重 V1/);
  assert.match(html, /總調整上限 ±3 點/);
  assert.match(html, /45日與6個月模型完全不變/);
  assert.match(html, /滿60日及200筆訊號/);
  assert.match(script, /flow_weight_shadow/);
  assert.match(script, /正式排名鎖定/);
  assert.match(script, /儲存未持久化・本次不計入驗證/);
  assert.match(script, /flow-alert-sound/);
  assert.match(html, /訊號後實際方向驗證/);
  assert.match(script, /訊號後5分鐘/);
  assert.match(script, /訊號後15分鐘/);
  assert.match(script, /訊號後60分鐘/);
  assert.match(script, /第1交易日/);
  assert.match(script, /第3交易日/);
  assert.match(script, /第5交易日/);
  assert.match(html, /官方 TWSE／TPEx 與 Alpaca 市場日曆/);
  assert.match(html, /不會拿下一日遞補/);
  assert.match(script, /官方交易日曆已驗證/);
  assert.match(script, /日序結算暫停/);
});
