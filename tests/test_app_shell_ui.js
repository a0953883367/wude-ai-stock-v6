const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const pages = [
  'index.html',
  'live-flow.html',
  'decision-hub.html',
  'chart-pattern-shadow.html',
  'inverse-etf-shadow.html',
  'valuation-risk-shadow.html',
  'next-session-shadow.html',
  'prediction-engine.html',
];

for (const page of pages) {
  const html = fs.readFileSync(page, 'utf8');
  assert.match(html, /app_shell\.css\?v=1/, `${page} 缺少共用 App 樣式`);
  assert.match(html, /human_ui\.css\?v=1/, `${page} 缺少人性化共用樣式`);
  assert.match(html, /human_ui\.js\?v=1/, `${page} 缺少人性化操作輔助`);
  assert.match(html, /app_shell\.js\?v=2/, `${page} 缺少共用 App 導覽`);
  assert.match(html, /manifest\.webmanifest/, `${page} 缺少主畫面 App manifest`);
  assert.doesNotMatch(html, /user-scalable\s*=\s*no/i, `${page} 不應禁止手機手勢縮放`);
}

const shell = fs.readFileSync('app_shell.js', 'utf8');
const humanCss = fs.readFileSync('human_ui.css', 'utf8');
const humanJs = fs.readFileSync('human_ui.js', 'utf8');
for (const expected of [
  "label: '總覽'",
  "label: '大量買賣'",
  "label: 'AI 決策'",
  "label: '型態核對'",
  "label: '反向 ETF'",
  "label: '估值雷達'",
  "aria-current",
]) {
  assert.ok(shell.includes(expected), `共用 App 導覽缺少 ${expected}`);
}

for (const expected of [
  'min-height: 46px',
  ':focus-visible',
  '.human-guide',
  '.human-scroll-hint',
  'prefers-reduced-motion',
]) {
  assert.ok(humanCss.includes(expected), `人性化樣式缺少 ${expected}`);
}

for (const expected of [
  '本頁怎麼操作',
  '表格可左右滑動',
  "'next-session-shadow.html'",
  "'prediction-engine.html'",
  "setAttribute('aria-live', 'polite')",
]) {
  assert.ok(humanJs.includes(expected), `人性化操作缺少 ${expected}`);
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
  assert.strictEqual(body.children[0].children.length, 6, `${current} 應顯示六個功能按鈕`);
  const active = body.children[0].children.filter(link => link.attributes['aria-current'] === 'page');
  assert.strictEqual(active.length, 1, `${current} 應只有一個目前頁面`);
  if (current === 'index.html') assert.ok(rootClasses.includes('app-shell-overview'));
}

console.log('app shell UI tests passed');
