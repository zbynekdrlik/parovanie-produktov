let PRODUCTS = [];
let DECISIONS = {};         // key -> {status, url}
let FILTER = 'unreviewed';
let ORDERS = [];            // [{key, orderCode, itemCode, size, qty, supplier, name, supplierUrl, ordered, assignedSupplier}]
let ORDERED = {};           // key -> true (ordered/objednané)
let WAITING = {};           // key -> true (čaká sa — deferred active line)
let INSTOCK = {};           // key -> true (skladom — máme/naskladnené)
let UNAVAIL = {};           // key -> true (nedostupné — u dodávateľa)
let NOTES = [];             // [{id, text, done, ts}] — 'Poznámky' tab
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

// ---- Na objednanie (to-order) tab ---------------------------------------- //
const TABS = [['review', '🔍 Kontrola párovania'], ['toorder', '📋 Na objednanie'],
  ['search', '🔎 Hľadať / opraviť'], ['notes', '📝 Poznámky']];

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
  if (tab === 'notes' && !NOTES.length) await loadNotes();
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
  cb.onchange = () => { saveOrdered(o.key, cb.checked); row.classList.toggle('done', cb.checked); };
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
  };
  row.appendChild(inStk);
  const unavailBtn = el('button', 'to-unavail' + (UNAVAIL[o.key] ? ' on' : ''), '✗ Nedostupné');
  unavailBtn.title = 'U dodávateľa nedostupné';
  unavailBtn.onclick = () => {
    const on = !UNAVAIL[o.key];
    saveUnavailable(o.key, on);
    unavailBtn.classList.toggle('on', on);
    row.classList.toggle('unavail', on);
  };
  row.appendChild(unavailBtn);
  return row;
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
  const cnt = {}, newest = {};
  for (const o of ORDERS) {
    const s = effSup(o);
    cnt[s] = (cnt[s] || 0) + 1;
    newest[s] = Math.max(newest[s] ?? -Infinity, oNum(o));
  }
  // dodávateľ s NAJNOVŠOU objednávkou hore; zhoda → abecedne
  const byPriority = (a, b) => (newest[b] - newest[a]) || (a < b ? -1 : a > b ? 1 : 0);
  const fbar = document.getElementById('filters'); fbar.innerHTML = '';
  const mk = (key, lbl) => {
    const b = el('button', ORDER_SUPPLIER === key ? 'active' : '', lbl);
    b.onclick = () => { ORDER_SUPPLIER = key; localStorage.setItem('orderSupplier', key); window.scrollTo(0, 0); render(); };
    return b;
  };
  fbar.appendChild(mk('all', `Všetci (${ORDERS.length})`));
  // escapeHtml: a supplier name is manually assignable (free text) → never trust it in
  // the innerHTML-based el() helper (filter label + group header below)
  for (const s of Object.keys(cnt).sort(byPriority)) fbar.appendChild(mk(s, `${escapeHtml(s)} (${cnt[s]})`));
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

function render() {
  renderTabs();
  const toorder = ACTIVE_TAB === 'toorder';
  const search = ACTIVE_TAB === 'search';
  const notes = ACTIVE_TAB === 'notes';
  document.body.classList.toggle('toorder-wide', toorder);   // od kraja po kraj len na tabe „Na objednanie"
  const prog = document.querySelector('.progress'); if (prog) prog.style.display = (toorder || search || notes) ? 'none' : '';
  const dls = document.querySelector('.downloads'); if (dls) dls.style.display = (toorder || search || notes) ? 'none' : '';
  const filt = document.getElementById('filters'); if (filt) filt.style.display = (search || notes) ? 'none' : '';
  const sec = document.getElementById('tab-search'); if (sec) sec.hidden = !search;
  const secNotes = document.getElementById('tab-notes'); if (secNotes) secNotes.hidden = !notes;
  const mainEl = document.getElementById('list'); if (mainEl) mainEl.style.display = (search || notes) ? 'none' : '';
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
  if (qTab === 'toorder' || qTab === 'review' || qTab === 'search' || qTab === 'notes') { ACTIVE_TAB = qTab; localStorage.setItem('tab', qTab); }
  if (ACTIVE_TAB === 'toorder') await loadOrders();
  if (ACTIVE_TAB === 'notes') await loadNotes();
  initSearch();
  render();
  const y = parseInt(localStorage.getItem('scrollY') || '0', 10);
  if (y) window.scrollTo(0, y);
}
init();
