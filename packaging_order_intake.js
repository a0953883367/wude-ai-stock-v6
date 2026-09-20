(function () {
  'use strict';
  var logic = window.PackagingOrderIntakeLogic;
  var key = 'wude.packaging.order-intake.demo.v1';
  var orders = load();
  var form = document.getElementById('intakeForm');
  var monthInput = document.getElementById('reportMonth');
  var now = new Date();
  monthInput.value = now.toISOString().slice(0, 7);
  form.elements.receivedAt.value = now.toISOString().slice(0, 10);

  function esc(value) {
    return String(value === undefined || value === null ? '—' : value).replace(/[&<>"']/g, function (ch) { return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]; });
  }
  function load() { try { return JSON.parse(localStorage.getItem(key) || '[]'); } catch (error) { return []; } }
  function save() { localStorage.setItem(key, JSON.stringify(orders)); }
  function selectedSource() { var selected = document.querySelector('input[name="source"]:checked'); return selected ? selected.value : 'manual'; }
  function statusLabel(status) { return {pending_review:'待核對',pending_signature:'待回簽',returned:'已回簽待確認',accepted:'已確認接單',converted:'正式工單',cancelled:'已取消'}[status] || status; }
  function nextLabel(status) { return {pending_review:'核對完成，進入回簽',pending_signature:'記錄已回簽',returned:'確認接單',accepted:'轉正式工單'}[status] || ''; }
  function formatWeight(value) { var kg = Number(value || 0); return kg >= 1000 ? (kg / 1000).toFixed(2) + ' 噸' : kg.toFixed(2) + ' kg'; }
  function updatePreview() {
    var kg = logic.calculateWeightKg(form.elements.quantity.value, form.elements.unitWeight.value, form.elements.weightBasis.value);
    document.getElementById('weightPreview').textContent = formatWeight(kg);
  }
  function renderSummary() {
    var summary = logic.summarize(orders, monthInput.value);
    var cards = [
      ['收到訂單', summary.received + ' 張'],
      ['待回簽', summary.pendingSignature + ' 張'],
      ['確認接單', summary.accepted + ' 張'],
      ['接單總支數', summary.acceptedPieces.toLocaleString('zh-TW')],
      ['接單淨重', formatWeight(summary.acceptedWeightKg)],
      ['預計生產量', formatWeight(summary.scheduledWeightKg)]
    ];
    document.getElementById('summaryCards').innerHTML = cards.map(function (card) { return '<div class="summary-card"><span>'+card[0]+'</span><b>'+card[1]+'</b></div>'; }).join('');
  }
  function renderOrders() {
    var list = document.getElementById('orderList');
    if (!orders.length) { list.innerHTML = '<div class="empty">尚無測試訂單。先在上方建立一張待核對訂單。</div>'; return; }
    list.innerHTML = orders.slice().reverse().map(function (order) {
      var next = nextLabel(order.status);
      var source = {photo:'拍照／掃描／傳真',email:'Email附件',excel:'Excel／CSV',manual:'手動輸入'}[order.source] || order.source;
      var action = next ? '<button class="action" data-id="'+esc(order.id)+'" data-action="advance">'+esc(next)+'</button>' : '';
      var cancel = order.status !== 'cancelled' && order.status !== 'converted' ? '<button class="action cancel" data-id="'+esc(order.id)+'" data-action="cancel">取消訂單</button>' : '';
      var work = order.workOrderId ? '<div class="work-order">✅ 內部工單：<b>'+esc(order.workOrderId)+'</b>｜QR識別：<b>'+esc(order.qrToken)+'</b><br><small>測試版只建立識別資料，尚未連接正式QR列印服務。</small></div>' : '';
      return '<article class="order-card"><div class="order-head"><h3>'+esc(order.customer)+'｜'+esc(order.customerPo)+' v'+esc(order.version)+'</h3><span class="status '+esc(order.status)+'">'+esc(statusLabel(order.status))+'</span></div><div class="order-meta"><div><span>產品</span><b>'+esc(order.item)+'</b></div><div><span>支數</span><b>'+Number(order.quantity).toLocaleString('zh-TW')+'</b></div><div><span>淨重</span><b>'+formatWeight(order.weightKg)+'</b></div><div><span>交貨日</span><b>'+esc(order.deliveryDate)+'</b></div><div><span>來源</span><b>'+esc(source)+'</b></div><div><span>附件</span><b>'+esc(order.fileName || '無')+'</b></div></div><div class="order-actions">'+action+cancel+'</div>'+work+(order.status==='pending_signature'?'<div class="danger-note">回簽屬於對外承諾；本測試頁不會自動寄送或傳真。</div>':'')+'</article>';
    }).join('');
  }
  function render() { renderSummary(); renderOrders(); }

  form.addEventListener('input', updatePreview);
  form.addEventListener('submit', function (event) {
    event.preventDefault();
    var data = new FormData(form);
    var order = {
      id: 'INT-' + Date.now(),
      source: selectedSource(),
      fileName: document.getElementById('sourceFile').files[0] ? document.getElementById('sourceFile').files[0].name : '',
      customer: data.get('customer'), customerPo: data.get('customerPo'), version: data.get('version'),
      receivedAt: data.get('receivedAt'), item: data.get('item'), deliveryDate: data.get('deliveryDate'),
      quantity: Number(data.get('quantity')), unitWeight: Number(data.get('unitWeight')), weightBasis: data.get('weightBasis'),
      packaging: data.get('packaging'), notes: data.get('notes'), status: 'pending_review'
    };
    order.weightKg = logic.calculateWeightKg(order.quantity, order.unitWeight, order.weightBasis);
    var duplicate = orders.some(function (existing) { return existing.status !== 'cancelled' && logic.fingerprint(existing) === logic.fingerprint(order); });
    var message = document.getElementById('formMessage');
    if (duplicate) { message.className = 'form-message error'; message.textContent = '此客戶訂單號與版本已存在，為避免重複統計，未再建立。'; return; }
    orders.push(order); save(); render();
    message.className = 'form-message ok'; message.textContent = '已建立待核對訂單；尚未回簽，也尚未列入正式接單量。';
    form.reset(); form.elements.version.value = '1'; form.elements.receivedAt.value = now.toISOString().slice(0, 10); updatePreview();
    document.getElementById('fileName').textContent = '尚未選擇檔案';
  });
  document.getElementById('sourceFile').addEventListener('change', function () { document.getElementById('fileName').textContent = this.files[0] ? this.files[0].name : '尚未選擇檔案'; });
  monthInput.addEventListener('change', renderSummary);
  document.getElementById('orderList').addEventListener('click', function (event) {
    var button = event.target.closest('button[data-id]'); if (!button) return;
    var order = orders.find(function (item) { return item.id === button.dataset.id; }); if (!order) return;
    if (button.dataset.action === 'cancel') { order.status = 'cancelled'; }
    if (button.dataset.action === 'advance') {
      order.status = logic.nextStatus(order.status);
      if (order.status === 'returned') order.returnedAt = new Date().toISOString();
      if (order.status === 'accepted') order.acceptedAt = new Date().toISOString();
      if (order.status === 'converted') {
        var priorConverted = orders.filter(function (item) { return item.status === 'converted' && item.id !== order.id; }).length;
        order.workOrderId = logic.createWorkOrderId(order, priorConverted + 1);
        order.qrToken = order.workOrderId + '-' + Math.random().toString(36).slice(2, 8).toUpperCase();
      }
    }
    save(); render();
  });
  document.getElementById('clearDemo').addEventListener('click', function () {
    if (!window.confirm('只清除此瀏覽器的測試訂單，不影響任何正式資料。確定清除嗎？')) return;
    orders = []; save(); render();
  });
  updatePreview(); render();
}());
