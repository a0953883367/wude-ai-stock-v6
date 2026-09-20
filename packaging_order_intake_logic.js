(function (root, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.PackagingOrderIntakeLogic = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  function text(value) {
    return String(value === undefined || value === null ? '' : value).trim();
  }

  function number(value) {
    var parsed = Number(value);
    return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0;
  }

  function calculateWeightKg(quantity, unitWeight, basis) {
    var pieces = number(quantity);
    var weight = number(unitWeight);
    if (basis === 'kg_per_1000') return pieces / 1000 * weight;
    return pieces * weight / 1000;
  }

  function fingerprint(order) {
    return [text(order.customer).toLowerCase(), text(order.customerPo).toLowerCase(), text(order.version || '1').toLowerCase()].join('|');
  }

  function monthOf(value) {
    var match = text(value).match(/^(\d{4}-\d{2})/);
    return match ? match[1] : '';
  }

  function summarize(orders, month) {
    var result = {
      received: 0,
      pendingSignature: 0,
      accepted: 0,
      acceptedPieces: 0,
      acceptedWeightKg: 0,
      scheduledWeightKg: 0
    };
    (Array.isArray(orders) ? orders : []).forEach(function (order) {
      if (monthOf(order.receivedAt) === month) result.received += 1;
      if (order.status === 'pending_signature' && monthOf(order.receivedAt) === month) result.pendingSignature += 1;
      if (order.status === 'accepted' || order.status === 'converted') {
        if (monthOf(order.acceptedAt) === month) {
          result.accepted += 1;
          result.acceptedPieces += number(order.quantity);
          result.acceptedWeightKg += number(order.weightKg);
        }
        if (monthOf(order.deliveryDate) === month) result.scheduledWeightKg += number(order.weightKg);
      }
    });
    return result;
  }

  function nextStatus(status) {
    return {
      pending_review: 'pending_signature',
      pending_signature: 'returned',
      returned: 'accepted',
      accepted: 'converted'
    }[status] || status;
  }

  function createWorkOrderId(order, sequence) {
    var date = text(order.acceptedAt || new Date().toISOString()).slice(0, 10).replace(/-/g, '');
    return 'WO-' + date + '-' + String(sequence || 1).padStart(3, '0');
  }

  return {
    calculateWeightKg: calculateWeightKg,
    fingerprint: fingerprint,
    summarize: summarize,
    nextStatus: nextStatus,
    createWorkOrderId: createWorkOrderId
  };
}));
