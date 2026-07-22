# Dodávatelia — recon webov a pridanie nového

Load BEFORE pridávaním nového dodávateľa do párovača alebo ladením parsovania výsledkov.

## Hotoví dodávatelia (živé zistenia)

| Dodávateľ | Web | Platforma | Hľadanie | Výsledok-selektor | Pozn. |
|---|---|---|---|---|---|
| BETALOV | huntingshop.eu | Nette | `/hladanie?search=<q>` | kontajner `#snippet--productList`, odkazy `.product-col a.mh-100` (thumb) / `.product-title a` (názov) | **AJAX-lazy: prázdne bez PHPSESSID** — klient musí `requests.Session` + warmup GET na homepage |
| WETLAND | wetland.sk | PrestaShop | `/vyhladavanie?controller=search&s=<q>` | `div.product-miniature__title a.link` | produkt-URL má `#/variant` fragment → striptnúť `urldefrag` |
| ODIMON | odimon.sk | BUXUS | `/vysledky-vyhladavania?term=<q>` | scope `.product-list__results`, karta = `a.product-card` (celý link), názov z `img[alt]/[title]` | statické HTML, cookie-gated (warmup GET homepage). Kód-search funguje (AH5→Alpenheat AH5 hore). 76 % má externalCode |
| TRIGONA | trigona.sk | Unisite | **SEO-path** `/eshop/searchstring/<q>/searchtype/all/searchsubmit/1/action/search/cid/0.xhtml` | karta `div.Product`, link `a[href*="/p-"]`, názov `.ProductName` | **POZOR**: `index.php?page=` form ticho redirectuje na generický výpis (nefiltruje!) — reálny search je SEO-path z JS autosuggestu. Statické HTML. 58 % má code |
| GRUBE | grube.sk | Shopware | `/search/?q=<q>` | karta `.product-box`, link `a[href*="/p/"]`→`/p/<slug>/<id>/`, názov **de-slugifikovaný z URL** | **JS/bot-gated**: requests dostane 0 boxov → gather cez **headless Playwright** (`scripts/gather_grube.py`, fetcher vstrekne page do gather linky; `systemd-run` detached + auto-resume kvôli ~40 min behu). 31 % má code |
| LUKO | luko.cz | Shoptet | `/vyhladavani/?string=<6-cifr.kód>` | scope `.products.products-block .product`, názov `[data-micro="name"]`, link `a.name`/`a.image` | **EXAKTNÝ kód v názve = bez AI** (viď gotcha nižšie). Statické SSR, cookie-gated (warmup). 35/38 napárovaných deterministicky. |

### Batch 2 (2026-06-29): 9 dodávateľov cez ZDIEĽANÉ platform-parsery

Veľa dodávateľov beží na rovnakej platforme → namiesto nového súboru na každého **použi zdieľaný platform-parser** (`src/parovanie/suppliers/*_generic.py`), len pridaj `config.SUPPLIERS` + zaregistruj v `client.PARSERS`:

| Zdieľaný parser | Platforma | Dodávatelia | Selektor / gotcha |
|---|---|---|---|
| `shoptet_generic.parse_search` | Shoptet | ZUBÍČEK (zubicek.cz), VIRGINIASHOP (virginiashop.sk), THERMVISIA (tenolix.cz) | scope `.products.products-block .product`, link `a.name`/`a.image`, názov `[data-micro="name"]`. **`?string=` NIE `?q=`** (q vráti homepage). zubicek je ČESKÝ (SK názvy) → slabý name-match, prísny AI |
| `prestashop_generic.parse_search` | PrestaShop | TTHUNT (tthunt.sk), LESONA (lesona.sk), LASTING (shop.lasting.eu) | scope `#js-product-list`, karta `article.product-miniature`, link+názov z `h1/h2/h3.product-title a` (téma sa líši!) alebo `.product-miniature__title a.link`; `#/variant` fragment → `urldefrag` |
| `woocommerce_generic.parse_search` | WooCommerce | LOVTEK (lovtek.sk), PYRA (pyra.eu) | `?s=<q>&post_type=product`. **DUAL MODE**: 1 presná zhoda → WooCommerce 301 na detail (`og:type=product`/`body.single-product`) → vráť 1 kandidáta z canonical+h1; inak scope `div.products` |
| `fomei.parse_search` | custom ASP.NET | FOMEI SLOVAKIA (fomei.com) | **`?ProductsSearch=` NIE `?search=`** (search je decoy → celý katalóg). scope `div.boxPl`, karta `div.plWrap[data-shop-product]`, link `a[href*="-detail-"]`, názov `h2.plWrapTitle` |

