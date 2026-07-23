# Shoptet eshop — import/export pravidlá (forestshop.sk)

Load BEFORE čímkoľvek okolo exportu z eshopu / importu do eshopu / polí produktov.
Toto sú dohodnuté pravidlá — nepýtaj sa ich nanovo.

## Export (z eshopu)

- **CSV** (pattern 14): `https://www.forestshop.sk/export/products.csv?patternId=14&partnerId=3&hash=<HASH>` — kódovanie **Windows-1250 (cp1250)**, `;`, hodnoty v úvodzovkách, viacriadkové. ~14 000 riadkov (variantov).
- **XML** (productsComplete, patternId=-5) — má SHOPITEM/CODE/IMAGES, ale **NEMÁ produktové URL** (ani CSV). URL sa rieši cez sitemap + HTTP-overenie slugov (viď skill `suppliers`).
- Hash v exportoch je **partner credential** — NIKDY do gitu (placeholder `<HASH>`); reálny iba lokálne.
- Kľúčové stĺpce: `code` (kód variantu), `pairCode` (zoskupuje varianty produktu), `name`, `externalCode` (kód dodávateľa), `supplier`, `productVisibility`, `availabilityInStock`/`availabilityOutOfStock`, `stock`, `price` (s DPH, EUR), `standardPrice`, `purchasePrice`, **`internalNote`** (privátna poznámka — drží objednávaciu linku), `textProperty10..20` (VEREJNÉ info-parametre, CSV importom sa nedajú meniť).

## Import (do eshopu) — DVA súbory (NIE jeden!)

