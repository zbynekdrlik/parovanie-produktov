# Webreview — kontrolný web (review + „Na objednanie")

Load BEFORE prácou na `webreview/` (Flask `app.py` + vanilla-JS SPA `static/app.js`,
`style.css`, `templates/index.html`). Dva taby: **Kontrola párovania** (review kariet)
a **Na objednanie** (otvorené objednávky → doobjednanie u dodávateľa).

## Per-riadkové stavy = 5 gitignored stores v `data/out/` (DÁTA MANAŽÉRA, ŽIVÉ)

Manažér si priamo na webe značí stav. Tieto súbory držia jeho ŽIVÚ prácu a **MUSIA prežiť každý deploy**:

| Súbor | Kľúč | Čo drží | Endpoint |
|---|---|---|---|
| `decisions.json` | product key | review párovania (good/manual/bad/unavailable + url) | `/api/decision` |
| `ordered_items.json` | `<orderCode>\|<itemCode>` | „objednané" check na to-order riadku | `/api/ordered` |
| `order_pairings.json` | forestshop `itemCode` | inline doobjednávacia URL (aj pre kódy MIMO review setu) | `/api/order-pair` |
| `waiting_items.json` | `<orderCode>\|<itemCode>` | „čaká sa" (aktívna objednávka, zatiaľ nenaskladniteľná) | `/api/waiting` |
| `supplier_assignments.json` | forestshop `itemCode` | doplnený dodávateľ pre riadok BEZ dodávateľa (regrupuje + zápis do eshopu) | `/api/order-supplier` |

**Kľúč per-PRODUKT (`itemCode`) vs per-RIADOK (`<orderCode>\|<itemCode>`):** `order_pairings` aj `supplier_assignments` sú per-PRODUKT (URL/dodávateľ je vlastnosť produktu) → platia pre VŠETKY riadky toho kódu. Preto JS save MUSÍ propagovať zmenu na všetky `ORDERS` s tým istým `itemCode` (`for (const x of ORDERS) if (x.itemCode===o.itemCode) x.assignedSupplier=…`) PRED re-renderom, inak sa preskupí len kliknutý riadok a súrodenci ostanú v starej skupine do reloadu. `ordered`/`waiting` sú per-RIADOK.

## Pridanie nového per-riadkového flagu — kopíruj `ordered`/`waiting` vzor (NEvymýšľaj)

1. **app.py**: `X = os.path.join(OUT, "x_items.json")` + `_load_x`/`_save_x` (atomický `os.replace` cez `.tmp`).
2. **app.py**: `@app.route("/api/x", methods=["GET","POST"])` — GET vráti mapu; POST `{key, x:bool}` pod `with _lock:` set/`pop`, `log.info`.
3. **app.py** `/api/orders`: `r["x"] = bool(x.get(r["key"]))`.
4. **app.js**: `let X = {}`; v `loadOrders` `X = (await (await fetch('/api/x')).json()).x || {}`; `saveX(key,on)` POST; v `renderOrderRow` trieda riadku `+ (X[o.key] ? ' x' : '')` + toggle button (synchrónne updatne DOM, async POST).
5. **style.css**: `.toorder-row.x { … }` + chip.
6. **index.html**: bumpni cache-bust `?v=N` na `style.css` AJ `app.js` (inak prehliadač drží starý JS/CSS).
7. **Testy**: unit (`monkeypatch.setattr(webapp,"X",str(tmp_path/"x.json"))` — endpoint persist + pole v `/api/orders`) + e2e (toggle on→off + reload persist, čistá konzola). E2e nový store si v teste **upracuj späť** (toggle off na konci) — fixture server je session-scoped, zdieľaný medzi testami.

Vstupy endpointov, čo píšu do CSV (kód/dodávateľ), MUSIA odmietnuť formula-injection: kód aj meno dodávateľa začínajúce `= + - @ \t \r` → 400; URL `^https?://`. **CSV sink prefixuje `'` cez `_csv_safe` — aj manuálny `/api/import` zip AJ nočný `upload-*` sink** (nočný píše naživo do eshopu, takže NESMIE byť slabšie chránený než zip).

**XSS — escapuj voľný text v KAŽDOM render-sinku, nie len v jednom.** `el(tag,cls,html)` používa `innerHTML`. Meno dodávateľa (voľný text manažéra) ide do 3 miest: 🏷️ menovka, **filter-button label** AJ **hlavička skupiny** — všetky 3 cez `escapeHtml(...)`. Escapnúť len menovku a zabudnúť na label/hlavičku = stored-XSS (našla to adversariálna revízia).

