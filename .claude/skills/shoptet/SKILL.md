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
- Stĺpce: `code;pairCode;textProperty10;textProperty11;stock;availabilityInStock;availabilityOutOfStock`.

## Význam polí + konvencie (DOHODNUTÉ)

- **`textProperty10`** = odkaz na produkt u dodávateľa. Slúži **automatizácii na doobjednávanie** (hitne link).
- **`textProperty11` = `human matched`** = ručne overené. Druhá automatizácia podľa neho (a podľa linku) **zapne produkt (visible)**; budúce párovanie tieto **preskočí**.
- **Napárovaný produkt (link):** `textProperty10`=URL, `textProperty11`=`human matched`, a **stock/availability nechaj PRÁZDNE** (viditeľnosť/dostupnosť rieši ich automatizácia — NEprepisuj starý stav typu „Predaj výrobku skončil").
- **Nedostupný (sold-out):** `textProperty10`=prázdne (kým sa nenájde link), `textProperty11`=prázdne, `stock=0`, `availabilityInStock`=`availabilityOutOfStock`=**`Vypredané`**. NEoznačuj `human matched` → ostáva v poole na re-kontrolu, či nepribudol link.

## productVisibility hodnoty

`visible`, `detailOnly` (len cez priamy odkaz — drop-ship, **predajný**), `hidden`, `blocked`, `cashDeskOnly`, `blockUnregistered`.

- **„vypnutý/nedostupný" = NEpredajný** = `hidden`/`blocked` ALEBO dostupnosť `Vypredané`/`Predaj výrobku skončil`/`Momentálne nedostupné`.
- **`detailOnly` NIE je vypnutý** (je predajný cez link). Nepočítaj ho ako off.

## Generovanie importu

`scripts/build_decisions_import.py` (alebo web `/api/import` tlačidlo „⬇ Stiahnuť import") → z rozhodnutí (`decisions.json`) cez `src/parovanie/import_builder.py`. Logika je v `import_builder.import_rows` (otestované).
