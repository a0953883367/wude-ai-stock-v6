const fs=require('fs');
const assert=require('assert');
const html=fs.readFileSync('agent-control.html','utf8');
const js=fs.readFileSync('agent_control.js','utf8');
const css=fs.readFileSync('agent_control.css','utf8');
const report=JSON.parse(fs.readFileSync('reports/agent_control.json','utf8'));
const runtime=JSON.parse(fs.readFileSync('reports/agent_runtime.json','utf8'));

[
  'Agent 控制中心','各事業資料仍完全分開','四個專屬 Agent','目前保持鎖定','資料隔離規則',
  '付款、寄送、下單及設備控制會停下等你同意','安全任務執行驗收','任務中心'
].forEach(text=>assert(html.includes(text),`missing UI copy: ${text}`));
assert(html.includes('agent_control.js?v=4'));
assert(html.includes('agent_control.css?v=2'));
assert(js.includes("reports/agent_control.json"));
assert(js.includes('正式 V6 權重'));
assert(js.includes('ERP／PLC／設備寫入'));
assert(js.includes("warning:'注意'"));
assert(js.includes("fetchJSON('reports/agent_runtime.json')"));
assert(js.includes("fetchJSON('reports/agent_tasks.json')"));
assert(js.includes('task.artifact_url'));
assert(js.includes('付費模型呼叫'));
assert(css.includes('.agent-grid'));
assert(css.includes('.task-grid'));
assert.strictEqual(report.agents.length,4);
assert.strictEqual(new Set(report.agents.map(row=>row.namespace)).size,4);
assert.strictEqual(report.data_policy.cross_domain_reads,false);
assert.strictEqual(report.data_policy.shared_business_database,false);
assert.strictEqual(report.safety.automatic_orders,false);
assert.strictEqual(report.safety.automatic_payments,false);
assert.strictEqual(report.safety.automatic_external_messages,false);
assert(report.agents.some(row=>row.id==='stock_shadow'&&row.runtime.formal_v6_locked===true));
assert.strictEqual(runtime.status,'passed');
assert.strictEqual(runtime.summary.validation_completed,4);
assert.strictEqual(runtime.summary.approval_gates_protected,4);
const index=fs.readFileSync('index.html','utf8');
assert(index.includes('agent-control.html'));
const tasks=JSON.parse(fs.readFileSync('reports/agent_tasks.json','utf8'));
assert.strictEqual(tasks.tasks.length,5);
assert.strictEqual(tasks.safety.formal_stock_weights_changed,false);
console.log('agent control UI checks passed');
