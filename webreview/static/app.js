let PRODUCTS = [];
let DECISIONS = {};         // key -> {status, url}
let FILTER = 'unreviewed';
let ORDERS = [];            // [{key, orderCode, itemCode, size, qty, supplier, name, supplierUrl, ordered}]
let ORDERED = {};           // key -> true (ordered/objednané)
let ORDER_SUPPLIER = 'all';
let ACTIVE_TAB = localStorage.getItem('tab') || 'review';
const expanded = new Set(); // keys whose resolution panel is open (transient, NOT saved)

const imgObserver = new IntersectionObserver((entries) => {
  for (const e of entries) {
    if (e.isIntersecting) { loadInfo(e.target); imgObserver.unobserve(e.target); }
  }
}, { rootMargin: '300px' });

async function loadInfo(box) {
  const url = box.dataset.url;
  if (!url) { box.classList.remove('loading'); return; }
  try {
    const j = await (await fetch('/api/images?url=' + encodeURIComponent(url))).json();
    box.classList.remove('loading');
    box.innerHTML = '';
    if (!j.images || !j.images.length) { box.innerHTML = '<span class="noimg">bez obrázkov</span>'; }
    else for (const u of j.images) { const im = document.createElement('img'); im.src = u; im.loading = 'lazy'; box.appendChild(im); }
    if (box.dataset.titleId && j.title) {
      const t = document.getElementById(box.dataset.titleId); if (t) t.textContent = j.title;
    }
    if (box.dataset.metaId) {
      const mEl = document.getElementById(box.dataset.metaId);
      if (mEl) {
        const parts = [];
        if (j.price) parts.push('💶 ' + j.price + ' €');
        if (j.availability) parts.push(j.availability);
        mEl.textContent = parts.join(' · ');
      }
    }
  } catch (_) { box.classList.remove('loading'); }
}

let _tid = 0;
function gallery(url, titleNode, metaNode) {
  const b = el('div', 'imgs loading'); b.dataset.url = url;
  if (titleNode) { const id = 'ti' + (++_tid); titleNode.id = id; b.dataset.titleId = id; }
  if (metaNode) { const id = 'me' + (++_tid); metaNode.id = id; b.dataset.metaId = id; }
  imgObserver.observe(b); return b;
}
function smallThumb(url, metaNode) {
  const b = el('div', 'thumb loading'); b.dataset.url = url;
  if (metaNode) { const id = 'me' + (++_tid); metaNode.id = id; b.dataset.metaId = id; }
  imgObserver.observe(b); return b;
}

async function saveDecision(p, status, url) {
  if (status === 'undo') delete DECISIONS[p.key];
  else DECISIONS[p.key] = { status, url: url || '' };
  expanded.delete(p.key);   // collapse panel; card now lands in its list
  render();
  await fetch('/api/decision', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key: p.key, status, url: url || '' })
  });
}

function statusOf(p) { const d = DECISIONS[p.key]; return d ? d.status : null; }
function decUrl(p) { const d = DECISIONS[p.key]; return d ? d.url : ''; }

function matchesFilter(p) {
  const s = statusOf(p);
  switch (FILTER) {
    case 'all': return true;
    case 'unreviewed': return s === null;
    case 'matched': return p.ai_status === 'matched';
    case 'unmatched': return p.ai_status === 'unmatched';
    case 'st1': return p.current && p.current.state === 1;
    case 'st2': return p.current && p.current.state === 2;
    case 'st3': return p.current && p.current.state === 3;
    case 'good': return s === 'good' || s === 'manual';
    case 'unavailable': return s === 'unavailable' || s === 'discontinued';
    default: return true;
  }
}

