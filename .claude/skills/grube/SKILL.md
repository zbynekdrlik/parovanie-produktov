# GRUBE — per-veľkosť kódy (špeciálne správanie, len GRUBE)

Load BEFORE prácou na GRUBE per-veľkosť kódoch / grube.de extrakcii / `externalCode` zápise.
Spec: `docs/superpowers/specs/2026-06-29-grube-per-size-pairing-design.md`. Logika: `src/parovanie/grube_de.py` (pure, otestované).

## Kľúčové fakty (overené naživo 2026-06-29)

- **grube.sk a grube.de zdieľajú productId AJ per-veľkosť itemId** — líši sa len lokalizovaná dostupnosť. Manažérove .sk párovania sa NEMUSIA prerábať; `to_grube_de(url)` = productId-rebuild `https://www.grube.de/p/x/<pid>/` (strip slug/query/`#itemId` fragment). Navigácia na `/p/x/<pid>/` (placeholder slug) sa presmeruje na kánonickú .de stránku.
- **Per-veľkosť itemId = schema.org Offer JSON**, NIE positional radio-zip (radio poradie NEsedí s itemId poradím). Offer: `"name":"[Farbe X. ]Größe <SIZE>.","price":"…","priceCurrency":"EUR","sku":"<itemId>"` — quotes sú `\"`-escaped (JSON v JSON) → `html.replace('\\"','"')` PRED matchom. Veľkosť z `Größe <SIZE>`, kód zo `sku`.
- **Cross-sell pollution:** stránka má `associatedProduct` kotvy s itemId INÝCH produktov → filter `sku.startswith(productId) AND len(sku)==len(productId)+4`. (Tá istá lekcia ako RELATED_PRODUCTS v marketing XML.)
- **Farba-os:** ak veľkosť mapuje na >1 itemId (viacfarebné) → `parse_variants` vráti `{}` (link-only, fail-closed).
- **Single-size produkty (nože) NEMAJÚ Offer-list** — jediný itemId je len v `itemId=<id>` kotve stránky (reminder link + canonical fragment `…/<pid>/#itemId=<id>`). **#60 class 1 HOTOVÉ (v0.79.0):** `parse_variants` pri prázdnom Offer-liste vezme vlastný itemId z `itemId=` kotvy (`prefix==productId AND len==pid+4`, rovnaký filter ako sku); PRESNE 1 → `{ONE_SIZE: itemId}` (`ONE_SIZE=""` sentinel), 0/>1 → `{}` (link-only, fail-closed). `match_variant_codes`: one-size row (`resolve_size None`) sa napáruje na single-size grube LEN keď je 1 one-size row (nikdy 1 itemId → N kódov). **Class 2 (dvojosové bunda+nohavice) + class 3 (rozsahové `Gr. X`/`39/40`) ostávajú link-only → #60 stále OTVORENÉ.**
- **Single-variant nôž má PRÁZDNY pairCode** → NIE je v `by_pair` (export grouping podľa pairCode) → `build_grube_codes` ho joinne cez `review_data.json` `variant_codes` (nový 4. param `review_by_key`, `main()` ho načíta; 3-arg volanie ostáva back-compat pre viacveľkostné). Fill-rate class 1: 32 napárovaných (universe 50 GRUBE single-variant one-size položiek).
- **NEcommituj surovú grube.de stránku ako fixtúru** — vloží grube-ove vlastné klientske kľúče (`apiKey`/`password`/`Secret`) → `block-sensitive-staging.sh` hook zablokuje `git add`. Sprav KOMPAKTNÚ secret-free fixtúru z REÁLNych `itemId=` kotiev (vlastná + pár cudzích cross-sell), bez `<script>` config blobov (vzor `grube_de_detail_254031_ocielka.html`, 1.8 KB). Live gather (`gather_grube_itemids.py`, ~40 min Playwright) je runtime data-refresh — nespúšťaj v tickete, over extrakciu proti fixtúre.

