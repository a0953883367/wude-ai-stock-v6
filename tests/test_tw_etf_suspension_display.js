'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/suspended_00662_20261114.json')));
const python = process.env.PYTHON || 'python';
const display = JSON.parse(execFileSync(python, ['-c',
  'import json; from tw_etf_suspension_display import quote_display_for; f=json.load(open("tests/fixtures/suspended_00662_20261114.json")); print(json.dumps(quote_display_for(f["row"], f["updated_at"])))'], {cwd: root}));
const context = {STATE:{}, lightFrom: () => '🟡', number: v => String(v), marketCode: s => s.split('.')[0],
  esc: v => String(v == null ? '—' : v).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))};
vm.createContext(context);
for (const name of ['normalizeReport', 'suspensionDisplayFor', 'stockCard']) {
  const source = html.match(new RegExp('function ' + name + '\\([^]*?\\n\\}'))[0];
  vm.runInContext(source, context);
}
const row = {...fixture.row, quote_display: display};
const original = JSON.stringify(row);
const normalized = context.normalizeReport(row);
const card = context.stockCard(normalized, 0);
assert.match(card, /分割停牌中/);
assert.match(card, /2026-11-12 僅供稽核/);
assert.match(card, /2026-11-14 報告狀態，非即時恢復確認/);
for (const forbidden of ['現價', '官方收盤', '117.8', '+0.00%', '第一買進區', 'data-live-symbol', 'data-capital-result']) assert.ok(!card.includes(forbidden), forbidden);
assert.equal(normalized.score, fixture.row.score);
assert.equal(normalized.overallRankingScore, fixture.row.overall_ranking_score);
assert.equal(JSON.stringify(row), original);
assert.match(card, /證交所公告/);
const bare = context.normalizeReport(fixture.row);
// Both directions of mixed publication and the exact merge that erased metadata.
for (const state of [
  {latest:{watchlist:[row]}, allAnalysis:{data:[fixture.row]}},
  {latest:{watchlist:[fixture.row]}, allAnalysis:{data:[row]}},
  {normalized:[normalized]},
]) {
  context.STATE=state;
  const merged=Object.assign({},normalized,bare);
  assert.equal(merged.quoteDisplay,undefined);
  const mixed=context.stockCard(merged,0);
  assert.match(mixed,/分割停牌中/);
  assert.ok(!mixed.includes('117.8'));
}
context.STATE={};
// No metadata at all, including legacy fallback and failed fetches, is fail-closed.
assert.match(context.stockCard(bare,0),/狀態尚待核對/);
assert.match(context.stockCard({symbol:'00662.TW',market:'TW',type:'ETF',price:121.8,name:'富邦NASDAQ'},0),/狀態尚待核對/);
assert.equal(context.suspensionDisplayFor({...bare,symbol:'0050.TW'}),null);
assert.equal(context.normalizeReport({...fixture.row, symbol:'0050.TW'}).quoteDisplay, undefined);
const hostile = context.stockCard({...normalized, name:'<script>alert(1)</script>'}, 0);
assert.ok(!hostile.includes('<script>'));
for (const script of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) new vm.Script(script[1]);
console.log('ETF suspension display tests passed; model scores unchanged; inline scripts parse.');