function el(tag, cls, html) { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }
function escapeHtml(s) { return (s || '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
function badge(s) {
  const t = { good: '✓ Dobré', manual: '✓ Vybraný link',
    unavailable: '📦 Nie je skladom', discontinued: '🚫 Nebude sa predávať' }[s];
  return t ? el('span', 'badge ' + s, t) : null;
}

// supplier block: title (lazy for manual links), url link, image gallery
function supplierBlock(container, p, url, showReason) {
  const cand = p.candidates.find(c => c.url === url);
  const title = el('div', 'pname', cand ? escapeHtml(cand.name || '(produkt)') : 'načítavam názov…');
  container.appendChild(title);
  const a = el('a', 'supurl'); a.href = url; a.target = '_blank'; a.rel = 'noopener'; a.textContent = url;
  container.appendChild(a);
  const meta = el('div', 'supmeta', 'cena/sklad…');
  container.appendChild(meta);
  if (showReason && p.ai_reason && p.ai_status === 'matched') container.appendChild(el('div', 'reason', '🤖 ' + escapeHtml(p.ai_reason)));
  container.appendChild(gallery(url, cand ? null : title, meta));   // lazy title/price/avail
}

// candidates + manual URL + Nedostupné. Saving here moves the card to its list.
function resolutionPanel(p) {
  const wrap = el('div', 'panel');
  const cur = decUrl(p), s = statusOf(p);
  p.candidates.forEach((c) => {
    const row = el('div', 'cand');
    const m = el('div', 'c-main');
    m.appendChild(el('div', 'c-name', escapeHtml(c.name || '(produkt)')));
    const a = el('a', 'supurl'); a.href = c.url; a.target = '_blank'; a.rel = 'noopener'; a.textContent = c.url;
    m.appendChild(a);
    const meta = el('div', 'supmeta', '');
    m.appendChild(meta);
    row.appendChild(smallThumb(c.url, meta));
    row.appendChild(m);
    const pick = el('button', 'btn good sm' + (s === 'manual' && cur === c.url ? ' active' : ''), 'Vybrať');
    pick.onclick = () => saveDecision(p, 'manual', c.url);
    row.appendChild(pick); wrap.appendChild(row);
  });
  const mr = el('div', 'manualrow');
  const inp = el('input'); inp.type = 'url'; inp.placeholder = 'Vlož vlastnú URL dodávateľa…';
  if (s === 'manual' && !p.candidates.some(c => c.url === cur)) inp.value = cur;
  const save = el('button', 'btn good sm', 'Uložiť URL');
  save.onclick = () => { const v = inp.value.trim(); if (v.startsWith('http')) saveDecision(p, 'manual', v); };
  mr.appendChild(inp); mr.appendChild(save); wrap.appendChild(mr);
  const states = el('div', 'staterow');
  const b2 = el('button', 'btn warn sm' + (s === 'unavailable' ? ' active' : ''), '📦 Nie je skladom');
  b2.title = 'visible + Vypredané, stock 0 — dočasne, ostáva na re-kontrolu';
  b2.onclick = () => saveDecision(p, 'unavailable', '');
  const b3 = el('button', 'btn ghost sm' + (s === 'discontinued' ? ' active' : ''), '🚫 Už sa nebude predávať');
  b3.title = 'detailOnly + Predaj výrobku skončil — link ostane pre Google';
  b3.onclick = () => saveDecision(p, 'discontinued', '');
  states.appendChild(b2); states.appendChild(b3);
  wrap.appendChild(states);
  return wrap;
}

function renderCard(p) {
  const s = statusOf(p);
  const exp = expanded.has(p.key);
  const card = el('div', 'card' + (s ? ' ' + s : '') + (p.current && p.current.off ? ' curoff' : ''));

  // LEFT — our product
  const left = el('div', 'side left');
  left.appendChild(el('div', 'label', 'Náš produkt'));
  left.appendChild(el('div', 'pname', escapeHtml(p.name)));
  const oa = el('a', 'supurl');
  oa.href = p.our_url || ('https://www.forestshop.sk/vyhladavanie/?string=' + encodeURIComponent(p.name));
  oa.target = '_blank'; oa.rel = 'noopener';
  oa.textContent = p.our_url ? '↗ otvoriť náš produkt na forestshop.sk' : '↗ nájsť náš produkt na forestshop.sk';
  left.appendChild(oa);
  left.appendChild(el('div', 'meta', `${p.supplier} · pairCode ${p.pairCode || '—'} · ${p.variant_codes.length} variant(ov)`));
  if (p.current && p.current.state) {
    const lbl = { 1: '🟢 Skladom', 2: '📦 Nie je skladom', 3: '🚫 Už sa nebude predávať' }[p.current.state];
    const cls = { 1: 'st1', 2: 'st2', 3: 'st3' }[p.current.state];
    left.appendChild(el('span', 'curbadge ' + cls, 'teraz u nás: ' + lbl));
  }
  if (p.current && (p.current.price || p.current.stock !== '')) {
    const cp = p.current, parts = [];
    if (cp.price) parts.push('💶 ' + cp.price + ' €');
    if (cp.std && cp.std !== cp.price) parts.push('pôv. ' + cp.std + ' €');
    if (cp.stock !== undefined && cp.stock !== '') parts.push('sklad: ' + cp.stock);
    if (cp.avail) parts.push(cp.avail);
    if (parts.length) left.appendChild(el('div', 'priceline', parts.join(' · ')));
  }
  const oimgs = el('div', 'imgs');
  if (p.our_images.length) for (const u of p.our_images) { const im = el('img'); im.src = u; im.loading = 'lazy'; oimgs.appendChild(im); }
  else oimgs.innerHTML = '<span class="noimg">bez obrázkov</span>';
  left.appendChild(oimgs);
  card.appendChild(left);

  // RIGHT — supplier / decision
  const right = el('div', 'side right');
  right.appendChild(el('div', 'label', 'Dodávateľ'));
  const bg = badge(s); if (bg) right.appendChild(bg);

  if (s === 'unavailable' || s === 'discontinued') {
    right.appendChild(el('div', 'reason', s === 'unavailable'
      ? '📦 Nie je skladom → import: visible + Vypredané (stock 0). Ostáva na re-kontrolu.'
      : '🚫 Už sa nebude predávať → import: detailOnly + Predaj výrobku skončil (link ostane pre Google).'));
    const back = el('button', 'btn ghost sm', '↩ Vrátiť');
    back.onclick = () => saveDecision(p, 'undo');
    right.appendChild(back);
  } else if (s === 'good' || s === 'manual') {
    supplierBlock(right, p, s === 'good' ? p.ai_chosen_url : decUrl(p), s === 'good');
    const act = el('div', 'actions');
    const change = el('button', 'btn ghost sm', '✗ Zmeniť / iný link');
    change.onclick = () => { expanded.add(p.key); render(); };
    act.appendChild(change); right.appendChild(act);
    if (exp) right.appendChild(resolutionPanel(p));
  } else if (p.ai_status === 'matched' && !exp) {
    supplierBlock(right, p, p.ai_chosen_url, true);
    const act = el('div', 'actions');
    const g = el('button', 'btn good', '✓ Dobré');
    g.onclick = () => saveDecision(p, 'good', p.ai_chosen_url);
    const bad = el('button', 'btn bad', '✗ Zlé');
    bad.onclick = () => { expanded.add(p.key); render(); };   // only reveals options, does NOT move card
    act.appendChild(g); act.appendChild(bad); right.appendChild(act);
  } else {
    if (p.ai_status === 'unmatched' && p.ai_reason) right.appendChild(el('div', 'reason', '🤖 AI nenašla istú zhodu: ' + escapeHtml(p.ai_reason)));
    right.appendChild(resolutionPanel(p));
  }
  card.appendChild(right);
  return card;
}

const FILTERS = [
  ['unreviewed', 'Nezrevidované'], ['matched', 'Napárované (AI)'], ['unmatched', 'Nenapárované'],
  ['st1', '🟢 Skladom'], ['st2', '📦 Nie skladom'], ['st3', '🚫 Nepredáva sa'],
  ['good', '✓ Dobré/Vybrané'], ['unavailable', '⛔ Vyriešené-vypnuté'], ['all', 'Všetky'],
];

function renderFilters() {
  const f = document.getElementById('filters'); f.innerHTML = '';
  for (const [key, lbl] of FILTERS) {
    const bt = el('button', FILTER === key ? 'active' : '', lbl);
    bt.onclick = () => { FILTER = key; localStorage.setItem('filter', key); window.scrollTo(0, 0); render(); };
    f.appendChild(bt);
  }
}

// ---- Na objednanie (to-order) tab ---------------------------------------- //
const TABS = [['review', '🔍 Kontrola párovania'], ['toorder', '📋 Na objednanie']];

function renderTabs() {
  const t = document.getElementById('tabs'); if (!t) return;
  t.innerHTML = '';
  for (const [key, lbl] of TABS) {
    const bt = el('button', 'tab' + (ACTIVE_TAB === key ? ' active' : ''), lbl);
    bt.onclick = () => switchTab(key);
    t.appendChild(bt);
  }
}

async function switchTab(tab) {
  ACTIVE_TAB = tab; localStorage.setItem('tab', tab); window.scrollTo(0, 0);
  if (tab === 'toorder' && !ORDERS.length) await loadOrders();
  render();
}

async function loadOrders() {
  try {
    ORDERS = (await (await fetch('/api/orders')).json()).orders || [];
    ORDERED = (await (await fetch('/api/ordered')).json()).ordered || {};
  } catch (_) { ORDERS = []; ORDERED = {}; }
}

async function saveOrdered(key, ordered) {
  if (ordered) ORDERED[key] = true; else delete ORDERED[key];
  await fetch('/api/ordered', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key, ordered })
  });
}

