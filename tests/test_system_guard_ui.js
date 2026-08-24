const assert = require('assert');
const fs = require('fs');

const html = fs.readFileSync('index.html', 'utf8');
assert.match(html, /data-view="GUARD"/);
assert.match(html, /function showSystemGuard\(\)/);
assert.match(html, /reports\/system_guard\.json/);
assert.match(html, /自動改排名：關閉/);
assert.match(html, /自動下單：關閉/);
assert.match(html, /if\(view==='GUARD'\)\{showSystemGuard\(\);return;\}/);

console.log('system guard UI tests passed');
