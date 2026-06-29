# parovanie_produktov

Nástroj: páruje forestshop (Shoptet) produkty na produktové stránky dodávateľov a píše URL do `internalNote` (pre auto-doobjednávanie; `textProperty*` sa CSV importom NEdá nastaviť — starý omyl). Detail: `README.md`, spec v `docs/superpowers/specs/`.

## Playbook router
Load the matching skill BEFORE working on that area (don't re-derive):
- shoptet eshop / export / import / polia produktov / textProperty / vypredané/vypnuté → load `.claude/skills/shoptet`
- dodávatelia / recon webu / pridanie dodávateľa / parsovanie výsledkov → load `.claude/skills/suppliers`
- deploy / verejná linka / cloudflare tunel / systemd služby → load `.claude/skills/deploy`
- webreview web (review tab / Na objednanie / per-riadkové stavy / api endpointy / úložiská párov) → load `.claude/skills/webreview`
- import párov z Discord vlákna cez n8n (forwardnuté notifikácie → páry) → load `.claude/skills/discord-import`

## Always
- Kódovanie I/O = **cp1250** na ČÍTANIE exportu; **import CSV = UTF-8 s BOM** (`utf-8-sig`), `;`, CRLF (cp1250 import → mojibake `č`→`è`).
- Testy bez živej siete (uložené HTML fixtúry): `.venv/bin/pytest`. Beh: `PYTHONPATH=src`. Hlavný beh ide `--ignore=tests/e2e`.
- Dáta (`data/`) a `.venv/` sú gitignored; veľký export sa necommituje.
- **Verzia**: `src/parovanie/__init__.py` `__version__` — bumpni na `dev` PRED prácou (CI job `version-check` vyžaduje dev > main). Zobrazuje sa na webe cez `/api/version` (footer); po deployi over na živom DOM.
- **Zdieľané helpery — NEkopíruj logiku**: `csv_loader.load_code2pair`, `writer.shoptet_writer` (kánonický CSV dialekt), `export_helpers` (`slug`/`state_of`/`IMGCOLS`/`row_images`/`current_of`/`fill_missing_prices`). 3-stavová klasifikácia produktu žije v `export_helpers.state_of` (raz, otestované). **`current` snapshot review položky (state/off/vis/avail/price/std/stock) staviaj LEN cez `export_helpers.current_of`** — majú ho 3 producenti (`build_review_data`/`resync_export`/`add_supplier_review_data`); keď jeden vynechal price/std/stock, web nezobrazil NAŠU cenu (bug). Chýbajúce ceny doplň `scripts/backfill_current_price.py`.
- **E2E webu**: `tests/e2e` (pytest-playwright) bootuje `webreview/app.py` proti fixture cez env `WEBREVIEW_OUT`/`WEBREVIEW_PRODUCTS`/`WEBREVIEW_PORT`; samostatný CI job `e2e`. App toleruje chýbajúci `review_data.json` (0 produktov).
