# Webreview — kontrolný web (review + „Na objednanie")

Load BEFORE prácou na `webreview/` (Flask `app.py` + vanilla-JS SPA `static/app.js`,
`style.css`, `templates/index.html`). Layout = **ľavý sidebar-dashboard** (redesign v0.42.0):
sidebar (nav = naše funkcie s outline SVG ikonami + `.navcount` počítadlami, zoskupené v
rozbaľovacom priečinku „Eshop" #118; admin „Užívatelia" je SAMOSTATNE úplne dole; dole tmavý-mód
prepínač + verzia) + top bar (`#pageTitle`/`#pageSub` + `.topacts`). Nav (`#tabs`) žije v
sidebar-e; `.filters`/`.progress`/`.downloads` v top bar-e.

## Sidebar strom — priečinok „Eshop" (#118) + „Užívatelia" štandalón

- **Priečinok „Eshop"** (rozbaľovací, default rozbalený, stav `localStorage('folder:eshop')`)
  obaľuje `#tabs` (pracovné taby) + pod-sekciu „Automatizácie" (`#autoTabs`). `initFolder(id, key)`
  drôtuje ďalší priečinok. `#tabs` OSTÁVA vnútri `.sidebar` (selektorový kontrakt).
- **`renderTabs()` vykresľuje TRI kontajnery**: `#tabs` (z `TABS`), `#autoTabs` (z
  `AUTOMATION_TABS`), a `#usersNav` (len admin, `isAdmin()` — NIE cez starý `visibleTabs()`).
  „Užívatelia" je MIMO priečinka: samostatný `#usersNav` (priamy potomok `.sidebar` medzi
  `.side-nav` a `.sidefoot`), skryje sa keď prázdny (`.users-nav:empty{display:none}` → non-admin
  nevidí prázdny separátor). Presun/skrytie „Užívatelia" NEmení `switchTab('users')`/`loadUsers`/
  `renderUsers`/`#tab-users` — tie ostávajú; mení sa len KDE žije nav-button.
- Sekcia „Čoskoro — rozšírime" + `.soon`/`.soonpill`/`.soon-nav` boli ODSTRÁNENÉ (#118, Marek
  2026-07-22) — nevracaj ich.

## UI redesign / shell zmeny — zachovaj SELEKTOROVÝ KONTRAKT (E2E ho merajú)

Pri prestavbe shellu/CSS **NEMEŇ** id/`data-testid`/triedy, na ktoré sa viažu `app.js` AJ
`tests/e2e/*` — zmapuj ich PRED zásahom (Explore agent nad app.js + testami). Kritické:
- IDs: `tabs filters progressText progressBar empty searchBox searchResults list tab-search
  tab-notes dlImport known-suppliers version pageTitle themeBtn`; `data-testid="version"`.
- **Order-supplier chip farby MUSIA ostať presné** — `body.toorder-wide .filters button.todo`
  `#6CAB68` / `.done` `#D14D3B` / `.active` `#DDA43C` (E2E asserty na computed rgb 108,171,104 /
  209,77,59 / 221,164,60 — zjemnené #143 2026-07-22, presné hex od šéfa pre todo/done). Review-filter
  `.active` je oddelený (accent green, `--accent-hov`).
- Nav-button accessible NAME = holý label ("Na objednanie", "Hľadať / opraviť") — SVG ikona
  nedáva text, `.navcount` len appenduje, takže `get_by_role(name=...)` substring stále sedí.
  `render()` viditeľnosť cieli `.progress`/`.downloads`/`#filters`/`#list`/`#tab-*`/`body.toorder-wide`.

## Tmavý mód (v0.42.0)

`[data-theme=dark]` na `<body>` + CSS premenné (`:root` / `body[data-theme=dark]`),
`localStorage('theme')`, prepínač `#themeBtn` v sidebar-e (`applyTheme`/`initTheme`).
**FOUC guard** = inline `<script>` ako PRVÉ dieťa `<body>` (body už existuje → nastaví
`data-theme` pred vykreslením obsahu, žiadny biely záblesk pre dark usera). E2E test
`tests/e2e/test_shell.py` (sidebar + page-title + tmavý-mód persistencia cez reload).

## Paleta — `--accent`/`--accent-hov` sú DVA odtiene, nie jeden (#143, v0.58.1)

Zámerná architektúra, nie duplicita: `--accent` = jemný ťah (TEXT/ikona na svetlom/mäkkom
podklade — aktívna nav položka na `--accent-soft`, progress bar, focus ring); `--accent-hov`
= silný ťah (SOLID výplň s BIELYM textom navrchu — brand logo, navcount badge, `.filters
button.active`, `.downloads a`, `.btn.good`, `.stockfilters .sf.active`). Dôvod: jedna
farba nevie súčasne vyhovieť „text na bielom/svetlom" (potrebuje TMAVší odtieň, aby mal
kontrast ≥4.5:1) AJ „biely text na plnej výplni" (potrebuje TIEŽ tmavší odtieň) — takže obe
použitia v skutočnosti chcú TEN ISTÝ tmavší odtieň, len `--accent` navyše slúži ako text na
takmer-bielom `--accent-soft` pozadí, kde sa naopak zíde byť o niečo SVETLEJŠÍ v tmavom móde
(text musí byť čitateľný na TMAVOM `--accent-soft`, teda jasnejšia zelená). Pri ĎALŠEJ zmene
palety over si kontrast WCAG luminanciou (rýchly python skript, nie odhad od oka) PRED
zápisom do CSS — pozri commit `63d3963` pre vzor výpočtu. Konkrétne hex boss-om zadaných
farieb (`#6CAB68`/`#D14D3B`) sa použili DOSLOVA len na miestach, ktoré explicitne pomenoval
(to-order chip-y, „Skladom"/„Nedostupné"), NIE na `--accent` samotnú (tá je odvodená tmavšia
verzia v rovnakom odtieni, lebo doslovný boss-ov hex by na bielom pozadí/bielom texte mal
kontrast len ~2.8:1 — príliš málo).

## Auth (#91, v0.44.0) — celý web za loginom; KAŽDÝ nový endpoint je chránený automaticky

Default-deny `before_request` gate v `app.py`: nový route NEtreba nijako značiť — chránený je
sám od seba. Verejné výnimky = množina `_PUBLIC_ENDPOINTS` (login/forgot/reset/static/favicon/
api_version) + path-prefix `/api/n8n/*` (vlastný bearer, n8n nemá session). Anonym: `/api/*` →
401 JSON, stránky → redirect `/login?next=…`. Session sa overuje proti store pri KAŽDOM requeste.

- **Stores (0600, data-safety počty pri deployi počítaj AJ tieto)**: `data/out/users.json`
  (email → pw_hash/is_admin/created_at), `data/out/reset_tokens.json` (sha256(token) → email/exp).
