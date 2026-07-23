let ME = null;              // {email, is_admin} — logged-in user (#91)
let USERS_LIST = [];        // admin 'Užívatelia' tab
let PRODUCTS = [];
let DECISIONS = {};         // key -> {status, url}
let VARIANT_LINKS = {};     // variant code -> supplier url (#174 per-size split links)
let FILTER = 'unreviewed';
let ORDERS = [];            // [{key, orderCode, itemCode, size, qty, supplier, name, supplierUrl, ordered, assignedSupplier}]
let ORDERED = {};           // key -> true (ordered/objednané)
let WAITING = {};           // key -> true (čaká sa — deferred active line)
let INSTOCK = {};           // key -> true (skladom — máme/naskladnené)
let UNAVAIL = {};           // key -> true (nedostupné — u dodávateľa)
let ORDER_COMMENTS = {};    // orderCode -> comment (#101 per-order manager note)
let NEDOSTUPNE = null;      // /api/nedostupne — flagged-unavailable products + customers (#100)
let ND_PENDING = null;      // {code, type} — the send the preview modal is showing
let VYSTAVY = null;         // /api/vystavy — poľovnícke výstavy (#111)
let VY_OPEN = new Set();    // ids of výstavy whose detail/edit panel is expanded (transient)
let VY_ADD_OPEN = false;    // the „+ Pridať výstavu" form is showing (transient)
let NOTES = [];             // [{id, text, done, ts}] — 'Poznámky' tab
let AUTOMATIONS = [];       // /api/automations — in-app runner statuses (#93)
let POSTA = null;           // /api/posta-uncollected — last run's display data
let SUPPLIER_STOCK = null;  // /api/supplier-stock — last scraper run's rows (#106)
let STOCK_FILTER = 'all';   // dodávateľský sklad filter: all | errors | llm | <supplier>
let RIZIKO = null;          // /api/riziko-vypadku — last risk-report run (#107)
let RESTOCK = null;         // /api/restock-skladom — last restock run (#108)
let STOCK_SKLADOM = null;   // /api/stock-skladom — last auto-skladom run (#98)
let ORDERS_REMINDER = null; // /api/orders-reminder — last orders-reminder run (#105)
let DEV = null;             // /api/dev/issues — {available, issues:[...]} or null (#115)
let DEV_FILTER = 'open';    // 'Vývoj' tab filter: open | closed | all
let UI_LABELS = {};         // /api/ui-labels — admin-set custom names {key: label} (#173)
let ORDER_SUPPLIER = 'all';
let ACTIVE_TAB = localStorage.getItem('tab') || 'toorder';
const expanded = new Set(); // keys whose resolution panel is open (transient, NOT saved)
const splitOpen = new Set(); // #174 — keys whose split-into-sizes editor is open (transient)

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

// A fast scroll through a large filter (e.g. ~1800+ 'Napárované' cards) makes MANY
// gallery boxes cross the 300px rootMargin within the same tick — each firing its own
// /api/images fetch. Without a cap, dozens of concurrent scrapes (our backend does a
// synchronous per-request supplier-page GET) pile up on the Flask worker pool and the
// slow ones all hang until Cloudflare's edge timeout — surfacing as a burst of
// "Failed to load resource: 524" console errors (#74). Cap concurrency; the rest queue
// and drain as slots free up — same eventual result, no backend/CDN pile-up.
const IMG_FETCH_CONCURRENCY = 4;
let _imgActive = 0;
const _imgQueue = [];
function _pumpImgQueue() {
  while (_imgActive < IMG_FETCH_CONCURRENCY && _imgQueue.length) {
    const task = _imgQueue.shift();
    _imgActive++;
    task().finally(() => { _imgActive--; _pumpImgQueue(); });
  }
}

async function loadInfo(box) {
  const url = box.dataset.url;
  if (!url) { box.classList.remove('loading'); return; }
  return new Promise((resolve) => {
    _imgQueue.push(async () => { await _loadInfoNow(box, url); resolve(); });
    _pumpImgQueue();
  });
}

async function _loadInfoNow(box, url) {
  try {
    const j = await (await fetch('/api/images?url=' + encodeURIComponent(url))).json();
    box.classList.remove('loading');
    box.innerHTML = '';
    if (!j.images || !j.images.length) { box.innerHTML = '<span class="noimg">bez obrázkov</span>'; }
    else for (const u of j.images) {
      const im = document.createElement('img'); im.src = u; im.loading = 'lazy';
      // broken supplier-CDN image → degrade to the placeholder instead of leaving a
      // broken-image icon on the card (the browser's own network-failure console log
      // for a genuinely 404/reset resource can't be suppressed from JS — see #50/#74
      // playbook note — this only fixes the VISUAL fallback).
      im.onerror = () => im.replaceWith(el('span', 'noimg', 'bez obrázka'));
      box.appendChild(im);
    }
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
    case 'good': return s === 'good' || s === 'manual' || s === 'split';
    case 'unavailable': return s === 'unavailable' || s === 'discontinued';
    default: return true;
  }
}