// Inline pairing: paste the supplier reorder URL straight onto an order line.
// Persists per forestshop code (covers items outside the review dataset too).
async function savePairUrl(o, url, row) {
  if (url && !/^https?:\/\//.test(url)) return;   // ignore non-URL input
  const r = await fetch('/api/order-pair', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code: o.itemCode, url })
  });
  if (!r.ok) return;
  o.pairUrl = url;                       // re-render so the new link shows immediately
  row.replaceWith(renderOrderRow(o));
}

function renderOrderRow(o) {
  const row = el('div', 'toorder-row' + (ORDERED[o.key] ? ' done' : ''));
  row.dataset.key = o.key; row.dataset.code = o.itemCode;
  const cb = el('input'); cb.type = 'checkbox'; cb.checked = !!ORDERED[o.key];
  cb.title = 'Označiť ako objednané';
  cb.onchange = () => { saveOrdered(o.key, cb.checked); row.classList.toggle('done', cb.checked); };
  row.appendChild(cb);
  if (o.supplierUrl) {
    // reviewed decision link — authoritative, shown read-only
    const a = el('a', 'to-link'); a.href = o.supplierUrl; a.target = '_blank'; a.rel = 'noopener';
    a.textContent = '🔗 ' + (o.itemCode || 'link');
    row.appendChild(a);
  } else {
    // no reviewed decision → inline pairing field: paste the supplier reorder URL
    // right here (he opens the product to order it anyway). Saved → shows as a link
    // and goes into the import.
    const pair = el('div', 'to-pair');
    pair.appendChild(el('span', 'to-pcode', escapeHtml(o.itemCode || '')));
    if (o.pairUrl) {
      const a = el('a', 'to-link'); a.href = o.pairUrl; a.target = '_blank'; a.rel = 'noopener';
      a.textContent = '🔗'; a.title = o.pairUrl;
      pair.appendChild(a);
    }
    const inp = el('input', 'to-pairurl'); inp.type = 'url';
    inp.placeholder = o.pairUrl ? 'upraviť párovaciu URL…' : 'vlož párovaciu URL dodávateľa…';
    inp.value = o.pairUrl || '';
    const save = el('button', 'to-pairsave', o.pairUrl ? '💾' : '💾 Spárovať');
    save.title = 'Uložiť párovaciu URL — objaví sa ako odkaz a pôjde do importu';
    const doSave = () => savePairUrl(o, inp.value.trim(), row);
    save.onclick = doSave;
    inp.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); doSave(); } };
    pair.appendChild(inp); pair.appendChild(save);
    row.appendChild(pair);
  }
  row.appendChild(el('span', 'to-size', escapeHtml(o.size || '')));
  row.appendChild(el('span', 'to-qty', (o.qty || '1') + ' ks'));
  row.appendChild(el('span', 'to-name', escapeHtml(o.name || '')));
  if (o.orderCode) {
    const oa = el('a', 'to-order');
    oa.href = 'https://www.forestshop.sk/admin/objednavky-detail/?code=' + encodeURIComponent(o.orderCode);
    oa.target = '_blank'; oa.rel = 'noopener';
    oa.textContent = '📋 obj. ' + o.orderCode;
    oa.title = 'Otvoriť objednávku ' + o.orderCode + ' v admine';
    row.appendChild(oa);
  }
  return row;
}

