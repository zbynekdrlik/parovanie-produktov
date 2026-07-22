## 2026-07-22 — #49 partial-batch upload-pairings mark-as-uploaded fix (worker)
- Bug (adversariálny review PR #48): `_do_upload_pairings` po úspešnom importe označil za „uploaded" VŠETKY `new_keys`, aj tie čo v `link_rows` vygenerovali 0 riadkov (produkt bez variant kódov, alebo kód zdedupovaný ako „seen"-loser inej položky tej istej dávky) — bezkódová položka sa navždy stratila, žiaden budúci beh ju neskúsil znova.
- Validated (verify-issue-still-valid): PR #133 (#109) len extrahoval `_do_upload_pairings` z pôvodného n8n endpointu, logiku okolo `_save_uploaded` nezmenil — bug stále reprodukoval na aktuálnom kóde.
- RED `4a78718` (`test_pairings_partial_batch_only_marks_keys_with_rows_uploaded` — 2 kľúče, jeden s kódom jeden bez; potvrdené zlyhanie pred fixom `assert count==1` → `2` cez git-stash overenie) → GREEN `7555f39`: `uploaded_keys` odvodené z `written_codes = {r[0] for r in rows}` ∩ `variant_codes`, len tie sa zapíšu do `uploaded_pairings.json`; zvyšok ostane „nový" (retry) a ráta sa do nového `blocked` počtu (mirror existujúceho celo-dávkového blocked prípadu). `_do_upload_suppliers` bug nemá (1:1 code→row, žiadna indirekcia) — overené čítaním `supplier_rows`.
- Verzia 0.53.0→0.54.0. 71/71 relevantných testov (`test_webreview.py`+`test_webreview_parovania_eshop.py`), 447/447 celkovo, ruff clean. PR #137 (Closes #49), merge `6555386`, main CI zelené, nasadené v0.54.0 (systemctl restart). Data-safety PRED==PO (decisions 2831, ordered 146, order_pairings 57, waiting 11, supplier_assignments 1, uploaded_pairings **1782**, users 3). Live dry-run overenie na skutočných dátach: 415 nových párovaní, blocked=0, dry_run nezmenil `uploaded_pairings.json` (md5 zhoda). DOM `[data-testid=version]`=v0.54.0.
- Follow-up docs-only PR #138 (playbook gotcha do `.claude/skills/webreview/SKILL.md`, verzia 0.54.1) — merge `51e1d8f`, nasadené a overené (DOM v0.54.1, store counts nezmenené).

## 2026-07-22 — #109 in-app „Párovania → eshop" automation (migrácia n8n YuDugCCOnwejRfva) (worker)
- Migrácia denného n8n workflowu (21:00 → upload-pairings → upload-suppliers → Discord súhrn) do appky ako automatizácia na generickom runneri (#93). Verzia 0.50.0→0.51.0. RED tests `20e0ef4` → GREEN implementácia (tento commit).
- **Architektúra (NEkopíruj logiku):** jadro oboch n8n endpointov vyextrahované do zdieľaných funkcií `_do_upload_pairings(dry)` / `_do_upload_suppliers(dry)` → `(result, status)`; HTTP endpointy robia len auth + delegujú; nová `run_parovania_eshop()` volá OBE jadrá priamo (žiadny self-HTTP, žiadny bearer — in-app migrácia zároveň eliminuje exposed-bearer dlh nočného pushu). Existujúcich 40+ upload/endpoint testov ostalo zelených (identický výstup).
- **Bezpečnosť:** automatizácia PÍŠE do živého eshopu → registrovaná `enabled=false` (štartuje Zastavené, beží len po ▶ Štart; deploy nikdy auto-nepushne). Idempotentná cez `uploaded_pairings.json`/`uploaded_suppliers.json` (re-run nič dvakrát nenahrá). Číta LEN manažérove decision/assign stores (čo pushnúť), nikdy ich nemení.
- **Stavy pre tab:** `last_result.status` = ok/blocked/failed (mirror n8n Sprava node) — blocked=napárované bez kódov, failed=import rc!=0/lock/timeout; genuine exception → runner `last_status='error'`, app žije. Tab `parovania_eshop` (status-only, vzor renderShoptetSync): Beží/Zastavené pill, ▶ Štart/⏹ Stop (persist), ⚡ Spustiť teraz, per-beh farebný `.autoresult` box (párovania+dodávatelia počty). Cache-bust v38→v39.
- Testy: 11 backend (`test_webreview_parovania_eshop.py`) + 3 e2e (`tests/e2e/test_parovania_eshop.py`) — registrácia/disabled, push oboch + počty, idempotencia, blocked/failed/error degrade, disabled netiká, reads-not-writes manager stores. 446 backend + 38 e2e zelené, ruff clean.
- Po nasadení: n8n workflow `YuDugCCOnwejRfva` treba UNPUBLIShnúť (konvencia #103) — spraví supervisor.

## 2026-07-22 — #118 sidebar „Eshop" folder + refinements (worker)
- Rozbaľovací priečinok „Eshop" (predošlý worker, PR #131) + Marekove upresnenia (comment 2026-07-22): (1) admin „Užívatelia" presunutý MIMO priečinka na samostatný `#usersNav` úplne dole (nad tmavým módom/verziou), (2) celá sekcia „Čoskoro — rozšírime" + 4 „soon" placeholdery + `.soon`/`.soonpill`/`.soon-nav` CSS ZMAZANÉ, (3) priečinok „Eshop" ponechaný (collapsible, persist `folder:eshop`) s pracovnými tabmi + „Automatizácie".
- `visibleTabs()` → `isAdmin()`; `renderTabs()` teraz plní 3 kontajnery (`#tabs`/`#autoTabs`/`#usersNav`), `.users-nav:empty{display:none}` skryje prázdny (non-admin). Selektorový kontrakt (`#tabs` v `.sidebar`, accessible names, `.navcount`) zachovaný — auth testy bez zmeny. Cache-bust v36→v37.
- Testy: `test_nav_order_has_review_last` upravený (#tabs bez „Užívatelia"); nový `test_users_standalone_at_bottom_and_no_soon_section`. Refinements commit `4c1e364`.
- **De-flake** `test_sidebar_hosts_nav_and_pagetitle_updates` (`6b9810a`): `wait_for_selector('[data-testid=version]')` NEgatuje init (span je v DOM od štartu); `#pageTitle` má statický „Kontrola párovania" markup čo `init()` async prepíše → race (green push / red pull_request TOHO istého commitu). Fix: wait na `.sidebar #tabs button` + title. Stale-comment fix `888d9fd`. Playbook (webreview SKILL.md) doplnený o oba gotchy.
- 32 e2e + 418 backend zelené; ruff clean. PR #131 (Closes #118), merge `9ccfaa5`, nasadené v0.48.0. Post-deploy živý forestshop.newlevel.media (admin): priečinok „Eshop" zoskupuje taby + collapse/expand + persist, „Užívatelia" samostatne dole (klik → tab funguje, 19 user-rows), žiadna „Čoskoro", 0 console errorov, DOM `[data-testid=version]`=v0.48.0. Data-safety PRED==PO (decisions 2831, ordered 146, order_pairings 57, waiting 11, supplier_assignments 1, users 3).

## 2026-07-22 — #91 auth (worker)
- #91 prihlasovací systém: RED 52ed16a → GREEN 62677d2 (22-test auth suita: gate/session/CSRF/rate-limit/reset-tokeny/admin CRUD/bootstrap); review fixes 79c3592 (dummy-hash timing) + e05c478 (CSRF bytes, SMTP degrade, rate-limit prune, +2 testy). PR #104, merge 9db7adb, nasadené v0.44.0 (DOM overené na živom webe, PRED==PO stores, n8n bearer intact).
- Bonus bug (pre-existing v0.42): [hidden] vs display:flex — search box presakoval do všetkých tabov; RED 76271be → GREEN bb3354f.
- Rozhodnutia: /api/n8n/* mimo session gate (vlastný bearer); reset-token store hashovaný sha256; bootstrap create-if-missing; legacy testy = reálna session (authed_client + store seed), e2e autouse cookie + @pytest.mark.anonymous.
- Otvorené: #113 SMTP údaje pre reset-mail (needs-answer — čaká na Mareka).

## 2026-07-22 — #94 rebrand „Forestaci" + nová URL (worker)
- Prebrandované z „Forestshop" na „Forestaci": sidebar `.brandtxt` + `<title>` v index.html AJ auth shell (auth.html; login/forgot/reset ho dedia). Eshop odkazy forestshop.sk NEDOTKNUTÉ; email subject „Párovanie Forestshop" ponechaný (ambiguous eshop-ref → default keep). Verzia 0.44.1→0.45.0. Commit 621e8fa, PR #116, merge e896040, nasadené v0.45.0.
- Brand-lock test tests/test_branding.py (index authed + login shell anonymous; pozit. + negat. assert). E2E fixture: pinnutý AUTH_COOKIE_SECURE=0 do _AUTH_ENV — lokálne e2e padali CSRF/400 na dev boxe s reálnym data/.auth_env (AUTH_COOKIE_SECURE=1 → Secure cookie sa cez http://127.0.0.1 nevráti); CI to nemal (žiadne data/). Deterministické lokálne e2e.
- Cloudflare (aditívne, API token data/.cf_token): ingress tunela a3df5493… + DNS CNAME forestaci.newlevel.media → tunel (proxied). parovanie-forestshop.newlevel.media OSTÁVA (obe → localhost:8801). Post-deploy: obe URL anon `/`→302 /login, /login 200, /api/version v0.45.0; DOM brand „Forestaci", [data-testid=version]=v0.45.0; post-login index sidebar brandtxt=Forestaci. Data-safety PRED==PO (decisions 2831, ordered 146, order_pairings 51, waiting 11, suppliers 1, instock 10, users 2).

## 2026-07-22 — #120 rebrand SPÄŤ na „Forestshop" + URL forestshop.newlevel.media (worker)
- Marek si to rozmyslel: brand názov appky späť z „Forestaci" na „Forestshop" (parciálny reverz #94). Tie isté 4 reťazce: index.html `.brandtxt` + `<title>`, auth.html brand `<b>` + `<title>`. Eshop odkazy forestshop.sk / Shoptet NEDOTKNUTÉ. Brand-lock test tests/test_branding.py otočený na „Forestshop" (pozit.+negat.): RED 3c47803 → GREEN f270935. Verzia 0.45.1→0.45.2. PR #122, merge 3b62047, nasadené v0.45.2.
- Cloudflare (aditívne): na ten istý tunel a3df5493 pridaný TRETÍ hostname forestshop.newlevel.media (ingress vložený PRED catch-all) + DNS CNAME proxied → localhost:8801. Reštart tunela NETREBA. Všetky tri URL žijú: forestshop (nová primárna), parovanie-forestshop (denná manažérova — MUSÍ ostať), forestaci (ponechaná, neškodí).
- Post-deploy (živý web, cez tunel): všetky 3 hosty /login 200, `/`→302 /login?next=/, /api/version v0.45.2. Real-browser DOM na forestshop.newlevel.media: login shell brand „Forestshop", title „Prihlásenie — Forestshop", [data-testid=version]=v0.45.2, žiadne „Forestaci". Post-login index (cez tunel, session): sidebar brandtxt=Forestshop, title „Kontrola párovania — Forestshop". Data-safety PRED==PO (decisions 2831, ordered 146, order_pairings 51, waiting 11, supplier_assignments 1, instock 10, users 3, unavailable 13).

## 2026-07-22 — #93 in-app automation runner + „Nevyzdvihnuté zásielky — Pošta SK" (worker)
- Generický runner (`automation_runner.py`: registry + thread-scheduler, stav `data/out/automations.json` 0600, default DISABLED, štart len v `__main__`) + prvá automatizácia = verný port n8n `2mhrdy0ouHe4VPeH` (`posta_uncollected.py` — čítané node-by-node cez MCP: notified+ZNP*, kadencia 0/+3/+3/+7 max 4, VŠETKY 4 maily ZÁKAZNÍKOVI, stav `count|YYYY-MM-DD`). Zdroj = vlastný orders export (packageNumber/email/phone/billFullName UŽ v ňom sú — Sheet netreba). invalid_format (13-14-miestne numerické štítky, ~57/281 reálnych zásielok — TO mesiac potichu rozbíjalo n8n; live-reprodukované na API) sa loguje + zobrazuje v tabe. Feature commit f487a4d + hardening 2752008 (eskalácia sa persistuje HNEĎ po sende — crash nesmie zajtra duplikovať mail; prázdny e-mail neskúša SMTP), PR #123 merge b303a8f, v0.46.0.
- Follow-up PR #124 merge 7ef02f3 (v0.46.1): admin link — live probe ukázal, že prehľad objednávok GET filter NEMÁ (`?query=`/`?code=` ignorované, POST-only); jediný deep-link = `/admin/vyhladavanie/?string=<kód>&src=orders` (vracia presne tú objednávku).
- Testy: 50 nových unit/integration (runner safety/persistencia/plánovanie; posta detekcia/kadencia/šablóny+escape; Flask auth-gate/toggle/full-run/SMTP-fail-retry/crash-mid-run/prune) + 3 e2e (`automations_server` fixture — hermetický „Spustiť teraz" s 0 zásielkami). Fixtures overené proti ŽIVÉMU api.posta.sk.
- Deploy: 2× reštart parovanie-web, DOM v0.46.0→v0.46.1, PRED==PO všetkých 9 stores (decisions 2831, ordered 146, order_pairings 51, waiting 11, supplier_assignments 1, instock 10, unavailable 13, users 3, grube 15). Živý toggle Štart→Beží (Ďalší beh 23.7. 09:00)→reload→Stop; finálny stav VYPNUTÉ (zapne Marek). n8n workflow ostáva aktívny — vypnúť po prevzatí (poznámka na #93).

## 2026-07-22 — #126 Nevyzdvihnuté zásielky filtruje LEN Pošta SK (worker)
- Rozhodnuté na #126 (Marek): DPD NEpridávať, automatizácia zostáva len Pošta SK — filtrovať zdroj zásielok podľa dopravcu z SHIPPING pseudo-položky exportu.
- Nález: reálny export NIKDY nepíše „Slovenská pošta" — Pošta SK domové doručenie sa volá „Kuriér" (223/228 objednávok, `EF…SK` formát čísla), DPD = „DPD doručenie na adresu"/„DPD kuriér" (14-miestne čísla). Allowlist „pošt"/„Balík" by preto vyradil 223/228 reálnych Pošta zásielok → implementácia je BLOCKLIST (dpd/gls/packeta/zásielkovňa/in time/wedo/spservis), objednávka bez SHIPPING riadku fail-open (zahrnutá). Zdokumentované v komentári na #126 + docstring `posta_uncollected.py` + `.claude/skills/webreview`.
- RED `0f15865`→GREEN (súčasť `56cb7b9`): `tests/test_posta_uncollected.py::test_shipments_dpd_carrier_excluded_posta_carrier_included`.
- Bonus (v scope, malý): `_send_mail_html` teraz defaultne BCC-uje `MAIL_BCC` (konvencia „BCC vždy" #105) keď volajúci bcc neuvedie; odstránený natvrdo zapísaný `POSTA_BCC` literál. RED `56cb7b9`→GREEN `8358fc0`: `test_send_mail_html_defaults_bcc_to_mail_bcc_env` (+2 ďalšie). Mimo scope: `_send_mail` (reset hesla) BCC nemá — filed #127.
- Verzia 0.46.2→0.46.3, PR #128 merge `edee661`, nasadené v0.46.3.
- Post-deploy: PRED==PO všetkých 11 stores. Živé overenie „Spustiť teraz" (bezpečné — `escalation=={}`+`uncollected==0` pred behom, `emails_sent==0` po behu): `checked` 21→13 (8 DPD vylúčených), `invalid` (nesledovateľné) 8→0, box „⚠️ nesledovateľných" úplne zmizol z UI, žiadne console errory.

## 2026-07-22 — #127 `_send_mail` (reset hesla) doplnené o BCC (worker)
- Doplnok k #126: `_send_mail_html` (automatizácie) BCC-uje `MAIL_BCC` už z #126; `_send_mail`
  (reset hesla, `/forgot` flow) BCC nemal — malá medzera (~7 riadkov), rovnaký vzor skopírovaný.
  Signatúra ostala 3-argumentová (bez `bcc=` parametra — jediný volajúci ho ani nechce vypínať).
- RED `ff86134` → GREEN `0967e6d`: `tests/test_webreview_auth.py::test_send_mail_bcc_to_mail_bcc_env_when_set`
  + `test_send_mail_no_bcc_when_mail_bcc_env_unset`. Playbook (webreview SKILL.md „BCC vždy" bullet)
  aktualizovaný, že teraz platí pre OBE mail cesty — `e63ba0e`.
- Verzia 0.46.4→0.46.5, PR #129 merge `3c09468`, nasadené v0.46.5.
- Post-deploy: DOM aj `/api/version` = v0.46.5, PRED==PO všetkých 7 stores (decisions 2831,
  ordered 146, order_pairings 57, waiting 11, supplier_assignments 1, users 3, reset_tokens 1).
  Live-overenie zámerne BEZ reálneho odoslania reset-mailu (aby sa nepošlo majiteľovi zbytočné
  BCC) — funkčnosť pokrytá unit testom; `/forgot` stránka načítaná (200, 0 console errorov).

## 2026-07-22 — #106 „Dodávateľský sklad" — in-app supplier scraper (worker)
- Migrácia n8n „Forestshop — Dodávateľský scraper" (`6kn7jzBXTjbmbiVa`) na in-app runner (#93):
  nová automatizácia `dodavatelsky_sklad`, denne 05:00, **default Zastavené** (veľa externých HTTP
  volaní + platený OpenAI → beží až po Štart).
- Pure logika `src/parovanie/supplier_stock.py` (bez siete/OpenAI): `links_from_export` (internalNote
  http + visible, dedup), `extract_static` (JSON-LD → og/product meta → text-keyword LEN pre 4 overené
  domény), `need_llm` (available ALEBO price None), `build_llm_messages`/`parse_llm_json`,
  `is_recently_checked` (stale-skip 20h). 96 % pokrytie.
- Flask: `run_supplier_stock` + `_fetch_supplier_html` + `_llm_extract` (OpenAI cez `requests.post`,
  kľúč z `data/.ai_env` cez `_load_env_file`, `response_format=json_object`), `/api/supplier-stock`,
  store `data/out/supplier_stock.json` (vstup pre #107/#108). Frontend: per-item tab (vzor `renderPosta`)
  + filtre (všetky/chyby/AI/dodávateľ); poll zovšeobecnený (`_reloadAuto`).
- Testy hermetické (mock supplier HTTP AJ OpenAI): `test_supplier_stock.py` (53) + `test_webreview_
  supplier_stock.py` (10) + e2e `test_supplier_stock.py` (3). RED-before-GREEN sa netýka (feature).
- Verzia 0.57.0, PR #142 merge `014e915`, nasadené v0.57.0.
- Post-deploy: DOM = v0.57.0, PRED==PO manager stores (decisions 2831, ordered 146, order_pairings 57,
  waiting 11, supplier_assignments 1). Živé overenie: tab „Dodávateľský sklad" existuje, default
  Zastavené, Štart/Stop + Spustiť teraz prítomné, plán denne o 05:00, 0 console errorov. Reálny scrape
  sa ZÁMERNE nespúšťal (hit by mnoho dodávateľských webov + minul OpenAI). Odložené: odpublikovať n8n
  `6kn7jzBXTjbmbiVa` po zapnutí in-app (ako #109).
