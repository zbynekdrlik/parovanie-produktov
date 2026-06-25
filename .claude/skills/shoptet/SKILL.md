# Shoptet eshop — import/export pravidlá (forestshop.sk)

Load BEFORE čímkoľvek okolo exportu z eshopu / importu do eshopu / polí produktov.
Toto sú dohodnuté pravidlá — nepýtaj sa ich nanovo.

## Export (z eshopu)

- **CSV** (pattern 14): `https://www.forestshop.sk/export/products.csv?patternId=14&partnerId=3&hash=<HASH>` — kódovanie **Windows-1250 (cp1250)**, `;`, hodnoty v úvodzovkách, viacriadkové. ~14 000 riadkov (variantov).
- **XML** (productsComplete, patternId=-5) — má SHOPITEM/CODE/IMAGES, ale **NEMÁ produktové URL** (ani CSV). URL sa rieši cez sitemap + HTTP-overenie slugov (viď skill `suppliers`).
- Hash v exportoch je **partner credential** — NIKDY do gitu (placeholder `<HASH>`); reálny iba lokálne.
- Kľúčové stĺpce: `code` (kód variantu), `pairCode` (zoskupuje varianty produktu), `name`, `externalCode` (kód dodávateľa), `supplier`, `productVisibility`, `availabilityInStock`/`availabilityOutOfStock`, `stock`, `price` (s DPH, EUR), `standardPrice`, `purchasePrice`, `textProperty10..20`.

## Import (do eshopu) — JEDEN súbor

- **Kódovanie: UTF-8 (s BOM).** Importuj v Shoptete ako UTF-8. (cp1250 spôsobí mojibake: `č`=0xE8 sa zobrazí ako `è` keď to číta cp1252 — preto UTF-8.)
- Oddeľovač `;`, CRLF.
- **VŽDY stĺpce `code` AJ `pairCode`** — Shoptet ich oba potrebuje na import variantových produktov. Párovanie podľa `code`.
- **„Nahradiť prázdne hodnoty" = VYPNUTÉ** (prázdna bunka = nechať tak). Tak jeden súbor zvládne aj linky aj vypredané bez vymazania nechcených polí.
- Stĺpce: `code;pairCode;textProperty10;textProperty11;productVisibility;stock;availabilityInStock;availabilityOutOfStock`.

## TRI STAVY produktu (overené z dát eshopu — DOHODNUTÉ)

| Stav | productVisibility | stock | availability | textProperty10 |
|---|---|---|---|---|
| **1. Skladom** | `visible` | >0 | `Skladom`/prázdne | (link ak je) |
| **2. Nie je skladom** (dočasne) | `visible` | 0 | `Vypredané` | prázdny (čaká na link) |
| **3. Už sa nebude predávať** (link pre Google) | `detailOnly` | 0 | `Predaj výrobku skončil` | prázdny |

`detailOnly` = LEN cez priamy odkaz (nie v kategóriách/vyhľadávaní) → stránka ostáva pre Google, ale produkt nie je v ponuke (stav 3, ~4 600 v katalógu). „Vypredané"=dočasne; „Predaj výrobku skončil"=už nikdy.

## Význam polí + konvencie (DOHODNUTÉ)

- **`textProperty10`** = odkaz na produkt u dodávateľa. Slúži **automatizácii na doobjednávanie** (hitne link).
- **`textProperty11` = `human matched`** = ručne overené. Druhá automatizácia podľa neho (a podľa linku) **zapne produkt (visible)**; budúce párovanie tieto **preskočí**.
- **Napárovaný (link):** `textProperty10`=URL, `textProperty11`=`human matched`; `productVisibility`/`stock`/`availability` **PRÁZDNE** (automatizácia ich nastaví z linku → stav 1). NEprepisuj starý stav.
- **Nie je skladom (stav 2):** prázdny link a marker, `productVisibility=visible`, `stock=0`, availability `Vypredané`. Ostáva v poole na re-kontrolu.
- **Už sa nebude predávať (stav 3):** prázdny link a marker, `productVisibility=detailOnly`, `stock=0`, availability `Predaj výrobku skončil`. Stránka ostane pre Google.

## productVisibility hodnoty

`visible`, `detailOnly` (predajný cez priamy odkaz; so „Predaj skončil" = stav 3), `hidden`, `blocked`, `cashDeskOnly`, `blockUnregistered`. `detailOnly` samo o sebe NIE je „vypnutý".

## Generovanie importu

`scripts/build_decisions_import.py` (alebo web `/api/import` tlačidlo „⬇ Stiahnuť import") → z rozhodnutí (`decisions.json`) cez `src/parovanie/import_builder.py`. Logika je v `import_builder.import_rows` (otestované).

## Auto-import do adminu (script)

`scripts/shoptet_import.py` — prihlási sa do Shoptet adminu a nahrá import CSV namiesto ručného klikania. Čistá logika (načítanie údajov, pred-letová kontrola CSV, parser výsledku) je v `src/parovanie/shoptet_import.py` (unit testy, bez prehliadača); browser riadi tenká Playwright obálka v scripte.

- **Secret:** `data/.shoptet_admin` (chmod 600, `data/` je gitignored — NIE v gite). Kľúče: `SHOPTET_ADMIN_URL`, `SHOPTET_USER`, `SHOPTET_PASS`, `SHOPTET_EXPORT_URL` (pattern-14 export s hash, na zálohu). Hodnoty NIKDY do gitu/skillu.
- **Beh:** `PYTHONPATH=src .venv/bin/python scripts/shoptet_import.py [--file CSV] [--dry-run] [--yes] [--headful]`. Default súbor `data/out/import_forestshop.csv`. Playwright: `pip install -r requirements-import.txt && playwright install chromium` (NIE v CI).
- **Poistky (poradie):** pred-letová kontrola CSV (UTF-8, `code`+`pairCode`, rozpis link/nie-skladom/nebude-predávať) → potvrdenie (`--yes` preskočí) → **záloha exportu** do `data/backups/export_<ts>.csv` (bez úspešnej zálohy sa NEimportuje) → login → nastaví **UTF-8 / „nahradiť prázdne" VYPNUTÉ / párovať podľa Kódu** → **read-back** týchto parametrov (nesedí → NEimportuje) → spustí → prečíta skutočný výsledok z **Logu** (Spracované/Upravené/Zlyhania). Audit (screenshot + log) do `data/out/shoptet_import_<ts>.*`.
- **Login selektory:** placeholder `E-mail` / `Vaše heslo`, tlačidlo `Prihlásenie` (overené). Import formulár (`/admin/produkty-import/`, voľby kódovania/prázdnych/párovania) sa dolaďuje v živom behu s prihlásením.