function el(tag, cls, html) { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }
function fmtDate(iso) { const p = (iso || '').split('-'); return p.length === 3 ? `${p[2]}.${p[1]}.${p[0]}` : (iso || ''); }  // 2026-04-24 → 24.04.2026
function escapeHtml(s) { return (s || '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
function badge(s) {
  const t = { good: '✓ Dobré', manual: '✓ Vybraný link', split: '✂ Rozdelené na veľkosti',
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

// #174 — save/clear one variant's supplier link (keyed by the stable variant code).
async function saveVariantLink(code, url) {
  const r = await fetch('/api/variant-link', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code, url: url || '' })
  });
  if (r.ok) { if (url) VARIANT_LINKS[code] = url; else delete VARIANT_LINKS[code]; }
  return r.ok;
}

// #174 — a multi-variant product (>1 size) can be "split": a different supplier link
// per size. Button opens the split editor for the card; single-variant → no button.
function splitButton(p) {
  if ((p.variant_codes || []).length <= 1) return null;
  const b = el('button', 'btn ghost sm splitbtn', '✂ Rozdeliť na veľkosti');
  b.title = 'Nastaviť iný dodávateľský link pre každú veľkosť';
  b.onclick = () => { splitOpen.add(p.key); render(); };
  return b;
}

// #174 — one editable row per variant (size): size label + code + candidate quick-picks
// + a manual URL input, each saved independently by variant code.
function splitRow(p, v) {
  const row = el('div', 'splitrow'); row.dataset.code = v.code;
  const head = el('div', 'splitrow-head');
  head.appendChild(el('span', 'splitsize', escapeHtml(v.size || v.code)));
  if (v.size) head.appendChild(el('span', 'splitcode', escapeHtml(v.code)));
  row.appendChild(head);
  const state = el('div', 'splitstate');
  const inp = el('input'); inp.type = 'url'; inp.className = 'spliturl';
  inp.placeholder = 'Link dodávateľa pre veľkosť ' + (v.size || v.code) + '…';
  inp.value = VARIANT_LINKS[v.code] || v.link || '';
  const mark = (val) => {
    state.className = 'splitstate' + (val ? ' has' : '');
    state.textContent = val ? '✓ link nastavený' : 'bez linku';
  };
  // Keep the variant object in sync (v.link) so the #180 missing-link check sees the
  // current saved state — including a CLEAR (v.link back to '' when the manager empties it).
  const commit = (val) => saveVariantLink(v.code, val).then(ok => { if (ok) { v.link = val || ''; mark(val); } });
  // whole-product candidates as quick-picks — the manager picks the right one per size
  if (p.candidates && p.candidates.length) {
    const cbox = el('div', 'splitcands');
    for (const c of p.candidates) {
      const pick = el('button', 'btn ghost sm', 'Vybrať: ' + escapeHtml(c.name || c.url));
      pick.onclick = () => { inp.value = c.url; commit(c.url); };
      cbox.appendChild(pick);
    }
    row.appendChild(cbox);
  }
  const mr = el('div', 'splitmanual');
  const save = el('button', 'btn good sm splitsave', 'Uložiť');
  save.onclick = () => { const val = inp.value.trim(); if (val && !val.startsWith('http')) return; commit(val); };
  mr.appendChild(inp); mr.appendChild(save);
  row.appendChild(mr);
  mark(inp.value);
  row.appendChild(state);
  return row;
}

// #180 — which sizes still have NO own supplier link. Split-commit does NOT delete an
// existing whole-product URL for those variants (skip-empty on purpose), so they silently
// keep the OLD link. Returns the size labels (or codes) of variants whose effective link is
// empty — mirrors the exact rule splitRow displays (VARIANT_LINKS override, else v.link).
function variantsWithoutLink(variants) {
  return (variants || [])
    .filter(v => !((VARIANT_LINKS[v.code] || '').trim() || (v.link || '').trim()))
    .map(v => v.size || v.code);
}

// #174 — the split-into-sizes editor for one product: hint + per-variant rows (loaded
// from /api/variants) + a commit/undo footer. `split` decision marks the card resolved.
function splitPanel(p) {
  const wrap = el('div', 'splitpanel');
  wrap.appendChild(el('div', 'splithint',
    'Dodávateľ má inú stránku pre každú veľkosť? Nastav vlastný link pre KAŽDÚ veľkosť.'));
  const rowsBox = el('div', 'splitrows loading', 'načítavam veľkosti…');
  wrap.appendChild(rowsBox);
  let loadedVariants = [];   // #180 — variant rows for the missing-link warning on commit
  fetch('/api/variants?key=' + encodeURIComponent(p.key))
    .then(r => r.json())
    .then(j => {
      loadedVariants = j.variants || [];
      rowsBox.classList.remove('loading'); rowsBox.innerHTML = '';
      for (const v of loadedVariants) rowsBox.appendChild(splitRow(p, v));
    })
    .catch(() => { rowsBox.classList.remove('loading'); rowsBox.textContent = 'Nepodarilo sa načítať veľkosti.'; });
  const foot = el('div', 'splitfoot');
  if (statusOf(p) === 'split') {
    const back = el('button', 'btn ghost sm', '↩ Zrušiť rozdelenie');
    back.onclick = () => { splitOpen.delete(p.key); saveDecision(p, 'undo'); };
    foot.appendChild(back);
  } else {
    const done = el('button', 'btn good sm', '✓ Hotovo – rozdelené');
    done.onclick = () => {
      // #180 — warn if some sizes have no own link: they'd keep the OLD whole-product URL.
      const missing = variantsWithoutLink(loadedVariants);
      if (missing.length) {
        const msg = missing.length === 1
          ? 'Veľkosť ' + missing[0] + ' nemá vlastný link — ostane jej pôvodný link produktu. Pokračovať?'
          : 'Veľkosti ' + missing.join(', ') + ' nemajú vlastný link — ostane im pôvodný link produktu. Pokračovať?';
        if (!confirm(msg)) return;   // cancel → stay in the split editor
      }
      splitOpen.delete(p.key); saveDecision(p, 'split', '');
    };
    const cancel = el('button', 'btn ghost sm', '✗ Zrušiť');
    cancel.onclick = () => { splitOpen.delete(p.key); render(); };
    foot.appendChild(done); foot.appendChild(cancel);
  }
  wrap.appendChild(foot);
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
  if (p.our_images.length) for (const u of p.our_images) {
    const im = el('img'); im.src = u; im.loading = 'lazy';
    // our own forestshop-CDN image can go stale (renamed/removed product photo) →
    // degrade to a clean placeholder instead of a broken-image icon (#50).
    im.onerror = () => im.replaceWith(el('span', 'noimg', 'bez obrázka'));
    oimgs.appendChild(im);
  }
  else oimgs.innerHTML = '<span class="noimg">bez obrázkov</span>';
  left.appendChild(oimgs);
  card.appendChild(left);

  // RIGHT — supplier / decision
  const right = el('div', 'side right');
  right.appendChild(el('div', 'label', 'Dodávateľ'));
  const bg = badge(s); if (bg) right.appendChild(bg);

  // #174 — split-into-sizes editor takes over the right side (while open OR committed).
  if (splitOpen.has(p.key) || s === 'split') {
    right.appendChild(splitPanel(p));
    card.appendChild(right);
    return card;
  }

  if (s === 'unavailable' || s === 'discontinued') {
    right.appendChild(el('div', 'reason', s === 'unavailable'
      ? '📦 Nie je skladom → import: visible + Vypredané (stock 0). Ostáva na re-kontrolu.'
      : '🚫 Už sa nebude predávať → import: detailOnly + Predaj výrobku skončil (link ostane pre Google).'));
    const back = el('button', 'btn ghost sm', '↩ Vrátiť');
    back.onclick = () => saveDecision(p, 'undo');
    right.appendChild(back);
    // #97 — 'Vrátiť' only clears this decision locally; it does NOT push an immediate
    // re-enable to the eshop. The real re-enable (Vypredané → Skladom) is done by the
    // nightly restock automation once the product is back in stock at the supplier.
    // Shown only for 'unavailable' (Vypredané) — 'discontinued' is not auto-re-enabled.
    if (s === 'unavailable')
      right.appendChild(el('div', 'reenote',
        '↩ Vrátiť len zruší toto označenie tu. Reálne zapnutie v eshope spraví '
        + 'nočná automatika, keď je produkt späť skladom.'));
  } else if (s === 'good' || s === 'manual') {
    supplierBlock(right, p, s === 'good' ? p.ai_chosen_url : decUrl(p), s === 'good');
    const act = el('div', 'actions');
    const change = el('button', 'btn ghost sm', '✗ Zmeniť / iný link');
    change.onclick = () => { expanded.add(p.key); render(); };
    act.appendChild(change);
    const sb = splitButton(p); if (sb) act.appendChild(sb);
    right.appendChild(act);
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
    const sb = splitButton(p); if (sb) act.appendChild(sb);
    right.appendChild(act);
  } else {
    if (p.ai_status === 'unmatched' && p.ai_reason) right.appendChild(el('div', 'reason', '🤖 AI nenašla istú zhodu: ' + escapeHtml(p.ai_reason)));
    const sb = splitButton(p);
    if (sb) { const act = el('div', 'actions'); act.appendChild(sb); right.appendChild(act); }
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
// Order = usage frequency (#117): 'Kontrola párovania' became the least-used
// page once the review backlog stabilized, so it sits LAST inside the 'Eshop'
// folder — before the 'Automatizácie' section, never first.
const TABS = [['toorder', 'Na objednanie'], ['nedostupne', 'Nedostupné tovary'],
  ['vystavy', 'Poľovnícke výstavy'],
  ['search', 'Hľadať / opraviť'],
  ['notes', 'Poznámky'], ['review', 'Kontrola párovania']];

// In-app automations (#93) — each gets its own nav item in the 'Automatizácie'
// sidebar section (#autoTabs) + its own tab section. New automations: add here.
const AUTOMATION_TABS = [['posta', 'Nevyzdvihnuté zásielky'], ['orders_reminder', 'Pripomienky objednávok'],
  ['shoptet_sync', 'Sync zo Shoptetu'],
  ['parovania_eshop', 'Párovania → eshop'], ['grube_externalcode', 'GRUBE kódy → eshop'],
  ['split_links', 'Veľkostné linky → eshop'],
  ['dodavatelsky_sklad', 'Dodávateľský sklad'],
  ['riziko_vypadku', 'Riziko výpadku'], ['restock_skladom', 'Vypredané → Skladom'],
  ['stock_skladom', 'Máme skladom → Skladom'],
  ['image_health', 'Kontrola obrázkov']];

const NAV_ICONS = {
  review: '<path d="M9 12l2 2 4-4"/><circle cx="12" cy="12" r="9"/>',
  toorder: '<path d="M9 5h6M9 9h6M9 13h4"/><rect x="4" y="3" width="16" height="18" rx="2"/>',
  nedostupne: '<circle cx="12" cy="12" r="9"/><path d="M6 6l12 12"/>',
  vystavy: '<path d="M3 21h18"/><path d="M5 21V10l7-5 7 5v11"/><path d="M12 5v16"/>'
    + '<path d="M5 10l7 4 7-4"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/>',
  notes: '<path d="M4 4h16v12l-4 4H4z"/><path d="M16 20v-4h4"/>',
  users: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 5-6 8-6s6.5 2 8 6"/>',
  posta: '<path d="M21 8l-9-5-9 5v8l9 5 9-5z"/><path d="M3 8l9 5 9-5"/><path d="M12 13v8"/>',
  shoptet_sync: '<path d="M21 12a9 9 0 01-15.3 6.4M3 12a9 9 0 0115.3-6.4"/>'
    + '<path d="M21 3v6h-6M3 21v-6h6"/>',
  parovania_eshop: '<path d="M12 3v12"/><path d="M8 7l4-4 4 4"/>'
    + '<path d="M4 15v4a2 2 0 002 2h12a2 2 0 002-2v-4"/>',
  dodavatelsky_sklad: '<path d="M3 7l9-4 9 4v10l-9 4-9-4z"/><path d="M3 7l9 4 9-4"/>'
    + '<path d="M12 11v10"/>',
  riziko_vypadku: '<path d="M12 3L2 20h20L12 3z"/><path d="M12 9.5v4"/><path d="M12 17v.01"/>',
  restock_skladom: '<path d="M3 7l9-4 9 4v10l-9 4-9-4z"/><path d="M9 12l3-3 3 3"/><path d="M12 9v7"/>',
  stock_skladom: '<path d="M3 7l9-4 9 4v10l-9 4-9-4z"/><path d="M8 12l3 3 5-5"/>',
  orders_reminder: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18"/>'
    + '<path d="M8 2v4M16 2v4"/><path d="M12 12v3"/><path d="M12 17.5v.01"/>',
  image_health: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="11" r="2"/>'
    + '<path d="M21 16l-5.5-5.5L11 15"/>',
  dev: '<path d="M8 9l-4 3 4 3"/><path d="M16 9l4 3-4 3"/><path d="M13 5l-2 14"/>',
};

// 'Užívatelia' is an ADMIN-ONLY nav item (the server 403s non-admins anyway).
// It is rendered STANDALONE at the sidebar bottom (#usersNav), OUTSIDE the
// 'Eshop' folder (#118 refinement, Marek 2026-07-22) — see renderTabs().
function isAdmin() { return !!(ME && ME.is_admin); }

// count badge per nav item — review: still-unreviewed, toorder: open lines, notes: count
function navCount(key) {
  if (key === 'review') return PRODUCTS.filter(p => !statusOf(p)).length;
  if (key === 'toorder') return ORDERS.length;
  if (key === 'nedostupne') return NEDOSTUPNE ? NEDOSTUPNE.length : 0;
  // badge = výstavy waiting for the manager's decision ('akcia bude') — the actionable ones
  if (key === 'vystavy') return VYSTAVY ? VYSTAVY.filter(v => (v.status || '') === 'akcia bude').length : 0;
  if (key === 'notes') return NOTES.length;
  if (key === 'users') return USERS_LIST.length;
  if (key === 'posta') return POSTA ? (POSTA.uncollected || []).length : 0;
  if (key === 'dodavatelsky_sklad') return SUPPLIER_STOCK ? (SUPPLIER_STOCK.stats || {}).errors || 0 : 0;
  if (key === 'riziko_vypadku') return RIZIKO ? (RIZIKO.risks || []).length : 0;
  if (key === 'restock_skladom') return RESTOCK ? (RESTOCK.candidates || []).length : 0;
  if (key === 'stock_skladom') return STOCK_SKLADOM ? (STOCK_SKLADOM.candidates || []).length : 0;
  if (key === 'orders_reminder') return ORDERS_REMINDER ? (ORDERS_REMINDER.red || []).length : 0;
  if (key === 'dev') return DEV ? (DEV.issues || []).filter(i => i.state === 'open').length : 0;
  return 0;
}

// #153 — visible failure indicator: TRUE when this automation's LAST run ended in error. Read
// straight from AUTOMATIONS (prefetched at init(), see below) so the badge shows on ANY page —
// the manager must not have to open the failing automation's own tab to find out (that silence
// is exactly why the #156 timeout went unnoticed until a human spotted it by chance).
function navError(key) {
  const a = autoByKey(key);
  return !!(a && a.last_status === 'error');
}

// `defaultLbl` = the built-in name; an admin-set override in UI_LABELS (#173)
// wins for DISPLAY (button text + accessible name + page title), but the prompt
// dialog for renaming always shows the built-in default too (so "vrátiť pôvodný"
// is meaningful). Returns a wrapper <div class="navrow"> — a bare tab button for
// a non-admin, tab button + ✏️ rename icon for an admin. The wrapper keeps
// `.tabs .tab` / `.tabs .tab .tlabel` selectors matching at any depth (existing
// E2E), and admin-only adds a SECOND button with a generic aria-label
// ("Premenovať", never the tab's own name) so it can never collide with an
// existing `get_by_role("button", name=<tab label>)` lookup (the #115 lightbulb
// substring-collision gotcha, avoided the same way here).
function _navButton(key, defaultLbl) {
  const lbl = UI_LABELS[key] || defaultLbl;
  const bt = el('button', 'tab' + (ACTIVE_TAB === key ? ' active' : ''));
  const n = navCount(key);
  const err = navError(key);
  bt.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${NAV_ICONS[key]}</svg>`
    + `<span class="tlabel">${escapeHtml(lbl)}</span>`
    + (err ? '<span class="navwarn" title="posledný beh zlyhal">⚠</span>' : '')
    + (n > 0 ? `<span class="navcount">${n}</span>` : '');
  bt.onclick = () => switchTab(key);
  const row = el('div', 'navrow');
  row.appendChild(bt);
  if (isAdmin()) {
    const edit = el('button', 'navedit', '✏️');
    edit.title = 'Premenovať';
    edit.setAttribute('aria-label', 'Premenovať');
    edit.dataset.testid = 'navedit-' + key;
    edit.onclick = (e) => { e.stopPropagation(); renameNavItem(key, defaultLbl); };
    row.appendChild(edit);
  }
  return row;
}

function renderTabs() {
  const t = document.getElementById('tabs'); if (!t) return;
  t.innerHTML = '';
  for (const [key, lbl] of TABS) t.appendChild(_navButton(key, lbl));
  const at = document.getElementById('autoTabs');
  if (at) {
    at.innerHTML = '';
    for (const [key, lbl] of AUTOMATION_TABS) at.appendChild(_navButton(key, lbl));
  }
  // 'Užívatelia' (admin-only) — standalone at the sidebar bottom, OUTSIDE the
  // 'Eshop' folder (#118 refinement). Non-admins: container stays empty.
  const un = document.getElementById('usersNav');
  if (un) {
    un.innerHTML = '';
    if (isAdmin()) un.appendChild(_navButton('users', 'Užívatelia'));
  }
  // 'Vývoj' (#115) — standalone at the very bottom, for EVERY logged-in user.
  const dn = document.getElementById('devNav');
  if (dn) {
    dn.innerHTML = '';
    dn.appendChild(_navButton('dev', 'Vývoj'));
  }
}

// Top-bar per-page title + a plain-language subtitle (with live counts).
const PAGE_TITLES = {
  review: 'Kontrola párovania', toorder: 'Na objednanie', nedostupne: 'Nedostupné tovary',
  vystavy: 'Poľovnícke výstavy',
  search: 'Hľadať / opraviť', notes: 'Poznámky', users: 'Užívatelia',
  posta: 'Nevyzdvihnuté zásielky', shoptet_sync: 'Sync zo Shoptetu',
  parovania_eshop: 'Párovania → eshop', grube_externalcode: 'GRUBE kódy → eshop',
  split_links: 'Veľkostné linky → eshop', dodavatelsky_sklad: 'Dodávateľský sklad',
  riziko_vypadku: 'Riziko výpadku', restock_skladom: 'Vypredané → Skladom',
  stock_skladom: 'Máme skladom → Skladom',
  orders_reminder: 'Pripomienky objednávok',
  image_health: 'Kontrola obrázkov', dev: 'Vývoj',
};
function setPageHead() {
  const h = document.getElementById('pageTitle');
  if (h) h.textContent = UI_LABELS[ACTIVE_TAB] || PAGE_TITLES[ACTIVE_TAB] || '';
  const s = document.getElementById('pageSub'); if (!s) return;
  if (ACTIVE_TAB === 'review') {
    const un = PRODUCTS.filter(p => !statusOf(p)).length;
    s.textContent = `${PRODUCTS.length} produktov · ${un} čaká na kontrolu`;
  } else if (ACTIVE_TAB === 'toorder') {
    s.textContent = `${ORDERS.length} otvorených položiek u dodávateľov`;
  } else if (ACTIVE_TAB === 'nedostupne') {
    const n = NEDOSTUPNE ? NEDOSTUPNE.length : 0;
    s.textContent = `${n} nedostupných tovarov · upozornenie zákazníkom s otvorenou objednávkou`;
  } else if (ACTIVE_TAB === 'vystavy') {
    const n = VYSTAVY ? VYSTAVY.length : 0;
    const akcia = VYSTAVY ? VYSTAVY.filter(v => (v.status || '') === 'akcia bude').length : 0;
    s.textContent = `${n} výstav · ${akcia} čaká na rozhodnutie`;
  } else if (ACTIVE_TAB === 'search') {
    s.textContent = 'Prehľadá všetky polia všetkých produktov';
  } else if (ACTIVE_TAB === 'notes') {
    s.textContent = `${NOTES.length} poznámok`;
  } else if (ACTIVE_TAB === 'users') {
    s.textContent = `${USERS_LIST.length} účtov s prístupom`;
  } else if (ACTIVE_TAB === 'posta') {
    const n = POSTA ? (POSTA.uncollected || []).length : 0;
    s.textContent = `${n} zásielok čaká na pošte · automatická kontrola + upozornenia zákazníkom`;
  } else if (ACTIVE_TAB === 'shoptet_sync') {
    s.textContent = 'Hodinové obnovenie objednávok a katalógu zo Shoptetu';
  } else if (ACTIVE_TAB === 'parovania_eshop') {
    s.textContent = 'Denné nahranie nových párovaní a dodávateľov do eshopu (o 21:00)';
  } else if (ACTIVE_TAB === 'dodavatelsky_sklad') {
    const n = SUPPLIER_STOCK ? (SUPPLIER_STOCK.rows || []).length : 0;
    s.textContent = `${n} dodávateľských liniek · denná kontrola dostupnosti a cien`;
  } else if (ACTIVE_TAB === 'riziko_vypadku') {
    const n = RIZIKO ? (RIZIKO.risks || []).length : 0;
    s.textContent = `${n} produktov v riziku výpadku · máme skladom, dodávateľ už nemá`;
  } else if (ACTIVE_TAB === 'restock_skladom') {
    const n = RESTOCK ? (RESTOCK.candidates || []).length : 0;
    s.textContent = `${n} produktov na naskladnenie · máme vypredané, dodávateľ má opäť skladom`;
  } else if (ACTIVE_TAB === 'stock_skladom') {
    const n = STOCK_SKLADOM ? (STOCK_SKLADOM.candidates || []).length : 0;
    s.textContent = `${n} produktov na prepnutie · fyzicky máme skladom, ale zobrazujú sa ako Vypredané`;
  } else if (ACTIVE_TAB === 'image_health') {
    s.textContent = 'Periodická kontrola vlastných obrázkov produktov (mŕtve odkazy sa z karty odstránia)';
  } else if (ACTIVE_TAB === 'dev') {
    if (DEV && DEV.available) {
      const iss = DEV.issues || [];
      const open = iss.filter(i => i.state === 'open').length;
      s.textContent = `${open} otvorených · ${iss.length - open} hotových úloh`;
    } else {
      s.textContent = 'Zoznam vývojových úloh (GitHub) + nápady zo žiarovky';
    }
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

// Edit-mode toggle (#176): the per-tab ✏️ rename pencils are HIDDEN by default
// (they used to show on every nav item at once, covering half the dashboard
// names). An admin turns them on/off with the 'Upraviť názvy' button in the
// sidebar footer; body.edit-labels is the CSS switch. State persists in
// localStorage so the mode survives a reload, but the default is always OFF.
function applyEditLabels(on) {
  document.body.classList.toggle('edit-labels', on);
  const b = document.getElementById('editLabelsBtn');
  if (!b) return;
  b.setAttribute('aria-pressed', on ? 'true' : 'false');
  const lbl = b.querySelector('.editlbl');
  if (lbl) lbl.textContent = on ? 'Hotovo — skryť ceruzky' : 'Upraviť názvy';
}
function initEditLabels() {
  const b = document.getElementById('editLabelsBtn');
  if (!b || !isAdmin()) return;   // admin-only; a non-admin never sees the toggle
  b.hidden = false;
  applyEditLabels(localStorage.getItem('editLabels') === 'on');
  b.onclick = () => {
    const on = !document.body.classList.contains('edit-labels');
    localStorage.setItem('editLabels', on ? 'on' : 'off');
    applyEditLabels(on);
  };
}

// Sidebar folders (#118): collapsible nav groups, system-like tree. Each folder's
// expanded/collapsed state persists per-key in localStorage (default = expanded).
// Designed for extensibility — register more folders by calling initFolder().
function initFolder(id, key) {
  const folder = document.getElementById(id);
  const head = folder && folder.querySelector('.folder-head');
  if (!folder || !head) return;
  const collapsed = localStorage.getItem(key) === 'collapsed';
  folder.classList.toggle('collapsed', collapsed);
  head.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  head.onclick = () => {
    const nowCollapsed = !folder.classList.contains('collapsed');
    folder.classList.toggle('collapsed', nowCollapsed);
    head.setAttribute('aria-expanded', nowCollapsed ? 'false' : 'true');
    localStorage.setItem(key, nowCollapsed ? 'collapsed' : 'open');
  };
}
function initFolders() {
  initFolder('folder-eshop', 'folder:eshop');
}

async function switchTab(tab) {
  ACTIVE_TAB = tab; localStorage.setItem('tab', tab); window.scrollTo(0, 0);
  if (tab === 'toorder' && !ORDERS.length) await loadOrders();
  if (tab === 'nedostupne') await loadNedostupne();   // always fresh — orders/state change
  if (tab === 'vystavy') await loadVystavy();   // always fresh — state advances via automations
  if (tab === 'notes' && !NOTES.length) await loadNotes();
  if (tab === 'users') await loadUsers();   // always fresh — small list
  if (tab === 'posta') await loadPosta();   // always fresh — status can change
  if (tab === 'shoptet_sync') await loadAutomations();   // always fresh — status can change
  if (tab === 'parovania_eshop') await loadAutomations();   // always fresh — status can change
  if (tab === 'dodavatelsky_sklad') await loadSupplierStock();   // always fresh — status can change
  if (tab === 'riziko_vypadku') await loadRiziko();   // always fresh — status can change
  if (tab === 'restock_skladom') await loadRestock();   // always fresh — status can change
  if (tab === 'stock_skladom') await loadStockSkladom();   // always fresh — status can change
  if (tab === 'orders_reminder') await loadOrdersReminder();   // always fresh — status can change
  if (tab === 'image_health') await loadAutomations();   // always fresh — status can change
  if (tab === 'dev') await loadDevIssues();   // always fresh — issues change on GitHub
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
    ORDER_COMMENTS = (await (await fetch('/api/order-comment')).json()).comments || {};
  } catch (_) { ORDERS = []; ORDERED = {}; WAITING = {}; INSTOCK = {}; UNAVAIL = {}; ORDER_COMMENTS = {}; }
}

async function saveOrdered(key, ordered) {
  if (ordered) ORDERED[key] = true; else delete ORDERED[key];
  await fetch('/api/ordered', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key, ordered })
  });
}

// ── Nedostupné tovary (#100) ───────────────────────────────────────────────
async function loadNedostupne() {
  try {
    NEDOSTUPNE = (await (await fetch('/api/nedostupne')).json()).products || [];
  } catch (_) { NEDOSTUPNE = []; }
}

async function saveNdState(code, field, value) {
  const p = (NEDOSTUPNE || []).find(x => x.code === code);
  if (p) p[field === 'nedostupne' ? 'nedostupne' : 'alternativa'] = value;   // optimistic
  render();
  try {
    await fetch('/api/nedostupne/state', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, field, value })
    });
  } catch (_) { /* store write best-effort; UI already reflects intent */ }
}

// Two customer-e-mail types; label + short human meaning for the tab UI.
const ND_TYPES = {
  nedostupne: { label: 'Nedostupné', desc: 'e-mail: objednaný produkt je nedostupný' },
  alternativa: { label: 'Alternatíva', desc: 'e-mail: nedostupný + návrh alternatív' },
};

function renderNedostupne() {
  const sec = document.getElementById('tab-nedostupne');
  if (!sec) return;
  sec.innerHTML = '';
  const list = NEDOSTUPNE || [];
  if (!list.length) {
    sec.appendChild(el('div', 'nd-empty',
      'Žiadne nedostupné tovary. Produkt označíš ako „nedostupné" na tabe '
      + '<strong>Na objednanie</strong> — tu sa potom zozbierajú všetky na jednom mieste.'));
    return;
  }
  for (const p of list) sec.appendChild(renderNdCard(p));
}

function renderNdCard(p) {
  const card = el('div', 'nd-card');
  card.dataset.code = p.code;
  card.dataset.testid = 'nd-card-' + p.code;
  const head = el('div', 'nd-head');
  head.innerHTML = `<div class="nd-title">${escapeHtml(p.itemName || p.code)}</div>`
    + `<div class="nd-code">kód ${escapeHtml(p.code)} · `
    + `${p.order_count} ${p.order_count === 1 ? 'zákazník' : 'zákazníkov'} s otvorenou objednávkou</div>`;
  card.appendChild(head);

  // affected customers (open orders)
  if (p.orders && p.orders.length) {
    const ul = el('ul', 'nd-orders');
    for (const o of p.orders) {
      const badges = (o.unavailable_sent ? '<span class="nd-badge ok">✓ nedostupné</span>' : '')
        + (o.alternative_sent ? '<span class="nd-badge ok">✓ alternatíva</span>' : '');
      ul.appendChild(el('li', null,
        `<span class="nd-oc">#${escapeHtml(o.orderCode)}</span> `
        + `${escapeHtml(o.billFullName || '—')} · `
        + `<span class="nd-em">${escapeHtml(o.email || 'bez e-mailu')}</span> ${badges}`));
    }
    card.appendChild(ul);
  } else {
    card.appendChild(el('div', 'nd-noorders', 'Žiadna otvorená objednávka na tento produkt.'));
  }

  // the two e-mail types, each = checkbox intent + Náhľad (preview → send)
  const acts = el('div', 'nd-types');
  acts.appendChild(renderNdType(p, 'nedostupne', p.nedostupne, p.unavailable_sent_count));
  acts.appendChild(renderNdType(p, 'alternativa', p.alternativa, p.alternative_sent_count,
    p.alternatives));
  card.appendChild(acts);
  return card;
}

