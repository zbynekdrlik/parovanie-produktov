# Záložka „Na objednanie" vo webreview — návrh

**Dátum:** 2026-06-27
**Verzia:** 0.12.0 (dev)
**Kontext:** sused projekt `objednavky_supliers` rieši doobjednávanie. Auto-plnenie B2B
košíka (huntingshop) sa ZASTAVILO — košík je viazaný na session, nie na účet (overené),
takže workflow vlastným loginom naplní košík, ktorý manažér nevidí. Manažér tiež sťažoval
neprehľadnosť Discord zoznamu pri objeme (100 obj/deň × 10 = 1000 položiek). Riešenie:
webová záložka v tejto párovacej appke.

## Cieľ

Záložka **„Na objednanie"** v `webreview` appke: kompaktný, škálovateľný zoznam položiek,
ktoré treba doobjednať u dodávateľa, s priamym linkom na produkt a odškrtávaním „objednané".
Manažér klikne link → pridá do SVOJHO košíka → objedná. (Workflow nič nepridáva do košíka.)

## Dáta

- **Objednávky:** forestshop export `orders.csv` (cp1250, `;`), filter `statusName == "Vybavuje sa"`,
  zahodiť `itemCode` ~ `^(SHIPPING|BILLING)`. URL z `data/.shoptet_admin` nový kľúč
  `SHOPTET_ORDERS_URL` (base + hash; appka doplní `dateFrom`/`dateUntil`, hash je date-independent).
  Fetch **cachovaný** (~5 min, `data/out/orders_cache.csv`) — nie live pri každom requeste.
- **Join (per riadok objednávky):** `order.itemCode → CODE2PAIR[code] → pairCode → product`
  (`PRODUCTS` podľa pairCode) → `supplier`, `name`; **dodávateľský link = `decisions[product.key].url`**
  (napárovaný worker-om). Bez napárovania → bez linku (sekcia „nenapárované").
- **Per riadok:** `{ orderCode, itemCode, size (itemVariantName), qty (itemAmount), supplier,
  name, supplierUrl, ordered }`. Kľúč riadka = `orderCode|itemCode`.

## Perzistencia „objednané"

- `data/out/ordered_items.json` = `{ "<orderCode>|<itemCode>": true, ... }`.
- Atomic write (tmp+`os.replace`) + `_lock` — presne ako `decisions.json`.
- Rieši aj „nepridať/neukazovať znova": odškrtnuté = objednané; manažér to spravuje.

## API (webreview/app.py)

- `GET /api/ordered` → `{ "ordered": {...} }`; `POST /api/ordered` `{key, ordered}` → toggle.
- `GET /api/orders` → `{ "orders": [ {orderCode, itemCode, size, qty, supplier, name,
  supplierUrl, ordered}, ... ] }`. Fetch (cache) → `build_to_order_rows()` (čistá fn,
  testovateľná na fixture) → doplň `ordered` stav.
- Čistá fn **`build_to_order_rows(orders_csv_bytes, code2pair, products, decisions) -> list`**
  v `app.py` (alebo helper) — TDD na fixture CSV, bez živej siete.

## Frontend (static/app.js, style.css, templates/index.html)

- **Tab bar** (nový): „🔍 Kontrola párovania" / „📋 Na objednanie" (active tab v `localStorage`).
- Záložka „Na objednanie": tabuľka zoskupená podľa **dodávateľa**, filter podľa dodávateľa
  (škáluje na 1000+), riadok = ☑ · **🔗 Link** · **Veľkosť** · **Počet ks** · názov.
  ☑ → `POST /api/ordered`. Discord ostane len krátky ranný ping s linkom na záložku.

## Testy (TDD)

- Unit (`tests/test_webreview.py`): `/api/ordered` GET/POST + perzistencia (monkeypatch path);
  `build_to_order_rows` na fixture CSV (Vybavuje sa filter, shipping/billing drop, join, qty).
- E2E (`tests/e2e/`): záložka prepne, zobrazí riadky, ☑ pretrvá po reloade, console clean.
- Fixture (`tests/e2e/conftest.py`): pridať mock orders.

## Nasadenie

- CI: ruff + pytest (cov ≥80% `parovanie`) + E2E + version-check (0.12.0 > 0.11.0 ✓).
- Po merge na main: na dev1 `git pull` + `systemctl --user restart parovanie-web`; over verziu
  na živom DOM (`parovanie-forestshop.newlevel.media`) + funkčnosť záložky.
- `SHOPTET_ORDERS_URL` doplniť do `data/.shoptet_admin` na dev1 (secret, mimo git).

## Mimo rozsahu (zatiaľ)

- Auto-plnenie košíka (session-bound, zastavené). Aggregácia množstiev naprieč objednávkami
  (zatiaľ per riadok). Wetland/Zubíček (rovnaký princíp, neskôr).
