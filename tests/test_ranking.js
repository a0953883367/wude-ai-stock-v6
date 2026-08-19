'use strict';

const assert = require('assert');
const ranking = require('../ranking.js');

const qualified = {
  symbol: 'SAFE', overallRankTier: 2, overallRankingScore: 61,
  shortTermRankTier: 2, shortTermRankingScore: 60,
  midLongRankTier: 2, midLongRankingScore: 62
};
const watch = {
  symbol: 'WATCH', overallRankTier: 1, overallRankingScore: 95,
  shortTermRankTier: 1, shortTermRankingScore: 96,
  midLongRankTier: 1, midLongRankingScore: 97
};
const blocked = {
  symbol: 'BLOCK', overallRankTier: 0, overallRankingScore: 99,
  shortTermRankTier: 0, shortTermRankingScore: 99,
  midLongRankTier: 0, midLongRankingScore: 99,
  tradeGuardBlocked: true
};

assert.deepStrictEqual(
  ranking.sortOverall([blocked, watch, qualified]).map(row => row.symbol),
  ['SAFE', 'WATCH', 'BLOCK']
);
assert.deepStrictEqual(
  ranking.sortShort([blocked, watch, qualified]).map(row => row.symbol),
  ['SAFE', 'WATCH', 'BLOCK']
);
assert.deepStrictEqual(
  ranking.sortLong([blocked, watch, qualified]).map(row => row.symbol),
  ['SAFE', 'WATCH', 'BLOCK']
);

const shortLabels = ranking.labelRows('SHORT:TW', [qualified, watch, blocked]);
assert.deepStrictEqual(
  shortLabels.map(row => row.displayRankLabel),
  ['短線 TOP 1', '短線觀察', '短線阻擋']
);

const noQualified = ranking.labelRows('TW', [watch, blocked]);
assert.deepStrictEqual(
  noQualified.map(row => row.displayRankLabel),
  ['觀察', '風險阻擋']
);
assert.ok(noQualified.every(row => !row.displayRankLabel.includes('TOP')));
assert.ok(noQualified.every(row => !/\d/.test(row.displayRankLabel)));

const fallbackBlocked = {
  symbol: 'LEGACY-BLOCK', score: 100, overallEligible: true,
  overallRankTier: 2, shortTermRankTier: 2,
  tradeGuardBlocked: true
};
assert.strictEqual(ranking.overallTier(fallbackBlocked), 0);
assert.strictEqual(ranking.shortTier(fallbackBlocked), 0);

console.log('ranking tests passed');
