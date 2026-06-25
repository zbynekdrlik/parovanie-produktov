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

## TRI STAVY produktu (overené z dát eshopu — DOHODNUTÉ)

| Stav | productVisibility | stock | availability | internalNote (privátna linka) |
|---|---|---|---|---|
| **1. Skladom** | `visible` | >0 | `Skladom`/prázdne | URL (ak napárované) |
| **2. Nie je skladom** (dočasne) | `visible` | 0 | `Vypredané` | — |
| **3. Už sa nebude predávať** (link pre Google) | `detailOnly` | 0 | `Predaj výrobku skončil` | — |

`detailOnly` = LEN cez priamy odkaz (nie v kategóriách/vyhľadávaní) → stránka ostáva pre Google, ale produkt nie je v ponuke (stav 3, ~4 600 v katalógu). „Vypredané"=dočasne; „Predaj výrobku skončil"=už nikdy.

## Význam polí + konvencie (DOHODNUTÉ — overené naživo)

- **`internalNote` (Interná poznámka)** = **PRIVÁTNE** pole (zákazník NEVIDÍ). Drží **objednávaciu linku** (URL na produkt u dodávateľa) pre automatizáciu doobjednávania. **Importuje sa cez CSV (overené).** Samotná prítomnosť URL = signál „napárované" → žiadny extra marker netreba. (Pozn.: pôvodne tam bol zdroj objednávania ako doména `betalov.sk`/`rhodani.com` — plná URL je presnejšia.)
- **`textProperty1..20`** = **VEREJNÉ** info-parametre (zobrazia sa zákazníkovi), formát „Názov;Hodnota". **CSV importom sa NEDAJÚ nastaviť** (Shoptet ich ignoruje) — NEpoužívaj na linku ani marker. Toto bol pôvodný omyl (textProperty10/11 sa nikdy nezapísali).
- **Napárovaný (link):** `import_links.csv` → `internalNote`=URL. NIČ iné — vis/stock/avail sa NEdotýkaj (prázdne by ich zmazalo).
- **Nie je skladom (stav 2):** `import_states.csv` → `visible`, `stock=0`, `Vypredané`.
- **Už sa nebude predávať (stav 3):** `import_states.csv` → `detailOnly`, `stock=0`, `Predaj výrobku skončil`.

## productVisibility hodnoty

`visible`, `detailOnly` (predajný cez priamy odkaz; so „Predaj skončil" = stav 3), `hidden`, `blocked`, `cashDeskOnly`, `blockUnregistered`. `detailOnly` samo o sebe NIE je „vypnutý".

## Generovanie importu

`scripts/build_decisions_import.py` → `import_links.csv` + `import_states.csv` (web `/api/import` → zip oboch) z rozhodnutí (`decisions.json`) cez `import_builder.link_rows` / `state_rows` (otestované).

## Auto-import do adminu (script)

`scripts/shoptet_import.py` — prihlási sa do Shoptet adminu a nahrá import CSV namiesto ručného klikania. Čistá logika (načítanie údajov, pred-letová kontrola CSV, parser výsledku) je v `src/parovanie/shoptet_import.py` (unit testy, bez prehliadača); browser riadi tenká Playwright obálka v scripte.

- **Secret:** `data/.shoptet_admin` (chmod 600, `data/` je gitignored — NIE v gite). Kľúče: `SHOPTET_ADMIN_URL`, `SHOPTET_USER`, `SHOPTET_PASS`, `SHOPTET_EXPORT_URL` (pattern-14 export s hash, na zálohu). Hodnoty NIKDY do gitu/skillu.
- **Beh:** `PYTHONPATH=src .venv/bin/python scripts/shoptet_import.py --file <CSV> [--dry-run] [--yes] [--headful]`. Importuj OBA súbory zvlášť: `import_links.csv` aj `import_states.csv`. Playwright: `pip install -r requirements-import.txt && playwright install chromium` (NIE v CI).
- **Poistky (poradie):** pred-letová kontrola CSV (UTF-8, `code`+`pairCode`, rozpis link/nie-skladom/nebude-predávať) → potvrdenie (`--yes` preskočí) → **záloha exportu** do `data/backups/export_<ts>.csv` (bez úspešnej zálohy sa NEimportuje) → login → upload + **nastaví a read-back overí bezpečné parametre** (viď nižšie) → spustí → prečíta skutočný výsledok z **Logu**. Audit (screenshot + log) do `data/out/shoptet_import_<ts>.*`.

### Reálny Shoptet import formulár (overené naživo)

- **URL:** `/admin/import-produktov/` (POZOR: NIE `produkty-import`). Login: placeholder `E-mail` / `Vaše heslo`, tlačidlo `Prihlásenie`.
- **Formulár NEMÁ voľby kódovania / „nahradiť prázdne" / párovania.** Shoptet **auto-detekuje kódovanie** (náš `utf-8-sig` BOM → UTF-8) a **páruje podľa `code`** automaticky (stĺpec v hlavičke). Stĺpec s prázdnou bunkou → Shoptet ho **ZMAŽE** (preto sú importy rozdelené — viď „Import — DVA súbory").
- **Jediné rizikové voľby na formulári** (script ich nastaví + read-back overí): radio **„Nemeniť produkty a varianty, ktoré nie sú obsiahnuté v importovanom súbore"** (bezpečný default — NIKDY „Zmazať…") + checkbox **„Zmeňte adresu URL produktu podľa názvu produktu" = VYPNUTÉ**.
- **Súbor sa MUSÍ nahrať cez file-chooser** (`expect_file_chooser` + klik „Vyberte súbor") — nastavenie skrytého `input[name=file]` Shoptet widget nezaregistruje (zostane „Povinné pole").
- **Spustenie:** tlačidlo `data-testid="buttonImport"` → presmeruje na `/admin/import-produktov/log/`.
- **Výsledok (Log):** SK „Spracované: N. Upravené: K. Zlyhanie variantov: M.", auto-import býva CZ „Zpracováno/Upraveno". Parser berie len najnovší riadok a „Zlyhanie: N" uprednostní pred prózou „skončil s chybou".
- **Export na zálohu:** hash je **per-pattern aj per-formát**. Plný export = šablóna **„vsetko" (patternId 14), formát CSV** — URL+hash vyčítaj na `/admin/export-produktov/` (vyber šablónu + formát → „Verejný odkaz"). Ten ide do `SHOPTET_EXPORT_URL`.
