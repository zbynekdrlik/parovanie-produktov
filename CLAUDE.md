# parovanie_produktov

Nástroj: páruje forestshop (Shoptet) produkty na produktové stránky dodávateľov a píše URL do `internalNote` (pre auto-doobjednávanie; `textProperty*` sa CSV importom NEdá nastaviť — starý omyl). Detail: `README.md`, spec v `docs/superpowers/specs/`.

## Playbook router
Load the matching skill BEFORE working on that area (don't re-derive):
- automatizácie: tichá smrť behu (alarm/stats/banner), kalibrácia prahov, dôvera fixtúram cudzieho API, tiché ROZŠÍRENIE mailovanej množiny → `.claude/rules/automation-health.md` (auto-loads on its `paths:`)
- e2e „Na objednanie": zdieľané `#list`/`ACTIVE_TAB`, výroba ✂️ split riadku, skloňovanie počtov, spy na hlásenie + route podľa metódy, zmena sémantiky vs. cudzie testy, optimistický zápis cez viac príznakov (`seq`/`confirmedSeq` nárokuje len vlastný príznak), surový NUL oslepí `grep`, seed predpokladu cez `page.request`, nový prvok na karte automatizácie nesmie nosiť triedy `.auto*`, nový `confirm()` prepne cudzí test na vetvu „zrušiť" → `.claude/rules/toorder-e2e.md` (auto-loads on its `paths:`)
- shoptet eshop / export / import / polia produktov / textProperty / vypredané/vypnuté → load `.claude/skills/shoptet`
- dodávatelia / recon webu / pridanie dodávateľa / parsovanie výsledkov → load `.claude/skills/suppliers`
- deploy / verejná linka / cloudflare tunel / systemd služby → load `.claude/skills/deploy`
- webreview web (review tab / Na objednanie / per-riadkové stavy / api endpointy / úložiská párov / záložka „Vývoj" = GitHub issues + žiarovka nápad→issue) → load `.claude/skills/webreview`
- import párov z Discord vlákna cez n8n (forwardnuté notifikácie → páry) → load `.claude/skills/discord-import`
- GRUBE per-veľkosť kódy / grube.de itemId extrakcia / externalCode zápis → load `.claude/skills/grube`
- mazanie/prune z manažérových úložísk (`data/out/*.json`), pridanie novej množiny do `order_statuses.json` → `.claude/rules/store-prune.md` (auto-loads on its `paths:`)

## Always
- Kódovanie I/O = **cp1250** na ČÍTANIE exportu; **import CSV = UTF-8 s BOM** (`utf-8-sig`), `;`, CRLF (cp1250 import → mojibake `č`→`è`).
- Testy bez živej siete (uložené HTML fixtúry): `.venv/bin/pytest`. Beh: `PYTHONPATH=src`. Hlavný beh ide `--ignore=tests/e2e`.
- Dáta (`data/`) a `.venv/` sú gitignored; veľký export sa necommituje.
- **Verzia**: `src/parovanie/__init__.py` `__version__` — bumpni na `dev` PRED prácou (CI job `version-check` vyžaduje dev > main). Zobrazuje sa na webe cez `/api/version` (footer); po deployi over na živom DOM.
- **Úložiská (`data/out/`) = živá práca manažéra.** Nový store deklaruj `_store("x.json")` (NIKDY `os.path.join(OUT, …)` — zmrazená cesta vymazala 2831 rozhodnutí, #261), čítaj `_read_json_store`, píš `_atomic_write_json` (`protect=True` pri neopakovateľnej práci + doplň ho do `scripts/backup_data.sh`), read-modify-write drž v `with _lock:` (je medziprocesový, #264). Detail → skill `webreview`.
- **Zdieľané helpery — NEkopíruj logiku**: `csv_loader.load_code2pair`, `writer.shoptet_writer` (kánonický CSV dialekt), `export_helpers` (`slug`/`state_of`/`IMGCOLS`/`row_images`/`current_of`/`fill_missing_prices`). 3-stavová klasifikácia produktu žije v `export_helpers.state_of` (raz, otestované). **`current` snapshot review položky (state/off/vis/avail/price/std/stock) staviaj LEN cez `export_helpers.current_of`** — majú ho 3 producenti (`build_review_data`/`resync_export`/`add_supplier_review_data`); keď jeden vynechal price/std/stock, web nezobrazil NAŠU cenu (bug). Chýbajúce ceny doplň `scripts/backfill_current_price.py`.
- **E2E webu**: `tests/e2e` (pytest-playwright) bootuje `webreview/app.py` proti fixture cez env `WEBREVIEW_OUT`/`WEBREVIEW_PRODUCTS`/`WEBREVIEW_PORT`; samostatný CI job `e2e`. App toleruje chýbajúci `review_data.json` (0 produktov).
