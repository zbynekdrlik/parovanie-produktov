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
let NEDOSTUPNE_BAD_CFG = false;   // …built on the DEFAULT statuses because the config broke
let ND_PENDING = null;      // {code, type} — the send the preview modal is showing
let VYSTAVY = null;         // /api/vystavy — poľovnícke výstavy (#111)
let VY_OPEN = new Set();    // ids of výstavy whose detail/edit panel is expanded (transient)
let VY_ADD_OPEN = false;    // the „+ Pridať výstavu" form is showing (transient)
let NOTES = [];             // [{id, text, done, ts}] — 'Poznámky' tab
let AUTOMATIONS = [];       // /api/automations — in-app runner statuses (#93)
// 'running' | 'blocked' (iná inštancia drží plánovač) | 'off' (WEBREVIEW_NO_SCHEDULER).
// Bez tohto vyzerá zablokovaná inštancia úplne zdravo: „Ďalší beh" sa číta z uloženého
// stavu, takže tab ukazuje budúci čas aj keď sa nikdy nič nespustí (review PR #265).
// …a 'corrupt' = /api/automations sa nepodarilo prečítať (server fail-closed 503 nad
// poškodeným automations.json, alebo request zlyhal). Bez toho sa 503 s návodom na
// opravu vykreslil ako čistý prvý štart (revízia PR #265, I3).
let SCHEDULER = 'running';
let SCHED_ERROR = '';       // serverová hláška k stavu 'corrupt' (čo presne opraviť)
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
let ORDER_STATUSES = null;  // /api/order-statuses — which Shoptet status means what (#209)
let ORDER_SUPPLIER = 'all';
// #205 — hide the lines the manager has already dealt with (any flag on them). Purely a
// VIEW filter: nothing is written, the chips keep counting every line, and it survives a
// reload (localStorage), because working a long supplier list down is a multi-visit job.
let HIDE_HANDLED = false;
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
// Celé dni od dátumu objednávky (YYYY-MM-DD) po dnes; prázdny/nevalidný → 0 (nikdy „staré").
// `now` je injektovateľné pre deterministický test. Nezáporné (budúci dátum → 0).
function orderAgeDays(orderDate, now) {
  const p = (orderDate || '').split('-');
  if (p.length !== 3) return 0;
  const t = Date.parse(orderDate + 'T00:00:00');
  if (isNaN(t)) return 0;
  return Math.max(0, Math.floor(((now || Date.now()) - t) / 86400000));
}
const STALE_ORDER_DAYS = 14;   // nevybavená objednávka staršia než toľko dní → ⚠️ upozornenie
function escapeHtml(s) { return (s || '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
// `#empty` is SHARED by the review tab and „Na objednanie", so a tab-specific wording
// („Všetko vybavené") must never stick to the other one. Every caller states what IT
// wants and `null` restores the neutral default — captured from the template on first
// use, so the two never drift apart.
let _EMPTY_DEFAULT = null;
function setEmptyText(text) {
  const e = document.getElementById('empty');
  if (!e) return;
  if (_EMPTY_DEFAULT === null) _EMPTY_DEFAULT = e.textContent;
  e.textContent = text == null ? _EMPTY_DEFAULT : text;
}
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
  card.dataset.key = p.key;      // so another tab can scroll straight to this product

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

// 'System' — foundational automations in their own top nav folder (#systemTabs).
// 'Sync zo Shoptetu' lives here: it fetches the fresh orders + catalog that
// everything else reads, so it is the base of the system, not an eshop feature.
const SYSTEM_TABS = [['shoptet_sync', 'Sync zo Shoptetu']];

// In-app automations (#93) — each gets its own nav item in the 'Automatizácie'
// sidebar folder (#autoTabs) + its own tab section. New automations: add here.
const AUTOMATION_TABS = [['posta', 'Nevyzdvihnuté zásielky'], ['orders_reminder', 'Pripomienky objednávok'],
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
  // #274 — these two were MISSING, so `${NAV_ICONS[key]}` interpolated the string
  // "undefined" into the <svg> and the browser painted it as text next to the
  // label („undefinedGRUBE kódy → eshop"). Every nav key needs an entry — pinned
  // by test_every_nav_key_has_an_icon.
  grube_externalcode: '<rect x="3" y="6" width="18" height="12" rx="2"/>'
    + '<path d="M7 9v6M11 9v6M15 9v6M18 9v6"/>',
  split_links: '<path d="M7 4v6a4 4 0 004 4h6"/><path d="M7 14v7"/>'
    + '<path d="M17 11l3 3-3 3"/>',
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
// The sidebar tab key is not always the automation key: the Pošta tab is the legacy 'posta'
// while its Automation.key is 'posta_uncollected' (same pairing app.py notes at AUTOMATION_TABS).
// `autoByKey('posta')` therefore matched NOTHING, so the ⚠ badge could never light for that
// automation — not for a degraded run, and not for the failed run #153 built it for either.
const NAV_AUTOMATION_KEY = { posta: 'posta_uncollected' };

// #282 — a DEGRADED run counts as a failure here too. The Pošta automation's source dried up on
// 2.7. and every run afterwards ended `ok`, so this badge stayed dark and the tab kept reporting
// „0 nevyzdvihnutých" while a real parcel ran out its pickup deadline. A run that cannot see its
// own input has failed, whether or not it threw.
function navError(key) {
  const a = autoByKey(NAV_AUTOMATION_KEY[key] || key);
  return !!(a && (a.last_status === 'error' || (a.last_result || {}).source_degraded));
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
  // `|| ''` — a key with no icon must render an EMPTY svg, never the literal
  // "undefined" as text beside the label (#274); the drift test above keeps the
  // table complete, this keeps the fallback harmless if one ever slips through.
  bt.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${NAV_ICONS[key] || ''}</svg>`
    + `<span class="tlabel">${escapeHtml(lbl)}</span>`
    + (err ? '<span class="navwarn" title="posledný beh zlyhal alebo je degradovaný">⚠</span>' : '')
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
  const st = document.getElementById('systemTabs');
  if (st) {
    st.innerHTML = '';
    for (const [key, lbl] of SYSTEM_TABS) st.appendChild(_navButton(key, lbl));
  }
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
    s.textContent = `${openItemsPhrase(ORDERS.length)} u dodávateľov`;
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
  initFolder('folder-system', 'folder:system');
  initFolder('folder-eshop', 'folder:eshop');
  initFolder('folder-automations', 'folder:automations');
}

async function switchTab(tab) {
  ACTIVE_TAB = tab; localStorage.setItem('tab', tab); window.scrollTo(0, 0);
  if (tab === 'toorder' && !ORDERS.length) await loadOrders();
  if (tab === 'nedostupne') await loadNedostupne();   // always fresh — orders/state change
  if (tab === 'vystavy') await loadVystavy();   // always fresh — state advances via automations
  if (tab === 'notes' && !NOTES.length) await loadNotes();
  if (tab === 'users') await loadUsers();   // always fresh — small list
  if (tab === 'posta') await loadPosta();   // always fresh — status can change
  if (tab === 'shoptet_sync') await loadShoptetSync();   // always fresh — status can change
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

// The flag maps are replaced by the server's own state, so every in-flight write's
// bookkeeping becomes void — dropping it stops a late failure from rolling FRESH data
// back to a baseline that predates the reload (the `live` check in saveOrderFlag).
// Fetch first, then drop the bookkeeping and install the new maps in ONE synchronous
// block: clearing `_flagWrites` five awaits BEFORE the maps were swapped left a window in
// which a click seeded its baseline from a still-optimistic value AND bound its write to
// a map object about to be thrown away — a rollback into thin air.
async function loadOrders() {
  const _wipe = () => { for (const k of Object.keys(_flagWrites)) delete _flagWrites[k]; };
  // A reload re-reads every flag from the server, so nothing on screen is an unsaved
  // intent any more — there is no lost work left for the banner (#234) to be about.
  clearToOrderFails();
  try {
    const [orders, ordered, waiting, instock, unavail, comments] = await Promise.all(
      ['/api/orders', '/api/ordered', '/api/waiting', '/api/instock', '/api/unavailable',
       '/api/order-comment'].map(u => fetch(u).then(r => r.json())));
    _wipe();
    ORDERS = orders.orders || [];
    ORDERED = ordered.ordered || {};
    WAITING = waiting.waiting || {};
    INSTOCK = instock.instock || {};
    UNAVAIL = unavail.unavailable || {};
    ORDER_COMMENTS = comments.comments || {};
  } catch (_) {
    _wipe();
    ORDERS = []; ORDERED = {}; WAITING = {}; INSTOCK = {}; UNAVAIL = {}; ORDER_COMMENTS = {};
  }
}

// #214 — every write on the „Na objednanie" tab reports its own failure. A silent
// `if (!r.ok) return;` left the manager guessing whether his click landed (and, for the
// optimistic flag toggles, showing a flag the server never stored). One message shape
// for all of them, in the manager's language.
// #234 — every failure the manager has not dealt with yet, oldest first. THE LIST ITSELF
// is the dedup: an identical failure already standing in the banner is not added twice.
//
// It used to be a 5 s TIME window, which was right while the report was a modal (he had
// just seen the alert). With a persistent banner the timer is wrong in BOTH directions:
// a retry after the window appended a SECOND line for one row (and the headline counts
// rows to redo, so it lied), and a retry inside the window after the banner had been
// cleared was swallowed with nothing on screen at all — the silent lost write #214 exists
// to remove. Deduping against what is VISIBLE is right in both.
//
// `where` (which row/order/product) is part of the identity on purpose: working DOWN a
// supplier group is what this tab is for, so 3-5 rows failing within a couple of seconds
// is the normal shape of a partial outage, not a repeat of one event. `value` is in the
// identity but never shown — without it a CORRECTION is swallowed: he fixes a rejected
// pair URL and re-saves (same what / where / 'chyba 500') and gets no feedback.
let _toOrderFails = [];

const _failIdent = (f) => [f.what, f.where || '', f.detail || '',
                           String(f.value == null ? '' : f.value)].join('\u0000');

// The banner lives in the TOP BAR (`#toFails` in index.html), never inside `#list`. Every
// rollback repaints the list BEFORE it reports, so a banner in there would be wiped by the
// NEXT failure and he would finish an outage holding only the last row's name. It is also
// why nothing here goes through captureOpenEditors/restoreOpenEditors (#208/#233).
function renderToOrderFails() {
  const box = document.getElementById('toFails');
  if (!box) return;
  box.innerHTML = '';
  if (!_toOrderFails.length) { box.hidden = true; return; }
  const head = el('div', 'tofail-head');
  // The count first: during an outage he wants to know HOW MANY rows he has to redo
  // without counting lines. ACCUSATIVE after „uložiť" (`itemsWord(n, true)`) — n=1 is the
  // banner's most common state and „uložiť 1 položka" is simply wrong Slovak.
  // No blanket „skús znova": some of these are DETERMINISTIC refusals (a URL without a
  // scheme, a product missing from the review) where an unchanged retry can never work —
  // the reason on each line says what to do.
  head.textContent = '⚠️ Nepodarilo sa uložiť ' + _toOrderFails.length + ' '
    + itemsWord(_toOrderFails.length, true) + ':';
  const x = el('button', 'tofail-close', '×');
  x.title = 'Zavrieť';
  x.onclick = () => { _toOrderFails = []; renderToOrderFails(); };
  head.appendChild(x);
  box.appendChild(head);
  for (const f of _toOrderFails) {
    // free text (a supplier name in `where`, the server's own reason in `detail`) —
    // textContent, never innerHTML
    const line = el('div', 'tofail');
    line.textContent = f.what + (f.where ? ' — ' + f.where : '')
      + (f.detail ? ' (' + f.detail + ')' : '');
    box.appendChild(line);
  }
  // The top bar is SHARED with the review tab, so an answer that settles AFTER he has left
  // must not raise this over „Kontrola párovania" (where „Nepodarilo sa uložiť…" reads as
  // failed review saves). The list survives; `render()` shows it when he comes back.
  box.hidden = ACTIVE_TAB !== 'toorder';
}

// Nothing is left to redo at all — a reload (the server's own state replaced everything)
// or his „×". A stale warning over a tab that is saving fine again is one he learns to
// ignore, and then the next real one goes unread.
function clearToOrderFails() {
  if (!_toOrderFails.length) return;
  _toOrderFails = [];
  renderToOrderFails();
}

// ONE write landed — drop only ITS line. Wiping the whole banner on the first write that
// happens to succeed takes away the names of the rows he has NOT redone yet, at exactly
// the moment he starts working through them, which defeats accumulating them at all.
function clearToOrderFail(what, where) {
  const before = _toOrderFails.length;
  _toOrderFails = _toOrderFails.filter(f => !(f.what === what && f.where === (where || '')));
  if (_toOrderFails.length !== before) renderToOrderFails();
}

function toOrderSaveFailed(what, detail, where, value) {
  const entry = { what, where: where || '', detail: detail || '', value, at: Date.now() };
  // Already standing in the banner → he can see it, and a second identical line would
  // only inflate the „how many rows to redo" count.
  if (_toOrderFails.some(f => _failIdent(f) === _failIdent(entry))) return;
  _toOrderFails.push(entry);
  renderToOrderFails();
}

// „obj. 20260910, kód C1" — the per-line key (orderCode|itemCode) in the manager's terms.
// Empty parts are dropped, so a row missing one never reads as a dangling „kód .".
const toOrderPartLabel = (kind, v) => (v ? kind + ' ' + v : '');
function toOrderRowLabel(key) {
  const [order, code] = String(key || '').split('|');
  return [toOrderPartLabel('obj.', order), toOrderPartLabel('kód', code)]
    .filter(Boolean).join(', ');
}

// Run one to-order write. Returns '' on success, else a short human reason. Reporting is
// left to the caller ON PURPOSE: a caller that changed the UI optimistically must roll
// back FIRST, so the tab is already telling the truth when the message pops up. Clearing
// the report is the caller's job too, and per WRITE (`clearToOrderFail`) — a blanket clear
// here would drop the rows he has not redone yet on the first success.
// `out` (optional) receives the parsed success body as `out.json` — the flag writes mirror
// the server's authoritative `flags` from it.
async function postToOrder(path, payload, out) {
  try {
    const r = await fetch(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (r.ok) {
      if (out) out.json = await r.json().catch(() => null);
      return '';
    }
    // surface the endpoint's own reason — 'comment too long' / 'invalid supplier' /
    // 'unauthorized' are all deterministic, so a bare status code would have the manager
    // retrying a write that can never succeed
    const j = await r.json().catch(() => null);
    return 'chyba ' + r.status + (j && j.error ? ': ' + j.error : '');
  } catch (_) {
    return 'server neodpovedal';
  }
}

// The four per-line flags (objednané / čaká sa / skladom / nedostupné) share one writer:
// the flag map is set optimistically (the row + the supplier chips repaint synchronously
// in the click handler), and on ANY failure it is rolled back and the tab re-rendered, so
// what the manager sees is never a flag the server refused (#214).
// One entry per (flag, row) that has a write in flight: { seq, confirmed }. `confirmed`
// is the value the SERVER is known to hold — NOT a per-call snapshot of the map. A plain
// double-click fires two writes for one row, and the second one's snapshot would be the
// OPTIMISTIC value the first had just written; rolling back to that restores a flag the
// server explicitly refused (the manager is told the save failed and then looks at a row
// claiming it succeeded). Keyed per FLAG too — 'čaká sa' and 'skladom' are independent
// writes on the same row and must not supersede each other.
// (#291: which answer is NEWEST is decided by the server's own commit number, not by the
// order this client issued the writes in — see `confirmedCommit` below.)
const _flagWrites = Object.create(null);

// The entry is NEVER deleted (bounded by rows x 4 flags): `confirmed` is this row+flag's
// last known SERVER value and is the baseline for every future write. Deleting it on
// settle opened a generation hole — a straggler from an older burst would land on the
// entry a LATER click had freshly created and poison its baseline, putting the phantom
// flag right back. `confirmedCommit` is what closes it: a write's acceptance only counts
// if no write that committed LATER has already been accepted.
//
// #291 — that number is the SERVER's (`commitSeq`, taken inside the same `with _lock:`
// block that writes the store), never this client's `seq`. `seq` is taken when a write is
// ISSUED, and two writes issued inside one round-trip go out on two connections whose
// server threads take the lock in either order — so issue order is simply not commit
// order, and a guard built on it stands on whichever answer was issued last instead of on
// the one that IS the store. `seq` keeps its OTHER job untouched: it decides which write
// still owns the (flag, row) for rollback and for REPORTING (`seqs[0] === sts[0].seq`),
// and that one really is about the manager's latest INTENT, not about the server's history.
//
// The two numbers are deliberately NOT interchangeable: `seq` is an independent counter
// per (flag, row), `confirmedCommit` is ONE global clock. That is what finally makes
// answers comparable across flags — but the stamping rule from the PR #290 review stands
// unchanged (a write may only stamp the guard of a flag it CLAIMED), because a write must
// not materialise bookkeeping for a flag it never wrote.
function _flagEntry(field, key, map) {
  const wk = field + '\u0000' + key;
  return _flagWrites[wk]
    || (_flagWrites[wk] = { wk, seq: 0, confirmed: !!map[key], confirmedCommit: 0, inflight: 0 });
}

// The server's commit number out of one answer, or `null` when the answer does not carry
// one — a success whose body `postToOrder` turned into `null` (a truncated response; this
// app is served through a tunnel), or a tab still running this file against a server that
// predates #291.
function _commitOf(body) {
  const n = body && body.commitSeq;
  return (typeof n === 'number' && isFinite(n)) ? n : null;
}

// An ACCEPTED answer with NO commit number cannot be placed in the server's history — and
// every way of GUESSING where it belongs is issue-order reasoning wearing a different hat.
// Both were tried and both are wrong:
//
//   * refuse it outright — `confirmed` freezes on a value the server has moved past, and
//     the next REFUSED write „rolls back" onto that stale value, i.e. does not roll back.
//     The row then shows a flag the server does not hold, right after the manager was told
//     the save failed. That is the #290 shape (and a regression against the code before
//     #291, measured on both).
//   * adopt it when this write is „still the latest issued and nothing else is out" — it
//     can adopt OVER a newer numbered answer, and when two unnumbered answers come back in
//     the reverse of their commit order it adopts NEITHER, leaving `confirmed` frozen and
//     the map forced back onto it.
//
// So the client does not decide at all: it asks the only party that knows. `loadOrders()`
// re-reads every flag from the server and drops the bookkeeping with it, which is exactly
// the recovery this situation calls for — and it is the same path a tab switch already
// takes, so nothing new can go stale. Debounced, because a burst of unnumbered answers is
// one event, not N.
//
// Reachable through a truncated body (`postToOrder` turns an unparsable one into `null`;
// this app is served through a tunnel) and through a tab that outlives a rollback deploy.
// Both are transient, so paying one extra read for them is the cheap side of the trade.
let _resyncPending = false;

async function _resyncFlagsFromServer() {
  if (_resyncPending) return;
  _resyncPending = true;
  try {
    await loadOrders();
  } finally {
    _resyncPending = false;
  }
  if (ACTIVE_TAB === 'toorder') renderToOrder();
}

// May this ACCEPTED answer be adopted as the flag's new server-known value? One rule, one
// number space: only an answer that says WHEN it committed, and only if nothing that
// committed later has been adopted already.
function _mayAdopt(commit, st) {
  return commit !== null && commit >= st.confirmedCommit;
}

// Every flag write answers `{ok, flags:{ordered, waiting, instock, unavailable}, commitSeq}`
// — the server's own account of the row AT THE MOMENT that write committed, plus WHEN it
// committed. Adopt it through the SAME gate the client's bookkeeping uses, so an answer
// that committed EARLIER can never overwrite one that committed later (`confirmedCommit`),
// and so a map another write is still holding optimistically is left alone (`inflight`).
// Returns true if anything moved.
//
// #291 — the comparison is on the SERVER's commit number, not on this client's issue
// counter. Issue order approximates commit order and is wrong exactly when it matters:
// two writes issued inside one round-trip travel on separate connections, so a write
// issued FIRST can take `with _lock:` LAST. Its answer is then the newest truth there is
// — it IS the store — and the old gate threw it away as stale for carrying the lower
// issue number, leaving the row showing the value that committed earlier.
//
// `claimed` = {field: the ISSUE number that field took}, which is now used only as the
// SET of flags this write may stamp — never as the number stamped. The answer describes
// all four flags, but a write may still only touch the guards of the ones it claimed:
// `_flagEntry` creates entries on demand, so stamping an unclaimed flag would materialise
// bookkeeping for a flag this write never wrote. (Under the old ISSUE numbering the
// restriction was load-bearing for CORRECTNESS too — the counters were independent per
// (flag, row), so a foreign number could outrank a flag's own accepted writes, freeze its
// `confirmed` on stale data and turn the next refused write's rollback into a no-op. A
// single global commit clock removes that hazard; the restriction stays for the reason
// above.)
//
// Nothing is lost by it, because no endpoint writes across the axes: a write can only ever
// change the flags it claimed. `/api/ordered` touches `ordered` alone (axis A clears
// nothing) and `_write_status_flag` only the three axis-B stores — and turning an axis-B
// status ON already claims all three of them (`toggleStatusFlag`).
function _mirrorServerFlags(body, key, claimed) {
  const flags = body && body.flags;
  const commit = _commitOf(body);
  // An answer with no commit number cannot be ordered against the others describing this
  // row, and here — unlike the write's OWN flag in `saveOrderWrite` — there is no „nothing
  // else is in flight for it" to fall back on, because these are the write's other flags.
  // Its own value is adopted there; the rest of the answer is simply not used.
  if (!flags || commit === null) return false;
  const maps = { ordered: ORDERED, waiting: WAITING, instock: INSTOCK, unavailable: UNAVAIL };
  let moved = false;
  for (const field of Object.keys(maps)) {
    if (typeof flags[field] !== 'boolean') continue;
    // an unclaimed flag is not even LOOKED UP: `_flagEntry` creates the entry on demand,
    // so touching one here would materialise bookkeeping for a flag this write never wrote
    if (claimed[field] === undefined) continue;
    const map = maps[field];
    const st = _flagEntry(field, key, map);
    if (_flagWrites[st.wk] !== st || commit < st.confirmedCommit) continue;
    st.confirmed = flags[field];
    st.confirmedCommit = commit;
    if (st.inflight !== 0 || !!map[key] === flags[field]) continue;
    if (flags[field]) map[key] = true; else delete map[key];
    moved = true;
  }
  return moved;
}

// One POST can move MORE than one flag on a row: turning an axis-B status on makes the
// server clear the other two in the same atomic write (#211), and the tab must mirror the
// whole of that write — optimistically at click time, and back again if it is refused.
// Every flag it touches claims its sequence number through the SAME `_flagWrites`
// bookkeeping, taken when the write is ISSUED (exactly like markGroupOrdered): a refusal
// then rolls each flag back to what the SERVER confirmed, never to a snapshot of the map.
// There is deliberately no second, unsequenced rollback path — that is the bug this
// bookkeeping exists to prevent, and a clear-side flag would be just as exposed to it.
// `writes` = [{field, map, value}], the first entry being the flag the manager clicked.
async function saveOrderWrite(path, payload, writes, key, what) {
  const ans = {};
  const sts = writes.map(w => _flagEntry(w.field, key, w.map));
  const seqs = sts.map(st => ++st.seq);
  // what this write CLAIMED, each flag under the number IT took — the only numbers that
  // may ever reach these flags' guards (see `_mirrorServerFlags`)
  const claimed = {};
  writes.forEach((w, i) => { claimed[w.field] = seqs[i]; });
  sts.forEach(st => { st.inflight += 1; });
  let err;
  // the decrement must be unskippable: postToOrder swallows every throw today, but a
  // leaked counter would permanently disable reconciliation for those (flag, row)
  // entries — they are never deleted, so they would never reach 0 again for this page
  try {
    writes.forEach(w => { if (w.value) w.map[key] = true; else delete w.map[key]; });
    err = await postToOrder(path, payload, ans);
  } finally { sts.forEach(st => { st.inflight -= 1; }); }
  // `loadOrders()` drops the maps when it re-reads the server, so an entry that is no
  // longer the live one for its key must not roll anything back over fresh data. The
  // wipe clears every entry at once, so this verdict is the same for the whole write.
  const live = _flagWrites[sts[0].wk] === sts[0];
  // Ownership for REPORTING is decided by the CLICKED flag alone. A write can still own a
  // flag it merely CLEARED (the newer write on the row need not have touched that one), and
  // treating that as ownership made a superseded write report a second time for one row —
  // the newer write already owns the row and speaks for it.
  const owner = live && seqs[0] === sts[0].seq;
  let repaint = false;
  // #291 — WHEN this write committed, from the server, never `seq` (when it was ISSUED).
  // One number per response, so it is read once rather than once per flag.
  const commit = _commitOf(ans.json);
  writes.forEach((w, i) => {
    const st = sts[i], seq = seqs[i];
    if (_flagWrites[st.wk] !== st) return;
    // `_mirrorServerFlags` below re-reads the same number and overwrites `confirmed` with
    // the server's own account of the flag; this line is what carries an answer that has
    // no `flags` to mirror, and the unnumbered one (`_mayAdopt`).
    if (!err && _mayAdopt(commit, st)) { st.confirmed = w.value; st.confirmedCommit = commit; }
    if (seq !== st.seq) {                     // a later write owns this (flag, row) now
      // …and once nothing else is out for it, the map owes the server's own last word
      if (st.inflight === 0 && !!w.map[key] !== st.confirmed) {
        if (st.confirmed) w.map[key] = true; else delete w.map[key];
        repaint = true;
      }
      return;
    }
    if (err && !!w.map[key] !== st.confirmed) {
      if (st.confirmed) w.map[key] = true; else delete w.map[key];
      repaint = true;
    }
  });
  // The SERVER is the authority and every answer says what the row now IS — and, since
  // #291, WHEN it said so. Two writes issued inside one round-trip travel on separate
  // connections and their server threads can take `with _lock:` in the REVERSE order, so
  // both succeed and the server's final state is the one that COMMITTED last, not the one
  // the client ISSUED last. Mirror the answered flags through the SAME
  // confirmed/confirmedCommit gate, so an answer that committed earlier can never overwrite
  // one that committed later, and only touch a map with nothing else in flight for it —
  // restricted to the flags this write claimed.
  const mirrored = live && !err && _mirrorServerFlags(ans.json, key, claimed);
  // Accepted, but it did not say WHEN — this client cannot place it in the server's
  // history, so it re-reads the row's flags instead of guessing (see `_mayAdopt`).
  if (!err && commit === null) _resyncFlagsFromServer();
  if (!live || !owner) {          // superseded by a newer write, or disowned by a reload
    if (repaint || mirrored) renderToOrder();
    // A write the reload DISOWNED has no map left to roll back — but it still failed, and
    // returning `false` in silence is the very silent-lost-write #214 exists to kill. A
    // write superseded by a NEWER one on the same row stays quiet on purpose: that newer
    // write owns the row and does the reporting.
    if (!live && err) toOrderSaveFailed(what, err, toOrderRowLabel(key), writes[0].value);
    return !err;
  }
  if (!err) {
    clearToOrderFail(what, toOrderRowLabel(key));   // this row's report is settled
    if (mirrored) renderToOrder();
    return true;
  }
  // Always repaint on the owner's failure, even when the map happens to already agree
  // with the server: the CLICK HANDLER painted the row (and its buttons) by hand before
  // this write was issued, so only a repaint puts the screen back in step with the maps.
  renderToOrder();                // roll the tab back BEFORE the message, never after
  toOrderSaveFailed(what, err, toOrderRowLabel(key), writes[0].value);
  return false;
}

const saveOrderFlag = (path, field, map, key, on, what) =>
  saveOrderWrite(path, { key, [field]: on }, [{ field, map, value: on }], key, what);

const saveOrdered = (key, on) =>
  saveOrderFlag('/api/ordered', 'ordered', ORDERED, key, on, 'Označenie „objednané“');

// #211 — axis B: the line's STATUS, and a line has one at a time. Built fresh on every
// call on purpose: `loadOrders()` REPLACES these map objects, so a module-level array
// holding them would go on writing into the map the reload threw away.
function statusFlagSpecs() {
  return [
    { field: 'waiting', path: '/api/waiting', map: WAITING, sel: '.to-wait',
      cls: 'waiting', what: 'Označenie „čaká sa“' },
    { field: 'instock', path: '/api/instock', map: INSTOCK, sel: '.to-instock',
      cls: 'instock', what: 'Označenie „skladom“' },
    { field: 'unavailable', path: '/api/unavailable', map: UNAVAIL, sel: '.to-unavail',
      cls: 'unavail', what: 'Označenie „nedostupné“' },
  ];
}

// Paint the row's three status affordances FROM the flag maps — the maps are the one
// truth the rest of the tab already reads (isHandled, the chips, the summary), so the
// row can never drift from them. Cheap and in place: a per-row toggle deliberately does
// NOT repaint the list (#208/#233), and all three buttons live in this one row anyway.
function paintRowStatus(row, key) {
  if (!row) return;
  for (const s of statusFlagSpecs()) {
    const on = !!s.map[key];
    row.classList.toggle(s.cls, on);
    const btn = row.querySelector(s.sel);
    if (!btn) continue;
    btn.classList.toggle('on', on);
    if (s.field === 'waiting') btn.textContent = on ? '⏳ Čaká sa' : '⏳ Počkať';
  }
}

// Turning a status ON says the other two are NOT the case, so the server clears them in
// one atomic write and the row shows that AT CLICK TIME — a row painted „čaká sa +
// skladom" for the length of a round-trip is the contradiction #211 is about, and on a
// slow link that is not a blink. Turning one OFF is no statement about the others.
function toggleStatusFlag(key, row, field) {
  const specs = statusFlagSpecs();
  const me = specs.find(s => s.field === field);
  const on = !me.map[key];
  const writes = [{ field: me.field, map: me.map, value: on }];
  if (on) {
    // EVERY other axis-B flag joins the write — NOT only the ones the client currently
    // shows. Filtering on `s.map[key]` was a real defect: a flag this client had already
    // cleared OPTIMISTICALLY for an earlier, still-in-flight write claimed no sequence
    // number here, so when that earlier write was REFUSED it was the sole owner of the
    // flag and rolled it back to life — over a newer write the server had accepted, whose
    // server-side clear had removed it. The row then showed two contradictory statuses
    // (the exact thing #211 removes) with a poisoned `confirmed` baseline for every later
    // write on it. Deleting an absent map key is a no-op and the server's conditional
    // write never touches a store it did not change, so claiming all three costs nothing.
    for (const s of specs) {
      if (s.field !== field) writes.push({ field: s.field, map: s.map, value: false });
    }
  }
  saveOrderWrite(me.path, { key, [field]: on }, writes, key, me.what);
  paintRowStatus(row, key);     // the maps are already written — mirror them now
  renderOrderFilters();
}

// Označiť celú skupinu dodávateľa objednané naraz (manažér objedná od dodávateľa
// všetko naraz). Pošle všetky per-riadkové kľúče cez bulk endpoint, updatne ORDERED
// mapu a prekreslí. items = riadky skupiny (each carries o.key = orderCode|itemCode).
// ORDERED sa mení až PO úspechu, takže pri zlyhaní netreba nič vracať — len to povedať.
//
// A write that does not go through saveOrderFlag still changes the same rows, so it joins
// the same `_flagWrites` bookkeeping — and it takes its OWNERSHIP numbers when it is
// ISSUED, exactly like a per-row write. Claiming them at RESPONSE time was the bug: the
// bulk then outranked per-row writes the manager issued AFTER clicking it (newer intent,
// and committed LATER on the server behind the bulk's own `with _lock:`), so his last
// click was silently inverted — the tab painted the row the bulk's way, the settle
// reconcile forced the map to match, and nothing was reported. Issue-time sequencing puts
// the bulk where it belongs in the manager's INTENT order.
//
// #291 — which answer is newest is a separate question, and the bulk answers it the same
// way every other write does: with the server's `commitSeq`, taken inside the bulk's own
// `with _lock:`. The endpoint returns no per-row `flags` (it moves one flag on many rows),
// so unlike `saveOrderWrite` there is nothing to mirror — the value it confirms is its own
// `ordered`, stamped with the moment the server actually wrote it.
async function markGroupOrdered(items, ordered) {
  const keys = items.map(o => o.key);
  const sts = keys.map(k => _flagEntry('ordered', k, ORDERED));
  const seqs = sts.map(st => ++st.seq);
  sts.forEach(st => { st.inflight += 1; });
  let err;
  const ans = {};
  // unskippable, for the same reason as in saveOrderFlag: a leaked counter would disable
  // reconciliation for those rows for the lifetime of the page
  try {
    err = await postToOrder('/api/ordered/bulk', { keys, ordered }, ans);
  } finally { sts.forEach(st => { st.inflight -= 1; }); }
  let repaint = false;
  const commit = _commitOf(ans.json);           // one number for the whole bulk
  keys.forEach((key, i) => {
    const st = sts[i], seq = seqs[i];
    if (_flagWrites[st.wk] !== st) return;      // loadOrders() disowned this write
    if (!err && _mayAdopt(commit, st)) { st.confirmed = ordered; st.confirmedCommit = commit; }
    if (seq !== st.seq) {                       // a later write owns this row now
      // …and once nothing else is out for it, the map owes the server's own last word
      if (st.inflight === 0 && !!ORDERED[key] !== st.confirmed) {
        if (st.confirmed) ORDERED[key] = true; else delete ORDERED[key];
        repaint = true;
      }
      return;
    }
    if (err) {
      // The bulk writes the map only on SUCCESS, so a refused owner is the ONE writer that
      // neither wrote its value nor has one to roll back — and a predecessor it superseded
      // skipped its own reconcile (still `inflight` then). Settling owes the server's last
      // word here too, or that predecessor's refused optimistic value stays for good.
      if (st.inflight === 0 && !!ORDERED[key] !== st.confirmed) {
        if (st.confirmed) ORDERED[key] = true; else delete ORDERED[key];
        repaint = true;
      }
      return;
    }
    if (ordered) ORDERED[key] = true; else delete ORDERED[key];
    repaint = true;
  });
  if (!err && commit === null) _resyncFlagsFromServer();   // same as saveOrderWrite
  if (err) {
    if (repaint) renderToOrder();        // roll the tab back BEFORE the message, never after
    toOrderSaveFailed('Hromadné označenie skupiny', err,
                      items.length ? 'skupina ' + effSup(items[0]) : '', ordered);
    return false;
  }
  clearToOrderFail('Hromadné označenie skupiny',
                   items.length ? 'skupina ' + effSup(items[0]) : '');
  renderToOrder();
  return true;
}

// ── Nedostupné tovary (#100) ───────────────────────────────────────────────
async function loadNedostupne() {
  try {
    const j = await (await fetch('/api/nedostupne')).json();
    NEDOSTUPNE = j.products || [];
    NEDOSTUPNE_BAD_CFG = !!j.bad_status_config;
  } catch (_) { NEDOSTUPNE = []; NEDOSTUPNE_BAD_CFG = false; }
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
  // The rows below were built from the DEFAULT statuses because the manager's file could
  // not be read, so they may be the wrong rows — say so before he acts on them. Own class
  // (styled from .autoerr): a second `.autoerr` on a tab breaks the strict e2e locators.
  if (NEDOSTUPNE_BAD_CFG) sec.appendChild(el('div', 'statuscfgerr',
    '⛔ Nastavenie stavov objednávok sa nedá prečítať — tento zoznam je zostavený podľa '
    + 'PREDVOLENÝCH stavov, takže nemusí sedieť. Oprav to na karte „Stavy objednávok" '
    + '(záložka Automatizácie).'));
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

// The pairing URL this row is actually SHOWING. A reviewed decision outranks an inline
// pairing BOTH on the row and in the eshop write-back (`order_pairing_rows` skips codes
// already covered by `link_rows`), so the editor must prefill, compare and save against
// the very same value — otherwise a correction is accepted and silently discarded (#242).
const rowPairUrl = (o) => (o.reviewKey ? o.supplierUrl : o.pairUrl) || '';

// Inline pairing: paste the supplier reorder URL straight onto an order line.
// Persists per forestshop code (covers items outside the review dataset too), or — when
// the link came from a reviewed decision — rewrites THAT decision (#242).
// #174/#242 — a `split` product's reorder link belongs to ONE SIZE, so there is nothing
// product-wide to save on this row: /api/order-decision-url refuses it (409, it would
// discard every per-size link), and the inline path is worse — order_pairings would win
// the write-back for that code and PERMANENTLY clobber an already-uploaded per-size link
// (`split_links` keeps its own uploaded_variant_links.json idempotency, so it never
// re-pushes). The ✂️ therefore takes him to the per-size panel instead of offering a save
// that can only fail or corrupt.
// #260 — `editorSnapHasWork` deliberately merges two states into ONE „carry this over"
// answer (see the note there): text the manager typed and has not saved, and a box he
// OPENED with ✏️/💬 and left empty. Both are kept across a repaint and both are closed by
// the trip to the sizes panel, so both are worth warning about — but only the first can
// be LOST, and calling an empty open box „rozpísaný neuložený text" warns him about work
// that does not exist. The counting is NOT narrowed — a second, narrower predicate beside
// a shared one is what the `outstandingOf` note forbids in so many words, and what
// `editorSnapHasWork`'s own „the same test the repaint uses" reasoning requires here;
// only the wording separates them.
// The count keeps the established `(N×)` shape after a singular noun, so no second
// declension rule is introduced beside `itemsWord`.
function leaveEditorsWarning(typed, opened) {
  if (!typed && !opened) return '';
  const parts = [];
  if (typed) parts.push(`rozpísaný neuložený text (${typed}×)`);
  if (opened) parts.push(`otvorené prázdne políčko (${opened}×)`);
  // each state gets its own verb: text is thrown away, an empty box is merely closed —
  // one shared „zahodí" would put the merely-opened box back under the claim #260 removed
  const fate = (typed && opened) ? 'zahodí a zavrie' : (typed ? 'zahodí' : 'zavrie');
  return `⚠️ Máš ${parts.join(' a ')} v objednávkach. `
    + `Prechodom na veľkosti sa ${fate}. Pokračovať?`;
}

async function openSplitSizes(o) {
  const p = (PRODUCTS || []).find(x => x.key === o.reviewKey);
  if (!p) {
    // #234 — through the banner like every other refusal on this tab: a modal here
    // freezes the tab just as hard, and this one lands mid-click while he is working
    // down a group.
    toOrderSaveFailed('Odkaz podľa veľkostí', 'produkt sa nenašiel v revízii — otvor tab '
                      + '„Kontrola párovania" a oprav odkaz pri konkrétnej veľkosti',
                      toOrderPartLabel('kód', o.itemCode), o.reviewKey);
    return;
  }
  // Leaving the tab rebuilds `#list` from scratch, so every open inline editor on every
  // OTHER row — and the half-typed pair / supplier / comment text in it — goes with it.
  // The button sits IN the row, right beside the ✏️ that edits in place, so it does not
  // read as navigation: warn first rather than lose the work silently (#205/#233). Same
  // predicate the repaint machinery uses, so „would be lost" cannot drift from „is lost".
  const busy = captureOpenEditors().filter(
    s => editorSnapHasWork(s, ORDERS.find(x => x.key === s.key)));
  // #260 — WHAT is at stake stays the one predicate above; only the sentence tells the
  // two states it merges apart (typed text vs a box he only opened and left empty).
  const typed = busy.filter(s => s.value.trim()).length;
  const warn = leaveEditorsWarning(typed, busy.length - typed);
  if (warn && !confirm(warn)) return;
  splitOpen.add(p.key);
  // in memory ONLY — 'split' sits under the „Dobré / Vybrané" filter, so the review tab
  // has to be on it to show the card. Persisting it replaced whichever filter the
  // manager had chosen, permanently, as a side effect of one ✂️ click.
  FILTER = 'good';
  await switchTab('review');
  const card = [...document.querySelectorAll('#list .card')]
    .find(c => c.dataset.key === p.key);
  if (card) card.scrollIntoView({ block: 'center' });
}

async function savePairUrl(o, url) {
  if (o.reviewStatus === 'split') {
    // unreachable from the row (a split row shows ✂️, not ✏️) — but a stale captured
    // editor must never turn into a product-wide write on a per-size product
    await openSplitSizes(o);
    return false;
  }
  if (url && !/^https?:\/\//.test(url)) {
    // #214 — the guard used to drop a typo'd URL on the floor with no explanation.
    // #234 — and it said so through a modal, which is the same interruption as a refused
    // write; a client-side refusal is a refusal and belongs in the same banner.
    toOrderSaveFailed('Párovacia URL', 'zadaj adresu začínajúcu http:// alebo https://',
                      toOrderPartLabel('kód', o.itemCode), url);
    return false;
  }
  if (o.reviewKey) {
    if (!url) {
      // clearing a reviewed pairing is a review-tab decision ('↩ Vrátiť'); an empty save
      // here would look like it un-paired the product while nothing actually changed
      toOrderSaveFailed('Párovacia URL', 'produkt je napárovaný v revízii — prázdnou '
                        + 'hodnotou sa párovanie nezruší; zadaj opravenú adresu, alebo '
                        + 'zruš párovanie v tabe „Kontrola párovania"',
                        toOrderPartLabel('kód', o.itemCode), url);
      return false;
    }
    const derr = await postToOrder('/api/order-decision-url', { key: o.reviewKey, url });
    if (derr) {
      toOrderSaveFailed('Párovacia URL', derr, toOrderPartLabel('kód', o.itemCode), url);
      return false;
    }
    // ONE decision covers EVERY variant code of the product, so every row it feeds must
    // follow — same reason as the per-product mirror below (#204): without it a sibling
    // size keeps showing the old link until the next reload.
    for (const x of ORDERS) {
      if (x.reviewKey === o.reviewKey) { x.supplierUrl = url; x.reviewStatus = 'manual'; }
    }
    clearToOrderFail('Párovacia URL', toOrderPartLabel('kód', o.itemCode));
    renderToOrder();
    return true;
  }
  const err = await postToOrder('/api/order-pair', { code: o.itemCode, url });
  if (err) { toOrderSaveFailed('Párovacia URL', err, toOrderPartLabel('kód', o.itemCode), url); return false; }
  // the pairing is keyed by itemCode (a PRODUCT property) → /api/orders already serves
  // it on EVERY order line of that code, so mirror that client-side: without it the
  // sibling lines keep showing an empty paste box for a product that IS paired (#204).
  for (const x of ORDERS) if (x.itemCode === o.itemCode) x.pairUrl = url;
  clearToOrderFail('Párovacia URL', toOrderPartLabel('kód', o.itemCode));
  renderToOrder();                       // re-render so the new link shows immediately
  return true;
}

// inline-pairing editor (code + URL input + save) — used for an unpaired row and
// when ✏️-editing an already-paired one
function pairEditor(o, focus) {
  const pair = el('div', 'to-pair');
  pair.dataset.editor = 'pair';        // so a repaint can carry unsaved typing over
  pair.appendChild(el('span', 'to-pcode', escapeHtml(o.itemCode || '')));
  const inp = el('input', 'to-pairurl'); inp.type = 'url';
  const cur = rowPairUrl(o);
  inp.placeholder = cur ? 'upraviť párovaciu URL…' : 'vlož párovaciu URL dodávateľa…';
  inp.value = cur;
  const save = el('button', 'to-pairsave', '💾 Spárovať');
  save.title = 'Uložiť párovaciu URL — objaví sa ako odkaz a pôjde do importu';
  const doSave = () => commitEditor(pair, () => savePairUrl(o, inp.value.trim()));
  save.onclick = doSave;
  inp.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); doSave(); } };
  pair.appendChild(inp); pair.appendChild(save);
  if (focus) setTimeout(() => inp.focus(), 0);
  return pair;
}

// Does this order line carry its OWN supplier from Shoptet? A supplier of only spaces is
// truthy but is not a supplier. Single source of truth for BOTH the grouping (effSup) and
// the row-level gate that shows the inline assign editor — when only effSup trimmed, such
// a row grouped under '—' yet showed no editor, i.e. the one place it could be fixed.
const hasOwnSupplier = (o) => !!(o.supplier || '').trim();

// Every href built from a STORE value goes through this: the write endpoints validate
// the scheme, but a `javascript:` left in a store by any other path would otherwise
// render as a clickable link. Same guard the GRUBE .de link already carries.
const safeHttpUrl = (u) => (/^https?:\/\//.test(u || '') ? u : '');

// Swap a read-only chip/link for its editor. The marker says the manager OPENED this
// one himself, so a repaint keeps it open even if he has emptied it (unlike the
// editors a row renders by default when nothing is paired/assigned yet).
function openRowEditor(node, editor, alsoRemove) {
  editor.dataset.editorOpened = '1';
  node.replaceWith(editor);
  if (alsoRemove) alsoRemove.remove();
}

// Saving CONSUMES the „he opened it himself" claim: once the manager has committed the
// value, the repaint that follows must be free to collapse the editor back to the link /
// tag / add-button — including when he deliberately saved it EMPTY to delete the note
// (value and stored are then both '', so only this marker tells the two apart from the
// „opened, nothing typed yet" box that MUST survive). A save that FAILED gives the claim
// straight back: that box still holds his unsaved work.
async function commitEditor(wrap, save) {
  wrap.dataset.editorSaving = '1';
  if (await save()) return;
  delete wrap.dataset.editorSaving;
  // …but a repaint during the flight (a failed flag write, a sibling's successful save)
  // has already dropped this node, and an EMPTY commit leaves captureOpenEditors nothing
  // to carry over (value '' with the „he opened it himself" claim consumed) — so the box
  // simply vanished, and the claim just went back to a DETACHED node. He is being told
  // his save failed while the editor holding that work is gone: give it back.
  if (!wrap.isConnected) reopenDetachedEditor(wrap);
}

// Re-open the editor `wrap` used to be, on the freshly rendered row, with the text it
// held. The old subtree is detached but intact, so it still carries both the row key and
// the value. Deliberately mirrors restoreOpenEditors' mechanics (same `_EDITORS` spec) —
// this is the same job, just one repaint too late to be caught by the snapshot.
function reopenDetachedEditor(wrap) {
  const spec = _EDITORS[wrap.dataset.editor];
  const oldRow = wrap.closest('.toorder-row');
  const key = oldRow && oldRow.dataset.key;
  const opened = wrap.dataset.editorOpened === '1';
  const inpOld = spec && wrap.querySelector(spec.input);
  if (!spec || !key || !inpOld) return;
  const value = inpOld.value;
  if (!value.trim() && !opened) return;      // nothing of his to give back
  const row = [...document.querySelectorAll('#list .toorder-row')]
    .find(r => r.dataset.key === key);
  const o = row && ORDERS.find(x => x.key === key);
  if (!o) return;                            // row filtered out / gone — nowhere to put it
  let inp = row.querySelector(spec.input), ed = null;
  if (!inp) { ed = spec.open(o, row); inp = ed && ed.querySelector(spec.input); }
  if (!inp) return;
  if (opened && ed) ed.dataset.editorOpened = '1';   // the claim comes back with the box
  inp.value = value;
  inp.focus();
}

// effective supplier for grouping: the order's OWN supplier (from Shoptet) wins;
// a manual assignment only fills in a line that arrived WITHOUT a supplier (BUG 1 —
// a stale assignment must never prebiehať the real supplier / clobber the eshop).
const effSup = (o) => (hasOwnSupplier(o) ? o.supplier : (o.assignedSupplier || '')).trim() || '—';

// #203 — the supplier name is free text the manager types by hand, so the SAME supplier
// keeps arriving spelled differently ('CITRADE' / 'Citrade' / 'Citrade  s.r.o.'). Group,
// colour and filter by a case+whitespace-insensitive key so one supplier is ONE chip and
// ONE group; the CASE of the stored value is never touched (it goes verbatim into the
// eshop `supplier` column). normSupplierName mirrors the server's `" ".join(s.split())`
// exactly, so the name the client shows is the name the server stored.
const normSupplierName = (s) => String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
const supKey = (s) => normSupplierName(s).toLocaleLowerCase('sk');
// Chip/filter keys are namespaced so a supplier literally named "All" can never collide
// with the 'all' (Všetci) sentinel — with case-folding that collision got likelier.
const supFilterKey = (o) => 's:' + supKey(effSup(o));
// The spelling to SHOW for a normalised key: the one used most often (ties → alphabetical,
// so the label never flickers between renders).
const supCanonPick = (counts) => Object.keys(counts).sort(
  (a, b) => (counts[b] - counts[a]) || (a < b ? -1 : a > b ? 1 : 0))[0];

// One pass over ORDERS → { canon, known }:
//   canon: chip/group key ('s:'+normalised) → the spelling to display
//   known: every distinct supplier seen in EITHER column, deduped case-insensitively,
//          canonical spelling, alphabetical — the `known-suppliers` autocomplete list
//          (which exists precisely to stop typo/case fragmentation at the source).
// Object.create(null) everywhere the KEY comes from the manager's free text: a supplier
// named '__proto__' silently swallows writes on a normal object literal (the chip would
// render as its raw key and vanish from the datalist) and one named 'constructor' would
// write the counter onto the global Object.
function supplierSpellingIndex(orders) {
  const bump = (m, k, raw) => {
    const b = (m[k] = m[k] || Object.create(null));
    b[raw] = (b[raw] || 0) + 1;
  };
  const grp = Object.create(null), all = Object.create(null);
  for (const o of orders || []) {
    // the DISPLAYED spelling is whitespace-normalised (mirrors what the server stored),
    // only the capitalisation the manager chose is preserved
    bump(grp, supFilterKey(o), normSupplierName(effSup(o)));
    for (const raw of [o.supplier, o.assignedSupplier]) {
      const name = normSupplierName(raw);
      if (name) bump(all, supKey(name), name);
    }
  }
  const canon = Object.create(null);
  for (const k of Object.keys(grp)) canon[k] = supCanonPick(grp[k]);
  // The datalist must offer the SAME spelling the chip shows. `grp` counts one vote per
  // ROW (effSup — the order's own supplier wins), `all` one vote per COLUMN, so a stale
  // assignment could outvote the chip: the tab said 'CITRADE (4)' while the autocomplete
  // offered 'Citrade', and the manager typed the one the datalist gave him. `all` still
  // decides WHICH suppliers are offered (a shadowed assignment is a real name he used) —
  // only the spelling of a name that has a chip is taken from `canon`.
  const known = Object.keys(all).sort().map(k => canon['s:' + k] || supCanonPick(all[k]));
  return { canon, known };
}

// Inline supplier assign: fill in the supplier for an order line that arrived WITHOUT
// one. Persists per forestshop code; the row then regroups under that supplier and the
// name is written back to the eshop `supplier` field by the nightly upload.
async function saveSupplier(o, raw) {
  // normalise BEFORE sending, with the same rule the endpoint applies — otherwise the row
  // (and the ✏️ editor reopened on it) would keep showing 'Citrade   s.r.o.' while the
  // store, and the eshop `supplier` column it feeds, hold 'Citrade s.r.o.'
  const supplier = normSupplierName(raw);
  const err = await postToOrder('/api/order-supplier', { code: o.itemCode, supplier });
  if (err) { toOrderSaveFailed('Priradenie dodávateľa', err, toOrderPartLabel('kód', o.itemCode), supplier); return false; }
  // assignment is keyed by itemCode (a product property) → apply to EVERY order line
  // of that code, so all sibling lines regroup together (not just the clicked one)
  for (const x of ORDERS) if (x.itemCode === o.itemCode) x.assignedSupplier = supplier;
  clearToOrderFail('Priradenie dodávateľa', toOrderPartLabel('kód', o.itemCode));
  renderToOrder();                 // re-render: the row(s) move into the supplier group
  return true;
}

// supplier editor (text input with known-supplier autocomplete + save) — used for an
// unassigned no-supplier row and when ✏️-editing an already-assigned one
function supplierEditor(o, focus) {
  const wrap = el('div', 'to-supplier');
  wrap.dataset.editor = 'supplier';    // so a repaint can carry unsaved typing over
  const inp = el('input', 'to-supinput'); inp.type = 'text';
  inp.placeholder = o.assignedSupplier ? 'upraviť dodávateľa…' : 'doplniť dodávateľa…';
  inp.value = o.assignedSupplier || '';
  inp.setAttribute('list', 'known-suppliers');   // autocomplete from existing suppliers
  const save = el('button', 'to-supsave', '💾 Uložiť');
  save.title = 'Priradiť dodávateľa — položka sa zaradí pod neho a zapíše sa do eshopu';
  const doSave = () => commitEditor(wrap, () => saveSupplier(o, inp.value.trim()));
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
async function saveOrderComment(o, comment) {
  const err = await postToOrder('/api/order-comment', { orderCode: o.orderCode, comment });
  if (err) { toOrderSaveFailed('Komentár k objednávke', err, toOrderPartLabel('obj.', o.orderCode), comment); return false; }
  if (comment) ORDER_COMMENTS[o.orderCode] = comment; else delete ORDER_COMMENTS[o.orderCode];
  clearToOrderFail('Komentár k objednávke', toOrderPartLabel('obj.', o.orderCode));
  renderToOrder();
  return true;
}

// comment editor (multi-line textarea + save) — opened from the 💬 button on a row.
// Ctrl/⌘+Enter saves (plain Enter keeps the note multi-line, like the admin textarea).
function commentEditor(o, focus) {
  const wrap = el('div', 'to-comment-edit');
  wrap.dataset.editor = 'comment';     // so a repaint can carry unsaved typing over
  const inp = el('textarea', 'to-cominput');
  inp.rows = 2;
  inp.placeholder = 'komentár k objednávke…';
  inp.value = ORDER_COMMENTS[o.orderCode] || '';
  const save = el('button', 'to-comsave', '💾 Uložiť');
  save.title = 'Uložiť komentár k objednávke (Ctrl+Enter)';
  const doSave = () => commitEditor(wrap, () => saveOrderComment(o, inp.value.trim()));
  save.onclick = doSave;
  inp.onkeydown = (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); doSave(); }
  };
  wrap.appendChild(inp); wrap.appendChild(save);
  if (focus) setTimeout(() => inp.focus(), 0);
  return wrap;
}

// A line's quantity as a NUMBER. The export gives it as a string and the row falls back
// to '1' when it is missing, so the arithmetic must fall back the same way — a line the
// tab shows as „1 ks" may never count as 0 in the total.
function orderQty(o) {
  const n = parseInt(o && o.qty, 10);
  return isNaN(n) ? 1 : n;
}

// #206 — build_to_order_rows emits ONE row per order line and never dedups, so the same
// product ordered by three customers is three rows and the manager had to add the
// quantities up in his head before ordering. This is the VISUAL total only: the row model
// is untouched (each line keeps its own key, flags and order). itemCode → { qty, lines }
// over whatever set it is handed — the CHIP is fed `outstandingOf(...)` (the work left),
// the tooltip additionally the whole group (the full demand).
function groupQtyTotals(items) {
  const t = Object.create(null);        // keys are itemCodes from the export (free text)
  for (const o of items || []) {
    const code = o.itemCode || '';
    const e = (t[code] = t[code] || { qty: 0, lines: 0 });
    e.qty += orderQty(o);
    e.lines += 1;
  }
  return t;
}

// #206 — what the „Σ spolu" chip says for one product, or `null` when it gets no chip.
// ONE place, because TWO paths render it: the full repaint (renderOrderRow) and the
// in-place refresh after a flag toggle (refreshOrderTotals), which must never disagree.
// The chip appears when the product genuinely spans SEVERAL of this supplier's order
// lines — counted on the WHOLE group, not on the outstanding rest: with 1 of 2 lines
// flagged the manager still needs the full demand, and „it is one hover away" is only
// true while the chip carrying that tooltip is on screen. On a product with a single
// order line there is nothing to add up and the chip would just repeat the qty beside it.
// The TEXT is the work LEFT — the same set „Kopírovať objednávku" pastes, so screen and
// e-mail always agree; the full demand rides in the tooltip instead of as a second number
// the manager would have to choose between.
function totalChipSpec(totals, code) {
  const all = totals && totals.all && totals.all[code];
  if (!all || all.lines < 2) return null;
  const open = (totals.open && totals.open[code]) || null;
  const openQty = open ? open.qty : 0;      // every line settled → 0 ks left to order
  return { text: `Σ spolu ${openQty} ks`,
           title: `Spolu vo všetkých objednávkach: ${all.qty} ks · nevybavené: ${openQty} ks` };
}

// …rendered as a node (.textContent/.title are property writes — the numbers are derived,
// but the chip is built in one place so no caller can reintroduce an innerHTML path).
function totalChip(spec) {
  const sum = el('span', 'to-total');
  sum.textContent = spec.text;
  sum.title = spec.title;
  return sum;
}

// Slovenské skloňovanie po číslovke — 1 → položka (v akuzatíve „vybaviť 1 položku"),
// 2–4 → položky, 0 a 5+ → položiek. Jeden helper pre hlavičku kopírovaného e-mailu
// (nominatív) aj pre súhrn nad zoznamom (akuzatív po „vybaviť"); líšia sa len v
// jednotnom čísle. „(1 položiek)" išlo dodávateľovi do mailu — nie je to kozmetika.
function itemsWord(n, acc) {
  if (n === 1) return acc ? 'položku' : 'položka';
  return (n >= 2 && n <= 4) ? 'položky' : 'položiek';
}

// The tab's own subtitle counts the same lines („7 otvorených položiek u dodávateľov"),
// and it hard-coded the genitive just like the group header did — „1 otvorených položiek"
// sat a few pixels above the header #240 was filed about. It needs its OWN function rather
// than a bare `itemsWord` call because the ADJECTIVE declines with the noun as well
// (1 → otvorená položka, 2–4 → otvorené položky, 0 and 5+ → otvorených položiek); the
// noun itself still comes from the one helper, so the rule stays in a single place.
function openItemsPhrase(n) {
  const adj = n === 1 ? 'otvorená' : ((n >= 2 && n <= 4) ? 'otvorené' : 'otvorených');
  return `${n} ${adj} ${itemsWord(n)}`;
}

// The lines of a supplier group that are still WORK — exactly `!isHandled`, and there may
// never be a second, narrower predicate beside it (see the note there). ONE scope for the
// „skryť poriešené" filter (#205), the supplier chip colours, the #208 toolbar tally, the
// „Σ spolu" chip (#206), the copied order (#207) and the `#empty` wording, so no two of
// them can ever describe different work — and DELIBERATELY independent of whether the
// #205 filter currently HIDES the rest: what is left to order does not depend on the view.
const outstandingOf = (items) => (items || []).filter(o => !isHandled(o));

const TO_COPY_LABEL = '📋 Kopírovať objednávku';
const TO_COPY_EMPTY_LABEL = 'Nič na objednanie';
// what the button takes, spelled out — the scope is a rule, not folklore
const TO_COPY_TITLE = 'Skopíruje riadky, ktoré ešte treba objednať (kód, veľkosť, ks, '
  + 'odkaz) — bez objednaných, čakajúcich, skladových a nedostupných';

// The link to paste for a line, in the same precedence the ROW itself renders: the
// reviewed decision link, then the inline pairing, then the grube .de order page. Only
// http(s) survives (`safeHttpUrl`) — a stored value the row refuses to turn into a link
// must never reach the supplier's inbox either.
const _copyUrl = (o) => safeHttpUrl(o.supplierUrl) || safeHttpUrl(o.pairUrl)
                        || safeHttpUrl(o.grubeDeUrl) || '';
// GRUBE's per-size itemId — the code GRUBE actually wants in the e-mail; '' elsewhere.
const _copyGrube = (o) => String((o && o.grubeItemId) || '').trim();

// #207 — GRUBE (and most of the others) has no B2B auto-ordering: the manager writes the
// order by hand, so he needs the whole supplier list as plain text in one click.
// The text AGGREGATES by product+SIZE, summing the pieces (the same arithmetic #206 put
// on screen): an order to a supplier asks for 3 ks of S1, never for the three separate
// customer lines that produced it. Order of first appearance is kept, so the pasted list
// reads in the same order as the tab.
// It is handed the OUTSTANDING lines (`outstandingOf`), never the rendered ones: a line
// already ticked „objednané" is goods on their way, and re-ordering it is the one mistake
// a pasted e-mail cannot take back.
function orderCopyLines(items) {
  const seen = Object.create(null), out = [];   // keys are export free text -> null proto
  for (const o of items || []) {
    const code = o.itemCode || '';
    const size = (o.size || '').trim();
    const k = code + ' ' + size;
    let e = seen[k];
    if (!e) {
      e = seen[k] = { code, size, qty: 0, grube: '', url: '' };
      out.push(e);
    }
    e.qty += orderQty(o);
    if (!e.grube) e.grube = _copyGrube(o);
    if (!e.url) e.url = _copyUrl(o);
  }
  // empty parts are DROPPED, never padded with a placeholder — a '—' column in a pasted
  // e-mail reads like an instruction to the supplier
  return out.map(e => [e.code, e.grube ? 'grube ' + e.grube : '', e.size,
                       e.qty + ' ks', e.url].filter(Boolean).join(' | '));
}

// The Clipboard API needs a secure context AND a permission; when it is missing or
// refused, fall back to the legacy selection + execCommand path instead of leaving the
// manager with a button that only ever says no.
async function copyPlainText(text) {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (_) { /* fall through to the legacy path */ }
  // `ta.select()` steals the caret from whatever the manager is typing in, so the focused
  // element and its selection are put back afterwards. The removal lives in `finally`: a
  // throwing execCommand used to leave the whole order text parked in an invisible,
  // tab-focusable textarea on <body> — one per failed click, forever.
  const prev = document.activeElement;
  let prevSel = null;
  try { prevSel = [prev.selectionStart, prev.selectionEnd]; } catch (_) { /* no selection API */ }
  const ta = document.createElement('textarea');
  try {
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    return !!document.execCommand('copy');
  } catch (_) {
    return false;
  } finally {
    ta.remove();
    if (prev && prev.focus && document.contains(prev)) {
      try {
        prev.focus();
        if (prevSel && prevSel[0] != null && prev.setSelectionRange) {
          prev.setSelectionRange(prevSel[0], prevSel[1]);
        }
      } catch (_) { /* element cannot take focus / has no selection API */ }
    }
  }
}

function orderCopyText(supplierLabel, items) {
  const lines = orderCopyLines(items);
  return [`Objednávka — ${supplierLabel} (${lines.length} ${itemsWord(lines.length)})`,
          ...lines].join('\n');
}

function renderOrderRow(o, totals) {
  const row = el('div', 'toorder-row' + (ORDERED[o.key] ? ' done' : '') + (WAITING[o.key] ? ' waiting' : '')
    + (INSTOCK[o.key] ? ' instock' : '') + (UNAVAIL[o.key] ? ' unavail' : ''));
  row.dataset.key = o.key; row.dataset.code = o.itemCode;
  const cb = el('input'); cb.type = 'checkbox'; cb.checked = !!ORDERED[o.key];
  cb.title = 'Označiť ako objednané';
  cb.onchange = () => { saveOrdered(o.key, cb.checked); row.classList.toggle('done', cb.checked); renderOrderFilters(); };
  row.appendChild(cb);
  // A stored value `safeHttpUrl` refuses must NOT become an <a> at all: `href=""`
  // resolves to the PAGE ITSELF, so one click reloads the tab and takes every open
  // editor — and the half-typed work in it — with it. The value is never echoed back
  // into a tooltip either.
  const supHref = safeHttpUrl(o.supplierUrl), pairHref = safeHttpUrl(o.pairUrl);
  // #242 — EVERY pairing this row can show gets the same ✏️. A reviewed link used to
  // be read-only here, so a wrong link ('mám to tam zle') meant hunting the product
  // down in the review tab and pairing it from scratch; the save routes itself to
  // whichever store owns the value (savePairUrl), so the fix cannot be a no-op.
  // …unless the product is split per size (#174): then the value belongs to ONE size and
  // the only honest affordance is the per-size panel. A DIFFERENT class matters — the
  // `.to-pairedit` selector is what `_EDITORS.pair.open` reopens after a repaint, and a
  // split row must never get a product-wide paste box back that way either.
  const pairPencil = (node) => {
    if (o.reviewStatus === 'split') {
      const sp = el('button', 'to-splitedit', '✂️');
      sp.title = 'Tento produkt má vlastný odkaz pre každú veľkosť — otvoriť veľkosti';
      sp.onclick = () => openSplitSizes(o);
      return sp;
    }
    const edit = el('button', 'to-pairedit', '✏️');
    edit.title = 'Zmeniť / opraviť párovaciu URL';
    edit.onclick = () => openRowEditor(node, pairEditor(o, true), edit);
    return edit;
  };
  if (supHref) {
    // reviewed decision link — correcting it here rewrites that decision itself
    const a = el('a', 'to-link'); a.href = supHref; a.target = '_blank'; a.rel = 'noopener';
    a.textContent = '🔗 ' + (o.itemCode || 'link');
    row.appendChild(a);
    row.appendChild(pairPencil(a));
  } else if (o.supplierUrl) {
    // slot with an unusable value — say so instead of faking a link, and let it be
    // repaired right here (this is the row that most needs fixing)
    const bad = el('span', 'to-badlink', '🔗 ' + escapeHtml(o.itemCode || 'link'));
    bad.title = 'Uložená adresa nie je platný http(s) odkaz — oprav ju cez ✏️';
    row.appendChild(bad);
    row.appendChild(pairPencil(bad));
  } else if (pairHref) {
    // inline-napárované → svieti rovnako ako ostatné napárované (🔗 odkaz) +
    // malá ✏️ na opravu, ak dal zlú URL
    const a = el('a', 'to-link'); a.href = pairHref; a.target = '_blank'; a.rel = 'noopener';
    a.textContent = '🔗 ' + (o.itemCode || 'link');
    row.appendChild(a);
    row.appendChild(pairPencil(a));
  } else {
    // an unusable inline pairing falls through to the paste box (its value pre-filled,
    // inert in an input) so the manager can repair it right where he sees it
    // nenapárované → políčko na vloženie URL (otvára produkt pri objednávaní)
    row.appendChild(pairEditor(o, false));
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
  const spec = totalChipSpec(totals, o.itemCode || '');
  if (spec) row.appendChild(totalChip(spec));
  row.appendChild(el('span', 'to-name', escapeHtml(o.name || '')));
  // supplier assign — ONLY for order lines that arrived WITHOUT a supplier. Same shape
  // as the URL pairing: doplniť → svieti názov + malá ✏️ na opravu.
  if (!hasOwnSupplier(o)) {
    if (o.assignedSupplier) {
      const tag = el('span', 'to-suptag', '🏷️ ' + escapeHtml(o.assignedSupplier));
      tag.title = 'Doplnený dodávateľ (zapíše sa do eshopu)';
      row.appendChild(tag);
      const sed = el('button', 'to-supedit', '✏️');
      sed.title = 'Zmeniť / opraviť dodávateľa';
      sed.onclick = () => openRowEditor(tag, supplierEditor(o, true), sed);
      row.appendChild(sed);
    } else {
      row.appendChild(supplierEditor(o, false));
    }
  }
  if (o.orderDate) {
    const d = el('span', 'to-date', '📅 ' + fmtDate(o.orderDate));
    d.title = 'Dátum objednávky';
    row.appendChild(d);
  }
  // ⚠️ stará NEVYBAVENÁ objednávka: keď nie je nijako poriešená a je staršia než
  // STALE_ORDER_DAYS → nenápadný badge + trieda na riadok (aby nezapadla dole).
  // Vybavená (ordered/waiting/instock/unavailable) alebo čerstvá → žiadny badge.
  const ageDays = orderAgeDays(o.orderDate);
  if (!isHandled(o) && ageDays > STALE_ORDER_DAYS) {
    row.classList.add('stale');
    const st = el('span', 'to-staleage', `⚠️ ${ageDays} dní`);
    st.title = `Nevybavená objednávka stará ${ageDays} dní — pozri, nech nezapadne`;
    row.appendChild(st);
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
    ce.onclick = () => openRowEditor(tag, commentEditor(o, true), ce);
    row.appendChild(ce);
  } else {
    const add = el('button', 'to-comadd', '💬 Komentár');
    add.title = 'Pridať komentár k objednávke';
    add.onclick = () => openRowEditor(add, commentEditor(o, true));
    row.appendChild(add);
  }
  // 'čaká sa' — aktívna objednávka, ktorú zatiaľ neobjednávame/naskladňujeme
  const w = el('button', 'to-wait' + (WAITING[o.key] ? ' on' : ''));
  w.textContent = WAITING[o.key] ? '⏳ Čaká sa' : '⏳ Počkať';
  w.title = 'Aktívna objednávka, ktorá sa zatiaľ nenaskladňuje — čaká sa na dodávateľa, '
    + 'zbierame viac položiek, alebo dohoda so zákazníkom (napr. september)';
  w.onclick = () => toggleStatusFlag(o.key, row, 'waiting');
  row.appendChild(w);
  // 'skladom' — už máme / naskladnené, a 'nedostupné' — u dodávateľa nedostupné.
  // The three are ONE axis (#211): switching one on switches the conflicting one off, on
  // the server and — through the same click — on the row.
  const inStk = el('button', 'to-instock' + (INSTOCK[o.key] ? ' on' : ''), '✓ Skladom');
  inStk.title = 'Máme skladom / naskladnené (zhasne „čaká sa" aj „nedostupné")';
  inStk.onclick = () => toggleStatusFlag(o.key, row, 'instock');
  row.appendChild(inStk);
  const unavailBtn = el('button', 'to-unavail' + (UNAVAIL[o.key] ? ' on' : ''), '✗ Nedostupné');
  unavailBtn.title = 'U dodávateľa nedostupné (zhasne „čaká sa" aj „skladom")';
  unavailBtn.onclick = () => toggleStatusFlag(o.key, row, 'unavailable');
  row.appendChild(unavailBtn);
  return row;
}

// A line is "poriešené" (resolved) once the manager put ANY flag on it — objednané /
// počkať / skladom / nedostupné. Read from the LIVE flag maps (ORDERED/WAITING/INSTOCK/
// UNAVAIL), which the toggle handlers update in place — NOT the o.* snapshot, which is
// frozen at /api/orders fetch time and would leave chips stale until a full reload (#86).
//
// THE ONE PREDICATE of the tab. Do NOT introduce a second, narrower one beside it: it was
// tried (a „settled only" scope that kept „čaká sa" in the copy and the „Σ spolu" chip)
// and it made the surfaces contradict each other on screen — the toolbar reading „ostáva
// vybaviť 0 z 2" next to a chip insisting „nevybavené: 3 ks", a RED (done) supplier chip
// over pieces the app still counted as work, and with „skryť poriešené" on the group and
// its copy button gone while those pieces were supposedly outstanding.
// „⏳ Čaká sa" means „this line is NOT today's work" in all three meanings the row
// button's own tooltip lists — čaká sa na dodávateľa (already at the supplier), zbierame
// viac položiek (parked on purpose), dohoda so zákazníkom (deferred). None of them belong
// in the order the manager is placing right now. When he decides to order a parked line he
// switches „⏳ Čaká sa" off and it re-enters the outstanding set — that toggle IS the
// workflow, and it is what keeps one number for one supplier order.
function isHandled(o) {
  return !!(ORDERED[o.key] || WAITING[o.key] || INSTOCK[o.key] || UNAVAIL[o.key]);
}

// #208 — the one global tally above the list. The supplier chips count LINES PER
// SUPPLIER; what the manager had no way to see is how much of today's work is left in
// total. Read from the LIVE flag maps (the same source as isHandled), so every toggle
// recomputes it instead of leaving it stale until a reload (#86).
function toOrderSummary(orders) {
  const s = { total: 0, remaining: 0, ordered: 0, waiting: 0, instock: 0, unavail: 0 };
  for (const o of orders || []) {
    s.total += 1;
    if (ORDERED[o.key]) s.ordered += 1;
    if (WAITING[o.key]) s.waiting += 1;
    if (INSTOCK[o.key]) s.instock += 1;
    if (UNAVAIL[o.key]) s.unavail += 1;
    if (!isHandled(o)) s.remaining += 1;
  }
  return s;
}

const _SUM_PARTS = [['ordered', 'objednané'], ['waiting', 'čaká sa'],
                    ['instock', 'skladom'], ['unavail', 'nedostupné']];

// „📋 Ostáva vybaviť 5 položiek z 7 · objednané 1 · čaká sa 1". Empty buckets are dropped
// so the line stays readable. One line can carry SEVERAL flags, so the breakdown is not a
// partition of `total` — it is never rendered as one (no percentages, no "+" arithmetic).
// The noun follows the REMAINING count and is declined (itemsWord, akuzatív po „vybaviť").
// `label` = the selected supplier: with a chip on, the tally counts THAT supplier and says
// whose it is — a global „7 z 7" printed above two visible ORBIS rows is a number the
// manager cannot act on.
function toOrderSummaryText(s, label) {
  const bits = _SUM_PARTS.filter(([k]) => s[k] > 0).map(([k, lbl]) => `${lbl} ${s[k]}`);
  const head = label ? `📋 ${label}: ostáva vybaviť` : '📋 Ostáva vybaviť';
  return `${head} ${s.remaining} ${itemsWord(s.remaining, true)} z ${s.total}`
    + (bits.length ? ' · ' + bits.join(' · ') : '');
}

// #205 follow-up — the wording for the shared `#empty` box, or `null` for the neutral
// default. An empty list because the day is DONE is a success, not missing data: the box
// said „Žiadne produkty v tomto filtri." under a toolbar reading „ostáva 0 z 7", which
// reads like the orders failed to load.
// But „Všetko vybavené" is a claim about the WHOLE day, so it may only be printed while
// the manager is looking at the whole day: with a supplier chip selected the OTHER
// suppliers' lines are out of VIEW, not done — the shared box announced the day finished
// over five still-outstanding lines. A chip therefore gets the narrower wording, scoped to
// what he is actually looking at (the same rule the #208 toolbar tally follows).
// Every other case (no orders at all, the toggle off) keeps the neutral wording, and the
// review tab — which shares this box — gets the default back on its own paint.
function toOrderEmptyText(hidden, total, shown, supplier) {
  if (!(hidden && total > 0 && shown === 0)) return null;
  return supplier === 'all' ? 'Všetko vybavené — poriešené riadky sú skryté'
                            : 'Tento dodávateľ je vybavený — poriešené riadky sú skryté';
}

// The toolbar lives in the top bar, NOT in `#list` — a summary inside the list would be
// wiped by (and would have to be rebuilt through) the editor capture/restore repaint, and
// the per-line toggles deliberately do NOT repaint the list. Rendered from
// renderOrderFilters, which is exactly what every toggle already calls.
function renderOrderToolbar(canon) {
  const bar = document.getElementById('toToolbar');
  if (!bar || ACTIVE_TAB !== 'toorder') return;
  bar.innerHTML = '';
  // scoped to the SELECTED supplier chip — the manager reads the tally as „what is left
  // of what I am looking at". „Všetci" keeps the global text unchanged.
  const sel = ORDER_SUPPLIER !== 'all' ? ORDER_SUPPLIER : '';
  const label = sel ? ((canon && canon[sel]) || sel) : '';
  const s = toOrderSummary(sel ? ORDERS.filter(o => supFilterKey(o) === sel) : ORDERS);
  const sum = el('span', 'to-sum' + (s.total > 0 && s.remaining === 0 ? ' alldone' : ''));
  sum.textContent = toOrderSummaryText(s, label);   // .textContent → free-text name is safe
  bar.appendChild(sum);
  // #205 — hide the lines already dealt with. A VIEW filter only: nothing is written,
  // the chips keep counting every line, and the choice survives a reload.
  const hide = el('button', 'to-hidehandled' + (HIDE_HANDLED ? ' on' : ''),
                  HIDE_HANDLED ? '🙈 Poriešené skryté' : '👁 Skryť poriešené');
  hide.title = HIDE_HANDLED
    ? 'Zobraziť aj riadky, ktoré si už vybavil'
    : 'Skryť riadky, ktoré si už vybavil (objednané / čaká sa / skladom / nedostupné)';
  hide.onclick = () => {
    HIDE_HANDLED = !HIDE_HANDLED;
    localStorage.setItem('hideHandled', HIDE_HANDLED ? '1' : '0');
    renderToOrder();     // the ONE repaint path — carries open editors across (#233)
  };
  bar.appendChild(hide);
}

// #206 — rewrite the „Σ spolu" chips of the ALREADY RENDERED rows, in place.
// A per-row flag toggle deliberately does NOT repaint `#list` (#205/#233 — a row the
// manager is typing in must never vanish under him), so the chips would otherwise keep the
// quantity from the last full paint while „Kopírovať objednávku", narrowed at CLICK time,
// already pastes the smaller one: the screen said „Σ spolu 3 ks" (tooltip: „nevybavené:
// 3 ks") over a clipboard asking for 2 ks — two numbers for one supplier order, which is
// exactly what #206/#207 exist to remove. Same `outstandingOf` scope as both of them, and
// deliberately NOT a repaint: it only touches `.to-total`, so no open editor is disturbed.
// It only ever REWRITES a chip that is already on the row — it never adds or removes one.
// It cannot need to: whether a product gets a chip depends solely on how many order LINES
// of this supplier carry its code (`totalChipSpec` → `all.lines < 2`), which comes from
// the ORDERS grouping — and the only callers are the four per-row flag toggles, which
// change a flag map and nothing else. A change that DOES move a line between groups (a
// supplier re-assignment) repaints the whole tab, and `renderOrderRow` builds the chips
// there. So `spec` and the chip's presence always agree, and the guards below just skip.
function refreshOrderTotals() {
  const rows = document.querySelectorAll('#list .toorder-row');
  if (!rows.length) return;
  // null-prototype: the keys are supplier names / row keys straight out of the export
  const bySup = Object.create(null), byKey = Object.create(null), totals = Object.create(null);
  for (const o of ORDERS) {
    const s = supFilterKey(o);
    (bySup[s] = bySup[s] || []).push(o);
    byKey[o.key] = o;
  }
  for (const row of rows) {
    const o = byKey[row.dataset.key];
    if (!o) continue;                       // a row whose line is gone → next paint drops it
    const sum = row.querySelector('.to-total');
    if (!sum) continue;                     // single-line product → it never had a chip
    const s = supFilterKey(o);
    // the totals are per SUPPLIER, over the supplier's OWN lines (a sibling hidden by
    // #205 belongs to the same product) — computed once per supplier, not once per row
    const t = totals[s] || (totals[s] = { open: groupQtyTotals(outstandingOf(bySup[s])),
                                          all: groupQtyTotals(bySup[s]) });
    const spec = totalChipSpec(t, o.itemCode || '');
    if (!spec) continue;                    // …and a chip that is there is always still due
    sum.textContent = spec.text;
    sum.title = spec.title;
  }
}

// Build the supplier filter chips for the Na-objednanie tab, coloured by resolved state:
// RED (done) = every one of the supplier's lines is flagged (nothing left to deal with),
// GREEN (todo) = at least one line still un-flagged, ORANGE (active) = the selected chip.
// Called by renderToOrder AND by every per-line flag toggle so the chips recolour LIVE as
// the manager works the list (each toggle updates a flag map, then re-renders this bar).
function renderOrderFilters(idx) {
  const fbar = document.getElementById('filters');
  if (!fbar || ACTIVE_TAB !== 'toorder') return;
  const oNum = (o) => { const n = parseInt(o.orderCode, 10); return isNaN(n) ? -Infinity : n; };
  // keyed by the NORMALISED supplier (#203) so case variants share one chip/count/colour.
  // renderToOrder passes its own index in (one pass per paint instead of two); a bare
  // toggle-triggered call builds it itself.
  // `idx.repaint` = the caller is renderToOrder, which rebuilds every row right after
  const { canon } = idx || supplierSpellingIndex(ORDERS);
  const cnt = {}, newest = {}, unhandled = {};
  for (const o of ORDERS) {
    const s = supFilterKey(o);
    cnt[s] = (cnt[s] || 0) + 1;
    if (!isHandled(o)) unhandled[s] = (unhandled[s] || 0) + 1;
    newest[s] = Math.max(newest[s] ?? -Infinity, oNum(o));
  }
  const allHandledGlobal = ORDERS.length > 0 && ORDERS.every(isHandled);
  const lbl = (k) => canon[k] || k;   // sort/compare on the DISPLAYED spelling
  const byPriority = (a, b) => (newest[b] - newest[a])
    || (lbl(a) < lbl(b) ? -1 : lbl(a) > lbl(b) ? 1 : 0);
  fbar.innerHTML = '';
  const mk = (key, text, done) => {         // `text`, not `lbl` — would shadow lbl() above
    const cls = (ORDER_SUPPLIER === key ? 'active ' : '') + (done ? 'done' : 'todo');
    const b = el('button', cls, text);
    b.onclick = () => { ORDER_SUPPLIER = key; localStorage.setItem('orderSupplier', key); window.scrollTo(0, 0); render(); };
    return b;
  };
  fbar.appendChild(mk('all', `Všetci (${ORDERS.length})`, allHandledGlobal));
  // escapeHtml: a supplier name is manually assignable (free text) → never trust it in
  // the innerHTML-based el() helper. done (RED) = no un-flagged line left; todo (GREEN) = some.
  for (const s of Object.keys(cnt).sort(byPriority)) {
    fbar.appendChild(mk(s, `${escapeHtml(lbl(s))} (${cnt[s]})`, !unhandled[s]));
  }
  renderOrderToolbar(canon);   // #208 — the tally rides along with every chip repaint
  // …and so do the per-product „Σ spolu" chips (#206) — but ONLY on the toggle path. This
  // is the ONE call every flag toggle already makes, and those chips live in `#list`,
  // which a toggle must not repaint. renderToOrder calls us one line BEFORE
  // `list.innerHTML = ''`, so there the rows we would walk are already doomed and their
  // replacements get their chips from renderOrderRow — rewriting them first is a full
  // groupQtyTotals pass per supplier over nodes nobody will ever see.
  if (!(idx && idx.repaint)) refreshOrderTotals();
}

// A whole-tab repaint (a failed flag's rollback, a saved pair URL / supplier / comment)
// used to silently destroy whatever the manager had half-typed into ANY open inline
// editor on ANY row — and the message he got talked only about the flag, so the lost note
// was invisible. Since #204 even a SUCCESSFUL pair save repaints the whole tab, so this
// hits the happy path too. The three editors tag their wrapper with `data-editor`, which
// lets one generic pass carry the unsaved text across the repaint.
// null-prototype: the lookup key is a `data-editor` attribute read off the DOM, and no
// DOM-keyed map in this file may inherit `constructor` / `toString`
const _EDITORS = Object.assign(Object.create(null), {
  pair: {
    input: '.to-pairurl',
    stored: rowPairUrl,          // reviewed decision or inline pairing — whichever shows
    same: (a, b) => a.trim() === b.trim(),
    open: (o, row) => {
      // a reviewed link that is not usable renders as an inert .to-badlink, and that
      // row is exactly the one the manager needs to repair — take it too (#242)
      const a = row.querySelector('a.to-link, .to-badlink');
      const edit = row.querySelector('.to-pairedit');
      if (!a || !edit) return null;
      const ed = pairEditor(o, false); a.replaceWith(ed); edit.remove(); return ed;
    },
  },
  supplier: {
    input: '.to-supinput',
    stored: (o) => o.assignedSupplier || '',
    // the endpoint whitespace-normalises what it stores, so '  X   Y ' IS the saved value
    same: (a, b) => normSupplierName(a) === normSupplierName(b),
    open: (o, row) => {
      const tag = row.querySelector('.to-suptag'), edit = row.querySelector('.to-supedit');
      if (!tag || !edit) return null;
      const ed = supplierEditor(o, false); tag.replaceWith(ed); edit.remove(); return ed;
    },
  },
  comment: {
    input: '.to-cominput',
    stored: (o) => (o.orderCode && ORDER_COMMENTS[o.orderCode]) || '',
    same: (a, b) => a.trim() === b.trim(),
    open: (o, row) => {
      const add = row.querySelector('.to-comadd');
      if (add) { const ed = commentEditor(o, false); add.replaceWith(ed); return ed; }
      const tag = row.querySelector('.to-comment'), edit = row.querySelector('.to-comedit');
      if (!tag || !edit) return null;
      const ed = commentEditor(o, false); tag.replaceWith(ed); edit.remove(); return ed;
    },
  },
});

function captureOpenEditors() {
  const out = [];
  for (const w of document.querySelectorAll('#list [data-editor]')) {
    const spec = _EDITORS[w.dataset.editor];
    const row = w.closest('.toorder-row');
    const inp = spec && w.querySelector(spec.input);
    if (!row || !inp) continue;
    // the selection API throws on some input types; this runs BEFORE list.innerHTML='',
    // so an unguarded throw here would abort the repaint and skip the rollback with it
    let sel = [null, null];
    try { sel = [inp.selectionStart, inp.selectionEnd]; } catch (_) { /* no selection API */ }
    out.push({
      kind: w.dataset.editor, key: row.dataset.key, value: inp.value,
      opened: w.dataset.editorOpened === '1' && w.dataset.editorSaving !== '1',
      focused: document.activeElement === inp, sel,
    });
  }
  return out;
}

// Does this snapshot hold work the manager would LOSE if its editor disappeared? The
// same test — in the same load-bearing order — that decides whether an editor is put back
// after a repaint, so „stays open" (restoreOpenEditors) and „stays visible" (the #205
// hide filter) can never disagree about the same box.
function editorSnapHasWork(s, o) {
  const spec = _EDITORS[s.kind];
  if (!spec || !o) return false;
  // only UNSAVED TYPING counts. An EMPTY box holds none — and pair/supplier
  // editors are rendered empty BY DEFAULT on every unpaired/unassigned row, so treating
  // one as unsaved work would pin an empty input onto a sibling line that a
  // just-propagated per-product value (#204) has since paired. A value that now equals
  // what is stored was likewise just saved, and re-opening its editor would undo the
  // „it landed" feedback (the row would show an input instead of the new link / tag).
  // an EMPTY box normally holds no unsaved work — but one the manager OPENED with ✏️/💬
  // is his, whether he has typed into it yet or has deliberately cleared it. That
  // exception has to be weighed BEFORE „same as stored": on a row with nothing stored
  // the box and the stored value are BOTH '', so `same` fired first and closed the box
  // under a manager who was about to type into it. A NON-empty value equal to what is
  // stored is the just-saved case and still closes — re-opening it would replace the
  // freshly rendered link / tag with an input again.
  const emptyOpened = s.opened && !s.value.trim();
  if (!emptyOpened && spec.same(s.value, spec.stored(o))) return false;
  if (!s.value.trim() && !s.opened) return false;
  return true;
}

// #235 — a snapshot whose row is NOT in the rebuilt list (the manager switched the
// supplier chip, or the row jumped to another group) used to be dropped on the floor,
// which is the same silent loss the carry-over was built to remove — just narrower.
// Park it here instead, keyed per (editor, row), and hand it back on the first repaint
// that shows that row again. Bounded by rows x 3 editors, and self-clearing: a parked
// snapshot dies the moment its row is on screen and `editorSnapHasWork` says it is no
// longer unsaved work (saved meanwhile, or an empty box he never opened himself). No
// wipe on `loadOrders()` is needed for the same reason — the predicate is re-evaluated
// against the FRESH stored value on the next repaint.
// null-prototype: the key is built from a `data-editor` attribute + a store key, so this
// map must not inherit `constructor` / `toString` (same reason as `_EDITORS`).
const _pendingEditors = Object.create(null);
const _editorSnapKey = (s) => s.kind + '\u0000' + s.key;

function restoreOpenEditors(snaps) {
  // parked snapshots ride along with every restore pass. A LIVE snapshot for the same
  // (editor, row) WINS — it is the newer state of that very box, and re-adding the
  // parked one would put stale text back over what he is typing right now.
  const live = new Set(snaps.map(_editorSnapKey));
  const all = snaps.concat(Object.keys(_pendingEditors)
    .filter(k => !live.has(k))
    .map(k => _pendingEditors[k]));
  if (!all.length) return;
  const rows = [...document.querySelectorAll('#list .toorder-row')];
  for (const s of all) {
    const pk = _editorSnapKey(s);
    const spec = _EDITORS[s.kind];
    const row = rows.find(r => r.dataset.key === s.key);
    const o = row && ORDERS.find(x => x.key === s.key);
    if (!spec) { delete _pendingEditors[pk]; continue; }
    // the row is not on screen right now → PARK, never drop (#235)
    if (!o) { _pendingEditors[pk] = s; continue; }
    delete _pendingEditors[pk];
    // only UNSAVED TYPING is carried over — the whole rule (and why the two conditions
    // are in that order) lives in editorSnapHasWork above
    if (!editorSnapHasWork(s, o)) continue;
    let inp = row.querySelector(spec.input);
    let ed = null;
    if (!inp) { ed = spec.open(o, row); inp = ed && ed.querySelector(spec.input); }
    if (!inp) continue;
    if (s.opened && ed) ed.dataset.editorOpened = '1';   // stays „opened" across repaints
    inp.value = s.value;
    if (s.focused) {
      inp.focus();
      try { inp.setSelectionRange(s.sel[0], s.sel[1]); } catch (_) { /* no selection API */ }
    }
  }
}

function renderToOrder() {
  // `#list`/`#empty` are SHARED with the review tab, and this now runs from async
  // continuations (a failed save's rollback, a saved pair URL / supplier / comment) —
  // without this guard a late re-render would wipe the review cards, and any open
  // resolution panel with them, after the manager switched tabs. Same guard as
  // renderOrderFilters; switchTab → render() repaints the tab on return anyway.
  if (ACTIVE_TAB !== 'toorder') return;
  // Najnovšie objednávky hore — Marek je tak naučený zo Shoptetu. Čísla objednávok sú
  // chronologické (vyššie = novšie); dodávateľ s NAJNOVŠOU objednávkou hore, v rámci
  // dodávateľa od najnovšej. Ne-číselné orderCode = -Infinity (nikdy nedominuje vrch).
  const keepY = window.scrollY;    // list.innerHTML='' collapses the page → the browser
                                   // would clamp the scroll to 0 on every save/rollback
  const editors = captureOpenEditors();   // …and would eat any half-typed editor with it
  const oNum = (o) => { const n = parseInt(o.orderCode, 10); return isNaN(n) ? -Infinity : n; };
  // #203 — one entry per REAL supplier: grouping keys and the datalist of known supplier
  // names (which exists to avoid typo/case-fragmented groups) both come from the
  // case+whitespace-insensitive index, labelled with the manager's most-used spelling.
  const { canon, known } = supplierSpellingIndex(ORDERS);
  let dl = document.getElementById('known-suppliers');
  if (!dl) { dl = el('datalist'); dl.id = 'known-suppliers'; document.body.appendChild(dl); }
  dl.innerHTML = '';
  for (const s of known) { const opt = document.createElement('option'); opt.value = s; dl.appendChild(opt); }
  const newest = {};
  for (const o of ORDERS) {
    const s = supFilterKey(o);
    newest[s] = Math.max(newest[s] ?? -Infinity, oNum(o));
  }
  // dodávateľ s NAJNOVŠOU objednávkou hore; zhoda → abecedne (podľa zobrazenej menovky)
  const lbl = (k) => canon[k] || k;
  const byPriority = (a, b) => (newest[b] - newest[a])
    || (lbl(a) < lbl(b) ? -1 : lbl(a) > lbl(b) ? 1 : 0);
  // a selection carried over from another day (or migrated from the old raw-name scheme)
  // may match no supplier in today's orders → the manager would face an empty list with no
  // active chip. Fall back to „Všetci"; never on an empty ORDERS (a transient /api/orders
  // failure must not throw his filter away).
  if (ORDERS.length > 0 && ORDER_SUPPLIER !== 'all' && !canon[ORDER_SUPPLIER]) {
    ORDER_SUPPLIER = 'all';
    localStorage.setItem('orderSupplier', 'all');
  }
  // live-coloured chips (recomputed from the flag maps). `repaint: true` — the rows below
  // are rebuilt from scratch a line later, so the in-place „Σ spolu" refresh is skipped.
  renderOrderFilters({ canon, known, repaint: true });
  const list = document.getElementById('list'); list.innerHTML = '';
  // #205 — „skryť poriešené" hides handled lines, with ONE exemption: a row holding
  // unsaved editor work stays visible. Hiding it would strand the half-typed pair URL /
  // supplier / comment (restoreOpenEditors drops a snapshot whose row is gone), and the
  // manager would get no message about it — exactly the silent loss #214/#233 removed
  // from the flag writes. The exemption uses the SAME predicate restoreOpenEditors uses,
  // so „visible" and „restored" can never disagree.
  const busy = new Set(
    HIDE_HANDLED
      ? editors.filter(s => editorSnapHasWork(s, ORDERS.find(x => x.key === s.key)))
               .map(s => s.key)
      : []);
  const shown = ORDERS.filter(o => (ORDER_SUPPLIER === 'all' || supFilterKey(o) === ORDER_SUPPLIER)
    && !(HIDE_HANDLED && isHandled(o) && !busy.has(o.key)));
  document.getElementById('empty').hidden = shown.length > 0;
  setEmptyText(toOrderEmptyText(HIDE_HANDLED, ORDERS.length, shown.length, ORDER_SUPPLIER));
  const groups = Object.create(null);
  for (const o of shown) { const s = supFilterKey(o); (groups[s] = groups[s] || []).push(o); }
  // #206/#207 — the per-product totals and the copied order are computed over the
  // supplier's OWN lines, not over the rendered ones: a sibling hidden by „skryť
  // poriešené" (#205) belongs to the same product, because the manager orders the
  // PRODUCT, not the visible rows. `outstandingOf` then narrows both to the work LEFT.
  const linesBySup = Object.create(null);
  for (const o of ORDERS) {
    const s = supFilterKey(o);
    (linesBySup[s] = linesBySup[s] || []).push(o);
  }
  for (const sup of Object.keys(groups).sort(byPriority)) {
    const items = groups[sup];
    items.sort((a, b) => oNum(b) - oNum(a));   // v rámci dodávateľa: najnovšia objednávka prvá
    // header = escapovaná menovka (label FIRST → startsWith(sup) kontrakt) + hromadné
    // tlačidlo „označiť skupinu objednané". Ak je UŽ všetko objednané, tlačidlo prepína späť.
    const head = el('div', 'toorder-supplier');
    // #238/#240 — the count is declined like every other counter on the tab, through the
    // ONE `itemsWord` helper (nominative here: „CITRADE — 1 položka"; the accusative
    // „položku" belongs to the toolbar's „ostáva vybaviť", and the page subtitle wraps it
    // in `openItemsPhrase` because its adjective declines too).
    head.appendChild(el('span', 'tosup-label',
                        `${escapeHtml(lbl(sup))} — ${items.length} ${itemsWord(items.length)}`));
    const allOrdered = items.every(o => ORDERED[o.key]);
    const bulk = el('button', 'tosup-bulk', allOrdered ? '↺ Zrušiť objednané' : '✔ Označiť skupinu objednané');
    bulk.title = allOrdered ? 'Odznačiť „objednané" pre celú skupinu'
                            : 'Označiť VŠETKY položky tohto dodávateľa ako objednané';
    bulk.onclick = () => markGroupOrdered(items, !allOrdered);
    head.appendChild(bulk);
    // #207 — the whole supplier list as plain text, for the (many) suppliers with no B2B
    // ordering. It copies the supplier's OUTSTANDING lines — never what the list happens
    // to render: with „skryť poriešené" (#205) off, a row he already ticked „objednané"
    // is still on screen, and pasting it into a supplier e-mail re-orders goods that are
    // already on their way. Same set the „Σ spolu" chip counts, so the number he reads is
    // the number he sends, in either toggle state.
    const supLines = linesBySup[sup] || items;
    const copy = el('button', 'tosup-copy', TO_COPY_LABEL);
    copy.title = TO_COPY_TITLE;
    // Every outcome label goes back to the default after 2,5 s — and each click owns ITS
    // timer: `resetT` is per copy BUTTON (one closure per supplier group), and a new click
    // cancels the pending one. Fired and forgotten, the SECOND click inside the window
    // inherited the FIRST one's remaining time — a copy that failed 2,3 s after a copy
    // that worked showed „⚠️ Schránka nedostupná" for 200 ms before the stale timer wiped
    // it, and the manager pasted the PREVIOUS supplier's order, still in the clipboard,
    // into this supplier's mail. (The node may be gone when it fires — a repaint —
    // which is harmless: the next paint renders the default label anyway.)
    let resetT = 0;
    const resetCopy = () => {
      clearTimeout(resetT);
      resetT = setTimeout(() => {
        copy.textContent = TO_COPY_LABEL; copy.classList.remove('ok');
      }, 2500);
    };
    // narrowed at CLICK time, not at paint time: a per-row flag toggle deliberately does
    // not repaint the list, so a set frozen here would paste a line he ticked meanwhile.
    copy.onclick = async () => {
      const lines = outstandingOf(supLines);
      // With the #205 filter OFF (the default) a group whose every line is settled stays
      // on screen, copy button and all — and used to hand over a bare header. An
      // „Objednávka — ORBIS (0 položiek)" pasted into a supplier's inbox is an order for
      // nothing, reported as „✓ Skopírované". Refuse, say so, write nothing.
      if (!lines.length) {
        copy.textContent = TO_COPY_EMPTY_LABEL;
        copy.classList.remove('ok');
        resetCopy();
        return;
      }
      const ok = await copyPlainText(orderCopyText(lbl(sup), lines));
      copy.textContent = ok ? '✓ Skopírované' : '⚠️ Schránka nedostupná';
      copy.classList.toggle('ok', ok);
      resetCopy();
    };
    head.appendChild(copy);
    list.appendChild(head);
    // `open` = the work left (what the chip shows and the copy pastes), `all` = the whole
    // demand, which only ever appears in the chip's tooltip.
    const totals = { open: groupQtyTotals(outstandingOf(supLines)),
                     all: groupQtyTotals(supLines) };
    for (const o of items) list.appendChild(renderOrderRow(o, totals));
  }
  restoreOpenEditors(editors);   // put the manager's unsaved typing back where it was
  window.scrollTo(0, keepY);   // stay where the manager was working (same as renderCards)
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
  await loadAutomations();   // the tab header hosts the 3 výstavy automation controls
  try {
    VYSTAVY = (await (await fetch('/api/vystavy')).json()).vystavy || [];
  } catch (_) { VYSTAVY = []; }
}

// The 3 background chains (#111) — no nav tab of their own; the manager toggles/runs
// them from the „Poľovnícke výstavy" tab header (requirement „všetko v appke"). Keys
// mirror AUTOMATIONS_REG; NOT added to AUTOMATION_TABS (no extra sidebar tabs wanted).
const VY_AUTO_KEYS = ['vystavy_otazka', 'vystavy_odpoved_otazka', 'vystavy_odpoved_prihlaska'];

function vyAutoRow(key) {
  const row = el('div', 'vy-auto-row');
  row.dataset.testid = 'vy-auto-' + key;
  const a = autoByKey(key);
  if (!a) {
    row.appendChild(el('div', 'muted', 'Automatizácia nie je dostupná (server nevrátil stav).'));
    return row;
  }
  const info = el('div', 'vy-auto-info');
  const name = el('div', 'vy-auto-name'); name.textContent = a.name || key;   // XSS-safe
  info.appendChild(name);
  if (a.description) {
    const d = el('div', 'vy-auto-desc'); d.textContent = a.description;        // XSS-safe
    info.appendChild(d);
  }
  const bits = [];
  bits.push('Posledný beh: ' + (a.last_run
    ? `${fmtDt(a.last_run)} — ${a.last_status === 'ok' ? '✅ OK' : '❌ CHYBA'}` : 'zatiaľ nikdy'));
  if (a.enabled && a.next_run) bits.push('Ďalší beh: ' + fmtDt(a.next_run));
  const meta = el('div', 'vy-auto-meta'); meta.textContent = bits.join(' · ');
  info.appendChild(meta);
  row.appendChild(info);

  const ctrl = el('div', 'vy-auto-ctrl');
  const pill = el('span', 'pill ' + (a.enabled ? 'on' : 'off'), a.enabled ? 'Beží' : 'Zastavené');
  pill.dataset.testid = 'vy-auto-status-' + key;
  ctrl.appendChild(pill);
  if (a.running) ctrl.appendChild(el('span', 'runningdot', '⏳'));
  const tgl = el('button', 'btn sm ' + (a.enabled ? 'warn' : 'good'), a.enabled ? '⏹ Stop' : '▶ Štart');
  tgl.dataset.testid = 'vy-auto-toggle-' + key;
  tgl.onclick = () => toggleAutomation(key, !a.enabled);
  ctrl.appendChild(tgl);
  const run = el('button', 'btn sm ghost', '⚡ Spustiť teraz');
  run.dataset.testid = 'vy-auto-run-' + key;
  run.disabled = !!a.running;
  run.onclick = () => runAutomation(key, 'vystavy');
  ctrl.appendChild(run);
  row.appendChild(ctrl);
  return row;
}

// Compact „Automatické spracovanie" panel for the Výstavy tab header — the 3 chains'
// Štart/Stop + „Spustiť teraz", so the manager controls everything from this one tab.
function vyAutoPanel() {
  const panel = el('div', 'vy-autopanel');
  panel.dataset.testid = 'vy-autopanel';
  panel.appendChild(el('div', 'vy-autopanel-h', '⚙️ Automatické spracovanie'));
  const hint = el('div', 'vy-autopanel-hint');
  hint.textContent = 'Tri automatické kroky pre výstavy — zapni/vypni ich alebo ručne spusti priamo tu.';
  panel.appendChild(hint);
  for (const key of VY_AUTO_KEYS) panel.appendChild(vyAutoRow(key));
  return panel;
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
  sec.appendChild(vyAutoPanel());          // automation controls live in this tab header
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
  row.dataset.num = it.number;      // lets a save re-open the detail on the rebuilt row
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
  const hasBody = !!(data.body && data.body.trim());
  // #243 — an already-sent request must stay CORRECTABLE. Without this the boss could
  // only append a comment, so a request that came out wrong was abandoned instead
  // („chcel som mu ešte niečo dopísať – nedá sa, tak som sa na to vykašlal").
  // The header (and with it the ✏️) is rendered even when the body is EMPTY: an issue
  // created straight on GitHub with no description could otherwise never have its
  // TITLE corrected in the app at all — and the title is the whole request there.
  if (hasBody || canAdd) {
    const h = el('div', 'dev-detail-h', 'Zadanie');
    if (canAdd) {
      const ed = el('button', 'dev-edit-btn', '✏️ Upraviť zadanie');
      ed.title = 'Opraviť alebo doplniť text tejto požiadavky';
      ed.onclick = () => _devEditForm(box, number, data, canAdd);
      h.appendChild(ed);
    }
    box.appendChild(h);
    const b = el('div', 'dev-detail-body');
    // an empty request has no text to show — say so instead of an unexplained blank
    if (hasBody) b.textContent = data.body;    // textContent → no XSS, no HTML render
    else { b.className = 'dev-detail-body dev-detail-empty'; b.textContent = 'Bez textu — zatiaľ len názov.'; }
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

// #243 — the edit form for an already-submitted request: name + text, prefilled with
// what is actually there. `data.editable` is the body WITHOUT the app's own signature
// lines, so the boss neither sees nor can accidentally delete bookkeeping he did not
// write (the server puts them back on save).
function _devEditForm(box, number, data, canAdd) {
  box.innerHTML = '';
  box.appendChild(el('div', 'dev-detail-h', 'Upraviť požiadavku'));
  const ti = el('input', 'dev-edit-title'); ti.type = 'text'; ti.maxLength = 200;
  ti.value = data.title || '';
  ti.placeholder = 'Názov požiadavky…';
  const ta = el('textarea', 'dev-edit-ta'); ta.maxLength = 5000;
  ta.value = data.editable || '';
  ta.placeholder = 'Popis požiadavky…';
  const bar = el('div', 'dev-note-bar');
  const save = el('button', 'dev-note-save', 'Uložiť zmeny');
  const cancel = el('button', 'dev-edit-cancel', 'Zrušiť');
  const msg = el('span', 'dev-note-msg', '');
  cancel.onclick = () => _renderDetail(box, number, data, canAdd);
  save.onclick = async () => {
    const title = ti.value.trim();
    if (!title) {
      msg.textContent = 'Názov nemôže byť prázdny.'; msg.className = 'dev-note-msg err';
      ti.focus(); return;
    }
    save.disabled = true; cancel.disabled = true;
    msg.textContent = 'Ukladám…'; msg.className = 'dev-note-msg';
    let ok = false, err = '';
    try {
      const r = await fetch(`/api/dev/issue/${number}/edit`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, text: ta.value }),
      });
      const j = await r.json(); ok = j.ok; err = j.error || '';
    } catch (_) { err = 'sieť'; }
    if (!ok) {
      save.disabled = false; cancel.disabled = false;
      msg.textContent = 'Nepodarilo sa: ' + err; msg.className = 'dev-note-msg err';
      return;                                  // his text stays in the form, not lost
    }
    // re-read so the box shows the STORED text (the server re-attached its markers),
    // and refresh the list so the corrected NAME shows there too
    let fresh = null;
    try { fresh = await (await fetch(`/api/dev/issue/${number}`)).json(); } catch (_) {}
    await loadDevIssues();
    if (ACTIVE_TAB === 'dev') renderDev(); else renderTabs();
    // renderDev() rebuilds the list, so `box` is now detached — re-open the detail on
    // the NEW row, otherwise the boss's edit would appear to close his own detail
    const row = document.querySelector(`.dev-row[data-num="${number}"]`);
    if (row && fresh && fresh.ok) {
      const nb = el('div', 'dev-detail-box');
      row.appendChild(nb);
      _renderDetail(nb, number, fresh, canAdd);
    }
  };
  bar.appendChild(save); bar.appendChild(cancel); bar.appendChild(msg);
  box.appendChild(ti); box.appendChild(ta); box.appendChild(bar);
  ti.focus();
}

// Idea lightbulb — any logged-in user writes an idea → POST /api/dev/idea creates a
// GitHub issue that then appears in the Vývoj list. The token stays server-side.
function _ideaMsg(text, cls) {
  const msg = document.getElementById('ideaMsg');
  if (!msg) return;
  if (!text) { msg.hidden = true; msg.textContent = ''; return; }
  msg.hidden = false; msg.className = 'idea-msg' + (cls ? ' ' + cls : ''); msg.textContent = text;
}
// One submission at a time, guarded on a FLAG rather than on `btn.disabled`. The button
// property only stops a second CLICK dispatch, and the title input's keydown-Enter calls
// _ideaSubmit() DIRECTLY — so with the click-only guard, Enter+Enter sent two POSTs and
// created two GitHub issues. Enter on a one-line title is the boss's primary submit
// affordance, so that was the live path. The flag sits inside the function every entry
// point goes through, and is cleared only on the error path (retry is fine) and in
// _ideaOpen — the success path stays locked exactly like the button.
let _ideaBusy = false;

function _ideaOpen() {
  const m = document.getElementById('ideaModal'); if (!m) return;
  _ideaBusy = false;
  document.getElementById('ideaTitleInput').value = '';
  document.getElementById('ideaDescInput').value = '';
  // back to the form — the previous open may have ended on the confirmation panel
  const form = document.getElementById('ideaForm'), done = document.getElementById('ideaDone');
  if (form && done) { form.hidden = false; done.hidden = true; }
  // the submit button stays disabled from the moment a submission is accepted until the
  // dialog is opened again — this is the one place it comes back
  const sb = document.getElementById('ideaSubmit');
  if (sb) sb.disabled = false;
  _ideaMsg('');
  m.hidden = false;
  document.getElementById('ideaTitleInput').focus();
}
function _ideaClose() {
  const m = document.getElementById('ideaModal'); if (m) m.hidden = true;
}
async function _ideaSubmit() {
  if (_ideaBusy) return;                 // click OR Enter — both come through here
  const ti = document.getElementById('ideaTitleInput');
  const de = document.getElementById('ideaDescInput');
  const btn = document.getElementById('ideaSubmit');
  const title = ti.value.trim();
  if (!title) { _ideaMsg('Napíš aspoň názov nápadu.', 'err'); ti.focus(); return; }
  _ideaBusy = true;
  btn.disabled = true;
  let ok = false, err = '', number = 0;
  try {
    const r = await fetch('/api/dev/idea', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, description: de.value.trim() }),
    });
    const j = await r.json().catch(() => ({}));
    ok = r.ok && j.ok;
    number = (j.issue && j.issue.number) || 0;
    if (!ok) err = j.error || ('chyba ' + r.status);
  } catch (_) { err = 'sieťová chyba'; }
  if (!ok) { _ideaBusy = false; btn.disabled = false; _ideaMsg('Nepodarilo sa: ' + err, 'err'); return; }
  // #243 — the dialog used to just vanish. The lightbulb is on EVERY tab, so unless he
  // happened to be standing in „Vývoj" he got no number, no confirmation and no way
  // back to what he had just sent. Say it landed, and offer to open it right away.
  //
  // Confirm FIRST, refresh the list AFTER — and never re-enable the button here. It used
  // to be re-enabled and the form left standing across `await loadDevIssues()`, so a
  // second click inside that window (6 s with a slow /api/dev/issues) sent a SECOND POST
  // and created a SECOND GitHub issue. It also made the new E2E flaky: 2 of 3 full runs
  // failed waiting for #ideaDone, because the confirmation only appeared after the list
  // round-trip. The button comes back when the dialog is opened again (_ideaOpen).
  _ideaDone(number);
  await loadDevIssues();                       // new issue appears + nav badge updates
  if (ACTIVE_TAB === 'dev') render(); else renderTabs();
}

