# GRUBE — per-veľkosť párovanie + zápis kódu do Shoptet (návrh)

> Dátum: 2026-06-29. Stav: schválený návrh (brainstorming). Ďalej: `writing-plans`.
> Špeciálne správanie LEN pre dodávateľa **GRUBE**; ostatní dodávatelia bez zmeny.

## Cieľ (1 veta)

Pre každú VEĽKOSŤ forestshop produktu od GRUBE získať jej grube per-veľkosť kód (grube
`itemId`) a (a) zapísať ho do Shoptet `externalCode` (per variant), (b) nastaviť grube.de
objednávaciu linku do `internalNote`, (c) zobraziť per-veľkosť kód + .de linku v
webreview tabe „Na objednanie" — aby manažér skopíroval presný veľkostný kód do
e-mailovej objednávky pre GRUBE (GRUBE nemá B2B auto-objednávanie).

## MVP rozsah (YAGNI)

- **V rozsahu:** per-veľkosť kód (itemId) → `externalCode` (write-back), grube.de objednávacia
  linka → `internalNote`, zobrazenie kódu+linky v „Na objednanie", a to LEN pre veľkosti ktoré
  sa dajú **jednoznačne a bezpečne** napárovať (písmenkové veľkosti S–5XL, over nižšie).
- **Auto-match v MVP:** písmenkové veľkosti (S–5XL — bundy/fleecy/vesty) AJ číselné nohavice
  (`46–64`) — oboje overené naživo na grube.de ako exact-string zhoda. Vzácne formáty
  (`39/40` rozsahy, `Gr. X`, XS) padnú na link-only automaticky (fail-safe).