function renderNdType(p, type, checked, sentCount, alternatives) {
  const box = el('div', 'nd-type');
  const t = ND_TYPES[type];
  const cb = el('label', 'nd-check');
  cb.innerHTML = `<input type="checkbox" data-testid="nd-cb-${type}-${p.code}" `
    + `${checked ? 'checked' : ''}>`
    + `<span><strong>${t.label}</strong><small>${t.desc}</small></span>`;
  cb.querySelector('input').onchange = (e) => saveNdState(p.code, type, e.target.checked);
  box.appendChild(cb);

  if (type === 'alternativa' && alternatives && alternatives.length) {
    const al = el('div', 'nd-alts', 'Alternatívy: ' + alternatives.map(a =>
      a.url ? `<a href="${escapeHtml(a.url)}" target="_blank" rel="noopener">${escapeHtml(a.name)}</a>`
        : escapeHtml(a.name)).join(', '));
    box.appendChild(al);
  }

  const canSend = (p.order_count || 0) > 0;
  const btn = el('button', 'btn sm nd-preview-btn',
    `✉ Náhľad e-mailu${sentCount ? ` · odoslané ${sentCount}` : ''}`);
  btn.disabled = !canSend;
  btn.dataset.testid = `nd-preview-${type}-${p.code}`;
  btn.onclick = () => openNdPreview(p.code, type);
  box.appendChild(btn);
  return box;
}

function _ndModalEls() {
  return {
    modal: document.getElementById('ndModal'),
    head: document.getElementById('ndHead'),
    hint: document.getElementById('ndHint'),
    rec: document.getElementById('ndRecipients'),
    frame: document.getElementById('ndPreview'),
    msg: document.getElementById('ndMsg'),
    send: document.getElementById('ndSend'),
  };
}

function closeNdModal() {
  const m = document.getElementById('ndModal');
  if (m) m.hidden = true;
  ND_PENDING = null;
}

async function openNdPreview(code, type) {
  const E = _ndModalEls();
  if (!E.modal) return;
  ND_PENDING = { code, type };
  E.head.textContent = 'Náhľad e-mailu — ' + ND_TYPES[type].label;
  E.hint.textContent = 'Skontroluj komu a čo pôjde. E-mail sa odošle až po kliknutí „Odoslať".';
  E.rec.innerHTML = 'Načítavam…';
  E.frame.srcdoc = '';
  E.msg.hidden = true;
  E.send.disabled = true;
  E.modal.hidden = false;
  let j;
  try {
    j = await (await fetch('/api/nedostupne/preview', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, type })
    })).json();
  } catch (_) { j = { ok: false }; }
  if (!j || !j.ok) { E.rec.textContent = 'Náhľad sa nepodarilo načítať.'; return; }
  const recips = j.recipients || [];
  if (!recips.length) {
    E.rec.innerHTML = '<em>Žiadni noví príjemcovia'
      + (j.already_sent ? ` (${j.already_sent} už bolo informovaných)` : '') + '.</em>';
    E.send.disabled = true;
    E.send.textContent = '✉ Odoslať (0)';
  } else {
    E.rec.innerHTML = `<div class="nd-rec-head">Príjemcovia (${recips.length}):</div>`
      + '<ul>' + recips.map(r =>
        `<li>${escapeHtml(r.name || '—')} · <span class="nd-em">${escapeHtml(r.email)}</span> `
        + `<span class="nd-oc">#${escapeHtml(r.orderCode)}</span></li>`).join('') + '</ul>';
    E.send.disabled = false;
    E.send.textContent = `✉ Odoslať (${recips.length})`;
  }
  E.frame.srcdoc = j.html || '';
}

async function ndSendNow() {
  if (!ND_PENDING) return;
  const E = _ndModalEls();
  E.send.disabled = true;
  E.msg.hidden = false;
  E.msg.textContent = 'Odosielam…';
  let j;
  try {
    j = await (await fetch('/api/nedostupne/send', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(ND_PENDING)
    })).json();
  } catch (_) { j = { ok: false }; }
  if (j && j.ok) {
    E.msg.textContent = `✓ Odoslané: ${j.sent}` + (j.skipped ? ` · preskočené: ${j.skipped}` : '');
    await loadNedostupne();
    render();
    setTimeout(closeNdModal, 1200);
  } else {
    E.msg.textContent = `Odoslanie zlyhalo${j && j.failed ? ` (${j.failed} chýb)` : ''}.`;
    E.send.disabled = false;
  }
}

function initNdModal() {
  const bd = document.getElementById('ndBackdrop');
  const cancel = document.getElementById('ndCancel');
  const send = document.getElementById('ndSend');
  if (bd) bd.onclick = closeNdModal;
  if (cancel) cancel.onclick = closeNdModal;
  if (send) send.onclick = ndSendNow;
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

// #101 — per-ORDER comment (the manager's note about the whole order, mirroring the
// Shoptet "Poznámka e-shopu"). Keyed by orderCode, so it applies to every line of that
// order → after a save re-render the whole tab so all sibling lines reflect it (same
// per-shared-property propagation as saveSupplier).
async function saveOrderComment(o, comment, row) {
  const r = await fetch('/api/order-comment', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ orderCode: o.orderCode, comment })
  });
  if (!r.ok) return;
  if (comment) ORDER_COMMENTS[o.orderCode] = comment; else delete ORDER_COMMENTS[o.orderCode];
  renderToOrder();
}

