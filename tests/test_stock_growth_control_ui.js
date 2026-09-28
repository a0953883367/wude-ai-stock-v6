'use strict';

const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const html = fs.readFileSync('stock-growth-control.html', 'utf8');
const js = fs.readFileSync('stock_growth_control.js', 'utf8');
const index = fs.readFileSync('index.html', 'utf8');
[
  '系統監控與影子驗證中心','七階段成長流程','六層監控','模型成長位置','目前要做的事',
  '影子驗證中心','明日方向驗證','10日／1月／3月／半年','K線型態驗證',
  '反向ETF驗證','估值風險驗證','20日只能初評','60日才進正式人工審查',
  '126日持續追蹤','不會自動改正式 V6','stock_growth_control.js?v=2'
].forEach((text) => assert(html.includes(text), `missing control tower UI: ${text}`));
assert(index.includes('stock-growth-control.html'));
['今日股票決策','各期間獨立排名','系統監控與影子驗證中心','查看進階證據與專項驗證','影子結果不會混入正式排名'].forEach((text) => {
  assert(index.includes(text), `missing integrated overview: ${text}`);
});
assert(js.includes('reports/stock_growth_control.json'));
assert(js.includes('正式 V6 不受影響'));

const sandbox = {globalThis:{}, console};
vm.createContext(sandbox);
vm.runInContext(js, sandbox);
const api = sandbox.globalThis.StockGrowthControl;
assert(api);
const stages = api.renderStages([{order:1,label:'資料進站',light:'green',status_label:'正常',detail:'完成'}]);
assert(stages.includes('資料進站'));
assert(stages.includes('green'));
assert(!stages.includes('undefined'));
console.log('stock growth control UI checks passed');
