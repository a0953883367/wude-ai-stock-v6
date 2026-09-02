const fs = require('node:fs');
const assert = require('node:assert/strict');

const html = fs.readFileSync('prediction-engine.html', 'utf8');
const js = fs.readFileSync('prediction_engine.js', 'utf8');
const index = fs.readFileSync('index.html', 'utf8');
const central = fs.readFileSync('decision_hub.js', 'utf8');

assert.match(html, /NEXT_1D/);
assert.match(html, /UP_5D/);
assert.match(html, /DOWN_14D/);
assert.match(html, /DOWN_21D/);
assert.match(html, /UP_45D/);
assert.match(html, /UP_126D/);
assert.match(html, /正式 V6、原本 5 日／60 日紀錄/);
assert.match(js, /reports\/prediction_engine\.json/);
assert.match(js, /REPORT\.data_files/);
assert.match(js, /本次答案模型/);
assert.match(js, /paper_portfolios/);
assert.match(html, /連續三個不同交易日/);
assert.match(index, /prediction-engine\.html/);
assert.match(central, /prediction_engine/);
assert.match(central, /AI 多週期預判/);

console.log('prediction engine UI checks passed');