// comment editor (multi-line textarea + save) — opened from the 💬 button on a row.
// Ctrl/⌘+Enter saves (plain Enter keeps the note multi-line, like the admin textarea).
function commentEditor(o, row, focus) {
  const wrap = el('div', 'to-comment-edit');
  const inp = el('textarea', 'to-cominput');
  inp.rows = 2;
  inp.placeholder = 'komentár k objednávke…';
  inp.value = ORDER_COMMENTS[o.orderCode] || '';
  const save = el('button', 'to-comsave', '💾 Uložiť');
  save.title = 'Uložiť komentár k objednávke (Ctrl+Enter)';
  const doSave = () => saveOrderComment(o, inp.value.trim(), row);
  save.onclick = doSave;
  inp.onkeydown = (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); doSave(); }
  };
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
  // #101 — existing Shoptet "Poznámka e-shopu" (read-only context; textContent → escaped)
  if (o.shopRemark) {
    const sr = el('span', 'to-shopnote');
    const flat = o.shopRemark.replace(/\s+/g, ' ').trim();
    sr.textContent = '🛈 ' + (flat.length > 40 ? flat.slice(0, 40) + '…' : flat);
    sr.title = 'Poznámka e-shopu v Shoptete:\n' + o.shopRemark;
    row.appendChild(sr);
  }
  // #101 — our per-ORDER comment: a chip (💬) when set (+ ✏️ edit), else an add button
  const com = (o.orderCode && ORDER_COMMENTS[o.orderCode]) || '';
  if (com) {
    const tag = el('span', 'to-comment');
    tag.textContent = '💬 ' + (com.length > 40 ? com.slice(0, 40) + '…' : com);
    tag.title = com;
    row.appendChild(tag);
    const ce = el('button', 'to-comedit', '✏️');
    ce.title = 'Upraviť komentár k objednávke';
    ce.onclick = () => { tag.replaceWith(commentEditor(o, row, true)); ce.remove(); };
    row.appendChild(ce);
  } else {
    const add = el('button', 'to-comadd', '💬 Komentár');
    add.title = 'Pridať komentár k objednávke';
    add.onclick = () => { add.replaceWith(commentEditor(o, row, true)); };
    row.appendChild(add);
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
  // #64: a pairCode reviewed under 2+ suppliers yields MULTIPLE rows sharing the same
  // res.key — dataset.reviewKey is the per-ROW identity (that specific review product's
  // real key, e.g. 'WETLAND|425'), empty for a not-yet-paired row.
  row.dataset.reviewKey = res.review_key || '';

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
  // #64: a pairCode reviewed under 2+ suppliers renders as multiple rows sharing the
  // SAME catalog res.supplier — show the row's OWN review_supplier (this specific
  // pairing's real supplier) so the manager can tell the duplicates apart; falls back
  // to the catalog supplier for the common (non-duplicate) case.
  meta.textContent = (res.review_supplier || res.supplier || '—') + ' · '
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
  if (res.in_review && res.review_key) {
    // Match by the row's OWN review_key — the EXACT product THIS row represents. #64:
    // a pairCode reviewed under 2+ suppliers (GRUBE|425 AND WETLAND|425) yields multiple
    // rows for the same catalog entry; matching by pairCode/shared-code alone (the old
    // approach) always finds the FIRST such product no matter which row was clicked, so
    // every duplicate past the first was unreachable/unfixable. review_key removes the
    // ambiguity — each row opens its own product.
    const product = PRODUCTS.find(p => p.key === res.review_key);
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
        // review_key (#64): when this row IS a specific duplicate (res.in_review true but
        // its product wasn't found client-side, above), targets that exact review entry
        // instead of letting the server fall back to its first-match scan.
        body: JSON.stringify({ key: res.key, url: v, review_key: res.review_key || '' })
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
// ── Poľovnícke výstavy (#111) — KARTY (nie tabuľka) ────────────────────────
// Canonical state → SK label + colour class (matches app.py's VY_* states).
const VY_STATUS = {
  '': { label: 'Nová', cls: 'nova' },
  'otazka': { label: 'Otázka poslaná', cls: 'otazka' },
  'akcia bude': { label: 'Odpovedali — čaká na rozhodnutie', cls: 'akcia' },
  'poziadane': { label: 'Prihláška poslaná', cls: 'poziadane' },
  'odpovedane od organizatora': { label: 'Potvrdené', cls: 'hotovo' },
};
// Display group order: needs-decision first, then new, then in-flight, then done.
const VY_ORDER = ['akcia bude', '', 'otazka', 'poziadane', 'odpovedane od organizatora'];
// Editable fields (mirror app.py's VY_EDIT_FIELDS, minus sposob which is a select).
const VY_FIELDS = [
  ['nazov', 'Názov'], ['datum', 'Dátum'], ['miesto', 'Miesto'],
  ['kontakt_osoba', 'Kontaktná osoba'], ['tel', 'Telefón'], ['email', 'E-mail'],
  ['velkost_stanku', 'Veľkosť stánku'], ['kedy_riesit', 'Kedy riešiť (mesiac)'],
];

async function loadVystavy() {
  try {
    VYSTAVY = (await (await fetch('/api/vystavy')).json()).vystavy || [];
  } catch (_) { VYSTAVY = []; }
}

function vyStatus(v) { return VY_STATUS[v.status || ''] || VY_STATUS['']; }

function vyFmtTs(iso) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso || '';
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// ── network actions (each reloads + re-renders on success) ──────────────────
async function vyReload() { await loadVystavy(); renderVystavy(); }

async function vyPost(path, payload, failMsg) {
  let j = {};
  try {
    const r = await fetch(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    j = await r.json().catch(() => ({}));
    if (!r.ok || !j.ok) { alert(j.error || failMsg); return false; }
  } catch (_) { alert(failMsg); return false; }
  return true;
}

async function vyAdd(fields) {
  if (await vyPost('/api/vystavy', fields, 'Výstavu sa nepodarilo pridať.')) {
    VY_ADD_OPEN = false; await vyReload();
  }
}
async function vySave(id, fields) {
  if (await vyPost('/api/vystava', { id, fields }, 'Zmeny sa nepodarilo uložiť.')) await vyReload();
}
async function vyDelete(id) {
  if (!confirm('Naozaj zmazať túto výstavu?')) return;
  if (await vyPost('/api/vystava', { id, delete: true }, 'Nepodarilo sa zmazať.')) {
    VY_OPEN.delete(id); await vyReload();
  }
}
async function vySetStatus(id, status) {
  if (await vyPost('/api/vystava', { id, status }, 'Stav sa nepodarilo zmeniť.')) await vyReload();
}
async function vySend(id, path, failMsg) {
  if (await vyPost(path, { id }, failMsg)) await vyReload();
}

// ── rendering ───────────────────────────────────────────────────────────────
function vyFieldsForm(v, prefix) {
  const form = el('div', 'vy-form');
  const inputs = {};
  for (const [key, label] of VY_FIELDS) {
    const row = el('label', 'vy-field');
    row.appendChild(el('span', 'vy-flabel', escapeHtml(label) + (key === 'nazov' ? ' *' : '')));
    const inp = el('input');
    inp.type = 'text';
    inp.value = (v && v[key]) || '';
    if (prefix) inp.dataset.testid = prefix + key;
    inputs[key] = inp;
    row.appendChild(inp);
    form.appendChild(row);
  }
  // sposob = select (email = automat can mail, pdf = manual only)
  const sRow = el('label', 'vy-field');
  sRow.appendChild(el('span', 'vy-flabel', 'Spôsob prihlášky'));
  const sel = document.createElement('select');
  for (const [val, lbl] of [['email', 'E-mail (automat)'], ['pdf', 'PDF (ručne)']]) {
    const o = document.createElement('option');
    o.value = val; o.textContent = lbl;
    if (((v && v.sposob) || 'email') === val) o.selected = true;
    sel.appendChild(o);
  }
  if (prefix) sel.dataset.testid = prefix + 'sposob';
  inputs.sposob = sel;
  sRow.appendChild(sel);
  form.appendChild(sRow);
  return { form, inputs };
}

function vyCollect(inputs) {
  const f = {};
  for (const k in inputs) f[k] = inputs[k].value.trim();
  return f;
}

function vyAddForm() {
  const box = el('div', 'vy-detail vy-add');
  const { form, inputs } = vyFieldsForm(null, 'vy-add-');
  box.appendChild(form);
  const acts = el('div', 'vy-detail-acts');
  const create = el('button', 'btn good sm', '➕ Vytvoriť');
  create.dataset.testid = 'vy-add-submit';
  create.onclick = () => {
    const f = vyCollect(inputs);
    if (!f.nazov) { alert('Názov výstavy je povinný.'); return; }
    vyAdd(f);
  };
  const cancel = el('button', 'btn ghost sm', 'Zrušiť');
  cancel.onclick = () => { VY_ADD_OPEN = false; renderVystavy(); };
  acts.appendChild(create); acts.appendChild(cancel);
  box.appendChild(acts);
  return box;
}

// per-state action button on the card (null when the state just waits on the organizer)
function vyAction(v) {
  const st = v.status || '';
  if (st === '') {
    const b = el('button', 'btn vy-act primary', '✉ Pošli otázku');
    b.dataset.testid = 'vy-otazka-' + v.id;
    b.onclick = (e) => { e.stopPropagation(); vySend(v.id, '/api/vystava/posli-otazku', 'E-mail sa nepodarilo odoslať.'); };
    return b;
  }
  if (st === 'akcia bude') {
    const b = el('button', 'btn vy-act go', '✅ Ideme na túto výstavu');
    b.dataset.testid = 'vy-ideme-' + v.id;
    b.onclick = (e) => { e.stopPropagation(); vySend(v.id, '/api/vystava/ideme', 'Prihlášku sa nepodarilo odoslať.'); };
    return b;
  }
  return null;
}

function vyDetail(v) {
  const d = el('div', 'vy-detail');
  const { form, inputs } = vyFieldsForm(v, null);
  d.appendChild(form);
  const acts = el('div', 'vy-detail-acts');
  const save = el('button', 'btn good sm', '💾 Uložiť');
  save.onclick = () => vySave(v.id, vyCollect(inputs));
  const del = el('button', 'btn warn sm', '🗑 Zmazať');
  del.dataset.testid = 'vy-del-' + v.id;
  del.onclick = () => vyDelete(v.id);
  acts.appendChild(save); acts.appendChild(del);
  d.appendChild(acts);
  // manual status reset dropdown
  const stRow = el('div', 'vy-status-reset');
  stRow.appendChild(el('span', 'vy-flabel', 'Stav (ručne)'));
  const stSel = document.createElement('select');
  for (const st of VY_ORDER) {
    const o = document.createElement('option');
    o.value = st; o.textContent = VY_STATUS[st].label;
    if ((v.status || '') === st) o.selected = true;
    stSel.appendChild(o);
  }
  stSel.onchange = () => vySetStatus(v.id, stSel.value);
  stRow.appendChild(stSel);
  d.appendChild(stRow);
  // info feed (chronology — newest first)
  if (v.feed && v.feed.length) {
    const feed = el('div', 'vy-feed');
    feed.appendChild(el('div', 'vy-feed-h', 'História'));
    for (const f of v.feed) {
      const item = el('div', 'vy-feed-item');
      const ts = el('span', 'vy-feed-ts'); ts.textContent = vyFmtTs(f.ts);
      const tx = el('span', 'vy-feed-tx'); tx.textContent = f.text || '';   // .textContent → XSS-safe
      item.appendChild(ts); item.appendChild(tx);
      feed.appendChild(item);
    }
    d.appendChild(feed);
  }
  return d;
}

function vyCard(v) {
  const st = vyStatus(v);
  const card = el('div', 'vy-card ' + st.cls);
  card.dataset.testid = 'vy-card-' + v.id;
  const head = el('div', 'vy-head');
  head.onclick = () => {
    if (VY_OPEN.has(v.id)) VY_OPEN.delete(v.id); else VY_OPEN.add(v.id);
    renderVystavy();
  };
  const title = el('div', 'vy-title');
  title.textContent = v.nazov || '(bez názvu)';   // .textContent → XSS-safe
  head.appendChild(title);
  const badge = el('span', 'vy-badge ' + st.cls);
  badge.textContent = st.label;
  head.appendChild(badge);
  card.appendChild(head);
  // meta lines
  const m1 = el('div', 'vy-meta');
  m1.textContent = [v.datum, v.miesto].filter(Boolean).join(' · ') || '—';
  card.appendChild(m1);
  const contact = [v.kontakt_osoba, v.tel, v.email].filter(Boolean).join(' · ');
  const m2 = el('div', 'vy-meta2');
  m2.textContent = [contact, v.velkost_stanku ? 'stánok ' + v.velkost_stanku : '',
    v.kedy_riesit ? 'riešiť: ' + v.kedy_riesit : ''].filter(Boolean).join(' · ') || '—';
  card.appendChild(m2);
  if (v.sposob === 'pdf') {
    const flag = el('div', 'vy-pdf', '📄 Prihláška ručne (PDF) — automat mail neposiela');
    card.appendChild(flag);
  }
  const act = vyAction(v);
  if (act) card.appendChild(act);
  if (VY_OPEN.has(v.id)) card.appendChild(vyDetail(v));
  return card;
}

function renderVystavy() {
  const sec = document.getElementById('tab-vystavy');
  if (!sec) return;
  sec.innerHTML = '';
  const top = el('div', 'vy-top');
  const addBtn = el('button', 'btn good', '➕ Pridať výstavu');
  addBtn.dataset.testid = 'vy-add-btn';
  addBtn.onclick = () => { VY_ADD_OPEN = !VY_ADD_OPEN; renderVystavy(); };
  top.appendChild(addBtn);
  sec.appendChild(top);
  if (VY_ADD_OPEN) sec.appendChild(vyAddForm());
  const list = VYSTAVY || [];
  if (!list.length && !VY_ADD_OPEN) {
    sec.appendChild(el('div', 'vy-empty',
      'Žiadne výstavy. Pridaj prvú tlačidlom <strong>„➕ Pridať výstavu"</strong>.'));
    return;
  }
  for (const st of VY_ORDER) {
    const group = list.filter(v => (v.status || '') === st);
    if (!group.length) continue;
    const h = el('div', 'vy-group ' + VY_STATUS[st].cls);
    h.textContent = `${VY_STATUS[st].label} (${group.length})`;
    sec.appendChild(h);
    for (const v of group) sec.appendChild(vyCard(v));
  }
}

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

// ---- Vývoj (#115): GitHub issues list + idea lightbulb -------------------- //
async function loadDevIssues() {
  try { DEV = await (await fetch('/api/dev/issues')).json(); }
  catch (_) { DEV = { available: false, issues: [] }; }
}

const DEV_FILTERS = [['open', 'Otvorené'], ['closed', 'Hotové'], ['all', 'Všetky']];

function _devDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return isNaN(d) ? '' : d.toLocaleDateString('sk-SK', { day: 'numeric', month: 'numeric', year: 'numeric' });
}

function renderDev() {
  const wrap = document.getElementById('tab-dev');
  if (!wrap) return;
  wrap.innerHTML = '';
  if (!DEV || !DEV.available) {
    wrap.appendChild(el('div', 'empty2',
      '⚠️ GitHub nedostupný — zoznam úloh sa teraz nedá načítať.<br>'
      + escapeHtml((DEV && DEV.error) || 'Skontroluj nastavenie (data/.gh_env).')));
    return;
  }
  const issues = DEV.issues || [];
  const openN = issues.filter(i => i.state === 'open').length;
  const closedN = issues.length - openN;
  const fbar = el('div', 'dev-filters');
  for (const [key, lbl] of DEV_FILTERS) {
    const n = key === 'open' ? openN : key === 'closed' ? closedN : issues.length;
    const b = el('button', DEV_FILTER === key ? 'active' : '', `${escapeHtml(lbl)} (${n})`);
    b.onclick = () => { DEV_FILTER = key; renderDev(); };
    fbar.appendChild(b);
  }
  wrap.appendChild(fbar);
  const shown = issues.filter(i => DEV_FILTER === 'all' ? true : i.state === DEV_FILTER);
  if (!shown.length) {
    wrap.appendChild(el('div', 'srch-empty', 'Žiadne úlohy v tomto filtri.'));
    return;
  }
  // Split by the boss's priority: „Riešiť čoskoro" on top, unprioritised in the
  // middle, „Riešiť neskôr" at the bottom (#Vývoj priority).
  const byPrio = { soon: [], '': [], later: [] };
  for (const it of shown) (byPrio[it.priority] || byPrio['']).push(it);
  const groups = [['soon', '🔴 Riešiť čoskoro'], ['', ''], ['later', '🟡 Riešiť neskôr']];
  const list = el('div', 'dev-list');
  for (const [key, label] of groups) {
    const items = byPrio[key];
    if (!items.length) continue;
    if (label) list.appendChild(el('div', 'dev-group ' + key, label));
    for (const it of items) list.appendChild(renderDevRow(it));
  }
  wrap.appendChild(list);
}

function renderDevRow(it) {
  const row = el('div', 'dev-row' + (it.state === 'closed' ? ' closed' : '')
                 + (it.priority ? ' prio-' + it.priority : ''));
  const head = el('div', 'dev-head');
  head.appendChild(el('span', 'dev-num', '#' + it.number));
  // GitHub is fully hidden — the title is NOT a link out. Clicking it opens the
  // in-app detail (issue text + all details/comments); adding is open-only.
  const canAdd = it.state !== 'closed';
  const title = el('span', 'dev-title clickable', escapeHtml(it.title || '(bez názvu)'));
  title.onclick = () => _devToggleDetail(row, it.number, canAdd);
  head.appendChild(title);
  head.appendChild(el('span', 'dev-state ' + (it.state === 'closed' ? 'done' : 'open'),
    it.state === 'closed' ? 'Hotové' : 'Otvorené'));
  row.appendChild(head);
  const meta = el('div', 'dev-meta');
  for (const lbl of (it.labels || [])) meta.appendChild(el('span', 'dev-label', escapeHtml(lbl)));
  if (it.comments) meta.appendChild(el('span', 'dev-cmt', '💬 ' + it.comments));
  const upd = _devDate(it.updated_at);
  if (upd) meta.appendChild(el('span', 'dev-upd', 'upravené ' + upd));
  if ((it.labels || []).length || it.comments || upd) row.appendChild(meta);
  // Boss controls — open issues only (closed ones are read-only history):
  // set priority (čoskoro/neskôr) + add a detail note. GitHub stays hidden.
  if (it.state !== 'closed') {
    const act = el('div', 'dev-actions');
    const cur = it.priority || 'none';
    for (const [key, lbl] of [['soon', '🔴 Čoskoro'], ['later', '🟡 Neskôr'], ['none', '— Bez priority']]) {
      const b = el('button', 'dev-prio' + (key === cur ? ' active' : ''), lbl);
      b.onclick = () => _devSetPriority(it.number, key === cur ? 'none' : key);
      act.appendChild(b);
    }
    const noteBtn = el('button', 'dev-note-btn', '🔎 Detail / doplniť');
    noteBtn.onclick = () => _devToggleDetail(row, it.number, true);
    act.appendChild(noteBtn);
    row.appendChild(act);
  }
  return row;
}

// Set the boss's priority for an issue, then refresh the split. GitHub hidden.
async function _devSetPriority(number, priority) {
  let ok = false, err = '';
  try {
    const r = await fetch(`/api/dev/issue/${number}/priority`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ priority }),
    });
    const j = await r.json(); ok = j.ok; err = j.error || '';
  } catch (_) { err = 'sieť'; }
  if (!ok) { window.alert('Nepodarilo sa nastaviť prioritu: ' + err); return; }
  await loadDevIssues();
  renderDev();
}

// Toggle the in-app DETAIL of an issue: its full text (zadanie) + ALL details/
// comments + (open issues) an editor to add more. Everything shows IN the app —
// GitHub stays hidden. `canAdd` gates the editor (closed issues are read-only).
async function _devToggleDetail(row, number, canAdd) {
  const existing = row.querySelector('.dev-detail-box');
  if (existing) { existing.remove(); return; }
  const box = el('div', 'dev-detail-box');
  box.appendChild(el('div', 'dev-detail-load', 'Načítavam detail…'));
  row.appendChild(box);
  let data = null;
  try { data = await (await fetch(`/api/dev/issue/${number}`)).json(); }
  catch (_) { data = { ok: false, error: 'sieť' }; }
  if (!data.ok) {
    box.innerHTML = '';
    box.appendChild(el('div', 'dev-detail-err',
      'Nepodarilo sa načítať detail: ' + escapeHtml(data.error || '')));
    return;
  }
  _renderDetail(box, number, data, canAdd);
}