**Zápis do eshopu (write-back):** doplnený dodávateľ → 3. import súbor `import_suppliers.csv` (`code;pairCode;supplier`, vlastný stĺpec) v `/api/import` zipe + nočný `/api/n8n/upload-suppliers` (inkrementálny `uploaded_suppliers.json`, mirror `upload-pairings`). **`supplier` JE importovateľný stĺpec Shoptetu** — overené naživo 2026-06-29 (set `40256/L`=PAROVANIE-TEST → export read-back potvrdil → revert na ''), NIE textProperty-style tichý no-op. Pri akomkoľvek NOVOM zápisovom poli ale ZNOVA over import-settability naživo (export presence ≠ importable).

## Deploy = reštart služby (data/out PREŽIJE) — over počty pred/po

`systemctl --user restart parovanie-web` (WorkingDirectory == repo, `.venv/bin/python webreview/app.py`, `:8801`, verejne `parovanie-forestshop.newlevel.media`). `data/out` je gitignored → checkout/restart sa ho NEDOTKNE. **Vždy over data-safety**: spočítaj entries v `ordered_items.json`/`order_pairings.json`/`waiting_items.json`/`supplier_assignments.json` PRED a PO deployi (musia sedieť) a `/api/version` == nasadená verzia. Tunel/systemd detaily → `.claude/skills/deploy`.

## Discord notifikácie = n8n, NIE Flask (a draft/publish gotcha)

Web NEposiela Discord priamo. Nočné workflowy v n8n volajú endpointy a ony posielajú do Discordu:

- **`/api/n8n/upload-pairings`** (nahrá nové párovania → eshop internalNote, kľúč `uploaded_pairings.json`) ← n8n workflow **„Forestshop — Párovania → eshop"** (`YuDugCCOnwejRfva`, denne 21:00). Endpoint na KAŽDEJ ceste vracia súhrnné počty pre n8n: `count` (nové), `total_uploaded`, `total_products`, `remaining`, `review_url`, voliteľne `blocked` (napárované, čo sa nedalo nahrať — chýbajú variant kódy → notifikátor varuje namiesto ticha) — n8n `Sprava` node z nich poskladá **JEDNU** súhrnnú správu (nie detail za každý produkt). **Počty sú ohraničené na živý review set**: `total_uploaded` ráta len kľúče stále v `PRODUCTS` (inak by ratio prekročilo total, napr. „Spolu 105 / 100"); `_load_uploaded` coercne ne-dict stav na `{}`.
- **`/api/n8n/shoptet-import`** (reštok vypredané→skladom) ← iné workflowy.

**GOTCHA (n8n MCP): `update_workflow` zapíše len DRAFT.** Aktívny (naplánovaný) beh ďalej používa STARÚ `activeVersionId`, kým nezavoláš **`publish_workflow`**. Po každej zmene uzla: `update_workflow` → `publish_workflow` → over `get_workflow_details` že `versionId == activeVersionId` a `activeVersion.nodes` má novú zmenu. Bez publish sa zmena navonok „neudeje". Over správu cez `test_workflow` s pinnutým HTTP node-om + `get_execution includeData` na `Sprava`. **POZOR: pinne sa LEN to, čo dáš do `pinData` argumentu — Discord node sa NEpinne automaticky!** Incident 2026-06-29: test s pinnutými len HTTP nodmi POSLAL reálnu Discord správu s testovými číslami do Marekovho kanála. Ak nechceš reálny send, daj do `pinData` AJ `"Discord": [{"json": {}}]`. Pin je jednorazový (argument volania, do workflowu sa NEuloží — plánovaný beh ním nie je ovplyvnený).

**Bezpečnostný dlh (pre-existing):** HTTP node `Nahraj parovania` má bearer token (`N8N_IMPORT_TOKEN`) **natvrdo v hlavičke** — n8n hlási `HARDCODED_CREDENTIALS`. Lepšie cez n8n credential (httpHeaderAuth). Token žije aj v `data/.shoptet_admin` (gitignored).

## Dve úložiská párov → eshop `internalNote` (KTORÉ kam tečie)

| Store | Kľúč | Na eshop cez | review_data nutné? |
|---|---|---|---|
| `decisions.json` | review **`key`** = `SUPPLIER\|pairCode` | **nočne** `/api/n8n/upload-pairings` (číta LEN decisions) | **ÁNO** — pri štarte sa decision s kľúčom mimo review_data **TICHO zmaže** (`app.py` prune) |
| `order_pairings.json` | forestshop **kód** (ľubovoľný) | LEN manuálny `/api/import` zip (`order_pairing_rows`, excl. review-kódy) — **NIE** nočne | nie |

Pre auto-doobjednávanie (nočný upload) musí pár byť v `decisions.json` pod review kľúčom. `order_pairings` je len pre kódy mimo review setu a na eshop ide iba cez zip.