function renderToOrder() {
  const cnt = {};
  for (const o of ORDERS) { const s = o.supplier || '—'; cnt[s] = (cnt[s] || 0) + 1; }
  const fbar = document.getElementById('filters'); fbar.innerHTML = '';
  const mk = (key, lbl) => {
    const b = el('button', ORDER_SUPPLIER === key ? 'active' : '', lbl);
    b.onclick = () => { ORDER_SUPPLIER = key; localStorage.setItem('orderSupplier', key); window.scrollTo(0, 0); render(); };
    return b;
  };
  fbar.appendChild(mk('all', `Všetci (${ORDERS.length})`));
  for (const s of Object.keys(cnt).sort()) fbar.appendChild(mk(s, `${s} (${cnt[s]})`));
  const list = document.getElementById('list'); list.innerHTML = '';
  const shown = ORDERS.filter(o => ORDER_SUPPLIER === 'all' || (o.supplier || '—') === ORDER_SUPPLIER);
  document.getElementById('empty').hidden = shown.length > 0;
  const groups = {};
  for (const o of shown) { const s = o.supplier || '—'; (groups[s] = groups[s] || []).push(o); }
  for (const sup of Object.keys(groups).sort()) {
    list.appendChild(el('div', 'toorder-supplier', `${sup} — ${groups[sup].length} položiek`));
    for (const o of groups[sup]) list.appendChild(renderOrderRow(o));
  }
}