- **Odložené (NIE v MVP):** scraping .de dostupnosti/skladu (manažér to chce neskôr — „vidieť
  stav v Nemecku"), nočný cron pre externalCode, .de vyhľadávací gather. Po implementácii MVP
  **založiť ako GitHub issue** (no-dropped-work), nie zahodiť.

## Naživo overené fakty (Playwright, 2026-06-29)

1. **grube.sk a grube.de zdieľajú productId AJ per-veľkosť itemId.** Líši sa len lokalizovaná
   dostupnosť/sklad. Príklad: `grube.de/p/x/154773/` → presmeruje na
   `…/percussion-winterjacke-grand-nord/154773/#itemId=1547734523`.
2. **grube.de URL = `/p/<slug>/<productId>/#itemId=<itemId>`.** Navigácia na `/p/x/<productId>/`
   (placeholder slug) sa presmeruje na kánonickú .de stránku — slug je odvoditeľný z productId.
3. **Stránka má všetky per-veľkosť itemId** vlastného produktu (prefix = productId, napr.
   `154773` → `1547734523`, `1547734598`, …). itemId = `<productId><4-ciferný-suffix>` →
   dĺžka == `len(productId)+4`.
4. **Cross-sell pollution:** stránka má aj `a[data-track-id="associatedProduct"]` kotvy s itemId
   INÝCH produktov (napr. `6165695125` na produkte `154773`) → **filtruj striktne:**
   `itemId.startswith(productId) AND len(itemId)==len(productId)+4`. (Tá istá lekcia ako
   RELATED_PRODUCTS pollution v marketing XML.)
5. **Veľkostné radio-labely na grube.de** (`label.radio-input`, `for="var[import:...]_N_1"`):
   - Oblečenie (písmenkové): `S, M, L, XL, XXL, 3XL, 4XL, 5XL` — **identické s forestshop**
     (pairCode 395: rovnaká sada) → zhoda bez normalizácie.
   - Nohavice (číselné): `46, 48, 50, …, 64` PLAIN integery (produkt 384893, 10 labelov == 10
     itemId) — **identické s forestshop `variant:Veľkosť číslo`** → exact-string zhoda.
   - Forestshop už mal `3848935751` (= veľkosť 56 tu) ako externalCode → potvrdzuje itemId =
     objednávkový kód aj pri číselných.
   - **Rozsahové (`39/40`) a `Gr. X` formáty NEoverené** → exact-string nezhoda ich automaticky
     hodí na link-only (fail-safe, netreba ich pre-validovať).
6. **Rozsah dát:** 145 GRUBE review položiek, 90 viacveľkostných, manažér napároval 27 (.sk url
   v `decisions.json`, productId-parsovateľný 27/27 = 100 %), ~118 ešte nenapárovaných, 4
   `discontinued` (bez url). Z 90 viacveľkostných: **73 má prázdny `externalCode`** (čisté
   doplnenie), 17 má kód (13 spoločný článkový `89-677-01`, 2 už per-veľkosť itemId).

## Architektúra a tok dát

```
                       (existujúce decisions.json — manažérove .sk párovania, productId zdieľaný)
                                            │
            ┌───────────────────────────────┼───────────────────────────────┐
            ▼                                ▼                               ▼
  A. .de URL normalizácia          B. per-veľkosť extrakcia        C. veľkosť forestshop
  (productId-rebuild,              (Playwright .de detail,          variantu (z variant:* stĺpca,
   strip ?q + #itemId,             {size→itemId}, filter prefix      NIE z code suffixu;
   kontinuálne/idempotentne)        +dĺžka, count-assert)            multi-os → link-only)
            │                                │                               │
            └────────────────┬───────────────┴───────────────┬──────────────┘
                             ▼                                ▼
                D. data/out/grube_codes.json        (len jednoznačný letter-size match)
                {variant_code: {itemId,size,deUrl,productId}}   durable, never-pruned
                             │
        ┌────────────────────┼─────────────────────────┐
        ▼                    ▼                          ▼
  E. import_externalcode   F. internalNote .de       G. webreview „Na objednanie"
  .csv (GRUBE-only,        link (link_rows            kód-čip (copy) + .de link
  snapshot+revert,         normalizácia GRUBE-only)   (/api/orders pripojí podľa itemCode)
  importability-gate)
```

Zdroj per-veľkosť kódu je **vždy čerstvá extrakcia z .de stránky** (nie `#itemId` fragment
z .sk url — ten je pre jednu veľkosť a nedôveryhodný).

## Komponenty

### A. grube .sk→.de URL normalizácia (productId-rebuild, kontinuálna)

**NIE string-swap** `replace('grube.sk','grube.de')` — 24/27 url má `?q=morakiv#itemId=…` a
2/27 má HTML-entity-zmrvený slug (`pracovn-yacute-n-ocircz-…`). String-swap by zapísal
zmrvený slug + cudzí query + jednoveľkostný fragment.

- `def to_grube_de(url: str) -> str | None`: regex `r'grube\.(?:sk|de)/p/[^/]+/(\d+)/?'`
  (search), vytiahne productId, vráti `f'https://www.grube.de/p/x/{productId}/'` (zahodí slug,
  query, fragment). Nezhoda vzoru (manažér vložil kategóriu/search url) → `None` (link-only,
  bez extrakcie).
- **Idempotentná a kontinuálna:** aplikuje sa pri KAŽDOM zápise GRUBE decision (v `link_rows`
  pre `internalNote` + v extrakčnom kroku), NIE jednorazový prepis `decisions.json`. .de url
  sa mapuje na seba → legacy aj novo-zadané .sk párovania tečú na .de rovnako. (Manažér je
  uprostred roboty; nové párovania zapisuje webreview `/api/decision` ako .sk — normalizér
  ich pochytí každý cyklus.)
- **Scope:** no-op pre ne-grube hosty (ostatným dodávateľom nesmie prepisovať `internalNote`).
- `discontinued` (url=="") → preskoč.

### B. per-veľkosť itemId extrakcia (nový parser + gather, Playwright .de)

- **Parser** `src/parovanie/suppliers/grube_variants.py`:
  `def parse_variants(html: str, product_id: str) -> dict[str, str]` → `{size_label: itemId}`
  z jednej vyrenderovanej .de detail stránky. Pravidlá:
  - Veľkostné štítky z `label.radio-input` (text = label, poradie z `for="…_N_1"`).
  - itemId z DOM/JS konfigurátora; **filter:** `itemId.startswith(product_id) AND
    len(itemId)==len(product_id)+4` (vlastný produkt, NIE cross-sell `associatedProduct`).
  - **Count-assert:** počet vlastných itemId == počet veľkostných radiov; ak nesedí → vráť `{}`
    (link-only pre tento produkt, loguj). Selektor PRIPNI na uloženú .de detail fixtúru +
    offline test (vrátane cross-sell kotvy v fixtúre, dôkaz že cudzie itemId sú vylúčené).
- **Gather** `scripts/gather_grube_itemids.py` reuse `PlaywrightFetcher` z `gather_grube.py`
  (parametrizuj jeho `wait_for_selector` — `.product-box` je len pre search grid; detail stránka
  ho nemá → čaká na buy/konfigurátor widget, inak zhorí 8 s/produkt).
  - Vstup: GRUBE produkty so spárovanou .de url (productId z `to_grube_de`).
  - **Vlastný checkpoint** `data/out_grube/itemids_checkpoint.json` keyed `productId` —
    incrementálny, re-run preskočí hotové, spracuje len novo-napárované. ~40 min trieda behu →
    `systemd-run` detached + auto-resume (ako `gather_grube.py`).
  - **404 / delisted / zmenený productId** → nechaj produkt bez kódov, **pokračuj v dávke**
    (loguj), nikdy nepadni. Flag review položku `grube produkt nenájdený na .de — znova spáruj`.
  - **Re-extrahuj** keď sa productId v decision url zmení (manažér prepáruje).
  - `productId` musí byť striktne `\d+` pred stavbou navigačnej url.
  - Po presmerovaní **over** že kánonická .de url má rovnaký productId (guard proti zlému
    presmerovaniu na iný produkt) pred prijatím itemId.

### C. Rozlíšenie veľkosti forestshop variantu (z `variant:*` stĺpca, NIE z code suffixu)

**Code suffix `/<SIZE>` je nespoľahlivý** (538/568 sedí; rozbíja sa na `997/S77` =
accessory id bez veľkosti, `62093/39/40` = slash vo veľkosti, `61933//M` = double-slash,
disambiguátory `3XL2`/`L3`). **Veľkostný STĹPEC je vždy čistý → jediný zdroj pravdy.**

Per-row (validované proti exportu, indexy v `data/products.csv`):

| Stĺpec | rows GRUBE |
|---|---|
| `variant:Veľkosť (všetko)` | 432 |
| `variant:Veľkosť číslo` | 136 |
| `variant:Bunda veľkosť` | 134 |
| `variant:Nohavice veľkosť` | 134 |

```
populated = [c for c in (Bunda, Nohavice, Veľkosť(všetko), Veľkosť číslo) if row[c].strip()]
if Bunda and Nohavice populated:   → DVOJOSOVÁ (komplet bunda+nohavice) → LINK-ONLY (nepíš kód)
elif len(populated) == 1:          → size_label = row[populated[0]]
else (0):                          → jednoveľkostný produkt (žiadny size rozmer)
```

- **Per-row, NIE per-product** (2 produkty 437/794 miešajú stĺpce naprieč vlastnými variantmi).
- 50 zero-size rows = standalone produkty (prázdny pairCode), žiadny nezdieľa pairCode so
  sized rowom → 0 stĺpcov bezpečne znamená „bez veľkosti".
- **Dvojosové komplety (134 rows, napr. produkt 12311 bunda×nohavice) → LINK-ONLY**, jeden
  grube itemId nereprezentuje dvojicu (bunda 3XL / nohavice 46…64). Web ukáže
  `dvojrozmerná veľkosť — zadaj grube kód ručne`.
- **In-code farba (napr. `61141/Z/48`, `61141/C/50`) — `variant:Farba*` je pre GRUBE prázdny**,
  farba žije len v kóde. Variant s ne-veľkostným stredným tokenom (`/Z/`, `/C/`) → multi-os →
  LINK-ONLY (inak by sa zapísal kód ignorujúc farbu → zlá farba).

### C2. Match veľkosti forestshop ↔ grube.de (EXACT-string, fail-closed)

**Princíp: exact-string zhoda je sama o sebe fail-safe** — nezhoda → link-only, NIKDY zlá
veľkosť. Preto netreba per-typ validačnú bránu; bežné formáty (písmenkové, číselné) sa napária,
vzácne (`39/40`, `Gr. X`, `XS` ak grube nemá) automaticky padnú na link-only.

- **Normalizuj** forestshop aj grube label: trim + uppercase pre písmenká; voliteľne `2XL→XXL`,
  `XXXL→3XL` (defenzívne, keby produkt použil inú notáciu — overené produkty ich už majú zhodné).
  Číselné nechaj ako string (`"48"`).
- **EXACT post-normalizačná rovnosť** label-u → vezmi grube itemId pre TEN label. Žiadny
  fuzzy/nearest fallback (`XS` nikdy nesnapne na `S`; `48` nikdy na `50`).
- **Kolízie-prostá kontrola:** assert že žiadne dva rôzne grube labely nenormalizujú na jeden →
  fail loud (ochrana proti tichej fold-strate).
- Match LEN proti itemId reálne prítomným pre presný label; nezhoda (vrátane asymetrického
  rozsahu kde forestshop má 5XL/XS a grube nie) → prázdno + link.
- **Pokrytie MVP:** písmenkové (bundy/fleecy/vesty) + číselné nohavice (`46–64`) sa auto-párujú.
  `39/40` rozsahy, `Gr. X`, kids-cm-ak-grube-formátuje-inak padnú na link-only automaticky.

### C3. Kardinalita (matica — kód zapíš LEN pri jednoznačnom mapovaní)

| forestshop | grube | akcia |
|---|---|---|
| 1 veľkosť | 1 itemId | zapíš ten itemId |
| N veľkostí (letter) | N itemId (label match) | per-veľkosť itemId pre zhodné labely |
| N veľkostí | 1 itemId | **LINK-ONLY** (nejednoznačné, pravdepodobne zlý pár) |
| 1 veľkosť | N itemId | **LINK-ONLY** |
| dvojosové (bunda+nohavice) | matica itemId | **LINK-ONLY** |
| veľkosť bez grube labelu (XS, číslo, rozsah) | — | **LINK-ONLY** |

- **Base code NIE je unikátny kľúč** (base `60656` → pairCode 796 AJ 521; `61141` → 1277 AJ
  437). Extrahuj a zapisuj striktne podľa review item / pairCode + presného celého variant code,
  NIKDY podľa prefixu base-code. Test: dva pairCody s rovnakým base sa spracujú nezávisle.

### D. Durable store `data/out/grube_codes.json`

- Shape: `{ "<forestshop_variant_code>": {"itemId": str, "size": str, "deUrl": str,
  "productId": str} }`. Kľúč = forestshop variant code.
- **Umiestnenie `data/out/`** (deploy-preserved, NEVER-pruned — ako `order_pairings`/
  `supplier_assignments`), NIE `data/out_grube/` (disposable workdir). Atomický zápis
  `tmp`+`os.replace` (vzor `_save_supplier_assign`). Re-gather ho nesmie zmazať.
- Producent = build krok: pre každý spárovaný GRUBE produkt: productId z `to_grube_de` →
  `parse_variants` → pre každý forestshop variant produktu resolvni size (C) → letter-match (C2)
  → zapíš `{variant_code: {...}}` LEN pri jednoznačnom letter-size matchi (preskoč
  multi-os/číselné/unmatched/N:1). Konzument: E (write-back), G (order app).
- `resync_export.py` keď prestavia `variant_codes`, musí **rebuildnúť** join na `grube_codes`
  podľa `key` (nie podľa exportu — ten nemá grube dáta), aby store neodkazoval na zahodené kódy.

### E. Write-back `externalCode` (mirror `supplier_rows`, GRUBE-only, bezpečne)

Vzor 1:1 podľa `supplier_rows` (`import_builder.py:144-161`) + wiring `/api/import`
(`app.py:433-434`):

- `EXTERNALCODE_HEADER = ["code", "pairCode", "externalCode"]`.
- `def externalcode_rows(grube_codes, code2pair, exclude_codes=None)`: iteruje
  `grube_codes.json`, emit `[code, code2pair.get(code,""), itemId]`; **dedup každý code raz**
  (first-wins, ako `link_rows` — duplicitný code → Shoptet zruší CELÝ import); **dropni riadok s
  prázdnym `externalCode`** (prázdna bunka v prítomnom stĺpci → Shoptet ZMAŽE existujúci kód);
  skip `exclude_codes`.
- **GRUBE-only guard + test:** každý emitovaný code patrí produktu so `supplier=="GRUBE"`
  (assert; fail loud) — Shoptet matchuje len podľa `code`, takže zdieľaný code s ne-grube
  produktom by inak prepísal cudzí `externalCode`. (Dnes 14059 distinct codes == 14059 rows,
  nula kolízií — ale guard, nie predpoklad.)
- **`_csv_safe` na sinku** (klonuj suppliers cestu `app.py:934`, NIE pairings `app.py:823` čo ho
  vynecháva) **+ validuj pri zdroji** že itemId je čisto číselný (own-productId-prefixed digits);
  nečíselné → dropni, nezapíš.
- Dialekt: `code;pairCode;externalCode`, **UTF-8 s BOM** (`utf-8-sig`), `;`, CRLF, QUOTE_MINIMAL
  (`writer.shoptet_writer`). Vlastný stĺpec → ostatné polia (`internalNote`, `supplier`, stav,
  ceny) sa NEdotkne (Shoptet importuje len prítomné stĺpce; žiadna „nahradiť prázdne" voľba na
  reálnom formulári — overené v shoptet skille).
- **Doprava: MANUÁLNY zip `/api/import` + `scripts/shoptet_import.py`** (berie zálohu katalógu +
  read-back), **NIE nočný cron** (zatiaľ). Nočný `/api/n8n/upload-externalcode` (+ inkrementálny
  `data/out/uploaded_externalcodes.json`, re-push pri zmenenom itemId ako `new_supplier_keys`)
  sa pridá LEN po (a) overení importability a (b) ustálení overwrite-politiky — inak by night-one
  ticho a bez review prepísal kódy.
- **Importability gate = PRVÁ implementačná úloha** (pozri H). Bez nej steps E sa NEspoliehaj.
- **Pre-flight:** rozšír `shoptet_import.classify_row` o typ `externalcode` + `_print_plan` riadok
  + `externalCode` do `EXPECTED_HEADER`, aby manuálny import-plán nebol opaque „iné: N".

### F. `internalNote` ← grube.de link (GRUBE-only normalizácia)

- V `link_rows` (alebo wrapper) pre GRUBE produkty: `internalNote = to_grube_de(decision_url)`
  (productId-rebuild, strip `?q`+`#itemId`). Ne-grube produkty bez zmeny (no-op normalizér).
- Unit test: legacy `…/268279/?q=morakiv#itemId=2682798474` → `https://www.grube.de/p/x/268279/`.
- **Over** že `internalNote` `.de` linka nezapne nechcene auto-reorder/restock n8n automatizáciu
  (GRUBE nemá B2B). Skontrolovať n8n filter (`internalNote` startuje http) — buď GRUBE vylúčiť,
  alebo .de linku brať ako manuál-only. (Read-only check, spravím sám pri impl.)

### G. webreview „Na objednanie" — kód-čip + .de link

- **`/api/orders`** (`app.py:584-592`, vedľa `pairUrl`): pripoj `r["grubeItemId"]` a
  `r["grubeDeUrl"]` z `grube_codes.json` podľa `r["itemCode"]` (forestshop variant code). Server
  validuj `grubeDeUrl` na `^https?://`.
- **`renderOrderRow`** (`app.js:~362`, gated na grube + `o.grubeItemId`):
  - Copy-čip: `el('span','to-grube'); chip.textContent = o.grubeItemId` (**`.textContent`,
    NIE el() 3. innerHTML arg**); `chip.onclick = () => navigator.clipboard?.writeText(...)`
    (graceful no-op keď clipboard API chýba — app beží cez plain-HTTP tunel, secure-context
    nie je istý).
  - .de link: `el('a','to-link'); a.href=o.grubeDeUrl; a.target='_blank'; a.rel='noopener';
    a.textContent='🇩🇪 .de'`.
- **CSS** `.toorder-row .to-grube{…cursor:pointer}` (mirror `.to-suptag`); **cache-bust** bumpni
  `?v=N` na `style.css` AJ `app.js` v `index.html`.
- **Mixed-scheme transparentnosť:** ukáž ktoré veľkosti majú overený grube kód vs len link (aby
  manažér nebol zmätený že časť má per-veľkosť kód a časť nie).
- XSS: kód aj label cez `textContent`/`escapeHtml`; url len server-/client-guarded `^https?://`.

### H. Importability gate (PRVÁ úloha — môže zrušiť celú E vetvu)

`externalCode` importability je **NEoverená** (textProperty10 bol kedysi tichý no-op; export
presence ≠ importable; `supplier` sa dôveroval až po live set→export-readback). PRED akýmkoľvek
spoliehaním na E:

1. Zachyť pôvodný `externalCode` jedného bezpečného sentinel grube variantu.
2. Importni `code;pairCode;externalCode` s testovou hodnotou cez `scripts/shoptet_import.py`
   (berie zálohu katalógu).
3. **Over čerstvým pattern-14 exportom** (NIE import Logom — `_read_result` vracia stale výsledok
   na veľkých async importoch) že nová hodnota je na TOM variante AJ že súrodenec-variant je
   nezmenený (dôkaz **variant-level**, nie len product-level).
4. Revert na zachytenú pôvodnú hodnotu; over revert (fail loud ak zlyhá). Crash-window: capture
   PRED write.
- **Ak NIE importable / len product-level** → spec dropne E (steps a/4/5) a ship LEN F
  (internalNote .de link) + G (webreview zobrazenie). Tento fallback je definovaný, nie afterthought.

## Bezpečnosť dát (overwrite je schválený, ale vratný a pozorovateľný)

- **Snapshot PRED hromadným zápisom:** ulož všetkých 142 (code, externalCode, pairCode) do
  vratného súboru (referencuj `export_<ts>.csv` zálohu z `shoptet_import._backup_export` ako
  rollback artefakt).
- **No-match → žiadny riadok** (nikdy prázdny `externalCode` cell) → existujúce kódy sa
  nestratia. Prepíš LEN keď sa itemId pozitívne a jednoznačne napáril.
- **73 prázdnych viacveľkostných** = čisté doplnenie. **17 s existujúcim kódom** (13 spoločný
  článkový, 2 už itemId) prepíš per-veľkosť itemId (schválené), ale snapshotnuté/vratné +
  pozorovateľné v G, takže manažér vidí výsledok a vie vrátiť ak je kód zlý.
- **Over importability + landing** vždy čerstvým exportom, nie import Logom.

## Edge-case matica (zhrnutie pravidiel)

| Edge-case | Pravidlo |
|---|---|
| Dvojosový komplet (bunda+nohavice) | LINK-ONLY |
| In-code farba `/Z/`, `/C/` | LINK-ONLY (multi-os) |
| Code suffix nie je veľkosť (`997/S77`, `62093/39/40`, `61933//M`) | veľkosť LEN zo stĺpca, nikdy z code |
| Písmenkové (S–5XL) + číselné nohavice (46–64) | auto exact-match → itemId |
| XS / rozsahové (`39/40`) / `Gr. X` formát | exact nezhoda → LINK-ONLY (automaticky) |
| N forestshop : 1 grube itemId | LINK-ONLY |
| grube discontinued / .de 404 | bez kódov, flag „znova spáruj", pokračuj |
| cross-sell itemId | filter prefix==productId AND dĺžka==len(pid)+4 |
| existujúci externalCode + no-match | nechaj (žiadny prázdny zápis) |
| zdieľaný code grube×ne-grube | guard supplier=="GRUBE", fail loud |
| duplicitný code | dedup first-wins + warn keď code→>1 itemId |

## GRUBE-only gating

Každá GRUBE-special operácia scope-ovaná `supplier=="GRUBE"` (a/alebo host `grube.*`):
externalCode builder iteruje len GRUBE produkty; `to_grube_de` normalizér je no-op pre
ne-grube hosty; guard/test že `import_externalcode.csv` obsahuje len codes s
`product.supplier=="GRUBE"`.

## Testovanie (offline fixtúry, bez živej siete)

- `tests/fixtures/grube_de_detail_*.html` — uložená .de detail stránka (multi-size + cross-sell
  kotva) → `parse_variants` vráti správnych N itemId, vylúči cross-sell, count-assert.
- Size-resolution unit testy: `997/S77`, `62093/39/40`, `61933//M`, `12311` komplet,
  `61141/Z/48`, disambiguátor `3XL2`, 0-size simple — každý → očakávaný label / LINK-ONLY.
- `.sk→.de` normalizér: legacy url s `?q`+`#itemId` + entity-slug → kánonická `/p/x/<pid>/`.
- `externalcode_rows` invariants (mirror `test_import_builder`): dedup, drop-empty, GRUBE-only,
  `_csv_safe`, číselný itemId.
- webreview unit (`/api/orders` pripojí grubeItemId/grubeDeUrl) + e2e (čip sa zobrazí, copy
  no-op bez clipboard, .de link, čistá konzola; e2e store upracuj späť).
- Importability gate je **živý** krok (nie offline test) — manuálny, s read-back+revert.

## Odložené → založiť ako GitHub issue po MVP (no-dropped-work)

1. Auto-match rozsahových (`39/40`) a `Gr. X` veľkostí (MVP ich má fail-safe na link-only;
   po validácii formátu doplniť normalizáciu) + dvojosové komplety (bunda+nohavice).
2. Scraping .de dostupnosti/skladu („vidieť stav v Nemecku").
3. Nočný `/api/n8n/upload-externalcode` cron (+ `uploaded_externalcodes.json`) — až po overení
   importability + ustálení overwrite-politiky.
4. .de vyhľadávací gather (decouplnutý — MVP gather ostáva .sk, productId zdieľaný).

## Otvorené predpoklady (over pri implementácii)

- grube.de detail stránka exposuje per-veľkosť itemId v DOM/JS (pripni selektor na fixtúru).
- .sk↔.de itemId rovnosť over na ≥1 MULTI-size oblečení naživo (zatiaľ validované hlavne na
  morakniv nožoch = jednoveľkostné).
- itemId je objednávkový kód aj pri 13 článkovo-kódových produktoch (2 produkty už majú per-veľkosť
  itemId → silná indícia áno; potvrdí sa pozorovaním v G po nasadení).
