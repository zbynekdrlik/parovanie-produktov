let PRODUCTS = [];
let DECISIONS = {};
let FILTER = 'unreviewed';

const imgObserver = new IntersectionObserver((entries) => {
  for (const e of entries) {
    if (e.isIntersecting) { loadSupplierImages(e.target); imgObserver.unobserve(e.target); }
  }
}, { rootMargin: '300px' });

async function loadSupplierImages(box) {
  const url = box.dataset.url;
  if (!url) { box.classList.remove('loading'); return; }
  try {
    const r = await fetch('/api/images?url=' + encodeURIComponent(url));
    const j = await r.json();
    box.classList.remove('loading');
    box.innerHTML = '';
    if (!j.images.length) { box.innerHTML = '<span style="font-size:12px;color:#9ca3af">bez obrázkov</span>'; return; }
    for (const u of j.images) {
      const im = document.createElement('img');
      im.src = u; im.loading = 'lazy'; box.appendChild(im);
    }
  } catch (_) { box.classList.remove('loading'); }
}

async function saveDecision(idx, status, url) {
  DECISIONS[idx] = { status, url: url || '' };
  await fetch('/api/decision', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ idx, status, url: url || '' })
  });
  render();
}

function statusOf(p) {
  const d = DECISIONS[String(p.idx)];
  return d ? d.status : null;  // good | bad | manual | none | null(unreviewed)
}

function matchesFilter(p) {
  const s = statusOf(p);
  switch (FILTER) {
    case 'all': return true;
    case 'unreviewed': return s === null;
    case 'matched': return p.ai_status === 'matched';
    case 'unmatched': return p.ai_status === 'unmatched';
    case 'good': return s === 'good' || s === 'manual';
    case 'bad': return s === 'bad' || s === 'none';
    default: return true;
  }
}

function el(tag, cls, html) { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }

function supplierImageBox(url) {
  const box = el('div', 'imgs loading'); box.dataset.url = url; imgObserver.observe(box); return box;
}

