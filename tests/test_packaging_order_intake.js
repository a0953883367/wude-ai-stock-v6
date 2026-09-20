const assert = require('assert');
const fs = require('fs');
const path = require('path');
const logic = require('../packaging_order_intake_logic.js');

assert.strictEqual(logic.calculateWeightKg(100000, 8.5, 'g_each'), 850);
assert.strictEqual(logic.calculateWeightKg(100000, 8.5, 'kg_per_1000'), 850);
assert.strictEqual(logic.fingerprint({customer:'ABC ',customerPo:' PO-1',version:'1'}), 'abc|po-1|1');
assert.strictEqual(logic.nextStatus('pending_review'), 'pending_signature');
assert.strictEqual(logic.nextStatus('pending_signature'), 'returned');
assert.strictEqual(logic.nextStatus('returned'), 'accepted');
assert.strictEqual(logic.nextStatus('accepted'), 'converted');
assert.strictEqual(logic.createWorkOrderId({acceptedAt:'2026-09-20T08:00:00Z'}, 1), 'WO-20260920-001');

const orders = [
  {status:'accepted',acceptedAt:'2026-09-10',receivedAt:'2026-09-01',deliveryDate:'2026-10-02',quantity:100000,weightKg:850},
  {status:'pending_signature',receivedAt:'2026-09-03',quantity:50000,weightKg:200},
  {status:'cancelled',receivedAt:'2026-09-04',acceptedAt:'2026-09-04',deliveryDate:'2026-09-20',quantity:999,weightKg:999}
];
const september = logic.summarize(orders, '2026-09');
assert.deepStrictEqual(september, {received:3,pendingSignature:1,accepted:1,acceptedPieces:100000,acceptedWeightKg:850,scheduledWeightKg:0});
const october = logic.summarize(orders, '2026-10');
assert.strictEqual(october.scheduledWeightKg, 850);

const html = fs.readFileSync(path.join(__dirname, '..', 'packaging-order-intake.html'), 'utf8');
assert.match(html, /未接 ERP 接單中心/);
assert.match(html, /不自動寄送/);
assert.match(html, /拍照／掃描／傳真/);
assert.match(html, /Email附件/);
assert.match(html, /Excel／CSV/);
assert.match(html, /確認接單後/);
console.log('packaging order intake tests passed');