### Pridanie PRE-napárovaného produktu (mimo review setu) ako napárovaného

Keď máš hotový pár `forestshop_kód → supplier_url` pre produkt, ktorý v review_data nie je (napr. dodávateľ mimo configu — Knifestock, Deerhunter), a má sa ZOBRAZIŤ ako napárovaný + ísť nočne:

1. Postav minimálnu review položku: `{key:"SUPPLIER|pairCode", supplier, name, pairCode, variant_codes, our_url, our_images, current: current_of(...), candidates:[], ai_status:"unmatched", ai_chosen_url:"", ai_reason:""}`. Kód+názov+obrázky z marketing XML; cena cez `current_of` z exportu. `pairCode` z exportu (vlastný al. ktorýkoľvek variant), fallback názov; **zaruč unikátny `key`** (zráža sa pri prázdnom pairCode).
2. Pripoj do `review_data.json` (čerstvé čítanie, `idx = max+1`, atomicky `tmp`+`os.replace`, zvaliduuj parse).
3. **Rozhodnutie cez ŽIVÉ API** `/api/decision {key, status:"manual", url}` (zámok appky — manažér edituje súbežne; NEpíš decisions.json priamo).
4. **Restart** `parovanie-web` (PRODUCTS sa číta pri štarte). Poradie: review_data zapíš PRED reštartom → štartový prune decision NEzmaže (kľúč už je v review_data).

