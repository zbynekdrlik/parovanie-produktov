# Vyhľadávanie produktov + oprava párovania — design

**Dátum:** 2026-06-30
**Stav:** schválený dizajn (2 product otázky zodpovedané používateľom)

## Cieľ

Manažér vie v review webe **vyhľadať produkt** (podľa názvu / nášho kódu / dodávateľa) a **opraviť jeho párovanie**, ak ho zle napároval — vrátane produktov, ktoré v appke ešte nie sú (nikdy sa nepárovali). Pre nájdený produkt použije ten istý párovací panel čo už existuje: vyber kandidáta alebo vlož odkaz na produkt u dodávateľa.

## Rozhodnutia používateľa (AskUserQuestion, 2026-06-30)

1. **Mechanizmus prepárovania = „Kandidáti + ručný odkaz"** — appka ukáže už uložených kandidátov (ak sú) + pole na ručný odkaz. ŽIADNE živé hľadanie u dodávateľa (mimo rozsahu).
2. **Rozsah hľadania = „Aj nenapárované / nové produkty"** — hľadanie siaha na CELÝ katalóg eshopu, nielen na ~2555 produktov teraz v appke.

## Čo už existuje (zistené, NEprerábať)

- **Prepárovanie je hotové:** karta má panel `resolutionPanel(p)` → zoznam `p.candidates` (klik „Vybrať") + ručné pole „Uložiť URL" → `saveDecision(p,'manual',url)` → `POST /api/decision` → atomický zápis do `decisions.json` (kľúč = `key`, čo je holý `pairCode` pre BETALOV/WETLAND, inak `SUPPLIER|pairCode`).
- **Propagácia na eshop:** `import_builder.link_rows(PRODUCTS, decisions, CODE2PAIR)` iteruje `PRODUCTS` (= `review_data.json`) → `[code, pairCode, url]` riadky → Shoptet `internalNote` (nočný upload / manuálny import). **Produkt, ktorý NIE JE v `PRODUCTS`, sa ticho preskočí.**
- **Štart appky už číta celý export:** `data/products.csv` (55 MB, 14059 variantov, cp1250) sa otvára pri štarte kvôli `CODE2PAIR` (len stĺpce `code`,`pairCode`). Súbor obsahuje aj `name`,`supplier`,`productVisibility`,`availability*`,`price`,`stock`,`defaultImage`… v tom istom prechode.
- **Chýba:** akékoľvek vyhľadávanie/filter podľa textu. Všetkých 2555 sa načíta naraz, filtruje sa len podľa stavu (tlačidlá).

## Architektúra

### 1. Katalógový vyhľadávací index (in-memory, pri štarte)

Rozšíriť existujúci štartový prechod cez `data/products.csv` (ten istý čo stavia `CODE2PAIR`) tak, aby zároveň postavil **`CATALOG`**: `dict[pairCode → {pairCode, name, supplier, variant_codes:[...], image, in_review:bool}]`. Zoskupené podľa `pairCode` (14059 variantov → ~4500 produktov). Nula extra I/O — ten istý súbor sa už streamuje po riadkoch.

- `name`/`supplier`/`image` = z prvého variantu daného `pairCode` (defaultImage; názvy sú per-pairCode rovnaké).
- `in_review` = `pairCode` (alebo jeho `key`) je v `PRODUCTS` (review set).
- Diakritika: index drží aj `name_norm` = NFKD-fold + lowercase pre vyhľadávanie bez diakritiky.
- Čistá funkcia `build_catalog_index(rows) -> dict` v `src/parovanie/` (unit-testovateľná), appka ju volá pri štarte.

### 2. Endpoint `GET /api/search?q=<dotaz>`

Server-side filter nad `CATALOG` (NEposielame 14000 produktov do prehliadača):

- Match = case+diacritic-insensitive **substring** v `name` ALEBO presná/substring zhoda v `variant_codes` ALEBO substring v `supplier`. (Žiadny fuzzy ranking — YAGNI.)
- Prázdny/krátky `q` (<2 znaky) → `{results: []}` (neslúžiť celý katalóg).
- Vráti top **50** výsledkov: `{pairCode, name, supplier, codes, image, in_review, our_url}`. Pre `in_review` produkt sa pripojí jeho `idx`/`key` (aby web otvoril existujúcu kartu s kandidátmi); pre nenapárovaný len holé info.
- `our_url` pre nenapárovaný produkt: lazy cez marketing-XML `code2url` (ak `data/out/marketing.xml` existuje), inak `None` (karta padne na vyhľadávací odkaz). Authoritatívne podľa `code`.

### 3. „Povýšenie" nenapárovaného produktu (promote-on-pair)

Keď manažér páruje produkt, ktorý NIE JE v `PRODUCTS`:

- `POST /api/search-pair {pairCode, url}` (alebo rozšírené `/api/decision`): server postaví **minimálnu review_data entry** v presnom tvare (kľúče: `idx, supplier, name, pairCode, variant_codes, our_images, ai_status="unmatched", ai_chosen_url="", ai_reason, candidates:[], our_url, key=pairCode, current`) — `current` cez `export_helpers.current_of`, `our_url` lazy (marketing XML), `variant_codes`/`name`/`image` z `CATALOG`.
- **Dodávateľ sa odvodí z domény vloženého odkazu** (`grube.sk/.de→GRUBE`, `wetland.sk→WETLAND`, `huntingshop.eu→BETALOV`, `odimon.sk→ODIMON`, `trigona.sk→TRIGONA`) cez mapu z `config.SUPPLIERS[*].base_url`; neznáma doména → `supplier=""`. (Dôležité len pre GRUBE `.de` normalizáciu v `link_rows`.)
- Entry sa **idempotentne** pridá do `review_data.json` (kľúč=`key`, atomicky `os.replace`+`_lock`) a do in-memory `PRODUCTS` (bez reštartu). Reuse logiky budovania entry z `scripts/add_supplier_review_data.py` (NEhand-rollovať tvar).
- Potom sa zapíše rozhodnutie do `decisions.json` ako pri ktoromkoľvek `manual` párovaní → `link_rows` ho odteraz emituje na eshop.

### 4. Frontend — nový tab „🔎 Hľadať / opraviť"

- Tretí tab popri „Kontrola párovania" / „Na objednanie".
- Vyhľadávací input (debounce ~250 ms) → `fetch('/api/search?q=')` → render kompaktných riadkov výsledku (obrázok, názov, dodávateľ, kódy, badge `v appke` / `nenapárované`, aktuálny odkaz ak je).
- Klik na riadok → otvorí **ten istý `resolutionPanel`**:
  - `in_review` produkt → naplno (kandidáti + ručný odkaz), dáta z existujúcej karty.
  - nenapárovaný → len ručné pole „Vlož odkaz na produkt u dodávateľa" (kandidáti prázdni) + auto-detekovaný dodávateľ (zobrazený, needitovateľný v MVP).
- Po uložení in-place re-render riadku (badge → `napárované ✓`, ukáž odkaz), bez scroll-resetu.
- Cache-bust `?v=N` bump na `style.css` AJ `app.js`.

## Tok dát

```
products.csv (štart) ── build_catalog_index ──► CATALOG (in-memory)
                                                   │
web: napíš dotaz ──► GET /api/search?q= ──────────┘ ──► výsledky
klik výsledok ──► resolutionPanel
   ├─ in_review:    POST /api/decision {key,manual,url} ─► decisions.json
   └─ nenapárovaný: POST /api/search-pair {pairCode,url}
                       ├─ promote → review_data.json (+PRODUCTS)
                       └─ decisions.json
decisions.json ──► link_rows(PRODUCTS, decisions, CODE2PAIR) ──► internalNote (eshop)
```

## Spracovanie chýb

- `q` prázdne/<2 znaky → prázdne výsledky (nie chyba).
- `search-pair` s neznámym `pairCode` (nie v `CATALOG`) → 404.
- URL vstup MUSÍ prejsť `^https?://` (inak 400) — formula-injection guard ako pri `order-pair`. `code`/URL do CSV cez `_csv_safe`.
- Promote nikdy neprepíše existujúcu review entry s rozhodnutím — idempotentne podľa `key`; ak už entry existuje, len doplní/ponechá.
- Súbeh: všetky zápisy `with _lock` + atomický `os.replace` cez `.tmp`.

## Testy

**Unit (bez siete):**
- `build_catalog_index`: zoskupenie podľa pairCode, name_norm bez diakritiky, in_review flag, prázdne/duplicitné kódy.
- search filter: zhoda názov/kód/dodávateľ; diakritika („bunda" nájde „Bundá"); prázdny dotaz → []; limit 50.
- supplier-from-URL: grube.sk→GRUBE, wetland.sk→WETLAND, neznáma→"".
- promote: nový pairCode → správny tvar review entry + decision + `link_rows` ho emituje; idempotent (2× promote = 1 entry); no-dup `code`.
- URL guard: nie-`http` → 400.

**E2E (Playwright, fixture server):**
- Otvor „Hľadať" tab → napíš dotaz → výsledok → otvor → vlož platný odkaz → Ulož → reload → párovanie ostalo; badge `napárované`. Čistá konzola (0 chýb/0 varovaní).
- Nenapárovaný produkt: promote → po reloade je v zozname/kontrole.
- E2E si po sebe upracuje (fixture server zdieľaný session-scope).

## Mimo rozsahu (YAGNI)

- Živé hľadanie u dodávateľa (Playwright-in-Flask) — používateľ zvolil kandidáti+ručný odkaz.
- Fuzzy/relevance ranking — stačí substring.
- Úprava cien/skladu/viditeľnosti cez hľadanie.
- Editácia auto-detekovaného dodávateľa v UI (MVP: len zobrazený).

## Reuse + riziká

- Reuse: `resolutionPanel`/`saveDecision`/`/api/decision`, `export_helpers.current_of`, atomický `_save_*`+`_lock`, `renderOrderRow` in-place re-render vzor, entry-builder z `add_supplier_review_data`.
- Riziko: `review_data.json` je živý súbor manažéra → promote je len aditívny + idempotentný (bezpečné). `CATALOG` sa stavia z `products.csv` ktorý sa obnovuje nočne (n8n) → index čerstvý po reštarte; manuálne pridané párovania prežijú (decisions/review_data v `data/out`, gitignored).
- Durabilita cez deploy: `data/out/*` sa pri reštarte/checkout nedotkne; in-memory `PRODUCTS` sa pri promote dopĺňa, `CATALOG` sa prestaví pri štarte.
