const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const pages = [
  'index.html',
  'live-flow.html',
  'decision-hub.html',
  'inverse-etf-shadow.html',
  'valuation-risk-shadow.html',
];

for (const page of pages) {
  const html = fs.readFileSync(page, 'utf8');
  assert.match(html, /app_shell\.css\?v=1/, `${page} 缺少共用 App 樣式`);
  assert.match(html, /app_shell\.js\?v=1/, `${page} 缺少共用 App 導覽`);
  assert.match(html, /manifest\.webmanifest/, `${page} 缺少主畫面 App manifest`);
}

const shell = fs.readFileSync('app_shell.js', 'utf8');
for (const expected of [
  "label: '總覽'",
  "label: '大量買賣'",
  "label: 'AI 決策'",
  "label: '反向 ETF'",
  "label: '估值雷達'",
  "aria-current",
]) {
  assert.ok(shell.includes(expected), `共用 App 導覽缺少 ${expected}`);
}

const manifest = JSON.parse(fs.readFileSync('manifest.webmanifest', 'utf8'));
assert.strictEqual(manifest.display, 'standalone');
assert.strictEqual(manifest.shortcuts.length, 4);

function element(tagName) {
  return {
    tagName,
    children: [],
    attributes: {},
    appendChild(child) { this.children.push(child); },
    setAttribute(name, value) { this.attributes[name] = value; },
  };
}

for (const current of pages) {
  const body = element('body');
  const rootClasses = [];
  const document = {
    body,
    readyState: 'complete',
    documentElement: { classList: { add(name) { rootClasses.push(name); } } },
    createElement: element,
    getElementById() { return null; },
  };
  vm.runInNewContext(shell, {
    document,
    window: { location: { pathname: `/wude-ai-stock-v6/${current}` } },
  });
  assert.strictEqual(body.children.length, 1, `${current} 應建立一個底部導覽`);
  assert.strictEqual(body.children[0].children.length, 5, `${current} 應顯示五個功能按鈕`);
  const active = body.children[0].children.filter(link => link.attributes['aria-current'] === 'page');
  assert.strictEqual(active.length, 1, `${current} 應只有一個目前頁面`);
  if (current === 'index.html') assert.ok(rootClasses.includes('app-shell-overview'));
}

console.log('app shell UI tests passed');
