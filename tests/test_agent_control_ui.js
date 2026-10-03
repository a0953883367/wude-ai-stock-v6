const fs=require('fs');
const assert=require('assert');
const {execFileSync}=require('child_process');
const html=fs.readFileSync('agent-control.html','utf8');
const js=fs.readFileSync('agent_control.js','utf8');
const css=fs.readFileSync('agent_control.css','utf8');
const report=JSON.parse(fs.readFileSync('reports/agent_control.json','utf8'));
const liveRuntime=JSON.parse(fs.readFileSync('reports/agent_runtime.json','utf8'));
// Exact normal-state expectations use the same isolated evidence as the Python
// tests. A real critical guard must remain attention, without failing UI checks.
const runtime=JSON.parse(execFileSync(process.env.PYTHON || 'python', ['-c', `
import json, runpy, tempfile
from pathlib import Path
from agent_runtime import build_runtime_report
fixture = runpy.run_path('tests/test_agent_runtime.py')['_runtime_root']
with tempfile.TemporaryDirectory() as directory:
    root = fixture(Path(directory))
    print(json.dumps(build_runtime_report(root / 'reports', root=root)))
`], {encoding:'utf8'}));

[
  'Agent 控制中心','各事業資料仍完全分開','三個專屬 Agent','目前保持鎖定','資料隔離規則',
  '付款、寄送、下單及設備控制會停下等你同意','安全任務執行驗收','任務中心','交付驗收清冊'
].forEach(text=>assert(html.includes(text),`missing UI copy: ${text}`));
assert(html.includes('agent_control.js?v=8'));
assert(html.includes('agent_control.css?v=3'));
assert(js.includes("reports/agent_control.json"));
assert(js.includes('正式 V6 權重'));
assert(js.includes("warning:'注意'"));
assert(js.includes("fetchJSON('reports/agent_runtime.json')"));
assert(js.includes("fetchJSON('reports/agent_tasks.json')"));
assert(js.includes("fetchJSON('reports/artifact_registry.json')"));
assert(js.includes('動態載入估計減少'));
assert(js.includes('function renderDeliveries'));
assert(js.includes('task.artifact_url'));
assert(js.includes('付費模型呼叫'));
assert(js.includes('report.stock_maintenance'));
assert(js.includes('已觸發，等待驗收'));
assert(js.includes('packaging-order-intake.html'));
assert(js.includes('開啟未接 ERP 接單中心'));
assert(css.includes('.agent-grid'));
assert(css.includes('.task-grid'));
assert(css.includes('.delivery-grid'));
assert.strictEqual(report.agents.length,3);
assert.strictEqual(new Set(report.agents.map(row=>row.namespace)).size,3);
assert.strictEqual(report.data_policy.cross_domain_reads,false);
assert.strictEqual(report.data_policy.shared_business_database,false);
assert.strictEqual(report.safety.automatic_orders,false);
assert.strictEqual(report.safety.automatic_payments,false);
assert.strictEqual(report.safety.automatic_external_messages,false);
assert(report.agents.some(row=>row.id==='stock_shadow'&&row.runtime.formal_v6_locked===true));
assert.strictEqual(runtime.status,'active');
assert.strictEqual(runtime.summary.actual_work_performed,3);
assert.strictEqual(runtime.summary.validation_completed,1);
assert.strictEqual(runtime.summary.waiting_input,2);
assert.strictEqual(runtime.summary.approval_gates_protected,3);
// Also validate the generated live report, including abnormal task states.
assert.strictEqual(liveRuntime.summary.validation_total,liveRuntime.validations.length);
assert.strictEqual(liveRuntime.summary.validation_completed,
  liveRuntime.validations.filter(row=>row.status==='completed').length);
assert.strictEqual(liveRuntime.summary.waiting_input,
  liveRuntime.validations.filter(row=>row.status==='waiting_input').length);
assert.strictEqual(liveRuntime.summary.actual_work_performed,3);
assert.strictEqual(liveRuntime.summary.approval_gates_protected,3);
assert.strictEqual(liveRuntime.summary.external_calls,0);
assert.strictEqual(liveRuntime.summary.paid_model_calls,0);
assert(liveRuntime.validations.every(row=>row.external_side_effect===false));
assert(liveRuntime.approval_gate_checks.every(row=>
  row.status==='waiting_for_approval' && row.external_side_effect===false));
const stockValidation=liveRuntime.validations.find(row=>row.agent_id==='stock_shadow');
assert(stockValidation);
if(stockValidation.evidence.guard_status==='critical') {
  assert.strictEqual(stockValidation.status,'attention');
}
const index=fs.readFileSync('index.html','utf8');
assert(index.includes('agent-control.html'));
const tasks=JSON.parse(fs.readFileSync('reports/agent_tasks.json','utf8'));
assert.strictEqual(tasks.tasks.length,4);
assert.strictEqual(tasks.safety.formal_stock_weights_changed,false);
const deliveries=JSON.parse(fs.readFileSync('reports/artifact_registry.json','utf8'));
assert.strictEqual(deliveries.items.length,2);
assert.strictEqual(deliveries.safety.cross_domain_reads,false);
console.log('agent control UI checks passed');