## Veľkosť forestshop variantu — zo STĹPCA, NIKDY z code suffixu

Code suffix je nespoľahlivý (`997/S77`=accessory bez veľkosti, `62093/39/40`=slash vo veľkosti, `61933//M`, disambiguátory `3XL2`). Veľkosť čítaj z `variant:*` stĺpca (`resolve_size`): `Bunda veľkosť`+`Nohavice veľkosť` oba → **MULTI_AXIS** (komplet, link-only); presne 1 populated → label; 0 → one-size (None). **Per-row, NIE per-product** (produkt môže miešať stĺpce).

## Match = EXACT-string, fail-closed

`normalize_size` (trim, uppercase písmenká, `2XL→XXL` alias). EXACT zhoda → itemId; nezhoda → link-only (NIKDY fuzzy/nearest; XS nikdy nesnapne na S). Pokryté naživo: písmenkové **S–5XL** + číselné nohavice **46–64**. Rozsahy `39/40`/`Gr. X` → link-only (issue #60). Collision guard: dva grube labely na jeden normal → `ValueError`.

## Pipeline + úložisko

1. `scripts/gather_grube_itemids.py` (live Playwright .de, reuse `PlaywrightFetcher(base=...)`, resumable checkpoint per productId, 404-tolerant) → `data/out_grube/itemids.json` `{pid: {size: itemId}}`.
2. `scripts/build_grube_codes.py` (join decisions+itemids+export podľa `match_variant_codes`) → **`data/out/grube_codes.json`** `{code: {itemId,size,deUrl,productId}}` — **durable, NEVER-pruned** (data/out, nie data/out_grube).
3. Write-back: `import_builder.externalcode_rows` → `import_externalcode.csv` (`code;pairCode;externalCode`, GRUBE-only, dedup, drop-empty/non-numeric, `_csv_safe`). `link_rows` normalizuje GRUBE `internalNote` na .de. Webreview „Na objednanie": `_attach_grube` → kód-čip + .de link.

## externalCode JE importovateľný (variant-level) — ale over pri NOVOM poli

Overené 2026-06-29: set `60645/L` externalCode cez `code;pairCode;externalCode` CSV → čerstvý export read-back: hodnota na variante AJ súrodenec nezmenený (variant-level) → revert na '' (prázdna bunka WIPE funguje). Technika `set→export-readback→revert` na jednom sentinel variante (ako `supplier`). **Export-presence ≠ importable — pri každom NOVOM zápisovom poli over rovnako naživo** (lekcia textProperty10). MVP = manuálny zip + `shoptet_import.py` (záloha+read-back), nočný cron až po stabilizácii (issue #62).

## Gather je .sk, .de len pre kódy+linky (decouple)

NEprepínaj `config.SUPPLIERS["GRUBE"].base_url` na .de — `grube.parse_search` filtruje `url.startswith(base_url)`, search renderuje na .sk → flip by ticho vrátil 0 kandidátov. Gather/search ostáva .sk (productId zdieľaný); .de sa odvodí cez `to_grube_de`. (.de search gather = issue, ak treba.)

**GRUBE .de platí AJ pre ZOBRAZENIE na webe, nielen eshop.** Zdroj kandidátov je .sk (zber), takže `review_data` + `decisions` držia .sk URL. `link_rows` (eshop internalNote) + „Na objednanie" čip normalizujú na .de, ale **review/search karta + `resolutionPanel` kandidáti + `ai_chosen_url`** by inak ukázali .sk (manažér: „GRUBE otvára .sk"). Preto `/api/products` robí **serve-time .de normalizáciu** (len GRUBE, cez `to_grube_de`, LEN zobrazenie — uložené .sk párovania sa NEMENIA, kópie nie mutácia). Jedno miesto pokrýva review kartu aj search tab (obe cez `resolutionPanel`). Test: `tests/test_webreview_grube_de.py`.
