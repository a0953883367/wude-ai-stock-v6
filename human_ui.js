(function () {
  'use strict';

  var guides = {
    'index.html': ['先選台股、美股或 ETF', '先看燈號與操作結論', '需要原因時再展開詳細資料'],
    'live-flow.html': ['先確認市場顯示已連線', '選台股或美股查看資金方向', '警報只作輔助，不會自動下單'],
    'decision-hub.html': ['先看「中央唯一答案」', '再選市場與持有期間', '打開個股卡核對排名原因與風險'],
    'chart-pattern-shadow.html': ['先選台股、美股或 ETF', '先看「已確認」再看疑似型態', '型態只作影子驗證，不直接改排名'],
    'inverse-etf-shadow.html': ['先確認市場資料已完成收盤', '搜尋原股票查看反向商品', '槓桿反向商品只作風險參考'],
    'valuation-risk-shadow.html': ['先選市場與風險燈號', '搜尋公司或股票代號', '估值壓力是比較結果，不代表一定下跌'],
    'next-session-shadow.html': ['先選台股、美股或 ETF', '切換上漲機率或可買性', '開盤後仍須確認量價與支撐'],
    'prediction-engine.html': ['先選市場與預判期間', '先看機率、幅度與排名原因', '再核對成熟樣本與歷史關聯']
  };

  function currentFile() {
    var file = window.location.pathname.split('/').pop();
    return file || 'index.html';
  }

  function addGuide() {
    var steps = guides[currentFile()];
    if (!steps || document.querySelector('.human-guide')) return;
    var main = document.querySelector('main');
    if (!main) return;
    main.id = main.id || 'mainContent';

    var skip = document.createElement('a');
    skip.className = 'human-skip-link';
    skip.href = '#mainContent';
    skip.textContent = '直接到主要內容';
    document.body.insertBefore(skip, document.body.firstChild);

    var guide = document.createElement('details');
    guide.className = 'human-guide';
    var summary = document.createElement('summary');
    summary.textContent = '👆 本頁怎麼操作';
    var list = document.createElement('ol');
    steps.forEach(function (step) {
      var item = document.createElement('li');
      item.textContent = step;
      list.appendChild(item);
    });
    guide.appendChild(summary);
    guide.appendChild(list);

    var anchor = main.querySelector('.hero, .header');
    if (anchor && anchor.nextSibling) main.insertBefore(guide, anchor.nextSibling);
    else if (anchor) main.appendChild(guide);
    else main.insertBefore(guide, main.firstChild);
  }

  function addTableHints() {
    document.querySelectorAll('.table-wrap').forEach(function (table) {
      if (table.previousElementSibling && table.previousElementSibling.classList.contains('human-scroll-hint')) return;
      var hint = document.createElement('p');
      hint.className = 'human-scroll-hint';
      hint.textContent = '👈 表格可左右滑動查看完整資料 👉';
      table.parentNode.insertBefore(hint, table);
      table.setAttribute('tabindex', '0');
      table.setAttribute('aria-label', '可左右滑動的資料表格');
    });
  }

  function syncControls() {
    document.querySelectorAll('.tabs button, .rank-tabs button, .mode-tabs button, .shadow-horizons button, .compare-filters button, .filters button, .buttons button').forEach(function (button) {
      button.setAttribute('aria-pressed', button.classList.contains('active') ? 'true' : 'false');
    });
    document.querySelectorAll('.tabs, .rank-tabs, .mode-tabs, .shadow-horizons, .compare-filters, .filters').forEach(function (row) {
      row.setAttribute('role', 'group');
      row.setAttribute('aria-label', row.getAttribute('aria-label') || '篩選與切換選項');
    });
  }

  function improveLiveRegions() {
    ['status', 'notice', 'syncStatus', 'liveLink'].forEach(function (id) {
      var element = document.getElementById(id);
      if (element) element.setAttribute('aria-live', 'polite');
    });
  }

  function init() {
    if (!document.body) return;
    document.body.classList.add('human-ui-ready');
    addGuide();
    addTableHints();
    syncControls();
    improveLiveRegions();
    document.addEventListener('click', function (event) {
      var button = event.target.closest && event.target.closest('button');
      if (!button) return;
      window.setTimeout(function () {
        syncControls();
        if (button.classList.contains('active') && button.scrollIntoView) {
          button.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
        }
      }, 0);
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
})();
