# Dodávatelia — recon webov a pridanie nového

Load BEFORE pridávaním nového dodávateľa do párovača alebo ladením parsovania výsledkov.

## Hotoví dodávatelia (živé zistenia)

| Dodávateľ | Web | Platforma | Hľadanie | Výsledok-selektor | Pozn. |
|---|---|---|---|---|---|
| BETALOV | huntingshop.eu | Nette | `/hladanie?search=<q>` | kontajner `#snippet--productList`, odkazy `.product-col a.mh-100` (thumb) / `.product-title a` (názov) | **AJAX-lazy: prázdne bez PHPSESSID** — klient musí `requests.Session` + warmup GET na homepage |
| WETLAND | wetland.sk | PrestaShop | `/vyhladavanie?controller=search&s=<q>` | `div.product-miniature__title a.link` | produkt-URL má `#/variant` fragment → striptnúť `urldefrag` |
| ODIMON | odimon.sk | BUXUS | `/vysledky-vyhladavania?term=<q>` | scope `.product-list__results`, karta = `a.product-card` (celý link), názov z `img[alt]/[title]` | statické HTML, cookie-gated (warmup GET homepage). Kód-search funguje (AH5→Alpenheat AH5 hore). 76 % má externalCode |

`/export/*` feedy nie sú verejné (huntingshop 302, wetland 404) → scrapujeme hľadanie.

## Pridanie nového dodávateľa

1. **Recon** (curl s browser UA): nájdi search URL + či sú výsledky v statickom HTML alebo cez JS (ak JS → Playwright). Pozri `<meta name="generator">` / `x-powered-by` hlavičku na platformu.
2. `config.SUPPLIERS[<MENO>]` = base_url + `search_url_template` s `{q}`.
3. `suppliers/<meno>.py` s `parse_search(html, base_url) -> list[Candidate]` — **scopuj na kontajner výsledkov**, nie celý `a[href]` (inak chytíš nav + „odporúčané" karusel). Over selektor proti uloženej fixtúre.
4. Zaregistruj parser v `client.PARSERS`.
5. Fixtúry do `tests/fixtures/` (uložené HTML), testy bez živej siete.

## Párovanie — overený postup (kvalita)

Surové fuzzy-párovanie podľa názvu je ~50 % zlé (supplier model-názvy ≠ forestshop názvy). **Preto:**

1. `gather_candidates` zoberie **top-K (8)** z viacerých query variantov (kód, celý názov, názov bez generického slova, prefix+suffix token-skupiny) — `scripts/run_gather.py` → `data/out/candidates.json`. Plný beh ~2h (huntingshop ~1.1s/req), resumable cez checkpoint.
2. **AI overí každý produkt** (Workflow nad `verify_input.json`, dávky ~40, sonnet): vyberie správneho kandidáta z 8 alebo `-1` (žiadny) — prísne, lebo odkaz kŕmi auto-objednávku. Detail: `docs/runbooks/verification-workflow.md`.
3. `scripts/finalize.py` → import (`code;textProperty10`, cp1250, holá URL) + report s verdiktmi + unmatched.

Výsledok Betalov+Wetland: 784/969 napárovaných, 185 prísne zamietnutých.

## Gotchas

- **Forestshop URL produktu sa NEDÁ z exportu** (CSV/XML nemajú čisté URL pole; `seoTitle` prázdne). Rieš cez **sitemap.xml** — kánonický resolver je `parovanie.url_resolver` (`assign_urls`), **NEkopíruj logiku**. Match: exact názov-slug → ak nie, kandidáti = sitemap slugy ktorých tokeny ⊇ tokeny názvu. **Pozor — číslo v názve sa zahodí ako digit token**, takže „Moor Padded 367" a „393" majú rovnaké tokeny a kolidujú; rozlišuje sa **slugom z názvu OBRÁZKA** (`15233_…waistcoat-vesta` → vyberie `…-waistcoat`). Pravidlá: zlý link je horší než žiadny → nejednoznačné = `None` (UI padne na search); dva RÔZNE produkty nikdy nesmú zdieľať jednu URL (dedup ponechá najsilnejší match, zvyšok `None`); rovnaký názov-slug = ozajstný katalógový duplikát (ten istý produkt 2×) → obe nechá. ~645/969 priamych URL; zvyšok fallback `…/vyhladavanie/?string=<name>` (**`?q=` presmeruje na homepage, nepouživaj**). **Opraviť URL na existujúcom `review_data.json` bez plného rebuildu: `scripts/reresolve_urls.py`** (in-place, idempotentný, padne ak zostane kolízia).
- **`productVisibility: detailOnly`** = produkt dostupný LEN cez priamy odkaz (nie v sitemap/vyhľadávaní). Mnohé naše drop-ship produkty sú také → sitemap (3378) < katalóg (4513). URL sa nedá z poľa; `resolve_urls.py` overí slug-kandidátov cez HTTP 200 (názov-slug, bez generického slova, `polovnicke-/polovnicka-` prefix). Tak vznikne 791/969 priamych URL; zvyšok fallback `?string=`.
- **Obrázky z produktovej stránky: ver LEN `og:image`** — gallery selektory ťahajú aj „súvisiace/odporúčané" produkty (zlé obrázky). og:image je spoľahlivo hlavný produkt.
- **Katalóg driftuje** (produkty sa prečíslujú/preskladnia) — zber je snapshot, kódy starnú. Po refreshi exportu spusti `resync_export.py`: rejoin každého produktu na AKTUÁLNY export podľa `(supplier, name)` → čerstvé kódy/obrázky/stav. Inak import sadne na zlý/neexistujúci kód.
- **„off" = NEpredajný** (hidden/blocked ALEBO availability vypredané/„predaj skončil"/nedostupné). **`detailOnly` NIE je off** (drop-ship, predajný cez link) — inak ukáže skladové produkty ako vypnuté.
- **`textProperty11=human matched`** v import_links → označí ručne overené; budúci zber tieto preskočí (filtruj v load step).
- **Durabilita rozhodnutí (webreview):** decisions kľúčuj **stabilným `supplier|pairCode`** (NIE array idx — prestavba dát by rozhodila), ukladaj na disk `data/out/decisions.json` (gitignored, prežije reštart/deploy), atomicky (tmp+rename). Assety verzuj `?v=N` nech reload ukáže novú UI.
- **Kódovanie cp1250** (Windows-1250) vstup aj výstup, `;`, CRLF — Shoptet import to čaká.
- `externalCode` = supplier kód, ale huntingshop ho často NEindexuje (napr. OB570 → 0 výsledkov) → query-rebrík/varianty nutné, nielen kód.
- `pair_key` musí byť scope-nutý dodávateľom (`sup|key`) — inak kolízia v checkpointe pri rovnakom pairCode u dvoch dodávateľov.