// Render (or re-render) an issue's detail into `box`: zadanie + comments + editor.
function _renderDetail(box, number, data, canAdd) {
  box.innerHTML = '';
  if (data.body && data.body.trim()) {
    box.appendChild(el('div', 'dev-detail-h', 'Zadanie'));
    const b = el('div', 'dev-detail-body');
    b.textContent = data.body;                 // textContent → no XSS, no HTML render
    box.appendChild(b);
  }
  const comments = data.comments || [];
  box.appendChild(el('div', 'dev-detail-h',
    'Detaily' + (comments.length ? ' (' + comments.length + ')' : '')));
  if (!comments.length) {
    box.appendChild(el('div', 'dev-detail-empty',
      canAdd ? 'Zatiaľ žiadny detail. Napíš prvý nižšie.' : 'Zatiaľ žiadny detail.'));
  } else {
    for (const c of comments) {
      const cm = el('div', 'dev-comment');
      const d = _devDate(c.created_at);
      if (d) cm.appendChild(el('div', 'dev-comment-date', d));
      const body = el('div', 'dev-comment-body');
      body.textContent = c.body || '';         // textContent → no XSS
      cm.appendChild(body);
      box.appendChild(cm);
    }
  }
  if (!canAdd) return;                          // closed issue → read-only
  const ta = el('textarea', 'dev-note-ta');
  ta.placeholder = 'Napíš ďalší detail…';
  ta.maxLength = 5000;
  const bar = el('div', 'dev-note-bar');
  const save = el('button', 'dev-note-save', 'Uložiť detail');
  const msg = el('span', 'dev-note-msg', '');
  save.onclick = async () => {
    const text = ta.value.trim();
    if (!text) { msg.textContent = 'Napíš aspoň nejaký text.'; msg.className = 'dev-note-msg err'; return; }
    save.disabled = true; msg.textContent = 'Ukladám…'; msg.className = 'dev-note-msg';
    let ok = false, err = '';
    try {
      const r = await fetch(`/api/dev/issue/${number}/note`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      const j = await r.json(); ok = j.ok; err = j.error || '';
    } catch (_) { err = 'sieť'; }
    if (!ok) {
      save.disabled = false;
      msg.textContent = 'Nepodarilo sa: ' + err; msg.className = 'dev-note-msg err';
      return;
    }
    // re-fetch so the just-added detail SHOWS in the list (no more „it vanished")
    let fresh = null;
    try { fresh = await (await fetch(`/api/dev/issue/${number}`)).json(); } catch (_) {}
    if (fresh && fresh.ok) _renderDetail(box, number, fresh, canAdd);
    else {                                       // fell back — at least confirm + clear
      save.disabled = false; ta.value = '';
      msg.textContent = 'Detail uložený ✓'; msg.className = 'dev-note-msg ok';
    }
  };
  bar.appendChild(save); bar.appendChild(msg);
  box.appendChild(ta); box.appendChild(bar);
  ta.focus();
}

// Idea lightbulb — any logged-in user writes an idea → POST /api/dev/idea creates a
// GitHub issue that then appears in the Vývoj list. The token stays server-side.
function _ideaMsg(text, cls) {
  const msg = document.getElementById('ideaMsg');
  if (!msg) return;
  if (!text) { msg.hidden = true; msg.textContent = ''; return; }
  msg.hidden = false; msg.className = 'idea-msg' + (cls ? ' ' + cls : ''); msg.textContent = text;
}
function _ideaOpen() {
  const m = document.getElementById('ideaModal'); if (!m) return;
  document.getElementById('ideaTitleInput').value = '';
  document.getElementById('ideaDescInput').value = '';
  _ideaMsg('');
  m.hidden = false;
  document.getElementById('ideaTitleInput').focus();
}
function _ideaClose() {
  const m = document.getElementById('ideaModal'); if (m) m.hidden = true;
}
async function _ideaSubmit() {
  const ti = document.getElementById('ideaTitleInput');
  const de = document.getElementById('ideaDescInput');
  const btn = document.getElementById('ideaSubmit');
  const title = ti.value.trim();
  if (!title) { _ideaMsg('Napíš aspoň názov nápadu.', 'err'); ti.focus(); return; }
  btn.disabled = true;
  let ok = false, err = '';
  try {
    const r = await fetch('/api/dev/idea', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, description: de.value.trim() }),
    });
    const j = await r.json().catch(() => ({}));
    ok = r.ok && j.ok;
    if (!ok) err = j.error || ('chyba ' + r.status);
  } catch (_) { err = 'sieťová chyba'; }
  btn.disabled = false;
  if (!ok) { _ideaMsg('Nepodarilo sa: ' + err, 'err'); return; }
  _ideaClose();
  await loadDevIssues();                       // new issue appears + nav badge updates
  if (ACTIVE_TAB === 'dev') render(); else renderTabs();
}
function initIdea() {
  const btn = document.getElementById('ideaBtn'); if (btn) btn.onclick = _ideaOpen;
  const cancel = document.getElementById('ideaCancel'); if (cancel) cancel.onclick = _ideaClose;
  const back = document.getElementById('ideaBackdrop'); if (back) back.onclick = _ideaClose;
  const submit = document.getElementById('ideaSubmit'); if (submit) submit.onclick = _ideaSubmit;
  const ti = document.getElementById('ideaTitleInput');
  if (ti) ti.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); _ideaSubmit(); }
    else if (e.key === 'Escape') _ideaClose();
  });
}

// ---- Automatizácie (#93): tab „Nevyzdvihnuté zásielky" -------------------- //
// ---- + „Sync zo Shoptetu" (#119, plain status-only tab, no per-item table) - //
async function loadAutomations() {
  try { AUTOMATIONS = (await (await fetch('/api/automations')).json()).automations || []; }
  catch (_) { AUTOMATIONS = []; }
}

// Admin-set custom nav/automation names (#173) — GET is open to every logged-in
// user, so a renamed tab shows its new name for everyone, not just the admin.
async function loadUiLabels() {
  try { UI_LABELS = (await (await fetch('/api/ui-labels')).json()).labels || {}; }
  catch (_) { UI_LABELS = {}; }
}

// Admin-only rename of one nav tab / automation (pencil next to its nav button).
// Empty input clears the override (reverts to the built-in default name).
async function renameNavItem(key, defaultLbl) {
  const current = UI_LABELS[key] || defaultLbl;
  const next = prompt(
    `Nový názov záložky (pôvodný: "${defaultLbl}"). Prázdne pole = vrátiť pôvodný názov.`,
    current);
  if (next === null) return;   // cancelled
  const label = next.trim();
  const r = await fetch('/api/ui-label', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key, label }),
  });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    alert(j.error || 'Nepodarilo sa premenovať.');
    return;
  }
  if (label) UI_LABELS[key] = label; else delete UI_LABELS[key];
  render();
}

async function loadPosta() {
  await loadAutomations();
  try { POSTA = await (await fetch('/api/posta-uncollected')).json(); }
  catch (_) { POSTA = null; }
}

async function loadSupplierStock() {
  await loadAutomations();
  try { SUPPLIER_STOCK = await (await fetch('/api/supplier-stock')).json(); }
  catch (_) { SUPPLIER_STOCK = null; }
}

async function loadRiziko() {
  await loadAutomations();
  try { RIZIKO = await (await fetch('/api/riziko-vypadku')).json(); }
  catch (_) { RIZIKO = null; }
}

async function loadRestock() {
  await loadAutomations();
  try { RESTOCK = await (await fetch('/api/restock-skladom')).json(); }
  catch (_) { RESTOCK = null; }
}

async function loadStockSkladom() {
  await loadAutomations();
  try { STOCK_SKLADOM = await (await fetch('/api/stock-skladom')).json(); }
  catch (_) { STOCK_SKLADOM = null; }
}

async function loadOrdersReminder() {
  await loadAutomations();
  try { ORDERS_REMINDER = await (await fetch('/api/orders-reminder')).json(); }
  catch (_) { ORDERS_REMINDER = null; }
}

// Reload AUTOMATIONS + the active tab's display data (used by toggle + run poll,
// so a live run refreshes whichever automation tab is open).
async function _reloadAuto(tab) {
  if (tab === 'dodavatelsky_sklad') { await loadSupplierStock(); return; }
  if (tab === 'riziko_vypadku') { await loadRiziko(); return; }
  if (tab === 'restock_skladom') { await loadRestock(); return; }
  if (tab === 'stock_skladom') { await loadStockSkladom(); return; }
  if (tab === 'orders_reminder') { await loadOrdersReminder(); return; }
  await loadPosta();   // loads AUTOMATIONS too; POSTA fetch is harmless elsewhere
}

function autoByKey(key) { return AUTOMATIONS.find(x => x.key === key); }

async function toggleAutomation(key, enabled) {
  await fetch(`/api/automations/${key}/toggle`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });
  await _reloadAuto(ACTIVE_TAB); render();
}

let _postaPoll = null;
// `tab` = which sidebar tab must stay open for polling to continue (default
// 'posta' keeps the original single-caller behavior unchanged).
async function runAutomation(key, tab = 'posta') {
  await fetch(`/api/automations/${key}/run`, { method: 'POST' });
  await _reloadAuto(tab); render();
  clearInterval(_postaPoll);
  _postaPoll = setInterval(async () => {           // refresh until the run ends
    if (ACTIVE_TAB !== tab) { clearInterval(_postaPoll); _postaPoll = null; return; }
    await _reloadAuto(tab); render();
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
    if (a.description) st.appendChild(el('div', 'autodesc', escapeHtml(a.description)));

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

// ---- Automatizácie (#119): tab „Sync zo Shoptetu" -------------------------- //
// Plain status-only tab (no per-item table like posta — a sync run has nothing
// to list, just counts) — status/controls come straight from AUTOMATIONS
// (last_result), no dedicated display endpoint needed.
function renderShoptetSync() {
  const wrap = document.getElementById('tab-shoptet_sync');
  if (!wrap) return;
  wrap.innerHTML = '';
  const a = autoByKey('shoptet_sync');
  if (!a) {
    wrap.appendChild(el('div', 'muted', 'Automatizácia nie je dostupná (server nevrátil stav).'));
    return;
  }
  const st = el('div', 'autostatus');
  const head = el('div', 'autohead');
  const pill = el('span', 'pill ' + (a.enabled ? 'on' : 'off'), a.enabled ? 'Beží' : 'Zastavené');
  pill.dataset.testid = 'shoptet-sync-status';
  head.appendChild(pill);
  if (a.running) head.appendChild(el('span', 'runningdot', '⏳ práve prebieha synchronizácia…'));
  const btn = el('button', 'btn sm ' + (a.enabled ? 'warn' : 'good'),
    a.enabled ? '⏹ Stop' : '▶ Štart');
  btn.dataset.testid = 'shoptet-sync-toggle';
  btn.onclick = () => toggleAutomation('shoptet_sync', !a.enabled);
  head.appendChild(btn);
  const run = el('button', 'btn sm ghost', '⚡ Spustiť teraz');
  run.dataset.testid = 'shoptet-sync-run';
  run.disabled = !!a.running;
  run.onclick = () => runAutomation('shoptet_sync', 'shoptet_sync');
  head.appendChild(run);
  st.appendChild(head);
  if (a.description) st.appendChild(el('div', 'autodesc', escapeHtml(a.description)));

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
      `Objednávky: ${(lr.orders_bytes || 0).toLocaleString('sk-SK')} B stiahnuté`
      + ` · katalóg: ${lr.catalog_products ?? 0} produktov (${lr.catalog_codes ?? 0} kódov)`
      + ` · zosynchronizované review karty: ${lr.review_synced ?? 0}`
      + (lr.review_stale ? ` (nenájdených v exporte: ${lr.review_stale})` : '')));
  }
  wrap.appendChild(st);
}

// ---- Automatizácie (#135): tab „Kontrola obrázkov" -------------------------- //
// Plain status-only tab (like Sync zo Shoptetu) — periodic HEAD-check of our own
// review-card image URLs (our_images). Nothing to browse per-item; just counts +
// last-run. The actual fix (dead URL never served) happens in /api/products —
// this tab only shows what the background check found.
function renderImageHealth() {
  const wrap = document.getElementById('tab-image_health');
  if (!wrap) return;
  wrap.innerHTML = '';
  const a = autoByKey('image_health');
  if (!a) {
    wrap.appendChild(el('div', 'muted', 'Automatizácia nie je dostupná (server nevrátil stav).'));
    return;
  }
  const st = el('div', 'autostatus');
  const head = el('div', 'autohead');
  const pill = el('span', 'pill ' + (a.enabled ? 'on' : 'off'), a.enabled ? 'Beží' : 'Zastavené');
  pill.dataset.testid = 'image-health-status';
  head.appendChild(pill);
  if (a.running) head.appendChild(el('span', 'runningdot', '⏳ práve prebieha kontrola…'));
  const btn = el('button', 'btn sm ' + (a.enabled ? 'warn' : 'good'),
    a.enabled ? '⏹ Stop' : '▶ Štart');
  btn.dataset.testid = 'image-health-toggle';
  btn.onclick = () => toggleAutomation('image_health', !a.enabled);
  head.appendChild(btn);
  const run = el('button', 'btn sm ghost', '⚡ Spustiť teraz');
  run.dataset.testid = 'image-health-run';
  run.disabled = !!a.running;
  run.onclick = () => runAutomation('image_health', 'image_health');
  head.appendChild(run);
  st.appendChild(head);
  if (a.description) st.appendChild(el('div', 'autodesc', escapeHtml(a.description)));

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
      `Skontrolovaných: ${lr.checked ?? 0} (preskočených ako čerstvé: ${lr.skipped ?? 0})`
      + ` · živé: ${lr.ok ?? 0} · zlyhalo: ${lr.failed ?? 0}`
      + ` · mŕtvych odkazov: ${lr.dead_urls ?? 0} (odstránených z kariet: ${lr.cleaned_images ?? 0})`));
  }
  wrap.appendChild(st);
}

