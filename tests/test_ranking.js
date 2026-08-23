'use strict';

const assert = require('assert');
const fs = require('fs');
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
  ['尚未符合買進條件', '風險阻擋']
);
assert.ok(noQualified.every(row => !row.displayRankLabel.includes('TOP')));
assert.ok(noQualified.every(row => !row.displayRankLabel.includes('同組排序')));

const twNoQualified = ranking.labelRows('TW', [watch, blocked]);
assert.deepStrictEqual(
  twNoQualified.map(row => row.displayRankLabel),
  ['尚未符合買進條件', '風險阻擋']
);
assert.ok(twNoQualified.every(row => !row.displayRankLabel.includes('同組排序')));

const fallbackBlocked = {
  symbol: 'LEGACY-BLOCK', score: 100, overallEligible: true,
  overallRankTier: 2, shortTermRankTier: 2,
  tradeGuardBlocked: true
};
assert.strictEqual(ranking.overallTier(fallbackBlocked), 0);
assert.strictEqual(ranking.shortTier(fallbackBlocked), 0);

const accumulationCandidate = {
  symbol: 'ACC', twAccumulationAvailable: true, twAccumulationCandidate: true,
  twAccumulationRankTier: 2, twAccumulationRankingScore: 72,
  twAccumulationRank: 1, twAccumulationDisplayRank: 1,
  twAccumulationGroupCount: 3
};
const accumulationObservation = {
  symbol: 'OBS', twAccumulationAvailable: true, twAccumulationCandidate: false,
  twAccumulationRankTier: 1, twAccumulationRankingScore: 95,
  twAccumulationDisplayRank: 2, twAccumulationGroupCount: 3
};
const accumulationBlocked = {
  symbol: 'BAD', twAccumulationAvailable: true, twAccumulationCandidate: true,
  twAccumulationRankTier: 0, twAccumulationRankingScore: 99,
  twAccumulationDisplayRank: 3, twAccumulationGroupCount: 3,
  tradeGuardBlocked: true
};
assert.deepStrictEqual(
  ranking.sortAccumulation([accumulationBlocked, accumulationObservation, accumulationCandidate]).map(row => row.symbol),
  ['ACC', 'OBS', 'BAD']
);
assert.deepStrictEqual(
  ranking.labelRows('SHORT:ACCUMULATION', [accumulationCandidate, accumulationObservation, accumulationBlocked]).map(row => row.displayRankLabel),
  ['法人蓄力 TOP 1', '法人觀察｜同組排序 2/3', '法人阻擋｜同組排序 3/3']
);

const html = fs.readFileSync('index.html', 'utf8');
[
  'data-short="ACCUMULATION"',
  'WudeRanking.sortAccumulation(rows)',
  '原短線80%＋法人蓄力20%',
  'short_term_accumulation_adjustment',
  'buildAllAnalysisPool():STATE.normalized'
].forEach((text) => assert.ok(html.includes(text), `missing accumulation UI contract: ${text}`));

console.log('ranking tests passed');