// Success state of the idea dialog: confirmation + a way straight into the request.
// A number is required — without it there is nothing to confirm or open, so fall back
// to the old silent close rather than promise something the panel cannot deliver.
function _ideaDone(number) {
  const form = document.getElementById('ideaForm');
  const done = document.getElementById('ideaDone');
  const txt = document.getElementById('ideaDoneText');
  if (!form || !done || !number) { _ideaClose(); return; }
  txt.textContent = 'Zapísali sme ju ako úlohu č. ' + number
    + ' — nájdeš ju v záložke „Vývoj". Môžeš ju tam kedykoľvek doplniť alebo opraviť.';
  const open = document.getElementById('ideaDoneOpen');
  open.onclick = async () => {
    _ideaClose();
    await switchTab('dev');
    const row = document.querySelector(`.dev-row[data-num="${number}"]`);
    if (row) {
      row.scrollIntoView({ block: 'center' });
      _devToggleDetail(row, number, true);
    }
  };
  form.hidden = true; done.hidden = false;
}
function initIdea() {
  const btn = document.getElementById('ideaBtn'); if (btn) btn.onclick = _ideaOpen;
  const cancel = document.getElementById('ideaCancel'); if (cancel) cancel.onclick = _ideaClose;
  const back = document.getElementById('ideaBackdrop'); if (back) back.onclick = _ideaClose;
  const submit = document.getElementById('ideaSubmit'); if (submit) submit.onclick = _ideaSubmit;
  const dclose = document.getElementById('ideaDoneClose'); if (dclose) dclose.onclick = _ideaClose;
  const ti = document.getElementById('ideaTitleInput');
  if (ti) ti.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); _ideaSubmit(); }
    else if (e.key === 'Escape') _ideaClose();
  });
}