### Batch 3 (2026-07-03): 8 ďalších dodávateľov cez zdieľané parsery (žiadny nový kód)

Rovnaký vzor — len `config.SUPPLIERS` + `client.PARSERS`. Kľúč = export supplier `.upper()` (`load_rows`+`client` oba upper-case; accented `JŠ SERVIS`/`CHOCOLENKA` zachované). **Shoptet SK cesta `/vyhladavanie/`, CZ `/vyhledavani/`** — použi správnu podľa domény (zlá = 404).

| Parser | Dodávateľ (export) | eshop | search |
|---|---|---|---|
| shoptet_generic | JŠ SERVIS | chiruca.sk (SK, distribútor CHIRUCA — páruj podľa MODELU: TORCAZ, SPANIEL…) | `/vyhladavanie/?string=` |
| shoptet_generic | HUNTING24 | hunting24.cz (CZ, PARD/Alpina) | `/vyhledavani/?string=` |
| shoptet_generic | CITRADE | citrade.cz (CZ) | `/vyhledavani/?string=` |
| shoptet_generic | SOXLAND | soxland.sk | `/vyhladavanie/?string=` |
| shoptet_generic | WERRA | werra.cz (CZ) | `/vyhledavani/?string=` |
| shoptet_generic | RUTEX | **termovel.sk** (značka TERMOVEL) | `/vyhladavanie/?string=` |
| shoptet_generic | CHOCOLENKA | chocolenka.cz **/sk/** (SK verzia) | `/sk/vyhladavanie/?string=` |
| woocommerce_generic | TATRAGOAT | tatragoat.sk (téma Extra/Divi → grid `ul.products`) | `?s=<q>&post_type=product` |

- **`woocommerce_generic` fix (batch 3):** scope teraz `ul.products` OR `div.products` (Divi/Extra téma dáva kánonický `ul.products.columns-N`, starý parser ho míňal). LOVTEK/PYRA regression-clean (majú 0× `ul.products` → fallthrough).
- **DYNAX** (dynax.sk, PrestaShop, `?controller=search&search_query=`): config pridaný, ale eshop bol v ÚDRŽBE (503) → chýba fixtúra + live-overenie selektora → issue #76.
- Výsledok batch 3: gather 154 kandidátov → AI verify **113 matched** (prísne) → merge do review (2576→2730). ORBIS/HABO OBUV bez verejného eshopu → #75 (ako Hunting & Fishing #58).

Hunting & Fishing (49 produktov) **vynechaný — nemá verejný eshop** (gmail veľkoobchod), viď #58.

**FMS TRADE (6 produktov) = „Poľovníctvo Komár" na `polovnictvokomar.sk`** (FMS Trade s.r.o., Snina, IČO 48262927; NIE `fmstrade.sk` = iná firma, hliníkové odliatky). Platforma WooCommerce (WordPress). **POZOR — celý FRONT-END je za pluginom „UnderConstructionPage"**: `/`, `/shop/`, `/produkt/<slug>/` aj WooCommerce `?s=` search vracajú len 2.9 KB placeholder „Na stránke sa pracuje" → **zdieľaný `woocommerce_generic` (HTML `?s=`) tu NEFUNGUJE**, verejný eshop reálne zavretý. **ALE WooCommerce Store REST API je otvorené** (plugin `/wp-json/` negatuje): `GET /wp-json/wc/store/products?search=<q>&per_page=100` vracia JSON (name, sku, permalink `/produkt/…`, prices.price v centoch, images). Search **PODĽA MODELU/kódu, NIE značky** — `search=Grisport` → `[]`, `search=Cadria`/`Quatro` → presná zhoda (názvy sú `12811-33, Cadria`). Len ~46 produktov (väčšinou HIKMICRO termovízia + pár Grisport). Ak by sa robil parser: custom JSON fetch na REST, nie HTML. Zatiaľ `has_public_eshop=false` (zákazník sa naň nedostane).

**Dávkový postup (recon → build → gather → verify → merge):** recon 10 dodávateľov paralelne (Workflow, web research + curl) → postav 4 platform-parsery + fixtúry + testy → `gather_supplier.py SUPPLIER data/out_<slug>` paralelne (rôzne hosty) → `build_verify_input.py` → AI overenie (Workflow, dávky ~28, **prísne — URL kŕmi auto-objednávku, -1 radšej než zlý link**) → zlúč verdikty do `ai_verdicts.json` → `add_supplier_review_data.py data/out_<slug>` sekvenčne → `url_from_marketing_xml.py` → restart. Výsledok: 808 produktov, 457 AI-matched, zvyšok do ručného review poolu.
> **Bash gotcha pri zber-driveri:** `local slug="$1" out="data/out_$slug"` v JEDNOM `local` → `$slug` ešte NIE je viditeľný pri `out=` → prázdny slug, všetky zbery do `data/out_` (poprepisujú sa). Daj `out=` na samostatný `local`.

## Pridanie nového dodávateľa

1. **Recon** (curl s browser UA): nájdi search URL + či sú výsledky v statickom HTML alebo cez JS (ak JS → Playwright). Pozri `<meta name="generator">` / `x-powered-by` hlavičku na platformu. **Ak je to Shoptet/PrestaShop/WooCommerce → použi zdieľaný `*_generic` parser (vyššie), nepíš nový.**
2. `config.SUPPLIERS[<MENO>]` = base_url + `search_url_template` s `{q}` (kľúč = `supplier.upper()` z exportu — `load_rows` filtruje exaktne).
3. Len pre NOVÚ platformu: `suppliers/<meno>.py` s `parse_search(html, base_url) -> list[Candidate]` — **scopuj na kontajner výsledkov**, nie celý `a[href]` (inak chytíš nav + „odporúčané" karusel). Over selektor proti uloženej fixtúre.
4. Zaregistruj parser v `client.PARSERS`.
5. Fixtúry do `tests/fixtures/` (uložené HTML), testy bez živej siete.

## Pridanie dodávateľa do ŽIVEJ review appky — celá linka (overené na ODIMON)

Po recon+parser+config (vyššie) sa nový dodávateľ pridá do bežiacej appky bez narušenia existujúcich produktov ani rozhodnutí pracovníkov (decisions kľúčujú `supplier|pairCode`):

1. **Gather** do oddeleného out-dir: `scripts/gather_supplier.py ODIMON data/out_odimon` → `candidates.json` (top-8, kód-first). ~20 min / 300 produktov. Neprepíše živý `data/out/candidates.json`.
2. **Verify-input:** `scripts/build_verify_input.py data/out_odimon/candidates.json data/out_odimon/verify_input.json` — každý záznam nesie `pair_key` (a `idx` len ako poradie-pomôcka). Pre Workflow sprav SLIM (bez url) `verify_slim.json` — **`pair_key` neodstraňuj**, je to neutrálny kľúč, agent ho len echoje späť.
3. **AI-overenie = Workflow** (ultracode): dávky ~20, agent ČÍTA `verify_slim.json` (idx-rozsah sa nepasuje cez args — len rozsah!), pre každý produkt vyberie `chosen_i` (0–7) alebo `-1`, **prísne** (kód musí sedieť, -1 radšej než zlý guess — kŕmi auto-objednávku). Výstup zapíš do `data/out_odimon/ai_verdicts.json` (`{pair_key,idx,chosen_i,reason}` — **`pair_key` je POVINNÝ**, echo z `verify_slim.json`; merge naň páruje, nie na `idx`/pozíciu — #43). ODIMON: **248/295 matched (84 %)**.
4. **Merge do appky:** `scripts/add_supplier_review_data.py data/out_odimon` → pripojí ODIMON produkty do živého `data/out/review_data.json` (idempotentné, kľúč=`pair_key`), forestshop URL cez sitemap. Reindexuje `idx` (decisions kľúčujú `key`, nie idx).
5. `systemctl --user restart parovanie-web` → over cez `/api/products` (počet + `ai_chosen_url`).

## Párovanie — overený postup (kvalita)

Surové fuzzy-párovanie podľa názvu je ~50 % zlé (supplier model-názvy ≠ forestshop názvy). **Preto:**

1. `gather_candidates` zoberie **top-K (8)** z viacerých query variantov (kód, celý názov, názov bez generického slova, prefix+suffix token-skupiny) — `scripts/run_gather.py` → `data/out/candidates.json`. Plný beh ~2h (huntingshop ~1.1s/req), resumable cez checkpoint.
2. **AI overí každý produkt** (Workflow nad `verify_input.json`, dávky ~40, sonnet): vyberie správneho kandidáta z 8 alebo `-1` (žiadny) — prísne, lebo odkaz kŕmi auto-objednávku. Detail: `docs/runbooks/verification-workflow.md`.
3. `scripts/finalize.py` → import (`code;textProperty10`, cp1250, holá URL) + report s verdiktmi + unmatched.

Výsledok Betalov+Wetland: 784/969 napárovaných, 185 prísne zamietnutých.

### Skratka: dodávateľov kód v NAŠOM názve = exaktné párovanie, BEZ AI (LUKO)

Keď forestshop nosí dodávateľov vlastný kód priamo v názve produktu (LUKO: jediné 6-ciferné číslo, napr. „Košeľa LUKO ALPINA … **034230**"), je to **exaktný join-kľúč** — nie fuzzy. Vtedy NEpoužívaj top-K + AI Workflow; namiesto toho `scripts/gather_luko.py`:

1. `luko.extract_code(name)` vytiahne to jediné 6-cif. číslo (presne jedno, inak None).
2. Hľadaj na dodávateľovi PODĽA kódu (`/vyhladavani/?string=<kód>`), parsuj výsledkový blok.
3. `luko.choose_exact(code, cands)` = index kandidáta, ktorého **NÁZOV** obsahuje presný kód — práve jeden, inak `-1`.
4. Gather zapíše `candidates.json` + `ai_verdicts.json` ROVNAKÉHO tvaru ako AI-pipeline → `add_supplier_review_data.py` ich zhltne bez zmeny. (Žiadny verify-Workflow.)

LUKO: **35/38** deterministicky, 3 bezpečne nenapárované (2 ten istý kód = klasik+slim → nejednoznačné, 1 stiahnutý).

## Gotchas

- **Forestshop URL produktu — AUTORITATÍVNE z marketing XML `ORIG_URL`** (`scripts/url_from_marketing_xml.py`): marketingový export `productsMarketing.xml?patternId=-23` (URL+hash v `data/.shoptet_admin` `SHOPTET_MARKETING_XML_URL`, gitignored) má `<ORIG_URL>` = reálnu stránku produktu pre KAŽDÝ kód (aj `detailOnly`, ktoré v sitemape NIE sú). Match podľa **presného CODE** → žiadne hádanie, žiadny zlý link. Toto je hlavný zdroj `our_url` — spusti po `build_review_data`/`add_supplier_review_data` (XML je 59 MB, lxml `recover=True` kvôli malformed tokenom). **POZOR — ber LEN vlastný `<CODE>` SHOPITEMu + `<VARIANT>` kódy, NIE `el.iter()` cez celý SHOPITEM**: kódy v `<RELATED_PRODUCTS>` (cross-sell) patria INÉMU produktu → `setdefault` by namapoval cudzí kód na zlú URL (manažér: AH5 vložky `60648` → Nitecore P30, lebo 60648 je v P30 RELATED_PRODUCTS; opravilo 309 produktov). Opravilo 595 produktov čo odkazovali na `?string=` search (manažér hlásil) → 4 zostali None. **Sitemap resolver (`url_resolver.assign_urls`) je teraz len FALLBACK** pre tých pár bez XML matchu.
- **Sitemap fallback** — kánonický resolver je `parovanie.url_resolver` (`assign_urls`), **NEkopíruj logiku**. Match: exact názov-slug → ak nie, kandidáti = sitemap slugy ktorých tokeny ⊇ tokeny názvu. **Pozor — číslo v názve sa zahodí ako digit token**, takže „Moor Padded 367" a „393" majú rovnaké tokeny a kolidujú; rozlišuje sa **slugom z názvu OBRÁZKA** (`15233_…waistcoat-vesta` → vyberie `…-waistcoat`). Pravidlá: zlý link je horší než žiadny → nejednoznačné = `None` (UI padne na search); dva RÔZNE produkty nikdy nesmú zdieľať jednu URL (dedup ponechá najsilnejší match, zvyšok `None`); rovnaký názov-slug = ozajstný katalógový duplikát (ten istý produkt 2×) → obe nechá. ~645/969 priamych URL; zvyšok fallback `…/vyhladavanie/?string=<name>` (**`?q=` presmeruje na homepage, nepouživaj**). **Opraviť URL na existujúcom `review_data.json` bez plného rebuildu: `scripts/reresolve_urls.py`** (in-place, idempotentný, padne ak zostane kolízia).
- **`productVisibility: detailOnly`** = produkt dostupný LEN cez priamy odkaz (nie v sitemap/vyhľadávaní). Mnohé naše drop-ship produkty sú také → sitemap (3378) < katalóg (4513). URL sa nedá z poľa; `resolve_urls.py` overí slug-kandidátov cez HTTP 200 (názov-slug, bez generického slova, `polovnicke-/polovnicka-` prefix). Tak vznikne 791/969 priamych URL; zvyšok fallback `?string=`.
- **Shoptet slug môže byť ZASTARANÝ — páruj podľa NÁZVU, nie slugu (LUKO):** po premenovaní produktu si Shoptet ponechá starý slug v URL (kód `024245` žije na `…-model-022263/`), takže „kód v URL" dá falošný miss/zlý link. Aktuálny kód je vždy v **názve** karty (`[data-micro="name"]`) aj v H1 detailu. Preto `choose_exact` páruje na `code in candidate.name`, NIE na slug. Overené: všetkých 7 „slug≠kód" zhôd malo presný kód vo vlastnom H1 → 0 zlých linkov. Ten istý kód s 2 výsledkami (klasik/slim) = nejednoznačné → `-1` (radšej žiadny link, manažérov strach zo zlého linku).
- **Obrázky z produktovej stránky: ver LEN `og:image`** — gallery selektory ťahajú aj „súvisiace/odporúčané" produkty (zlé obrázky). og:image je spoľahlivo hlavný produkt.
- **Katalóg driftuje** (produkty sa prečíslujú/preskladnia) — zber je snapshot, kódy starnú. Po refreshi exportu spusti `resync_export.py`: rejoin každého produktu na AKTUÁLNY export podľa `(supplier, name)` → čerstvé kódy/obrázky/stav. Inak import sadne na zlý/neexistujúci kód.
- **„off" = NEpredajný** (hidden/blocked ALEBO availability vypredané/„predaj skončil"/nedostupné). **`detailOnly` NIE je off** (drop-ship, predajný cez link) — inak ukáže skladové produkty ako vypnuté.
- **`textProperty11=human matched`** v import_links → označí ručne overené; budúci zber tieto preskočí (filtruj v load step).
- **Durabilita rozhodnutí (webreview):** decisions kľúčuj **stabilným `supplier|pairCode`** (NIE array idx — prestavba dát by rozhodila), ukladaj na disk `data/out/decisions.json` (gitignored, prežije reštart/deploy), atomicky (tmp+rename). Assety verzuj `?v=N` nech reload ukáže novú UI.
- **Kódovanie cp1250** (Windows-1250) vstup aj výstup, `;`, CRLF — Shoptet import to čaká.
- `externalCode` = supplier kód, ale huntingshop ho často NEindexuje (napr. OB570 → 0 výsledkov) → query-rebrík/varianty nutné, nielen kód.
- `pair_key` musí byť scope-nutý dodávateľom (`sup|key`) — inak kolízia v checkpointe pri rovnakom pairCode u dvoch dodávateľov.
