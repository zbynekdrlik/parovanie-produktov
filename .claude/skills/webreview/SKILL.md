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

**GOTCHA (n8n MCP): `update_workflow` zapíše len DRAFT.** Aktívny (naplánovaný) beh ďalej používa STARÚ `activeVersionId`, kým nezavoláš **`publish_workflow`**. Po každej zmene uzla: `update_workflow` → `publish_workflow` → over `get_workflow_details` že `versionId == activeVersionId` a `activeVersion.nodes` má novú zmenu. Bez publish sa zmena navonok „neudeje". Over správu cez `test_workflow` s pinnutým HTTP node-om (Discord má credentials → je tiež pinnutý, NEpošle sa reálne) + `get_execution includeData` na `Sprava`.

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

## Živé Playwright overenie bez znečistenia dát

To-order flagy píšu do živých stores. Pri overovaní na živom webe **toggluj on→off** (skonči v pôvodnom stave) a potom over `data/out/<store>.json` že je zase `{}` (resp. pôvodný počet) — nikdy nenechaj reálnu objednávku označenú z testu.

## Gotcha — `gh pr edit` / `gh issue edit` na tomto repo ZLYHÁ (classic Project)

Repo má pripojený classic GitHub Project → `gh pr edit`/`gh issue edit` GraphQL mutácia padá na `Projects (classic) is being deprecated … (repository.pullRequest.projectCards)` a **nič nezmení** (titulok/telo ostanú staré). Použi REST:

```bash
gh api -X PATCH repos/zbynekdrlik/parovanie-produktov/pulls/<N> \
  -f title="…" -F body=@body.md --jq '.title'
```
(READ cez `gh pr view --json …` funguje normálne; len edit mutácia padá.)
