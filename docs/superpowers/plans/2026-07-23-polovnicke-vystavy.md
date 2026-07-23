# Poľovnícke výstavy (#111) — Implementačný plán

> **Pre staviteľa:** implementuj task-po-tasku s TDD (RED→GREEN, testy bez živej siete). Detaily každého tasku sú v spec `docs/superpowers/specs/2026-07-23-polovnicke-vystavy-design.md` — TÚ čítaj ako zdroj pravdy (dátový model, verbatim maily, endpointy, stavový stroj). Verbatim n8n logika: `data/out/vystavy_workflow_digest.md` (gitignored). Tento plán je poradie + hranice taskov.

**Cieľ:** migrovať n8n workflow „Poľovnícke výstavy" celý do appky (záložka + CRUD + 3 automatizácie default-off + in-app schvaľovanie + info feed).

## Global Constraints (platí pre KAŽDÝ task)

- Verzia už bumpnutá na `0.84.0` (prvý commit). Netreba znova.
- Testy bez živej siete: SMTP aj IMAP monkeypatchnuté (vzor `tests/test_webreview_nedostupne.py`). `PYTHONPATH=src`, `.venv/bin/pytest`.
- Kódovanie/store: atomický JSON (`.tmp`+`os.replace`, `with _lock:`), gitignored `data/out/vystavy.json`.
- Maily VERBATIM zo spec (from `info@forestshop.sk`, BCC `drlik.marek@gmail.com` default). Žiadne tajomstvá do gitu.
- Automatizácie posielajúce/čítajúce mail = **default VYPNUTÉ** (#93). 
- Frontend: karty (nie tabuľka). `NAV_KEYS` guard test (`test_nav_keys_match_appjs`) musí prejsť.
- Po každom tasku spusti CELÝ balík testov (regresia), commit.

## Task 1 — Store + migrácia dát

**Files:** `scripts/migrate_vystavy.py` (create), `webreview/app.py` (`_load_vystavy`/`_save_vystavy`, kópia `_load_notes`), `tests/test_vystavy_migrate.py`.
**Produkuje:** `data/out/vystavy.json` schéma (viď spec „Dátový model"), `VYSTAVY` store path.
- TDD: test mapovania stĺpcov CSV→objekt + normalizácie stavov (odslany/ososlaný/odoslany→`otazka`, prázdne→`""`, atď.), Message-ID extrakcia z `email_otazka`/`email_ziadost` (ignoruj ne-msgid text). Idempotencia (`--force`).
- Spusti migráciu na `data/out/vystavy_import.csv` → `vystavy.json` (15 výstav). Over počet.

## Task 2 — Email helper s Message-ID

**Files:** `webreview/app.py` (`_send_vystava_mail(to, subject, text_body) -> str|None`), `tests/test_vystavy_mail.py`.
**Produkuje:** helper čo pošle `text/plain` s explicitným `Message-ID` (`email.utils.make_msgid(domain="forestshop.sk")`), reuse SMTP config z `_send_mail_html`, BCC default. Vráti msgid pri úspechu / `None` pri zlyhaní.
- TDD: mock SMTP → over Message-ID header nastavený + vrátený; zlyhanie → `None`. Texty mailov (otázka/prihláška) ako konštanty/templaty (spec verbatim) — test že `{nazov}`/`{datum}`/`{velkost_stanku}` sa doplnia.

## Task 3 — IMAP helper (pure + I/O)

**Files:** `src/parovanie/vystavy_imap.py` (create), `tests/test_vystavy_imap.py`.
**Produkuje:** `parse_inbox`, `trim_quote`, `match_reply` (pure), `fetch_inbox` (I/O). Signatúry v spec „IMAP helper".
- TDD (pure, fixtúry): `trim_quote` odreže reply-chain (SK/CZ/EN vzory); `match_reply` matchne from+inReplyTo, ignoruje nesúvisiace, viac výstav 1 organizátora → správna podľa msgid; `parse_inbox` z raw mailov. `fetch_inbox` netestuj naživo (I/O), len že chyba spojenia → `[]`.

## Task 4 — CRUD endpointy

**Files:** `webreview/app.py` (`GET/POST /api/vystavy`, `POST /api/vystava` edit+delete+status-reset), `tests/test_vystavy_crud.py`.
- TDD (Flask client): list; add (uuid, status `""`, validácia povinné `nazov`, formula-lead reject 400); edit whitelist polí; delete; manuálny status reset (reset na `""` vymaže msgid); reject formula-lead na editovateľných poliach.

## Task 5 — Send endpointy (otázka + „Ideme")

**Files:** `webreview/app.py` (`POST /api/vystava/posli-otazku`, `POST /api/vystava/ideme`), `tests/test_vystavy_send.py`.
- TDD (mock `_send_vystava_mail`): `posli-otazku` → status `otazka`, msgid uložený, feed; `ideme` over stav `akcia bude` → pošle prihlášku → status `poziadane`, `email_ziadost_msgid`, feed; zlyhanie mailu → 502, stav NEZMENENÝ; `ideme` na zlom stave → 409/400.

## Task 6 — 3 automatizácie (default off)

**Files:** `webreview/app.py` (run_fns `run_vystavy_otazka`/`run_vystavy_odpoved_otazka`/`run_vystavy_odpoved_prihlaska` + `AUTOMATIONS_REG` položky + `AUTOMATION_DESCRIPTIONS`), `tests/test_vystavy_automations.py`.
- Schedule: `vystavy_otazka` daily 06:00, `vystavy_odpoved_otazka` daily 09:00, `vystavy_odpoved_prihlaska` daily 09:30. Všetky default OFF.
- TDD (mock inbox + mock send): `vystavy_otazka` pošle len `status==""` & `sposob==email` & `kedy_riesit==aktuálny mesiac (sk monthLong lowercase)` & email neprázdny; ostatné preskočí. `odpoved_*` posunú stav pri IMAP matchi (mock `fetch_inbox`). Over default disabled (`status()` → enabled False).

## Task 7 — Frontend záložka (karty)

**Files:** `webreview/static/app.js` (TABS+`vystavy`, NAV_ICONS, PAGE_TITLES, switchTab, render dispatch, `loadVystavy`/`renderVystavy`/detail-edit-add/feed/akčné tlačidlá), `webreview/app.py` (`NAV_KEYS += "vystavy"`), `webreview/templates/index.html` (`<section id="tab-vystavy">` + cache-bust `app.js?v=+1`, `style.css` ak treba), `webreview/static/style.css` (karty + badge farby).
- Karty zoskupené/zoradené podľa stavu, farebné badge, per-stav tlačidlo (Nová→„Pošli otázku", akcia bude→„Ideme na túto výstavu"), klik→detail/edit + feed, „+ Pridať". Zrozumiteľné, nie tabuľka.
- Test `test_nav_keys_match_appjs` musí prejsť.

## Task 8 — E2E

**Files:** `tests/e2e/test_vystavy_e2e.py`.
- Playwright: otvor tab, karty sa vykreslia, add→edit→delete testovacej výstavy (uprac po sebe), čistá konzola. Vzor webreview e2e.

## Po implementácii

Celý balík testov zelený + lint (`ruff check .` / `.venv/bin/pytest --ignore=tests/e2e` + e2e job) → PR dev→main → CI zelené + mergeable → **ZASTAV, NEMERGUJ**, vráť PR# + BASE/HEAD SHA (review + merge robí supervisor).