// ---- Automatizácie (#109): tab „Párovania → eshop" ------------------------- //
// Status-only tab (like Sync zo Shoptetu): the nightly push of new pairings +
// assigned suppliers to the eshop. WRITES to the live shop → default Zastavené;
// last_result.status (ok/blocked/failed) colours the counts line.
const _PAROVANIA_STATUS = {
  ok: ['✅ OK', 'ok'], blocked: ['⚠️ Časť zablokovaná', 'warn'],
  failed: ['❌ Zlyhalo', 'bad'],
};
function renderParovaniaEshop() {
  const wrap = document.getElementById('tab-parovania_eshop');
  if (!wrap) return;
  wrap.innerHTML = '';
  const a = autoByKey('parovania_eshop');
  if (!a) {
    wrap.appendChild(el('div', 'muted', 'Automatizácia nie je dostupná (server nevrátil stav).'));
    return;
  }
  const st = el('div', 'autostatus');
  const head = el('div', 'autohead');
  const pill = el('span', 'pill ' + (a.enabled ? 'on' : 'off'), a.enabled ? 'Beží' : 'Zastavené');
  pill.dataset.testid = 'parovania-status';
  head.appendChild(pill);
  if (a.running) head.appendChild(el('span', 'runningdot', '⏳ práve prebieha nahrávanie…'));
  const btn = el('button', 'btn sm ' + (a.enabled ? 'warn' : 'good'),
    a.enabled ? '⏹ Stop' : '▶ Štart');
  btn.dataset.testid = 'parovania-toggle';
  btn.onclick = () => toggleAutomation('parovania_eshop', !a.enabled);
  head.appendChild(btn);
  const run = el('button', 'btn sm ghost', '⚡ Spustiť teraz');
  run.dataset.testid = 'parovania-run';
  run.disabled = !!a.running;
  run.onclick = () => runAutomation('parovania_eshop', 'parovania_eshop');
  head.appendChild(run);
  st.appendChild(head);
  if (a.description) st.appendChild(el('div', 'autodesc', escapeHtml(a.description)));

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
  if (a.last_run && a.last_status === 'ok' && lr.status) {
    const p = lr.pairings || {}, s = lr.suppliers || {};
    const [label, cls] = _PAROVANIA_STATUS[lr.status] || [lr.status, 'ok'];
    const box = el('div', 'autoresult ' + cls);
    box.appendChild(el('div', 'autoresult-head', label));
    box.appendChild(el('div', '',
      `🔗 Párovania: +${p.count ?? 0} nových`
      + (p.blocked ? ` · ${p.blocked} zablokovaných (chýbajú kódy)` : '')
      + ` · spolu ${p.total_uploaded ?? 0} / ${p.total_products ?? 0} napárovaných`
      + ` · chýba ${p.remaining ?? 0}`));
    // #156: on a chunk failure, show WHICH chunk failed + how many rows made it (the
    // successful chunks ARE saved → the next run only retries the rest)
    if (p.error) box.appendChild(el('div', 'sub2 err', '❌ ' + escapeHtml(p.error)));
    // #38: inline páry pridané priamo na riadku „Na objednanie" (mimo review setu)
    box.appendChild(el('div', '',
      `📦 Inline páry: +${p.order_count ?? 0} nových`
      + (p.order_blocked ? ` · ${p.order_blocked} prekrytých recenziou` : '')));
    box.appendChild(el('div', '',
      `🏷️ Dodávatelia: +${s.count ?? 0} nových`
      + (s.blocked ? ` · ${s.blocked} zablokovaných (chýbajú kódy)` : '')
      + ` · spolu ${s.total_uploaded ?? 0} / ${s.total_assigned ?? 0} doplnených`
      + ` · chýba ${s.remaining ?? 0}`));
    if (s.error) box.appendChild(el('div', 'sub2 err', '❌ ' + escapeHtml(s.error)));
    st.appendChild(box);
  } else if (!a.last_run) {
    st.appendChild(el('div', 'muted',
      'Zatiaľ neprebehol žiadny beh — spusti automatizáciu (▶ Štart) alebo klikni ⚡ Spustiť teraz.'));
  }
  wrap.appendChild(st);
}

// ---- Automatizácie (#62): tab „GRUBE kódy → eshop" ------------------------ //
// Nočný upload GRUBE per-veľkosť kódov (itemId → eshop externalCode). PÍŠE do
// eshopu → štartuje Zastavené (#93). Samostatná od „Párovania → eshop" (tá je na
// prode zapnutá; externalCode je iné pole) — zapnutie je explicitný opt-in.
function renderGrubeExternalcode() {
  const wrap = document.getElementById('tab-grube_externalcode');
  if (!wrap) return;
  wrap.innerHTML = '';
  const a = autoByKey('grube_externalcode');
  if (!a) {
    wrap.appendChild(el('div', 'muted', 'Automatizácia nie je dostupná (server nevrátil stav).'));
    return;
  }
  const st = el('div', 'autostatus');
  const head = el('div', 'autohead');
  const pill = el('span', 'pill ' + (a.enabled ? 'on' : 'off'), a.enabled ? 'Beží' : 'Zastavené');
  pill.dataset.testid = 'grubeext-status';
  head.appendChild(pill);
  if (a.running) head.appendChild(el('span', 'runningdot', '⏳ práve prebieha nahrávanie…'));
  const btn = el('button', 'btn sm ' + (a.enabled ? 'warn' : 'good'),
    a.enabled ? '⏹ Stop' : '▶ Štart');
  btn.dataset.testid = 'grubeext-toggle';
  btn.onclick = () => toggleAutomation('grube_externalcode', !a.enabled);
  head.appendChild(btn);
  const run = el('button', 'btn sm ghost', '⚡ Spustiť teraz');
  run.dataset.testid = 'grubeext-run';
  run.disabled = !!a.running;
  run.onclick = () => runAutomation('grube_externalcode', 'grube_externalcode');
  head.appendChild(run);
  st.appendChild(head);
  if (a.description) st.appendChild(el('div', 'autodesc', escapeHtml(a.description)));

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
  if (a.last_run && a.last_status === 'ok' && lr.status) {
    const e = lr.externalcodes || {};
    const [label, cls] = _PAROVANIA_STATUS[lr.status] || [lr.status, 'ok'];
    const box = el('div', 'autoresult ' + cls);
    box.appendChild(el('div', 'autoresult-head', label));
    box.appendChild(el('div', '',
      `🏷️ GRUBE kódy: +${e.count ?? 0} nových`
      + (e.blocked ? ` · ${e.blocked} zablokovaných (chýbajú kódy)` : '')
      + ` · spolu ${e.total_uploaded ?? 0} / ${e.total_codes ?? 0} nahraných`
      + ` · chýba ${e.remaining ?? 0}`));
    // #156: on a chunk failure, show WHICH chunk failed + how many rows made it (the
    // successful chunks ARE saved → the next run only retries the rest)
    if (e.error) box.appendChild(el('div', 'sub2 err', '❌ ' + escapeHtml(e.error)));
    st.appendChild(box);
  } else if (!a.last_run) {
    st.appendChild(el('div', 'muted',
      'Zatiaľ neprebehol žiadny beh — spusti automatizáciu (▶ Štart) alebo klikni ⚡ Spustiť teraz.'));
  }
  wrap.appendChild(st);
}

// ---- Automatizácie (#192): tab „Veľkostné linky → eshop" ------------------ //
// Nočný upload per-veľkosť split-linkov (#174 „✂ Rozdeliť na veľkosti") do eshopu
// internalNote, per variant. PÍŠE do eshopu → štartuje Zastavené (#93). Samostatná
// od „Párovania → eshop" (split decision nemá decision URL, linky sú vo variant_links
// per kód) — zapnutie je explicitný opt-in.
function renderSplitLinks() {
  const wrap = document.getElementById('tab-split_links');
  if (!wrap) return;
  wrap.innerHTML = '';
  const a = autoByKey('split_links');
  if (!a) {
    wrap.appendChild(el('div', 'muted', 'Automatizácia nie je dostupná (server nevrátil stav).'));
    return;
  }
  const st = el('div', 'autostatus');
  const head = el('div', 'autohead');
  const pill = el('span', 'pill ' + (a.enabled ? 'on' : 'off'), a.enabled ? 'Beží' : 'Zastavené');
  pill.dataset.testid = 'splitlinks-status';
  head.appendChild(pill);
  if (a.running) head.appendChild(el('span', 'runningdot', '⏳ práve prebieha nahrávanie…'));
  const btn = el('button', 'btn sm ' + (a.enabled ? 'warn' : 'good'),
    a.enabled ? '⏹ Stop' : '▶ Štart');
  btn.dataset.testid = 'splitlinks-toggle';
  btn.onclick = () => toggleAutomation('split_links', !a.enabled);
  head.appendChild(btn);
  const run = el('button', 'btn sm ghost', '⚡ Spustiť teraz');
  run.dataset.testid = 'splitlinks-run';
  run.disabled = !!a.running;
  run.onclick = () => runAutomation('split_links', 'split_links');
  head.appendChild(run);
  st.appendChild(head);
  if (a.description) st.appendChild(el('div', 'autodesc', escapeHtml(a.description)));

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
  if (a.last_run && a.last_status === 'ok' && lr.status) {
    const e = lr.variantlinks || {};
    const [label, cls] = _PAROVANIA_STATUS[lr.status] || [lr.status, 'ok'];
    const box = el('div', 'autoresult ' + cls);
    box.appendChild(el('div', 'autoresult-head', label));
    box.appendChild(el('div', '',
      `🔗 Veľkostné linky: +${e.count ?? 0} nových`
      + (e.blocked ? ` · ${e.blocked} zablokovaných (chýbajú kódy)` : '')
      + ` · spolu ${e.total_uploaded ?? 0} / ${e.total_codes ?? 0} nahraných`
      + ` · chýba ${e.remaining ?? 0}`));
    // #156: on a chunk failure, show WHICH chunk failed + how many rows made it (the
    // successful chunks ARE saved → the next run only retries the rest)
    if (e.error) box.appendChild(el('div', 'sub2 err', '❌ ' + escapeHtml(e.error)));
    st.appendChild(box);
  } else if (!a.last_run) {
    st.appendChild(el('div', 'muted',
      'Zatiaľ neprebehol žiadny beh — spusti automatizáciu (▶ Štart) alebo klikni ⚡ Spustiť teraz.'));
  }
  wrap.appendChild(st);
}

// ---- Automatizácie (#106): tab „Dodávateľský sklad" ----------------------- //
// Per-item table tab (like posta): status controls + filters + a table of every
// supplier link's availability / price / source / last-checked / error.
const _EXTRACTED_LABEL = {
  jsonld: 'JSON-LD', meta: 'meta', text: 'text', llm: 'AI (LLM)',
  'static-only': 'staticky', error: 'chyba',
};

function _availChip(av) {
  if (av === true) return '<span class="avail avail-yes">Skladom</span>';
  if (av === false) return '<span class="avail avail-no">Nie je skladom</span>';
  return '<span class="avail avail-unknown">Neznáme</span>';
}

function _stockRowsFiltered() {
  const rows = (SUPPLIER_STOCK && SUPPLIER_STOCK.rows) || [];
  if (STOCK_FILTER === 'all') return rows;
  if (STOCK_FILTER === 'errors') return rows.filter(r => !r.ok);
  if (STOCK_FILTER === 'llm') return rows.filter(r => r.extractedBy === 'llm');
  return rows.filter(r => (r.supplier || '') === STOCK_FILTER);   // a supplier name
}

function renderDodavatelskySklad() {
  const wrap = document.getElementById('tab-dodavatelsky_sklad');
  if (!wrap) return;
  wrap.innerHTML = '';
  const a = autoByKey('dodavatelsky_sklad');

  const st = el('div', 'autostatus');
  if (!a) {
    st.appendChild(el('div', 'muted', 'Automatizácia nie je dostupná (server nevrátil stav).'));
    wrap.appendChild(st);
    return;
  }
  const head = el('div', 'autohead');
  const pill = el('span', 'pill ' + (a.enabled ? 'on' : 'off'), a.enabled ? 'Beží' : 'Zastavené');
  pill.dataset.testid = 'sklad-status';
  head.appendChild(pill);
  if (a.running) head.appendChild(el('span', 'runningdot', '⏳ práve prebieha kontrola…'));
  const btn = el('button', 'btn sm ' + (a.enabled ? 'warn' : 'good'),
    a.enabled ? '⏹ Stop' : '▶ Štart');
  btn.dataset.testid = 'sklad-toggle';
  btn.onclick = () => toggleAutomation('dodavatelsky_sklad', !a.enabled);
  head.appendChild(btn);
  const run = el('button', 'btn sm ghost', '⚡ Spustiť teraz');
  run.dataset.testid = 'sklad-run';
  run.disabled = !!a.running;
  run.onclick = () => runAutomation('dodavatelsky_sklad', 'dodavatelsky_sklad');
  head.appendChild(run);
  st.appendChild(head);
  if (a.description) st.appendChild(el('div', 'autodesc', escapeHtml(a.description)));

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
      `Liniek: ${lr.total ?? 0} · skontrolovaných: ${lr.checked ?? 0}`
      + ` · preskočených (čerstvé): ${lr.skipped ?? 0}`
      + ` · skladom: ${lr.available ?? 0} · nie je: ${lr.unavailable ?? 0}`
      + ` · neznáme: ${lr.unknown ?? 0}`
      + ` · AI volaní: ${lr.llm_calls ?? 0}`
      + (lr.errors ? ` · chyby: ${lr.errors}` : '')));
  }
  wrap.appendChild(st);

  const s = SUPPLIER_STOCK || {};
  const rows = s.rows || [];
  if (!rows.length) {
    wrap.appendChild(el('div', 'empty2',
      s.last_check ? `Žiadne dáta (kontrola ${fmtDt(s.last_check)}).`
                   : 'Zatiaľ neprebehla žiadna kontrola — spusti automatizáciu (▶ Štart) alebo klikni ⚡ Spustiť teraz.'));
    return;
  }

  // filters: all / errors / llm + per-supplier dropdown
  const filt = el('div', 'stockfilters');
  filt.dataset.testid = 'sklad-filters';
  const mk = (key, lbl) => {
    const b = el('button', 'sf' + (STOCK_FILTER === key ? ' active' : ''), escapeHtml(lbl));
    b.onclick = () => { STOCK_FILTER = key; render(); };
    return b;
  };
  filt.appendChild(mk('all', `Všetky (${rows.length})`));
  filt.appendChild(mk('errors', `Len chyby (${rows.filter(r => !r.ok).length})`));
  filt.appendChild(mk('llm', `Len AI (${rows.filter(r => r.extractedBy === 'llm').length})`));
  const suppliers = [...new Set(rows.map(r => r.supplier).filter(Boolean))].sort();
  if (suppliers.length > 1) {
    const sel = el('select', 'sfsel');
    const optAll = el('option', '', 'Všetci dodávatelia'); optAll.value = '';
    sel.appendChild(optAll);
    for (const sup of suppliers) {
      const o = el('option', '', escapeHtml(sup)); o.value = sup;
      if (STOCK_FILTER === sup) o.selected = true;
      sel.appendChild(o);
    }
    sel.onchange = () => { STOCK_FILTER = sel.value || 'all'; render(); };
    filt.appendChild(sel);
  }
  wrap.appendChild(filt);

  const shown = _stockRowsFiltered();
  const tbl = el('table', 'posta-table');
  tbl.dataset.testid = 'sklad-table';
  tbl.innerHTML = '<thead><tr><th>Dodávateľ</th><th>Produkt</th><th>Dostupnosť</th>'
    + '<th>Cena</th><th>Zdroj</th><th>Kontrolované</th></tr></thead>';
  const tb = el('tbody');
  for (const r of shown) {
    const tr = el('tr', r.ok ? '' : 'callneeded');
    const price = (r.price != null)
      ? `${r.price} ${escapeHtml(r.currency || '')}`.trim() : '—';
    const src = r.ok ? (_EXTRACTED_LABEL[r.extractedBy] || escapeHtml(r.extractedBy || '—'))
                     : '❌ chyba';
    tr.innerHTML =
      `<td>${escapeHtml(r.supplier || '—')}</td>`
      + `<td><a href="${escapeHtml(r.link)}" target="_blank" rel="noopener">`
      + `${escapeHtml(r.name || r.link)}</a>`
      + (r.error ? `<div class="sub2 err">${escapeHtml(r.error)}</div>` : '') + '</td>'
      + `<td>${r.ok ? _availChip(r.available) : '<span class="avail avail-unknown">—</span>'}</td>`
      + `<td>${price}</td>`
      + `<td>${src}</td>`
      + `<td>${fmtDt(r.checkedAt)}</td>`;
    tb.appendChild(tr);
  }
  tbl.appendChild(tb);
  wrap.appendChild(tbl);
}

