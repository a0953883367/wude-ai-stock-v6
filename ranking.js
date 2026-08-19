(function (root, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.WudeRanking = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  function number(value, fallback) {
    var result = Number(value);
    return Number.isFinite(result) ? result : (fallback === undefined ? 0 : fallback);
  }

  function truthy(value) {
    return value === true || value === 1 || String(value).toLowerCase() === 'true';
  }

  function tierValue(stock, field, eligibleField, blocked) {
    // Safety state always wins, even when a cached report contains an older
    // conflicting tier value.
    if (blocked) return 0;
    var explicit = Number(stock && stock[field]);
    if (Number.isFinite(explicit)) return Math.max(0, Math.min(2, explicit));
    return truthy(stock && stock[eligibleField]) ? 2 : 1;
  }

  function overallTier(stock) {
    return tierValue(stock, 'overallRankTier', 'overallEligible',
      truthy(stock && stock.tradeGuardBlocked) || stock && stock.marketContractValid === false);
  }

  function shortTier(stock) {
    return tierValue(stock, 'shortTermRankTier', 'shortTermEligible',
      truthy(stock && stock.tradeGuardBlocked) || stock && stock.marketContractValid === false);
  }

  function longTier(stock) {
    return tierValue(stock, 'midLongRankTier', 'midLongEligible',
      truthy(stock && stock.tradeGuardSevere) || stock && stock.marketContractValid === false);
  }

  function symbol(stock) { return String(stock && stock.symbol || ''); }

  function compareValues(a, b, values) {
    for (var i = 0; i < values.length; i += 1) {
      var difference = number(values[i](b)) - number(values[i](a));
      if (difference !== 0) return difference;
    }
    return symbol(a).localeCompare(symbol(b));
  }

  function compareOverall(a, b) {
    return compareValues(a, b, [
      overallTier,
      function (row) { return number(row.overallRankingScore, row.score); },
      function (row) { return row.entryScore; },
      function (row) { return row.score; }
    ]);
  }

  function compareShort(a, b) {
    return compareValues(a, b, [
      shortTier,
      function (row) { return number(row.shortTermRankingScore, row.shortTermScore); },
      function (row) { return row.shortTermScore; },
      function (row) { return row.entryScore; }
    ]);
  }

  function compareLong(a, b) {
    return compareValues(a, b, [
      longTier,
      function (row) { return number(row.midLongRankingScore, row.midLongScore); },
      function (row) { return row.midLongScore; },
      function (row) { return row.score; }
    ]);
  }

  function sorted(rows, comparator) {
    return (rows || []).slice().sort(comparator);
  }

  function labelRows(mode, rows) {
    var counts = {qualified: 0, watch: 0, blocked: 0};
    return (rows || []).map(function (stock) {
      var copy = Object.assign({}, stock), tier, prefix;
      if (String(mode).indexOf('SHORT') === 0) {
        tier = shortTier(stock);
        prefix = tier === 2 ? '短線 TOP' : tier === 1 ? '短線觀察' : '短線阻擋';
      } else if (String(mode).indexOf('LONG') === 0) {
        tier = longTier(stock);
        prefix = tier === 2 ? '中長線 TOP' : tier === 1 ? '中長線觀察' : '中長線阻擋';
      } else if (mode === 'WATCH') {
        tier = 1; prefix = '追蹤';
      } else if (mode === 'TEST') {
        tier = 2; prefix = '實測資格';
      } else if (String(mode).indexOf('QUANTUM') === 0 || String(mode).indexOf('OPTICAL') === 0) {
        tier = overallTier(stock); prefix = tier === 2 ? '題材 TOP' : tier === 1 ? '題材觀察' : '題材阻擋';
      } else if (mode === 'ALL' || String(mode).indexOf('SEARCH') === 0) {
        tier = overallTier(stock); prefix = tier === 2 ? '全部 TOP' : tier === 1 ? '全部觀察' : '全部阻擋';
      } else {
        tier = overallTier(stock); prefix = tier === 2 ? 'TOP' : tier === 1 ? '觀察' : '風險阻擋';
      }
      var bucket = tier === 2 ? 'qualified' : tier === 1 ? 'watch' : 'blocked';
      counts[bucket] += 1;
      copy.displayRankLabel = prefix + ' ' + counts[bucket];
      return copy;
    });
  }

  return {
    overallTier: overallTier,
    shortTier: shortTier,
    longTier: longTier,
    compareOverall: compareOverall,
    compareShort: compareShort,
    compareLong: compareLong,
    sortOverall: function (rows) { return sorted(rows, compareOverall); },
    sortShort: function (rows) { return sorted(rows, compareShort); },
    sortLong: function (rows) { return sorted(rows, compareLong); },
    labelRows: labelRows
  };
}));