**Draho zaplatené poučenie (naživo na ostrom eshope):** Shoptet **PREPÍŠE prázdnu bunku** v uvedenom stĺpci (NIE „nechá tak"). Na import formulári NEEXISTUJE voľba „nahradiť prázdne hodnoty". Pravidlo:
- Stĺpec, ktorý NIE JE v súbore → Shoptet ho NEZMENÍ.
- Stĺpec, ktorý JE v súbore ale má prázdnu bunku → Shoptet ho **ZMAŽE**.

Preto sa import **rozdelí podľa polí** — každý súbor/riadok nesie LEN stĺpce, ktoré naozaj nastavuje:

- **`import_links.csv`** = `code;pairCode;internalNote` — objednávacia linka do **privátneho poľa `internalNote`** (žiadne state stĺpce → vis/stock/avail ostanú nedotknuté).
- **`import_states.csv`** = `code;pairCode;productVisibility;stock;availabilityInStock;availabilityOutOfStock` — stavy (nie skladom / nebude predávať; žiadny internalNote → linka nedotknutá).
- **Kódovanie UTF-8 (s BOM)** (cp1250 → mojibake `č`→`è`), `;`, CRLF. **VŽDY `code` + `pairCode`.** Kódovanie aj párovanie (podľa `code`) Shoptet rieši sám.
- **`VÝSLEDOK: upravené=N` je PRODUKTOVÁ úroveň, nie variantová** — 15 variantov 2 produktov → `upravené=2/3` (nie 15). NIKDY never tomu číslu; over zápis vždy **čerstvým export read-backom** (stiahni export, porovnaj `externalCode`/`internalNote` po `code`). `spracované` = počet riadkov, `zlyhania=None` = 0 nezhôd kódov.

## TRI STAVY produktu (overené z dát eshopu — DOHODNUTÉ)

| Stav | productVisibility | stock | availability | internalNote (privátna linka) |
|---|---|---|---|---|
| **1. Skladom** | `visible` | >0 | `Skladom`/prázdne | URL (ak napárované) |
| **2. Nie je skladom** (dočasne) | `visible` | 0 | `Vypredané` | — |
| **3. Už sa nebude predávať** (link pre Google) | `detailOnly` | 0 | `Predaj výrobku skončil` | — |

`detailOnly` = LEN cez priamy odkaz (nie v kategóriách/vyhľadávaní) → stránka ostáva pre Google, ale produkt nie je v ponuke (stav 3, ~4 600 v katalógu). „Vypredané"=dočasne; „Predaj výrobku skončil"=už nikdy.

**„availability" = DVE polia a menia sa SPOLU (CEO nález 2026-07-14):** `availabilityInStock` (text pri stock>0) + `availabilityOutOfStock` (text pri stock=0). Prepnutie stavu musí nastaviť OBE — reštok, ktorý menil len `availabilityInStock`, nechal produkt po vypredaní fiktívnych kusov zobrazovať staré „Vypredané" z `availabilityOutOfStock` (CEO stav „vypredané" má totiž obe polia `Vypredané`; zdravé skladom produkty majú obe `Skladom`).

**OFF→ON cyklus je CROSS-MODULE KONTRAKT — nezlom ho potichu (#97):** re-enable „musí spoľahlivo fungovať" závisí od reťaze v TROCH moduloch: `import_builder.state_rows` (vypnutie → `visible`+`0`+`Vypredané`/`Vypredané` = state 2) → `restock_skladom.compute_candidates` (číta PRESNE tie stĺpce `productVisibility`/`availabilityInStock`/`availabilityOutOfStock` a chytí state-2) → `import_builder.restock_rows` (zapnutie → OBE polia `Skladom`+`visible`+stock, NIKDY prázdna bunka — prázdna by nechala Vypredané). Tvar, ktorý disable ZAPÍŠE, je presne to, čo detekcia ČÍTA späť — zmena jednej strany bez druhej ticho rozbije re-enable. Uzamknuté end-to-end v `tests/test_reenable_roundtrip.py` (disable→detect→re-enable→state 1→idempotent + n8n `sanitize_csv` cesta). Pri zásahu do ktoréhokoľvek z tých 3 modulov ten test SPUSTI/AKTUALIZUJ.

## Význam polí + konvencie (DOHODNUTÉ — overené naživo)

- **`supplier` (Dodávateľ)** = product-level pole, **JE importovateľné cez CSV** — overené naživo 2026-06-29 (import `code;pairCode;supplier` na `40256/L` → fresh export read-back ukázal novú hodnotu → revert na ''). NA ROZDIEL od `textProperty*` (tie sú v exporte, ale import ich ignoruje). Vlastný split súbor `import_suppliers.csv` (`code;pairCode;supplier`) — nemieša sa s linkom/stavom. Pozn.: export-presence sám osebe NEDOKAZUJE importovateľnosť (viď textProperty omyl) — pri novom poli vždy over set→export-readback.
- **`internalNote` (Interná poznámka)** = **PRIVÁTNE** pole (zákazník NEVIDÍ). Drží **objednávaciu linku** (URL na produkt u dodávateľa) pre automatizáciu doobjednávania. **Importuje sa cez CSV (overené).** Samotná prítomnosť URL = signál „napárované" → žiadny extra marker netreba. (Pozn.: pôvodne tam bol zdroj objednávania ako doména `betalov.sk`/`rhodani.com` — plná URL je presnejšia.)
- **`textProperty1..20`** = **VEREJNÉ** info-parametre (zobrazia sa zákazníkovi), formát „Názov;Hodnota". **CSV importom sa NEDAJÚ nastaviť** (Shoptet ich ignoruje) — NEpoužívaj na linku ani marker. Toto bol pôvodný omyl (textProperty10/11 sa nikdy nezapísali).
- **Napárovaný (link):** `import_links.csv` → `internalNote`=URL. NIČ iné — vis/stock/avail sa NEdotýkaj (prázdne by ich zmazalo).
- **Nie je skladom (stav 2):** `import_states.csv` → `visible`, `stock=0`, `Vypredané`.
- **Už sa nebude predávať (stav 3):** `import_states.csv` → `detailOnly`, `stock=0`, `Predaj výrobku skončil`.

## DVE „→ Skladom" automatizácie — RÔZNY spúšťač, RÔZNY zápis (nezameň)

Máme DVE automatizácie, čo prepínajú produkt na Skladom. Nezlučuj ich a nepýtaj sa
zdroj nanovo:

| Automatizácia | Kľúč | Spúšťač (zdroj) | Zápis (import_builder) |
|---|---|---|---|
| „Vypredané → Skladom" (#108) | `restock_skladom` | DODÁVATEĽ má opäť skladom (scrape `supplier_stock.json`, čerstvé ≤48 h) | `restock_rows` → `RESTOCK_COLS` = OBE avail Skladom + visible + **fiktívny stock 5** (produkt mal 0 reálnych ks) |
| „Máme skladom → Skladom" (#98) | `stock_skladom` | NÁŠ Shoptet sklad `stock > 0` (zelené pásy/„máme" v admine) | `skladom_rows` → `SKLADOM_COLS` = OBE avail Skladom + visible, **stock NEPÍŠE** (reálny sklad je autoritatívny, fiktívny by pretiekol) |

- **`stock_skladom` (#98) NEPOTREBUJE dodávateľské dáta** — číta len export (`stock` stĺpec). `compute_candidates(csv_text)` v `src/parovanie/stock_skladom.py`: kandidát = `stock>0` AND `productVisibility=='visible'` AND `export_helpers.state_of==2` (zdieľaný klasifikátor). Ten filter automaticky preskočí stav 3 (`detailOnly`/discontinued/hidden = VEDOMÉ off rozhodnutie manažéra → nikdy neprepnúť, ani so zvyškovým skladom) aj stav 1 (idempotencia). Denne 06:45, default DISABLED (píše do eshopu, #93).
- **VERIFY-FIRST dáta (2026-07-23, reálny export 14066 variantov):** `stock>0` = 730 variantov; z nich `state_of!=1` = 65 → **50 stav 3 (ukončené, NEPREPÍNAŤ)** + **15 stav 2 (visible+Vypredané+reálny sklad = jediné legitímne auto-skladom ciele)**. Pozor: `availabilityInStock=="Skladom"` NIE je použiteľný zdroj signálu — také produkty sú UŽ stav 1, niet čo prepínať; jediný zmysluplný signál nezrovnalosti je `stock>0`.
- **`SKLADOM_COLS` zámerne VYNECHÁVA `stock`** (na rozdiel od `RESTOCK_COLS`): chýbajúci stĺpec Shoptet NEZMENÍ (nie ZMAŽE — mazanie je len pri PRÍTOMNOM prázdnom stĺpci), takže reálny kladný sklad ostane nedotknutý. Fiktívna hodnota by mohla predať viac než máme.

## productVisibility hodnoty

`visible`, `detailOnly` (predajný cez priamy odkaz; so „Predaj skončil" = stav 3), `hidden`, `blocked`, `cashDeskOnly`, `blockUnregistered`. `detailOnly` samo o sebe NIE je „vypnutý".

## Generovanie importu

`scripts/build_decisions_import.py` → `import_links.csv` + `import_states.csv` (web `/api/import` → zip oboch) z rozhodnutí (`decisions.json`) cez `import_builder.link_rows` / `state_rows` (otestované).

## Auto-import do adminu (script)

`scripts/shoptet_import.py` — prihlási sa do Shoptet adminu a nahrá import CSV namiesto ručného klikania. Čistá logika (načítanie údajov, pred-letová kontrola CSV, parser výsledku) je v `src/parovanie/shoptet_import.py` (unit testy, bez prehliadača); browser riadi tenká Playwright obálka v scripte.

**VEĽKÝ import → OBÁVAJ SA 120s browser timeoutu, chunkuj (#156):** `scripts/shoptet_import.py:296` čaká `page.wait_for_url(re.compile(r"import-produktov/log"), timeout=120000)` — Shoptet spracúva veľké CSV na SERVERI dlho a presmeruje na log až potom. **~1195 riadkov > 120 s → zlyhanie „Timeout 120000ms".** Root-cause fix (NIE väčší timeout — `no-timeout-band-aids`): **rozdeľ dávku na časti ≤`IMPORT_CHUNK_ROWS` (300) riadkov**, jeden `run_import` na časť. V `webreview/app.py` je na to zdieľaný **`_import_rows_chunked(all_rows, header, dry, prefix, csv_safe=?, timeout=900)`** — volaj ho pod `_import_lock` (drž lock cez CELÚ dávku), release v `finally` (žiaden zaseknutý lock). PRVÁ zlyhaná časť zastaví dávku; `success_codes` = kódy z ÚSPEŠNÝCH častí → uploaded stav zapíš LEN pre kľúče, ktorých VŠETKY kódy sú v `success` (idempotent + resumable + #49 ochrana partial-key). Používa `_do_upload_pairings`, `_do_upload_suppliers`, AJ (od #158) `run_restock_skladom` a `/api/n8n/shoptet-import` — všetky 4 zápisové cesty do Shoptetu idú cez ten istý chunker, žiadna necháva jeden veľký `run_import` na celú dávku. `n8n_shoptet_import` má drobnú odlišnosť: `sanitize_csv(raw_path, out_path)` najprv zapíše CELÚ sanitizovanú dávku ako audit súbor (`out_path`, prežíva aj po behu), a AŽ POTOM sa `out_path` prečíta späť (`csv.reader`) na riadky pre `_import_rows_chunked` — nečíta sa surový upload dvakrát, ale kanonický už-sanitizovaný súbor.

- **Secret:** `data/.shoptet_admin` (chmod 600, `data/` je gitignored — NIE v gite). Kľúče: `SHOPTET_ADMIN_URL`, `SHOPTET_USER`, `SHOPTET_PASS`, `SHOPTET_EXPORT_URL` (pattern-14 export s hash, na zálohu). Hodnoty NIKDY do gitu/skillu.
- **Beh:** `PYTHONPATH=src .venv/bin/python scripts/shoptet_import.py --file <CSV> [--dry-run] [--yes] [--headful]`. Importuj OBA súbory zvlášť: `import_links.csv` aj `import_states.csv`. Playwright: `pip install -r requirements-import.txt && playwright install chromium` (NIE v CI).
- **Poistky (poradie):** pred-letová kontrola CSV (UTF-8, `code`+`pairCode`, rozpis link/nie-skladom/nebude-predávať) → potvrdenie (`--yes` preskočí) → **záloha exportu** do `data/backups/export_<ts>.csv` (bez úspešnej zálohy sa NEimportuje) → login → upload + **nastaví a read-back overí bezpečné parametre** (viď nižšie) → spustí → prečíta skutočný výsledok z **Logu**. Audit (screenshot + log) do `data/out/shoptet_import_<ts>.*`.

### Admin deep-link na objednávku (overené naživo 2026-07-22)

Orders export NEMÁ interné admin `id` objednávky a prehľad objednávok
(`/admin/prehlad-objednavok/`) NEMÁ GET filter — `?query=`/`?code=` sa TICHO ignorujú
(filter je POST s `__csrf__`). **Jediný funkčný GET deep-link = globálne vyhľadávanie:**
`/admin/vyhladavanie/?string=<orderCode>&src=orders` → vráti presne tú objednávku aj
s linkom na detail (`objednavky-detail/?id=<interné id>`). Používa ho tab
„Nevyzdvihnuté zásielky"; použi ho pre KAŽDÝ budúci link do adminu podľa kódu objednávky.

### Reálny Shoptet import formulár (overené naživo)

- **URL:** `/admin/import-produktov/` (POZOR: NIE `produkty-import`). Login: placeholder `E-mail` / `Vaše heslo`, tlačidlo `Prihlásenie`.
- **Formulár NEMÁ voľby kódovania / „nahradiť prázdne" / párovania.** Shoptet **auto-detekuje kódovanie** (náš `utf-8-sig` BOM → UTF-8) a **páruje podľa `code`** automaticky (stĺpec v hlavičke). Stĺpec s prázdnou bunkou → Shoptet ho **ZMAŽE** (preto sú importy rozdelené — viď „Import — DVA súbory").
- **Jediné rizikové voľby na formulári** (script ich nastaví + read-back overí): radio **„Nemeniť produkty a varianty, ktoré nie sú obsiahnuté v importovanom súbore"** (bezpečný default — NIKDY „Zmazať…") + checkbox **„Zmeňte adresu URL produktu podľa názvu produktu" = VYPNUTÉ**.
- **Súbor sa MUSÍ nahrať cez file-chooser** (`expect_file_chooser` + klik „Vyberte súbor") — nastavenie skrytého `input[name=file]` Shoptet widget nezaregistruje (zostane „Povinné pole").
- **Spustenie:** tlačidlo `data-testid="buttonImport"` → presmeruje na `/admin/import-produktov/log/`.
- **Výsledok (Log):** SK „Spracované: N. Upravené: K. Zlyhanie variantov: M.", auto-import býva CZ „Zpracováno/Upraveno". Parser berie len najnovší riadok a „Zlyhanie: N" uprednostní pred prózou „skončil s chybou".
- **Export na zálohu:** hash je **per-pattern aj per-formát**. Plný export = šablóna **„vsetko" (patternId 14), formát CSV** — URL+hash vyčítaj na `/admin/export-produktov/` (vyber šablónu + formát → „Verejný odkaz"). Ten ide do `SHOPTET_EXPORT_URL`.

## n8n denný export → Data Table (pre sklad/ceny/objednávky)

Workflow **„Forestshop products export"** (`pi2vURB2wwGwTBPG`, n8n.newlevel.media) beží **denne o 00:00** a upsertne produkty do Data Table **„ForestShop"** (`OR5AnysuvKFXY8j6`, projekt `KWh7xR1bd5EyraM5`). Z neho idú ďalšie automatizácie (porovnanie skladu, cien, doobjednávanie).

- **Dve vetvy:** XLS `patternId=11` (má `code`,`pairCode`,`guid`,`internalNote`,ceny,sklad…) + marketingový XML `patternId=-23` (**59 MB!**, len kvôli `ORIG_URL`→`url` a `id`→`editUrl`; join na `guid`). Hashe v URL sú **partner creds — placeholder `<HASH>`, NIE do gitu**.
- **Filter (DOHODNUTÉ):** `internalNote` **začína `http`** → píše len **spárované** (variant má reorder link). `internalNote` je **zmiešané pole**: ~2388 variantov (489 produktov) má plnú URL (náš link), ~4444 má len starý textový zdroj (`tthunt.sk`,`betalov.sk`,`Trigona`…) — tie **nie sú linka**, filter ich odreže.
- **Význam polí v tabuľke:** `internalNote` = **reorder link** (URL na produkt u dodávateľa, z neho objednávajú automatizácie). `url` = vlastná stránka produktu na forestshop.sk (z XML `ORIG_URL`) — NEzamieňať. Upsert match = `code` (len technický kľúč).
- **Pasce n8n (overené naživo):**
  - `update_workflow` uloží len **DRAFT** (`versionId` ≠ `activeVersionId`) → **MUSÍŠ `publish_workflow`**, inak schedule ide po starom.
  - Beh cez MCP: **`executionMode:"manual"` reálne spustí** pipeline (aj na schedule-trigger workflowe); **`"production"` je na schedule-trigger no-op** (nezapíše). Manuálne behy sa **neukladajú** (`saveManualExecutions=false`) → over cez čítací workflow nad tabuľkou, nie cez `get_execution`.
  - `saveDataError=none` → zlyhania nikde nevidno; odporúčanie: zapnúť „Save failed executions" v nastavení workflowu (workflow-level, cez MCP sa nedá — len UI).
  - Beh je **ťažký**: 59 MB XML, **~3,5 min**, počas neho n8n padá na **502/520** (preťaženie). Poistka = `retryOnFail` na HTTP uzloch. Zľahčenie = doplniť `url` do XLS exportu a zrušiť XML (zatiaľ NErobené — user zvolil len poistky).
  - dataTable `deleteRows` má `dryRun` (vracia **2× riadky** — before+after stav); upsert **neprunuje** → nespárované staré riadky čisti `deleteRows` kde `internalNote isEmpty` (jednorazovo).
  - Read tabuľky: nový workflow v **tom istom projekte** (`KWh7xR1bd5EyraM5`), inak Data Table nevidí. Žiadny MCP „read rows" tool — čítaj cez `dataTable get returnAll`.

## n8n → spustenie importu (HTTP endpoint na dev1)

n8n (cloud) spustí Shoptet import cez **HTTP endpoint na dev1**: `POST https://parovanie-forestshop.newlevel.media/api/n8n/shoptet-import` (route vo webreview Flask appke `webreview/app.py`, za existujúcim `parovanie-tunnel`). n8n je v cloude, creds+prehliadač sú na dev1 → cloud volá verejnú adresu tunela.

- **Auth:** `Authorization: Bearer <token>`, constant-time (`hmac.compare_digest`). Token v `data/.shoptet_admin` kľúč **`N8N_IMPORT_TOKEN`** (gitignored, chmod 600). Bez tokenu endpoint odmietne všetko (401).
- **Telo:** CSV (multipart `file` alebo raw body; raw MUSÍ mať Content-Type napr. `text/csv` — form-encoded telo Flask zparsuje do `request.form` a endpoint vidí „empty body"). Endpoint **whitelistne stĺpce** na `import_builder.RESTOCK_COLS` = `code, pairCode, productVisibility, availabilityInStock, availabilityOutOfStock, stock` — **nikdy ceny/názvy** (n8n feed nesie navyše `name`,`purchasePrice`,`ourPrice`… → Shoptet by ich inak prepísal). **Feed, ktorému whitelistovaný stĺpec CHÝBA, dostane 400** (prázdna bunka by pole v Shoptete ZMAZALA — sanitize nič nedopĺňa). Potom spustí `scripts/shoptet_import.py --file … --yes` (záloha katalógu + bezpečný režim + read-back).
- **`?dry_run=1`** → script ide `--dry-run` (login + dôjde na import, NIČ nenahrá) — takto over chain bez zmeny eshopu.
- **Odpoveď JSON:** `{ok, exit_code, rows, processed, updated, failed, dry_run, stdout_tail}`. 401 bez tokenu, 400 zlé CSV, 409 keď už beží import (lock), 504 timeout, 502 keď import zlyhal.
- **n8n workflow „Forestshop — Vypredané → Skladom v2"** (`KN1BE18HLdM8mfTc`): `Denne 06:00 → Naše produkty + Dodávateľský sklad → Vyhodnoť kandidátov → CSV príloha → HTTP „Importuj do Shoptetu" → Sprava (Code) → Discord`. HTTP uzol = `httpRequest` v4.4, `contentType: binaryData`, `inputDataFieldName: data`, `options.timeout: 200000`, `neverError: true`. **PUBLISHED + active** (denne ~06:00, plne automaticky).
- **Discord = DETAILNÝ zoznam** (nie len počet): Code uzol „Sprava" zoskupí kandidátov podľa produktu (`$('Vyhodnoť kandidátov').all()`) a zlepí markdown — `N produktov (M variantov)`, per produkt **názov (veľkosti) — cena (konk. cena)** + **🟢 naše: forestshop URL** + **🔵 dodávateľ: internalNote URL** (na porovnanie), na konci **📦 Import: spracované/upravené/zlyhania** (z `$('Importuj do Shoptetu').first().json`). Discord `content = ={{ $json.text }}`, bez prílohy.
- **Discord 2000-znakový limit → CHUNKING:** „Sprava" rozdelí text na časti ≤1900 znakov (po riadkoch) a vráti **viac items** (`parts.map(t => ({json:{text:t}}))`); Discord uzol **`executeOnce: false`** → pošle JEDNU správu na chunk (s oboma linkami 10 produktov ≈ 2 správy). Pozor: tým že Discord nie je executeOnce, NESMIE byť napojený na uzol s mnohými items (napr. `Naše produkty`) — len na „Sprava" (1 item na chunk).
- **Bez embed kariet:** Discord robí preview kartu pre KAŽDÝ holý link (~20 kariet = nečitateľné). Riešenie = obaliť každý URL do **`<...>`** (`'🟢 naše: <' + p.url + '>'`) — Discord embed nezobrazí, link ostane klikateľný (overené: `embeds:[]` v send response). NIE message-flag `SUPPRESS_EMBEDS` (n8n discord uzol ho neexponuje).
- **Pasca:** n8n credential cez MCP **NEvieš vytvoriť** (žiadny tool) → token je natvrdo v header parametri uzla (`HARDCODED_CREDENTIALS` warning). Pre privátny n8n OK; presun do httpHeaderAuth credentialu sa dá len cez UI.

## n8n → nočný upload PÁROVANÍ (reorder link → eshop)

Keď pracovníci napárujú produkty v review appke (`decisions.json`), **denne o 21:00** sa nové párovania nahrajú na eshop ako **reorder link do `internalNote`** (NIE viditeľnosť/sklad — to rieši ranná restock automatizácia podľa skladu dodávateľa).

- **Endpoint:** `POST /api/n8n/upload-pairings` (Bearer `N8N_IMPORT_TOKEN`, dry_run). Číta `decisions.json` lokálne, vyberie **NOVÉ** párovania (good/manual + url, ešte nenahrané) cez `import_builder.new_pairing_keys` (inkrementálny stav v `data/out/uploaded_pairings.json` = `{key: url}`), postaví `import_links.csv` (`link_rows`) a spustí import (timeout 900s — býva veľký). Vráti `{ok, count, processed, updated, failed, products:[{name,our_url,supplier_url}]}`. Uploaded stav zapíše LEN po reálnom úspechu (dry_run nezapisuje).
- **n8n workflow „Forestshop — Párovania → eshop"** (`YuDugCCOnwejRfva`): `Denne 21:00 → HTTP „Nahraj parovania" → HTTP „Nahraj dodavatelov" → Sprava → Discord`. Reťaz je SEKVENČNÁ (každý import berie `_import_lock` zvlášť, uvoľní → druhý ho dostane); oba HTTP majú `neverError:true`, takže zlyhanie jedného nezhodí druhý ani Sprava. „Nahraj dodavatelov" = `POST /api/n8n/upload-suppliers` (supplier write-back, mirror pairings, ten istý Bearer). Sprava poskladá JEDNU kombinovanú správu z oboch (párovania + dodávatelia); ak oba `count:0` → `return []` (žiadna správa). **Po zmene uzla `update_workflow` → `publish_workflow` → over `versionId==activeVersionId`** (inak schedule beží po starom).
- **PASCA — duplicitné `code` (Shoptet ZRUŠÍ celý import):** katalóg má duplicitné produkty so ZDIEĽANÝMI variant kódmi; ak sú oba napárované, `link_rows`/`state_rows` by emitli ten istý `code` 2× a Shoptet padne na `Data in column "code" are not unique` (zruší CELÝ import, 0 zapísaných). **`link_rows`/`state_rows` preto dedupujú `code` (prvý vyhráva)** — overené naživo (690 párovaní, 25 dupes, prvý import zlyhal, po dedupe processed=3280, na exporte všetkých 3280 kódov má link).
- **Result read-back je zosilnený proti STARÉMU výsledku (#23, opravené):** `pick_result_row` (`src/parovanie/shoptet_import.py`, čisté + unit-testované) rozpoznáva aj TVRDÚ Shoptet chybu bez sumáru (`Chyba | Číslo riadku: N - Data in column code are not unique`) — predtým úzky keyword-regex takýto riadok preskočil a spadol na STARŠÍ dokončený beh nižšie v tabuľke (presne 690-import prípad: nahlásené `spracované=19` zo starého behu). `scripts/shoptet_import.py::_capture_baseline` odfotí najvrchnejší riadok Logu PRED submitom; `_read_result` pollne (wait+reload, max 6×) kým sa vrchný riadok nezmení od baseline — nikdy nevráti starý riadok. `parse_import_log` navyše vracia `error_detail` (surová chybová hláška), prehnané cez všetky 3 n8n JSON odpovede (restock/pairings/suppliers) → n8n vidí PREČO, nie len `processed:null`. Endpointy (`_do_upload_pairings`/`_do_upload_suppliers`) aj tak zapisujú „uploaded" LEN pri `rc==0` (`result_exit_code` už predtým brala `processed=None` ako zlyhanie) — hardening zatvára medzeru v ČÍTANÍ výsledku, nie v gate-ovaní zápisu. Napriek tomu **pri podozrení over reálne cez čerstvý export** (`internalNote` je stĺpec 257 v pattern-14 CSV) — nahlásený výsledok je teraz spoľahlivejší, ale export read-back ostáva finálny dôkaz.

## relatedProduct* stĺpce (priradené alternatívy) — POZOR na názvy (#100)

Pattern-14 export má „súvisiace/alternatívne produkty" v stĺpcoch, ktorých názvy sú **`relatedProduct`
(PRVÝ, bez čísla — NIE `relatedProduct1`), potom `relatedProduct2`..`relatedProduct28`**. Hodnoty sú
forestshop **kódy** (variant kód `60116/90` alebo bare kód/pairCode `60109`). Toto sú Shoptet-om
priradené alternatívy k produktu (boss #100: „relatedProduct1..8" = prvých 8 neprázdnych). Na
vyriešenie kódu → názov ber z toho istého exportu (name po `code` AJ `pairCode`), kód → forestshop URL
z marketing XML `ORIG_URL` (`_CODE2URL`), fallback `/vyhladavanie/?string=<kód>`.
