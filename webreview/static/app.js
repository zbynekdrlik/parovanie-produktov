let PRODUCTS = [];
let DECISIONS = {};         // key -> {status, url}
let FILTER = 'unreviewed';
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
    // fill an associated title node (for manually entered links)
    if (box.dataset.titleId && j.title) {
      const t = document.getElementById(box.dataset.titleId);
      if (t) t.textContent = j.title;
    }
  } catch (_) { box.classList.remove('loading'); }
}

let _tid = 0;
function gallery(url, titleNode) {
  const b = el('div', 'imgs loading'); b.dataset.url = url;
  if (titleNode) { const id = 'ti' + (++_tid); titleNode.id = id; b.dataset.titleId = id; }
  imgObserver.observe(b); return b;
}
function smallThumb(url) { const b = el('div', 'thumb loading'); b.dataset.url = url; imgObserver.observe(b); return b; }

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
    case 'off_now': return p.current && p.current.off;
    case 'good': return s === 'good' || s === 'manual';
    case 'unavailable': return s === 'unavailable';
    default: return true;
  }
}

function el(tag, cls, html) { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }
function escapeHtml(s) { return (s || '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
function badge(s) {
  const t = { good: '✓ Dobré', manual: '✓ Vybraný link', unavailable: '⛔ Nedostupné' }[s];
  return t ? el('span', 'badge ' + s, t) : null;
}

// supplier block: title (lazy for manual links), url link, image gallery
function supplierBlock(container, p, url, showReason) {
  const cand = p.candidates.find(c => c.url === url);
  const title = el('div', 'pname', cand ? escapeHtml(cand.name || '(produkt)') : 'načítavam názov…');
  container.appendChild(title);
  const a = el('a', 'supurl'); a.href = url; a.target = '_blank'; a.rel = 'noopener'; a.textContent = url;
  container.appendChild(a);
  if (showReason && p.ai_reason && p.ai_status === 'matched') container.appendChild(el('div', 'reason', '🤖 ' + escapeHtml(p.ai_reason)));
  container.appendChild(gallery(url, cand ? null : title));   // lazy title only when unknown
}

// candidates + manual URL + Nedostupné. Saving here moves the card to its list.
function resolutionPanel(p) {
  const wrap = el('div', 'panel');
  const cur = decUrl(p), s = statusOf(p);
  p.candidates.forEach((c) => {
    const row = el('div', 'cand');
    row.appendChild(smallThumb(c.url));
    const m = el('div', 'c-main');
    m.appendChild(el('div', 'c-name', escapeHtml(c.name || '(produkt)')));
    const a = el('a', 'supurl'); a.href = c.url; a.target = '_blank'; a.rel = 'noopener'; a.textContent = c.url;
    m.appendChild(a); row.appendChild(m);
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
  const un = el('button', 'btn warn sm' + (s === 'unavailable' ? ' active' : ''), '⛔ Nedostupné → Vypredané');
  un.style.marginTop = '6px';
  un.onclick = () => saveDecision(p, 'unavailable', '');
  wrap.appendChild(un);
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
  if (p.current) left.appendChild(el('span', 'curbadge ' + (p.current.off ? 'off' : 'on'),
    p.current.off ? '⚫ teraz vypnutý u nás' : '🟢 teraz zapnutý u nás'));
  const oimgs = el('div', 'imgs');
  if (p.our_images.length) for (const u of p.our_images) { const im = el('img'); im.src = u; im.loading = 'lazy'; oimgs.appendChild(im); }
  else oimgs.innerHTML = '<span class="noimg">bez obrázkov</span>';
  left.appendChild(oimgs);
  card.appendChild(left);

  // RIGHT — supplier / decision
  const right = el('div', 'side right');
  right.appendChild(el('div', 'label', 'Dodávateľ'));
  const bg = badge(s); if (bg) right.appendChild(bg);

  if (s === 'unavailable') {
    right.appendChild(el('div', 'reason', 'Označené ako nedostupné → pri importe sa nastaví Vypredané (stock 0).'));
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
  ['off_now', '⚫ Teraz vypnuté'], ['good', '✓ Dobré/Vybrané'], ['unavailable', '⛔ Vypnuté'], ['all', 'Všetky'],
];

function renderFilters() {
  const f = document.getElementById('filters'); f.innerHTML = '';
  for (const [key, lbl] of FILTERS) {
    const bt = el('button', FILTER === key ? 'active' : '', lbl);
    bt.onclick = () => { FILTER = key; localStorage.setItem('filter', key); window.scrollTo(0, 0); render(); };
    f.appendChild(bt);
  }
}

function render() {
  const keepY = window.scrollY;
  renderFilters();
  const reviewed = Object.keys(DECISIONS).length;
  document.getElementById('progressText').textContent = `${reviewed} / ${PRODUCTS.length} skontrolovaných`;
  document.getElementById('progressBar').style.width = (100 * reviewed / PRODUCTS.length) + '%';
  const list = document.getElementById('list'); list.innerHTML = '';
  const shown = PRODUCTS.filter(matchesFilter);
  document.getElementById('empty').hidden = shown.length > 0;
  for (const p of shown) list.appendChild(renderCard(p));
  window.scrollTo(0, keepY);
}

let _scrollTimer;
window.addEventListener('scroll', () => {
  clearTimeout(_scrollTimer);
  _scrollTimer = setTimeout(() => localStorage.setItem('scrollY', String(window.scrollY)), 150);
});

async function init() {
  const j = await (await fetch('/api/products')).json();
  PRODUCTS = j.products;
  DECISIONS = j.decisions || {};
  PRODUCTS.sort((a, b) =>
    ((a.ai_status === 'unmatched') ? 1 : 0) - ((b.ai_status === 'unmatched') ? 1 : 0) || a.idx - b.idx);
  FILTER = localStorage.getItem('filter') || 'unreviewed';
  render();
  const y = parseInt(localStorage.getItem('scrollY') || '0', 10);
  if (y) window.scrollTo(0, y);
}
init();