// ---- Automatizácie (#93): tab „Nevyzdvihnuté zásielky" -------------------- //
// ---- + „Sync zo Shoptetu" (#119, plain status-only tab, no per-item table) - //
async function loadAutomations() {
  try {
    const r = await fetch('/api/automations');
    const j = await r.json().catch(() => ({}));
    if (!r.ok) {
      // The server FAILS CLOSED over an unreadable automations.json (503 + a repair
      // message). Reading that answer with `j.automations || []` / `j.scheduler ||
      // 'running'` rendered the clean first-run state the guard exists to prevent —
      // „nič nie je nastavené, plánovač je zdravý" while every pripomienka, pošta a
      // hodinová synchronizácia bola vypnutá (revízia PR #265, I3). The global fetch
      // wrapper only handles 401, so nothing else was going to catch this.
      AUTOMATIONS = []; SCHEDULER = 'corrupt'; SCHED_ERROR = j.error || '';
      return;
    }
    AUTOMATIONS = j.automations || [];
    SCHEDULER = j.scheduler || 'running';
    SCHED_ERROR = '';
  } catch (_) {
    // Same reasoning: a failed request means we do NOT know the state, and pretending
    // „running" is the silent switch-off. No server text to show here.
    AUTOMATIONS = []; SCHEDULER = 'corrupt'; SCHED_ERROR = '';
  }
}

