let ME = null;              // {email, is_admin} — logged-in user (#91)
let USERS_LIST = [];        // admin 'Užívatelia' tab
let PRODUCTS = [];
let DECISIONS = {};         // key -> {status, url}
let FILTER = 'unreviewed';
let ORDERS = [];            // [{key, orderCode, itemCode, size, qty, supplier, name, supplierUrl, ordered, assignedSupplier}]
let ORDERED = {};           // key -> true (ordered/objednané)
let WAITING = {};           // key -> true (čaká sa — deferred active line)
let INSTOCK = {};           // key -> true (skladom — máme/naskladnené)
let UNAVAIL = {};           // key -> true (nedostupné — u dodávateľa)
let NOTES = [];             // [{id, text, done, ts}] — 'Poznámky' tab
let AUTOMATIONS = [];       // /api/automations — in-app runner statuses (#93)
let POSTA = null;           // /api/posta-uncollected — last run's display data
let ORDER_SUPPLIER = 'all';
let ACTIVE_TAB = localStorage.getItem('tab') || 'review';
const expanded = new Set(); // keys whose resolution panel is open (transient, NOT saved)

// Session-expiry guard (#91): ANY api 401 → back to the login page. The server
// gate protects the data; this just swaps a dead UI for the login form.
const _origFetch = window.fetch.bind(window);
window.fetch = async (...args) => {
  const r = await _origFetch(...args);
  if (r.status === 401) location.href = '/login';
  return r;
};

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
function fmtDate(iso) { const p = (iso || '').split('-'); return p.length === 3 ? `${p[2]}.${p[1]}.${p[0]}` : (iso || ''); }  // 2026-04-24 → 24.04.2026
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
    // '✗ Zlé' expanded to 3 direct one-click actions (same status strings/calls the
    // resolutionPanel uses — surfaced on the card so no panel-open is needed first):
    const pick = el('button', 'btn ghost sm', 'vyber url');
    pick.onclick = () => { expanded.add(p.key); render(); };   // opens panel to pick/paste a URL — does NOT move card
    const unav = el('button', 'btn warn sm', '📦 Nie je skladom');
    unav.title = 'visible + Vypredané, stock 0 — dočasne, ostáva na re-kontrolu';
    unav.onclick = () => saveDecision(p, 'unavailable', '');
    const disc = el('button', 'btn ghost sm', '🚫 Už sa nebude predávať');
    disc.title = 'detailOnly + Predaj výrobku skončil — link ostane pre Google';
    disc.onclick = () => saveDecision(p, 'discontinued', '');
    act.appendChild(g); act.appendChild(pick); act.appendChild(unav); act.appendChild(disc);
    right.appendChild(act);
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

// ---- Sidebar nav + page head --------------------------------------------- //
// Nav labels carry NO emoji — the outline SVG icon replaces it (moderný sidebar).
// The accessible name stays the plain label ("Na objednanie", "Hľadať / opraviť"),
// which the E2E tests match on, so an added count badge doesn't break them.
const TABS = [['review', 'Kontrola párovania'], ['toorder', 'Na objednanie'],
  ['search', 'Hľadať / opraviť'], ['notes', 'Poznámky']];

// In-app automations (#93) — each gets its own nav item in the 'Automatizácie'
// sidebar section (#autoTabs) + its own tab section. New automations: add here.
const AUTOMATION_TABS = [['posta', 'Nevyzdvihnuté zásielky']];

const NAV_ICONS = {
  review: '<path d="M9 12l2 2 4-4"/><circle cx="12" cy="12" r="9"/>',
  toorder: '<path d="M9 5h6M9 9h6M9 13h4"/><rect x="4" y="3" width="16" height="18" rx="2"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/>',
  notes: '<path d="M4 4h16v12l-4 4H4z"/><path d="M16 20v-4h4"/>',
  users: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 5-6 8-6s6.5 2 8 6"/>',
  posta: '<path d="M21 8l-9-5-9 5v8l9 5 9-5z"/><path d="M3 8l9 5 9-5"/><path d="M12 13v8"/>',
};

// 'Užívatelia' is an ADMIN-ONLY nav item (the server 403s non-admins anyway).
function visibleTabs() {
  return (ME && ME.is_admin) ? TABS.concat([['users', 'Užívatelia']]) : TABS;
}

// count badge per nav item — review: still-unreviewed, toorder: open lines, notes: count
function navCount(key) {
  if (key === 'review') return PRODUCTS.filter(p => !statusOf(p)).length;
  if (key === 'toorder') return ORDERS.length;
  if (key === 'notes') return NOTES.length;
  if (key === 'users') return USERS_LIST.length;
  if (key === 'posta') return POSTA ? (POSTA.uncollected || []).length : 0;
  return 0;
}

