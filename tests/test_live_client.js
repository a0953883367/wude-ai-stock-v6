const assert = require('assert');
const client = require('../live_client.js');

assert.strictEqual(
  client.buildUrl('https://live.example.com/', 'nvda', 'us'),
  'https://live.example.com/api/live?symbol=NVDA&market=US&options=1'
);
assert.strictEqual(client.buildUrl('', 'NVDA', 'US'), '');
assert.strictEqual(client.pollInterval(100), 3000);
assert.strictEqual(client.pollInterval(60000), 30000);

assert.strictEqual(client.tokenFromHash('#live_token=private-value'), 'private-value');
const stored = {};
const storage = {setItem: (key, value) => { stored[key] = value; }, getItem: key => stored[key]};
let replaced = '';
assert.strictEqual(client.saveTokenFromHash(
  {hash: '#live_token=private-value', pathname: '/wude-ai-stock-v6/', search: '?mode=owner'},
  {replaceState: (_state, _title, value) => { replaced = value; }}, storage
), 'private-value');
assert.strictEqual(client.accessToken(storage), 'private-value');
assert.strictEqual(replaced, '/wude-ai-stock-v6/?mode=owner');
assert.deepStrictEqual(client.requestHeaders('private-value'), {
  Accept: 'application/json', 'X-Live-Token': 'private-value'
});

const us = client.mergeStock({market: 'US', usLivePrice: 190}, {
  ok: true,
  source: 'Alpaca SIP',
  fetched_at: '2026-08-18T12:00:00Z',
  quote: {us_live_price: 200, us_live_quote_imbalance_pct: 25},
  options: {us_option_safety_score: 68}
});
assert.strictEqual(us.usLivePrice, 200);
assert.strictEqual(us.usQuoteImbalance, 25);
assert.strictEqual(us.usOptionSafetyScore, 68);

const tw = client.taiwanQuote({
  ok: true,
  fetched_at: '2026-08-18T12:00:00+08:00',
  quote: {lastPrice: 1060, bidTotal: 100, askTotal: 80}
});
assert.deepStrictEqual(tw, {
  lastPrice: 1060, bidTotal: 100, askTotal: 80,
  fetchedAt: '2026-08-18T12:00:00+08:00'
});

console.log('live client tests passed');
