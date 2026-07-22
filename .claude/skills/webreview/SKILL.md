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
   výpadku sa preskočí dopredu. „⚡ Spustiť teraz" beží aj vypnutá (explicitná akcia) a
   POSIELA REÁLNE e-maily zákazníkom pri KAŽDOM aktuálne nevyzdvihnutom balíku — pri overovaní
   na živom webe štandardne NEklikaj Spustiť teraz, toggle Štart→Stop stačí.
   **`parovania_eshop` (#109) PÍŠE do ŽIVÉHO eshopu** (nočný push párovaní/dodávateľov):
   pri post-deploy overení NEklikaj ani Spustiť teraz ANI Štart — enable by naplánoval
   reálny push ~1000 nenahraných párovaní o 21:00; over LEN že tab existuje, default
   Zastavené, tlačidlá prítomné (persistenciu pokrýva e2e). Manažér ho spustí keď sám
   chce začať nahrávať. **Výnimka (#126,
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
