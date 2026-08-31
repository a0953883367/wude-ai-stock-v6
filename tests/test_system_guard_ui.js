const assert = require('assert');
const fs = require('fs');

const html = fs.readFileSync('index.html', 'utf8');
assert.match(html, /data-view="GUARD"/);
assert.match(html, /function showSystemGuard\(\)/);
assert.match(html, /function currentSystemGuard\(guard\)/);
assert.match(html, /reports\/system_guard\.json/);
assert.match(html, /guard_heartbeat/);
assert.match(html, /age>180/);
assert.match(html, /actions\/workflows\/system-guard\.yml\/runs\?per_page=1/);
assert.match(html, /無法取得 GitHub Actions 即時狀態；保留上一份巡檢結果，不誤判為停止/);
assert.match(html, /App、Railway、台／美股串流、Telegram、手機授權與正式報表分開判斷/);
assert.match(html, /自動改排名：關閉/);
assert.match(html, /自動下單：關閉/);
assert.match(html, /if\(view==='GUARD'\)\{showSystemGuard\(\);return;\}/);

const guardFunction = html.slice(
  html.indexOf('function currentSystemGuard(guard)'),
  html.indexOf('function showSystemGuard()', html.indexOf('function currentSystemGuard(guard)')),
);
let STATE = {guardWorkflow: null};
eval(guardFunction);
const baseGuard = {checks: []};
let runtime = currentSystemGuard(baseGuard);
assert.strictEqual(runtime.status, 'ok');
assert.strictEqual(runtime.checks[0].level, 'info');

STATE.guardWorkflow = {workflow_runs: [{
  status: 'completed', conclusion: 'failure', updated_at: new Date().toISOString(),
}]};
runtime = currentSystemGuard(baseGuard);
assert.strictEqual(runtime.status, 'critical');

STATE.guardWorkflow = {workflow_runs: [{
  status: 'completed', conclusion: 'success', updated_at: new Date(Date.now() - 4 * 60 * 60 * 1000).toISOString(),
}]};
runtime = currentSystemGuard(baseGuard);
assert.strictEqual(runtime.status, 'critical');

console.log('system guard UI tests passed');