function render() {
  renderTabs();
  const toorder = ACTIVE_TAB === 'toorder';
  const prog = document.querySelector('.progress'); if (prog) prog.style.display = toorder ? 'none' : '';
  const dls = document.querySelector('.downloads'); if (dls) dls.style.display = toorder ? 'none' : '';
  if (toorder) { renderToOrder(); return; }
  const keepY = window.scrollY;
  renderFilters();
  const reviewed = Object.keys(DECISIONS).length;
  document.getElementById('progressText').textContent = `${reviewed} / ${PRODUCTS.length} skontrolovaných`;
  document.getElementById('progressBar').style.width = (100 * reviewed / PRODUCTS.length) + '%';
  const list = document.getElementById('list'); list.innerHTML = '';
  const shown = PRODUCTS.filter(matchesFilter);
  document.getElementById('empty').hidden = shown.length > 0;
  for (const p of shown) list.appendChild(renderCard(p));
  const dl = document.getElementById('dlImport');
  if (dl) {
    const n = Object.values(DECISIONS).filter(d =>
      ((d.status === 'good' || d.status === 'manual') && d.url) || d.status === 'unavailable').length;
    dl.textContent = `⬇ Stiahnuť import (${n})`;
  }
  window.scrollTo(0, keepY);
}

let _scrollTimer;
window.addEventListener('scroll', () => {
  clearTimeout(_scrollTimer);
  _scrollTimer = setTimeout(() => localStorage.setItem('scrollY', String(window.scrollY)), 150);
});

async function loadVersion() {
  try {
    const v = await (await fetch('/api/version')).text();
    const el = document.getElementById('version');
    if (el) el.textContent = v.trim();
  } catch (_) { /* version label is non-critical */ }
}

async function init() {
  loadVersion();
  const j = await (await fetch('/api/products')).json();
  PRODUCTS = j.products;
  DECISIONS = j.decisions || {};
  PRODUCTS.sort((a, b) =>
    ((a.ai_status === 'unmatched') ? 1 : 0) - ((b.ai_status === 'unmatched') ? 1 : 0) || a.idx - b.idx);
  FILTER = localStorage.getItem('filter') || 'unreviewed';
  ORDER_SUPPLIER = localStorage.getItem('orderSupplier') || 'all';
  // ?tab=toorder — Discord posts a link straight to the to-order list
  const qTab = new URLSearchParams(location.search).get('tab');
  if (qTab === 'toorder' || qTab === 'review') { ACTIVE_TAB = qTab; localStorage.setItem('tab', qTab); }
  if (ACTIVE_TAB === 'toorder') await loadOrders();
  render();
  const y = parseInt(localStorage.getItem('scrollY') || '0', 10);
  if (y) window.scrollTo(0, y);
}
init();