// ---- Automatizácie (#107): tab „Riziko výpadku" ---------------------------- //
// Per-item table tab (like posta / dodavatelsky_sklad): READ-ONLY join of OUR
// catalog (Skladom + viditeľné) against #106's already-scraped supplier stock —
// products we still show as available but our supplier has, in the meantime,
// sold out. No write action here (advisory only, per the digest).
function renderRizikoVypadku() {
  const wrap = document.getElementById('tab-riziko_vypadku');
  if (!wrap) return;
  wrap.innerHTML = '';
  const a = autoByKey('riziko_vypadku');
  if (!a) {
    wrap.appendChild(el('div', 'muted', 'Automatizácia nie je dostupná (server nevrátil stav).'));
    return;
  }
  const st = el('div', 'autostatus');
  const head = el('div', 'autohead');
  const pill = el('span', 'pill ' + (a.enabled ? 'on' : 'off'), a.enabled ? 'Beží' : 'Zastavené');
  pill.dataset.testid = 'riziko-status';
  head.appendChild(pill);
  if (a.running) head.appendChild(el('span', 'runningdot', '⏳ práve prebieha kontrola…'));
  const btn = el('button', 'btn sm ' + (a.enabled ? 'warn' : 'good'),
    a.enabled ? '⏹ Stop' : '▶ Štart');
  btn.dataset.testid = 'riziko-toggle';
  btn.onclick = () => toggleAutomation('riziko_vypadku', !a.enabled);
  head.appendChild(btn);
  const run = el('button', 'btn sm ghost', '⚡ Spustiť teraz');
  run.dataset.testid = 'riziko-run';
  run.disabled = !!a.running;
  run.onclick = () => runAutomation('riziko_vypadku', 'riziko_vypadku');
  head.appendChild(run);
  st.appendChild(head);
  if (a.description) st.appendChild(el('div', 'autodesc', escapeHtml(a.description)));

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
  wrap.appendChild(st);

  const r = RIZIKO || {};
  if (!r.has_supplier_data) {
    wrap.appendChild(el('div', 'empty2',
      'Žiadne dáta o dodávateľskom sklade — najprv spusti automatizáciu „Dodávateľský sklad".'));
    return;
  }
  const risks = r.risks || [];
  if (risks.length) {
    const dl = el('div', 'downloads');
    const da = el('a', '', '⬇ Stiahnuť CSV');
    da.href = '/api/riziko-vypadku/csv'; da.setAttribute('download', '');
    da.dataset.testid = 'riziko-csv';
    dl.appendChild(da);
    wrap.appendChild(dl);
  }
  if (!risks.length) {
    wrap.appendChild(el('div', 'empty2',
      r.last_check ? `Žiadne riziko výpadku (kontrola ${fmtDt(r.last_check)}).`
                   : 'Zatiaľ neprebehla žiadna kontrola — spusti automatizáciu (▶ Štart) alebo klikni ⚡ Spustiť teraz.'));
    return;
  }
  const tbl = el('table', 'posta-table');
  tbl.dataset.testid = 'riziko-table';
  tbl.innerHTML = '<thead><tr><th>Produkt</th><th>Dodávateľ</th><th>Naša cena / sklad</th>'
    + '<th>Dostupnosť u dodávateľa</th><th>Kontrolované</th></tr></thead>';
  const tb = el('tbody');
  for (const x of risks) {
    const tr = el('tr', 'callneeded');
    tr.innerHTML =
      `<td><code>${escapeHtml(x.code || '')}</code><div class="sub2">${escapeHtml(x.name || '')}</div></td>`
      + `<td>${escapeHtml(x.supplier || '—')}`
      + (x.link ? `<div class="sub2"><a href="${escapeHtml(x.link)}" target="_blank" rel="noopener">produkt u dodávateľa</a></div>` : '')
      + '</td>'
      + `<td>${escapeHtml(x.ourPrice || '—')} € · ${escapeHtml(x.ourStock || '0')} ks</td>`
      + `<td>${_availChip(false)}<div class="sub2">${escapeHtml(x.supplierAvailabilityText || '')}</div></td>`
      + `<td>${fmtDt(x.checkedAt)}</td>`;
    tb.appendChild(tr);
  }
  tbl.appendChild(tb);
  wrap.appendChild(tbl);
}

// ---- Automatizácie (#108): tab „Vypredané → Skladom" ----------------------- //
// Restock: JOIN of OUR catalog (Vypredané + viditeľné) against #106's already-
// scraped supplier stock — products the supplier has AGAIN, flipped back to
// Skladom via the careful Shoptet import. WRITES to the live eshop, so the
// automation starts Zastavené (#93 contract) — the manager clicks Štart.
function renderRestockSkladom() {
  const wrap = document.getElementById('tab-restock_skladom');
  if (!wrap) return;
  wrap.innerHTML = '';
  const a = autoByKey('restock_skladom');
  if (!a) {
    wrap.appendChild(el('div', 'muted', 'Automatizácia nie je dostupná (server nevrátil stav).'));
    return;
  }
  const st = el('div', 'autostatus');
  const head = el('div', 'autohead');
  const pill = el('span', 'pill ' + (a.enabled ? 'on' : 'off'), a.enabled ? 'Beží' : 'Zastavené');
  pill.dataset.testid = 'restock-status';
  head.appendChild(pill);
  if (a.running) head.appendChild(el('span', 'runningdot', '⏳ práve prebieha naskladnenie…'));
  const btn = el('button', 'btn sm ' + (a.enabled ? 'warn' : 'good'),
    a.enabled ? '⏹ Stop' : '▶ Štart');
  btn.dataset.testid = 'restock-toggle';
  btn.onclick = () => toggleAutomation('restock_skladom', !a.enabled);
  head.appendChild(btn);
  const run = el('button', 'btn sm ghost', '⚡ Spustiť teraz');
  run.dataset.testid = 'restock-run';
  run.disabled = !!a.running;
  run.onclick = () => runAutomation('restock_skladom', 'restock_skladom');
  head.appendChild(run);
  st.appendChild(head);
  if (a.description) st.appendChild(el('div', 'autodesc', escapeHtml(a.description)));

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
  wrap.appendChild(st);

  const r = RESTOCK || {};
  // import outcome of the last run (naskladnené = upravené v Shoptete)
  if (r.status === 'error') {
    st.appendChild(el('div', 'autoerr',
      '❌ Import zlyhal — nič sa nenaskladnilo. ' + escapeHtml(r.error_detail || '')));
  } else if (r.status === 'busy') {
    st.appendChild(el('div', 'muted', '⏳ Iný import práve bežal — beh sa preskočil, skús neskôr.'));
  } else if (r.last_check && (r.candidates || []).length) {
    st.appendChild(el('div', 'muted',
      `Naskladnených: ${r.updated ?? 0} · spracované: ${r.processed ?? 0}`
      + (r.failed ? ` · zlyhania: ${r.failed}` : '')));
  }

  if (!r.has_supplier_data) {
    wrap.appendChild(el('div', 'empty2',
      'Žiadne dáta o dodávateľskom sklade — najprv spusti automatizáciu „Dodávateľský sklad".'));
    return;
  }
  const cands = r.candidates || [];
  if (!cands.length) {
    wrap.appendChild(el('div', 'empty2',
      r.last_check ? `Žiadne produkty na naskladnenie (kontrola ${fmtDt(r.last_check)}).`
                   : 'Zatiaľ neprebehla žiadna kontrola — spusti automatizáciu (▶ Štart) alebo klikni ⚡ Spustiť teraz.'));
    return;
  }
  const tbl = el('table', 'posta-table');
  tbl.dataset.testid = 'restock-table';
  tbl.innerHTML = '<thead><tr><th>Produkt</th><th>Dodávateľ</th>'
    + '<th>Naša cena / cena dodávateľa</th><th>Dostupnosť u dodávateľa</th>'
    + '<th>Kontrolované</th></tr></thead>';
  const tb = el('tbody');
  for (const x of cands) {
    const tr = el('tr', '');
    const supPrice = x.supplierPrice ? `${escapeHtml(x.supplierPrice)} €` : '—';
    tr.innerHTML =
      `<td><code>${escapeHtml(x.code || '')}</code><div class="sub2">${escapeHtml(x.name || '')}</div></td>`
      + `<td>${escapeHtml(x.supplier || '—')}`
      + (x.link ? `<div class="sub2"><a href="${escapeHtml(x.link)}" target="_blank" rel="noopener">produkt u dodávateľa</a></div>` : '')
      + '</td>'
      + `<td>${escapeHtml(x.ourPrice || '—')} € · ${supPrice}</td>`
      + `<td>${_availChip(true)}<div class="sub2">${escapeHtml(x.supplierAvailabilityText || '')}</div></td>`
      + `<td>${fmtDt(x.checkedAt)}</td>`;
    tb.appendChild(tr);
  }
  tbl.appendChild(tb);
  wrap.appendChild(tbl);
}

// ---- Automatizácie (#98): tab „Máme skladom → Skladom" --------------------- //
// Produkty, ktoré fyzicky MÁME na sklade (Shoptet stock>0) ale zákazníkom sa
// stále zobrazujú ako Vypredané → automaticky prepne na Skladom. Trigger je náš
// vlastný Shoptet sklad (nie dodávateľ ako #108). PÍŠE do eshopu → štartuje
// Zastavené (#93). Vedome ukončené (detailOnly) produkty sa nikdy nedotýka.
function renderStockSkladom() {
  const wrap = document.getElementById('tab-stock_skladom');
  if (!wrap) return;
  wrap.innerHTML = '';
  const a = autoByKey('stock_skladom');
  if (!a) {
    wrap.appendChild(el('div', 'muted', 'Automatizácia nie je dostupná (server nevrátil stav).'));
    return;
  }
  const st = el('div', 'autostatus');
  const head = el('div', 'autohead');
  const pill = el('span', 'pill ' + (a.enabled ? 'on' : 'off'), a.enabled ? 'Beží' : 'Zastavené');
  pill.dataset.testid = 'stock-skladom-status';
  head.appendChild(pill);
  if (a.running) head.appendChild(el('span', 'runningdot', '⏳ práve prebieha naskladnenie…'));
  const btn = el('button', 'btn sm ' + (a.enabled ? 'warn' : 'good'),
    a.enabled ? '⏹ Stop' : '▶ Štart');
  btn.dataset.testid = 'stock-skladom-toggle';
  btn.onclick = () => toggleAutomation('stock_skladom', !a.enabled);
  head.appendChild(btn);
  const run = el('button', 'btn sm ghost', '⚡ Spustiť teraz');
  run.dataset.testid = 'stock-skladom-run';
  run.disabled = !!a.running;
  run.onclick = () => runAutomation('stock_skladom', 'stock_skladom');
  head.appendChild(run);
  st.appendChild(head);
  if (a.description) st.appendChild(el('div', 'autodesc', escapeHtml(a.description)));

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
  wrap.appendChild(st);

  const r = STOCK_SKLADOM || {};
  if (r.status === 'error') {
    st.appendChild(el('div', 'autoerr',
      '❌ Import zlyhal — nič sa neprepolo. ' + escapeHtml(r.error_detail || '')));
  } else if (r.status === 'busy') {
    st.appendChild(el('div', 'muted', '⏳ Iný import práve bežal — beh sa preskočil, skús neskôr.'));
  } else if (r.last_check && (r.candidates || []).length) {
    st.appendChild(el('div', 'muted',
      `Prepnutých na Skladom: ${r.updated ?? 0} · spracované: ${r.processed ?? 0}`
      + (r.failed ? ` · zlyhania: ${r.failed}` : '')));
  }

  const cands = r.candidates || [];
  if (!cands.length) {
    wrap.appendChild(el('div', 'empty2',
      r.last_check ? `Žiadne produkty na prepnutie (kontrola ${fmtDt(r.last_check)}).`
                   : 'Zatiaľ neprebehla žiadna kontrola — spusti automatizáciu (▶ Štart) alebo klikni ⚡ Spustiť teraz.'));
    return;
  }
  const tbl = el('table', 'posta-table');
  tbl.dataset.testid = 'stock-skladom-table';
  tbl.innerHTML = '<thead><tr><th>Produkt</th><th>Náš sklad</th>'
    + '<th>Naša cena</th><th>Teraz zobrazuje</th></tr></thead>';
  const tb = el('tbody');
  for (const x of cands) {
    const tr = el('tr', '');
    tr.innerHTML =
      `<td><code>${escapeHtml(x.code || '')}</code><div class="sub2">${escapeHtml(x.name || '')}</div></td>`
      + `<td>${escapeHtml(x.stock || '—')} ks</td>`
      + `<td>${escapeHtml(x.ourPrice || '—')} €</td>`
      + `<td>${_availChip(false)}<div class="sub2">${escapeHtml(x.availabilityText || '')}</div></td>`;
    tb.appendChild(tr);
  }
  tbl.appendChild(tb);
  wrap.appendChild(tbl);
}