- **`status:"manual"` UŽ = napárované** (SPA: filter „Dobré" = `good||manual`; label `✓ Vybraný link`). **Nepoužívaj `good` pre ručný link** — `good` render je `p.ai_chosen_url`, nie `decUrl(p)` → ukázal by zlý/prázdny link.
- **Prázdny pairCode import TOLERUJE** — jednovariantové produkty (nože/termosky) majú v Shoptete pairCode prázdny aj v čerstvom exporte; `code;;url` sa nahrá OK (overené: stovky už-nahraných párov majú prázdny pairCode). Nie je to dôvod refreshovať export.
- Export refresh = len `products.csv` (`SHOPTET_EXPORT_URL` v `.shoptet_admin`, **má `&` → NEdá sa `source`-núť, ťahaj `grep|cut`**) obnoví CODE2PAIR (variantové produkty získajú pairCode); plný `resync_export.py` (mení review kódy) NETREBA len kvôli pairCode.

## Tab „🔎 Hľadať / opraviť" = celokatalógové vyhľadávanie + promote-on-pair

In-app verzia manuálneho promote vyššie — manažér nájde a napáruje produkt MIMO review setu rovno z webu (nemusí sa skriptovať). Pure logika žije v `src/parovanie/catalog_index.py` (otestované); `app.py` ju len drôtuje.

- **`CATALOG` index sa stavia PRI ŠTARTE** z `data/products.csv` v **tom istom cp1250 prechode** ako `CODE2PAIR` (`_load_catalog`), cez `catalog_index.build_catalog_index`. Zoskupené per **KĽÚČ = `pairCode` ALEBO (keď je pairCode prázdny) `code`** — jednovariantové produkty (čiapky/nože/svietidlá) majú v exporte PRÁZDNY pairCode; keby sme zoskupovali len per pairCode, ~2600 z nich by v indexe VÔBEC nebolo (bug „nehľadá všetky produkty" — index mal len 1804 z 4371). Riadok BEZ `code` sa preskočí (code je variant-id AJ fallback kľúč). Entry má `key` (pairCode-or-code), `pairCode` (reálny, môže byť `""`), `name_norm`/`name_words`, a **`search_blob_norm`** (znorm. blob VŠETKÝCH polí — viď nižšie) + `codes_norm`/`ext_norm`. `in_review` = `key` alebo **hociktorý variant code** je v `review_keys`; app posiela **coverage set = holé pairCodes + VŠETKY variant kódy** (`_review_cover`), nie `key` (C1) a nie len pairCode (jednovariantové by nikdy nesedeli).
- **`GET /api/search?q=`** = server-side cez `catalog_index.search_catalog` — **SUBSTRING nad blobom + RANKING** (nie word-boundary — to bolo „hrozné, nikdy nič nevyhľadá": `"hunter"`→0 lebo `Deerhunter` je substring v strede slova, a hľadalo len name/supplier/code). Blob agreguje (first-non-empty naprieč variantmi, HTML tagy zhodené): **name, supplier, VŠETKY variant kódy, externalCode, shortDescription, description, manufacturer, ean, productNumber, categoryText(2..8)**. Dotaz sa rozdelí na SLOVÁ, **KAŽDÉ musí byť substring blobu** (AND, nezávisle od poradia) → NÁJDE veci; **ranking** zoradí kvalitu: celé slovo názvu=5, prefix slova názvu=4, substring názvu/kódu/externalCode=3, inde v blobe=1; bonusy: presný kód/externalCode +100, kód/ext substring +20, presný názov +50, súvislý v názve +10. Zoradené score DESC → kratší názov; default **top 100**. `<2` znaky → `[]`. `_search_result` vracia **`key`** (pairCode-or-code identita, klient tým páruje) + `pairCode` (back-compat) + `idx`/`our_url` (in-review match cez pairCode ALEBO zdieľaný variant code, nie key==key — C1) + **`price`/`stock`/`state`** + **`paired_url`** (good/manual; GRUBE→.de; decisions load RAZ per request). Klik na produkt v appke otvorí **plnú `renderCard`**. **POZOR: Shoptet `stock` môže byť ZÁPORNÝ (backorder)** — ks zobrazuj len `> 0`.
- **`POST /api/search-pair {key,url}`** (legacy `{pairCode}` funguje ako fallback): ak produkt nie je v review_data, **POVÝŠI** ho — `build_promoted_entry` (promoted `key = catalog entry key = pairCode-or-code`, takže jednovariantový produkt s prázdnym pairCode dostane reálny unikátny kľúč = svoj code, ktorý `link_rows` prečíta a jeho variant_codes idú na eshop `code;;url`) + `current` snapshot + best-effort `our_url`; pripojí do `PRODUCTS`, atomicky zapíše `review_data.json`; potom `decision {status:"manual", url}` cez živý store. Existujúci produkt (match cez pairCode ALEBO zdieľaný code) sa NEpovyšuje duplicitne — decision ide pod jeho REÁLNY key. URL musí byť `^https?://` (inak 400), neznámy `key` → 404.
- **GOTCHA — promote `current` MUSÍ čítať stĺpec `productVisibility`, NIE `visibility`** (`_current_for_entry` → `current_of`). Žiadny `visibility` stĺpec v exporte neexistuje → zlý názov nechá `vis=""` a skryté/blokované produkty nikdy nedostanú stav 3 (snapshot drift bug). `_current_for_entry` matchuje riadok cez pairCode ALEBO variant code (jednovariantové majú prázdny pairCode). `current_of` arg-poradie/stĺpce zrkadli `build_review_data`/`resync_export`. Chýbajúci/nečitateľný export → `{}` (karta sa renderuje bez nášho stavu, nikdy 500).
- Dodávateľ sa odvodí z **domény URL** (`supplier_from_url`: grube.de/grube.sk → GRUBE, inak match na `SUPPLIERS[*].base_url` host, neznámy → `""`). `our_url` best-effort z marketing XML (`build_code2url` podľa variant kódu, cached; akékoľvek zlyhanie → `None`).

## e2e gotcha — `saveDecision` render je SYNC pred `await fetch` → serializuj POSTy

`saveDecision(p,status,url)` synchronne updatne `DECISIONS` + `render()` a AŽ POTOM `await fetch('/api/decision')` → POST odletí ONESKORENE. V e2e kde klikáš viac tlačidiel za sebou (napr. 📦 → ↩ Vrátiť → 🚫), sa POST z predošlej akcie (`undo`) stihne vypustiť **do `expect_response` okna ĎALŠEJ akcie** → zachytíš zlý request (`assert 'undo' == 'discontinued'`). Lokálne (rýchle) prejde, na CI (pomalšie) padne = flaky. **Fix: KAŽDÝ `/api/decision` POST konzumuj vo VLASTNOM `with page.expect_response("**/api/decision")` — vrátane každého `↩ Vrátiť` (undo)** — pred ďalšou akciou; medzi akciami `wait_for_selector` na cieľový stav (nie `sleep`/`wait_for_timeout`).

## Živé Playwright overenie bez znečistenia dát

To-order flagy píšu do živých stores. Pri overovaní na živom webe **toggluj on→off** (skonči v pôvodnom stave) a potom over `data/out/<store>.json` že je zase `{}` (resp. pôvodný počet) — nikdy nenechaj reálnu objednávku označenú z testu.

## Gotcha — `gh pr edit` / `gh issue edit` na tomto repo ZLYHÁ (classic Project)

Repo má pripojený classic GitHub Project → `gh pr edit`/`gh issue edit` GraphQL mutácia padá na `Projects (classic) is being deprecated … (repository.pullRequest.projectCards)` a **nič nezmení** (titulok/telo ostanú staré). Použi REST:

```bash
gh api -X PATCH repos/zbynekdrlik/parovanie-produktov/pulls/<N> \
  -f title="…" -F body=@body.md --jq '.title'
```
(READ cez `gh pr view --json …` funguje normálne; len edit mutácia padá.)
