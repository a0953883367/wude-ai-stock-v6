(function () {
  'use strict';

  var pages = [
    { file: 'index.html', href: './', icon: '📊', label: '總覽' },
    { file: 'live-flow.html', href: 'live-flow.html', icon: '🚨', label: '大量買賣' },
    { file: 'decision-hub.html', href: 'decision-hub.html', icon: '🧠', label: 'AI 決策' },
    { file: 'inverse-etf-shadow.html', href: 'inverse-etf-shadow.html', icon: '↘️', label: '反向 ETF' },
    { file: 'valuation-risk-shadow.html', href: 'valuation-risk-shadow.html', icon: '🧮', label: '估值雷達' }
  ];

  function currentFile() {
    var file = window.location.pathname.split('/').pop();
    return file || 'index.html';
  }

  function buildNav() {
    if (!document.body || document.getElementById('appShellNav')) return;
    var current = currentFile();
    var nav = document.createElement('nav');
    nav.id = 'appShellNav';
    nav.className = 'app-shell-nav';
    nav.setAttribute('aria-label', 'App 功能選單');

    pages.forEach(function (page) {
      var link = document.createElement('a');
      var icon = document.createElement('span');
      var label = document.createElement('span');
      link.href = page.href;
      link.title = page.label;
      icon.className = 'app-shell-nav-icon';
      icon.setAttribute('aria-hidden', 'true');
      icon.textContent = page.icon;
      label.textContent = page.label;
      link.appendChild(icon);
      link.appendChild(label);
      if (page.file === current) link.setAttribute('aria-current', 'page');
      nav.appendChild(link);
    });

    if (current === 'index.html') document.documentElement.classList.add('app-shell-overview');
    document.body.appendChild(nav);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', buildNav, { once: true });
  } else {
    buildNav();
  }
})();