function renderCard(p) {
  const s = statusOf(p);
  const card = el('div', 'card' + (s ? ' ' + s : ''));

  // LEFT — our product
  const left = el('div', 'side left');
  left.appendChild(el('div', 'label', 'Náš produkt'));
  left.appendChild(el('div', 'pname', escapeHtml(p.name)));
  const oa = el('a', 'supurl');
  oa.href = 'https://www.forestshop.sk/vyhladavanie/?q=' + encodeURIComponent(p.name);
  oa.target = '_blank'; oa.rel = 'noopener'; oa.textContent = '↗ otvoriť náš produkt na forestshop.sk';
  left.appendChild(oa);
  left.appendChild(el('div', 'meta', `${p.supplier} · pairCode ${p.pairCode || '—'} · ${p.variant_codes.length} variant(ov)`));
  const oimgs = el('div', 'imgs');
  if (p.our_images.length) { for (const u of p.our_images) { const im = el('img'); im.src = u; im.loading = 'lazy'; oimgs.appendChild(im); } }
  else oimgs.innerHTML = '<span style="font-size:12px;color:#9ca3af">bez obrázkov</span>';
  left.appendChild(oimgs);
  card.appendChild(left);

  // RIGHT — supplier
  const right = el('div', 'side right');
  right.appendChild(el('div', 'label', 'Dodávateľ' + (s ? '' : '')));
  if (s) { const b = el('span', 'badge ' + s, badgeText(s)); right.appendChild(b); }

  if (p.ai_status === 'matched') {
    const chosen = p.candidates.find(c => c.url === p.ai_chosen_url) || { name: '', url: p.ai_chosen_url };
    right.appendChild(el('div', 'pname', escapeHtml(chosen.name || '(produkt)')));
    const a = el('a', 'supurl'); a.href = p.ai_chosen_url; a.target = '_blank'; a.rel = 'noopener'; a.textContent = p.ai_chosen_url;
    right.appendChild(a);
    if (p.ai_reason) right.appendChild(el('div', 'reason', '🤖 ' + escapeHtml(p.ai_reason)));
    right.appendChild(supplierImageBox(p.ai_chosen_url));
    const act = el('div', 'actions');
    const g = el('button', 'btn good' + (s === 'good' ? ' active' : ''), '✓ Dobré');
    g.onclick = () => saveDecision(p.idx, 'good', p.ai_chosen_url);
    const b = el('button', 'btn bad' + (s === 'bad' ? ' active' : ''), '✗ Zlé');
    b.onclick = () => saveDecision(p.idx, 'bad', '');
    act.appendChild(g); act.appendChild(b);
    right.appendChild(act);
  } else {
    // unmatched — pick a candidate or enter URL manually
    if (p.ai_reason) right.appendChild(el('div', 'reason', '🤖 AI zamietla: ' + escapeHtml(p.ai_reason)));
    const chosenUrl = (s === 'manual') ? (DECISIONS[String(p.idx)].url) : '';
    p.candidates.forEach((c) => {
      const row = el('div', 'cand');
      const ib = el('div', 'thumb loading'); ib.dataset.url = c.url; imgObserver.observe(ib);
      row.appendChild(ib);
      const m = el('div', 'c-main');
      m.appendChild(el('div', 'c-name', escapeHtml(c.name || '(produkt)')));
      const a = el('a', 'supurl'); a.href = c.url; a.target = '_blank'; a.rel = 'noopener'; a.textContent = c.url;
      m.appendChild(a);
      row.appendChild(m);
      const pick = el('button', 'btn good sm' + (chosenUrl === c.url ? ' active' : ''), 'Vybrať');
      pick.onclick = () => saveDecision(p.idx, 'manual', c.url);
      row.appendChild(pick);
      right.appendChild(row);
    });
    const mr = el('div', 'manualrow');
    const inp = el('input'); inp.type = 'url'; inp.placeholder = 'Vlož vlastnú URL dodávateľa…';
    if (s === 'manual' && !p.candidates.some(c => c.url === chosenUrl)) inp.value = chosenUrl;
    const save = el('button', 'btn good sm', 'Uložiť URL');
    save.onclick = () => { if (inp.value.trim().startsWith('http')) saveDecision(p.idx, 'manual', inp.value.trim()); };
    mr.appendChild(inp); mr.appendChild(save);
    right.appendChild(mr);
    const none = el('button', 'btn ghost sm' + (s === 'none' ? ' active' : ''), 'Žiadny / preskočiť');
    none.style.marginTop = '6px';
    none.onclick = () => saveDecision(p.idx, 'none', '');
    right.appendChild(none);
  }
  card.appendChild(right);
  return card;
}

function badgeText(s) { return { good: '✓ Dobré', bad: '✗ Zlé', manual: '✓ Ručne vybrané', none: '— Žiadny' }[s] || s; }
function escapeHtml(s) { return (s || '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

const FILTERS = [
  ['unreviewed', 'Nezrevidované'], ['matched', 'Napárované (AI)'], ['unmatched', 'Nenapárované'],
  ['good', '✓ Dobré/Vybrané'], ['bad', '✗ Zlé/Žiadny'], ['all', 'Všetky'],
];

function renderFilters() {
  const f = document.getElementById('filters'); f.innerHTML = '';
  for (const [key, lbl] of FILTERS) {
    const b = el('button', FILTER === key ? 'active' : '', lbl);
    b.onclick = () => { FILTER = key; localStorage.setItem('filter', key); window.scrollTo(0, 0); render(); };
    f.appendChild(b);
  }
}

function render() {
  const keepY = window.scrollY;  // preserve scroll across re-render (e.g. after a decision)
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
  const r = await fetch('/api/products');
  const j = await r.json();
  PRODUCTS = j.products;
  DECISIONS = j.decisions || {};
  // matched first, unmatched at the end (stable by idx within each group)
  PRODUCTS.sort((a, b) =>
    ((a.ai_status === 'unmatched') ? 1 : 0) - ((b.ai_status === 'unmatched') ? 1 : 0) || a.idx - b.idx);
  FILTER = localStorage.getItem('filter') || 'unreviewed';
  render();
  const y = parseInt(localStorage.getItem('scrollY') || '0', 10);
  if (y) window.scrollTo(0, y);
}
init();