- **Creds súbory (gitignored, chmod 600, NIKDY do gitu)**: `data/.auth_env` (SECRET_KEY,
  ADMIN_EMAIL, ADMIN_PW, AUTH_COOKIE_SECURE=1, APP_BASE_URL) a `data/.mail_env` (MAIL_HOST/PORT/
  USER/PASS/FROM — SMTP pre reset-maily, viď #113). Bootstrap admin sa vytvorí pri štarte
  create-if-missing — reštart NIKDY neprepíše zmenené heslo.
- **Testy sú auto-prihlásené**: backend cez `authed_client()` z `tests/conftest.py` (autouse
  fixture seedne user store), E2E cez autouse session-cookie fixture v `tests/e2e/conftest.py`
  (fixture servery dostávajú `**_AUTH_ENV`). Test, ktorý MUSÍ začať odhlásený →
  `@pytest.mark.anonymous`. Nový fixture server v e2e conftest → pridaj ho do `_SERVER_FIXTURES`
  + `**_AUTH_ENV` do env.
- **`[hidden]` guard**: `[hidden]{display:none!important}` v style.css je NUTNÝ — sekcia
  s author `display:flex` (`#tab-search`/`#tab-notes`) inak prebije hidden atribút a presakuje
  do všetkých tabov (bug opravený v #104). Neodstraňuj; nové `#tab-*` sekcie ho dedia zadarmo.

## Screenshot OSTREJ appky bez reštartu živej služby (:8801)

**Od v0.44.0 vyžaduje login.** Throwaway inštancia si pri štarte načíta `data/.auth_env` →
bootstrapne reálneho admina do svojho tmp store — prihlás sa jeho údajmi, alebo daj do env
vlastné `ADMIN_EMAIL`/`ADMIN_PW` (env vyhráva nad súborom).

Náhľad reálneho vzhľadu (reálne dáta, nie fixture): bootni ODHODENÚ inštanciu na inom porte
`WEBREVIEW_PORT=8811 PYTHONPATH=src nohup .venv/bin/python webreview/app.py &`, Playwright
screenshot (LEN GET — nav prepínanie + tmavý mód sú bezpečné; NEklikaj row-toggly = POST do
živých dát), potom `kill`. NIKDY nereštartuj živú :8801 kvôli náhľadu.

Dva taby: **Kontrola párovania** (review kariet) a **Na objednanie** (doobjednanie u dodávateľa).

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

**Živo-odvodený AGREGÁT (napr. farba supplier chip-ov podľa stavu) čítaj z FLAG MÁP, nie z `o.*`, a prekresli ho v KAŽDOM toggli.** Polia `o.ordered/waiting/instock/unavailable` v `ORDERS` sú zamrznuté v čase `/api/orders` fetchu — toggle handler updatuje globálne mapy (`ORDERED/WAITING/INSTOCK/UNAVAIL`, synchrónne PRED `await` v `saveX`) + lokálnu row triedu, ale `o.*` NIE. Preto agregát (chip `done`/`todo`) počítaj cez `isHandled(o)=!!(ORDERED[o.key]||WAITING[o.key]||…)` a po každom toggli zavolaj `renderOrderFilters()` (prekreslí LEN `#filters`, nie riadky) — inak sa chip prefarbí až po reloade (bug #86). E2e MUSÍ klikať reálne tlačidlo v session (nie seed-before-load), inak nezachytí tento bug.

**Per-ORDER (nie per-line) store — vzor `order_comments` (#101):** keď hodnota patrí celej OBJEDNÁVKE (nie riadku), kľúč je **`<orderCode>`** (NIE `<orderCode>|<itemCode>`). `data/out/order_comments.json`, endpoint `/api/order-comment` (GET mapu / POST `{orderCode, comment}`, dĺžkový cap `ORDER_COMMENT_MAX`, prázdny = clear, login-gated). `/api/orders` doplní `comment` na KAŽDÝ riadok tej objednávky. Frontend: `ORDER_COMMENTS[o.orderCode]`, editor = **textarea** (nie 1-riadkový input; Ctrl/⌘+Enter uloží, plain Enter je nový riadok), po save volaj **`renderToOrder()`** (nie len replace riadku) — komentár je zdieľaný medzi VŠETKÝMI riadkami objednávky, presne ako per-produktový `assignedSupplier`. Voľný text ide cez `.textContent` (auto-escape, žiadny `escapeHtml`/`innerHTML`). Na riadku sa read-only zobrazuje aj Shoptet `shopRemark` (`build_to_order_rows` číta stĺpec exportu; `.to-shopnote`). Zápis komentára späť do Shoptetu → skill `shoptet` (order `shopRemark` write-back), odložené na follow-up.

Vstupy endpointov, čo píšu do CSV (kód/dodávateľ), MUSIA odmietnuť formula-injection: kód aj meno dodávateľa začínajúce `= + - @ \t \r` → 400; URL `^https?://`. **CSV sink prefixuje `'` cez `_csv_safe` — aj manuálny `/api/import` zip AJ nočný `upload-*` sink** (nočný píše naživo do eshopu, takže NESMIE byť slabšie chránený než zip).

**XSS — escapuj voľný text v KAŽDOM render-sinku, nie len v jednom.** `el(tag,cls,html)` používa `innerHTML`. Meno dodávateľa (voľný text manažéra) ide do 3 miest: 🏷️ menovka, **filter-button label** AJ **hlavička skupiny** — všetky 3 cez `escapeHtml(...)`. Escapnúť len menovku a zabudnúť na label/hlavičku = stored-XSS (našla to adversariálna revízia).

**Zápis do eshopu (write-back):** doplnený dodávateľ → 3. import súbor `import_suppliers.csv` (`code;pairCode;supplier`, vlastný stĺpec) v `/api/import` zipe + nočný `/api/n8n/upload-suppliers` (inkrementálny `uploaded_suppliers.json`, mirror `upload-pairings`). **`supplier` JE importovateľný stĺpec Shoptetu** — overené naživo 2026-06-29 (set `40256/L`=PAROVANIE-TEST → export read-back potvrdil → revert na ''), NIE textProperty-style tichý no-op. Pri akomkoľvek NOVOM zápisovom poli ale ZNOVA over import-settability naživo (export presence ≠ importable).

## In-app AUTOMATIZÁCIE (#93) — generický runner; nová automatizácia = registrácia, NIE nový scheduler

Appka má vlastný scheduler (`src/parovanie/automation_runner.py` — registry `Automation`
+ background-thread tick; štartuje sa LEN v `__main__`, testy vlákno nespúšťajú). Migrácia
ďalšieho n8n workflowu (#103/#105–#111) = **registrácia, nič viac**:

1. Pure logika do `src/parovanie/<key>.py` (vzor `posta_uncollected.py` — žiadna sieť/SMTP,
   všetko `today=`-injektovateľné, fixtures overené proti ŽIVÉMU API).
2. `run_<key>()` v `app.py` (sieť + `_send_mail_html` + vlastný stav-store 0600 atomicky)
   + `Automation(key=…, name=…, schedule={"daily_at":"HH:MM","tz":"Europe/Bratislava"},
   run_fn=…)` do `AUTOMATIONS_REG`. Endpointy `/api/automations*` (status/toggle/run) sú UŽ
   generické — netreba nové.
3. Frontend: záložka do `AUTOMATION_TABS` (renderuje sa v sidebar sekcii `#autoTabs`
   „Automatizácie") + `#tab-<key>` sekcia + `render<Key>()` — vzor `renderPosta`
   (per-item tab) alebo `renderShoptetSync`/`renderParovaniaEshop` (status-only tab).
   **Status-only tab s MALÝM výsledkom (len počty) čítaj z `a.last_result` PRIAMO**
   (vzor `renderParovaniaEshop`/`renderGrubeExternalcode` #62) — NErob osobitný
   `<key>.json` store + osobitný `<KEY>` global + `automations_server` fixture entry;
   `automation_runner.status()` už `last_result` vracia. Osobitný store (vzor
   `renderRestockSkladom` s globálom `RESTOCK`) treba LEN keď tab renderuje veľkú
   tabuľku riadkov, nie zopár čísel. Frontend ešte: `#tab-<key>` sekcia v `index.html`
   (pri ostatných), `render()` boolean + `auto` set + `hidden` toggle + dispatch riadok,
   `PAGE_TITLES`, cache-bust `?v=` bump. Backend NAV_KEYS + `AUTOMATION_DESCRIPTIONS`
   (drift-guard `test_nav_keys_match_appjs` + description test to vynúti).
   **Migrácia workflowu, ktorý VOLÁ EXISTUJÚCI endpoint appky** (napr. #109 nočný push
   párovaní/dodávateľov cez `/api/n8n/upload-pairings|upload-suppliers`) = **NEROB
   self-HTTP ani neduplikuj logiku**: vyextrahuj jadro endpointu do plain funkcie
   `_do_<x>(dry) -> (result, status)` (endpoint = auth + `jsonify(*_do_x(dry))`),
   a `run_<key>()` volá tie jadrá PRIAMO (žiaden bearer, žiaden localhost round-trip).
   Vzor `_do_upload_pairings`/`_do_upload_suppliers` + `run_parovania_eshop`. Overené:
   40+ pôvodných endpoint testov ostalo zelených (identický výstup). Idempotenciu drží
   existujúci `uploaded_*.json`; automatizácia číta manažérove decision/assign stores LEN
   na čítanie (čo pushnúť), nikdy ich nemení.
4. **BEZPEČNOSŤ (dohodnuté #93): nová automatizácia štartuje `enabled=false`** — beží až po
   ▶ Štart; `enabled` prežíva reštart (`data/out/automations.json`); zmeškaný beh počas
   výpadku sa preskočí dopredu.
   **GOTCHA (#62): nový ŽIVÝ zápis do eshopu NEZlievaj do UŽ ZAPNUTEJ automatizácie.**
   `parovania_eshop` je na prode `enabled=true`, takže pridať doň nové write-pole (napr.
   GRUBE `externalCode`, alebo split-linky #192) by ho na prode HNEĎ aktivovalo pri
   najbližšom behu — poruší #93 (nový živý zápis musí štartovať DISABLED). Preto GRUBE
   externalCode dostal VLASTNÚ default-disabled automatizáciu `grube_externalcode`
   (denne 03:30), nie ďalší krok v `parovania_eshop`. Rovnaké platí pre každý budúci
   nový write-feed — vlastná automatizácia = explicitný opt-in. „⚡ Spustiť teraz" beží aj vypnutá (explicitná akcia) a
   POSIELA REÁLNE e-maily zákazníkom pri KAŽDOM aktuálne nevyzdvihnutom balíku — pri overovaní
   na živom webe štandardne NEklikaj Spustiť teraz, toggle Štart→Stop stačí.
   **`parovania_eshop` (#109) PÍŠE do ŽIVÉHO eshopu** (nočný push párovaní/dodávateľov +
   od #38 aj inline `order_pairings`): pri post-deploy overení NEklikaj ani Spustiť teraz
   ANI Štart — manuálny beh by naplánoval reálny push tisícok nenahraných párovaní. Over
   LEN že tab existuje, tlačidlá prítomné (persistenciu pokrýva e2e). **GOTCHA (zistené
   #38, 2026-07-22): na PRODE je táto automatizácia UŽ `enabled=true` (manažér ju sám
   zapol) — nepredpokladaj default „Zastavené" pri post-deploy overení, over reálny stav
   cez `data/out/automations.json` alebo `GET /api/automations` PRED tvrdením o stave.**
   Pri `enabled=true` beží denne o 21:00 — ak veľká dávka (>~1000 riadkov) prekročí 120s
   `page.wait_for_url` timeout v `scripts/shoptet_import.py`, beh zlyhá bezpečne (nič sa
   nezapíše, retry ďalší beh) — viď #156. Manažér ho spustí/zastaví keď sám chce. **Výnimka (#126,
   bezpečné post-deploy overenie funkčnosti):** smieš kliknúť Spustiť teraz LEN keď si PRED tým
   z `data/out/posta_uncollected.json` overil `stats.uncollected==0` A `escalation=={}` (žiadna
   rozbehnutá eskalácia) — vtedy je isté, že beh pošle 0 reálnych mailov, aj keď osloví živé
   Pošta SK API. Po behu over `stats.emails_sent==0` v tom istom súbore (nie len UI).
- Pošta SK API fakty (live-overené 2026-07-22): `invalid_format` je PER-RESULT status
  (top-level je stále "ok") — 13-14-miestne numerické štítky ho vracajú vždy (to mesiac
  potichu rozbíjalo n8n); nevyzdvihnutá = posledný event `notified` + detailCode `ZNP*`.
  E-mail eskalácia sa bumpuje do stavu HNEĎ po každom sende (pád uprostred behu nesmie
  zajtra poslať duplicitný mail) a NEbumpuje sa pri zlyhanom SMTP (retry ďalší beh).
- **Carrier filter (#126) — dopravcu odvoď z BLOCKLISTU, nie z allowlistu.** Zásielkový
  export NIKDY doslovne nepíše „Slovenská pošta" — SHIPPING pseudo-položka (`itemCode` na
  `SHIPPING*`, `itemName`=názov dopravy) pre Pošta SK domové doručenie sa v reálnom exporte
  volá **„Kuriér"** (overené na živých dátach 2026-07-22: 223/228 takých objednávok má
  trackovacie číslo v Pošta SK formáte `EF…SK`). Allowlist podľa „pošt"/„Balík" by preto
  vyradil takmer VŠETKY reálne Pošta zásielky. Pri filtrovaní dopravcu z exportu vždy over
  reálne `itemName` hodnoty na živom `data/out/orders_cache.csv` PRED písaním filtra — text v
  zadaní/issue môže byť len predpoklad, nie skutočný string z exportu.
- **„BCC vždy" (#105/#126/#127) je teraz vynútené v kóde v OBOCH mail cestách, nie len
  konvenciou v docs**: `_send_mail_html(...)` (automatizácie) automaticky doplní `bcc=`
  z `MAIL_BCC` (`data/.mail_env`), keď volajúci `bcc` neuvedie explicitne (`bcc=""`
  explicitne vypne). `_send_mail(...)` (reset hesla, `/forgot` flow) rovnako VŽDY pridá
  `MAIL_BCC` do príjemcov, keď je nastavené — nemá `bcc=` parameter, takže sa nedá
  per-call vypnúť (jediný volajúci ho ani nechce vypínať). Nová automatizácia s
  vlastným e-mailom → stačí NEuvádzať `bcc=` a konvencia platí sama.
- E2E gotcha: `.pill` má CSS `text-transform:uppercase` → `inner_text()` vráti „ZASTAVENÉ";
  porovnávaj `evaluate("el => el.textContent")` (CSS transform nemení DOM text).

### AI-automatizácia (scraper) — vzor `dodavatelsky_sklad` (#106), podklad pre #105/#107/#108

Automatizácia, ktorá scrapuje externé weby a/alebo volá LLM (OpenAI). Pure jadro do
`src/parovanie/<key>.py` (bez siete/OpenAI, testovateľné s uloženými HTML fixtúrami + mock LLM JSON):

- **OpenAI kľúč = `data/.ai_env`** (`OPENAI_API_KEY`, gitignored, chmod 600) — načítaj v `app.py`
  cez `_load_env_file(os.path.join(ROOT, "data", ".ai_env"))` (rovnaký vzor ako `.auth_env`/`.mail_env`;
  env vyhráva nad súborom). **NIKDY nehardcoduj/necommituj kľúč** — žije v Authorization HLAVIČKE
  (`requests.post`), nie v URL, takže error-text nenesie tajomstvo (žiadny sanitizer netreba).
- **OpenAI cez `requests.post`, NIE openai SDK** (žiadna nová závislosť): `POST
  https://api.openai.com/v1/chat/completions`, `model="gpt-4o-mini"`, `response_format={"type":
  "json_object"}`, `temperature=0`; parse `choices[0].message.content` cez čistý `parse_llm_json`
  (tolerantný na ```json fence, `raise ValueError` na nevalidný → error riadok).
- **STATIC tier PRED LLM** (šetrí ~2/3 platených volaní): JSON-LD Product schema (`offers.availability`
  schema.org token → bool, `price`/`priceCurrency`) → og/product meta (`og:availability`/`product:
  price:amount`…) → **text-keyword klasifikácia LEN pre overené domény** (`is_static_text_domain`;
  4 domény huntingshop.eu/betalov.sk/zubicek.cz/virginiashop.sk — na neoverenej doméne loose text
  NEklasifikuj, radšej LLM). `need_llm = available is None OR price is None`. LLM sa volá LEN keď
  `need_llm` a je kľúč; **bez kľúča → `extractedBy="static-only"`, `ok=True` (graceful degrade, NEspadne)**.
- **Pozn.**: `export_helpers.state_of` klasifikuje NÁŠ 3-stavový eshop (vis+avail), NIE dodávateľovu
  dostupnosť — preto samostatný `classify_availability` (bool orderable), zdieľa len OUT-keyword slovník.
- **Náklady/robustnosť**: stale-skip (`is_recently_checked` — refetch len liniek nekontrolovaných >N h,
  error riadky sa retryujú vždy), per-doménová zdvorilosť (`_politeness_wait` cez `time.monotonic`),
  per-link try/except → error riadok, beh NIKDY nespadne. Zdroj liniek = export `internalNote` (http +
  visible) cez `links_from_export` — číta on-disk `SRC` (`data/products.csv`, refreshuje `shoptet_sync`),
  chýbajúci → 0 liniek. Store `data/out/<key>.json` (`{last_check, rows, stats}`) atomicky 0600.
- **Testy = mock OBE hrany**: `_fetch_supplier_html` (kanonické HTML) AJ `_llm_extract` (kanonický dict),
  0 siete. Over: static resolves → LLM sa NEvolá (`llm_calls==0` pre tú linku); no-key → static-only;
  fetch raise → error riadok nie pád; stale-skip nefetchne; disabled tick nebeží; nedotýka sa manager
  stores. **Default DISABLED** (scrape+LLM stoja) — pri post-deploy NEklikaj Spustiť teraz, len over
  tab existuje + Zastavené + tlačidlá.

### JOIN-automatizácia (žiadne škrabanie) — vzor `riziko_vypadku` (#107)

Automatizácia, ktorá NEROBÍ žiadnu sieť ani LLM — len SPOJÍ náš katalóg export s
`data/out/<inej-automatizácie>.json`, ktorý UŽ napísal INÝ automation run (napr.
`dodavatelsky_sklad` #106). Pure jadro je jedna funkcia `compute_risk(csv_text,
other_rows)` — číta export cez ten istý `_read_export_for_links()`/`internalNote`
join-kľúč ako scraper, mapuje `{link: row}` z `other_rows` (`by_link` dict),
a filtruje cez `export_helpers.state_of` (NIKDY nekopíruj 3-stavovú klasifikáciu
nanovo). **Absencia dát ≠ risk**: chýbajúci link v `by_link`, `ok=False` (chyba pri
scrapovaní), alebo `available is None` (nevie sa) sa VŽDY preskočí — nikdy sa
netvári ako "nie je skladom".

**Kontrakt pre tab, keď závislá automatizácia ešte nebežala**: `run_fn` vráti
(a store nesie) explicitný `has_<x>_data: bool` flag (`bool(other_rows)` — prázdne
`rows`/chýbajúci store = `False`), NIE len prázdny `risks: []`. Frontend potom
zobrazí „najprv spusti <závislá automatizácia>" namiesto zavádzajúceho „0 rizík"
(ktoré vyzerá ako čisté hlásenie, keď v skutočnosti nikto ešte nemeral). Tento
kontrakt je NUTNÝ pre KAŽDÚ automatizáciu, čo číta iný `data/out/*.json` store bez
vlastného scrapovania — kopíruj ho, nevymýšľaj vlastnú signalizáciu.

- **Registrácia**: rovnaký `Automation(...)` do `AUTOMATIONS_REG`, **default DISABLED**
  ako VŠETKY ostatné — aj keď je čisto READ-ONLY (žiadny e-mail, žiadny zápis, žiadna
  cena) sa drží konzistencie s `shoptet_sync` (tiež read-only, tiež default Stop);
  deploy nikdy nič sám nezapne.
- **Tab**: per-item tabuľka (vzor `renderPosta`/`renderDodavatelskySklad`) — žiadne
  nové CSS triedy netreba (`.autostatus`/`.posta-table`/`.avail`/`.downloads` sa
  recyklujú). Voliteľné „Stiahnuť CSV" = `_csv_response` + `_csv_safe` (rovnaký
  formula-injection guard ako `/api/import`).
- **E2E**: fixture server nasadí PRE-vypočítaný `data/out/<key>.json` priamo (žiadny
  reálny `products.csv`, žiadna sieť) — presne ako `supplier_stock.json` fixture v
  `automations_server`; test nikdy neklika „Spustiť teraz" (to by chcelo skutočný
  export na disku).

### WRITE-JOIN automatizácia (JOIN + zápis do eshopu) — vzor `restock_skladom` (#108)

JOIN-automatizácia ako #107, ale namiesto read-only **PÍŠE do živého eshopu** (napr.
reštok Vypredané→Skladom). Pure detekcia je v `src/parovanie/restock_skladom.py`
(`compute_candidates(csv_text, supplier_rows, now, max_pair_age_h)`) — mirror
`compute_risk`, len opačný smer: náš produkt `vis=='visible'` A **stav 2** (Vypredané)
cez zdieľaný `export_helpers.state_of` (NIKDY nekopíruj klasifikáciu; `state_of==2`
chytí AJ `availabilityOutOfStock`-only vypredané, kde `availabilityInStock==''` — presnejšie
než doslovný n8n `availabilityInStock=='Vypredané'`), A dodávateľ `ok && available is True
&& checkedAt čerstvé`. **Čerstvosť je NUTNÁ pre prod zápis**: `_is_fresh` prah `MAX_PAIR_AGE_H`
(48 h — presne n8n per-row check); prázdny/nevalidný/naivný `checkedAt` sa rieši (naivný dedí
`now.tzinfo`). Idempotencia = stav-2-only detekcia (už-Skladom produkt sa nikdy neflipne znova,
keď sa export obnoví); netreba osobitný „flipped codes" store.

- **Zápis = REUSE careful importu, NEreimplementuj**: `import_builder.restock_rows(candidates,
  code2pair)` postaví riadky vo whiteliste `RESTOCK_COLS` (OBE polia dostupnosti → `Skladom`,
  `visible`, `stock 5` — CEO 2026-07-14, dedup kódov ako `link_rows`). `run_<key>()` zapíše CSV
  v kánonickom dialekte (`utf-8-sig` BOM, `;`, CRLF, header=`RESTOCK_COLS`) a spustí PRIAMO
  `run_import` (ten istý ako `/api/n8n/shoptet-import` — záloha + safe-mode + #23 read-back),
  žiaden self-HTTP/bearer.
- **Bezpečné zlyhanie (ako `parovania_eshop`)**: `run_<key>()` NEVYHADZUJE výnimku pri zlyhanom
  importe — degraduje na `status='error'` (z `parse_import_log` read-backu, nie tiché „success");
  `_import_lock.acquire(blocking=False)` → ak beží iný import, `status='busy'` (preskočí, nie
  dvojitý import). `TimeoutExpired` z `run_import` → `rc=1`. Kandidáti sa ukladajú VŽDY (aj pri
  zlyhaní tab ukáže čo sa PROBOVALO naskladniť).
- **Default DISABLED je tu obzvlášť dôležité** (píše do prod eshopu) — deploy over LEN že tab
  existuje + Zastavené + tlačidlá; NIKDY neklikaj Štart ani Spustiť teraz (reálny prod zápis).
  `automations.json` bez kľúča = disabled (over že kľúč tam PO deployi NIE je).
- Testy: pure JOIN + `restock_rows` (hermetic), Flask wiring incl. **zlyhaný import → status=error**,
  **busy lock**, no-supplier-data → nič neflipne, both-availability-fields na import riadku,
  manager-store izolácia; e2e = `automations_server` s pred-vypočítaným `restock_skladom.json`.

- **Review „↩ Vrátiť" (undo) NEROBÍ re-enable do eshopu — to robí LEN táto nočná automatika (#97).**
  Review karta pri stave `unavailable` (Vypredané, stock 0) má pod „↩ Vrátiť" nenápadnú `.reenote`
  poznámku: reálne zapnutie (Vypredané→Skladom) spraví nočný `restock_skladom`, keď je produkt späť
  skladom. „↩ Vrátiť" (`saveDecision(p,'undo')` → `/api/decision` status=undo → `d.pop(key)`) len zmaže
  rozhodnutie lokálne, žiadny import/CSV/eshop zápis. **Poznámka je SCOPED len na `unavailable`** — NIE
  na `discontinued` („Už sa nebude predávať", detailOnly), lebo ten sa nočnou automatikou nezapína
  (poznámka by tam klamala). Pri zmene review-decision statusov over túto väzbu na automatiku, nech UI
  netvrdí re-enable, ktorý sa nestane.

### AI-EMAIL automatizácia (klasifikuj + pošli zákazníkovi mail) — vzor `orders_reminder` (#105)

Automatizácia, ktorá číta OBJEDNÁVKY (nie katalóg), LLM-klasifikuje voľný text a podľa výsledku
POSIELA reálny zákaznícky email. Pure jadro `src/parovanie/orders_reminder.py` (bez siete/OpenAI/SMTP):
`select_orders(csv, now)` (Vybavuje sa, dedup per code, >Nd), `build_reminder_email` (verbatim
n8n HTML šablóna, free-text escapnutý), `build_classifier_messages` + `parse_classification`
(faithful port n8n Text Classifier system promptu). Flask `run_orders_reminder()` drôtuje CSV +
OpenAI + `_send_mail_html` + store.

- **Zdroj objednávok = REUSE `_orders_csv_cached()`** (SHOPTET_ORDERS_URL CSV, 30-min cache) —
  NIE XLS `patternId=20` z n8n; žiadna nová závislosť (openpyxl), a rieši to „cachovanie" optim.
  Orders CSV má `shopRemark` (**interná** poznámka predajne, stĺpec 28) vs `remark` (poznámka
  ZÁKAZNÍKA) — klasifikuje sa `shopRemark`. Export NEMÁ admin `id` objednávky → admin link cez
  `posta_uncollected.ADMIN_ORDER_LINK` (globálne vyhľadávanie, reuse, needsIMPORT).
- **OpenAI = REUSE #106 infra, NErob nové** (`supplier_stock.LLM_MODEL` gpt-4o-mini, `OPENAI_URL`,
  `OPENAI_TIMEOUT`): `_classify_contacted()` = `requests.post` s `response_format json_object`,
  kľúč v Authorization HLAVIČKE (nie URL → error nenesie tajomstvo). **Bez kľúča → NEposielaj
  naslepo** (degraduj: „AI nedostupné", NEzapíš do store → retry ďalší beh); chyba klasifikácie/
  SMTP per-obj → log+skip, beh nespadne.
- **Dedup = per-order store `data/out/orders_reminder.json`** (`orders:{code:{status:emailed|
  skipped_contacted,date,...}}`) — objednávka už v store sa NEklasifikuje/NEposiela znova (max
  raz/obj, zrkadlí n8n Data Table). **Immediate-persist po každom úspešnom maile** (crash uprostred
  behu nesmie zajtra poslať duplikát — rovnaký vzor ako `run_posta_uncollected`).
- **Čistá stavová mašina** (keď issue chce „zjednotiť n8n vetvy"): prázdna poznámka → LEN červený
  alert (žiaden mail); s poznámkou → AI. n8n často mal duplicitný dvojtok (prázdna šla AJ do
  alertu AJ do mailu cez „BEZ POZNAMKY"→nekontaktovaný) — v appke to ZJEDNOTÍŠ (dokumentuj odklon).
- **Default DISABLED** (posiela reálne zákaznícke maily + stojí OpenAI) — deploy over LEN tab +
  Zastavené + tlačidlá; **NIKDY neklikaj Spustiť teraz** (mailoval by reálnych zákazníkov + minul
  OpenAI). Testy = mock OBE hrany (`_classify_contacted` AJ `_send_mail_html`, 0 siete/mailu):
  no-note→red, contacted→skip, not-contacted→mail-raz+dedup(druhý beh neposle), no-key→žiaden mail,
  classify/SMTP error→retry, BCC na drôte (`_FakeSMTP` cez reálny `_send_mail_html`), manager-store
  izolácia; e2e = `automations_server` s pred-vypočítaným `orders_reminder.json` (red+orange).
- **GOTCHA (python literál):** slovenské úvodzovky `„...”` s ASCII `"` ako uzáverom vnútri
  `"..."` reťazca predčasne UKONČIA reťazec (`SyntaxError: invalid character '„'`). Dlhý SK prompt
  s ukážkovými `"kľúčovými slovami"` píš ako **triple-quoted** `f"""..."""` (ASCII `"` je tam OK).

### Ručný per-riadkový OVERRIDE nad AI/automatizáciou (#153) — reuse existujúci store, nie nový

Keď manažér potrebuje priamo v tabe opraviť/obísť rozhodnutie automatizácie (napr. AI zle
vyhodnotilo, alebo riadok ešte nemá klasifikáciu) — vzor `orders_reminder` override
(`/api/orders-reminder/override`, akcie `contact`/`send`): **NEPRIDÁVAJ nový store súbor** —
display dáta v `red`/`orange`/`skipped` snapshote UŽ nesú všetky polia, čo override potrebuje
(meno, email, poznámka); endpoint ich len prečíta cez malý `_find_current_row(st, code)` helper
a zapíše do TOHO ISTÉHO per-code dedup store (`orders_reminder.json["orders"]`), presne ako
automatizovaný beh. Terminálny status (`emailed`/`skipped_contacted`) v tom istom slovníku
prirodzene dedupuje — override je len ĎALŠÍ spôsob, ako sa doň zapíše.

**GOTCHA — sieťové/SMTP volanie NIKDY pod globálnym `_lock`.** `_lock` (app.py:56) je JEDEN
zdieľaný zámok pre VŠETKY stores v appke — držať ho cez `_send_mail_html`/OpenAI call (až ~20s
SMTP timeout) by zmrazilo KAŽDÚ inú admin akciu na webe na tú dobu. `run_orders_reminder()` už
sieťové volania robí MIMO zámku (len zápis súboru je locknutý) — nový endpoint, čo volá von
(mail/AI), musí ROBIŤ TO ISTÉ: (1) krátky `with _lock:` na kontrolu stavu, (2) sieťové volanie
BEZ zámku, (3) krátky `with _lock:` na finálny zápis + **re-check stavu** (concurrentný
double-click medzitým mohol už zapísať terminálny status — re-check zabráni duplicitnému zápisu/
mailu). Nájdené vlastnou review-passou v #153 (žiaden subagent-dispatch tool v tomto prostredí —
review robil worker sám priamo nad diffom).

**GOTCHA — e2e fixtúra, čo klikne skutočnú `_send_mail_html`/`_send_mail` cestu, MUSÍ pripnúť
`MAIL_HOST=""` do `env`.** `_load_env_file()` číta `data/.mail_env` podľa ABSOLÚTNEJ repo cesty
(`ROOT` z `__file__`), NIE podľa izolovaného `WEBREVIEW_OUT` fixtúry — takže na dev boxe, čo má
reálny `data/.mail_env` (produkčné SMTP heslá) checked out, by neopatrený e2e klik na tlačidlo
posielajúce mail poslal SKUTOČNÝ mail cez reálne credentials (na CI bez súboru sa to prejaví len
ako `502` — nekonzistentné správanie medzi CI a dev boxom, kým sa nepripne). `os.environ.
setdefault()` nikdy neprebije UŽ nastavený kľúč, takže `"MAIL_HOST": ""` v subprocess `env`
dict-e (napr. `automations_server` v `tests/e2e/conftest.py`) vynúti deterministickú
not-configured vetvu na KAŽDOM stroji vrátane CI. Zistené live behom #153 (worker si na svojom
dev boxe omylom poslal test-mail na fixture adresu cez reálny SMTP relay).

### VALIDÁCIA + serve-time filter automatizácia (žiadny zápis do review_data.json) — vzor `image_health` (#135)

Automatizácia, čo periodicky OVERUJE (nie scrapuje/klasifikuje) niečo o dátach, čo appka UŽ má —
tu HTTP HEAD na každú `our_images` URL (naše vlastné cdn.myshoptet.com fotky) — a výsledok sa
NEZAPISUJE do `review_data.json` (na rozdiel od `resync_current`/WRITE-JOIN vzorov vyššie). Namiesto
toho automatizácia udržiava LEN vlastný per-URL cache store (`data/out/image_health.json`,
`{url: {ok, fails, checked_at}}`) a **existujúci serve endpoint** (`/api/products`) ho aplikuje AŽ
PRI REQUESTE (`image_health.clean_products` — shallow-copy len produktov, čo naozaj strácajú
obrázok; storage netknuté). Dôvod: obrázok, čo dnes zomrel, môže o týždeň znova žiť (dodávateľ/CDN
sa opraví) — zápis do `review_data.json` by vyžadoval ĎALŠÍ resync krok na obnovenie; serve-time
filter sa automaticky "opraví" na ĎALŠOM requeste, len čo cache záznam zastarne a ďalší beh ho
znova nájde živý. Tento vzor sa hodí pre KAŽDÚ "over či X ešte platí" automatizáciu, kde X sa môže
samo vrátiť do dobrého stavu — na rozdiel od JOIN/WRITE-JOIN vzorov, ktoré menia TRVALÝ stav
(eshop/review_data).

- **Anti-flap (transient blip ≠ mŕtve)**: až N (2) PO SEBE IDÚCICH zlyhaní → dead; úspech OKAMŽITE
  vynuluje streak. Bez tohto by jeden dočasný CDN výpadok vymazal dobrý obrázok z karty na celý
  deň (do ďalšieho behu). Kopíruj `needs_check`/`record_result`/`is_dead` vzor (fresh-window na
  potvrdené-OK URL, ale VŽDY re-check na URL, čo naposledy zlyhala — rýchle potvrdenie/vyčistenie).
- **HEAD s GET-Range fallback** (405/501 = host nepodporuje HEAD) — `stream=True` + `Range:
  bytes=0-1`, aby sa pri neúctivom serveri (ignoruje Range, vráti 200+celé telo) nesťahoval celý
  obrázok len na kontrolu živosti.
- **GOTCHA — `automations_server` e2e fixtúra NEMÁ `review_data.json`** (0 produktov) → pre TÚTO
  KONKRÉTNU automatizáciu (na rozdiel od VŠETKÝCH ostatných network/write automatizácií, čo v e2e
  NIKDY neklikajú „Spustiť teraz") je klik bezpečný a hermetický — beh nájde 0 URL, 0 sieťových
  volaní, dokončí sa okamžite s `checked=0`. Over si to najprv (`total_urls==0` scenár) predtým než
  napíšeš podobný "safe run-now" e2e test pre inú automatizáciu — inak riskuješ skutočný sieťový
  beh v CI.

## Pridanie plnej WORK záložky (nie automatizácia) — vzor `nedostupne`/`vystavy` (#100/#111)

Nová pracovná záložka s vlastným obsahom (nie per-riadkový flag, nie automatizácia). Checklist —
**vynechaný krok = tichý bug** (tab sa neprepne / nezobrazí / drift test padne):

1. **app.js**: `TABS += ['<key>', '<Label>']`, `NAV_ICONS.<key>`, `PAGE_TITLES.<key>`, vetva v
   `setPageHead` (pageSub), `navCount('<key>')`, `switchTab` (`if tab===key await load<Key>()`),
   `render()`: pridaj `const <key> = ACTIVE_TAB===key`, zahrň do `plain`, `#tab-<key>` hidden toggle,
   a **dispatch riadok** `if (<key>) { ...; render<Key>(); return; }` (PRED review/toorder blokom).
   `load<Key>`/`render<Key>` (globál `let <KEY> = null`).
2. **app.py**: `NAV_KEYS += "<key>"` (inak #173 rename → 400). Store + endpointy podľa potreby.
3. **index.html**: `<section id="tab-<key>" hidden></section>` + bumpni cache-bust `?v=` na app.js AJ style.css.
4. **GOTCHA — nový TAB rozbije DVE veci, oprav OBE:**
   - `tests/e2e/test_shell.py::test_nav_order_has_review_last` hard-koduje CELÝ zoznam `#tabs .tlabel`
     — pridaj nový label na správnu pozíciu (poradie = poradie v `TABS`).
   - `init()` má **whitelist `?tab=` deep-linku** (`qTab==='toorder'||'review'||...`) ktorý NOVÝ kľúč
     NEobsahuje → `/?tab=<key>` sa NEprepne. E2E preto naviguj KLIKOM na nav button
     (`get_by_role("button", name="<Label>")`), nie cez `?tab=` (ako `nedostupne`/`vystavy` e2e). Ak
     chceš deep-link, dopln kľúč do whitelistu.
5. **E2E fixture**: vlastný function-scoped server (vzor `nedostupne_server`/`vystavy_server`) +
   pridaj ho do `_SERVER_FIXTURES` (auth cookie). `MAIL_HOST=""` ak tab vie kliknúť send-cestu.
6. Karty (nie tabuľka) sú preferovaný layout pre manažérske taby (šéfova požiadavka #111) — grupuj
   podľa stavu, farebný `border-left`+badge, per-stav akčné tlačidlo, klik-na-hlavičku → inline
   detail/edit (`VY_OPEN` Set prežije re-render, takže po save ostane detail otvorený).

## Automatizácia BEZ nav tabu (background-only) — vzor `vystavy_otazka/_odpoved_*` (#111)

Keď automatizácia beží len na pozadí a NEMÁ mať vlastnú záložku (jej efekt vidno v inom WORK tabe):
registruj ju do `AUTOMATIONS_REG` + **`AUTOMATION_DESCRIPTIONS`** (description-completeness test
`test_ui_labels.py` iteruje VŠETKY `/api/automations` a vyžaduje neprázdny popis), ale **NEpridávaj**
kľúč do `AUTOMATION_TABS` (app.js) ani do `NAV_KEYS` (app.py) — `test_nav_keys_match_appjs` odvodzuje
`NAV_KEYS` z `TABS|AUTOMATION_TABS`, takže background kľúč v `NAV_KEYS` = drift fail.

**Ovládanie takej automatizácie patrí do HLAVIČKY jej WORK tabu (#198 FIX 1), NIE „len ručne v
`automations.json`".** Vzor `vyAutoPanel()`/`vyAutoRow(key)` v renderVystavy: kompaktný panel so
zoznamom kľúčov, každý riadok `autoByKey(key)` → názov+popis+stav+`next_run` + `toggleAutomation(key,
!a.enabled)` (Štart/Stop) + `runAutomation(key, '<tab>')` (⚡ Spustiť teraz). DVA drôty nutné, inak sa
panel po toggli neobnoví: (1) `load<Tab>()` musí volať `await loadAutomations()` (naplní `AUTOMATIONS`),
(2) `_reloadAuto(tab)` potrebuje vetvu pre ten tab (`if (tab==='<tab>') { await load<Tab>(); return; }`)
— `toggle/run` volajú `_reloadAuto(ACTIVE_TAB)`+`render()`, bez vetvy padnú do default `loadPosta`.
Reusuje sa existujúca `.pill on/off` + `.btn sm good/warn/ghost`. E2E: toggle on→off (fixture zdieľaný).

## „Poľovnícke výstavy" (#111) — IMAP reply-detekcia + Message-ID threading

- **Reply detekcia = ulož Message-ID pri odoslaní, matchni pri prijatí.** `_send_vystava_mail`
  (app.py) posiela s explicitným `msg["Message-ID"]=make_msgid(domain="forestshop.sk")` a VRÁTI ho
  (`_send_mail_html` vracia len `bool` → nestačí). Uloží sa do `email_*_msgid`; `vystavy_imap.match_reply`
  matchne `from==vystava.email` AND stored-msgid v `In-Reply-To`/`References` odpovede (msgid rozlíši
  keď 1 organizátor má viac výstav). `trim_quote` odreže reply-chain (SK „Dňa … napísal:" má meno PRED
  `:` → marker `D[ňn][ae] .*nap[íi]?sal.*:`, nie len `napísal:`).
- **IMAP creds z `data/.mail_env`**: `IMAP_HOST` (default `mbox.myshoptet.com`), `IMAP_PORT` (993),
  reuse `MAIL_USER`/`MAIL_PASS`; self-signed → `ssl.CERT_NONE`. `fetch_inbox` degrade→`[]` (automat nespadne).
- **Migračný store**: `scripts/migrate_vystavy.py` → gitignored `data/out/vystavy.json` (jednorazovo pri
  deployi, `--force` na re-migráciu; app toleruje chýbajúci súbor = 0 výstav). Pri deployi ho MUSÍŠ spustiť.
- **Formula-guard LEN na polia, čo idú do CSV/formula sinku (#198 FIX 2)**: `_vy_clean_fields` strážila
  formula-lead (`= + - @`) na VŠETKÝCH edit poliach — lenže `tel` (`+421 …` legitímne začína `+`) ani
  `kontakt_osoba` nejdú do žiadneho CSV (maily interpolujú len `nazov/datum/velkost_stanku`), takže guard
  blokoval platné dáta (edit form posiela VŠETKY polia → jeden `+` telefón znemožnil uložiť celú výstavu).
  `VY_NO_FORMULA_GUARD=("tel","kontakt_osoba")` ich vyníma; ostatné polia ostávajú strážené. Pravidlo:
  formula-guard patrí len na pole, čo naozaj tečie do CSV/formula sinku, nie na plain-text kontaktné pole.
- **Send tlačidlá STRÁŽIA vstupný stav (#198 FIX 3)**: `posli-otazku` smie ísť LEN z `VY_NEW` (inak 409,
  re-check aj po maili — ako `ideme` stráži `VY_AKCIA`), inak by priamy API na výstavu v „poziadane"/
  „odpovedane" resetol stav→otazka a re-mailoval organizátora. Každé nové send tlačidlo pridaj s
  rovnakým vstupno-stavovým guardom.
- **Chain-A summary shape (#198 FIX 4)**: `run_vystavy_otazka` vracia `{poslane, preskocene, …}`
  (`preskocene` = `len(all_vystavy)-len(candidates)`, výstavy preskočené kvôli mesiacu/stavu/pdf/mailu) —
  spec `design.md:145` predpisuje `{poslane, preskocene}`, zvyšok je superset.

## Záložka „Vývoj" (#115, v0.59.0) — GitHub issues + žiarovka nápad→issue

Samostatná nav „Vývoj" DOLE (`#devNav`, mimo priečinka, pre KAŽDÉHO prihláseného —
NIE admin-only ako Užívatelia) vypíše GitHub issues repa (open+closed, PR-ka
odfiltruj cez `pull_request` kľúč). Fixná žiarovka vpravo dole (`#ideaBtn` + modal
`#ideaModal`) vytvorí issue → objaví sa v zozname.

- **Token = backend-proxy, NIKDY do prehliadača.** `data/.gh_env` (`GITHUB_TOKEN` +
  `GITHUB_REPO`, gitignored 600) sa načíta cez `_load_env_file` (ako `.auth_env`/
  `.mail_env`/`.ai_env`). Token žije LEN v server-side `Authorization: Bearer`
  hlavičke (`_gh_headers`) — nikdy URL/log/JS. Endpointy `/api/dev/issues` (list,
  bounded `GH_MAX_PAGES`=5 pagination — `/issues?state=all` vracia issues AJ PR-ka
  premiešané podľa updated, takže jedna 100-stranka by mohla odrezať staršie issues
  po odfiltrovaní PR) + `/api/dev/idea` (create; title povinný/capnutý, rate-limited
  per user). **Chýbajúci/neplatný token → graceful `{available:False}`, NIKDY 500** —
  tab aj žiarovka to zvládnu. `GITHUB_API_BASE` env override = pointne na iný base
  (e2e stub).
- **E2E hermeticky** (`tests/e2e/test_dev.py` + `dev_server` fixture): fixture bootne
  malý `http.server.ThreadingHTTPServer` GitHub-stub (GET `/issues` → canned open+
  closed+PR, POST → append+echo) a appku spustí s `GITHUB_TOKEN`/`GITHUB_REPO`/
  `GITHUB_API_BASE`→stub. Pridaj `dev_server` do `_SERVER_FIXTURES` (auth cookie).
  Backend testy (`tests/test_webreview_dev.py`) mockujú `webapp.requests.get/post`;
  autouse guard delenv-ne reálny `GITHUB_TOKEN` (dev box ho má z `.gh_env`) + spraví
  z requests raising stub → žiadne reálne volanie omylom.
- **GOTCHA — `get_by_role("button", name="Vývoj")` chytí AJ žiarovku** (jej
  `aria-label="Zapíš nápad na vývoj"` obsahuje „vývoj", substring match) → strict-mode
  chyba. Nav klikaj scoped: `page.locator("#devNav button")`. `.dev-state` pill má
  `text-transform:uppercase` (ako `.pill`) → `inner_text()` vráti „OTVORENÉ"; assertuj
  cez triedu `.dev-state.open`/`.dev-state.done`, nie text.
- **GOTCHA — po create je GitHub list eventuálne konzistentný**: `loadDevIssues()`
  hneď po POST create môže vrátiť ešte starý zoznam (nová issue tam ešte nie je);
  reload (klik nav) o pár s ju už ukáže. Post-deploy: smieš vytvoriť 1 test issue cez
  živú žiarovku a hneď ju `gh issue close` (overené #149 pri v0.59.0).

### Šéf riadi vývoj U NÁS — GitHub je ÚPLNE skrytý (#170, v0.69.0)

Šéf GitHub „vôbec nezaujíma" — píše detaily a prioritizuje LEN v appke, backend to potichu premietne na GitHub. Vzor **write-behind** (nikdy neukazuj surové GitHub veci):

- **Doplniť detail k issue** = GitHub **komentár** (`POST /repos/{repo}/issues/{n}/comments`), NIE prepis tela — non-destruktívne. Endpoint `/api/dev/issue/<int:n>/note` (text povinný/capnutý `NOTE_MAX`, rate-limited zdieľaným `_idea_rate_limited`, autor-suffix `_Doplnené cez appku (Vývoj) — <email>_`). Frontend: inline `.dev-note-box` (textarea + „Uložiť detail"), po úspechu „✓" bez reloadu.
- **Priorita čoskoro/neskôr** = dva SKRYTÉ labely `prio:soon`/`prio:later` (`PRIO_LABELS`). `_slim_issue` label **dvihne** do poľa `priority` ('', 'soon', 'later') a **strhne** ho z `labels` (šéf nikdy nevidí `prio:*`). Endpoint `/api/dev/issue/<int:n>/priority` (soon|later|none): pridaj zvolený, zmaž opačný. `renderDev` grupuje zoznam: `🔴 Riešiť čoskoro` hore → bežné → `🟡 Riešiť neskôr` dole; per-riadok `.dev-prio` buttony (klik na aktívny = clear na none).
- **GOTCHA — DELETE label musí URL-encodnúť meno**: `quote(name, safe='')` → `prio%3Asoon` (dvojbodka), inak 404/nezmaže. Pred add labelu ho **ensure-ni** (`POST /repos/{repo}/labels`, 422 already-exists je OK) nech existuje. 404 pri delete (label nebol) je OK — ignoruj.
- **E2E stub musí vedieť aj comments/labels/delete** (`_GHStub` v `tests/e2e/conftest.py`): `do_POST` vetvi na `/issues/<n>/comments` (bump count), `/issues/<n>/labels` (mutuj `it["labels"]`), `/labels` (ensure), inak create-issue; `do_DELETE` na `/issues/<n>/labels/<encoded>` (odstráň z issue). Tak e2e overí prioritný split naživo. Backend testy: guard mockuj aj `webapp.requests.delete` (nielen get/post).
- **Post-deploy overenie bez špiny**: prioritu nastav→vyčisti (`none` zmaže label, GitHub čistý); detail-komentár je trvalý → píš ho len na VLASTNÝ tracking issue („overené naživo"), nie na cudzí. Reálne over cez `gh issue view <n> --json labels,comments`.

## Admin premenovanie záložiek + popis automatizácií (#173, v0.70.0)

Šéf chcel (1) jasný SK popis čo/kedy automatizácia robí a (2) vedieť premenovať KAŽDÚ záložku
(pracovnú aj automatizačnú aj Užívatelia/Vývoj) — nie len automatizácie.

- **Popis = samostatný `AUTOMATION_DESCRIPTIONS` dict** (kľúč=`Automation.key`, hodnota=SK text
  „čo + kedy"), merge-nutý do `/api/automations` (`a["description"] = AUTOMATION_DESCRIPTIONS.get(...)`).
  **NIE pole na `Automation` dataclass** — panel nikdy nerenderuje `a.name` (len nav label + page title),
  takže žiadna zmena v `automation_runner.py` netreba. Frontend: `.autodesc` div hneď za `st.appendChild(head)`
  vo VŠETKÝCH 8 render*() automatizačných funkciách (identický 2-riadkový vzor, `replace_all` Edit).
- **Premenovanie = JEDEN generický store `data/out/ui_labels.json`** (`{nav_key: label}`), endpointy
  `GET /api/ui-labels` (hocikto prihlásený — inak by renamed label nevidel non-admin používateľ)
  + `POST /api/ui-label` (admin-only, `_admin_or_none`/`_forbidden` ako `/api/users`; prázdny label = clear).
  Renaming automatizácie JE renaming jej záložky — žiadna samostatná "name override" logika netreba,
  lebo `Automation.name` sa nikde nerenderuje.
- **GOTCHA — nav kľúč ≠ `Automation.key` pre „Pošta" tab!** `AUTOMATION_TABS` v app.js má `['posta', …]`
  (nav/page-title kľúč = `posta`), ale `Automation(key="posta_uncollected", …)` v app.py — DVA rôzne
  stringy pre TÚ ISTÚ automatizáciu (legacy). Server-side `NAV_KEYS` validácia MUSÍ byť explicitný
  literál set kopírujúci `app.js`'s `TABS`+`AUTOMATION_TABS` (vrátane `"posta"`), **NIE**
  `{a.key for a in AUTOMATIONS_REG}` — to by odmietlo `"posta"` (chýba v registry) a prijalo
  `"posta_uncollected"` (nikto ho nikdy nepošle). Test na to: `test_automation_registry_key_rejected_for_posta`.
- **Admin-only ✏️ vedľa nav buttonu — `.navrow` wrapper, NIE úprava existujúceho `.tab` markupu.**
  `_navButton()` teraz vracia `<div class="navrow">` (tab button + voliteľný `.navedit` button, len keď
  `isAdmin()`). `.navrow .tab{flex:1;width:auto}` MUSÍ prísť ZA `.tabs .tab{width:100%}` v CSS (rovnaká
  špecificita, cascade poradie rozhoduje). Edit button má VŽDY generický `aria-label="Premenovať"`
  (NIKDY meno záložky) — inak by kolidoval s `get_by_role("button", name=<label>)` presne ako
  žiarovka/„Vývoj" gotcha vyššie. Cielenie z testov: `[data-testid="navedit-<key>"]`.
- **GOTCHA — existujúce e2e `#devNav button` (bez `.tab` scope) sa POKAZILI**, lebo teraz `#devNav`
  obsahuje 2 buttony pre admina (nav + ✏️) → `count()==1` padne a `.click()` na 2 elementoch hodí
  strict-mode chybu. Fix: scope na `.tab` triedu (`#devNav .tab`), nie bare `button` — oprav VŠADE, kde
  test klika/počíta nav button v kontajneri s NEZNÁMYM počtom detí (týka sa `#usersNav`/`#autoTabs`
  rovnako, keby tam niekto pridal podobný unscoped selektor). Selektory s `.filter(has_text=…)` alebo
  `get_by_role(name=…)` OSTÁVAJÚ bezpečné (✏️ button nemá zhodný text/name).
- Rename = natívny `prompt()` (nie inline input) — MVP, žiadny nový modal/CSS. E2E: `page.once("dialog",
  d => d.accept("text"))` PRED klikom na `.navedit`; prázdny string = clear/revert.

## Favicon + edit-mód pre ceruzky (#175/#176, v0.77.0)

- **#175 branding**: `<title>` je GENERICKÉ `Forestshop` (nie per-view „Kontrola párovania…"; JS
  title NEprepisuje — statický `<title>`). Favicon = inline SVG data-URI (`<link rel="icon" ...>`
  v `<head>` — biely štít na brand-zelenom `#356B32` štvorci, ten istý mark ako `.brand .logo`).
  `.brandtxt small` subline = „Firemný systém" (bol „Párovač & dashboard"). **`test_branding.py`
  je LOCK na `<title>` + `.brandtxt` — pri zmene brandingu ho updatni (asertuje presný `<title>`).**
- **#176 edit-mód**: `.navedit` ceruzky sú DEFAULT `display:none`; admin ich odkryje cez `body.edit-labels`
  CSS switch, ktorý toggluje footer tlačidlo `#editLabelsBtn` („Upraviť názvy", `.editbtn` v `.sidefoot`).
  Stav v `localStorage('editLabels')`, default OFF. **Vzor pre admin-only globálny UI mód** = `initEditLabels()`
  (unhide tlačidlo + wire toggle) sa volá v `init()` AŽ PO `ME` fetchi (potrebuje `isAdmin()`), presne
  ako by mal každý admin-gated init — NIE v `initTheme`/`initFolders` bloku (tam ešte `ME` nie je).
- **E2E gotcha — ceruzka už NIE je default viditeľná**: každý test čo klika `.navedit`/`[data-testid=navedit-*]`
  MUSÍ najprv zapnúť edit-mód (`page.locator("#editLabelsBtn").click()` + počkať `.navedit` visible) —
  inak Playwright `.click()` timeoutne na `display:none`. Non-admin: ceruzky (count 0) AJ `#editLabelsBtn`
  (hidden) neviditeľné. Folder expand/collapse (`initFolder`) #176 NEmení — ostáva funkčné.

## Deploy = reštart služby (data/out PREŽIJE) — over počty pred/po

`systemctl --user restart parovanie-web` (WorkingDirectory == repo, `.venv/bin/python webreview/app.py`, `:8801`, verejne `parovanie-forestshop.newlevel.media`). `data/out` je gitignored → checkout/restart sa ho NEDOTKNE. **Vždy over data-safety**: spočítaj entries v `ordered_items.json`/`order_pairings.json`/`waiting_items.json`/`supplier_assignments.json` PRED a PO deployi (musia sedieť) a `/api/version` == nasadená verzia. Tunel/systemd detaily → `.claude/skills/deploy`.

## Discord notifikácie = n8n, NIE Flask (a draft/publish gotcha)

Web NEposiela Discord priamo. Nočné workflowy v n8n volajú endpointy a ony posielajú do Discordu:

- **`/api/n8n/upload-pairings`** (nahrá nové párovania → eshop internalNote, kľúč `uploaded_pairings.json`) ← n8n workflow **„Forestshop — Párovania → eshop"** (`YuDugCCOnwejRfva`, denne 21:00). Endpoint na KAŽDEJ ceste vracia súhrnné počty pre n8n: `count` (nové), `total_uploaded`, `total_products`, `remaining`, `review_url`, voliteľne `blocked` (napárované, čo sa nedalo nahrať — chýbajú variant kódy → notifikátor varuje namiesto ticha) — n8n `Sprava` node z nich poskladá **JEDNU** súhrnnú správu (nie detail za každý produkt). **Počty sú ohraničené na živý review set**: `total_uploaded` ráta len kľúče stále v `PRODUCTS` (inak by ratio prekročilo total, napr. „Spolu 105 / 100"); `_load_uploaded` coercne ne-dict stav na `{}`.
- **`/api/n8n/shoptet-import`** (reštok vypredané→skladom) ← iné workflowy.

**GOTCHA (#49) — pri čiastočnej dávke označuj „uploaded" LEN kľúče, čo naozaj vygenerovali riadok — nikdy celý input-selection set.** `_do_upload_pairings` vyberie `new_keys` (kandidáti), ale `link_rows` z nich vie vyprodukovať MENEJ riadkov než kľúčov — produkt bez `variant_codes`, alebo kód zdedupovaný ako „seen"-loser inej položky v TEJ istej dávke (viď PASCA duplicitný `code` v `.claude/skills/shoptet`). Ak by sa po úspešnom importe do `uploaded_pairings.json` zapísali VŠETKY `new_keys` (nie len tie s riadkom), bezkódová položka sa navždy stratí — nikdy sa neprepošle, žiaden budúci beh ju neskúsi znova. **Fix: odvoď `uploaded_keys` z kódov skutočne prítomných v `rows`** (`written_codes = {r[0] for r in rows}`, potom kľúč je „uploaded" iba ak `written_codes & set(variant_codes)` prienik). Zvyšné kľúče ostanú „nové" (retry budúci beh) a rátajú sa do `blocked` (rovnaká sémantika ako celo-dávkový `blocked` prípad). **`_do_upload_suppliers` má TÚTO triedu bugu vylúčenú stavbou** — `supplier_rows` ide 1:1 podľa `assignments.items()` (žiadna produkt→variant_codes indirekcia), takže vstupný kľúč VŽDY vyprodukuje presne jeden riadok; netreba tam rovnaký odvodzovací krok.

**GOTCHA (n8n MCP): `update_workflow` zapíše len DRAFT.** Aktívny (naplánovaný) beh ďalej používa STARÚ `activeVersionId`, kým nezavoláš **`publish_workflow`**. Po každej zmene uzla: `update_workflow` → `publish_workflow` → over `get_workflow_details` že `versionId == activeVersionId` a `activeVersion.nodes` má novú zmenu. Bez publish sa zmena navonok „neudeje". Over správu cez `test_workflow` s pinnutým HTTP node-om + `get_execution includeData` na `Sprava`. **POZOR: pinne sa LEN to, čo dáš do `pinData` argumentu — Discord node sa NEpinne automaticky!** Incident 2026-06-29: test s pinnutými len HTTP nodmi POSLAL reálnu Discord správu s testovými číslami do Marekovho kanála. Ak nechceš reálny send, daj do `pinData` AJ `"Discord": [{"json": {}}]`. Pin je jednorazový (argument volania, do workflowu sa NEuloží — plánovaný beh ním nie je ovplyvnený).

**Bezpečnostný dlh (pre-existing):** HTTP node `Nahraj parovania` má bearer token (`N8N_IMPORT_TOKEN`) **natvrdo v hlavičke** — n8n hlási `HARDCODED_CREDENTIALS`. Lepšie cez n8n credential (httpHeaderAuth). Token žije aj v `data/.shoptet_admin` (gitignored).

## Dve úložiská párov → eshop `internalNote` (KTORÉ kam tečie)

**Od #38 (v0.63.0) ideš OBOMI cestami NOČNE aj RUČNE** — predtým `order_pairings` išli
na eshop LEN cez ručný zip; teraz `_do_upload_pairings` (zdieľané jadro pre
`/api/n8n/upload-pairings` AJ pre in-app automatizáciu „Párovania → eshop") pushne OBE
do JEDNÉHO combined `import_links.csv` v tom istom behu:

| Store | Kľúč | Na eshop cez | review_data nutné? |
|---|---|---|---|
| `decisions.json` | review **`key`** = `SUPPLIER\|pairCode` | ručný zip (`/api/import`) AJ nočne `/api/n8n/upload-pairings` | **ÁNO** — pri štarte sa decision s kľúčom mimo review_data **TICHO zmaže** (`app.py` prune) |
| `order_pairings.json` | forestshop **kód** (ľubovoľný) | ručný zip AJ nočne (`_do_upload_pairings` → `order_pairing_rows(..., exclude_codes=<kódy už v decision rows>)`) | nie |

`order_pairings` kód pokrytý reviewed decisiou v TOM ISTOM behu sa **vynechá** (Shoptet
padá na duplicitný `code` v jednom importe — decision vyhráva). Dedup nočného stavu pre
`order_pairings` žije v TOM ISTOM `uploaded_pairings.json` ako decisions, ale pod
**`order:<code>`** namespace (`import_builder.new_order_pairing_keys`) — nikdy sa nekríži
s review kľúčmi (`SUPPLIER|pairCode` vždy obsahuje `|`, nikdy nezačína `order:`). Odpoveď
endpointu/`run_parovania_eshop` má vlastné `order_count`/`order_blocked` polia (oddelené od
`count`/`blocked`, ktoré ostávajú len pre decisions) — UI tab to zobrazuje ako samostatný
riadok „📦 Inline páry".

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

## e2e gotcha — `[data-testid=version]` NIE je „init hotový" wait (flaky title race)

Shell E2E, čo overuje stav PO `init()` (napr. default `#pageTitle`), NESMIE gate-ovať len na
`page.wait_for_selector('[data-testid="version"]')` — verziový `<span>` je v DOM od prvého
vykreslenia (zobrazuje `…` kým nedobehne fetch), takže ten wait prejde OKAMŽITE, ešte pred
`init()`. `index.html` navyše dodáva STATICKÝ `<h1 id="pageTitle">Kontrola párovania</h1>`, ktorý
`init()→render()→setPageHead()` async prepíše na default „Na objednanie" — na pomalom CI teda
assert prečíta ten statický titulok a padne (green na `push` behu, red na `pull_request` behu TOHO
istého commitu = klasický race). **Fix: čakaj na vykreslený nav `page.wait_for_selector(".sidebar
#tabs button")`** (renderTabs beží v tom istom `render()` ako setPageHead) a/alebo
`wait_for_function` na cieľový text titulku — až potom assertni. Platí pre KAŽDÝ shell test
čítajúci post-init stav.

## Živé Playwright overenie bez znečistenia dát

To-order flagy píšu do živých stores. Pri overovaní na živom webe **toggluj on→off** (skonči v pôvodnom stave) a potom over `data/out/<store>.json` že je zase `{}` (resp. pôvodný počet) — nikdy nenechaj reálnu objednávku označenú z testu.

## Gotcha — `.card{display:grid}` deti potrebujú `min-width:0`, inak sa button „stratí" na úzkom displeji

`.card` je CSS Grid (`grid-template-columns:1fr 1fr`, mobil `1fr`). Grid ITEM (`.side.left`/`.side.right`) má bez `min-width:0` automatickú minimálnu šírku = min-content jeho OBSAHU — dlhý nezalomiteľný text (candidate name, URL) v `.manualrow`/`.cand` vnútri vie natiahnuť grid TRACK ďaleko za viewport; `.card` samo zostane správne úzke (`overflow:hidden`), ale JEHO VNÚTRO pretečie a zelené tlačidlo („Uložiť URL"/„Vybrať") skončí v odrezanej oblasti — neviditeľné a neklikateľné (#82). Samotné `flex-wrap`/`min-width:0` na `.manualrow`/`.cand` NESTAČÍ, ak `.side` sám o sebe nemá `min-width:0` — fix musí byť na GRID ITEME (`.side{min-width:0}`), flex-level úpravy sú len defense-in-depth. **Krátky test fixture (krátky názov/URL) bug NEREPRODUKUJE** — nič sa nemusí zmenšovať, takže RED nikdy nenastane; na overenie/regression e2e treba REALISTICKY DLHÝ obsah (skutočná dĺžka candidate name + supplier URL). Diagnostika: `page.evaluate("el => ({sw: el.scrollWidth, cw: el.clientWidth})")` na `.card`/`.side.*` — `scrollWidth > clientWidth` = vnútri pretieklo.

## Gotcha — `gh pr edit` / `gh issue edit` na tomto repo ZLYHÁ (classic Project)

Repo má pripojený classic GitHub Project → `gh pr edit`/`gh issue edit` GraphQL mutácia padá na `Projects (classic) is being deprecated … (repository.pullRequest.projectCards)` a **nič nezmení** (titulok/telo ostanú staré). Použi REST:

```bash
gh api -X PATCH repos/zbynekdrlik/parovanie-produktov/pulls/<N> \
  -f title="…" -F body=@body.md --jq '.title'
```
(READ cez `gh pr view --json …` funguje normálne; len edit mutácia padá.)

## Gotcha — dva „zjavné DRY" ciele v tejto appke NEROB (#12) — over si skryté API/výkonové väzby PRED refaktorom

Starý audit (#12) navrhoval ĎALŠIE dva dedup kroky nad tie, čo sú už hotové (`csv_loader.load_code2pair`, `writer.shoptet_writer`) — pri overení sa oba ukázali ako ZLÝ nápad, nie len „netreba":

- **`scripts/shoptet_import.py`'s `print()` riadky NIE sú kozmetické logovanie — sú to load-bearing dáta.** `webreview/app.py::run_import()` spúšťa tento skript ako subprocess a **zachytáva jeho stdout** (`subprocess.PIPE`); `_import_rows_chunked()` volá `parse_import_log(out)`, ktorá REGEXOM z toho istého textu vyťahuje `spracované=/upravené=/zlyhania=` pre agregáciu chunkovaného importu (`restock_skladom`, `parovania_eshop`, `/api/n8n/shoptet-import`). Prerobenie na `logging` (default ide na stderr, iný formát) by ticho rozbilo tento parse. Ak niekedy treba tieto print() naozaj zlogovať, musí sa to spraviť tak, aby `run_import`/`_import_rows_chunked` dostali výsledok INAK (napr. štruktúrovaný návrat namiesto textového stdout parsovania) — nie holým s/print/log/.
- **`webreview/app.py::_load_catalog()` zámerne robí JEDEN cp1250 prechod** cez `data/products.csv`, čo postaví AJ `CODE2PAIR` AJ `CATALOG` (vyhľadávací index #115) z tých istých načítaných riadkov. Presmerovanie `CODE2PAIR` cez zdieľaný `csv_loader.load_code2pair` (ten ho stavia sám, bez `rows`) by vyžadovalo DRUHÝ celý prechod súborom — zdvojenie I/O pri každom štarte appky aj pri `/api/resync`, nie čistý dedup.

Overuj PRED refaktorom, či „duplicitný" kód v skutočnosti nenesie skrytú závislosť (parsovaný výstup, jednoprechodový výkon) — CLAUDE.md's „NEkopíruj logiku" pravidlo pre `csv_loader`/`writer` sa vzťahuje na NOVÝ kód, nie na tieto dve už-zámerne-oddelené miesta.

## Nedostupné tovary tab (#100, v0.71.0) — flagged-unavailable → preview-gated zákaznícky e-mail

Samostatný WORK tab „Nedostupné tovary" (`#tab-nedostupne`, v TABS hneď za „Na objednanie") zbiera
na jednom mieste každý produkt, ktorý manažér označil „nedostupné u dodávateľa" na tabe Na
objednanie, napáruje ho na otvorené objednávky (zákazníkov) a nechá poslať zákazníkovi jeden z 2
e-mailov — VŽDY za náhľadom, nikdy auto.

- **Zdroj zoznamu = EXISTUJÚCI `unavailable_items.json`** (#84 per-line flag `<orderCode>|<itemCode>`).
  `nedostupne.unavailable_item_codes` z neho vytiahne distinct itemCode-y → `affected_orders`
  napáruje VŠETKY otvorené („Vybavuje sa") order-lines s tým EXAKTNÝM variant kódom (nie pairCode —
  size L sa neupozorní keď je nedostupné len M). Pure logika v `src/parovanie/nedostupne.py`
  (žiadna sieť/SMTP/súbor — testovateľné s fixture CSV + mock mailom).
- **2 „štvorčeky" = nový store `data/out/nedostupne.json`** per itemCode: `{nedostupne, alternativa,
  sent:{"<orderCode>|<type>":{at,email}}}`. Vzor per-flag store, ALE per-PRODUKT (itemCode), nie
  per-line. Checkbox = len intent (`/api/nedostupne/state`), NEposiela.
- **Bezpečné odoslanie (2 endpointy):** `/api/nedostupne/preview` (vráti príjemcov po dedupe +
  vyrenderovaný e-mail, NEposiela — modal ho ukáže v `<iframe sandbox srcdoc>`) → `/api/nedostupne/send`
  (pošle len tým, čo ešte nedostali; `plan_sends` dedupuje persistentne per order+type AJ per e-mail
  v rámci dávky). SMTP je MIMO `_lock` + per-recipient re-check pod lockom (vzor `orders_reminder/override`),
  immediate-persist po každom úspechu (crash mid-batch neposle duplikát). BCC owner automaticky
  (`_send_mail_html`). E-mail HTML = ten istý štýl ako `orders_reminder.build_reminder_email`.
- **Alternatívy = `relatedProduct*` z `data/products.csv`** (POZOR na názvy stĺpcov — viď skill
  `shoptet`). `_ensure_nedostupne_catalog()` = LAZY {code|pairCode→name} + {code|pairCode→related
  codes} sken exportu na PRVÉ otvorenie tabu (reset na resync spolu s `_CODE2URL`). URL alternatívy
  z marketing XML `_CODE2URL`, fallback `forestshop /vyhladavanie/?string=<kód>` (vždy klikateľné).
- **E2E fixture `nedostupne_server`** (funkčne-scoped): seedne `unavailable_items.json` +
  fresh `orders_cache.csv` + `products.csv` s relatedProduct stĺpcami; **`MAIL_HOST=""`** poistka
  (žiadny reálny send). Test klikne LEN náhľad (neklikaj Odoslať — 502 na nenakonfigurovanom SMTP).
- **GOTCHA — `page.wait_for_selector("#el[hidden]")` NEČAKÁ na skrytie**: default state je „visible",
  a `[hidden]` element nikdy nie je visible → timeout. Na čakanie na SKRYTIE modalu použi
  `page.wait_for_selector("#el", state="hidden")`.
- **GOTCHA — nový TAB v TABS rozbije `test_shell.py::test_nav_order_has_review_last`** (hard-koduje
  celý zoznam label-ov `#tabs .tlabel`). Pridaj nový label na správnu pozíciu. Server-side pridaj
  kľúč aj do `NAV_KEYS` (inak #173 premenovanie tabu → 400).
- **Email text = ŠÉFOVO PRESNÉ znenie (#183, v0.76.0)**: `build_unavailable_email` je šéfovo verbatim
  telo („veľmi sa ospravedlňujeme … momentálne nedostupný … nevieme kedy bude naskladnený …") + jeho
  podpis „S pozdravom … Drlík, Forestshop.sk" (NIE „Tím Forestshop.sk"). `build_alternative_email`
  ostáva na house-style „Tím" podpise. **`_shell(name_h, inner, sign=_SIGN_DEFAULT)` je ZDIEĽANÝ oboma**
  — keď meníš text/podpis JEDNÉHO e-mailu, parametrizuj (default arg) nech DRUHÝ ostane byte-identický;
  NEmeň `_shell` telo natvrdo. Personalizované oslovenie menom rieši shell (`Dobrý deň, <strong>Meno</strong>,`),
  takže šéfovo telo NEmá vlastné „Dobrý deň" (žiadne dvojité oslovenie/podpis).
- **GOTCHA — test čo asertuje kľúčovú frázu e-mailu ako plain substring**: NEobaľuj časť frázy do
  `<strong>` (napr. `momentálne <strong>nedostupný</strong>`) — rozbije to substring `momentálne
  nedostupný`. Šéfovo verbatim znenie píš bez inline tagov vnútri kľúčových fráz.
- **Zoradenie tabu (#185, v0.76.0)**: `build_view` vracia HORE produkty s OTVORENOU objednávkou
  zoradené podľa MAX(date) objednávok zostupne (najnovšia hore), potom bez objednávky (name/code).
  Dátum objednávky parsuj cez `_order_date_key` (export „YYYY-MM-DD HH:MM:SS", `[:10]`+`strptime`;
  prázdny/nevalidný → '' = najstarší, nespadne). Stabilný dvojkľúčový sort (name/code base → date desc).

## Rozdeliť produkt na veľkosti — per-veľkosť dodávateľský link (#174, v0.72.0)

Produkt s viacerými veľkosťami, kde dodávateľ má INÚ produktovú stránku PRE KAŽDÚ veľkosť
(napr. TRIGONA THERMOPAD S/M/L/XL/XXL, každá vlastný `p-XXXXX.xhtml`). Review karta má tlačidlo
**„✂ Rozdeliť na veľkosti"** (len pri `variant_codes.length > 1`) → rozbalí per-veľkosť riadky,
manažér nastaví VLASTNÝ link pre KAŽDÚ veľkosť.

- **Store `data/out/variant_links.json` `{variant_code: url}`** (vzor `order_pairings`, per-KÓD,
  NEVER-pruned, atomický). Kľúč = STABILNÝ variant kód, NIE array idx.
- **Nový decision status `"split"`** (`decisions.json`, `{status:"split", url:""}`) = marker že
  produkt je vyriešený per-veľkosť + BRÁNA pre zápis. `matchesFilter` 'good' zahŕňa split;
  `badge` má split; `navCount('review')` (unreviewed) ho nepočíta (má decision). **Zápis do eshopu
  cez `import_builder.link_rows` — dostal 4. param `variant_links`**: pre `split` píše per-variant
  `variant_links[code]` (preskočí variant BEZ linku — NIKDY prázdna internalNote bunka, tá by
  zmazala existujúci link), GRUBE→.de normalizácia zachovaná; good/manual = 1 link na všetky
  varianty (bez zmeny, default `variant_links={}` → back-compat 3-arg callers).
- **Doprava = ručný zip `/api/import` AJ nočná automatizácia „Veľkostné linky → eshop" (#192,
  v0.81.0).** `_do_upload_pairings` (párovania) je NEzmenený — split decision nemá decision URL,
  takže párovací push ho nikdy nechytí. Split-linky idú VLASTNOU default-disabled automatizáciou
  `split_links` (denne 03:45), presne ako GRUBE `grube_externalcode` #62, ale pre iné pole
  (`internalNote` per variant, NIE `externalCode`). Sub-vzor oproti supplier/externalcode (tie majú
  1:1 `*_rows`): split **REUSuje `link_rows`** (ten istý builder ako zip — GRUBE→.de + skip-empty),
  obmedzený na `split` decisions (`{k:d for k,d in dec if d.status=='split'}`) + na variant_links
  len NOVÝCH kódov (`{c:vlinks[c] for c in new_codes}`); good/manual sa tým vyfiltrujú. Inkrementálny
  tracking je per-VARIANT `data/out/uploaded_variant_links.json` `{code: url}` (vlastný store, mirror
  `uploaded_externalcodes.json`; NIE `vlink:<code>` namespace v zdieľanom `uploaded_pairings.json` —
  vlastný store nekoliduje, netreba prefix). `new_variant_link_keys(variant_links, split_codes,
  uploaded)` gate-uje na split_codes (kód, ktorého produkt už NIE je split, sa nepushne) + zahodí
  non-http URL (fail-safe, nikdy do živého internalNote). **Idempotencia trackuje ZDROJOVÚ .sk URL**
  (`uploaded[c]=vlinks[c]`), nie .de ktorú `link_rows` zapíše — porovnáva sa proti variant_links, tam
  je .sk. csv_safe=True (nočný sink nesmie byť slabší než zip, aj keď http-URL sa nikdy nepreprefixne).
- **`CODE2VARIANT` (veľkostné labely) sa stavia v `_load_catalog`** — ten teraz vracia **3-tuple**
  `(code2pair, code2variant, catalog)`. DVAJA volajúci (štart + `run_shoptet_sync` global) MUSIA
  unpacknúť 3. Label = populated `variant:*` stĺpce (colon prefix, NIE `variantVisibility`) joinnuté;
  LEN pre DISPLAY (autoritatívny kľúč = kód).
- **Endpointy**: `POST /api/variant-link {code,url}` (set/clear, formula-lead + `^https?://` guard,
  mirror `/api/order-pair`), `GET /api/variants?key=` (per-produkt `[{code,size,link}]`); `variant_links`
  mapa doplnená do `/api/products` (klient ju načíta ako `DECISIONS`).
- **Frontend**: `splitPanel(p)` async-fetchne `/api/variants` → `splitRow` per variant (size label +
  kód + whole-product kandidáti ako per-veľkosť „Vybrať" + manuálny URL input, save per kód cez
  `saveVariantLink`). `splitOpen` Set (transient). Split UI preberá pravú stranu karty keď
  `splitOpen.has(key) || status==='split'`. Cache-bust `?v=` bump.
- **E2E**: `test_review_ui::test_split_into_sizes_...` používa **`matched_server`** (function-scoped,
  izolovaný — split decision NEpolutuje session-scoped `live_server`, rovnaká pointa ako matched-buttons).
  Po reloade je split karta „vyriešená" → default `unreviewed` filter ju NEukáže; test klikne filter
  „✓ Dobré/Vybrané" (zahŕňa split) a čaká na `.badge.split`.
- **#180 — varovanie pri commite splitu keď VEĽKOSŤ ostane bez linku**: split-commit skip-empty
  NEzmaže starú celo-produktovú URL pri veľkosti bez linku (zámerne), takže tá si ticho ponechá
  STARÝ link. `splitPanel` `done.onclick` teraz volá pure helper `variantsWithoutLink(loadedVariants)`
  → ak nejaké → `confirm()` ich vymenuje (singular/plural), zrušenie = ostane v edit móde (`return`
  pred `saveDecision`). Helper zrkadlí PRESNE `splitRow` display rule: bez linku = prázdny AJ
  `VARIANT_LINKS[code]` AJ `v.link`; label `size || code`. `splitRow` commit MUTUJE `v.link` (aj na
  clear) nech `loadedVariants` nesie aktuálny uložený stav (nie len load-time). Všetky veľkosti s
  linkom → žiadne `confirm()`, priamy commit (pre-#180 správanie).

## E2E gotcha — natívny `confirm()` / `prompt()` dialóg + unit-test pure JS helpera v prehliadači

- **`confirm()` (a `prompt()` #173 rename) sa v Playwrighte chytá cez `page.on("dialog", handler)`** —
  handler MUSÍ zavolať `d.accept()` / `d.dismiss()` (registrovaný listener vypne auto-dismiss). Klik
  na tlačidlo, čo spustí SYNCHRÓNNY `confirm()`, sa resolvne AŽ PO odpovedi handlera, takže hneď po
  `.click()` smieš assertnúť na zozbierané `dialogs`. Vzor: `dialogs=[]; page.on("dialog", lambda d:
  (dialogs.append(d.message), d.accept()))`. Assertni `len(dialogs)` + obsah správy (názvy veľkostí).
  Test „zrušené" = `d.dismiss()` → over že sa akcia NEvykonala (editor otvorený, žiadny `.badge.split`).
  Test „bez dialógu" = `dialogs == []`. (Pri `prompt()` rename je zaužívaný `page.once("dialog", d =>
  d.accept("text"))` — jednorazový; `confirm()` warning s viac klikmi radšej `page.on`.)
- **Pure JS helper sa dá unit-testnúť v prehliadačovom realme cez `page.evaluate` — žiadny JS toolchain
  netreba.** `app.js` je plain `<script>` (nie module), takže top-level `function foo(){}` sú GLOBÁLNE
  (na `window`) a `let` globály (`VARIANT_LINKS`, …) sú dosiahnuteľné/priraditeľné holým menom v
  `page.evaluate`. Vzor: `page.evaluate("() => { VARIANT_LINKS={...}; return variantsWithoutLink([...]); }")`
  s viacerými vstupmi (mixed / from-link / fallback / empty / null) — testuje čistú logiku bez DOM/siete.