// Banner nad obsahom: automatizácie v tejto inštancii nebežia na časovač.
function renderSchedulerWarning(onAutomationTab) {
  const box = document.getElementById('schedWarn');
  if (!box) return;
  if (!onAutomationTab || SCHEDULER === 'running') { box.hidden = true; box.textContent = ''; return; }
  const SCHED_MSG = {
    // fail-closed: nevieme, čo je nastavené, tak to NEHRÁME na zdravý prvý štart
    'corrupt': '⚠ ' + (SCHED_ERROR || 'Stav automatizácií sa nedá načítať zo servera — '
      + 'nevieme, ktoré automatizácie sú zapnuté, takže sa možno nespustia. Obnov '
      + 'stránku; ak to trvá, pozri log služby parovanie-web.'),
    'blocked': '⚠ Plánovač v tejto inštancii nebeží — drží ho iná spustená inštancia aplikácie. '
      + 'Automatizácie sa tu samy nespustia (ručné „⚡ Spustiť teraz" funguje). '
      + 'Časy „Ďalší beh" nižšie sú z uloženého stavu, nie prísľub.',
    // spadol počas behu — naštartoval sa, ale vlákno plánovača už nebeží
    'dead': '⚠ Plánovač spadol — naštartoval sa, ale už nebeží, takže automatizácie sa '
      + 'samy nespustia (ručné „⚡ Spustiť teraz" funguje). Časy „Ďalší beh" nižšie sú '
      + 'z uloženého stavu, nie prísľub. Reštartuj službu parovanie-web.',
    'off': '⚠ Plánovač je v tejto inštancii vypnutý — automatizácie sa nespustia samé '
      + '(ručné „⚡ Spustiť teraz" funguje). Toto je náhľadová/testovacia inštancia.',
  };
  box.textContent = SCHED_MSG[SCHEDULER] || SCHED_MSG['off'];
  box.hidden = false;
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

async function loadShoptetSync() {
  await loadAutomations();
  // #209 — the status configuration is edited on this card, so it is loaded with it (and
  // re-read on every visit: another admin may have changed it meanwhile).
  try { ORDER_STATUSES = await (await fetch('/api/order-statuses')).json(); }
  catch (_) { ORDER_STATUSES = null; }
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
  if (tab === 'vystavy') { await loadVystavy(); return; }   // reloads výstavy + AUTOMATIONS
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

// „BCC vždy" is BINDING for the two CUSTOMER automations (Pošta escalation + order reminders):
// with no MAIL_BCC configured they refuse to send anything at all. That used to be an ERROR line
// in the log only, so the tab showed a healthy run that had quietly mailed nobody — a silently
// dead automation. The run now reports `bcc_missing` and both tabs surface it.
function bccMissingWarning() {
  return el('div', 'autoerr', '⚠️ Chýba MAIL_BCC v data/.mail_env — automatizácia NEPOSIELA '
    + 'žiadne e-maily zákazníkom, kým sa nedoplní konfigurácia.');
}

// PR #295 review — the loader answers an unusable `order_statuses.json` with the built-in
// DEFAULTS, which is right for rendering a tab and wrong for anything that ACTS: the customer
// reminders would go out on the default „Vybavuje sa" — i.e. to exactly the people the manager
// may have excluded by narrowing that set. The run now refuses, and this is what says so.
function badStatusConfigWarning() {
  return el('div', 'autoerr', '⛔ Nastavenie stavov objednávok (data/out/order_statuses.json) '
    + 'sa nedá prečítať, takže appka dočasne používa predvolené stavy — pripomienkové e-maily '
    + 'sa preto NEPOSIELAJÚ, aby ich nedostal niekto, koho si zo zoznamu vyradil. Oprav to na '
    + 'karte „Stavy objednávok" (záložka Automatizácie).');
}

// #282 — one step upstream of bcc_missing: the automation could still send, but it has nothing to
// send ABOUT. Its only source of shipments is the export's „podacie číslo" column; that column
// stopped being filled on 2.7. and every run since ended a healthy-looking `ok` with a shrinking
// „Skontrolovaných zásielok" (21 → 13 → 9 → 6 → 4), the tab reading „0 nevyzdvihnutých" while a
// parcel sat at the post office until its deadline. Numbers, not adjectives: the manager has to
// see HOW blind it is, and what to go and check.
function sourceDegradedWarning(lr) {
  const gap = lr.days_since_last_package;
  // „pred" governs the instrumental in Slovak — „pred 26 dňami", never „pred 26 dní". dniLabel()
  // gives the nominative/genitive forms used by the bare-count sites ("5 dní v stave"), so it is
  // deliberately NOT reused here. The wording also says what the number actually measures: the
  // age of the newest ORDER carrying a number, which is not quite „when a number last arrived".
  const since = (gap === null || gap === undefined)
    ? 'a v celom 30-dňovom okne nie je ani jedna'
    : (gap === 0 ? '' : `najnovšia objednávka s číslom je spred ${Number(gap)} `
        + `${gap === 1 ? 'dňa' : 'dní'}`);
  return el('div', 'autoerr',
    `⛔ Automatizácia nevidí takmer žiadne zásielky — ${Number(lr.dispatched_without_package ?? 0)} `
    + `z ${Number(lr.dispatched_orders ?? 0)} odoslaných objednávok nemá podacie číslo`
    + (since ? ` (${since})` : '')
    + '. Kým sa čísla nezačnú zapisovať, nikoho neupozorní na nevyzdvihnutú zásielku — '
    + 'skontroluj, či sa podacie čísla ešte dostávajú do Shoptetu.');
}

// #225 — the evidence of who was already mailed is unreadable, so the automation refuses to send
// anything. Without this banner the tab would render as a clean, empty day: the manager would see
// „0 objednávok" and never learn the automation had stopped (same class of silent death the
// bcc_missing warning exists for). NEVER tell them to delete the file — an empty store means
// every customer gets mailed again.
function storeCorruptWarning(file) {
  return el('div', 'autoerr', '⛔ Poškodená evidencia odoslaných e-mailov (data/out/'
    + escapeHtml(file) + ') — automatizácia NEPOSIELA nič, aby zákazníci nedostali maily '
    + 'druhýkrát. Zoznam nižšie je preto prázdny. Súbor treba opraviť podľa zálohy '
    + '<code>.corrupt-*</code> v tom istom priečinku (NEMAZAŤ ho).');
}

// ── #217 — read-only e-mail preview shared by both customer-mail automations ────────────
// The manager could not see what a customer gets until the automation had already sent it.
// This dialog is inert BY CONSTRUCTION: the endpoints behind it write nothing (no claim, no
// state, no SMTP) and the dialog has no „Odoslať" button at all, so looking can never turn
// into sending by accident. Sending stays on its own confirmed path (the row's ▶ button).
function _emModalEls() {
  return {
    modal: document.getElementById('emModal'),
    head: document.getElementById('emHead'),
    hint: document.getElementById('emHint'),
    rec: document.getElementById('emRecipient'),
    frame: document.getElementById('emPreview'),
  };
}

function closeEmModal() {
  const m = document.getElementById('emModal');
  if (m) m.hidden = true;
}

// Two quick clicks used to be able to show customer A's e-mail under order B's heading: the
// heading is set synchronously per click, the body by whichever fetch resolves last. On a screen
// whose whole point is „see exactly what THIS customer gets", a mis-paired header/body is the one
// failure worth guarding — so a superseded response is dropped.
let _emSeq = 0;

async function openEmailPreview(url, payload, head) {
  const E = _emModalEls();
  if (!E.modal) return;
  const my = ++_emSeq;
  E.head.textContent = head;
  E.hint.textContent = 'Presne toto dostane zákazník. Nič sa teraz neodosiela.';
  E.rec.innerHTML = 'Načítavam…';
  E.frame.srcdoc = '';
  E.modal.hidden = false;
  let j;
  try {
    j = await (await fetch(url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })).json();
  } catch (_) { j = null; }
  if (my !== _emSeq) return;              // a later click already owns the dialog
  if (!j || !j.ok) {
    E.rec.textContent = 'Náhľad sa nepodarilo načítať'
      + (j && j.error ? ': ' + j.error : '.');
    return;
  }
  E.rec.innerHTML = '<div class="nd-rec-head">Príjemca:</div><ul><li>'
    + `${escapeHtml(j.name || '—')} · `
    + `<span class="nd-em">${escapeHtml(j.recipient || 'bez e-mailu')}</span></li></ul>`
    + `<div class="nd-subj">Predmet: <strong>${escapeHtml(j.subject || '')}</strong></div>`
    + (j.max_reached
      ? '<div class="nd-subj">⚠️ Zákazník už dostal maximálny počet upozornení — automat '
        + 'ďalšie neposiela. Toto je posledné, ktoré odišlo.</div>'
      : '');
  E.frame.srcdoc = j.html || '';
}

function initEmModal() {
  const bd = document.getElementById('emBackdrop');
  const close = document.getElementById('emClose');
  if (bd) bd.onclick = closeEmModal;
  if (close) close.onclick = closeEmModal;
}

// One „👁 Náhľad" button, wired to the read-only preview endpoint. Never disabled by a send —
// it is safe to click at any time, on any row.
function _previewBtn(testid, url, payload, head) {
  const b = el('button', 'btn sm ghost act-preview', '👁 Náhľad');
  b.dataset.testid = testid;
  b.onclick = () => openEmailPreview(url, payload, head);
  return b;
}

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

    const lr = a.last_result || {};
    // #282 — a run whose source went dry must NOT read „✅ OK". It did not crash, so `last_status`
    // is legitimately 'ok'; what failed is the input, and that is exactly the state five days of
    // green runs hid while `checked` slid 21 → 4.
    const degraded = !!lr.source_degraded;
    const meta = el('div', 'autometa');
    const bits = [`Plán: ${escapeHtml(a.schedule || '')}`];
    bits.push('Posledný beh: ' + lastRunLabel(a, degraded));
    if (a.enabled && a.next_run) bits.push('Ďalší beh: ' + fmtDt(a.next_run));
    meta.innerHTML = bits.map(b => `<span>${b}</span>`).join(' · ');
    st.appendChild(meta);
    if (a.last_status === 'error' && a.last_error) {
      st.appendChild(el('div', 'autoerr', '❌ ' + escapeHtml(a.last_error)));
    }
    if (a.last_run && a.last_status === 'ok') {
      st.appendChild(el('div', 'muted',
        `Skontrolovaných zásielok: ${lr.checked ?? 0} · nevyzdvihnuté: ${lr.uncollected ?? 0}`
        + ` · odoslané e-maily: ${lr.emails_sent ?? 0}`
        + (lr.invalid ? ` · nesledovateľné: ${lr.invalid}` : '')
        + (lr.api_skipped ? ` · už doručené/vrátené (nekontrolujú sa): ${lr.api_skipped}` : '')
        + (lr.errors ? ` · chyby pri kontrole: ${lr.errors}` : '')));
    }
    if (a.last_run && degraded) st.appendChild(sourceDegradedWarning(lr));
    if (a.last_run && lr.bcc_missing) st.appendChild(bccMissingWarning());
  }
  wrap.appendChild(st);

  const p = POSTA || {};
  if (p.store_corrupt) wrap.appendChild(storeCorruptWarning('posta_uncollected.json'));
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
      + '<th>Na pošte</th><th>Vyzdvihnúť do</th><th>E-maily</th><th>Náhľad</th></tr></thead>';
    const tb = el('tbody');
    for (const u of unc) {
      const tr = el('tr', u.call_needed ? 'callneeded' : '');
      tr.dataset.pkg = u.packageNumber || '';
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
      // #217 — see the escalation mail BEFORE the automation sends it (read-only, no SMTP)
      const actTd = el('td', 'ordrem-actions');
      actTd.appendChild(_previewBtn(
        `posta-preview-${u.packageNumber}`, '/api/posta-uncollected/preview',
        { package: u.packageNumber }, `Náhľad e-mailu — zásielka ${u.packageNumber}`));
      tr.appendChild(actTd);
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

// #293 — the prune refused, and the refusal is PERMANENT until someone fixes the export.
// Every reason gets its OWN sentence AND its own place to go and look, because they are four
// different faults; and every one carries the number it fired on — „your export is wrong"
// with no number leaves him nothing to check (`.claude/rules/automation-health.md` §3,
// store-prune §7).
function flagPruneBlockedWarning(lr) {
  // a refusal recorded BEFORE this field existed carries no counts. Say nothing rather than
  // coerce the absent value to 0 and state „export nesie 0 objednávok" as THE number the
  // refusal fired on — a confident wrong fact until the next hourly run.
  const seen = lr.flags_orders_seen == null ? null : Number(lr.flags_orders_seen);
  const count = seen == null ? '' : ` (${seen} objednávok)`;
  const reason = lr.flags_prune_skipped;
  const CASES = {
    'no-open-orders': {
      // #209 — name the statuses the run was ACTUALLY looking for, not a hard-coded
      // „Vybavuje sa": after a rename that sends him looking for exactly the wrong thing.
      // An older recorded refusal has no such field → fall back to the built-in default.
      why: `v exporte${count} nie je ani jedna objednávka v stave `
           + escapeHtml((lr.flags_open_statuses?.length
             ? lr.flags_open_statuses : ['Vybavuje sa'])
             .map(s => `„${s}"`).join(' / ')),
      look: 'skontroluj v Shoptete názvy stavov objednávok (asi sa niektorý premenoval) '
            + 'a nižšie na tejto karte ich zaraď do správnej skupiny',
    },
    'no-status-column': {
      why: `export${count} vôbec nemá stĺpec so stavom objednávky`,
      look: 'skontroluj v Shoptete šablónu exportu objednávok — chýba v nej stĺpec so '
            + 'stavom, alebo je v nastaveniach prehodená adresa exportu',
    },
    'unparsable-source': {
      why: 'stiahnutý export sa nedá prečítať — namiesto tabuľky prišlo niečo iné',
      look: 'skús export objednávok stiahnuť ručne zo Shoptetu a pozri sa, čo príde '
            + '(chybová stránka, prihlásenie, prázdny súbor)',
    },
    'bad-status-config': {
      // #209 — the manager's own classification is unreadable, so the run refuses to
      // delete rather than fall back to the built-in list and delete on statuses he may
      // have removed on purpose. The fix is the panel right below.
      why: 'nastavenie stavov objednávok sa nedá použiť (prázdny alebo protirečivý zoznam)',
      look: 'skontroluj nižšie na tejto karte zoznamy stavov — jeden stav nesmie byť '
            + 'vo viacerých zoznamoch a prvé dva nesmú byť prázdne',
    },
    'implausible-source': {
      why: `export${count} nesie príliš málo objednávok na to, aby bol úplný`,
      look: 'sťahovanie pravdepodobne skončilo v polovici — skontroluj pripojenie a '
            + 'skús export stiahnuť ručne',
    },
  };
  // an unexpected reason (the housekeeping try/except passes the exception repr through)
  // must still reach him — rendering nothing is the exact failure this banner fixes. It is
  // the one dynamic value here that is not a fixed literal, so it is ESCAPED.
  const c = CASES[reason] || {
    why: 'neočakávaná chyba: ' + escapeHtml(String(reason)),
    look: 'pozri sa do logu služby, čo presne zlyhalo',
  };
  return el('div', 'autoerr',
    `⛔ Upratovanie starých značiek pri riadkoch objednávok je zastavené: ${c.why}. `
    + 'Značky „objednané u dodávateľa" / „čaká sa" / „skladom" / „nedostupné" sa zatiaľ '
    + `nemažú, takže ich bude stále pribúdať. Nič sa nestratilo — ${c.look}.`);
}

// „Posledný beh: <čas> — <verdict>". DEGRADED is its own verdict, not a flavour of OK: the
// run did not throw, but a part of it could not see its own input (#282 Pošta, #293 sync).
// Shared so a third automation cannot quietly invent a fourth spelling of it.
function lastRunLabel(a, degraded) {
  if (!a.last_run) return 'zatiaľ nikdy';
  const verdict = a.last_status !== 'ok' ? '❌ CHYBA'
    : (degraded ? '⚠️ DEGRADOVANÝ' : '✅ OK');
  return `${fmtDt(a.last_run)} — ${verdict}`;
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
    // the status configuration is a SETTING, not part of the automation card — a failed
    // /api/automations must not take away the one screen that fixes a renamed status
    wrap.appendChild(renderOrderStatusConfig());
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

  const lr = a.last_result || {};
  // #293 — a run whose prune REFUSED must not read „✅ OK". Nothing crashed (orders,
  // catalogue and review all landed), so `last_status` is legitimately 'ok'; what failed is
  // the one part of this automation that DELETES data, and its refusal reasons are
  // PERMANENT — until the export is fixed the prune never runs once and the flag stores grow
  // exactly as they did before #212. Same flag and same wording as Pošta (#282), so the
  // sidebar ⚠ (navError) lights from it with no second predicate to keep in sync.
  const degraded = !!lr.source_degraded;
  const meta = el('div', 'autometa');
  const bits = [`Plán: ${escapeHtml(a.schedule || '')}`];
  bits.push('Posledný beh: ' + lastRunLabel(a, degraded));
  if (a.enabled && a.next_run) bits.push('Ďalší beh: ' + fmtDt(a.next_run));
  meta.innerHTML = bits.map(b => `<span>${b}</span>`).join(' · ');
  st.appendChild(meta);
  if (a.last_status === 'error' && a.last_error) {
    st.appendChild(el('div', 'autoerr', '❌ ' + escapeHtml(a.last_error)));
  }
  if (a.last_run && a.last_status === 'ok') {
    st.appendChild(el('div', 'muted',
      `Objednávky: ${(lr.orders_bytes || 0).toLocaleString('sk-SK')} B stiahnuté`
      + ` · katalóg: ${lr.catalog_products ?? 0} produktov (${lr.catalog_codes ?? 0} kódov)`
      + ` · zosynchronizované review karty: ${lr.review_synced ?? 0}`
      + (lr.review_stale ? ` (nenájdených v exporte: ${lr.review_stale})` : '')
      // #212/#293 — the prune is the ONE thing here that removes the manager's markings, so
      // its count belongs in front of him, not only in the log. Reported even when it is 0:
      // „0" is the normal, reassuring answer, and it was the ABSENCE of this line that let a
      // permanently refused prune look identical to a healthy hour.
      + (lr.flags_prune_skipped ? ''
         : ` · vyčistené osirelé značky: ${Number(lr.flags_pruned ?? 0)}`)));
    // an added or renamed status silently narrows what the prune considers finished — the
    // honest cost of the terminal-status allow-list. Informational, NOT a warning: these
    // statuses are legitimate and permanent, so a banner here would be noise for ever.
    const unknown = lr.flags_unknown_statuses || [];
    if (unknown.length) {
      st.appendChild(el('div', 'muted',
        'Stavy objednávok, ktoré nepoznám, a preto ich nepovažujem za vybavené (značky '
        + 'pri nich ostávajú): <b>' + escapeHtml(unknown.join(', ')) + '</b>'
        // #209 — the whole point of naming them: the box that fixes it is right below.
        + '. Zaraď ich nižšie do správnej skupiny.'));
    }
    if (lr.flags_prune_skipped) st.appendChild(flagPruneBlockedWarning(lr));
    // #280 review — a NON-FATAL degradation has to be VISIBLE. Both of these leave
    // last_status = ok on purpose (the critical refresh did land), so without a line
    // here a degraded hour reads exactly like a healthy one: the „quietly dead
    // automation" the playbook warns about. Own class, not `.autoerr` — the run did
    // not fail, one source of it did.
    if (lr.export_error) {
      st.appendChild(el('div', 'autowarn',
        '⚠️ Katalógový export sa neobnovil — pracujem s predošlým súborom na disku: '
        + escapeHtml(lr.export_error)));
    }
    if (lr.customers_error) {
      st.appendChild(el('div', 'autowarn',
        '⚠️ Export zákazníkov sa neobnovil: ' + escapeHtml(lr.customers_error)));
    }
  }
  wrap.appendChild(st);
  wrap.appendChild(renderOrderStatusConfig());
}

// ---- #209: which order statuses mean WHAT ---------------------------------- //
// It lives on THIS card and nowhere else on purpose: this is where the manager is told
// that a status he does not recognise appeared (the „nepoznám" line above) and where the
// „nothing is open" refusal points him, so it is where he must be able to file it. Shoptet's
// status names are a text field the shop owner edits; until #209 they were baked into the
// code, so a rename emptied „Na objednanie", „Nedostupné" and the customer reminders in
// silence and narrowed the prune.
const ORDER_STATUS_BOXES = [
  ['to_order', 'Objednávka sa spracúva',
   'Riadky takých objednávok sa zobrazujú v „Na objednanie" a v „Nedostupné" a chodia z '
   + 'nich pripomienkové e-maily zákazníkom.'],
  ['terminal', 'Objednávka je ukončená',
   'Len pri týchto stavoch sa po čase upratujú staré značky („objednané u dodávateľa", '
   + '„čaká sa", „skladom", „nedostupné"). Stav sem daj, len keď si istý — zmazané '
   + 'značky sa nedajú vrátiť.'],
  ['known_open', 'Ostatné známe stavy (nie sú ukončené)',
   'Stavy, o ktorých vieš, ale ktoré neznamenajú ukončenie. Značky pri nich ostávajú; '
   + 'sem patria, aby ich appka nehlásila ako neznáme.'],
  // #296 — a REFINEMENT of „ukončená", not a fourth independent bucket: it says which of
  // the finished statuses mean „odvolaná" instead of „odoslaná". „Odoslaná" is derived as
  // the rest of „ukončená" and deliberately has no box, so a rename stays ONE edit.
  ['cancelled', 'Objednávka je zrušená',
   'Podmnožina ukončených — ktoré z nich znamenajú zrušenie. Takým objednávkam Pošta '
   + 'neposiela upozornenia na nevyzdvihnutú zásielku a nepočíta ich do kontroly podacích '
   + 'čísel. Zvyšok ukončených berie ako odoslané. Každý stav odtiaľto musí byť aj '
   + 'v zozname „Objednávka je ukončená".'],
];

function renderOrderStatusConfig() {
  // Its OWN css namespace on purpose: the automation-card classes (.autostatus/.autometa/
  // .autoerr) are what a dozen E2E tests locate strictly on these very tabs, and a second
  // element wearing them turns every one of those into a strict-mode violation.
  const box = el('div', 'statuscfg');
  box.dataset.testid = 'order-statuses';
  box.appendChild(el('div', 'statuscfg-head', '<b>Stavy objednávok v Shoptete</b>'));
  box.appendChild(el('div', 'statuscfg-desc',
    'Názvy stavov si v Shoptete nastavuje obchod, takže ich appka nemôže mať napevno. '
    + 'Tu je povedané, čo ktorý stav znamená — jeden stav na riadok.'));
  if (!ORDER_STATUSES) {
    box.appendChild(el('div', 'statuscfg-help', 'Nastavenie sa nepodarilo načítať.'));
    return box;
  }
  if (ORDER_STATUSES.reason) {
    // This is the card he was sent to, so it must not present the built-in defaults as
    // though he had typed them (PR #295 review).
    box.appendChild(el('div', 'statuscfgerr',
      '⛔ Uložené nastavenie sa nedá použiť, takže appka dočasne beží na PREDVOLENÝCH '
      + 'stavoch nižšie: staré značky sa neupratujú a pripomienkové e-maily zákazníkom sa '
      + 'neposielajú. Skontroluj zoznamy a ulož ich znova.'));
  }
  const admin = isAdmin();
  const fields = {};
  for (const [key, title, help] of ORDER_STATUS_BOXES) {
    const values = (ORDER_STATUSES.statuses || {})[key] || [];
    box.appendChild(el('div', 'statuscfg-label', `<b>${escapeHtml(title)}</b>`));
    box.appendChild(el('div', 'statuscfg-help', escapeHtml(help)));
    // PR #295 review — a name that matches NOTHING is otherwise invisible: it is echoed
    // back exactly as typed while the tab, „Nedostupné" and the reminders quietly go
    // empty. The server sends what the cached export really carries, so say which of his
    // names are not in it (a typo, a rename in Shoptet, a paste with odd characters).
    const inExport = ORDER_STATUSES.export_statuses;
    if (Array.isArray(inExport) && inExport.length) {
      const orphans = values.filter(v => !inExport.includes(v));
      if (orphans.length) box.appendChild(el('div', 'statuscfg-warn',
        '⚠️ V objednávkach sa nenachádza: ' + escapeHtml(orphans.join(' · '))
        + ' — preklep alebo premenovaný stav? Export teraz nesie: '
        + escapeHtml(inExport.join(' · '))));
    }
    if (admin) {
      const ta = document.createElement('textarea');
      ta.className = 'statusset';
      ta.rows = Math.max(3, values.length + 1);
      ta.value = values.join('\n');
      ta.dataset.testid = `order-statuses-${key}`;
      fields[key] = ta;
      box.appendChild(ta);
    } else {
      // a non-admin still needs to SEE what the app is going by — the counts on this card
      // only make sense next to it
      box.appendChild(el('div', 'statuscfg-help',
        values.length ? escapeHtml(values.join(' · ')) : '(prázdne)'));
    }
  }
  if (!admin) return box;
  const save = el('button', 'btn sm good', '💾 Uložiť stavy');
  save.dataset.testid = 'order-statuses-save';
  const msg = el('div', 'statuscfg-msg', '');
  msg.dataset.testid = 'order-statuses-msg';
  save.onclick = async () => {
    const payload = {};
    for (const [key] of ORDER_STATUS_BOXES) {
      payload[key] = fields[key].value.split('\n').map(s => s.trim()).filter(Boolean);
    }
    save.disabled = true;
    try {
      const r = await fetch('/api/order-statuses', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        // the server REFUSES a configuration that would break the prune; show its sentence
        // verbatim instead of a generic „nepodarilo sa", which would tell him nothing
        msg.className = 'statuscfg-msg bad';
        msg.textContent = '⛔ ' + (j.error || 'Nepodarilo sa uložiť.');
        return;
      }
      ORDER_STATUSES.statuses = j.statuses;
      // PR #295 review — this endpoint only ever answers 200 once its own
      // `_resolve_status_sets` found nothing wrong with what was just written (the
      // response carries no separate `reason` field to read instead — checked in
      // app.py), so success itself IS the „the stale reason no longer holds" signal.
      // Without clearing it, `renderOrderStatusConfig()` keeps drawing the ⛔ banner
      // for a configuration that no longer exists on disk, right above this very
      // message saying it is fixed — he would only find out on the next reload.
      ORDER_STATUSES.reason = '';
      render();   // rebuilds this whole panel — the OLD `msg` node above is now detached
      const freshMsg = document.querySelector('[data-testid="order-statuses-msg"]');
      if (freshMsg) {
        freshMsg.className = 'statuscfg-msg';
        freshMsg.textContent = '✅ Uložené. Platí to hneď pre celú appku.';
      }
    } catch (e) {
      msg.className = 'statuscfg-msg bad';
      msg.textContent = '⛔ Server neodpovedal: ' + String(e);
    } finally {
      save.disabled = false;
    }
  };
  const foot = el('div', 'statuscfg-head');
  foot.appendChild(save);
  box.appendChild(foot);
  box.appendChild(msg);
  return box;
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
// #270/#275 — „eshop taký kód nemá". A variant code that is missing from the
// eshop's own catalogue export can never be imported: Shoptet rejects that row on
// EVERY run (the same „Zlyhanie variantov: 2" every night since 24. 7. 2026), and
// the manager saw only a red count with no way to learn WHICH code or WHY. The push
// now holds those rows back and names them here, with the value it wanted to
// write, so the code can be fixed. `p`/`s` are the pairings/suppliers halves of
// last_result; returns null when there is nothing to report (the normal night).
// Since #275 BOTH halves hold, so a listed code means the same thing either way —
// the per-row „(dodávateľ — zapisuje sa ďalej)" caveat is gone with the asymmetry.
// Why the fail-closed gate blocked the supplier write-back (#280 review). Every block
// used to render „(chýbajú kódy)", which is the one cause it never is: nothing is
// missing, the export itself is not believable. `g` is suppliers.gate_blocked from the
// run result, null on a healthy night AND on a block that really IS about missing codes
// (#270) — that one keeps its original wording. Pure; unit-tested in the browser realm.
function gateBlockedWhy(g) {
  if (!g) return ' (chýbajú kódy)';
  if (g.reason === 'stale') {
    return ` (export je starý ${g.age_h} h, limit ${g.max_age_h} h`
      + ' — čakám na čerstvý export)';
  }
  if (g.reason === 'small') {
    return ` (export vyzerá neúplný — ${g.codes} kódov, limit ${g.min_codes}`
      + ' — čakám na dobrý export)';
  }
  return ' (export chýba alebo je prázdny — čakám na dobrý export)';
}

// Pure enough to unit-test in the browser realm — see tests/e2e/test_shell.py.
function missingCodesBox(p, s) {
  const rows = [...(p.missing_in_eshop || []), ...(s.missing_in_eshop || [])];
  const total = (Number(p.missing_count) || 0) + (Number(s.missing_count) || 0);
  if (!total) return null;
  // own class (styled FROM .autoerr): `.autoerr` means „the last run failed" and sits
  // as a direct child of the tab section — a second, NESTED match would break every
  // unscoped `.autoerr` locator in the e2e suite.
  const box = el('div', 'automiss');
  box.appendChild(el('div', '',
    `⛔ Eshop tieto kódy v katalógu nemá (${total}) — oprav kód v eshope alebo `
    + 'párovanie na záložke „Na objednanie".'));
  const ul = el('ul', 'misscodes');
  for (const m of rows) {
    const li = document.createElement('li');
    // free text out of the manager's stores — textContent only, never innerHTML
    li.textContent = m.code + (m.value ? ' → ' + m.value : '');
    ul.appendChild(li);
  }
  box.appendChild(ul);
  if (rows.length < total) {
    box.appendChild(el('div', 'sub2', `… a ďalších ${total - rows.length}`));
  }
  return box;
}

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
    // #257: the two facts a partially-accepted push turns on. Both come from the API
    // result and were invisible here, so the manager could not see that Shoptet had
    // rejected rows at all — only the generic error line below (if any).
    //   • potvrdené z exportu = rows the eshop already had exactly as we would write
    //     them, credited from its own export and NOT re-sent;
    //   • odmietol = rows Shoptet refused out of the ones we did send (they stay
    //     pending and are re-sent until the export confirms them).
    box.appendChild(el('div', 'sub2',
      `✔ Už v eshope (potvrdené z exportu): ${p.confirmed_in_export ?? 0}`
      + ` · ⛔ Shoptet odmietol: ${p.rejected ?? 0} riadkov`
      + (p.partial ? ' · časť dávky odmietnutá — zvyšok sa potvrdí z exportu' : '')));
    // #156: on a chunk failure, show WHICH chunk failed + how many rows made it (the
    // successful chunks ARE saved → the next run only retries the rest)
    if (p.error) box.appendChild(el('div', 'sub2 err', '❌ ' + escapeHtml(p.error)));
    // #38: inline páry pridané priamo na riadku „Na objednanie" (mimo review setu)
    box.appendChild(el('div', '',
      `📦 Inline páry: +${p.order_count ?? 0} nových`
      + (p.order_blocked ? ` · ${p.order_blocked} prekrytých recenziou` : '')));
    box.appendChild(el('div', '',
      `🏷️ Dodávatelia: +${s.count ?? 0} nových`
      + (s.blocked ? ` · ${s.blocked} zablokovaných${gateBlockedWhy(s.gate_blocked)}` : '')
      + ` · spolu ${s.total_uploaded ?? 0} / ${s.total_assigned ?? 0} doplnených`
      + ` · chýba ${s.remaining ?? 0}`));
    if (s.error) box.appendChild(el('div', 'sub2 err', '❌ ' + escapeHtml(s.error)));
    // #270 — the codes the eshop's catalogue does not have at all
    const miss = missingCodesBox(p, s);
    if (miss) box.appendChild(miss);
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
  const d = ORDERS_REMINDER || {};
  const st = el('div', 'autostatus');
  if (!a) {
    st.appendChild(el('div', 'muted', 'Automatizácia nie je dostupná (server nevrátil stav).'));
    wrap.appendChild(st);
    // …but a corrupt store must still be announced on THIS path too (renderPosta handles the
    // same case inline and never returned early — the asymmetry hid the banner here).
    if (d.store_corrupt) wrap.appendChild(storeCorruptWarning('orders_reminder.json'));
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
      // „v evidencii", not „spolu": the dedup store drops records for orders long gone from the
      // export (#220), so this is the number still on record — not a lifetime total that would
      // otherwise appear to shrink on its own.
      + (lr.emailed_total ? ` · pripomenutých v evidencii: ${lr.emailed_total}` : '')
      + (lr.ai_unavailable ? ` · AI nedostupné: ${lr.ai_unavailable}` : '')
      + (lr.no_email ? ` · chýba e-mail: ${lr.no_email}` : '')
      + (lr.errors ? ` · chyby: ${lr.errors}` : '')));
  }
  if (a.last_run && lr.bcc_missing) st.appendChild(bccMissingWarning());
  if (a.last_run && lr.bad_status_config) st.appendChild(badStatusConfigWarning());
  wrap.appendChild(st);

  if (d.store_corrupt) wrap.appendChild(storeCorruptWarning('orders_reminder.json'));
  const red = d.red || [];
  const orange = d.orange || [];
  const skipped = d.skipped || [];
  const noEmail = d.no_email || [];
  if (!red.length && !orange.length && !skipped.length && !noEmail.length) {
    wrap.appendChild(el('div', 'empty2',
      d.last_check ? `Žiadne objednávky na pripomenutie (kontrola ${fmtDt(d.last_check)}).`
                   : 'Zatiaľ neprebehla žiadna kontrola — spusti automatizáciu (▶ Štart) alebo klikni ⚡ Spustiť teraz.'));
    return;
  }

  // manual per-row override (#153) — "send" (▶ pripomienka teraz) works on red AND skipped rows
  // (overriding a wrong AI 'už kontaktovaný' verdict); "contact" (✓ kontaktované) only on red
  // rows (no note ever ran through the AI, so it can't already be resolved).
  // The button is disabled for the whole round-trip: the send takes ~20s (SMTP), and a second
  // click during it used to reach the server as a second 'send' request. The backend now
  // rejects that with 409, but a disabled button is what keeps the manager from triggering it
  // (and from staring at an unresponsive row) in the first place.
  function _ordremAction(btn, code, action) {
    btn.onclick = async () => {
      if (btn.disabled) return;
      btn.disabled = true;
      try { await overrideOrdersReminder(code, action); }
      finally { btn.disabled = false; }   // no-op when the re-render already replaced the row
    };
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
      actTd.appendChild(_ordremPreviewBtn(o.code));
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

  // NO E-MAIL — the customer has no address on file, so the reminder can never be sent and the
  // order is never classified (an AI call every run would be paid for nothing). Shown so the
  // manager can add the address in Shoptet — the next run then handles the order normally.
  if (noEmail.length) {
    wrap.appendChild(el('div', 'warnhead',
      `✉️ ${noEmail.length} — chýba e-mail zákazníka (doplň ho v Shoptete)`));
    const tbl = el('table', 'posta-table');
    tbl.dataset.testid = 'ordrem-noemail';
    tbl.innerHTML = '<thead><tr><th>Objednávka</th><th>Zákazník</th><th>Položka</th>'
      + '<th>Interná poznámka</th><th>Bez pohybu</th></tr></thead>';
    const tb = el('tbody');
    for (const o of noEmail) {
      const tr = el('tr', 'callneeded'); tr.dataset.code = o.code;
      tr.innerHTML =
        `<td><a href="${escapeHtml(o.admin_link)}" target="_blank" rel="noopener">${escapeHtml(o.code)}</a></td>`
        + `<td>${escapeHtml(o.billFullName || '')}`
        + `<div class="sub2">${escapeHtml(o.phone || '') || 'bez telefónu'} · chýba e-mail</div></td>`
        + `<td>${escapeHtml(o.itemName || '')}</td>`
        + `<td class="sub2">${escapeHtml(o.shopRemark || '—')}</td>`
        + `<td>${o.days || 0} ${dniLabel(o.days || 0)}</td>`;
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

  // The backend keeps all three kinds of row in one `skipped` list (so the override endpoint
  // finds them the same way), but they mean very different things to the manager, so they render
  // as THREE sections — and every row lands in exactly one of them:
  //   • `pending`  — the run STARTED the order but could not finish it (failed send, failed
  //     classification, unwritable claim, missing config);
  //   • `manual`   — the MANAGER resolved it by hand (#227);
  //   • the rest   — the AI's own „already contacted" verdicts.
  // Only the last group may carry the „AI usúdilo…" heading: for the other two the classifier
  // frequently never ran at all (a red row has no note; with no MAIL_BCC or no OPENAI_API_KEY
  // the AI is deliberately not called), so that heading would state something that never
  // happened and hide the fact that a HUMAN decided.
  _ordremSkippedTable(wrap, skipped.filter(o => !o.pending && !o.manual), _ordremAction,
    `⚪ %n — AI usúdilo, že zákazník je už kontaktovaný`, 'ordrem-skipped');
  _ordremSkippedTable(wrap, skipped.filter(o => !o.pending && o.manual), _ordremAction,
    `✋ %n — vybavené ručne manažérom`, 'ordrem-manual');
  _ordremSkippedTable(wrap, skipped.filter(o => o.pending), _ordremAction,
    `⚠️ %n — automat ich nestihol vybaviť (pošli ručne)`, 'ordrem-pending');
}

// #217 — „👁 Náhľad" for one order's reminder e-mail (read-only; the send is the ▶ button).
function _ordremPreviewBtn(code) {
  return _previewBtn(`ordrem-preview-${code}`, '/api/orders-reminder/preview',
    { code }, `Náhľad pripomienky — objednávka ${code}`);
}

// One „skipped-shaped" table (note + „▶ Poslať pripomienku"), rendered for all three sections
// above.
function _ordremSkippedTable(wrap, rows, actionWire, headTpl, testid) {
  if (rows.length) {
    wrap.appendChild(el('div', 'warnhead', headTpl.replace('%n', String(rows.length))));
    const tbl = el('table', 'posta-table');
    tbl.dataset.testid = testid;
    tbl.innerHTML = '<thead><tr><th>Objednávka</th><th>Zákazník</th><th>Položka</th>'
      + '<th>Interná poznámka</th><th>Akcia</th></tr></thead>';
    const tb = el('tbody');
    for (const o of rows) {
      const tr = el('tr', ''); tr.dataset.code = o.code;
      tr.innerHTML =
        `<td><a href="${escapeHtml(o.admin_link)}" target="_blank" rel="noopener">${escapeHtml(o.code)}</a></td>`
        + `<td>${escapeHtml(o.billFullName || '')}<div class="sub2">${escapeHtml(o.email || '')}</div></td>`
        + `<td>${escapeHtml(o.itemName || '')}<div class="sub2">${o.days || 0} ${dniLabel(o.days || 0)} v stave</div></td>`
        + `<td class="sub2">${escapeHtml(o.shopRemark || '—')}`
        + (o.pending ? `<div class="sub2">⚠️ ${escapeHtml(o.pending)}</div>` : '')
        + `</td>`;
      const actTd = el('td', 'ordrem-actions');
      actTd.appendChild(_ordremPreviewBtn(o.code));
      const sendBtn = el('button', 'btn sm ghost ordrem-act-send', '▶ Poslať pripomienku');
      actionWire(sendBtn, o.code, 'send');
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
  renderSchedulerWarning(auto || vystavy);   // výstavy majú vlastnú automatizáciu v tabe
  document.body.classList.toggle('toorder-wide', toorder);   // od kraja po kraj len na tabe „Na objednanie"
  const prog = document.querySelector('.progress'); if (prog) prog.style.display = (toorder || plain) ? 'none' : '';
  const dls = document.querySelector('.downloads'); if (dls) dls.style.display = (toorder || plain) ? 'none' : '';
  const filt = document.getElementById('filters'); if (filt) filt.style.display = plain ? 'none' : '';
  const tbar = document.getElementById('toToolbar'); if (tbar) tbar.hidden = !toorder;   // #208
  // #234 — the top bar is SHARED with the review tab (the `setEmptyText` lesson), so a
  // warning about order lines must not follow him there. It is only ever shown on this
  // tab, and only while something on it still needs redoing.
  renderToOrderFails();   // #234 — its own rule: this tab only, and only while non-empty
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
  setEmptyText(null);   // the box is SHARED — never inherit „Na objednanie"'s wording
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
  initEmModal();
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
  // #203 — chip keys are now 's:'+normalised supplier. Migrate a value stored by an
  // older build (the raw supplier name) so the manager keeps his selected supplier.
  const savedSup = localStorage.getItem('orderSupplier') || 'all';
  ORDER_SUPPLIER = (savedSup === 'all' || savedSup.startsWith('s:'))
    ? savedSup : 's:' + supKey(savedSup);
  // persist the migrated form, so the old value is converted once and not on every load
  if (ORDER_SUPPLIER !== savedSup) localStorage.setItem('orderSupplier', ORDER_SUPPLIER);
  // #205 — working a long supplier list down spans several visits, so the „skryť
  // poriešené" choice is remembered. Default OFF: the tab must never hide rows the
  // manager did not ask it to hide.
  HIDE_HANDLED = localStorage.getItem('hideHandled') === '1';
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
  if (ACTIVE_TAB === 'shoptet_sync') await loadShoptetSync();   // #209 — the panel needs it
  if (ACTIVE_TAB === 'users') await loadUsers();
  if (ACTIVE_TAB === 'posta') await loadPosta();
  if (ACTIVE_TAB === 'dev') await loadDevIssues();
  if (ACTIVE_TAB === 'vystavy') await loadVystavy();   // #199: deep-link / remembered tab must load data
  initSearch();
  render();
  const y = parseInt(localStorage.getItem('scrollY') || '0', 10);
  if (y) window.scrollTo(0, y);
}
init();

// Resizable sidebar — drag the right-edge grip to widen/narrow; width persists in
// localStorage('sideW'). Double-click the grip resets to the default. The width is
// applied via the CSS var --side-w (see .sidebar in style.css).
(function initSidebarResize() {
  const side = document.querySelector('.sidebar');
  if (!side) return;
  const MIN = 170, MAX = 560, KEY = 'sideW';
  const saved = parseInt(localStorage.getItem(KEY) || '', 10);
  if (saved >= MIN && saved <= MAX) {
    document.documentElement.style.setProperty('--side-w', saved + 'px');
  }
  const grip = document.createElement('div');
  grip.className = 'side-resizer';
  grip.title = 'Ťahaj pre zmenu šírky (dvojklik = pôvodná)';
  side.appendChild(grip);
  let startX = 0, startW = 0, dragging = false;
  const onMove = (e) => {
    if (!dragging) return;
    if (e.buttons === 0) { onUp(); return; }   // released outside the window → stop (no rubber-band)
    const w = Math.max(MIN, Math.min(MAX, startW + (e.clientX - startX)));
    document.documentElement.style.setProperty('--side-w', w + 'px');
  };
  const onUp = () => {
    if (!dragging) return;
    dragging = false;
    grip.classList.remove('dragging');
    document.body.classList.remove('resizing-side');
    localStorage.setItem(KEY, parseInt(getComputedStyle(side).width, 10));
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
  };
  grip.addEventListener('mousedown', (e) => {
    dragging = true;
    startX = e.clientX;
    startW = parseInt(getComputedStyle(side).width, 10);
    grip.classList.add('dragging');
    document.body.classList.add('resizing-side');
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
    e.preventDefault();
  });
  grip.addEventListener('dblclick', () => {
    document.documentElement.style.removeProperty('--side-w');
    localStorage.removeItem(KEY);
  });
})();