function _navButton(key, lbl) {
  const bt = el('button', 'tab' + (ACTIVE_TAB === key ? ' active' : ''));
  const n = navCount(key);
  bt.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${NAV_ICONS[key]}</svg>`
    + `<span class="tlabel">${escapeHtml(lbl)}</span>`
    + (n > 0 ? `<span class="navcount">${n}</span>` : '');
  bt.onclick = () => switchTab(key);
  return bt;
}

function renderTabs() {
  const t = document.getElementById('tabs'); if (!t) return;
  t.innerHTML = '';
  for (const [key, lbl] of visibleTabs()) t.appendChild(_navButton(key, lbl));
  const at = document.getElementById('autoTabs');
  if (at) {
    at.innerHTML = '';
    for (const [key, lbl] of AUTOMATION_TABS) at.appendChild(_navButton(key, lbl));
  }
}

// Top-bar per-page title + a plain-language subtitle (with live counts).
const PAGE_TITLES = {
  review: 'Kontrola párovania', toorder: 'Na objednanie',
  search: 'Hľadať / opraviť', notes: 'Poznámky', users: 'Užívatelia',
  posta: 'Nevyzdvihnuté zásielky',
};
function setPageHead() {
  const h = document.getElementById('pageTitle');
  if (h) h.textContent = PAGE_TITLES[ACTIVE_TAB] || '';
  const s = document.getElementById('pageSub'); if (!s) return;
  if (ACTIVE_TAB === 'review') {
    const un = PRODUCTS.filter(p => !statusOf(p)).length;
    s.textContent = `${PRODUCTS.length} produktov · ${un} čaká na kontrolu`;
  } else if (ACTIVE_TAB === 'toorder') {
    s.textContent = `${ORDERS.length} otvorených položiek u dodávateľov`;
  } else if (ACTIVE_TAB === 'search') {
    s.textContent = 'Prehľadá všetky polia všetkých produktov';
  } else if (ACTIVE_TAB === 'notes') {
    s.textContent = `${NOTES.length} poznámok`;
  } else if (ACTIVE_TAB === 'users') {
    s.textContent = `${USERS_LIST.length} účtov s prístupom`;
  } else if (ACTIVE_TAB === 'posta') {
    const n = POSTA ? (POSTA.uncollected || []).length : 0;
    s.textContent = `${n} zásielok čaká na pošte · automatická kontrola + upozornenia zákazníkom`;
  } else { s.textContent = ''; }
}

// Dark mode: [data-theme=dark] on <body>, persisted in localStorage('theme').
function applyTheme(theme) {
  if (theme === 'dark') document.body.setAttribute('data-theme', 'dark');
  else document.body.removeAttribute('data-theme');
  const btn = document.getElementById('themeBtn');
  if (btn) btn.setAttribute('aria-pressed', theme === 'dark' ? 'true' : 'false');
  const lbl = document.getElementById('themeLbl');
  if (lbl) lbl.textContent = theme === 'dark' ? 'Svetlý mód' : 'Tmavý mód';
  const ic = document.getElementById('themeIcon');
  if (ic) ic.innerHTML = theme === 'dark'
    ? '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'
    : '<path d="M21 12.8A9 9 0 1111.2 3 7 7 0 0021 12.8z"/>';
}
function initTheme() {
  applyTheme(localStorage.getItem('theme') || 'light');
  const b = document.getElementById('themeBtn'); if (!b) return;
  b.onclick = () => {
    const next = document.body.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    localStorage.setItem('theme', next);
    applyTheme(next);
  };
}

async function switchTab(tab) {
  ACTIVE_TAB = tab; localStorage.setItem('tab', tab); window.scrollTo(0, 0);
  if (tab === 'toorder' && !ORDERS.length) await loadOrders();
  if (tab === 'notes' && !NOTES.length) await loadNotes();
  if (tab === 'users') await loadUsers();   // always fresh — small list
  if (tab === 'posta') await loadPosta();   // always fresh — status can change
  render();
  if (tab === 'search') { const b = document.getElementById('searchBox'); if (b) b.focus(); }
}

async function loadOrders() {
  try {
    ORDERS = (await (await fetch('/api/orders')).json()).orders || [];
    ORDERED = (await (await fetch('/api/ordered')).json()).ordered || {};
    WAITING = (await (await fetch('/api/waiting')).json()).waiting || {};
    INSTOCK = (await (await fetch('/api/instock')).json()).instock || {};
    UNAVAIL = (await (await fetch('/api/unavailable')).json()).unavailable || {};
  } catch (_) { ORDERS = []; ORDERED = {}; WAITING = {}; INSTOCK = {}; UNAVAIL = {}; }
}

async function saveOrdered(key, ordered) {
  if (ordered) ORDERED[key] = true; else delete ORDERED[key];
  await fetch('/api/ordered', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key, ordered })
  });
}

async function saveWaiting(key, waiting) {
  if (waiting) WAITING[key] = true; else delete WAITING[key];
  await fetch('/api/waiting', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key, waiting })
  });
}

async function saveInstock(key, instock) {
  if (instock) INSTOCK[key] = true; else delete INSTOCK[key];
  await fetch('/api/instock', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key, instock })
  });
}

async function saveUnavailable(key, unavailable) {
  if (unavailable) UNAVAIL[key] = true; else delete UNAVAIL[key];
  await fetch('/api/unavailable', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key, unavailable })
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

// inline-pairing editor (code + URL input + save) — used for an unpaired row and
// when ✏️-editing an already-paired one
function pairEditor(o, row, focus) {
  const pair = el('div', 'to-pair');
  pair.appendChild(el('span', 'to-pcode', escapeHtml(o.itemCode || '')));
  const inp = el('input', 'to-pairurl'); inp.type = 'url';
  inp.placeholder = o.pairUrl ? 'upraviť párovaciu URL…' : 'vlož párovaciu URL dodávateľa…';
  inp.value = o.pairUrl || '';
  const save = el('button', 'to-pairsave', '💾 Spárovať');
  save.title = 'Uložiť párovaciu URL — objaví sa ako odkaz a pôjde do importu';
  const doSave = () => savePairUrl(o, inp.value.trim(), row);
  save.onclick = doSave;
  inp.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); doSave(); } };
  pair.appendChild(inp); pair.appendChild(save);
  if (focus) setTimeout(() => inp.focus(), 0);
  return pair;
}

// effective supplier for grouping: a manually-assigned supplier wins over the
// order-given one (empty for lines that arrived without a supplier → '—')
const effSup = (o) => (o.assignedSupplier || o.supplier || '—');

// Inline supplier assign: fill in the supplier for an order line that arrived WITHOUT
// one. Persists per forestshop code; the row then regroups under that supplier and the
// name is written back to the eshop `supplier` field by the nightly upload.
async function saveSupplier(o, supplier, row) {
  const r = await fetch('/api/order-supplier', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code: o.itemCode, supplier })
  });
  if (!r.ok) return;
  // assignment is keyed by itemCode (a product property) → apply to EVERY order line
  // of that code, so all sibling lines regroup together (not just the clicked one)
  for (const x of ORDERS) if (x.itemCode === o.itemCode) x.assignedSupplier = supplier;
  renderToOrder();                 // re-render: the row(s) move into the supplier group
}

// supplier editor (text input with known-supplier autocomplete + save) — used for an
// unassigned no-supplier row and when ✏️-editing an already-assigned one
function supplierEditor(o, row, focus) {
  const wrap = el('div', 'to-supplier');
  const inp = el('input', 'to-supinput'); inp.type = 'text';
  inp.placeholder = o.assignedSupplier ? 'upraviť dodávateľa…' : 'doplniť dodávateľa…';
  inp.value = o.assignedSupplier || '';
  inp.setAttribute('list', 'known-suppliers');   // autocomplete from existing suppliers
  const save = el('button', 'to-supsave', '💾 Uložiť');
  save.title = 'Priradiť dodávateľa — položka sa zaradí pod neho a zapíše sa do eshopu';
  const doSave = () => saveSupplier(o, inp.value.trim(), row);
  save.onclick = doSave;
  inp.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); doSave(); } };
  wrap.appendChild(inp); wrap.appendChild(save);
  if (focus) setTimeout(() => inp.focus(), 0);
  return wrap;
}

function renderOrderRow(o) {
  const row = el('div', 'toorder-row' + (ORDERED[o.key] ? ' done' : '') + (WAITING[o.key] ? ' waiting' : '')
    + (INSTOCK[o.key] ? ' instock' : '') + (UNAVAIL[o.key] ? ' unavail' : ''));
  row.dataset.key = o.key; row.dataset.code = o.itemCode;
  const cb = el('input'); cb.type = 'checkbox'; cb.checked = !!ORDERED[o.key];
  cb.title = 'Označiť ako objednané';
  cb.onchange = () => { saveOrdered(o.key, cb.checked); row.classList.toggle('done', cb.checked); renderOrderFilters(); };
  row.appendChild(cb);
  if (o.supplierUrl) {
    // reviewed decision link — authoritative, read-only (mení sa v párovacom tabe)
    const a = el('a', 'to-link'); a.href = o.supplierUrl; a.target = '_blank'; a.rel = 'noopener';
    a.textContent = '🔗 ' + (o.itemCode || 'link');
    row.appendChild(a);
  } else if (o.pairUrl) {
    // inline-napárované → svieti rovnako ako ostatné napárované (🔗 odkaz) +
    // malá ✏️ na opravu, ak dal zlú URL
    const a = el('a', 'to-link'); a.href = o.pairUrl; a.target = '_blank'; a.rel = 'noopener';
    a.textContent = '🔗 ' + (o.itemCode || 'link'); a.title = o.pairUrl;
    row.appendChild(a);
    const edit = el('button', 'to-pairedit', '✏️');
    edit.title = 'Zmeniť / opraviť párovaciu URL';
    edit.onclick = () => { a.replaceWith(pairEditor(o, row, true)); edit.remove(); };
    row.appendChild(edit);
  } else {
    // nenapárované → políčko na vloženie URL (otvára produkt pri objednávaní)
    row.appendChild(pairEditor(o, row, false));
  }
  // GRUBE per-veľkosť kód: kopírovateľný čip + .de objednávacia linka. GRUBE nemá B2B
  // auto-objednávanie, takže manažér skopíruje presný veľkostný kód do e-mailu.
  if (o.grubeItemId) {
    const chip = el('span', 'to-grube');
    chip.textContent = o.grubeItemId;            // .textContent → auto-escaped, never innerHTML
    chip.title = 'Kopírovať grube kód';
    chip.onclick = () => navigator.clipboard && navigator.clipboard.writeText(o.grubeItemId);
    row.appendChild(chip);
    if (o.grubeDeUrl && /^https?:\/\//.test(o.grubeDeUrl)) {   // server + client guard: len http(s)
      const de = el('a', 'to-link');
      de.href = o.grubeDeUrl; de.target = '_blank'; de.rel = 'noopener';
      de.textContent = '🇩🇪 .de';
      row.appendChild(de);
    }
  }
  row.appendChild(el('span', 'to-size', escapeHtml(o.size || '')));
  row.appendChild(el('span', 'to-qty', (o.qty || '1') + ' ks'));
  row.appendChild(el('span', 'to-name', escapeHtml(o.name || '')));
  // supplier assign — ONLY for order lines that arrived WITHOUT a supplier. Same shape
  // as the URL pairing: doplniť → svieti názov + malá ✏️ na opravu.
  if (!o.supplier) {
    if (o.assignedSupplier) {
      const tag = el('span', 'to-suptag', '🏷️ ' + escapeHtml(o.assignedSupplier));
      tag.title = 'Doplnený dodávateľ (zapíše sa do eshopu)';
      row.appendChild(tag);
      const sed = el('button', 'to-supedit', '✏️');
      sed.title = 'Zmeniť / opraviť dodávateľa';
      sed.onclick = () => { tag.replaceWith(supplierEditor(o, row, true)); sed.remove(); };
      row.appendChild(sed);
    } else {
      row.appendChild(supplierEditor(o, row, false));
    }
  }
  if (o.orderDate) {
    const d = el('span', 'to-date', '📅 ' + fmtDate(o.orderDate));
    d.title = 'Dátum objednávky';
    row.appendChild(d);
  }
  if (o.orderCode) {
    const oa = el('a', 'to-order');
    oa.href = 'https://www.forestshop.sk/admin/objednavky-detail/?code=' + encodeURIComponent(o.orderCode);
    oa.target = '_blank'; oa.rel = 'noopener';
    oa.textContent = '📋 obj. ' + o.orderCode;
    oa.title = 'Otvoriť objednávku ' + o.orderCode + ' v admine';
    row.appendChild(oa);
  }
  // 'čaká sa' — aktívna objednávka, ktorú zatiaľ neobjednávame/naskladňujeme
  const w = el('button', 'to-wait' + (WAITING[o.key] ? ' on' : ''));
  w.textContent = WAITING[o.key] ? '⏳ Čaká sa' : '⏳ Počkať';
  w.title = 'Aktívna objednávka, ktorá sa zatiaľ nenaskladňuje — čaká sa na dodávateľa, '
    + 'zbierame viac položiek, alebo dohoda so zákazníkom (napr. september)';
  w.onclick = () => {
    const on = !WAITING[o.key];
    saveWaiting(o.key, on);
    w.textContent = on ? '⏳ Čaká sa' : '⏳ Počkať';
    w.classList.toggle('on', on);
    row.classList.toggle('waiting', on);
    renderOrderFilters();
  };
  row.appendChild(w);
  // 'skladom' — už máme / naskladnené, a 'nedostupné' — u dodávateľa nedostupné.
  // Independent toggles, same shape as 'čaká sa' (synchronous DOM update + async POST).
  const inStk = el('button', 'to-instock' + (INSTOCK[o.key] ? ' on' : ''), '✓ Skladom');
  inStk.title = 'Máme skladom / naskladnené';
  inStk.onclick = () => {
    const on = !INSTOCK[o.key];
    saveInstock(o.key, on);
    inStk.classList.toggle('on', on);
    row.classList.toggle('instock', on);
    renderOrderFilters();
  };
  row.appendChild(inStk);
  const unavailBtn = el('button', 'to-unavail' + (UNAVAIL[o.key] ? ' on' : ''), '✗ Nedostupné');
  unavailBtn.title = 'U dodávateľa nedostupné';
  unavailBtn.onclick = () => {
    const on = !UNAVAIL[o.key];
    saveUnavailable(o.key, on);
    unavailBtn.classList.toggle('on', on);
    row.classList.toggle('unavail', on);
    renderOrderFilters();
  };
  row.appendChild(unavailBtn);
  return row;
}

// A line is "poriešené" (resolved) once the manager put ANY flag on it — objednané /
// počkať / skladom / nedostupné. Read from the LIVE flag maps (ORDERED/WAITING/INSTOCK/
// UNAVAIL), which the toggle handlers update in place — NOT the o.* snapshot, which is
// frozen at /api/orders fetch time and would leave chips stale until a full reload (#86).
function isHandled(o) {
  return !!(ORDERED[o.key] || WAITING[o.key] || INSTOCK[o.key] || UNAVAIL[o.key]);
}

// Build the supplier filter chips for the Na-objednanie tab, coloured by resolved state:
// RED (done) = every one of the supplier's lines is flagged (nothing left to deal with),
// GREEN (todo) = at least one line still un-flagged, ORANGE (active) = the selected chip.
// Called by renderToOrder AND by every per-line flag toggle so the chips recolour LIVE as
// the manager works the list (each toggle updates a flag map, then re-renders this bar).
function renderOrderFilters() {
  const fbar = document.getElementById('filters');
  if (!fbar || ACTIVE_TAB !== 'toorder') return;
  const oNum = (o) => { const n = parseInt(o.orderCode, 10); return isNaN(n) ? -Infinity : n; };
  const cnt = {}, newest = {}, unhandled = {};
  for (const o of ORDERS) {
    const s = effSup(o);
    cnt[s] = (cnt[s] || 0) + 1;
    if (!isHandled(o)) unhandled[s] = (unhandled[s] || 0) + 1;
    newest[s] = Math.max(newest[s] ?? -Infinity, oNum(o));
  }
  const allHandledGlobal = ORDERS.length > 0 && ORDERS.every(isHandled);
  const byPriority = (a, b) => (newest[b] - newest[a]) || (a < b ? -1 : a > b ? 1 : 0);
  fbar.innerHTML = '';
  const mk = (key, lbl, done) => {
    const cls = (ORDER_SUPPLIER === key ? 'active ' : '') + (done ? 'done' : 'todo');
    const b = el('button', cls, lbl);
    b.onclick = () => { ORDER_SUPPLIER = key; localStorage.setItem('orderSupplier', key); window.scrollTo(0, 0); render(); };
    return b;
  };
  fbar.appendChild(mk('all', `Všetci (${ORDERS.length})`, allHandledGlobal));
  // escapeHtml: a supplier name is manually assignable (free text) → never trust it in
  // the innerHTML-based el() helper. done (RED) = no un-flagged line left; todo (GREEN) = some.
  for (const s of Object.keys(cnt).sort(byPriority)) fbar.appendChild(mk(s, `${escapeHtml(s)} (${cnt[s]})`, !unhandled[s]));
}

function renderToOrder() {
  // Najnovšie objednávky hore — Marek je tak naučený zo Shoptetu. Čísla objednávok sú
  // chronologické (vyššie = novšie); dodávateľ s NAJNOVŠOU objednávkou hore, v rámci
  // dodávateľa od najnovšej. Ne-číselné orderCode = -Infinity (nikdy nedominuje vrch).
  const oNum = (o) => { const n = parseInt(o.orderCode, 10); return isNaN(n) ? -Infinity : n; };
  // datalist of known supplier names (avoid typo-fragmented groups) — distinct real
  // suppliers seen across orders, both order-given and manually assigned
  const known = [...new Set(ORDERS.flatMap(o => [o.supplier, o.assignedSupplier]).filter(Boolean))].sort();
  let dl = document.getElementById('known-suppliers');
  if (!dl) { dl = el('datalist'); dl.id = 'known-suppliers'; document.body.appendChild(dl); }
  dl.innerHTML = '';
  for (const s of known) { const opt = document.createElement('option'); opt.value = s; dl.appendChild(opt); }
  const newest = {};
  for (const o of ORDERS) {
    const s = effSup(o);
    newest[s] = Math.max(newest[s] ?? -Infinity, oNum(o));
  }
  // dodávateľ s NAJNOVŠOU objednávkou hore; zhoda → abecedne
  const byPriority = (a, b) => (newest[b] - newest[a]) || (a < b ? -1 : a > b ? 1 : 0);
  renderOrderFilters();   // live-coloured supplier chips (recomputed from the flag maps)
  const list = document.getElementById('list'); list.innerHTML = '';
  const shown = ORDERS.filter(o => ORDER_SUPPLIER === 'all' || effSup(o) === ORDER_SUPPLIER);
  document.getElementById('empty').hidden = shown.length > 0;
  const groups = {};
  for (const o of shown) { const s = effSup(o); (groups[s] = groups[s] || []).push(o); }
  for (const sup of Object.keys(groups).sort(byPriority)) {
    groups[sup].sort((a, b) => oNum(b) - oNum(a));   // v rámci dodávateľa: najnovšia objednávka prvá
    list.appendChild(el('div', 'toorder-supplier', `${escapeHtml(sup)} — ${groups[sup].length} položiek`));
    for (const o of groups[sup]) list.appendChild(renderOrderRow(o));
  }
}

// ---- Hľadať / opraviť (catalog search + re-pair) tab --------------------- //
// Search the whole catalog (in-review AND not-yet-paired products) and re-pair
// straight from the result row: an in-review hit reuses the SAME resolutionPanel
// as the review tab; a not-in-review hit gets a manual-URL panel that promotes +
// pairs the product via /api/search-pair, flipping the badge in-place.
let SEARCH_T = null;     // debounce timer
let SEARCH_SEQ = 0;      // request token — drop stale responses (fast typing)

function initSearch() {
  const box = document.getElementById('searchBox');
  if (!box) return;
  box.addEventListener('input', () => {
    clearTimeout(SEARCH_T);
    SEARCH_T = setTimeout(() => runSearch(box.value), 250);
  });
}

async function runSearch(q) {
  const out = document.getElementById('searchResults');
  if (!out) return;
  if ((q || '').trim().length < 2) { out.innerHTML = ''; return; }   // <2 znaky → nič
  const seq = ++SEARCH_SEQ;
  let data;
  try {
    data = await (await fetch('/api/search?q=' + encodeURIComponent(q))).json();
  } catch (_) { return; }                       // network blip — keep the console clean
  if (seq !== SEARCH_SEQ) return;               // a newer query superseded this one
  out.innerHTML = '';
  const results = (data && data.results) || [];
  if (!results.length) { out.appendChild(el('div', 'srch-empty', 'Nič sa nenašlo.')); return; }
  for (const res of results) out.appendChild(renderSearchRow(res));
}

function searchBadge(res) {
  return res.in_review ? el('span', 'sbadge inreview', 'v appke')
                       : el('span', 'sbadge new', 'nenapárované');
}

// compact result row: thumb · name/meta/our-link · badge, with an inline panel below
function renderSearchRow(res) {
  const row = el('div', 'search-row');
  row.dataset.key = res.key;   // pairCode-or-code identity (empty-pairCode products keyed by code)

  const head = el('div', 'srch-head');
  const thumb = el('div', 'srch-thumb');
  if (res.image) {
    const im = el('img'); im.src = res.image; im.loading = 'lazy'; im.alt = '';
    // broken catalog CDN image (404) → degrade to the same 'bez obrázka' placeholder
    // instead of a broken-image icon + a dirty console error
    im.onerror = () => im.replaceWith(el('span', 'noimg', 'bez obrázka'));
    thumb.appendChild(im);
  } else thumb.appendChild(el('span', 'noimg', 'bez obrázka'));
  head.appendChild(thumb);

  const main = el('div', 'srch-main');
  const nm = el('div', 'srch-name'); nm.textContent = res.name || '(produkt)';   // .textContent → XSS-safe
  main.appendChild(nm);
  const meta = el('div', 'srch-meta');
  meta.textContent = (res.supplier || '—') + ' · '
    + ((res.codes || []).join(', ') || 'bez kódu');
  main.appendChild(meta);
  // commerce line — NAŠA cena + eshop stav (rovnaké labely ako filtre) + sklad;
  // toto bolo manažérovo „nie sú tam skoro žiadne údaje"
  const comm = el('div', 'srch-comm');
  if (res.price) {
    const pr = el('span', 'srch-price');
    pr.textContent = '💶 ' + res.price + (String(res.price).includes('€') ? '' : ' €');
    comm.appendChild(pr);
  }
  const stLbl = { 1: '🟢 Skladom', 2: '📦 Nie skladom', 3: '🚫 Nepredáva sa' }[res.state];
  if (stLbl) {
    const chip = el('span', 'curbadge ' + ({ 1: 'st1', 2: 'st2', 3: 'st3' }[res.state]));
    chip.textContent = stLbl;
    comm.appendChild(chip);
    if (res.state === 1 && res.stock > 0) {   // Shoptet stock môže byť záporný (backorder) — „(-150 ks)" je šum
      const st = el('span', 'srch-stock');
      st.textContent = '(' + res.stock + ' ks)';
      comm.appendChild(st);
    }
  }
  if (comm.childNodes.length) main.appendChild(comm);
  const link = el('div', 'srch-link');                 // our_url now / paired URL after save
  if (res.our_url) {
    const a = el('a', 'supurl'); a.href = res.our_url; a.target = '_blank'; a.rel = 'noopener';
    a.textContent = '↗ náš produkt';
    a.onclick = (e) => e.stopPropagation();            // link click ≠ open panel
    link.appendChild(a);
  }
  if (res.paired_url) {
    // aktuálne rozhodnutie (good/manual) — priamy odkaz na dodávateľa; GRUBE už
    // display-normalizované na .de serverom
    const pa = el('a', 'supurl'); pa.href = res.paired_url; pa.target = '_blank'; pa.rel = 'noopener';
    pa.textContent = '🔗 dodávateľ';
    pa.onclick = (e) => e.stopPropagation();           // link click ≠ open panel
    link.appendChild(pa);
  }
  main.appendChild(link);
  head.appendChild(main);

  const badge = searchBadge(res);
  head.appendChild(badge);

  const panel = el('div', 'srch-panel'); panel.hidden = true;
  head.onclick = () => openSearchRow(res, panel, badge, link);
  row.appendChild(head);
  row.appendChild(panel);
  return row;
}

function openSearchRow(res, panel, badge, link) {
  if (!panel.hidden) { panel.hidden = true; panel.innerHTML = ''; return; }   // toggle closed
  panel.innerHTML = '';
  if (res.in_review) {
    // match by pairCode (when the result has one) OR by a shared variant code — most
    // review entries are keyed "SUPPLIER|pairCode" (e.g. GRUBE|425), so a key===key
    // lookup missed them and wrongly opened the manual-promote panel (C1); a
    // single-variant product (empty pairCode) is matched by its code instead.
    const product = PRODUCTS.find(p =>
      (res.pairCode && p.pairCode === res.pairCode) ||
      (p.variant_codes || []).some(c => (res.codes || []).includes(c)));
    // FULL review card (obrázky, náš stav/cena, stav párovania, decision buttony) —
    // holý resolutionPanel ukazoval „skoro žiadne údaje". saveDecision→render() na
    // search tabe early-returnuje (#searchResults ostáva), karta sa len live-nerefreshne
    // po rozhodnutí — rovnaké akceptované správanie ako mal panel.
    if (product) { panel.appendChild(renderCard(product)); panel.hidden = false; return; }
  }
  // not in review (or its product not loaded client-side) → manual promote-and-pair
  panel.appendChild(manualPairPanel(res, panel, badge, link));
  panel.hidden = false;
}

// manual-only re-pair: paste a supplier URL → /api/search-pair promotes + records a
// `manual` decision. On success flip the badge to 'napárované ✓' and show the URL,
// IN-PLACE (no full re-render, no scroll reset).
function manualPairPanel(res, panel, badge, link) {
  const wrap = el('div', 'panel');
  const mr = el('div', 'manualrow');
  const inp = el('input'); inp.type = 'url';
  inp.placeholder = 'Vlož URL produktovej stránky dodávateľa…';
  const save = el('button', 'btn good sm', 'Uložiť odkaz');
  const doSave = async () => {
    const v = inp.value.trim();
    if (!/^https?:\/\//.test(v)) return;             // client guard (server re-checks)
    save.disabled = true;
    let r;
    try {
      r = await fetch('/api/search-pair', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: res.key, url: v })
      });
    } catch (_) { save.disabled = false; return; }
    if (!r.ok) { save.disabled = false; return; }
    res.in_review = true;                            // a re-click can now open resolutionPanel
    badge.className = 'sbadge paired'; badge.textContent = 'napárované ✓';
    link.innerHTML = '';
    const a = el('a', 'supurl'); a.href = v; a.target = '_blank'; a.rel = 'noopener';
    a.textContent = '🔗 ' + v; a.onclick = (e) => e.stopPropagation();
    link.appendChild(a);
    panel.innerHTML = ''; panel.appendChild(el('div', 'srch-saved', '✓ Odkaz uložený'));
  };
  save.onclick = doSave;
  inp.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); doSave(); } };
  mr.appendChild(inp); mr.appendChild(save); wrap.appendChild(mr);
  return wrap;
}

// ---- Poznámky (notes) tab — free-form reminders, Discord replacement ----- //
async function loadNotes() {
  try {
    NOTES = (await (await fetch('/api/notes')).json()).notes || [];
  } catch (_) { NOTES = []; }
}

async function addNote(text) {
  const r = await fetch('/api/notes', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text })
  });
  if (!r.ok) return;
  const j = await r.json();
  NOTES.unshift(j.note);
  renderNotes();
}

async function toggleNoteDone(n) {
  n.done = !n.done;
  renderNotes();
  await fetch('/api/note', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: n.id, done: n.done })
  });
}

async function deleteNote(n) {
  NOTES = NOTES.filter((x) => x.id !== n.id);
  renderNotes();
  await fetch('/api/note', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: n.id, delete: true })
  });
}

function fmtNoteTs(ts) {
  const d = new Date(ts * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function renderNoteCard(n) {
  const card = el('div', 'note' + (n.done ? ' done' : ''));
  const txt = el('div', 'note-text'); txt.textContent = n.text;   // .textContent → XSS-safe
  card.appendChild(txt);
  const meta = el('div', 'note-meta');
  const ts = el('span', 'note-ts'); ts.textContent = fmtNoteTs(n.ts);
  meta.appendChild(ts);
  const doneBtn = el('button', 'note-done', n.done ? '↩ Vrátiť' : '✓ Hotovo');
  doneBtn.onclick = () => toggleNoteDone(n);
  meta.appendChild(doneBtn);
  const delBtn = el('button', 'note-del', '✕ Zmazať');
  delBtn.onclick = () => { if (confirm('Zmazať poznámku?')) deleteNote(n); };
  meta.appendChild(delBtn);
  card.appendChild(meta);
  return card;
}

function renderNotes() {
  const wrap = document.getElementById('tab-notes');
  if (!wrap) return;
  wrap.innerHTML = '';
  const addBox = el('div', 'note-add');
  const ta = el('textarea');
  ta.placeholder = 'Nová poznámka… (napr. „objednať na výmenu betelavo“, „pridať spreje do roy“)';
  const btn = el('button', 'btn good sm', 'Pridať');
  const doAdd = () => { const v = ta.value.trim(); if (v) addNote(v); };
  btn.onclick = doAdd;
  ta.onkeydown = (e) => { if (e.key === 'Enter' && e.ctrlKey) { e.preventDefault(); doAdd(); } };
  addBox.appendChild(ta); addBox.appendChild(btn);
  wrap.appendChild(addBox);
  const list = el('div', 'note-list');
  for (const n of NOTES) list.appendChild(renderNoteCard(n));
  wrap.appendChild(list);
}

// ── admin 'Užívatelia' tab (#91) ─────────────────────────────────────────────

async function loadUsers() {
  try { USERS_LIST = (await (await fetch('/api/users')).json()).users || []; }
  catch (_) { USERS_LIST = []; }
}

async function userAction(url, payload) {
  const r = await fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  if (!r.ok) {
    let msg = '';
    try { msg = (await r.json()).error || ''; } catch (_) { /* non-JSON error */ }
    alert('Nepodarilo sa: ' + (msg || ('chyba ' + r.status)));
  }
  await loadUsers();
  render();
}

function renderUserRow(u) {
  const row = el('div', 'user-row' + (u.is_admin ? ' admin' : ''));
  const who = el('div', 'user-who');
  who.appendChild(el('span', 'user-mail', escapeHtml(u.email)));
  if (u.is_admin) who.appendChild(el('span', 'user-badge', 'admin'));
  if (ME && u.email === ME.email) who.appendChild(el('span', 'user-badge me', 'ty'));
  row.appendChild(who);
  const acts = el('div', 'user-acts');
  const admBtn = el('button', 'btn ghost sm', u.is_admin ? 'Odobrať admina' : 'Spraviť adminom');
  admBtn.onclick = () => userAction('/api/users/admin', { email: u.email, is_admin: !u.is_admin });
  acts.appendChild(admBtn);
  const pwBtn = el('button', 'btn ghost sm', 'Nové heslo');
  pwBtn.onclick = () => {
    const p = prompt(`Nové heslo pre ${u.email} (min. 8 znakov):`);
    if (p) userAction('/api/users/password', { email: u.email, password: p });
  };
  acts.appendChild(pwBtn);
  const delBtn = el('button', 'btn warn sm', '✕ Zmazať');
  delBtn.onclick = () => {
    if (confirm(`Zmazať účet ${u.email}?`)) userAction('/api/users/delete', { email: u.email });
  };
  acts.appendChild(delBtn);
  row.appendChild(acts);
  return row;
}

function renderUsers() {
  const wrap = document.getElementById('tab-users');
  if (!wrap) return;
  wrap.innerHTML = '';
  const addBox = el('div', 'user-add');
  const em = el('input'); em.type = 'email'; em.placeholder = 'email@firma.sk';
  const pw = el('input'); pw.type = 'password'; pw.placeholder = 'heslo (min. 8 znakov)';
  const admLbl = el('label', 'user-admchk');
  const adm = el('input'); adm.type = 'checkbox';
  admLbl.appendChild(adm); admLbl.appendChild(document.createTextNode(' admin'));
  const btn = el('button', 'btn good sm', '➕ Pridať používateľa');
  btn.onclick = () => {
    const e = em.value.trim(), p = pw.value;
    if (!e || !p) { alert('Vyplň e-mail aj heslo.'); return; }
    userAction('/api/users', { email: e, password: p, is_admin: adm.checked });
  };
  addBox.appendChild(em); addBox.appendChild(pw);
  addBox.appendChild(admLbl); addBox.appendChild(btn);
  wrap.appendChild(addBox);
  const list = el('div', 'user-list');
  for (const u of USERS_LIST) list.appendChild(renderUserRow(u));
  wrap.appendChild(list);
}

// ---- Automatizácie (#93): tab „Nevyzdvihnuté zásielky" -------------------- //
async function loadPosta() {
  try {
    const [a, p] = await Promise.all([
      fetch('/api/automations').then(r => r.json()),
      fetch('/api/posta-uncollected').then(r => r.json()),
    ]);
    AUTOMATIONS = a.automations || [];
    POSTA = p;
  } catch (_) { AUTOMATIONS = []; POSTA = null; }
}

function autoByKey(key) { return AUTOMATIONS.find(x => x.key === key); }

async function toggleAutomation(key, enabled) {
  await fetch(`/api/automations/${key}/toggle`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });
  await loadPosta(); render();
}

let _postaPoll = null;
async function runAutomation(key) {
  await fetch(`/api/automations/${key}/run`, { method: 'POST' });
  await loadPosta(); render();
  clearInterval(_postaPoll);
  _postaPoll = setInterval(async () => {           // refresh until the run ends
    if (ACTIVE_TAB !== 'posta') { clearInterval(_postaPoll); _postaPoll = null; return; }
    await loadPosta(); render();
    const a = autoByKey(key);
    if (!a || !a.running) { clearInterval(_postaPoll); _postaPoll = null; }
  }, 2000);
}

function fmtDt(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleDateString('sk-SK') + ' '
    + d.toLocaleTimeString('sk-SK', { hour: '2-digit', minute: '2-digit' });
}

function dniLabel(n) { return n === 1 ? 'deň' : (n >= 2 && n <= 4 ? 'dni' : 'dní'); }

function renderPosta() {
  const wrap = document.getElementById('tab-posta');
  if (!wrap) return;
  wrap.innerHTML = '';
  const a = autoByKey('posta_uncollected');

  // status + controls (Štart/Stop persists; Spustiť teraz = manual run)
  const st = el('div', 'autostatus');
  if (!a) {
    st.appendChild(el('div', 'muted', 'Automatizácia nie je dostupná (server nevrátil stav).'));
  } else {
    const head = el('div', 'autohead');
    const pill = el('span', 'pill ' + (a.enabled ? 'on' : 'off'), a.enabled ? 'Beží' : 'Zastavené');
    pill.dataset.testid = 'posta-status';
    head.appendChild(pill);
    if (a.running) head.appendChild(el('span', 'runningdot', '⏳ práve prebieha kontrola…'));
    const btn = el('button', 'btn sm ' + (a.enabled ? 'warn' : 'good'),
      a.enabled ? '⏹ Stop' : '▶ Štart');
    btn.dataset.testid = 'posta-toggle';
    btn.onclick = () => toggleAutomation('posta_uncollected', !a.enabled);
    head.appendChild(btn);
    const run = el('button', 'btn sm ghost', '⚡ Spustiť teraz');
    run.dataset.testid = 'posta-run';
    run.disabled = !!a.running;
    run.onclick = () => runAutomation('posta_uncollected');
    head.appendChild(run);
    st.appendChild(head);

    const meta = el('div', 'autometa');
    const bits = [`Plán: ${escapeHtml(a.schedule || '')}`];
    bits.push('Posledný beh: ' + (a.last_run
      ? `${fmtDt(a.last_run)} — ${a.last_status === 'ok' ? '✅ OK' : '❌ CHYBA'}`
      : 'zatiaľ nikdy'));
    if (a.enabled && a.next_run) bits.push('Ďalší beh: ' + fmtDt(a.next_run));
    meta.innerHTML = bits.map(b => `<span>${b}</span>`).join(' · ');
    st.appendChild(meta);
    if (a.last_status === 'error' && a.last_error) {
      st.appendChild(el('div', 'autoerr', '❌ ' + escapeHtml(a.last_error)));
    }
    const lr = a.last_result || {};
    if (a.last_run && a.last_status === 'ok') {
      st.appendChild(el('div', 'muted',
        `Skontrolovaných zásielok: ${lr.checked ?? 0} · nevyzdvihnuté: ${lr.uncollected ?? 0}`
        + ` · odoslané e-maily: ${lr.emails_sent ?? 0}`
        + (lr.invalid ? ` · nesledovateľné: ${lr.invalid}` : '')
        + (lr.errors ? ` · chyby pri kontrole: ${lr.errors}` : '')));
    }
  }
  wrap.appendChild(st);

  const p = POSTA || {};
  // uncollected shipments table
  const unc = p.uncollected || [];
  if (!unc.length) {
    wrap.appendChild(el('div', 'empty2',
      p.last_check ? `Žiadne nevyzdvihnuté zásielky (kontrola ${fmtDt(p.last_check)}).`
                   : 'Zatiaľ neprebehla žiadna kontrola — spusti automatizáciu (▶ Štart) alebo klikni ⚡ Spustiť teraz.'));
  } else {
    const tbl = el('table', 'posta-table');
    tbl.dataset.testid = 'posta-table';
    tbl.innerHTML = '<thead><tr><th>Zásielka</th><th>Objednávka</th><th>Zákazník</th>'
      + '<th>Na pošte</th><th>Vyzdvihnúť do</th><th>E-maily</th></tr></thead>';
    const tb = el('tbody');
    for (const u of unc) {
      const tr = el('tr', u.call_needed ? 'callneeded' : '');
      tr.innerHTML =
        `<td><a href="${escapeHtml(u.tracking_link)}" target="_blank" rel="noopener">${escapeHtml(u.packageNumber)}</a>`
        + `<div class="sub2">${escapeHtml(u.office_name || '')}</div></td>`
        + `<td><a href="${escapeHtml(u.admin_link)}" target="_blank" rel="noopener">${escapeHtml(u.orderCode)}</a></td>`
        + `<td>${escapeHtml(u.name || '')}<div class="sub2">${escapeHtml(u.phone || '')}</div></td>`
        + `<td>${u.days_at_post || 1} ${dniLabel(u.days_at_post || 1)}`
        + (u.notified_since ? `<div class="sub2">od ${escapeHtml(u.notified_since)}</div>` : '') + '</td>'
        + `<td>${escapeHtml(u.retained_till || '—')}</td>`
        + `<td>${u.count || 0}/4`
        + (u.last_sent ? `<div class="sub2">naposledy ${escapeHtml(u.last_sent)}</div>` : '')
        + (u.call_needed ? '<div class="callbadge">⚠️ TREBA ZAVOLAŤ</div>' : '') + '</td>';
      tb.appendChild(tr);
    }
    tbl.appendChild(tb);
    wrap.appendChild(tbl);
  }

  // invalid_format packages — the class that silently broke the n8n workflow
  const inv = p.invalid || [];
  if (inv.length) {
    const box = el('div', 'warnbox');
    box.dataset.testid = 'posta-invalid';
    box.appendChild(el('div', 'warnhead', `⚠️ ${inv.length} zásielok s nesledovateľným číslom`));
    box.appendChild(el('div', 'muted',
      'Pošta SK tieto čísla nepozná (invalid_format — pravdepodobne iný prepravca '
      + 'alebo nový typ štítku). Treba ich preveriť ručne.'));
    const ul = el('ul');
    for (const i of inv) {
      ul.appendChild(el('li', '',
        `<code>${escapeHtml(i.packageNumber)}</code> — obj. `
        + `<a href="${escapeHtml(i.admin_link)}" target="_blank" rel="noopener">${escapeHtml(i.orderCode)}</a>`
        + ` ${escapeHtml(i.name || '')}`));
    }
    box.appendChild(ul);
    wrap.appendChild(box);
  }

  // per-shipment tracking errors (API down / timeouts after retries)
  const errs = p.errors || [];
  if (errs.length) {
    const box = el('div', 'warnbox');
    box.appendChild(el('div', 'warnhead', `❌ ${errs.length} zásielok sa nepodarilo skontrolovať`));
    const ul = el('ul');
    for (const i of errs) {
      ul.appendChild(el('li', '',
        `<code>${escapeHtml(i.packageNumber)}</code> (obj. ${escapeHtml(i.orderCode)}) — ${escapeHtml(i.error || '')}`));
    }
    box.appendChild(ul);
    wrap.appendChild(box);
  }
}

function render() {
  renderTabs();
  setPageHead();
  const toorder = ACTIVE_TAB === 'toorder';
  const search = ACTIVE_TAB === 'search';
  const notes = ACTIVE_TAB === 'notes';
  const users = ACTIVE_TAB === 'users';
  const posta = ACTIVE_TAB === 'posta';
  document.body.classList.toggle('toorder-wide', toorder);   // od kraja po kraj len na tabe „Na objednanie"
  const prog = document.querySelector('.progress'); if (prog) prog.style.display = (toorder || search || notes || users || posta) ? 'none' : '';
  const dls = document.querySelector('.downloads'); if (dls) dls.style.display = (toorder || search || notes || users || posta) ? 'none' : '';
  const filt = document.getElementById('filters'); if (filt) filt.style.display = (search || notes || users || posta) ? 'none' : '';
  const sec = document.getElementById('tab-search'); if (sec) sec.hidden = !search;
  const secNotes = document.getElementById('tab-notes'); if (secNotes) secNotes.hidden = !notes;
  const secUsers = document.getElementById('tab-users'); if (secUsers) secUsers.hidden = !users;
  const secPosta = document.getElementById('tab-posta'); if (secPosta) secPosta.hidden = !posta;
  const mainEl = document.getElementById('list'); if (mainEl) mainEl.style.display = (search || notes || users || posta) ? 'none' : '';
  if (posta) { document.getElementById('empty').hidden = true; renderPosta(); return; }
  if (users) { document.getElementById('empty').hidden = true; renderUsers(); return; }
  if (notes) { document.getElementById('empty').hidden = true; renderNotes(); return; }
  if (search) { document.getElementById('empty').hidden = true; return; }
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
  initTheme();
  // Who am I? (#91) — 401 → the fetch guard above already navigates to /login.
  try {
    const meR = await fetch('/api/me');
    if (meR.status === 401) return;   // navigating to /login
    ME = await meR.json();
  } catch (_) { /* network hiccup — the server gate on / already handled auth */ }
  const ub = document.getElementById('userBox');
  if (ub && ME) {
    ub.hidden = false;
    document.getElementById('userEmail').textContent = ME.email;
    const lb = document.getElementById('logoutBtn');
    if (lb) {
      lb.onclick = async () => {
        try { await fetch('/logout', { method: 'POST' }); } catch (_) { /* navigating anyway */ }
        location.href = '/login';
      };
    }
  }
  if (ACTIVE_TAB === 'users' && !(ME && ME.is_admin)) ACTIVE_TAB = 'review';
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
  if (qTab === 'toorder' || qTab === 'review' || qTab === 'search' || qTab === 'notes' || qTab === 'posta') { ACTIVE_TAB = qTab; localStorage.setItem('tab', qTab); }
  if (ACTIVE_TAB === 'toorder') await loadOrders();
  if (ACTIVE_TAB === 'notes') await loadNotes();
  if (ACTIVE_TAB === 'users') await loadUsers();
  if (ACTIVE_TAB === 'posta') await loadPosta();
  initSearch();
  render();
  const y = parseInt(localStorage.getItem('scrollY') || '0', 10);
  if (y) window.scrollTo(0, y);
}
init();
