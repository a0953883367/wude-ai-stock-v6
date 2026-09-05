import assert from "node:assert/strict";
import test from "node:test";

let payload;
global.window = {
  fetch: async () => new Response(JSON.stringify(payload), {
    headers: { "content-type": "application/json" },
  }),
};
await import(`../live_config.js?test=${Date.now()}`);

test("retired 5371 is hidden while a separate official 3718 row is shown", async () => {
  payload = { data: [{ symbol: "5371.TWO", name: "中光電", price: 83.5 }] };
  const response = await window.fetch("reports/all_analysis.json");
  const result = await response.json();
  assert.equal(result.data.length, 1);
  assert.equal(result.data[0].symbol, "3718.TWO");
  assert.equal(result.data[0].name, "中光電投控");
  assert.equal(result.data[0].price, 75.5);
  assert.equal(result.data[0].score, null);
  assert.deepEqual(payload.data, [{ symbol: "5371.TWO", name: "中光電", price: 83.5 }]);
});

test("a fresh generated 3718 row retires the compatibility bridge", async () => {
  payload = { data: [{
    symbol: "3718.TWO",
    name: "中光電投控",
    price: 76,
    official_session_date: "2026-09-05",
    score: 62,
  }] };
  const response = await window.fetch("reports/all_analysis.json");
  const result = await response.json();
  assert.deepEqual(result.data, payload.data);
});