// ---- Automatizácie (#105): tab „Pripomienky objednávok" -------------------- //
// „Vybavuje sa" objednávky >4 dni: BEZ poznámky → červený „nikto sa jej nedotkol"
// alert (žiaden mail); S poznámkou → AI klasifikuje, či bol zákazník kontaktovaný —
// ak nie, pošle jednu pripomienku (max raz/obj) a ukáže oranžovo. POSIELA reálne
// zákaznícke e-maily + stojí OpenAI → automatizácia štartuje Zastavené (#93).
function renderOrdersReminder() {
  const wrap = document.getElementById('tab-orders_reminder');
  if (!wrap) return;
  wrap.innerHTML = '';
  const a = autoByKey('orders_reminder');
  const st = el('div', 'autostatus');
  if (!a) {
    st.appendChild(el('div', 'muted', 'Automatizácia nie je dostupná (server nevrátil stav).'));
    wrap.appendChild(st);
    return;
  }
  const head = el('div', 'autohead');
  const pill = el('span', 'pill ' + (a.enabled ? 'on' : 'off'), a.enabled ? 'Beží' : 'Zastavené');
  pill.dataset.testid = 'ordrem-status';
  head.appendChild(pill);
  if (a.running) head.appendChild(el('span', 'runningdot', '⏳ práve prebieha kontrola…'));
  const btn = el('button', 'btn sm ' + (a.enabled ? 'warn' : 'good'),
    a.enabled ? '⏹ Stop' : '▶ Štart');
  btn.dataset.testid = 'ordrem-toggle';
  btn.onclick = () => toggleAutomation('orders_reminder', !a.enabled);
  head.appendChild(btn);
  const run = el('button', 'btn sm ghost', '⚡ Spustiť teraz');
  run.dataset.testid = 'ordrem-run';
  run.disabled = !!a.running;
  run.onclick = () => runAutomation('orders_reminder', 'orders_reminder');
  head.appendChild(run);
  st.appendChild(head);
  if (a.description) st.appendChild(el('div', 'autodesc', escapeHtml(a.description)));

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
      `Objednávky >4 dni: ${lr.orders_4d ?? 0} · bez poznámky: ${lr.no_note ?? 0}`
      + ` · odoslané pripomienky teraz: ${lr.emailed_now ?? 0}`
      + (lr.emailed_total ? ` · spolu pripomenutých: ${lr.emailed_total}` : '')
      + (lr.ai_unavailable ? ` · AI nedostupné: ${lr.ai_unavailable}` : '')
      + (lr.errors ? ` · chyby: ${lr.errors}` : '')));
  }
  wrap.appendChild(st);

  const d = ORDERS_REMINDER || {};
  const red = d.red || [];
  const orange = d.orange || [];
  const skipped = d.skipped || [];
  if (!red.length && !orange.length && !skipped.length) {
    wrap.appendChild(el('div', 'empty2',
      d.last_check ? `Žiadne objednávky na pripomenutie (kontrola ${fmtDt(d.last_check)}).`
                   : 'Zatiaľ neprebehla žiadna kontrola — spusti automatizáciu (▶ Štart) alebo klikni ⚡ Spustiť teraz.'));
    return;
  }

  // manual per-row override (#153) — "send" (▶ pripomienka teraz) works on red AND skipped rows
  // (overriding a wrong AI 'už kontaktovaný' verdict); "contact" (✓ kontaktované) only on red
  // rows (no note ever ran through the AI, so it can't already be resolved).
  function _ordremAction(btn, code, action) {
    btn.onclick = () => overrideOrdersReminder(code, action);
  }

  // RED — >4d orders with NO internal note (nobody has touched them yet)
  if (red.length) {
    wrap.appendChild(el('div', 'warnhead', `🔴 ${red.length} bez internej poznámky — nikto sa jej ešte nedotkol`));
    const tbl = el('table', 'posta-table');
    tbl.dataset.testid = 'ordrem-red';
    tbl.innerHTML = '<thead><tr><th>Objednávka</th><th>Zákazník</th><th>Položka</th>'
      + '<th>Bez pohybu</th><th>Akcia</th></tr></thead>';
    const tb = el('tbody');
    for (const o of red) {
      const tr = el('tr', 'callneeded'); tr.dataset.code = o.code;
      tr.innerHTML =
        `<td><a href="${escapeHtml(o.admin_link)}" target="_blank" rel="noopener">${escapeHtml(o.code)}</a></td>`
        + `<td>${escapeHtml(o.billFullName || '')}`
        + `<div class="sub2">${escapeHtml(o.phone || '')} · ${escapeHtml(o.email || '')}</div></td>`
        + `<td>${escapeHtml(o.itemName || '')}</td>`
        + `<td>${o.days || 0} ${dniLabel(o.days || 0)}</td>`;
      const actTd = el('td', 'ordrem-actions');
      const sendBtn = el('button', 'btn sm ghost ordrem-act-send', '▶ Poslať pripomienku');
      _ordremAction(sendBtn, o.code, 'send');
      const contactBtn = el('button', 'btn sm ghost ordrem-act-contact', '✓ Kontaktované');
      _ordremAction(contactBtn, o.code, 'contact');
      actTd.appendChild(sendBtn); actTd.appendChild(contactBtn);
      tr.appendChild(actTd);
      tb.appendChild(tr);
    }
    tbl.appendChild(tb);
    wrap.appendChild(tbl);
  }

  // ORANGE — reminder e-mail sent to the customer (terminal — no override action)
  if (orange.length) {
    wrap.appendChild(el('div', 'warnhead', `🟠 ${orange.length} — pripomienka odoslaná zákazníkovi`));
    const tbl = el('table', 'posta-table');
    tbl.dataset.testid = 'ordrem-orange';
    tbl.innerHTML = '<thead><tr><th>Objednávka</th><th>Zákazník</th><th>Položka</th>'
      + '<th>Interná poznámka</th><th>Odoslané</th></tr></thead>';
    const tb = el('tbody');
    for (const o of orange) {
      const tr = el('tr', ''); tr.dataset.code = o.code;
      tr.innerHTML =
        `<td><a href="${escapeHtml(o.admin_link)}" target="_blank" rel="noopener">${escapeHtml(o.code)}</a></td>`
        + `<td>${escapeHtml(o.billFullName || '')}<div class="sub2">${escapeHtml(o.email || '')}</div></td>`
        + `<td>${escapeHtml(o.itemName || '')}<div class="sub2">${o.days || 0} ${dniLabel(o.days || 0)} v stave</div></td>`
        + `<td class="sub2">${escapeHtml(o.shopRemark || '—')}</td>`
        + `<td>${fmtDt(o.sent_date)}</td>`;
      tb.appendChild(tr);
    }
    tbl.appendChild(tb);
    wrap.appendChild(tbl);
  }

  // SKIPPED — AI classified the note as 'already contacted', so no e-mail went out. Shown so the
  // manager can correct a wrong AI read (#153) — the only override here is 'send anyway'.
  if (skipped.length) {
    wrap.appendChild(el('div', 'warnhead',
      `⚪ ${skipped.length} — AI usúdilo, že zákazník je už kontaktovaný`));
    const tbl = el('table', 'posta-table');
    tbl.dataset.testid = 'ordrem-skipped';
    tbl.innerHTML = '<thead><tr><th>Objednávka</th><th>Zákazník</th><th>Položka</th>'
      + '<th>Interná poznámka</th><th>Akcia</th></tr></thead>';
    const tb = el('tbody');
    for (const o of skipped) {
      const tr = el('tr', ''); tr.dataset.code = o.code;
      tr.innerHTML =
        `<td><a href="${escapeHtml(o.admin_link)}" target="_blank" rel="noopener">${escapeHtml(o.code)}</a></td>`
        + `<td>${escapeHtml(o.billFullName || '')}<div class="sub2">${escapeHtml(o.email || '')}</div></td>`
        + `<td>${escapeHtml(o.itemName || '')}<div class="sub2">${o.days || 0} ${dniLabel(o.days || 0)} v stave</div></td>`
        + `<td class="sub2">${escapeHtml(o.shopRemark || '—')}</td>`;
      const actTd = el('td', 'ordrem-actions');
      const sendBtn = el('button', 'btn sm ghost ordrem-act-send', '▶ Poslať pripomienku');
      _ordremAction(sendBtn, o.code, 'send');
      actTd.appendChild(sendBtn);
      tr.appendChild(actTd);
      tb.appendChild(tr);
    }
    tbl.appendChild(tb);
    wrap.appendChild(tbl);
  }
}

// manual per-row override (#153) — POST + reload the tab data + re-render.
async function overrideOrdersReminder(code, action) {
  const r = await fetch('/api/orders-reminder/override', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code, action })
  });
  if (!r.ok) {
    let msg = '';
    try { msg = (await r.json()).error || ''; } catch (_) { /* non-JSON error */ }
    alert('Nepodarilo sa: ' + (msg || ('chyba ' + r.status)));
  }
  await loadOrdersReminder();
  render();
}

function render() {
  renderTabs();
  setPageHead();
  const toorder = ACTIVE_TAB === 'toorder';
  const nedostupne = ACTIVE_TAB === 'nedostupne';
  const vystavy = ACTIVE_TAB === 'vystavy';
  const search = ACTIVE_TAB === 'search';
  const notes = ACTIVE_TAB === 'notes';
  const users = ACTIVE_TAB === 'users';
  const posta = ACTIVE_TAB === 'posta';
  const shoptetSync = ACTIVE_TAB === 'shoptet_sync';
  const parovaniaEshop = ACTIVE_TAB === 'parovania_eshop';
  const grubeExternalcode = ACTIVE_TAB === 'grube_externalcode';
  const splitLinks = ACTIVE_TAB === 'split_links';
  const dodavatelskySklad = ACTIVE_TAB === 'dodavatelsky_sklad';
  const rizikoVypadku = ACTIVE_TAB === 'riziko_vypadku';
  const restockSkladom = ACTIVE_TAB === 'restock_skladom';
  const stockSkladom = ACTIVE_TAB === 'stock_skladom';
  const ordersReminder = ACTIVE_TAB === 'orders_reminder';
  const imageHealth = ACTIVE_TAB === 'image_health';
  const dev = ACTIVE_TAB === 'dev';
  const auto = posta || shoptetSync || parovaniaEshop || grubeExternalcode || splitLinks || dodavatelskySklad || rizikoVypadku || restockSkladom || stockSkladom || ordersReminder || imageHealth;  // any automation tab
  const plain = nedostupne || vystavy || search || notes || users || auto || dev;   // non-review/non-toorder full-width tabs
  document.body.classList.toggle('toorder-wide', toorder);   // od kraja po kraj len na tabe „Na objednanie"
  const prog = document.querySelector('.progress'); if (prog) prog.style.display = (toorder || plain) ? 'none' : '';
  const dls = document.querySelector('.downloads'); if (dls) dls.style.display = (toorder || plain) ? 'none' : '';
  const filt = document.getElementById('filters'); if (filt) filt.style.display = plain ? 'none' : '';
  const secNd = document.getElementById('tab-nedostupne'); if (secNd) secNd.hidden = !nedostupne;
  const secVy = document.getElementById('tab-vystavy'); if (secVy) secVy.hidden = !vystavy;
  const sec = document.getElementById('tab-search'); if (sec) sec.hidden = !search;
  const secNotes = document.getElementById('tab-notes'); if (secNotes) secNotes.hidden = !notes;
  const secUsers = document.getElementById('tab-users'); if (secUsers) secUsers.hidden = !users;
  const secPosta = document.getElementById('tab-posta'); if (secPosta) secPosta.hidden = !posta;
  const secShoptetSync = document.getElementById('tab-shoptet_sync'); if (secShoptetSync) secShoptetSync.hidden = !shoptetSync;
  const secParovania = document.getElementById('tab-parovania_eshop'); if (secParovania) secParovania.hidden = !parovaniaEshop;
  const secGrubeExt = document.getElementById('tab-grube_externalcode'); if (secGrubeExt) secGrubeExt.hidden = !grubeExternalcode;
  const secSplitLinks = document.getElementById('tab-split_links'); if (secSplitLinks) secSplitLinks.hidden = !splitLinks;
  const secSklad = document.getElementById('tab-dodavatelsky_sklad'); if (secSklad) secSklad.hidden = !dodavatelskySklad;
  const secRiziko = document.getElementById('tab-riziko_vypadku'); if (secRiziko) secRiziko.hidden = !rizikoVypadku;
  const secRestock = document.getElementById('tab-restock_skladom'); if (secRestock) secRestock.hidden = !restockSkladom;
  const secStock = document.getElementById('tab-stock_skladom'); if (secStock) secStock.hidden = !stockSkladom;
  const secOrdRem = document.getElementById('tab-orders_reminder'); if (secOrdRem) secOrdRem.hidden = !ordersReminder;
  const secImgHealth = document.getElementById('tab-image_health'); if (secImgHealth) secImgHealth.hidden = !imageHealth;
  const secDev = document.getElementById('tab-dev'); if (secDev) secDev.hidden = !dev;
  const mainEl = document.getElementById('list'); if (mainEl) mainEl.style.display = plain ? 'none' : '';
  if (nedostupne) { document.getElementById('empty').hidden = true; renderNedostupne(); return; }
  if (vystavy) { document.getElementById('empty').hidden = true; renderVystavy(); return; }
  if (dev) { document.getElementById('empty').hidden = true; renderDev(); return; }
  if (imageHealth) { document.getElementById('empty').hidden = true; renderImageHealth(); return; }
  if (ordersReminder) { document.getElementById('empty').hidden = true; renderOrdersReminder(); return; }
  if (restockSkladom) { document.getElementById('empty').hidden = true; renderRestockSkladom(); return; }
  if (stockSkladom) { document.getElementById('empty').hidden = true; renderStockSkladom(); return; }
  if (rizikoVypadku) { document.getElementById('empty').hidden = true; renderRizikoVypadku(); return; }
  if (dodavatelskySklad) { document.getElementById('empty').hidden = true; renderDodavatelskySklad(); return; }
  if (parovaniaEshop) { document.getElementById('empty').hidden = true; renderParovaniaEshop(); return; }
  if (grubeExternalcode) { document.getElementById('empty').hidden = true; renderGrubeExternalcode(); return; }
  if (splitLinks) { document.getElementById('empty').hidden = true; renderSplitLinks(); return; }
  if (shoptetSync) { document.getElementById('empty').hidden = true; renderShoptetSync(); return; }
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
  initFolders();
  initIdea();
  initNdModal();
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
  if (ACTIVE_TAB === 'users' && !(ME && ME.is_admin)) ACTIVE_TAB = 'toorder';
  initEditLabels();   // #176 — admin edit-mode toggle (needs ME/isAdmin())
  loadVersion();
  const j = await (await fetch('/api/products')).json();
  PRODUCTS = j.products;
  DECISIONS = j.decisions || {};
  VARIANT_LINKS = j.variant_links || {};   // #174 per-variant split links
  PRODUCTS.sort((a, b) =>
    ((a.ai_status === 'unmatched') ? 1 : 0) - ((b.ai_status === 'unmatched') ? 1 : 0) || a.idx - b.idx);
  FILTER = localStorage.getItem('filter') || 'unreviewed';
  ORDER_SUPPLIER = localStorage.getItem('orderSupplier') || 'all';
  // ?tab=toorder — Discord posts a link straight to the to-order list
  const qTab = new URLSearchParams(location.search).get('tab');
  if (qTab === 'toorder' || qTab === 'review' || qTab === 'search' || qTab === 'notes'
      || qTab === 'posta' || qTab === 'shoptet_sync' || qTab === 'parovania_eshop'
      || qTab === 'dev') {
    ACTIVE_TAB = qTab; localStorage.setItem('tab', qTab);
  }
  // Prefetch nav badge counts for ALL tabs on first paint (#112) — not just the
  // active one; the 'Na objednanie'/'Poznámky' counts used to stay empty until
  // that tab was first opened. loadOrders()/loadNotes() already swallow fetch
  // failures internally (fall back to empty arrays), so a network hiccup here
  // can't crash init() or spam the console. loadAutomations() (#153) is prefetched
  // the SAME way — the ⚠ failed-run nav badge must show from ANY page, not only
  // after the manager happens to open that specific automation's tab.
  // loadUiLabels() (#173) is prefetched too — renderTabs()'s first paint must
  // already show admin-set custom names, not the default flashing first.
  await Promise.all([loadOrders(), loadNotes(), loadAutomations(), loadUiLabels()]);
  if (ACTIVE_TAB === 'users') await loadUsers();
  if (ACTIVE_TAB === 'posta') await loadPosta();
  if (ACTIVE_TAB === 'dev') await loadDevIssues();
  initSearch();
  render();
  const y = parseInt(localStorage.getItem('scrollY') || '0', 10);
  if (y) window.scrollTo(0, y);
}
init();
