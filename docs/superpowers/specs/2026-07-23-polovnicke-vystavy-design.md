# Poľovnícke výstavy — migrácia n8n workflowu do appky (#111)

**Dátum:** 2026-07-23
**Issue:** #111 — Migrácia n8n: Poľovnícke výstavy → záložka v appke

## Cieľ

Presunúť celý n8n workflow „Polovnicke vystavy" (registrácia stánku ForestShop.sk na poľovnícke dni / výstavy) **do našej appky** — nová záložka so zoznamom výstav, editovaním/pridávaním, stavovým strojom, in-app schvaľovaním (namiesto Discord ✅ fajky) a in-app info feedom (namiesto Discordu). Manažér (Štepán) uvidí a odkliká všetko v appke; Discord ani n8n už netreba.

Zdroj pravdy o pôvodnej logike: `data/out/vystavy_workflow_digest.md` (gitignored digest n8n workflowu — verbatim texty mailov + logika 4 reťazí). Migračné dáta: `data/out/vystavy_import.csv` (15 výstav z Google Sheetu).

## Architektúra (3 vrstvy, presne ako existujúce features)

1. **Úložisko** `data/out/vystavy.json` — list objektov výstav (gitignored, prežije deploy, atomický zápis). CRUD endpointy. Vzor = „Poznámky" (`notes.json`).
2. **Automatizácie na runneri** — 3 nové `Automation` položky v `AUTOMATIONS_REG`, **default VYPNUTÉ** (#93 contract). Bez samostatných nav tabov (bežia na pozadí, ich efekt vidno v tabe Výstavy + stav v správe Automatizácií).
3. **Frontend** — nová záložka `vystavy` v `TABS` (karty, nie tabuľka), detail/edit/add, farebné stavy, per-stav akčné tlačidlá.

## Dátový model — jedna výstava (objekt v `vystavy.json`)

```
{
  "id": "<uuid4 hex>",                 // stabilný kľúč (generovaný pri migrácii/add)
  "nazov": "Poľovnícky deň Malacky",   // Názov poľovníckych dní (povinné)
  "datum": "15.8.2026",                // Dátum (voľný text — rôzne formáty)
  "miesto": "Malacky",                 // Miesto
  "kontakt_osoba": "...",              // Kontaktná osoba
  "tel": "...",                        // Tel. číslo
  "email": "organizator@...",          // email organizátora (match pri IMAP)
  "velkost_stanku": "8x3",             // velkost_stanku
  "kedy_riesit": "jún",                // mesiac (SK monthLong) — filter reťaze A
  "sposob": "email",                   // ziadost: email | pdf (pdf = manuálne, automat mail neposiela)
  "status": "akcia bude",              // stavový stroj (kanonické n8n hodnoty, viď nižšie)
  "email_datum": "2026-07-23T...",     // timestamp poslednej email-akcie (ISO)
  "email_otazka_msgid": "<...@forestshop.sk>",  // Message-ID odoslanej otázky (IMAP inReplyTo)
  "email_ziadost_msgid": "<...@forestshop.sk>", // Message-ID odoslanej prihlášky
  "feed": [                            // in-app info feed (namiesto Discordu), newest-first
     {"ts": "...", "typ": "otazka_poslana|odpoved_otazka|prihlaska_poslana|odpoved_prihlaska|manual",
      "text": "...orezaný citát odpovede / info..."}
  ]
}
```

Editovateľné polia v appke: `nazov, datum, miesto, kontakt_osoba, tel, email, velkost_stanku, kedy_riesit, sposob`. Stavové polia (`status`, `*_msgid`, `email_datum`, `feed`) mení len logika/automatizácie, nie priamy edit (okrem manuálneho resetu stavu — viď nižšie).

### Stavový stroj (kanonické hodnoty n8n — nechať 1:1)

| interná hodnota `status` | display label (SK) | farba | akcia dostupná |
|---|---|---|---|
| `""` (nova) | Nová | šedá | „Pošli otázku" (manuál) |
| `otazka` | Otázka poslaná | modrá | čaká na odpoveď (IMAP) |
| `akcia bude` | Odpovedali — čaká na rozhodnutie | oranžová | **„Ideme na túto výstavu"** (pošle prihlášku) |
| `poziadane` | Prihláška poslaná | fialová | čaká na potvrdenie (IMAP) |
| `odpovedane od organizatora` | Potvrdené | zelená | konečný stav |

Prechody:
```
""            --A: mesiac==kedy_riesit & sposob=email--(pošli otázku)-->  otazka
otazka        --B: IMAP odpoveď inReplyTo==email_otazka_msgid----------->  akcia bude
akcia bude    --C: manažér klikne „Ideme" (pošli prihlášku)------------->  poziadane
poziadane     --D: IMAP odpoveď inReplyTo==email_ziadost_msgid--------->  odpovedane od organizatora
```
Manažér môže stav aj ručne resetovať (napr. na nový ročník: späť na „Nová") cez edit — dropdown stavu v detaile. Reset na „" vymaže `*_msgid` (nový cyklus).

## Migrácia dát (jednorazový skript + import pri prvom štarte)

`scripts/migrate_vystavy.py` — načíta `data/out/vystavy_import.csv` (UTF-8), pre každý riadok vytvorí objekt výstavy s `uuid4`, **znormalizuje stav**:

| CSV `email_status` | → interná `status` |
|---|---|
| prázdne | `""` |
| `odslany` / `odoslany` / `ososlaný` (preklepy „odoslaný") | `otazka` |
| `akcia bude` | `akcia bude` |
| `poziadane` | `poziadane` |
| `odpovedane od organizatora` | `odpovedane od organizatora` |

Mapovanie stĺpcov: `Názov poľovníckych dní→nazov`, `Dátum→datum`, `Miesto→miesto`, `Kontaktná osoba→kontakt_osoba`, `Tel. číslo→tel`, `email→email`, `velkost_stanku→velkost_stanku`, `kedy riesit→kedy_riesit` (lowercase, trim), `ziadost→sposob` (`pdf`→`pdf`, inak `email`). `email_otazka`/`email_ziadost` z CSV obsahujú Message-ID (`<...>`) → `email_otazka_msgid`/`email_ziadost_msgid` (ak vyzerá ako msgid, inak ignoruj — napr. „poslať dokumenty na mesto" je poznámka, nie msgid). `feed=[]`. Idempotentné: ak `vystavy.json` už existuje, NEPREPÍŠE (skript má `--force` na re-migráciu).

Skript spusti raz manuálne pri deployi. App pri štarte tolerantne načíta chýbajúci súbor (0 výstav).

## CRUD endpointy (webreview/app.py — vzor „Poznámky")

- `GET /api/vystavy` → `{vystavy: [...]}` (celý list, zoradený podľa stavu/mesiaca).
- `POST /api/vystavy` (add) → nový objekt s `uuid4`, status `""`, feed `[]`; validácia povinné `nazov`; dĺžkový cap; **formula-injection reject** (`_FORMULA_LEAD`) na `nazov/miesto/email/...` (idú do mailu).
- `POST /api/vystava` (edit + delete v jednom, vzor `api_note`):
  - `{id, delete: true}` → odstráni.
  - `{id, fields: {nazov, datum, ...}}` → prepíše whitelist editovateľných polí (+ reject formula-lead).
  - `{id, status: "<hodnota>"}` → manuálny reset stavu (validuj proti povoleným hodnotám; reset na „" vymaže msgid).
- `POST /api/vystava/ideme` `{id}` → **schválenie (Chain C manuál)**: over stav `akcia bude` → pošli prihlášku (mail „prihláška") → status `poziadane`, `email_ziadost_msgid`, `Prihlaška poslaná`, feed „prihláška poslaná". Ak mail zlyhá → 502, stav nemení. (Toto je jediný „send" cez tlačidlo — manažérova explicitná akcia, ako „Nedostupné" send button #100.)
- `POST /api/vystava/posli-otazku` `{id}` → **manuálne poslanie otázky** pre jednu výstavu (mimo automatizácie): pošli mail „otázka" → status `otazka`, msgid, feed. 502 pri zlyhaní.

Atomický store `_load_vystavy`/`_save_vystavy` (kópia `_load_notes`/`_save_notes`, `.tmp`+`os.replace`, `with _lock:`).

## Email — texty VERBATIM z n8n (from `"Forestshop.sk" <info@forestshop.sk>`, BCC `drlik.marek@gmail.com` default)

**Message-ID threading:** `_send_mail_html` vracia len `bool`. Pre IMAP vláknovanie treba mať kontrolu nad `Message-ID`. Rieš: nový helper `_send_vystava_mail(to, subject, text_body) -> str|None` (vráti vygenerovaný Message-ID pri úspechu, `None` pri zlyhaní) — reuse SMTP config z `_send_mail_html` (host/port/user/pass/from/BCC), ale s explicitným `msg["Message-ID"] = make_msgid(domain="forestshop.sk")` a pošle ako `text/plain` (maily sú čistý text). Uložený msgid ide do `email_*_msgid`.

**Mail „otázka":**
Predmet: `Otázka ohľadom: {nazov} dňa {datum}`
```
Dobrý deň,

obraciam sa na Vás s otázkou, či aj tento rok plánujete organizovať podujatie {nazov} v termíne {datum}.

Ak áno, prosím Vás o krátke potvrdenie. Následne Vám pošlem ďalší email so všetkými potrebnými detailmi a informáciami k prihláseniu.

Vopred ďakujem za odpoveď.

S pozdravom,
Štepán Drlík
ForestShop.sk
```

**Mail „prihláška":**
Predmet: `Žiadosť o účasť: {nazov} dňa {datum}`
```
Dobrý deň,

ďakujem za potvrdenie, že podujatie {nazov} sa bude konať aj tento rok dňa {datum}.

Týmto sa záväzne prihlasujem ako vystavovateľ za spoločnosť ForestShop.sk. Predbežne mám záujem o stánok veľkosti {velkost_stanku}.

Prosím o potvrdenie prijatia prihlášky.

V prípade akýchkoľvek otázok ma kedykoľvek kontaktujte.

Vopred ďakujem a teším sa na spoluprácu.

S pozdravom,
Štepán Drlík
ForestShop.sk
```

## IMAP helper (od nuly — `src/parovanie/vystavy_imap.py`, pure + testovateľný)

- `parse_inbox(raw_messages) -> list[dict]` — **pure** funkcia (testovateľná s fixtúrami): z parsovaných mailov vráti `[{from, subject, in_reply_to, body_text, date}]`.
- `match_reply(messages, vystavy, awaited_status, msgid_field) -> list[(vystava_id, trimmed_quote)]` — **pure**: pre každú výstavu v stave `awaited_status` nájde mail kde `from==vystava.email` (case-insensitive) AND `in_reply_to==vystava[msgid_field]` → vráti (id, orezaný citát). Reuse orezávača (nižšie).
- `trim_quote(body_text) -> str` — **pure**: odreže reply-chain (regex: `Dňa .* napísal:`, `Dne .* napsal:`, `On .* wrote:`, `From: `, `---.*(Original|Originálna|Forwarded)`) — vráti len novú časť odpovede (skrátené na ~500 znakov pre feed).
- `fetch_inbox() -> list[dict]` — **I/O** (nie v teste): `imaplib.IMAP4_SSL(host, port)`, login, `SELECT INBOX`, `SEARCH SINCE <7 dní>`, `FETCH` (RFC822), parse cez `email.message_from_bytes` → `parse_inbox`. Prihlásenie z `data/.mail_env`: `IMAP_HOST` (default `mbox.myshoptet.com`), `IMAP_PORT` (default `993`), reuse `MAIL_USER`/`MAIL_PASS`. Self-signed allow (ssl context). Chyba spojenia → prázdny list + log (automat sa neposunie, ale nespadne).

## Automatizácie (3, default VYPNUTÉ, tz Europe/Bratislava)

Registrácia v `AUTOMATIONS_REG` + `AUTOMATION_DESCRIPTIONS` (app.py), run_fn pred `AUTOMATIONS_REG`. **Bez nav tabov** (efekt vidno v tabe Výstavy).

1. **`vystavy_otazka`** — „Výstavy: rozposlať otázky" — `{"daily_at": "06:00"}`.
   run_fn: pre každú výstavu `status==""` AND `sposob=="email"` AND `kedy_riesit==aktuálny mesiac (sk-SK monthLong, lowercase)` AND `email` neprázdny → pošli mail „otázka" → status `otazka`, msgid, `email_datum`, feed. Summary `{poslane: N, preskocene: M}`.

2. **`vystavy_odpoved_otazka`** — „Výstavy: kontrola odpovedí na otázku" — `{"daily_at": "09:00"}`.
   run_fn: `fetch_inbox()` → `match_reply(msgs, vystavy, "otazka", "email_otazka_msgid")` → pre každý match: status `akcia bude`, feed „prišla odpoveď + citát". Summary `{najdene: N}`.

3. **`vystavy_odpoved_prihlaska`** — „Výstavy: kontrola odpovedí na prihlášku" — `{"daily_at": "09:30"}`.
   run_fn: `fetch_inbox()` → `match_reply(msgs, vystavy, "poziadane", "email_ziadost_msgid")` → status `odpovedane od organizatora`, feed. Summary `{najdene: N}`.

(Chain C = manuálne tlačidlo `/api/vystava/ideme`, nie automatizácia — manažérovo rozhodnutie, okamžitý send.)

Runner efekt zapisuje do `vystavy.json` cez `_save_vystavy` (import z app.py do run_fn, alebo run_fn v app.py má prístup). run_fn musí byť bezargumentová a atomicky uložiť.

## Frontend — záložka „Poľovnícke výstavy" (KARTY, nie tabuľka)

`TABS` += `["vystavy", "Poľovnícke výstavy"]` (app.js), `NAV_ICONS.vystavy` (napr. ikona stánku/kalendára), `PAGE_TITLES.vystavy`, `NAV_KEYS` += `"vystavy"` (app.py — guard test). `index.html`: `<section id="tab-vystavy" hidden></section>`, cache-bust `app.js?v=+1` (a `style.css` ak treba).

**Zoznam = karty, zoskupené/zoradené podľa stavu** (nie 17-stĺpcová tabuľka — to je požiadavka „zrozumiteľnejšie než tabuľka"):
- Hore: tlačidlo **„+ Pridať výstavu"** + prípadný filter podľa stavu.
- Každá výstava = karta: **veľký názov**, dátum + miesto, **farebný badge stavu**, kontakt (osoba/tel/email), veľkosť stánku, mesiac riešenia. Vpravo per-stav akčné tlačidlo:
  - stav „Nová" → „Pošli otázku" (`/api/vystava/posli-otazku`).
  - stav „Odpovedali — čaká na rozhodnutie" (`akcia bude`) → zvýraznené **„Ideme na túto výstavu"** (`/api/vystava/ideme`).
  - inak žiadne tlačidlo (čaká na IMAP) alebo len info.
- Klik na kartu → rozbalí **detail/edit**: editovateľné polia (inputy) + **feed** (chronológia: čo sa poslalo, aké prišli odpovede — citáty), dropdown na manuálny reset stavu, tlačidlo „Uložiť" a „Zmazať".
- Add form = tie isté polia, prázdne.

Feed robí flow zrozumiteľným: manažér vidí pri každej výstave presne čo sa udialo (poslaná otázka → prišla odpoveď „…" → poslaná prihláška → potvrdené).

## Testy (bez živej siete — fixtúry + mock SMTP/IMAP)

- **Unit pure** (`tests/test_vystavy_*.py`): `migrate_vystavy` mapovanie + normalizácia stavov; `trim_quote` (odreže reply-chain rôzne formáty); `match_reply` (from+inReplyTo match, ignoruje nesúvisiace maily, viac výstav jedného organizátora → vyberie správnu podľa msgid); stavové prechody.
- **Endpoint** (Flask test client, vzor `test_webreview_nedostupne.py`): CRUD (add/edit/delete/list); `/api/vystava/ideme` (mock `_send_vystava_mail` → status prejde na `poziadane`, msgid uložený, zlyhanie→502 stav nezmenený); `/api/vystava/posli-otazku`; formula-injection reject (400).
- **Automation run_fn** (mock inbox + mock send): `vystavy_otazka` pošle len tento-mesiac+prázdny-stav+email; `vystavy_odpoved_*` posunú stav pri matchi. **Žiadny reálny SMTP/IMAP** (monkeypatch ako #100).
- **NAV_KEYS guard**: `test_nav_keys_match_appjs` musí prejsť (pridať `vystavy` do TABS aj NAV_KEYS).
- **E2E** (`tests/e2e`, pytest-playwright, vzor webreview skill): otvor tab Výstavy, over karty sa vykreslia, add → edit → delete jednej testovacej výstavy (uprac po sebe), čistá konzola. Toggle po sebe upratať (fixture server zdieľaný).

## Bezpečnosť

- Žiadne tajomstvá do gitu: IMAP/SMTP creds len `data/.mail_env` (gitignored). Discord tokeny/ID sa v migrácii NEPOUŽÍVAJÚ (ideme in-app).
- Editovateľné polia výstav idú do mailu → **reject formula-lead na vstupe** + escape ak by sa niekedy písali do CSV.
- Automatizácie posielajúce mail = **default OFF** (#93). IMAP-čítacie tiež default OFF (konzistencia; manažér zapne celý flow keď chce).
- Maily posielajú naživo organizátorom → default-off + manuálne tlačidlá dávajú manažérovi plnú kontrolu; žiadne nechcené hromadné odoslanie.

## Verzia

`src/parovanie/__init__.py` bump `0.83.0` → `0.84.0` PRED prácou (prvý commit).

## Mimo rozsahu (YAGNI)

- PDF-prihlášky (`sposob=pdf`) — automat mail neposiela, manažér rieši ručne (len vlajka v karte). Generovanie PDF nie je súčasťou.
- Google Sheet spätný zápis — migrujeme PREČ zo Sheetu, appka je nový zdroj pravdy. Sheet ostáva ako záloha, appka doň nepíše.
- Discord — úplne vypustený (in-app feed ho nahrádza).
