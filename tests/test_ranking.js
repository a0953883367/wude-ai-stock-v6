'use strict';

const assert = require('assert');
const ranking = require('../ranking.js');

const qualified = {
  symbol: 'SAFE', overallRankTier: 2, overallRankingScore: 61,
  overallRank: 1, overallDisplayRank: 1,
  shortTermRankTier: 2, shortTermRankingScore: 60,
  shortTermRank: 1, shortTermDisplayRank: 1,
  midLongRankTier: 2, midLongRankingScore: 62,
  midLongRank: 1, midLongDisplayRank: 1, rankingGroupCount: 3
};
const watch = {
  symbol: 'WATCH', overallRankTier: 1, overallRankingScore: 95,
  overallDisplayRank: 2,
  shortTermRankTier: 1, shortTermRankingScore: 96,
  shortTermDisplayRank: 2,
  midLongRankTier: 1, midLongRankingScore: 97,
  midLongDisplayRank: 2, rankingGroupCount: 3
};
const blocked = {
  symbol: 'BLOCK', overallRankTier: 0, overallRankingScore: 99,
  overallDisplayRank: 3,
  shortTermRankTier: 0, shortTermRankingScore: 99,
  shortTermDisplayRank: 3,
  midLongRankTier: 0, midLongRankingScore: 99,
  midLongDisplayRank: 3, rankingGroupCount: 3,
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
  ['短線 TOP 1', '短線觀察｜同組排序 2/3', '短線阻擋｜同組排序 3/3']
);

const noQualified = ranking.labelRows('TW', [watch, blocked]);
assert.deepStrictEqual(
  noQualified.map(row => row.displayRankLabel),
  ['觀察｜同組排序 2/3', '風險阻擋｜同組排序 3/3']
);
assert.ok(noQualified.every(row => !row.displayRankLabel.includes('TOP')));
assert.ok(noQualified.every(row => row.displayRankLabel.includes('同組排序')));

const fallbackBlocked = {
  symbol: 'LEGACY-BLOCK', score: 100, overallEligible: true,
  overallRankTier: 2, shortTermRankTier: 2,
  tradeGuardBlocked: true
};
assert.strictEqual(ranking.overallTier(fallbackBlocked), 0);
assert.strictEqual(ranking.shortTier(fallbackBlocked), 0);

console.log('ranking tests passed');
