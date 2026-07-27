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
**vždy s vypnutým plánovačom**
`WEBREVIEW_NO_SCHEDULER=1 WEBREVIEW_PORT=8811 PYTHONPATH=src nohup .venv/bin/python webreview/app.py &`,
Playwright screenshot (LEN GET — nav prepínanie + tmavý mód sú bezpečné; NEklikaj row-toggly =
POST do živých dát), potom **`kill`** (ten krok NEVYNECHAJ). NIKDY nereštartuj živú :8801 kvôli
náhľadu.

**PREČO `WEBREVIEW_NO_SCHEDULER=1` (#262):** presne táto odhodená inštancia raz osirela a bežala
ŠTYRI DNI vedľa ostrej služby — s vlastným plánovačom automatizácií nad tými istými `data/out`
a so štyri týždne starým kódom (zákaznícke maily 09:00, nočný zápis do eshopu 21:00, platený
scrape 05:00). Dva plánovače nad jedným dátovým priečinkom si pretekajú nočné behy a vedia
poslať zákazníkovi mail dvakrát. S tou premennou inštancia NEMÁ plánovač vôbec, takže
zabudnutý proces sám od seba nikdy nič nespustí (žiadne 09:00 maily, žiadny 21:00 zápis do
eshopu, žiadny platený 05:00 scrape). Ručné „⚡ Spustiť teraz" v jej UI beží ďalej — vypína sa
NEobsluhovaný plán, nie klik prihláseného človeka; preto ju aj tak ZABI, keď dokončíš screenshot. Druhá poistka je v appke: plánovač si berie
medziprocesový nárok (`data/out/.scheduler.lock`, flock) a druhá inštancia ho nespustí — do logu
napíše, kto ho drží. Nárok drží otvorený deskriptor, takže pád procesu ho uvoľní sám (žiadny
zabudnutý pidfile). V UI to už aj VIDNO: `/api/automations` vracia `scheduler`
(`running` / `blocked` / `off`) a nad automatizačnými tabmi sa zobrazí žltý banner — bez neho
vyzerala vypnutá aj zablokovaná inštancia úplne zdravo, lebo „Ďalší beh" sa číta z uloženého
stavu, nie z bežiaceho časovača.

**POZOR — `WEBREVIEW_NO_SCHEDULER=1` NIE JE izolácia od ostrej služby:** náhľadová inštancia
zdieľa ten istý `data/out/automations.json`, takže keď v NEJ klikneš „▶ Štart"/„⏹ Stop",
prepíšeš `enabled`/`next_run` pre OSTRÚ službu — jej plánovač tú automatizáciu odteraz spustí
(alebo prestane spúšťať). Premenná odoberá časovač TEJTO inštancii, nie jej schopnosť
naplánovať cudzí. V náhľade preto nič neprepínaj — len sa pozeraj.

Dva taby: **Kontrola párovania** (review kariet) a **Na objednanie** (doobjednanie u dodávateľa).

## Úložiská: LENIVÉ cesty, JEDEN reader/writer, medziprocesový `_lock` (#261/#264)

Kontext, prečo to takto je: 2026-07-26 testovací beh vymazal všetkých 2831 rozhodnutí
manažéra. `DECISIONS` bola konštanta počítaná pri IMPORTE, takže `monkeypatch OUT`
nepresmeroval nič a fixtúra sa zapísala do ŽIVÝCH dát.

- **Nový store deklaruj VŽDY `X = _store("meno.json")`**, nikdy `os.path.join(OUT, ...)` na
  module-leveli. `_StorePath` sa správa ako string (`open`, `os.path.*`, `+ ".tmp"`, `%s`),
  takže volajúci nič nevie a `monkeypatch.setattr(webapp, "X", str(tmp))` funguje ďalej.
  Drift stráži `test_no_store_path_is_frozen_at_import` — **cez AST** (`OUT` čítaný mimo tela
  funkcie), nie hľadaním reťazcov: predošlá verzia videla len module-level `str` globaly,
  takže `Path(OUT)/"x"`, f-string či cesta v dicte/class-atribúte/defaulte jej unikli.
  Rovnaká lekcia platí pre každý drift guard: **guard, ktorý počíta výskyty reťazca, prejde
  aj keď nič nestráži** (ten e2e počítal literál `'"WEBREVIEW_OUT": str(out)'`, takže fixture
  s inak pomenovaným adresárom sa rátal ako nula serverov). Píš ich nad AST.
- **Čítaj `_read_json_store(X, {})`, píš `_atomic_write_json(X, d, ...)`** — nekopíruj
  try/open/json.load ani tmp+replace (bolo ich 17 + 29). Reader degraduje LEN
  `FileNotFoundError` (prvý beh) a `ValueError` (useknutý zápis, vrátane
  UnicodeDecodeError); **každá iná `OSError` musí prebublať** — „súbor sa nedá prečítať"
  nie je dôkaz, že manažér nič neurobil, a tiché `{}` je presne tá strata (nález revízie).
- **`protect=True` = neopakovateľná práca; POTVRDENKOU je samotné ČÍTANIE, nie počet.**
  Writer povolí ZMENŠENIE len vtedy, keď zapisovaná mapa JE objekt, ktorý reader vrátil
  (alebo ho volajúci pomenuje cez `prev=`). **Prvá verzia si pamätala len POČET a bola tým
  trvalo odzbrojená** — appka číta `decisions.json` pri každom načítaní stránky, takže
  „naposledy sme čítali N a na disku je N" platilo vždy a incidentný zápis by prešiel.
  Rast nikdy nevyžaduje potvrdenku.
  - **Potvrdenka NESMIE byť JEDEN slot na store — musí byť PER-THREAD** (druhá revízia
    PR #265). Flask beží `threaded=True` a KAŽDÝ GET číta chránené story (`/api/orders` ich
    načíta osem, `_require_login` číta `users.json` pri každom requeste, tab pollne
    dokola). Pri jednom slote vyhrávalo POSLEDNÉ ČÍTANIE: displejové čítanie z iného
    vlákna trafilo okno medzi `_load_x()` writeru a jeho kontrolou, prepísalo potvrdenku a
    **manažérov vlastný klik dostal 503** (namerané: 11 zo 400 odznačení pri troch
    polleroch, 317 zo 400 bez pauzy). Preto `_thread_reads` (this-thread ring, iné vlákno
    ho nevie vytesniť — read-modify-write je vždy v jednom vlákne) + malý zdieľaný ring.
    **Neriešiť to zamknutím GET-ov** — to serializuje každé čítanie za každý zápis zadarmo.
  - **Zdieľaný ring je LEN „best effort" — NESPOLIEHAJ sa naň** (tretia revízia PR #265).
    Je to jeden 8-slotový ring pre všetky vlákna a pridáva doň KAŽDÝ GET, takže potvrdenku
    z iného vlákna vytesní v priebehu mikrosekúnd: namerané 300/300 zamietnutých zápisov
    pre writer, ktorý sa naň spoliehal, pri štyroch polleroch. V strome na ňom nezávisí
    nič — všetkých 24 `protect=` zápisov číta aj zapisuje v JEDNOM `with _lock:` bloku,
    teda v jednom vlákne. **Nový read-modify-write cez dve vlákna ber ako NEPODPOROVANÝ**
    (čítaj v tom vlákne, ktoré zapisuje, a pošli `prev=`), nie ako niečo, čo ring unesie.
  - **Retenciu drž pri zemi:** potvrdenka drží načítaný store nažive (jedno čítanie
    `review_data.json` ≈ 15 MB), takže po ÚSPEŠNOM zápise sa oba ringy resetujú na ten
    jeden zapísaný objekt (staršie potvrdenky sú aj tak neplatné — súbor sa zmenil). To
    zároveň drží „druhé undo za sebou" funkčné, inak spadne na 503. **MEDZI zápismi sa
    ringy zase naplnia** (až `_READ_RING` kópií na ring a cestu — pri `decisions.json`
    ≈ 1,3 MB na kópiu, teda ~10 MB) — je to ohraničené a v poriadku, ale NIE je pravda
    „na store prežije nanajvýš jedna načítaná kópia", ako tvrdila predošlá verzia.
  - **Kto prestavia mapu NANOVO, musí poslať `prev=`** (`_save_decisions(d1, prev=d0)`,
    `_save_vystavy(kept, prev=vystavy)`). Kto mutuje načítaný objekt (drvivá väčšina ciest)
    nepotrebuje nič. Pri novom store-zápise sa vždy spýtaj: *je toto ten istý objekt, ktorý
    som čítal?*
  - **`prev=` je ZÚŽENIE, nie zadné dvierka.** Prvá verzia porovnávala len `rec[0] is prev`
    a zapisovanú mapu neporovnávala s ničím — pomenovanie reálneho čítania teda povoľovalo
    zapísať ČOKOĽVEK (`_save_decisions({}, prev=_load_decisions())` zmazalo 2831 záznamov
    bez sťažnosti). Prestavba smie len UBERAŤ, takže `_is_derived_from` žiada podmnožinu:
    kľúče pri mape — **vrátane VNORENÝCH máp, ktoré `protect=("orders",)` stráži**, lebo
    vonkajšiu sadu kľúčov nechá rovnakú každý reálny writer, takže kontrola len na
    najvyššej úrovni dovolila `prev=` zápisu podstrčiť cudziu mapu „komu sme už písali";
    **a porovnávaj až PO kontrole typu (`type(a) is not type(b)` → zamietni)** — obe
    vetvy porovnávajú rovnaké s rovnakým (`isinstance(a, dict) and isinstance(b, dict)`,
    to isté pre list), takže zápis, ktorý tú vnorenú mapu NAHRADÍ niečím iným (`None`,
    list, string, `0`) alebo jej kľúč rovno vypustí, netrafil ani jednu vetvu a prešiel
    ako „odvodený" — všetky štyri tvary namerané ako POVOLENÉ (finálna revízia PR #265);
    a pri liste identita prvkov **alebo hodnotová zhoda** — samotná identita zamietala
    poctivú prestavbu `[dict(x) for x in prev if …]`, a to hláškou „nepochádza z načítania
    tohto úložiska", čo je nepravda a pozvánka nájsť si obchádzku (tretia revízia).
    Pri každom novom „escape hatch" parametri sa pýtaj: *porovnáva sa vôbec to, čo
    zapisujem — a na správnej úrovni?*
  - **NEČITATEĽNÝ súbor ≠ 0 záznamov.** Useknutý/nerozparsovateľný store sa zazálohuje
    (`<store>.corrupt-<ts>`) a zápis sa ODMIETNE — aj rast (nad neparsovateľným súborom
    nevieme, z čoho rastieme). Predtým sa počítal ako 0 a jeden klik prepísal ~1400
    zachrániteľných záznamov. Prázdny (0 B) súbor korupcia NIE JE.
  - **`protect=("orders",)` / `("escalation",)` pre dedup story.** `orders_reminder.json` a
    `posta_uncollected.json` majú mapu „komu sme už písali" ZANORENÚ a vonkajšie kľúče sa
    nikdy nemenia — `protect=True` bol pre ne úplne nečinný. Guarduj tú úroveň, kde dáta
    naozaj sú.
  - Legitímne zmenšenia (undo, hromadné odznačenie `/api/ordered/bulk`, retention prune
    dedup mapy) čítajú pod `_lock` chvíľu predtým, takže prejdú — **prvá verzia pravidla
    znela „prázdne cez neprázdne = zamietni" a HNEĎ rozbila hromadné tlačidlo**, over každú
    takú ochranu proti bulk cestám. Odmietnutie letí ako `StoreWipeRefused` → 503 so
    slovenskou hláškou (nie 500 — na 500 manažér klikne znova), a hláška musí dávať zmysel
    aj CRONU, nie len prehliadaču.
  - **Každý `protect=` store patrí do `scripts/backup_data.sh`** — pokrytie deriveruje test
    z AST `protect=` volaní v `app.py`; ručne udržiavaný zoznam si tri story nevšimol.
- **`_lock` = RLock + `fcntl.flock` na `OUT/.store.lock`.** Existujúce `with _lock:` bloky
  sú tým atomické aj MEDZI PROCESMI (lost update). Dôsledky: sieťové/SMTP volanie ostáva
  MIMO `_lock` (teraz by blokovalo aj druhý proces), `_lock` je reentrantný zámerne (writer
  si ho berie sám) a čakanie je ohraničené (30 s → `StoreLockTimeout` → 503; env
  `WEBREVIEW_STORE_LOCK_TIMEOUT` na skrátenie v testoch). `acquire(blocking=False)` vracia
  `False` ako `threading.Lock`, nevyhadzuje.
- **`_lock` VIE VYHODIŤ — každé miesto, kde predtým stál `threading.Lock`, preto prever.**
  Dva reálne nálezy: `AutomationRunner._execute` čistil príznak „už beží" ako prvý riadok
  VNÚTRI zámku, ktorý si berie na zápis výsledku — jeden timeout a automatizácia ostala
  „bežiaca" navždy (plánovač ju preskakuje, „⚡ Spustiť teraz" hlási „už beží"), hoci maily
  už odišli; a `_bootstrap_admin()` beží pri IMPORTE, takže výnimka tam neznamená degradáciu
  ale to, že sa služba VÔBEC nenaštartuje. Pravidlo: príznaky čisti v `finally` MIMO zámku,
  zápis výsledku obaľ, a všetko, čo beží pri importe, obaľ
  `(StoreLockTimeout, StoreWipeRefused, OSError, ValueError)`.
  - **`ValueError` v tej trojici CHÝBAL a to je práve tá najpravdepodobnejšia korupcia**
    (druhá revízia PR #265): useknutý zápis JSONu / mid-UTF-8 hodí `JSONDecodeError` resp.
    `UnicodeDecodeError` — oboje `ValueError`, ani jedno `OSError`. Useknutý `users.json`
    tak zhodil celý import (systemd restart loop, žiadne UI). Boot-time try/except píš vždy
    proti tomu, ako súbor reálne zomiera, nie proti tomu, ako sa nedá otvoriť. Za behu to
    isté: `_require_login` mení nečitateľný `users.json` na **503 so slovenským návodom**
    (ne 500 — na 500 sa klikne znova).
  - **POZOR na opačnú chybu: `finally` s vyčistením príznaku NESMIE bežať PRED zápisom
    výsledku.** Presne to zaviedla prvá oprava a otvorila okno na DUPLICITNÝ BEH: príznak
    „už bežím" bol zhodený, kým `automations.json` ešte držal starý, dávno prešlý
    `next_run` — plánovač tiká každých 30 s, nevidí ani jedno, a spustí automatizáciu
    DRUHÝKRÁT (duplicitné maily zákazníkom). Správny tvar: **JEDEN `try`, ktorého telom je
    beh AJ zápis výsledku, a `finally` s príznakom až za ním.**
- **`PRODUCTS` (review_data.json) sa mení POD bežiacou appkou** —
  `scripts/add_supplier_review_data.py` pridá dodávateľa za behu. `run_shoptet_sync` preto
  súbor pred resyncom ZNOVA načíta pod `_lock`: bez toho by zapisoval boot-time zoznam,
  writer to (správne) odmietne a automatizácia padá KAŽDÚ hodinu až do reštartu.
- **Dlhý beh NESMIE uložiť mapu, ktorú prečítal na začiatku.** Nočné write-backy sedia
  minúty v import subprocese; `_record_uploaded(load_fn, save_fn, entries)` znovu načíta
  a MERGNE pod zámkom (a vráti post-run mapu pre súhrny). Inak sa stratí kľúč, ktorý
  medzitým zapísal niekto iný — a stratený review kľúč znamená, že sa nabudúce nahrá
  odkaz, ktorý manažér už opravil.
- **`fsync` PRED `os.replace`** v oboch writeroch. `os.replace` je atomický voči ČITATEĽOM,
  nie voči pádu prúdu: premenovanie môže byť trvanlivé skôr než dáta — presne tak vznikne
  useknutý store z predošlého bodu. **Plus `fsync` ADRESÁRA PO `os.replace` — v KAŽDOM
  writeri, nielen v tom JSON-ovom**: bez neho môže pád prúdu stratiť samotné premenovanie
  a vrátia sa staré bajty — trvanlivý obsah zverejnený netrvanlivým adresárovým záznamom.
  Prvá oprava ho dala len do `_write_json_locked` a nechala diery v `_atomic_write_bytes`
  (55 MB export + zákaznícke cache) a v `AutomationRunner._save` — teda práve na súbore,
  ktorého useknutie fail-closed loader hlási ako korupciu (tretia revízia PR #265).
  **A oba fsync-y otestuj** (spy na poradie `os.fsync`/`os.replace`) — pôvodná oprava
  fsyncu bola jediná v celej vlne BEZ testu: zmazanie oboch riadkov nechalo suite zelenú.
  - **Trvanlivosť je BONUS — nikdy nesmie zhodiť už HOTOVÝ zápis.** `_fsync_dir` prehĺtal
    chyby z `os.open`/`os.fsync`, ale nie `os.close` vo svojom `finally` — a volá sa AŽ ZA
    `os.replace`, vnútri `except BaseException: unlink(tmp); raise`. Jedna chyba pri
    zatváraní adresára by tak ohlásila 55 MB export, ktorý JE na disku, ako zlyhaný (plus
    nezmyselné „temp file sa nepodarilo odstrániť" — v tej chvíli už neexistuje). Pravidlo:
    v takom helperi obaľ KAŽDÉ volanie vrátane `close`, a jeho volanie daj **za** `try/except`,
    ktorý upratuje po neúspechu (finálna revízia PR #265).
- **Tmp meno: JSON writer per-proces (`<store>.<pid>.tmp`, beží pod `_lock`), ale
  `_atomic_write_bytes` MUSÍ `tempfile.mkstemp`** — `orders_cache.csv` má DVOCH pisateľov v
  jednom procese (request thread pri starej 30-min cache + hodinový sync), takže pid-ové meno
  bolo pre oboch to isté: jeden premenoval inode, do ktorého druhý ešte písal, a ten potom
  dopisoval rovno do ŽIVEJ cache. Per-proces meno nestačí, keď sú pisatelia dva THREADY.
  - **Náhodné meno ale prestalo byť samo-obmedzujúce → treba METLU pri štarte.** Pid-ové
    meno nechalo max. jeden sirotinec na život procesu; `mkstemp` nechá jeden ~55 MB
    `products.csv.XXXX.tmp` po KAŽDOM SIGKILL/OOM počas exportu a nič ich nemaže.
    `_sweep_stale_tmp(OUT, dirname(SRC))` pri importe zmaže `*.tmp` staršie ako 12 h (vekový
    limit = bezpečnosť: živý zápis trvá sekundy, takže cudzí rozpísaný tmp nikdy nie je
    12 h starý). Metla musí čítať `OUT` LENIVO (vnútri funkcie), inak ju zrazí drift guard.
    **A musí ísť cez `_refuse_live_data_under_pytest` — pytest sieť patrí ku KAŽDEJ
    deštruktívnej operácii, nielen k zápisom.** Metla bola jediné `os.unlink` v strome
    mimo tej siete, a beží pri IMPORTE nad `OUT` + `dirname(SRC)`: pri nepripnutom
    `WEBREVIEW_OUT` (presne konfigurácia incidentu) mazala živé `data/out/*.tmp`
    (tretia revízia PR #265). **A sieť musí kryť OBA tie adresáre**: pôvodne poznala
    `data/out` (aj čokoľvek pod ním) a SÚBOR `products.csv`, ale nie `data/` samotné —
    čiže presne ten druhý argument metly (`dirname(SRC)`), takže helper, ktorý prestaví
    `WEBREVIEW_PRODUCTS` na živý export, ju pustil mazať živé `data/*.tmp` (finálna
    revízia PR #265). Volajúci pri štarte musí zamietnutie prežiť
    (`except (OSError, StoreWipeRefused)`), inak z ochrany spravíš mŕtvy import.
  - **`mode` sa dedí z `mkstemp` (0600) — nešir ho naslepo na 0644.** `orders_cache.csv` a
    `customers_cache.csv` držia mená, e-maily a telefóny zákazníkov (tá istá trieda dát ako
    `posta_uncollected.json` / `orders_reminder.json`, ktoré 0600 majú); číta ich len táto
    jedna `systemd --user` služba. Export (`products.csv`) ostáva 0644 ako predtým.
    AST test stráži, že každý zápis týchto dvoch pýta `mode=0o600`.
- **Testy: `tests/conftest.py` prepne `WEBREVIEW_OUT` + `WEBREVIEW_PRODUCTS` do tmp** pri
  IMPORTE conftestu (pred kolekciou), takže sa žiadny test nedostane na `data/out` ani keď
  nič nepatchne. Dev box sa tým správa ako CI. **Nový e2e fixture server** dedí pinned env
  cez `**_AUTH_ENV` — tam žije aj `WEBREVIEW_NO_SCHEDULER=1` (bez neho každý z 13 fixture
  serverov štartoval OSTRÝ plánovač; neškodilo to len preto, že žiadna fixtúra neseeduje
  `enabled: true`).
  - **To isté platí aj pre BACKEND test: nikdy neštartuj produkčný `RUNNER`.** Má
    zaregistrované reálne mailujúce automatizácie a v pytest procese bol bezpečný len
    náhodou (`tick=30.0` prežil test, fixture dir nemal `automations.json`). Použi stub
    `AutomationRunner` s neškodným `run_fn` (tretia revízia PR #265).
  - **Každý nový test si over, či je ZÁVISLÝ NA OPRAVE** — spusti ho proti kódu PRED
    opravou (`git stash push <súbor>`). Test „cudzie vlákno zapíše `{}`" prechádzal
    rovnako pred aj po zavedení per-thread potvrdenky, takže o nej nedokazoval nič;
    až zápis mapy ODVODENEJ z čítania druhého vlákna (`prev=`) sa o ňu naozaj oprie.
- **Nový `protect=True` store pridaj aj do `scripts/backup_data.sh`** — zoznam stráži
  `test_the_backup_script_covers_every_irreplaceable_store`.
- **Story MIMO `app.py` nedostanú tieto pravidlá zadarmo — `automations.json` ich nemal
  ani jedno.** `AutomationRunner` má vlastný `_load`/`_save` (modul je zámerne bez Flasku),
  takže vracal `{}` pri parse erroru a nefsyncoval — na súbore, ktorý ROZHODUJE, ktoré
  automatizácie sú zapnuté. Useknutý stav teda ticho vypol pripomienky, poštu aj hodinovú
  synchronizáciu a tab vykreslil čistý „prvý beh". Teraz padá naprázdno (`AutomationStateCorrupt`
  → 503 s návodom, kópia `<state>.corrupt-<ts>`, originál nedotknutý) a `_save` fsyncuje.
  **Keď pridávaš stav do iného modulu, prejdi ten istý checklist: nečitateľné ≠ prázdne,
  fsync pred replace (aj adresára), kópia pri korupcii, v `backup_data.sh`.**
  - **Fail-closed na serveri je len POLOVICA — frontend musí čítať HTTP STATUS.**
    `loadAutomations` robil `(await fetch(...)).json()` a `j.scheduler || 'running'`, takže
    503 s návodom na opravu vykreslil ako čistý prvý beh a banner SKRYL: manažér videl
    „nič nie je nastavené, plánovač je zdravý", kým boli vypnuté všetky pripomienky
    (tretia revízia PR #265). Globálny `fetch` wrapper rieši LEN 401 — nič iné nechytí.
    Pravidlo: **ku každému fail-closed endpointu patrí vetva `if (!r.ok)`, ktorá zobrazí
    `j.error` zo servera**, a e2e test, ktorý ten stav naozaj pošle po drôte
    (`page.route(...).fulfill(status: 503)`).
  - **Loader, ktorý parsuje JSON, MUSÍ overiť aj TYP.** `_load_users` bol jediný bez
    `isinstance` — `users.json` s `[]` prešiel parsovaním a spadol až na
    `users[email] = …` (TypeError pri IMPORTE = systemd restart loop) resp. `[].get(...)`
    (AttributeError = 500 na každom requeste). Do boot/`before_request` `except` tuple
    preto patrí aj `TypeError`. Zlá ručná oprava alebo zlý restore vyrobí presne toto —
    a je to to, na čo naša vlastná 503 hláška operátora navádza.
  - Starý test `test_corrupt_state_file_tolerated` tvrdil presný OPAK („poškodený stav = 0
    automatizácií a ďalší klik ho prepíše") — teda tú chybu chválil a jeho „recovery"
    prepísalo jediný dôkaz, čo bolo zapnuté. Keď test kodifikuje defekt, **nahraď ho vo
    vlastnom commite s odôvodnením**, nikdy ho neoslabuj potichu.
- **Stav hlás ODVODENÝ zo skutočnosti, nie zapamätaný z bootu.** `SCHEDULER_STATE` sa
  nastavil raz pri štarte, takže keď vlákno plánovača umrelo, `/api/automations` hlásil
  „running" naveky — presne ten zdravo vyzerajúci tab s budúcimi „Ďalší beh", proti ktorému
  banner vznikol. Teraz `SCHEDULER_INTENT` (čo inštancia zamýšľala) + `_scheduler_state()`,
  ktorý „running" degraduje na `dead` podľa `RUNNER.is_alive()`; banner to povie a pomenuje
  službu na reštart. Test na „running" preto MUSÍ spustiť skutočné vlákno — stubnutý
  `RUNNER.start` je práve ten stav, ktorý má rozlišovať. A testy, čo hýbu modulovým
  globálom, ho pinuj cez `monkeypatch.setattr(webapp, "X", webapp.X)`, nech ho nenechajú
  špinavý pre ďalšie testy.
- **Drift guard nad AST musí poznať VŠETKY tvary toho istého zápisu.** Ten náš videl len
  holý `ast.Name` `"OUT"`, takže cesta zamrazená z `webapp.OUT` (`ast.Attribute`) alebo
  rovno z `os.environ["WEBREVIEW_OUT"]` mu bola neviditeľná. Vždy si guard otestuj tým, že
  mu podhodíš každý tvar (a vylúč definíciu samotného `OUT`, nech neflaguje sám seba).

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

**GOTCHA — dodávateľ objednávky (`o.supplier`) VYHRÁVA nad ručným priradením (`o.assignedSupplier`); priradenie je len FILL-IN pre riadok BEZ vlastného dodávateľa (BUG 1).** Priradenie je per-PRODUKT, ale realita objednávky je per-RIADOK — ten istý kód môže mať jednu objednávku bez dodávateľa (dostane priradenie) a inú už s vlastným zo Shoptetu. Preto `effSup = o.supplier || o.assignedSupplier || '—'` (NIE naopak — obrátené poradie prebíjalo reálneho dodávateľa v zoskupení). A nočná `_do_upload_suppliers` (na prode ENABLED, píše `supplier` NAŽIVO) MUSÍ vylúčiť kódy, ktorých produkt UŽ má vlastného `supplier` v aktuálnom exporte — `_export_supplier_index()` (streamovane číta `supplier` stĺpec, #272) → `supplier_rows(..., exclude_codes=...)`. Bez toho stará priradenie natrvalo prepíše reálneho dodávateľa v eshope. `uploaded_suppliers.json` idempotencia sama nestačí (priradenie sa nemení → stále „nové"). Riadok BEZ `o.supplier` naďalej zobrazuje inline supplier-assign pole (`if (!o.supplier)`) — nič sa tu nemení. **FAIL-CLOSED na NEPOUŽITEĽNÝ export (PR #213 review, sprísnené PR #276 review):** samotný `_export_supplier_index()` stále vracia prázdnu množinu vlastných dodávateľov pri prázdnom exporte, ALE zápisový volajúci NESMIE fail-openovať — `_do_upload_suppliers` preskočí CELÝ supplier upload (`count=0`, `blocked=len(new_codes)`, `uploaded_suppliers.json` netknuté, log.warning) na export, ktorý je (a) PRÁZDNY/nečitateľný (tretia návratová hodnota = mal súbor vôbec nejaký obsah) ALEBO (b) má menej ako `EXPORT_MIN_CODES` kódov. Bez použiteľného exportu nevie, ktoré kódy sú chránené → skoro prázdna množina by povolila prepis reálneho dodávateľa. **Slabšia brána nesmie strážiť nebezpečnejšiu akciu:** do PR #276 sa zápis pýtal len „mal súbor nejaké bajty", kým (len hlásiaci) verdikt vedľa už vyžadoval `EXPORT_MIN_CODES` — pokazený feed s hŕstkou riadkov teda prešiel a staré priradenie prepísalo živého dodávateľa. Zádrž je bezpečná a samoliečivá (ďalší beh s dobrým exportom ich pošle), fail-open je nevratný prepis. POUŽITEĽNÝ-ale-partial export (nemá daný kód) blokádu NEspustí — kód sa len nevyloči a zapíše sa (pred-PR správanie), pozri „zámerná asymetria" nižšie. **TEST-PASCA:** každý test, čo vezme `_do_upload_suppliers`/`run_parovania_eshop`/`/api/n8n/upload-suppliers` cez reálny zápis, MUSÍ `monkeypatch _iter_export_lines` (NIE `_read_export_for_links` — nočný push ho od #272 vôbec nevolá; helper `_export_lines(text)` v test_webreview_parovania_eshop) na NEPRÁZDNY export (kódy s prázdnym `supplier`) — inak prejde na dev boxe (reálny `data/products.csv` existuje) a padne na CI (žiadny `data/` → prázdny export → blocked). Zdieľané fixtúry `_arm_suppliers` (test_webreview) + `iso` (test_webreview_parovania_eshop) to stubujú; over cez `WEBREVIEW_PRODUCTS=/nonexistent`.

**GOTCHA — SAFE loader platí pre DISPLAY/flag stores, NIE pre DEDUP stores (#225).** Než pridáš `try/except → {}` na nový loader, rozhodni, čo strata dát znamená: display flag = kozmetika (degraduj), evidencia „komu sme už poslali mail" = **duplicitný mail zákazníkovi** (nikdy nedegraduj). Detail nižšie v „Fail-closed dedup stores".

**GOTCHA — VŠETKY loadery čo kŕmia `/api/orders` používaj SAFE vzor `_load_instock` (try/except `(FileNotFoundError, json.JSONDecodeError)` → `{}` + `isinstance dict` guard), NIE `os.path.exists`+holé `json.load()`.** Ten starý vzor zhodil CELÝ `/api/orders` (500) pri jednom poškodenom súbore. Guardnuté: `_load_ordered`/`_load_waiting`/`_load_order_pairings`/`_load_variant_links` a (PR #213 review) aj `_load_decisions` + `_load_supplier_assign` — tie DVE sú NAJexponovanejšie (`decisions.json` sa píše pri KAŽDOM review kliku, `supplier_assignments.json` píše app AJ n8n), takže najviac náchylné na partial write; corrupt-store test ich MUSÍ prehnať reálnou corrupt-file cestou (monkeypatch `DECISIONS`/`SUPPLIER_ASSIGN` na poškodený súbor, NIE `_load_*` na lambdu). Endpointy `/api/ordered`+`/api/waiting` (ako instock/unavailable) validujú `key = str(body.get('key') or '').strip(); if not key: 400` — inak `str(None)='None'` zapíše trvalý smetný kľúč. Hromadné „označiť skupinu objednané" = `POST /api/ordered/bulk {keys,ordered}` (atomicky pod `_lock`, non-list keys → 400) + `markGroupOrdered(items, ordered)` v hlavičke skupiny. Stará nevybavená objednávka: `orderAgeDays(orderDate, now)` (pure, `now` injektovateľné pre test) + `!isHandled(o) && age>STALE_ORDER_DAYS(14)` → badge `.to-staleage`.

**Hlavička skupiny `.toorder-supplier` je teraz FLEX kontajner: `.tosup-label` (menovka PRVÁ — E2E `startswith(sup)` kontrakt) + `.tosup-bulk` tlačidlo.** Pri zmene hlavičky drž menovku PRVÚ a escapuj ju (`escapeHtml` — XSS test `.toorder-supplier i` count==0).

## Pridanie nového per-riadkového flagu — kopíruj `ordered`/`waiting` vzor (NEvymýšľaj)

1. **app.py**: `X = os.path.join(OUT, "x_items.json")` + `_load_x`/`_save_x` (atomický `os.replace` cez `.tmp`).
2. **app.py**: `@app.route("/api/x", methods=["GET","POST"])` — GET vráti mapu; POST `{key, x:bool}` pod `with _lock:` set/`pop`, `log.info`.
3. **app.py** `/api/orders`: `r["x"] = bool(x.get(r["key"]))`.
4. **app.js**: `let X = {}`; v `loadOrders` `X = (await (await fetch('/api/x')).json()).x || {}`; `saveX(key,on)` POST; v `renderOrderRow` trieda riadku `+ (X[o.key] ? ' x' : '')` + toggle button (synchrónne updatne DOM, async POST).
5. **style.css**: `.toorder-row.x { … }` + chip.
6. **index.html**: bumpni cache-bust `?v=N` na `style.css` AJ `app.js` (inak prehliadač drží starý JS/CSS).
7. **Testy**: unit (`monkeypatch.setattr(webapp,"X",str(tmp_path/"x.json"))` — endpoint persist + pole v `/api/orders`) + e2e (toggle on→off + reload persist, čistá konzola). E2e nový store si v teste **upracuj späť** (toggle off na konci) — fixture server je session-scoped, zdieľaný medzi testami.

**Živo-odvodený AGREGÁT (napr. farba supplier chip-ov podľa stavu) čítaj z FLAG MÁP, nie z `o.*`, a prekresli ho v KAŽDOM toggli.** Polia `o.ordered/waiting/instock/unavailable` v `ORDERS` sú zamrznuté v čase `/api/orders` fetchu — toggle handler updatuje globálne mapy (`ORDERED/WAITING/INSTOCK/UNAVAIL`, synchrónne PRED `await` v `saveX`) + lokálnu row triedu, ale `o.*` NIE. Preto agregát (chip `done`/`todo`) počítaj cez `isHandled(o)=!!(ORDERED[o.key]||WAITING[o.key]||…)` a po každom toggli zavolaj `renderOrderFilters()` (prekreslí `#filters` + `#toToolbar` a prepíše `.to-total` čipy NA MIESTE — riadky neprekresľuje NIKDY) — inak sa chip prefarbí až po reloade (bug #86). E2e MUSÍ klikať reálne tlačidlo v session (nie seed-before-load), inak nezachytí tento bug.

**Živo prepočítavaný prvok tabu (súhrn, prepínač) daj MIMO `#list` — do top baru — a renderuj ho z `renderOrderFilters` (#208).** `#toToolbar` v `index.html` (`render()` mu prepína `hidden` podľa `toorder`). Dôvod je ten istý dvakrát: (1) per-riadkový toggle ZÁMERNE neprekresľuje zoznam (len triedu riadku + `#filters`), takže prvok vnútri `#list` by po toggli klamal až do reloadu; (2) čokoľvek v `#list` musí prejsť `captureOpenEditors`/`restoreOpenEditors` mašinériou — nový repaint path okolo nej je presne to, čo #233 zakazuje. `renderOrderFilters` je jediné miesto, ktoré volá KAŽDÝ toggle aj `renderToOrder`, takže z neho zavolaj aj toolbar. NEDÁVAJ nový prvok do `#filters` — `#filters button` je selektor tuctu e2e (chip testy) a hociktoré ďalšie tlačidlo tam ich rozbije. **Ak sa prvok von z `#list` presunúť NEDÁ** (per-riadkový čip „Σ spolu“ — patrí k riadku), platí to isté pravidlo, len inak: prepíš ho NA MIESTE z toho istého `renderOrderFilters` (`refreshOrderTotals()` prejde `#list .toorder-row` a prepíše LEN `.to-total` text/`title`). **Span NEDOROBUJE ani NEODSTRAŇUJE — a nemá prečo**: či produkt čip vôbec dostane, závisí jedine na počte objednávkových RIADKOV dodávateľa s tým kódom (`totalChipSpec` → `all.lines < 2`), čo vychádza z groupingu `ORDERS` — a volajú ho len per-riadkové toggle, ktoré menia príznakovú mapu a nič iné. Zmena, ktorá riadok medzi skupinami naozaj presunie (priradenie dodávateľa), prekresľuje celý tab a čipy tam stavia `renderOrderRow`. Vetvy „dorob span" / „zmaž span" boli teda mŕtvy kód (overené: pri prepínaní všetkých riadkov na objednané a späť je prítomnosť spanu byte-identická) — zostali len `continue` guardy. **A z PREKRESĽOVACEJ cesty sa `refreshOrderTotals()` nevolá vôbec** (`renderOrderFilters({..., repaint: true})` z `renderToOrder`): to volanie stojí jeden riadok PRED `list.innerHTML = ''`, takže by prešlo riadky, ktoré o mikrosekundu zaniknú, a spravilo `groupQtyTotals` pass na dodávateľa nad uzlami, ktoré nikto neuvidí. Keď to volanie presúvaš, over, že KAŽDÁ meniaca cesta ostane krytá — prekresľujúce mutácie (hromadné označenie skupiny, rollback odmietnutého zápisu) pinuje `test_the_chip_follows_every_mutation_path`, tie neprekresľujúce `test_the_chip_follows_a_flag_toggle_without_a_repaint`. NIKDY to neriešiť prekreslením `#list` — to je presne ten repaint path okolo `captureOpenEditors`, ktorý #233 zakazuje.

**Filter, ktorý SKRÝVA riadky, musí vyňať riadok s NEULOŽENOU prácou v inline editore (#205).** Skrytie takého riadku = `restoreOpenEditors` zahodí snapshot (riadok neexistuje) a rozpísaný text zmizne BEZ hlášky — tichá strata, ktorú z príznakov odstránilo #214/#233. Vzor: `renderToOrder` si už `captureOpenEditors()` volá, tak z neho postav `busy` set kľúčov cez **ten istý predikát** `editorSnapHasWork(s, o)`, ktorý používa `restoreOpenEditors` (preto je vytiahnutý zo `restoreOpenEditors` do vlastnej funkcie — poradie jeho dvoch podmienok ostáva load-bearing, viď #233 vyššie). „Viditeľný" a „obnovený" sa tak nemôžu rozísť. Over MUTANTOM: bez výnimky musí `test_a_row_with_unsaved_typing_is_never_hidden_under_the_manager` padnúť.

**Celý tab má JEDEN predikát — `isHandled`; `outstandingOf = !isHandled` (#205/#206/#207/#208, revízia PR #236 pass 1-3).** Ten JEDEN predikát drží VŠETKO: filter „skryť poriešené" (#205), farbu chipu dodávateľa, súhrn v toolbare (#208), čip „Σ spolu" (#206), kopírovanie objednávky (#207) aj znenie `#empty`. **NEROZDEĽUJ ho** — pass 2 to skúsilo (užší „settled" rozsah = objednané/skladom/nedostupné, aby „čaká sa" ostalo v maile) a tab si začal protirečiť na obrazovke: zaparkuj obe položky jedného dodávateľa a toolbar píše „ostáva vybaviť 0 položiek z 2", čip vedľa neho „nevybavené: 3 ks", chip je ČERVENÝ (všetko poriešené) a so zapnutým filtrom #205 celá skupina AJ s kopírovacím tlačidlom zmizne, hoci appka tie 3 ks stále ráta ako prácu — dve čísla pre jednu objednávku, presne to, čo #206/#207 mali odstrániť. **„⏳ Čaká sa" = „toto nie je dnešná práca"** vo všetkých troch významoch, ktoré vymenúva vlastný tooltip tlačidla (čaká sa na dodávateľa / zbierame viac položiek / dohoda so zákazníkom) — ani jeden nepatrí do objednávky, ktorú manažér práve odosiela. Keď sa rozhodne zaparkovaný riadok objednať, VYPNE „čaká sa" a riadok sa do objednávky vráti; ten prepínač JE ten workflow. Rozsah dopíš aj do `title` tlačidla (nech to nie je folklór) a **pinni sám invariant**: súhrn #208, číslo „nevybavené" v tooltipe čipu, farba chipu a počet ks, ktoré reálne vypadnú z kopírovania, musia opisovať TÚ ISTÚ prácu — pri filtri #205 vypnutom aj zapnutom, pod „Všetci" aj pod chipom dodávateľa (`test_every_surface_agrees_when_a_group_is_parked`, bez reloadu medzi meniacim a overujúcim klikom). História, prečo tam ten jeden rozsah vôbec je: pôvodne mali plochy zámerne RÔZNY rozsah — čip nad VŠETKÝMI riadkami dodávateľa, kopírovaný text nad VYKRESLENÝMI (WYSIWYG). Oboje bolo zle a v opačnom smere: pri VYPNUTOM filtri #205 (default) je už vybavený riadok stále na obrazovke, takže padol do mailu a manažér doobjednal tovar na ceste; a so ZAPNUTÝM filtrom ukazoval čip „3 ks" nad riadkom, ktorý sa do schránky skopíroval ako „1 ks". **A ten jeden rozsah musí platiť aj BEZ reloadu:** čipy v `#list` prepisuje `refreshOrderTotals()` z `renderOrderFilters` (viď vyššie) — inak obrazovka drží číslo z posledného paintu, kým `copy.onclick` (zúžený až pri KLIKU, lebo toggle zámerne neprekresľuje zoznam) už vkladá menšie. Celé množstvo (aj vybavené) žije v `title` čipu, nie ako druhé číslo na výber; `renderOrderRow` aj `refreshOrderTotals` stavajú čip cez JEDEN `totalChipSpec({open, all}, code)`. **Prah čipu ráta na CELEJ skupine (`all.lines > 1`), nie na zvyšku** — pri vybavení 1 z 2 riadkov čip inak zmizne aj s tooltipom presne vtedy, keď je celý dopyt potrebný; text je zvyšok (`0 ks`, keď je vybavené všetko), tooltip celý dopyt. Keď pridáš tretiu plochu s počtom ks, napoj ju na ten istý helper — a pinni invariant „číslo v čipe == súčet v skopírovanom texte" v OBOCH polohách prepínača (`test_the_screen_number_equals_the_copied_number`) AJ bez reloadu (`test_the_chip_follows_a_flag_toggle_without_a_repaint`). Kopírovaný text navyše agreguje per produkt+VEĽKOSŤ (dve veľkosti = dva riadky objednávky) a púšťa len `safeHttpUrl` odkaz; prázdne stĺpce sa VYNECHÁVAJÚ, nevypchávajú `—` (v maile by to čítal dodávateľ ako pokyn). Množstvo ber cez `orderQty(o)` — fallback na `1` presne ako zobrazenie riadku (`o.qty || '1'`), inak riadok „1 ks" ráta ako 0. **Tlačidlo nad živo zúženou množinou ošetri aj pre PRÁZDNU množinu:** skupina, ktorej sú všetky riadky vybavené, ostáva pri vypnutom filtri #205 aj s tlačidlom na obrazovke a kopírovala hlavičku „Objednávka — X (0 položiek)" s hláškou „✓ Skopírované" — objednávka na nič. Nekopíruj nič a povedz to („Nič na objednanie"). **Každý výsledok tlačidla si drží VLASTNÝ 2,5 s timer** — `let t = 0; clearTimeout(t); t = setTimeout(...)` per tlačidlo (jeden closure na skupinu), nie holý `setTimeout`: druhý klik v okne inak zdedí zvyšok PRVÉHO timera a varovanie „⚠️ Schránka nedostupná" zhasne po ~200 ms — manažér prečíta default label, myslí si, že sa skopírovalo, a vlepí do mailu PREDOŠLÚ objednávku, ktorá ostala v schránke — a „jeden closure na skupinu" drží druhá polovica toho istého testu (`test_a_later_copy_outcome_survives_the_earlier_click_timer`: kópia ORBIS-u, o sekundu kópia CITRADE-u; vytiahni `let resetT = 0` nad `for (const sup …)` a ORBIS ostane na „✓ Skopírované" navždy → test padne).

**E2E schránky: `page.add_init_script` + `Object.defineProperty(navigator, 'clipboard', …)` spy, NIE permission grant.** `navigator.clipboard` je read-only getter (holé priradenie ticho zlyhá). Spy zbiera do `window.__copied` a vracia resolved promise, takže sa asertuje PRESNÝ text, ktorý appka odovzdáva — deterministicky aj v headless CI, bez `grant_permissions`. Produkčný kód má fallback na `execCommand('copy')` (nezabezpečený kontext / odmietnutá permission), aby tlačidlo nikdy len nepovedalo „nedá sa".

**Fallback vetvu MUSÍ niečo spúšťať, inak je to len próza (revízia PR #236).** Spy, ktorý `writeText` VŽDY resolvuje, nechá `execCommand` vetvu aj jej label „⚠️ Schránka nedostupná" navždy nespustené — a playbook medzitým tvrdí, že fallback existuje. Maj tri varianty spy-a: (1) resolving, (2) `writeText` rejectne + `document.execCommand` je stub, ktorý vráti `true` a text si prečíta z `document.activeElement.value` (tak sa overí, že sa naozaj preniesol), (3) oboje zlyhá + `execCommand` HODÍ výnimku. Varianty (2)/(3) zároveň pinujú dve veci, ktoré scratch `<textarea>` rozbíja: **`ta.remove()` patrí do `finally`** (vyhodená výnimka inak nechá na `<body>` osirelý, tabom fokusovateľný textarea s celým textom objednávky, jeden na každý klik → asertuj `document.querySelectorAll('body > textarea').length === 0`; komentárové editory sú v `#list`, takže do tohto selektora nespadnú) a **`ta.select()` kradne karet** — pred vložením si odlož `document.activeElement` + `selectionStart/End` a v `finally` ich vráť (`document.contains(prev)`, oboje v `try`, lebo `selectionStart` na niektorých typoch inputu hádže). Klikaj v tomto teste cez `element.click()` v `page.evaluate`, nie myšou — reálny klik fokus presunie na tlačidlo a stratu karetu by si nezmeral.

**`#empty` a `#list` sú ZDIEĽANÉ s revíznou záložkou — text v nich sa musí VŽDY vracať na default (#205 follow-up).** Prázdny zoznam, lebo je všetko vybavené, je ÚSPECH, nie chyba načítania: pri zapnutom filtri + `ORDERS.length && !shown.length` píš „Všetko vybavené — poriešené riadky sú skryté". Ale rovnaký box používa revízia, takže ho nastavuj cez `setEmptyText(text|null)`, ktorý si default zapamätá z template pri prvom volaní, a v `render()` revíznej vetvy volaj `setEmptyText(null)` — inak si manažér prenesie hlášku z objednávok do párovania. To isté platí pre KAŽDÝ ďalší zdieľaný prvok top baru. **A úspešnú hlášku píš v ROZSAHU toho, na čo sa manažér pozerá (revízia pass 2):** „Všetko vybavené“ je tvrdenie o CELOM dni, takže smie ísť len pri „Všetci“; pri vybranom chipe sú ostatní dodávatelia mimo POHĽADU, nie hotoví (hlásilo to hotový deň nad 5 nevybavenými riadkami) → „Tento dodávateľ je vybavený…“. Rovnaké pravidlo ako pri #208 súhrne; drž to v čistej funkcii (`toOrderEmptyText(hidden, total, shown, supplier)`), nech sa dá otestovať aj bez UI.

**Súhrn nad zoznamom rátaj v ROZSAHU vybraného chipu (#208 follow-up).** Globálne „Ostáva vybaviť 7 z 7" nad dvoma viditeľnými ORBIS riadkami je číslo, s ktorým manažér nevie nič urobiť. `renderOrderToolbar(canon)` dostane kanonické písanie z `renderOrderFilters` a pri vybranom chipe počíta len jeho riadky + pomenuje ho („📋 ORBIS: ostáva vybaviť 2 položky z 3"); „Všetci" ostáva pri pôvodnom globálnom texte. Meno dodávateľa je voľný text → do `textContent`, nikdy do `innerHTML`.

**Počty v slovenčine sklonuj — `itemsWord(n, acc)` (#207/#208 follow-up).** 1 → položka (v akuzatíve po „vybaviť" → položku), 2–4 → položky, 0 a 5+ → položiek. Hlavička kopírovanej objednávky ide DODÁVATEĽOVI DO MAILU, takže „(1 položiek)" nie je kozmetika. Kvôli akuzatívu má súhrn číslovku pred podstatným menom („vybaviť 5 položiek z 7"), nie za ním — pri zmene textu súhrnu prehľadaj `tests/e2e` na „Ostáva vybaviť", čaká naň niekoľko `wait_for_function`.

**Playwright pasce, ktoré tu stáli cyklus (revízia PR #236):** `page.wait_for_function(expr, arg)` chce v Pythone `arg=` ako KEYWORD (inak `TypeError: takes 2 positional arguments`); `add_init_script` spy sa po `page.reload()` NAINŠTALUJE ZNOVA, takže `window.__copied` je prázdne — buffer čisti pred každým meraním, needexuj cez reload; `[...].map(itemsWord)` pošle INDEX poľa do druhého parametra helpera (`.map(n => itemsWord(n))`); a stĺpce kopírovaného riadku sa pri prázdnych hodnotách VYNECHÁVAJÚ, takže „N ks" hľadaj regexom, nie fixným indexom. Kontrast farby merај v teste voči REÁLNE vykreslenému pozadiu (vyjdi hore po `parentElement`, kým nie je `backgroundColor` priehľadná) a asertuj pomer ≥ 4.5, nie len hex — pravidlo tak prežije aj zmenu palety. **A NAJDRAHŠIA pasca (pass 2): `page.reload()` medzi mutujúcim klikom a asertujúcim klikom ZAKRYJE celú triedu bugov.** Oba testy invariantu „obrazovka == schránka“ mali medzi klikom na príznak a klikom na kopírovanie reload — takže okno, v ktorom je prvok v `#list` zastaraný (toggle ho zámerne neprekresľuje), sa nikdy nevykonalo a kritický bug prešiel zeleným testom. Keď testuješ prvok, ktorý sa má aktualizovať BEZ prekreslenia, klikaj v JEDNEJ session bez reloadu; reload patrí len do testu perzistencie. **Pass 3 pridala dve lacnejšie, ale opakujúce sa:** (1) text toolbaru je SKLONOVANÝ (`itemsWord`: „1 položku" / „2-4 položky" / „5+ položiek"), takže pomocník, ktorý z neho ťahá číslo, musí regexom skončiť PRED koncovkou (`stáva vybaviť (\d+) polož`) — inak test zelený pre 5 padne pre 1; a `#toToolbar` obsahuje aj tlačidlo „skryť poriešené", takže čítaj `.to-sum`, nie celý bar. (2) Zámerne vstreknutá chyba (`route.fulfill(status=500)`) sa objaví v `_console_watch` ako `[error] Failed to load resource: ... 500` — prehliadač loguje každý neúspešný request. Kontrolu konzoly NEVYPÍNAJ: asertuj čistú konzolu PRED vstreknutím a po ňom povoľ presne tú jednu vstreknutú hlášku (`[m for m in console if "500 (Internal Server Error)" not in m] == []`), nech appka aj tak nesmie na rollback ceste pridať nič vlastné.

**Zoskupovanie podľa VOĽNÉHO TEXTU (meno dodávateľa) rob cez normalizovaný kľúč, ale ZOBRAZUJ pôvodné písanie (#203).** `effSup(o)` je voľný text, ktorý manažér píše ručne → ten istý dodávateľ príde raz `CITRADE`, raz `Citrade`, raz `Citrade  s.r.o.`; surový string ako grupovací kľúč = N chipov s vlastnými počtami a protichodnými farbami. Vzor: `supKey(s)` = `trim` + `replace(/\s+/g,' ')` + `toLocaleLowerCase('sk')` (slovenské locale kvôli diakritike), grupuj/farbi/filtruj podľa neho, a **zobraz kanonický variant = najčastejšie použité písanie** (`supCanonPick`, zhoda počtov → abecedne, inak label bliká medzi rendermi). Datalist `known-suppliers` deduplikuj rovnakým kľúčom — existuje presne na to, aby fragmentácia nevznikala. Tri veci, na ktorých to inak padne:
- **Kľúč filtra PREFIXUJ** (`'s:' + supKey(...)`) — sentinel `'all'` („Všetci") je legitímny výstup `supKey` pre dodávateľa menom „All"/„ALL"; po case-foldingu je kolízia pravdepodobnejšia než predtým a klik na jeho chip by prepol filter na „všetko". `localStorage('orderSupplier')` zo staršieho buildu (surové meno) zmigruj pri načítaní, inak manažérovi po nasadení zmizne vybraný dodávateľ.
- **Zoraďuj a porovnávaj podľa ZOBRAZENEJ menovky** (`canon[k]`), nie podľa kľúča — inak sa abecedný tie-break riadi lowercase kľúčom a poradie skupín sa nečakane preusporiada.
- **NEnormalizuj case pri ZÁPISE.** `assignedSupplier` ide doslovne do `import_suppliers.csv` → Shoptet stĺpec `supplier`, takže lowercase by prepísal reálne meno dodávateľa v eshope. Na zápise rob LEN whitespace collapse (`" ".join(s.split())` v `/api/order-supplier`) — to je pre eshop bezpečné a rieši druhý reálny zdroj rozdvojenia. Case-insensitivita je DISPLAY vec a patrí do `app.js`.

**Zlyhaný zápis MUSÍ byť vidieť — a optimistický príznak sa MUSÍ vrátiť späť (#214).** Vzor `postToOrder(path, payload)` → vráti `''` alebo krátky dôvod (`'chyba 500'` / `'server neodpovedal'`), **hlási až VOLAJÚCI** cez `toOrderSaveFailed(what, err, where)`. Prečo nie hlásenie priamo v posielacej funkcii: `alert()` blokuje, takže volajúci, ktorý zmenil UI optimisticky, musí **najprv vrátiť stav a prekresliť** a až potom hlásiť — inak manažér číta „nepodarilo sa uložiť" nad riadkom, ktorý stále svieti ako uložený. Štyri per-riadkové príznaky zdieľajú `saveOrderFlag(path, field, map, key, on, what)` (optimistický zápis → POST → rollback + `renderToOrder()` + hláška). Holé `if (!r.ok) return;` je tu horšie než chyba: mapa `ORDERED/WAITING/…` je už zmutovaná, takže tab ukazuje príznak, ktorý server nikdy neuložil (tichá strata manažérovej práce do najbližšieho reloadu). E2E sa to testuje **Playwright request-interception** — `page.route("**/api/instock", lambda r: r.fulfill(status=500, …))` pre odmietnutý zápis, `route.abort()` pre mŕtvu sieť; `alert` sa chytá `page.on("dialog", …)` + `d.accept()`.

**Optimistický rollback MUSÍ mať sekvenciu per (príznak, riadok) — snapshot v premennej je BUG (revízia PR #233).** `saveOrderFlag` si pôvodne pri KAŽDOM volaní odložil `was = !!map[key]` a pri zlyhaní ho vrátil. Obyčajný DVOJKLIK na „✓ Skladom" počas výpadku ale spustí DVA zápisy pre ten istý riadok, a snapshot toho druhého je OPTIMISTICKÁ hodnota, ktorú práve zapísal prvý → posledná odpoveď vráti príznak, ktorý server výslovne odmietol. Manažér dostane „nepodarilo sa uložiť" a pozerá na riadok (a ČERVENÝ „všetko vybavené" chip), ktorý tvrdí opak — presne tá tichá strata, kvôli ktorej #214 vzniklo. Vzor `_flagWrites` `{wk, seq, confirmed, confirmedSeq}` kľúčovaný **`field + NUL + key`** (nie len `key` — „čaká sa" a „skladom" sú nezávislé zápisy na tom istom riadku a nesmú sa prebíjať). ŠTYRI veci, bez ktorých to nefunguje — každú kryje vlastný test a každá bola overená mutantom:
- **`confirmed` = posledná hodnota, ktorú PRIJAL SERVER**, nie snapshot mapy. Bez toho odmietnutý DRUHÝ zápis zhodí z tabu PRIJATÝ prvý (`test_a_refused_second_write_rolls_back_to_what_the_server_ACCEPTED`).
- **Záznam sa NIKDY nemaže** (ohraničený počtom riadkov × 4). Mazanie pri „usadení" otvára GENERAČNÚ dieru: oneskorenec z predošlej dávky pristane na zázname, ktorý medzitým vytvoril NOVÝ klik, a otrávi jeho baseline → fantómový príznak je späť. Pravidlo pinuje `test_a_straggler_from_an_older_burst_cannot_poison_a_later_click`, ale AŽ odkedy asertuje `Object.keys(_flagWrites).length` PO usadení dávky — jeho pôvodné asserty prežili delete-on-settle mutanta bez škrtnutia (`live` identity check straggler-a zneškodní sám), takže „nikdy nemaže" bola nepinnutá próza. **Keď v playbooku vyhlásiš pravidlo za load-bearing, over ho MUTANTOM** — a keď mutant neprejde červeným, buď dopíš assert, alebo pravidlo z playbooku škrtni; próza, ktorú nič nedrží, je horšia než žiadna.
- **`confirmedSeq`**: prijatie sa zapíše LEN keď žiadny NESKÔR vydaný zápis už prijatý nebol — inak zastaraná úspešná odpoveď prepíše novšiu pravdu na tom istom (nemazanom) zázname.
- **`live` = kontrola IDENTITY záznamu** (`_flagWrites[st.wk] === st`) — `loadOrders()` mapy prepíše serverovým stavom a `_flagWrites` vyčistí, takže letiaci zápis nesmie rollbackovať ČERSTVÉ dáta. Vlastníctvo drž na `seq !== st.seq`, nie na identite objektu.
- **Po USADENÍ poslednej letiacej odpovede ZOSÚLAĎ mapu s `confirmed` (`_reconcileFlag`, revízia PR #233 pass 2).** Pôvodne prehlásený („preusporiadanie odpovedí je klientsky neriešiteľné") zvyšok bol v skutočnosti REGRESIA, ktorú zaviedol samotný sekvenčný guard: superseded zápis sa vracal NASLEPO (`return !err`), takže PRIJATÝ zápis, ktorého úspech pristál AŽ PO tom, čo novší zápis už stihol rollback, sa ticho stratil — manažér čítal „nepodarilo sa uložiť", riadok ukazoval príznak VYPNUTÝ a server ho držal ZAPNUTÝ (zrkadlový #214, pre-fix kód tento vstup zvládal správne). Refused-fast + accepted-slow je presne tvar čiastočného výpadku, na ktorý je celé toto PR: 500/401 odpovie okamžite, reálny zápis stojí za `with _lock:` + atomickým replace. Vzor: `st.inflight` počítadlo per (príznak, riadok), dekrement v `finally` (uniknutý counter by pre ten riadok vypol zosúlaďovanie NATRVALO — záznam sa nikdy nemaže, takže by sa už na 0 nedostal); keď ho odpoveď zrazí na 0 a `!!map[key] !== st.confirmed`, prepíš mapu z `confirmed` + `renderToOrder()`. Zosúlaďuj LEN pri usadení POSLEDNEJ letiacej odpovede — skôr by si prebíjal optimistickú hodnotu zápisu, ktorý ešte letí. `confirmed` drží najvyšší-seq ÚSPEŠNÝ zápis a pri samých zlyhaniach ostáva pôvodný baseline, takže je správnym cieľom v OBOCH smeroch. Test drž s odpoveďami pustenými v OPAČNOM poradí (`test_an_accepted_write_survives_a_refusal_that_answers_FIRST`) — test, čo pinuje len jedno poradie pustenia, druhé mlčky nepokrýva.

**Zápis MIMO `saveOrderFlag`, čo mení tie isté riadky, MUSÍ vstúpiť do tej istej evidencie — a nárokovať si `seq` pri VYDANÍ, nie pri odpovedi (revízia PR #233, finálny verdikt).** Hromadné „označiť skupinu objednané" (`markGroupOrdered`) šlo najprv mimo evidencie (pomalý per-riadkový toggle, čo zlyhal AŽ POTOM, vrátil riadok na neobjednaný a vyhlásil chybu, hoci server ho drží objednaný). Prvý fix ho ale prihlásil `noteFlagConfirmed`-om AŽ PO úspechu — tým prebil aj zápisy, ktoré manažér vydal AŽ POTOM, čo klikol hromadné (jeho NOVŠIA vôľa, a na serveri sa commitnú NESKÔR, lebo stoja za `with _lock:` toho hromadného). Dvojklik na riadok počas letu hromadného zápisu tak skončil ticho OBRÁTENE: klient nakreslil riadok objednaný, `_reconcileFlag` mu pri usadení dorovnal mapu na hromadnú hodnotu a server držal opak — bez jedinej hlášky. **Vzor: `const sts = keys.map(k => _flagEntry(...)); const seqs = sts.map(st => ++st.seq); st.inflight += 1` PRED POSTom, dekrement v `finally`** — presne ako `saveOrderFlag`; po odpovedi per kľúč: crown (`seq >= confirmedSeq → confirmed`), a zápis do mapy LEN keď `seq === st.seq` (inak riadok vlastní novší zápis a pri `inflight === 0` sa mapa dorovná na `confirmed`). `noteFlagConfirmed` je preto ZRUŠENÝ — celý jeho kontrakt bol ten response-time nárok. **NEriešiť to „snapshotom `seq` pred POSTom + preskočením riadku, čo sa medzitým pohol"** — vyzerá to ako menší zásah, ale zahodí PRIJATÝ hromadný zápis: keď oba neskoršie per-riadkové zápisy zlyhajú, rollback pristane na PRED-hromadnom baseline a klient sa rozíde so serverom v opačnom smere (pinuje `test_a_bulk_that_LANDED_still_owns_the_row_when_the_later_writes_all_fail`). Obidva smery testuj — `..._issued_DURING_the_bulk_flight_is_not_inverted` (novšie klik y vyhrávajú) AJ ten druhý; test, čo pinuje len jeden smer, druhý mlčky nepokrýva.

**A zápis, čo mapu mutuje LEN pri ÚSPECHU, MUSÍ zosúlaďovať aj na CHYBOVEJ ceste — inak po sebe nechá odmietnutú optimistickú hodnotu predchodcu (revízia PR #233 pass 5).** `markGroupOrdered` mal na vlastníckej vetve holé `if (err) return;`. Každý `saveOrderFlag` zápis mapu mutuje pri VYDANÍ, takže vlastná hodnota vždy prepíše zvyšok predchodcu a chybová cesta rolluje na `confirmed` — hromadný zápis píše mapu LEN po úspechu, takže odmietnutý hromadný je JEDINÝ vlastník, čo ani nezapísal, ani nemá čo vrátiť. Keď sa navyše usádza POSLEDNÝ, prebitý per-riadkový zápis už svoj reconcile preskočil (vtedy `inflight !== 0`) → pre ten záznam sa už NIKDY nič nespustí: riadok drží „objednané", ktoré server dvakrát odmietol, až do ručného reloadu (#214 fantóm, zrkadlený do hromadnej cesty). OPAČNÉ poradie usadenia sa uzdraví samo cez `_reconcileFlag` — preto okolo tejto diery prešli VŠETKY štyri smerové testy; ani jeden nedal odmietnutý per-riadkový zápis PRED odmietnutý hromadný na tom istom riadku. Pin: `test_a_refused_bulk_settling_LAST_clears_the_refused_per_row_value`. Pravidlo pre KAŽDÝ nový zápis na tomto tabe: keď mapu nemutuješ pri vydaní, chybová vetva musí robiť ten istý settle-reconcile (`inflight === 0 && !!map[key] !== st.confirmed`) ako vetva prebitá novším zápisom.

**Zápis, ktorého evidenciu zahodil `loadOrders()` (`!live`), NEMÁ čo vrátiť — ale STÁLE zlyhal (revízia PR #233, finálny verdikt).** `return !err` bez hlášky je presne tá tichá strata, kvôli ktorej #214 existuje. Hlás `toOrderSaveFailed` na `!live && err`; zápis prebitý NOVŠÍM zápisom na tom istom riadku ostáva ticho zámerne (hlási ten novší, ktorý riadok vlastní). A `loadOrders()` samotný **čisti `_flagWrites` v TOM ISTOM synchrónnom bloku, v ktorom priraďuje nové mapy** (šesť fetchov paralelne cez `Promise.all`, potom wipe + priradenie) — pôvodne čistil PÄŤ awaitov pred výmenou máp, takže klik v tom okne si zasadil baseline z ešte-optimistickej hodnoty a naviazal zápis na mapu, ktorú reload o chvíľu zahodil (rollback do prázdna). Nový store v `loadOrders` pridávaj do toho `Promise.all` + do toho istého bloku, nie za ďalší `await`.

**E2E race testuj PODRŽANÍM `fetch`, nie route stubom** — stub odpovie príliš rýchlo na skutočný súbeh. `_hold(path)` v `test_order_save_errors.py`: `window.__held` (uvoľni `__held[i](status, passthrough)`), `window.__settled` (deterministický signál „odpoveď doručená" namiesto `wait_for_timeout`), `__realFetch` na čítanie reálneho stavu servera. **Path matchuj cez `new URL(url).pathname === path`, NIE `indexOf`** — `/api/ordered` inak podrží aj `/api/ordered/bulk` a test ticho meria niečo iné. `(200, true)` prepustí request na reálny server, `(200)` úspech len predstiera (izolovaná klientska evidencia). A pozor na wait, čo nič nedokazuje: `.toorder-row.done` prepína klik handler SYNCHRÓNNE, takže po hromadnom zápise čakaj na prekreslený label tlačidla („Zrušiť objednané"), nie na triedu riadku.

**Chybová hláška sa dedupuje per ZÁPIS, nie per PROZA — a nesie identitu riadku (revízia PR #233).** Dedup kľúč `what + NUL + where + NUL + detail + NUL + value` (`value` sa manažérovi NEzobrazuje, je len v kľúči — bez neho sa zhltne OPRAVA: prepíše odmietnutú URL, uloží znova v tom istom okne a nedostane žiadnu spätnú väzbu; 5 s okno, mapa nie jeden slot, `Object.create(null)` — `where` nesie voľný text manažéra). Kľúčovať na samotnú prozu je bug: prechádzať dodávateľskú skupinu DOLE je zmysel tohto tabu, takže 3-5 riadkov, čo zlyhá v priebehu pár sekúnd, je NORMÁLNY tvar čiastočného výpadku — nie opakovanie jednej udalosti; hláška bez identity riadku bola pre všetky riadky bajtovo rovnaká, takže manažér sa dozvedel o jednom a zvyšné príznaky sa mu ticho vrátili späť. `where` píš po ľudsky (`toOrderRowLabel(key)` → „obj. 20260910, kód C1", `'kód ' + itemCode`, `'obj. ' + orderCode`, `'skupina ' + effSup(...)`). Skutočný dedup (ten istý odmietnutý toggle klikne znova) ostáva — `alert()` blokuje vlákno.

**`renderToOrder()` je CELOTABOVÝ repaint — NESMIE zožrať rozpísaný text (revízia PR #233).** Volá ho rollback zlyhaného príznaku AJ (od #204) ÚSPEŠNÝ `savePairUrl`/`saveSupplier`/`saveOrderComment`, takže aj šťastná cesta ticho zahodila, čo mal manažér rozpísané v ĽUBOVOĽNOM otvorenom inline editore na ĽUBOVOĽNOM riadku (a hláška hovorila len o príznaku, takže strata bola neviditeľná). Vzor: každý editor si označí wrapper `dataset.editor = 'pair'|'supplier'|'comment'`, `captureOpenEditors()` pred `list.innerHTML=''` odloží `{kind, key, value, opened, focused, sel}` (čítanie `selectionStart` obaľ `try//except` — beží PRED wipe-om, takže výnimka by zhodila celý repaint aj s rollbackom) a `restoreOpenEditors()` po dostavaní zoznamu editor znova otvorí a doplní. DVE podmienky, bez ktorých to rozbije existujúce správanie: (1) **prázdny box preskoč — okrem editora, ktorý manažér SÁM otvoril ✏️** (`dataset.editorOpened`, nastavuje ho `openRowEditor`; vyprázdnený otvorený editor je vedomé zmazanie poznámky, nie prázdny default) — pair/supplier editor sa na nenapárovanom/nepriradenom riadku renderuje prázdny BY DEFAULT, takže by sa prilepil prázdny input na súrodenca, ktorého práve napárovala per-produktová propagácia (#204); (2) **hodnotu zhodnú s uloženou preskoč** (supplier porovnávaj cez `normSupplierName` — endpoint whitespace-normalizuje) — inak sa práve uložený riadok vráti do edit módu namiesto odkazu/menovky a `wait_for_selector('.to-link')` timeoutne. **PORADIE tých dvoch podmienok je load-bearing (revízia PR #233 pass 2):** výnimku „manažér ho otvoril SÁM a je PRÁZDNY" vyhodnocuj PRED „zhodné s uloženým". Na riadku, kde ešte NIČ uložené nie je, sú hodnota AJ uložené oboje `''`, takže same-check spadol prvý a zavrel box manažérovi, ktorý doň práve išiel písať (💬 Komentár na riadku bez komentára). NEotáčaj to naplocho (`!s.opened && same(...)`) — tým by sa PRÁVE ULOŽENÝ ✏️ editor vrátil do edit módu namiesto odkazu; výnimka platí len pre `opened && !value.trim()`. **A tá výnimka si hneď pýta svoj protikus: ULOŽENIE musí nárok „otvoril som si ho sám" SPOTREBOVAŤ** (`commitEditor` → `dataset.editorSaving`, `captured.opened = editorOpened && !editorSaving`). Vyprázdnený ✏️ editor, ktorý manažér ULOŽIL (= vedomé zmazanie poznámky), má hodnotu AJ uložené oboje `''`, takže sa od „otvorený, ešte nič nenapísané" nedá odlíšiť inak než týmto markerom — bez neho sa editor po úspešnom uložení znovu otvorí a `restoreOpenEditors` mu nárok ZNOVA opečiatkuje, takže sa už nezavrie NIKDY. Neúspešné uloženie nárok vracia (text je stále jeho nezapísaná práca) — **a keď medzitým prebehol repaint, vracia ho MŔTVEMU uzlu, takže editor treba OTVORIŤ ZNOVA (`reopenDetachedEditor`, finálny verdikt PR #233)**: spotrebovaný nárok + PRÁZDNA hodnota = `captureOpenEditors` nemá čo prenášať, box po repainte zmizol, a hláška „nepodarilo sa uložiť" príde nad miesto, kde manažér nemá kam znova písať. Na zlyhaní preto testuj `!wrap.isConnected` a editor postav nanovo cez ten istý `_EDITORS` spec (kľúč riadku aj text si vytiahni z ODPOJENÉHO podstromu — je neporušený; nárok `editorOpened` sa vracia s ním). Nový inline editor na tomto tabe → pridaj mu `dataset.editor` + záznam do `_EDITORS` (`input`/`stored`/`same`/`open`; je `Object.create(null)`, lebo kľúč sa číta z DOM atribútu `data-editor`), inak jeho rozpísaný text repaint zožerie.

**`safeHttpUrl` vracia `''` — a `<a href="">` NIE JE mŕtvy odkaz, naviguje na SEBA (revízia PR #233 pass 2).** Sanitizácia schémy bola správna, ale volajúci okolo toho `''` ďalej staval `<a>`: klik = plný reload tabu, teda zahodenie KAŽDÉHO otvoreného editora aj s rozpísaným textom — presne tá práca, ktorú zvyšok tohto PR chráni. Odmietnutú hodnotu preto NErenderuj ako `<a>` vôbec: opraviteľné inline párovanie prepadni na vkladacie pole (manažér ju vidí aj opraví), read-only decision slot na inertný `<span class="to-link to-badlink">`. A NEecho-uj otrávenú hodnotu do `title` (bola tam ako tooltip). Platí pre KAŽDÝ nový href postavený z hodnoty zo store: `href` stav tri (odkaz / editor / inertný text), nie dva. **Inertnej náhrade NEDÁVAJ triedu odkazu** (`to-badlink`, NIE `to-link to-badlink`): `.to-link` je selektor, na ktorý sa viaže `_EDITORS.pair.open` aj pol tucta e2e asertov, a ne-anchor ticho započítaný ako odkaz je presne to, ako „žiadne mŕtve odkazy" prestane platiť — daj jej vlastný štýl a `_EDITORS` cieli `a.to-link`. (Bonus: tým odpadne aj tmavý-mód pasca, kde `body[data-theme=dark] .to-link` prebije plochý `.to-badlink` a inertný text svieti modrou ako živý odkaz.)

**Chip a datalist MUSIA vybrať to isté písanie (revízia PR #233).** `supplierSpellingIndex` ráta DVE tally: `grp` jeden hlas per RIADOK (`effSup` — vlastný dodávateľ objednávky vyhráva), `all` jeden hlas per STĹPEC (`supplier` AJ `assignedSupplier`). Zastarané priradenie tak vie prehlasovať chip: tab ukázal „CITRADE (4)", autocomplete ponúkol „Citrade" a manažér napísal to, čo mu dal datalist. `all` naďalej rozhoduje, KTORÉ mená sa ponúkajú (zatienené priradenie je reálne meno, ktoré použil) — ale písanie mena, ktoré MÁ chip, ber z `canon` (`canon['s:' + k] || supCanonPick(all[k])`).

**Trim maj na JEDNOM mieste — `hasOwnSupplier(o)` (revízia PR #233).** `effSup` trimoval oba stĺpce, riadková brána `if (!o.supplier)` nie → dodávateľ zo samých medzier by riadok zaradil pod '—', ale NEUKÁZAL by inline supplier-assign editor, teda jediné miesto, kde sa to dá opraviť. Dnes nedosiahnuteľné (`build_to_order_rows` `itemSupplier` stripuje), ale keď dva výrazy odpovedajú na tú istú otázku, musia zdieľať helper.

**Per-PRODUKT hodnota (`pairUrl`, `assignedSupplier`) sa po save propaguje na VŠETKY riadky toho `itemCode` — inak sa klient rozíde so serverom (#204).** `/api/orders` vracia `pairUrl` na každom riadku toho kódu, takže `savePairUrl`, ktorý updatol len kliknutý riadok (`row.replaceWith(...)`), nechal súrodencov s prázdnym vkladacím poľom pre produkt, ktorý JE spárovaný — manažér lepil URL znova pre každú objednávku. Vzor je rovnaký ako `saveSupplier`: `for (const x of ORDERS) if (x.itemCode === o.itemCode) x.pairUrl = url;` + `renderToOrder()`. Platí pre KAŽDÉ nové per-produktové pole; per-RIADKOVÉ (`ordered`/`waiting`) sa naopak propagovať NESMIE.

**Per-ORDER (nie per-line) store — vzor `order_comments` (#101):** keď hodnota patrí celej OBJEDNÁVKE (nie riadku), kľúč je **`<orderCode>`** (NIE `<orderCode>|<itemCode>`). `data/out/order_comments.json`, endpoint `/api/order-comment` (GET mapu / POST `{orderCode, comment}`, dĺžkový cap `ORDER_COMMENT_MAX`, prázdny = clear, login-gated). `/api/orders` doplní `comment` na KAŽDÝ riadok tej objednávky. Frontend: `ORDER_COMMENTS[o.orderCode]`, editor = **textarea** (nie 1-riadkový input; Ctrl/⌘+Enter uloží, plain Enter je nový riadok), po save volaj **`renderToOrder()`** (nie len replace riadku) — komentár je zdieľaný medzi VŠETKÝMI riadkami objednávky, presne ako per-produktový `assignedSupplier`. Voľný text ide cez `.textContent` (auto-escape, žiadny `escapeHtml`/`innerHTML`). Na riadku sa read-only zobrazuje aj Shoptet `shopRemark` (`build_to_order_rows` číta stĺpec exportu; `.to-shopnote`). Zápis komentára späť do Shoptetu → skill `shoptet` (order `shopRemark` write-back), odložené na follow-up.

Vstupy endpointov, čo píšu do CSV (kód/dodávateľ), MUSIA odmietnuť formula-injection: kód aj meno dodávateľa začínajúce `= + - @ \t \r` → 400; URL `^https?://`. **CSV sink prefixuje `'` cez `_csv_safe` — aj manuálny `/api/import` zip AJ nočný `upload-*` sink** (nočný píše naživo do eshopu, takže NESMIE byť slabšie chránený než zip).

**Každý endpoint, čo ukladá URL, potrebuje EŠTE TRI veci (revízia PR #255):** (1) **dĺžkový strop `URL_MAX` (2000)** — 300 000-znaková URL sa prijala a nafúkla `decisions.json` na 300 kB, pričom ten store sa re-číta pri KAŽDOM `/api/orders` a hodnota končí v Shoptet `internalNote` bunke; majú ho **VŠETKY** endpointy, čo URL ukladajú — `/api/decision`, `/api/order-pair`, `/api/order-decision-url`, `/api/variant-link` a `/api/search-pair`. (2) **`_log_safe()` pred zápisom do logu** — `^https?://` regex prepustí `https://x.test/a\r\nSet-Cookie: x`, takže surové `log.info(... url=%s)` vyrobí vlastný falošný log riadok (log-line forging); sanitizuj AJ **kľúč/kód** (`/api/decision` loguje manažérov `key` rovnako surovo). (3) **hlášky po SLOVENSKY** — `postToOrder` vypisuje `j.error` doslova do manažérovho alertu, takže „unknown review key" (dosiahnuteľné: zastaraný tab po resynci, ktorý produkt vypustil) mu prišlo v angličtine.
**A pri dopisovaní takej ochrany si VYGREPUJ VŠETKY endpointy toho tvaru naraz** (revízia
PR #255, druhá vlna): prvá vlna strop aj sanitizér doplnila na tri endpointy a `/api/
variant-link` + `/api/search-pair` nechala tak — pritom `variant_links` práve TOTO PR
zohrialo (`build_to_order_rows(..., _load_variant_links())` ho re-číta pri každom
`/api/orders`), takže 300 000-znaková URL nafúkla `variant_links.json` na 300 029 bajtov.
Pravidlo je „každý endpoint, čo URL ukladá", nie „ten, ktorý sme práve opravovali" —
a test drž ako SLUČKU cez zoznam endpointov, aby ďalší pribudol jedným riadkom.

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
   `PAGE_TITLES`, **`NAV_ICONS[key]`** (bez neho sa do `<svg>` interpoluje reťazec
   `undefined` a prehliadač ho VYKRESLÍ ako text vedľa názvu — #274; stráži
   `test_every_nav_key_has_an_icon`), cache-bust `?v=` bump. Backend NAV_KEYS + `AUTOMATION_DESCRIPTIONS`
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
BEZ zámku, (3) krátky `with _lock:` na finálny zápis + **re-check stavu**. Nájdené vlastnou
review-passou v #153.

**Re-check pri zápise ale NESTAČÍ — akcia, čo POSIELA MAIL, si musí odoslanie NÁROKOVAŤ PRED
SMTP (DÁVKA A, PR #223).** Re-check dedupne len ZÁPIS; dva súbežné klik-y oba prejdú
pre-checkom a OBA POŠLÚ mail (zákazník dostane dva). Vzor claim (`api_orders_reminder_override`):
pod prvým `_lock` zapíš tranzitný stav `{status:'sending', claimed_at, claim:<token>}` +
persistuj → druhý request vidí živý claim → **409**; po úspechu prepíš terminálnym stavom; na
KAŽDEJ zlyhanej ceste claim **uvoľni** (obnov pôvodný záznam) nech je retry možný. Claim MUSÍ
mať TTL (`SENDING_CLAIM_TTL_S`, 10 min) — bez neho pád/restart medzi claim-om a sendom objednávku
**natrvalo zamkne**. Helpery `_reminder_is_terminal` / `_reminder_claim_active` sú tolerantné k
smetiu (non-dict, naive/neparsovateľný `claimed_at`) a **fail-open** = re-claimovateľné, nikdy
mŕtvy zámok. Kto testuje claim, musí testovať AJ: expirovaný claim neblokuje ani `contact`
akciu, a zlyhaný send claim uvoľní.

**Claim MUSÍ byť SYMETRICKÝ — nárokuje si ho AJ dlhý BEH, nielen endpoint (PR #223 review).**
Jednostranný claim (len endpoint) nechráni pred ničím: `run_orders_reminder` spraví čerstvý
per-order read, **pustí `_lock`** a strávi ~20 s v OpenAI+SMTP **bez akéhokoľvek in-flight
markera na disku** → manuálny klik v tom okne prejde 409 bránou, claimne a pošle — a beh pošle
tiež (zákazník dostane dva maily). Beh preto nárokuje hneď PO claim/terminal checkoch a AŽ za
lacnými diskvalifikátormi (no-note / no-email / **no-BCC** / no-key — tie žiadny claim
nepotrebujú). **Claim je VLASTNÁ akvizícia `_lock` s VLASTNÝM re-readom záznamu vnútri**, nie
pokračovanie čerstvého per-order readu (ten zámok je už dávno pustený) — a práve to je správne:
stav sa re-checkne pod tým istým zámkom, ktorý claim zapisuje, takže medzi check a zápis sa nič
nevmestí. Čerstvý read vyššie len rozhoduje, či sa objednávkou vôbec oplatí zaoberať.
Šesť vecí, na ktorých to inak padne:
- **`claimed_at` ber ČERSTVO pri každom claime**, nie `now_iso` zo začiatku behu — dlhý beh by
  rozdával claimy, čo už vyzerajú expirované.
- **`claim` token je per-BEH** (`secrets.token_hex`) a release obnoví `prev` **len keď sa token
  na disku stále zhoduje** — inak by beh prepísal terminálny záznam súbežného override-u.
- **Uvoľni na KAŽDEJ ceste bez výsledku** (zlyhaná klasifikácia AJ zlyhaný send), inak je
  objednávka zamknutá do vypršania TTL.
- **Claim, ktorý sa NEPODARILO zapísať, nechráni nič** → objednávku PRESKOČ (retry ďalší beh),
  neposielaj ju „nenárokovanú"; a zápis claimu obaľ `try/except`, inak plný disk zhodí celý beh.
- **Nezapísaný claim je CHYBA, nie obyčajný skip (B1 M1)** — „niekto iný to vzal" (legitímne)
  a „zápis zlyhal" (plný disk) NESMÚ zdieľať návratovú hodnotu. `_claim` vracia `None` pre prvé
  a sentinel `_CLAIM_WRITE_FAILED` pre druhé; volajúci na sentineli robí `errors += 1`. Bez toho
  beh, ktorému zlyhali VŠETKY claimy, neposlal nikomu nič a hlásil `emailed_now: 0, errors: 0` —
  na tabe nerozoznateľné od pokojného dňa (presne tá „ticho mŕtva automatizácia", proti ktorej
  je `bcc_missing`/`· chyby: N`).
- **Každá vetva, čo objednávku vzdá uprostred, ju MUSÍ pridať do display listu (B1 M2)** —
  display listy sa stavajú od nuly každý beh a `continue` znamená, že riadok na ten beh z tabu
  ZMIZNE, a override endpoint (hľadá len v `red`/`orange`/`skipped`) na neho vráti **404
  „objednávka sa v aktuálnom zozname nenašla"**. Zbieraj ich do `pending` a pri finálnom save
  ich cez `_relocate` prilej do `skipped` (ten riadok nesie poznámku AJ „▶ Poslať pripomienku");
  každý riadok nesie pole `pending` = dôvod, ktorý appka vypíše. Týka sa: stratený claim race,
  nezapísaný claim, `_release` po zlyhanej klasifikácii/sende, chýbajúce `MAIL_BCC`, chýbajúci
  `OPENAI_API_KEY`. **`pending` MUSÍŠ zmazať vo VŠETKÝCH TROCH cestách, ktorými sa objednávka
  vyrieši** — `_relocate` (kopíruje riadok, dnes už filtrovanou comprehension),
  inkrementálna rýchla cesta (`dict(prev_row)` + `row.pop("pending", None)`)
  **A ručný override endpoint** (`api_orders_reminder_override` mal `append({**row, …})` na OBOCH
  miestach — `skipped` pri `contact` aj `orange` pri `send`; teraz `row_done`). Inak varovanie
  „automat to nestihol" visí na vybavenom riadku a nafukuje počítadlo nedokončených až do
  ďalšieho behu (≤24 h) — override je práve tá cesta, kde ho manažér ručne odstraňuje, takže
  tam bolo najviditeľnejšie (nálezy revízie PR #224, dva samostatné fixy). Pri PRIDANÍ ďalšej
  cesty, ktorá objednávku uzatvára, strippni `pending` hneď — je to per-BEH pole, nie stav. A tieto
  `pending` riadky renderuj vo VLASTNEJ sekcii, nie pod hlavičkou „AI usúdilo, že zákazník je
  už kontaktovaný" — pri chýbajúcom `MAIL_BCC` AI vôbec nebežala, takže by hlavička tvrdila
  niečo, čo sa nestalo.

**`manual` je PRESNÝ OPAK `pending` — vlastnosť ZÁZNAMU, ktorú re-derivuješ, nie per-beh
poznámka, ktorú strippuješ (#227).** Override zapisoval `manual: True` do záznamu, ale nie do
DISPLAY riadku, takže tab tvrdil „⚪ AI usúdilo…" aj o riadkoch, kde klasifikátor NIKDY nebežal
(bez poznámky / bez `OPENAI_API_KEY` / bez `MAIL_BCC`). Fix je jeden zdieľaný `_mark_manual(row,
entry)` volaný vo VŠETKÝCH ŠTYROCH cestách, čo riadok vyrábajú: override endpoint, terminálna
vetva behu, inkrementálna rýchla cesta, `_relocate`. **Re-derivuj z aktuálneho záznamu, nekopíruj
z predošlého riadku** — tým je výsledok správny bez ohľadu na cestu a sám sa opraví aj na riadku
prenesenom z čias, keď pole ešte neexistovalo. Tab potom delí `skipped` na TRI sekcie (AI /
`✋ ručne` / `⚠️ nedokončené`); filtre musia byť disjunktné (`!pending && !manual`, `!pending &&
manual`, `pending`), inak riadok zmizne alebo sa zdvojí. Pri PRIDANÍ ďalšieho takého príznaku sa
najprv rozhodni, či je per-BEH (strippuj) alebo vlastnosť ZÁZNAMU (re-derivuj).

### Náhľad zákazníckeho e-mailu — VLASTNÝ read-only endpoint + VLASTNÝ modal bez „Odoslať" (#217)

Manažér musí vidieť, čo zákazník dostane, PRED odoslaním. Vzor pre každú mailovú automatizáciu:

- `GET|POST /api/<key>/preview` — vráti `{subject, html, recipient}` z **TÝCH ISTÝCH builderov**,
  ktoré použije beh aj ručné odoslanie (`posta_uncollected.build_email` /
  `orders_reminder.build_reminder_email`), kŕmených reálnymi hodnotami z aktuálneho display
  riadku. NIKDY nekopíruj šablónu do endpointu — dvojička sa rozíde s tým, čo sa naozaj posiela.
- **Inertný BY CONSTRUCTION**: žiadny claim, žiadny zápis, žiadne SMTP, žiadne volanie Pošta API.
  Test to LOCKNE porovnaním BAJTOV store súboru pred/po + `_send_mail_html` nevolané.
- Eskalačný náhľad ukazuje NASLEDUJÚCI mail (`už_odoslané + 1`); po vyčerpaní kadencie
  (`>= MAX_EMAILS`) vráť posledný odoslaný + `max_reached: True` — vymyslený „5. mail", ktorý
  automat nikdy nepošle, je klamstvo.
- Frontend: `openEmailPreview(url, payload, head)` + **SAMOSTATNÝ `#emModal`**, ktorý zámerne
  NEMÁ tlačidlo Odoslať (recykluje `.nd-*` CSS). `#ndModal` (#100) ostáva preview+send; miešať
  ich by znamenalo, že pozeranie sa dá odklikať na odoslanie. E2E smie modal otvárať bezpečne.
- E2E gotcha: `automations_server` je **function-scoped** (fresh store pre KAŽDÝ test) — testy
  nemusia po sebe upratovať ani závisieť od poradia. A `page.locator(".warnhead", has_text="ručne")`
  padne na strict-mode, keď dve hlavičky zdieľajú slovo — cieľ celou frázou.

**GOTCHA — tranzitný claim NIE JE mitigácia zlyhaného post-send zápisu (PR #223 review).**
Nechať claim po zlyhaní zápisu (aby manažér neklikol znova) kupuje len `SENDING_CLAIM_TTL_S`;
potom `sending` nie je ani aktívny ani terminálny → nočný beh objednávku považuje za
nespracovanú a pošle DRUHÝ mail. Zapíš **NEEXPIRUJÚCI terminálny marker** (`{status:'emailed',
persist_failed:True}`) vlastnou minimálnou transakciou (bez display-list práce — tá zlyhala
pravdepodobne ako prvá), vo vlastnom `try/except`. Ak zlyhá aj ten, nič nie je horšie.

**GOTCHA — dlhý beh, čo si na ZAČIATKU spraví snapshot stavu, ho NESMIE na konci zapísať
wholesale — a NESMIE z neho ani ČÍTAŤ rozhodnutia (lost update, OBE strany).** `run_orders_reminder`
strávi minúty v OpenAI+SMTP; manažér medzitým cez tab zapíše override. **Zápis:** finálny
`_save_*` musí znovu načítať `orders` z disku (priebežné `_persist_done` tam už zapísalo) a
prepísať LEN display polia. **Čítanie (na toto sa zabudlo pri prvom fixe — našla adversariálna
review):** per-objednávkové rozhodnutie „už je vybavená / práve sa posiela" sa musí čítať
`_load_*()` **čerstvo pod `_lock` tesne pred rozhodnutím**, nie zo štartového snapshotu — inak
beh pošle mail zákazníkovi, ktorého manažér pred 2 minútami vybavil. Platí pre KAŽDÝ dlhý beh
so súbežne editovateľným storom.

**A ten „čítaj z disku" fix si vypýta ĎALŠIE DVE veci (PR #223 review) — bez nich lieči jeden bug
a otvorí druhý:**
- **Zlyhaný priebežný zápis sa finálnym save-om ZARUČENE ZAHODÍ.** Keď `orders` berieš z čerstvého
  disk readu, in-memory záznam z `_persist_done`, ktorého zápis padol, tam nikdy nebude → mail
  odišiel, stav sa stratil, ďalší beh pošle znova (`emailed_now: 1`, ale store ten kód
  neobsahuje). Zbieraj kódy zlyhaných zápisov (`failed_writes`) a vo finálnom save ich **re-apply
  NA VRCH** disk mapy — ale LEN keď záznam na disku nie je terminálny (súbežný override, čo
  medzitým dospel k verdiktu, musí vyhrať; tým ostáva lost-update fix zachovaný).
- **DISPLAY listy počítané zo štartového snapshotu a zapísané wholesale vracajú vyriešený riadok
  na tab.** Objednávka vybavená cez override počas behu sa vráti do `red`/`no_email` a manažérov
  ďalší klik dostane nevysvetliteľné 409. Fix má DVE polovice: (1) čerstvý per-order read patrí
  **NAD** vetvu `if not o["has_note"]` (nad KAŽDÚ display vetvu), (2) tesne pred finálnym save-om
  prefiltruj `red`/`no_email` proti čerstvej `orders` mape a terminálne riadky **presuň** do
  `orange`/`skipped` (nielen zahoď — inak riadok z tabu úplne zmizne).

**GOTCHA — `smtplib` má DVE pasce, kvôli ktorým helper hlási zlý výsledok; obe rieši zdieľaný
`_smtp_deliver()` (nová mailová cesta MUSÍ ísť cezeň, nekopíruj connect/send/quit).**
(1) Mail je **DORUČENÝ v momente, keď `sendmail()` vráti** — `quit()`, čo potom vyhodí výnimku
(server zhodí spojenie po DATA), sa NESMIE hlásiť ako zlyhanie: volajúci by nezapísal dedup stav
a ďalší beh pošle ten istý mail znova. (2) `sendmail()` vyhodí výnimku LEN keď sú odmietnutí
**VŠETCI** príjemcovia; pri čiastočnom odmietnutí vráti **dict** odmietnutých — odmietnutý
ZÁKAZNÍK musí byť zlyhanie (stav sa nebumpne → retry), odmietnuté len-BCC nesmie zahodiť fakt,
že zákazníkovi mail odišiel. Porovnanie adries case-insensitive + strip.
(3) `quit()` sa dosiahne LEN na úspešnej ceste — výnimka zo `starttls()`/`login()`/`sendmail()`
(alebo zo samotného `quit()`) nechala socket GC-čku; `_smtp_deliver` preto zatvára v `try/finally`
best-effort `close()`, keď sa `quit()` nedosiahol. Teardown NESMIE prebiť pôvodný výsledok
(doručený mail ostáva úspech).

**Automatizačný ZÁKAZNÍCKY mail posielaj `require_bcc=True`** (`_send_mail_html`): bez `MAIL_BCC`
sa NEodošle vôbec (retry) namiesto tichého odoslania bez kópie pre majiteľa. Reset hesla /
nedostupné / výstavy ostávajú best-effort (jednorazový warning). **Zápis stavu PO úspešnom
maili obaľ `try/except`** — a keď zlyhá, NEhlás manažérovi obyčajnú chybu (klikol by znova =
druhý mail): povedz explicitne „mail ODIŠIEL, neklikaj znova" + zaloguj kód objednávky. V behu
(nie endpointe) po zlyhanom zápise **nerob `continue`**, ktorý by riadok vyhodil z tabu — je to
presne ten riadok, čo má manažér skontrolovať.

**„Konfigurácia chýba" hlás ako KONFIGURÁCIU, nie ako zlyhané odoslanie, a UKÁŽ to v UI
(PR #223 review).** Generické 502 „odoslanie zlyhalo" manažér nerozozná od prechodného výpadku →
kliká donekonečna. Endpoint, čo posiela `require_bcc=True` mail, si chýbajúce `MAIL_BCC`
**pre-flightne PRED claimom** (`_mail_bcc() is None` → **503** + hláška „Chýba MAIL_BCC v
data/.mail_env — mail sa neodošle, doplň konfiguráciu"; žiadny premárnený claim). A obe
zákaznícke automatizácie vracajú v stats `bcc_missing`, z ktorého taby renderujú `.autoerr`
riadok (`bccMissingWarning()` v app.js) — bez toho je mŕtva automatizácia vidno LEN v logu
(`emails_sent: 0` vyzerá ako pokojný beh). **`_mail_bcc()` je JEDINÁ definícia „BCC je
nastavené"** — používajú ju všetky tri sendery, pre-flight aj `bcc_missing`; nová cesta MUSÍ ísť
cezeň (holý `os.environ.get("MAIL_BCC")` bez `.strip()` berie prázdny `MAIL_BCC=` riadok ako
nastavený → tab a sender by si protirečili).

**GOTCHA — DRAHÉ (platené) volanie až PO lacných diskvalifikátoroch.** `_classify_contacted`
(OpenAI) bežalo pred kontrolou `if not o['email']` — taká objednávka sa nikdy nestane terminálnou,
takže sa klasifikovala **každý beh donekonečna**. Pri každom novom AI/scrape kroku over poradie:
najprv všetko, čo vie položku vylúčiť zadarmo, až potom platené volanie.
**To isté platí pre KONFIGURAČNÝ diskvalifikátor, nielen pre dátový (B1 M3):** chýbajúce
`MAIL_BCC` beh počítal do `bcc_missing`, ale NEPOUŽIL ho ako bránu — claimol, zaplatil OpenAI, a
až `require_bcc` send odmietol; objednávka sa nikdy nestala terminálnou → to isté donekonečna.
Endpoint to pre-flightoval správne (503 pred claimom), beh nie — **keď jedna cesta pre-flightuje
konfiguráciu, druhá musí tiež**, inak sa asymetria prejaví ako tichý účet za OpenAI.

**Test-pasca — monkeypatch stub `_send_mail_html` MUSÍ mať `**kw`.** Stuby po celom test-suite
sú `lambda to, subject, body, bcc=None, **kw:` — bez `**kw` každé budúce rozšírenie signatúry
(`require_bcc`) zhodí cudzie testy `TypeError`-om. Kto chce testovať REÁLNU SMTP cestu spod
stubovaného `iso` fixture, uloží si `_REAL_SEND_MAIL_HTML = webapp._send_mail_html` na úrovni
MODULU (pred akýmkoľvek patchom) a monkeypatchne ho späť + `smtplib.SMTP`/`SMTP_SSL` na fake
(+ `MAIL_PORT=587`, inak reálny `.mail_env` s portom 465 obíde `SMTP` fake cez `SMTP_SSL`).

**Test-pasca — `MAIL_BCC` PRIPNI vo fixture, presne ako `MAIL_HOST` (PR #223 review).** Odkedy
beh vracia `bcc_missing` a endpoint pre-flightuje `MAIL_BCC`, rozhoduje o VETVE — a `app.py`
načíta repo `data/.mail_env` do `os.environ` (`setdefault`), takže dev box (súbor má) a CI (nemá)
by šli inou vetvou: zelené tu, červené tam. Oba automation `iso` fixtures aj e2e `automations_server`
majú `MAIL_BCC` pripnuté; test, čo chce missing-BCC vetvu, si ho `delenv`-ne sám.

**Test-pasca — flaky `_save_*` cieli podľa OBSAHU, nie podľa poradia volania.** Odkedy beh
zapisuje aj claimy, `if n["calls"] == 1` netrafí priebežný `_persist_done`, ale claim (test
potom testuje niečo úplne iné a stále „prejde"). Podmieňuj zlyhanie tým, čo sa zapisuje
(`data["orders"]["<kód>"]["status"] == "emailed"`). A `monkeypatch.undo()` v teste zhodí AJ celú
`iso` izoláciu — na dočasné vrátenie jedného patchu si ulož originál a `setattr`-ni ho späť.
**Pozor aj na „obsahovú" podmienku, čo trafí VIAC zápisov (B1 M5):** endpoint po zlyhanom
post-send zápise píše núdzový `{status:'emailed', persist_failed:True}` marker — ten MUSÍ
prejsť, takže podmienka je `status=='emailed' and not entry.get('persist_failed')`.

**Test-pasca — zvyškový tranzitný `sending` claim MASKUJE chýbajúci dedup záznam celý TTL
(B1 M4).** Test „po zlyhanom priebežnom zápise sa zajtra neposiela znova" prešiel AJ bez
failed_writes re-aplikácie: zlyhal len `emailed` zápis, claim na disku ostal, a ďalší beh
objednávku preskočil ako „práve sa posiela" — nie preto, že by bola vybavená. Test, čo overuje
správanie NASLEDUJÚCEHO behu, preto MUSÍ claim nechať vypršať:
`monkeypatch.setattr(webapp, "SENDING_CLAIM_TTL_S", 0)` pred druhým behom. Overenie, či test
naozaj testuje: dočasne odstráň fix a pozri, či PADNE (bez TTL patchu prejde = vata).

### Fail-closed DEDUP stores — poškodený súbor NESMIE degradovať na `{}` (#225)

`orders_reminder.json` (komu už pripomienka odišla) a `posta_uncollected.json` (`escalation`
= koľký mail zásielka dostala) NIE SÚ display stores. Keby `_load_*` pri `JSONDecodeError`
vrátil `{}` (bežný SAFE vzor), prvý ďalší `_claim`/`_persist_done`/escalation bump zapíše
**nový JEDNOpoložkový súbor** — celá evidencia je preč a KAŽDÁ otvorená objednávka dostane
DRUHÝ mail. Vzor, ktorý drž pri každom novom dedup store:

- `_load_dedup_store(path, label, dedup_keys)`: chýbajúci súbor → `{}` (**legitímny prvý beh**
  — nikdy neblokuj, inak sa nerozbehne čerstvý deploy); nečitateľný ALEBO ne-dict → zálohuj +
  `raise DedupStoreCorrupt`. Ne-dict je druhá tichá wipe cesta (`else {}`), nie kuriozita.
- **Strážiť VONKAJŠÍ dict NESTAČÍ — validuj aj samotnú dedup MAPU** (`dedup_keys`: `orders` /
  `escalation`). `{"orders": null}` sa naparsuje A JE dict, takže vonkajší guard ho pustí, beh
  prečíta „nikomu sme ešte neposlali" a zapíše to ako fakt → ďalší beh mailuje všetkých znova
  (revízia PR #228, reprodukované). Kľúč, ktorý CHÝBA, je naďalej v poriadku (prvý beh).
- **Fail-closed daj LEN na to, čo drží „čo už odišlo".** `posta_uncollected.json`'s `terminal`
  je VÝKONNOSTNÁ cache — jej strata stojí API volanie, nikdy nie duplicitný mail, kým padnutie
  behu na nej by UMLČALO reálne upozornenie zákazníkovi. Preto `terminal` NIE je v `dedup_keys`
  a jeho ne-dict hodnotu len skoercuj na `{}` (`dict("kaboom")` inak zhodí celý beh).
- **Chytaj `ValueError`, nie `json.JSONDecodeError`.** Stores sa píšu `ensure_ascii=False` a sú
  plné slovenčiny → zápis prerezaný uprostred viacbajtového znaku hodí `UnicodeDecodeError`
  (najpravdepodobnejšia reálna korupcia). Ten guardu unikal: žiadna záloha, 500 na tabe, raw
  traceback v `last_error`. Oba sú podtriedy `ValueError`.
- **Guard musí ísť až na ÚROVEŇ ZÁZNAMU, nie len mapy.** Ne-dict hodnota POD jedným kódom
  (`null` / string / číslo / list) nie je terminálna → beh ju čítal ako „nikdy sme neposlali" a
  zákazníkovi poslal DRUHÝ mail (u pošty `parse_notified` degraduje čokoľvek ne-stringové na
  `(0, None)` → kadencia začne od upozornenia #1). Nečitateľný záznam ≠ žiadny záznam: **nevieme
  dokázať, že mail NEodišiel → neposielaj** a riadok vypíš (pending riadok / `errors`), nech to
  dorieši človek. Ručný override na tom riadku ostáva povolený — to je vedomé rozhodnutie
  manažéra, nie odhad automatu.
- **Prítomnosť KĽÚČA, nie hodnotu `None`.** `orders.get(code)` vráti `None` aj pre „žiadny
  záznam" (normálny stav každej novej objednávky) aj pre JSON `null` záznam. Rozlíšiť ich vie iba
  `code in orders` — inak buď prehliadneš `null` korupciu, alebo (horšie) vyhlásiš za poškodenú
  každú novú objednávku a neodošle sa NIČ.
- **NEZRUŠ fail-open, kým si neoveril, čo ešte kryje.** `if not isinstance(orders_map, dict):
  orders_map = dict(done)` vo finálnom save vyzeral po zavedení guardu ako mŕtvy kód — lenže
  kryl aj prípad „mapa CHÝBA" (súbor zmizol počas behu), kde `{}` zapíše prázdnu evidenciu a
  ďalší beh mailuje všetkých znova. Správne je `st.get("orders") or dict(done)`: `done` je
  snapshot zo štartu + záznamy tohto behu, teda nikdy nie menej úplný než chýbajúca mapa.
- **Hláška NESMIE radiť „zmaž súbor"** — prázdny/chýbajúci súbor je „prvý beh", teda presne ten
  wipe. Píš „oprav podľa zálohy, NEMAŽ ho".
- **Záloha je KÓPIA, nie presun.** Presunutý originál = ďalší load vidí „súbor neexistuje" =
  prvý beh = presne ten wipe. Originál necháš na mieste, nech padá nahlas, kým to človek
  neopraví. Kópie dedupuj podľa OBSAHU (`<path>.corrupt-<ts>`), inak denná automatizácia
  nechá jednu zálohu za každý beh. **Časová značka musí byť sub-sekundová (`%f`) a súbor
  zakladaj `O_EXCL`** — pri sekundovej presnosti druhá, INÁ korupcia v tej istej sekunde
  prepísala prvú kópiu, teda práve tie bajty, kvôli ktorým záloha existuje (tretia revízia).
- **Fail-closed je DEFAULT loader**, tolerantná je explicitná `_load_*_display()` varianta
  (len read-only taby). Nový call site tak zdedí bezpečné správanie, nie nebezpečné. Display
  varianta vracia **`(state, corrupt)`** a endpoint pošle `store_corrupt` → tab vykreslí
  `storeCorruptWarning()`. Bez toho poškodený store vyzerá ako pokojný deň (prázdne zoznamy) a
  korupcia, čo vznikne MEDZI behmi, je neviditeľná — tá istá „ticho mŕtva automatizácia", proti
  ktorej existuje `bcc_missing`.
- Banner vykresli na **KAŽDEJ** render ceste — `renderOrdersReminder` má skorý `return`, keď
  `/api/automations` nevráti stav; `renderPosta` ten prípad rieši inline. Asymetria = banner sa
  na jednej ceste ticho stratí. Testuje sa cez `page.evaluate` nad globálmi (`AUTOMATIONS = []`,
  `ORDERS_REMINDER = {store_corrupt:true}` → `renderOrdersReminder()`), obnov globály po teste.
- Zálohu rob **idempotentne per (path, obsah)**: `hashlib.sha256` memo + VLASTNÝ malý zámok
  (`_quarantine_lock`, NIE globálny `_lock` — nie je reentrantný a volajúci ho často už drží).
  Poškodený store sa číta aj pri KAŽDOM display requeste (tab poll-uje počas behu), takže bez
  memo by každý request skenoval priečinok a čítal všetky doterajšie zálohy.
- Beh `raise`-ne → `automation_runner._execute` zapíše `last_status='error'` + hlášku do
  `last_error`, tab ju vykreslí ako `.autoerr`. **Endpointy rieši jeden `@app.errorhandler(
  DedupStoreCorrupt)` → 503** (nie 500 — manažér má vidieť, čo opraviť, nie klikať dokola).
- Store čítaj **PRED** exportom/sieťou (lacný lokálny diskvalifikátor pred drahou prácou).
- **Revízny agent píše scratch súbory do repa** — po každom review skontroluj `git status` a
  `git ls-files | grep -i probe` PRED `git add -A`. Do vetvy sa takto omylom dostali tri dočasné
  `test_zzprobe_*.py` s testami bez asertov (vždy zelené, falošné pokrytie).

### Rastúci stav automatizácie — prunuj proti SOURCE OKNU, nikdy len podľa veku (#220, #222)

Každý akumulovaný per-položkový store (dedup, cache) rastie donekonečna, kým ho niekto neohraničí.
Vzor `orders_reminder.prune_done` + terminálna cache v `run_posta_uncollected`:

- **Prunuj proti tomu, čo je ešte v ZDROJI, nie proti dátumu.** Kód/zásielka, ktorá je stále
  v aktuálnom exporte (resp. v 30-dňovom okne), sa NIKDY nezmaže — pri dedupe by chýbajúci
  záznam znamenal DRUHÝ mail zákazníkovi. Vek je až sekundárne kritérium pre to, čo zo zdroja
  vypadlo. Okno pre `orders_reminder` je `all_order_codes()` = **VŠETKY** kódy exportu, nie len
  tie, čo prejdú `select_orders` (objednávka sa vie vrátiť do „Vybavuje sa", čerstvá prekročí
  4-dňový prah o pár dní).
- **Prázdne okno = neprunuj vôbec** (fail-closed — rovnaký reflex ako fail-closed supplier
  upload): prázdny/nečitateľný export nie je „nič nie je živé", je to „neviem".
- **POČETNÝ strop NIKDY neaplikuj na datované záznamy — len na nedatovateľné** (nález
  adversariálnej revízie PR #224, reprodukovaný). Skrátený/čiastočný export spraví okno malé,
  takže záznamy ŽIVÝCH objednávok vypadnú „mimo okna" — a strop ich potom zahodí len preto, že
  ich je veľa → duplicitný mail. Bezpečné je iba VEKOVÉ kritérium, a to preto, že retention
  (180 d) je **dvojnásobok okna orders exportu** — `ORDERS_EXPORT_WINDOW_DAYS = 90`
  (`_fetch_orders_csv`): záznam dosť starý na zmazanie nemôže patriť objednávke, ktorá je ešte
  v exporte — ani v skrátenom. Strop ostáva len pre záznamy BEZ použiteľného dátumu (tie nikdy
  nevypršia sami). **Tá väzba je PINNUTÁ testom**
  `test_retention_stays_at_least_twice_the_orders_export_window` (identifikátor drž na JEDNOM
  riadku — zalomený v backtickoch sa nedá vygrepovať) — okno bolo holá `90` bez čohokoľvek, čo
  ju spája s retentiou (import konštanty do `orders_reminder.py` by bol cyklický, preto test), takže
  rozšírenie exportu nad 180 d by ticho začalo mazať záznamy živých objednávok. Keď meníš okno,
  meň konštantu (nie literál) a rešpektuj `>= 2×`. **Ako testovať anti-strop správne:** test
  s `max_undated=3` chytí len strop, ktorý recykluje `max_undated` — proti stropu s vlastným
  konštantom (napr. 500) treba probe s TISÍCAMI datovaných záznamov mimo okna, všetkými vnútri
  retentie (`test_prune_never_drops_a_dated_record_on_count_at_ANY_scale`).
  **A `SHOPTET_ORDERS_URL` je ručne editovaná konfigurácia** — `_fetch_orders_csv` preto z nej
  `_strip_date_params()`-om odstráni prípadné vlastné `dateFrom`/`dateUntil` (dva rovnaké
  parametre = o okne rozhoduje server a väzba neplatí). Strip je TEXTOVÝ (split po `&`), nie
  `parse_qsl`+`urlencode` — URL nesie `hash` token, ktorý sa NESMIE pre-encodovať.
- **Zmazanie dedup záznamu LOGUJ** (kódy + dôvod). Keď zákazník dostane druhý mail, je to
  jediné miesto, kde sa dá zistiť, či za to mohol prune.
- **Cache terminálneho stavu (#222)** je prunovaná tým istým oknom, takže rásť ani nemôže:
  `terminal: {packageNumber: {state, at, code}}` v `posta_uncollected.json`. **Cache smie
  ušetriť API volanie, NIKDY nesmie umlčať zákaznícke upozornenie** — preto prepadne na reálnu
  kontrolu v ŠTYROCH prípadoch: poškodený záznam, iný `code` (trackovacie čísla sa do Shoptetu
  píšu ručne — preklep/recyklované číslo nesmie stiahnuť cudzí verdikt), `at` staršie než
  `POSTA_TERMINAL_RECHECK_DAYS` (7 — jedno chybné čítanie sa zahojí do týždňa, nie až po 30
  dňoch), a vyvrátený verdikt sa z cache **maže** (nie iba ignoruje).
  **GOTCHA — dátum v store porovnávaj OHRANIČENE Z OBOCH STRÁN, nie len `>= cutoff`.** `at` je
  obyčajný reťazec, takže čokoľvek, čo lexikograficky prevyšuje cutoff (`"zzz"` z poškodeného
  zápisu, budúci dátum po skoku hodín), robilo záznam **navždy čerstvým** — zásielka sa
  preskakovala celé 30-dňové okno a 7-dňová sebauzdravovacia poistka bola ticho vypnutá
  (nález revízie PR #224). Správne: `recheck_before <= str(cached.get("at") or "") <= today_iso`.
  Platí pre KAŽDÝ „čerstvosť z uloženého času" test v tejto appke: horná hranica je to, čo drží
  pravidlo „poškodené/nejednoznačné → over to", inak sa smetie tvári ako najčerstvejší možný
  záznam. **Druhý taký test je `_reminder_claim_active`** — mal iba `< SENDING_CLAIM_TTL_S`,
  takže `claimed_at` v BUDÚCNOSTI sa tváril ako živý nárok, kým ho reálny čas nedobehol:
  manažérovi „▶ Poslať pripomienku" vracalo 409 a beh objednávku preskakoval ako pending →
  zákazník pripomienku NIKDY nedostal a TTL (ktorý existuje presne preto, aby nárok objednávku
  nezamkol navždy) bol vyradený. Správne `0 <= (now - claimed).total_seconds() < TTL`. Túto
  dieru našla až revízia PR #224 — pri prvom fixe som ohraničil len `at` a v playbooku vyhlásil
  pravidlo za univerzálne; keď takéto pravidlo píšeš, VYGREPUJ všetky ostatné výskyty vzoru
  (`>= cutoff`, `< TTL`, `total_seconds()`) a oprav ich v tom istom PR.
- **Do `TERMINAL_STATE_CODES` daj LEN live overené kódy.** Live probe api.posta.sk
  (2026-07-25) vrátil presne štyri: `received`, `transit`, `notified`, `delivered` — a
  ukázal, že `delivered` pokrýva OBA konce eskalácie: „Doručená" (OK) aj **„Prevzatá na pošte"
  (OKP)**, teda aj vyzdvihnutie po ZNP oznámení (fixtúra `tracking_collected_at_office.json`).
  `notified` tam NIE JE (to je stav, ktorý automatizácia naháňa). `returned` sa NEpozorovalo →
  **NEdôveruje sa mu** (#226): keby to znamenalo „vrátená na dodaciu poštu" (späť na pošte,
  stále vyzdvihnuteľná), cache by ticho zmrazila reálne nevyzdvihnutú zásielku. Neznámy kód /
  `invalid_format` / bez eventov = NEterminálne. Overiť sa dá zadarmo: `TRACKING_API` je
  verejné GET API, stačí prebehnúť reálne čísla z `orders_cache.csv` a vypísať `stateCode`-y.
- **Reopen (vedomé rozhodnutie #220):** reopenutá objednávka si ponechá terminálny záznam a
  druhú AUTOMATICKÚ pripomienku nedostane, kým je v exporte (duplicitný mail zákazník vidí,
  chýbajúci nie); manažér vie poslať ručne z tabu. Nový cyklus o mesiace neskôr už začne čistý.

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

### Úprava už odoslanej požiadavky — REST PATCH + podpisy sa zachovávajú (#243, v0.94.0)

Komentár (`/note` vyššie) je **doplnenie**, nie oprava. Prepis samotného zadania robí
`POST /api/dev/issue/<n>/edit` → `PATCH repos/{repo}/issues/{n}` (`gh issue edit` padá,
viď classic-Project gotcha nižšie; `requests.patch`, nie post).

- **Podpisy appky v TELE sú bookkeeping, nie šéfov text.** `_split_app_markers` odreže
  koncové `_Nápad|Upravené cez appku (Vývoj) — …_` riadky, detail ich vracia zvlášť
  (`editable` = telo bez nich, `body` = celé). Pri uložení `_compose_edited_body`
  **zachová** `_Nápad …_` (jediný záznam, kto o vec požiadal) a `_Upravené …_`
  **PREPÍŠE, nepridá** — inak by z tela po pár úpravách bol changelog. Nový podpisový
  riadok pridávaj do `_APP_BODY_MARKER`, inak ho ďalšia úprava nechá napevno v texte.
- **Uzavretú úlohu odmietaj** (prečítaj `state` PRED patchom): prepísať zadanie, podľa
  ktorého už niekto konal, je preňho neviditeľné — na to je komentár.
- **Rozsah = ako komentáre a priority** (každý prihlásený, otvorené úlohy). Užšie „len
  úlohy vytvorené cez appku" by šéfovi znemožnilo opraviť práve požiadavky prepísané
  za neho z Discordu — teda ten prípad, kvôli ktorému #243 vznikla.
- **Hermetický guard v `tests/test_webreview_dev.py` musí stubovať aj `requests.patch`.**
  Dev box má reálny `data/.gh_env`, takže zabudnutý mock by trafil ŽIVÉ repo. Pri
  pridaní ďalšieho HTTP slovesa ho do guardu dopíš hneď. E2E `_GHStub` má `do_PATCH`
  (mutuje stav, takže ďalší GET vidí úpravu) a create-issue ukladá `body`/`_comments`.
- **Potvrdenie po odoslaní cez žiarovku je SAMOSTATNÝ skrytý panel (`#ideaDone`), nie
  prepis `innerHTML` dialógu** — `_ideaOpen` očakáva prvky formulára, takže prepis by
  rozbil ďalšie otvorenie. `_ideaOpen` panely prepína späť. Žiarovka je na KAŽDEJ
  záložke, takže tiché zavretie znamenalo, že šéf mimo „Vývoja" nedostal ani číslo úlohy.
- **Po uložení sa zoznam prekresľuje (`renderDev`), takže pôvodný `.dev-detail-box` je
  odpojený** — riadky nesú `data-num`, aby sa detail dal znova otvoriť na NOVOM riadku;
  bez toho vyzerá úprava ako zavretie vlastného detailu.
- **Číslo z `/issues/<n>` NIE JE dôkaz, že ide o úlohu — GitHub tým istým endpointom
  vracia AJ PULL REQUESTY (revízia PR #255).** Zoznam PR-ká filtruje (`pull_request`
  kľúč), takže každý ĎALŠÍ endpoint, čo podľa čísla ZAPISUJE (`/edit`, a rovnako každý
  budúci), ich musí odmietnuť tiež — inak si ktorýkoľvek prihlásený užívateľ prepíše
  názov a telo PR-ka len tým, že napíše jeho číslo. Odpoveď „toto číslo nepatrí úlohe".
  **Ochranu daj do ZDIEĽANÉHO `_gh_issue_or_refuse(token, repo, number)`, nie do jedného
  endpointu (revízia PR #255, druhá vlna).** Prvá vlna ju dala LEN do `/edit`, takže
  `/note` (komentár) a `/priority` (labely) ďalej písali do ľubovoľného PR-ka a
  `_do_issue_detail` jeho telo aj komentáre VRACAL — pravidlo bolo v playbooku napísané
  pre „každý endpoint", ale v kóde platilo pre jeden. Helper vracia `(issue, refusal)`,
  volajúci si necháva vlastný `try/except` (sieťová chyba je jeho degradácia), takže
  ďalší by-number endpoint ochranu ZDEDÍ namiesto toho, aby ju znova vymýšľal.
  **Test-pasca:** stub, ktorý `raise`-ne, tu prejde AJ s dierou — endpointy chytajú
  všetko do `except` a vrátia „GitHub nedostupný", čo je tiež ne-ASCII; použi
  ZAZNAMENÁVAJÚCI stub a asertuj `calls == []` + presné znenie odmietnutia.
- **Potvrdenie ukáž PRED refreshom zoznamu a tlačidlo NEODOMYKAJ (revízia PR #255).**
  `_ideaSubmit` odomkol `#ideaSubmit` a AŽ POTOM `await loadDevIssues()` — v tom okne
  (namerané 6 s pri pomalom `/api/dev/issues`) druhý klik poslal DRUHÝ POST a vytvoril
  DRUHÚ GitHub úlohu; navyše sa `#ideaDone` objavil až po round-tripe, čo robilo e2e
  flaky (2 z 3 plných behov padli). Poradie je `_ideaDone(number)` → `await
  loadDevIssues()`; tlačidlo sa odomyká JEDINE v `_ideaOpen`. Pravidlo pre každý ďalší
  „odošli a obnov zoznam" dialóg: potvrď hneď, obnovuj potom, odomkni pri otvorení.
- **Zámok drž na PRÍZNAKU VNÚTRI funkcie, NIKDY na `btn.disabled` (revízia PR #255,
  druhá vlna).** `_ideaSubmit` `disabled` iba NASTAVOVAL, nikdy nečítal — a keydown-Enter
  na poli s názvom volá `_ideaSubmit()` PRIAMO, takže `disabled` zastavil druhý KLIK a nič
  viac: Enter, Enter → dva POSTy → **dve GitHub úlohy**. Enter na jednoriadkovom názve je
  šéfova hlavná cesta odoslania, takže to bola tá živá. Vzor: `let _ideaBusy = false;` +
  `if (_ideaBusy) return;` ako PRVÝ riadok funkcie, cez ktorú idú VŠETKY vstupné body;
  nuluje sa len na chybovej ceste (retry je v poriadku) a v `_ideaOpen`. Pri KAŽDOM
  ďalšom „nesmie sa odoslať dvakrát" si vymenuj vstupné body (klik, Enter, Ctrl+Enter,
  submit formulára) a over, že zámok vidia VŠETKY — DOM vlastnosť tlačidla vidí jeden.
  **A test pomenuj podľa toho, čo naozaj pinuje**: `..._one_click_...` hnal len
  `ideaSubmit.click()`, takže ostal zelený celý čas, čo bol Enter rozbitý.
- **E2E na dvojité odoslanie drž PODRŽANÍM route** (`page.route(..., lambda r:
  held.append(r))` a `r.continue_()` až na konci) — stub odpovie príliš rýchlo na to,
  aby sa to okno vôbec otvorilo. Druhý klik posielaj cez `page.evaluate(... .click())`,
  nie Playwright klikom: ten kontroluje actionability a na disabled/skrytom tlačidle
  padne skôr, než čokoľvek zmeria.
- **✏️ „Upraviť zadanie" NEVIAŽ na neprázdne telo.** Úloha založená priamo na GitHube
  môže mať telo prázdne a NÁZOV je vtedy celá požiadavka — hlavička s ✏️ sa preto
  renderuje aj bez tela (telo vypíše „Bez textu — zatiaľ len názov.").

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

## Čítanie katalógového exportu — DVA čitatelia, jeden seam (#272/#270)

`data/products.csv` má ~57 MB, takže KTO ho číta rozhoduje o pamäti celého procesu:

- **`_iter_export_lines()` = streamovaný čitateľ (nočný push).** `open(SRC,
  encoding="cp1250", errors="replace", newline="")` + `yield from f`; `newline=""` drží
  ukončovače riadkov nedotknuté, takže `csv.DictReader` nad ním parsuje IDENTICKY ako nad
  `io.StringIO(celý_text)` — vrátane citovaného poľa cez viac riadkov aj osamoteného
  návratu vozíka (pinuje `test_the_streamed_index_parses_exactly_like_a_whole_text_parse`).
  Namerané na živom 57,4 MB exporte: špička 346,6 MB → 3,0 MB (max RSS 361 → 17,6 MB) pri
  rovnakom čase (~1,4 s). **Binárne čítanie + `raw.decode()` na každý riadok je ~1,8×
  POMALŠIE** (2,5 s) — textový režim má inkrementálny dekodér v C, použi ten.
- **`_read_export_for_links()` = bulk (scrape/JOIN automatizácie #106/#107/#108).** Zámerne
  NIE je postavený nad streamom: `"".join(riadky)` si najprv postaví zoznam všetkých
  riadkov, takže by špičku ešte ZVÝŠIL. Dvaja čitatelia sú tu správne, nie duplicita.
- **Seam pre testy je `_iter_export_lines`** (helper `_export_lines(text)`); patchnutie
  `_read_export_for_links` nočný push UŽ NEOVPLYVNÍ.
- **`_export_note_index()` / `_export_supplier_index()` = jeden prechod, viac faktov** —
  `{code: internalNote}` + množina VŠETKÝCH kódov, resp. kódy s vlastným `supplier` + všetky
  kódy + „mal súbor vôbec nejaký obsah". Keď potrebuješ ďalší fakt z exportu, PRIDAJ ho do
  prechodu, ktorý v TEJ ISTEJ funkcii už beží — nerob druhý. (Dva prechody za nočný beh sú
  v poriadku: párovania a dodávatelia sú dva nezávislé vstupné body, každý si číta sám.)

**`_export_row_verdicts(rows)` (#270) = `{"confirmed", "absent"}` z toho jedného prechodu.**
`absent` = kód, ktorý eshop v katalógu VÔBEC nemá → riadok sa NEPOŠLE (Shoptet by ho
zakaždým odmietol — presne to bolo „Zlyhanie variantov: 2" každú noc) a vypíše sa na karte
automatizácie ako „⛔ Eshop tieto kódy v katalógu nemá" spolu s hodnotou, ktorú sme chceli
zapísať; PÁROVACIA polovica behu je tak oranžová (`blocked`), nie falošne zelená.
**POZOR na formuláciu „beh je oranžový" — platí len o párovacej polovici.** Dodávateľská
polovica ten istý kód zatiaľ NAĎALEJ posiela (zámerná asymetria nižšie), Shoptet ho odmietne
→ `s_ok=False` → `run_parovania_eshop` vráti `status="failed"` (červená), nie oranžová. Na
prode je to živý stav: `supplier_assignments.json = {"145/3XL": "FOREST"}` a `145/3XL` eshop
v katalógu nemá. Celý beh sa prestane sfarbovať na červeno až keď dobehne #275. Tri veci,
ktoré k tomu patria:

- **Zadržanie NIE JE zápis do `uploaded_*.json`** — je ohraničené a samoliečivé: len čo sa
  kód v katalógu objaví, najbližší beh ho pošle. Preto stačia dve brány — a **NEROB pomerovú
  „vyzerá to na neúplný export" bránu**: sama sa vyradí vo chvíli, keď v dávke ostanú už LEN
  tie doomed riadky (100 % „absent" → brána ich zase pustí), a nočné odmietanie sa vráti.
- **Brány: čerstvosť (`EXPORT_MAX_AGE_S`) + `EXPORT_MIN_CODES` (1000).** Katalóg má
  ~14 000 kódov, takže čerstvý neprázdny export s hŕstkou kódov je pokazený feed
  (useknuté stiahnutie, zabudnutý filter), nie malý obchod — veril by mu a zadržal by
  riadky kódov, ktoré eshop má. Absolútny prah sa (na rozdiel od pomerového) nevie sám
  vyradiť. Testy si ho znižujú vo fixture; produkčnú hodnotu pinuje
  `test_an_implausibly_small_export_is_not_trusted`. Rovnaké brány platia aj pre
  hlásenie na dodávateľskej strane (oranžový beh je tvrdenie, nech stojí na overených
  bajtoch) — a `EXPORT_MIN_CODES` je tam navyše aj ZÁPISOVÁ brána (PR #276 review,
  `test_an_implausibly_small_export_blocks_the_supplier_write_back`). Chýbajúci/prázdny/
  starý export nesmie ani potvrdiť, ani zadržať — `absent` je NOVÁ podmienka na zápis do
  ostrého eshopu, takže nesie tú istú bránu ako potvrdzovanie z exportu.
- **Dodávateľský write-back tie kódy LEN HLÁSI a ďalej ich zapisuje — zámerná asymetria.**
  PR #213 rozhodlo, že prítomný-ale-partial export nesmie zahodiť doplneného dodávateľa (ten
  zápis vie len DOPLNIŤ meno tam, kde eshop žiadne nemá). Pinuje
  `test_supplier_codes_absent_from_the_catalogue_are_reported_but_still_written`.

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

**Rozhodnutie VYHRÁVA nad inline párovaním — a to isté poradie musí platiť aj v UI (#242).**
`exclude_codes` znamená, že kód pokrytý rozhodnutím sa z `order_pairings` do eshopu NIKDY
nedostane; `renderOrderRow` to zrkadlí (`supplierUrl` → potom `pairUrl`). Dôsledok, na ktorý
sa dá naletieť: **editačné pole, ktoré zapisuje do `order_pairings` na riadku s rozhodnutím,
je TICHÝ NO-OP** — uloží sa, endpoint vráti 200, a riadok aj eshop ďalej nesú starú
rozhodnutú URL („som to naparoval, ale tie linky vobec nefungujú"). Na živých dátach malo
8 kódov obe hodnoty naraz, aspoň jedna rozdielna. **Pravidlo pre KAŽDÝ nový editor na tomto
tabe: edituj to úložisko, ktoré riadok naozaj ZOBRAZUJE a ktoré sa naozaj EXPEDUJE** —
riadok preto nesie `reviewKey`/`reviewStatus` (z `import_builder.link_row_specs`) a
`savePairUrl` podľa nich smeruje zápis: `POST /api/order-decision-url` (prepíše rozhodnutie,
`status:'manual'`, 409 na `split`) vs. `POST /api/order-pair` (inline). Pomocník
`rowPairUrl(o)` je JEDINÁ definícia „ktorá URL na tomto riadku platí" — používa ju prefill
editora, `_EDITORS.pair.stored` aj porovnanie „zhodné s uloženým".

**Vylúčenie je o VLASTNÍCTVE, nie o tom, čo daný beh práve posiela (revízia PR #255).**
`_do_upload_pairings` staval `exclude_codes` z **tohtobehových NOVÝCH** decision riadkov.
Len čo sa rozhodnutie zapíše ako uploaded, prestane byť „nové", množina sa vyprázdni a
nočná dávka pošle do eshopu **starú inline URL** — oprava prežila presne JEDNU noc a
`internalNote` ostal natrvalo zlý (kód sa zapíše ako uploaded a už sa NIKDY neskúsi),
zatiaľ čo tab, `/api/orders` aj `/api/import` ďalej ukazovali tú správnu. A tá URL kŕmi
automatické doobjednávanie, takže zlý odkaz objedná zlý tovar. Vylúčenie preto počítaj zo
**VŠETKÝCH** rozhodnutí presne ako ručný zip (`link_rows(PRODUCTS, dec, CODE2PAIR,
_load_variant_links())`) — a keď v tejto appke uvidíš dve cesty do TOHO ISTÉHO eshop poľa,
over, či majú **rovnaké vstupy** (zip ich mal, nočná nie: `variant_links` chýbali).
Druhá polovica: `/api/order-decision-url` po zápise **zmaže `order_pairings` pre variantné
kódy** toho produktu — v tom momente je inline hodnota preukázateľne prebitá, takže tam
nemá čo čakať na noc, keď vylúčenie zlyhá.

**Endpoint, ktorý mení rozhodnutie podľa kľúča zo SNAPSHOTU klienta, musí prečítať ČERSTVÝ
stav a odmietnuť všetko, čo nie je párovanie (revízia PR #255).** `reviewKey` je zamrznuté
v `ORDERS`, takže čokoľvek, čo manažér medzitým zmenil v revízii (druhé okno, otvorený tab),
sa ✏️ zápisom prepísalo: `unavailable` → `manual` (eshop prestal dostávať Vypredané+stock 0),
`discontinued` → `manual` (zmizol riadok „Predaj skončil"), a **chýbajúce rozhodnutie sa
VYTVORILO** — nerecenzovaný produkt sa označil ako recenzovaný. Guard je „prijmi len
`good`/`manual`", inak 409 (split má vlastnú hlášku). Pravidlo pre KAŽDÝ ďalší taký
endpoint: kľúč zo snapshotu je len ADRESA, stav sa vždy re-číta pod zámkom.

**`import_builder.link_row_specs` je JEDNA slučka za `link_rows` aj za mapu vlastníkov.**
Pravidlo „každý kód raz, prvé párovanie vyhráva" rozhoduje AJ o tom, ktorý kód komu patrí,
takže druhá kópia tej dedup logiky by sa časom rozišla s tým, čo sa naozaj zapisuje. Nový
**A filter, ktorý z toho vyplýva, patrí DO tej slučky — nie do jedného čitateľa (revízia
PR #255, druhá vlna).** Špecifikácia musí POMENOVAŤ vlastníka, takže produkt bez
použiteľného kľúča nesmie vydať nič — lenže prvá vlna filtrovala `if s[3]` len v mape
vlastníkov (`code2owner`), nie v `code2url` ani v `link_rows`. Riadok tak ďalej ukazoval
odkaz rozhodnutia AJ ✏️, ale s prázdnym `reviewKey` → `savePairUrl` poslal opravu do
`order_pairings`, ktoré `_do_upload_pairings` vylúči (`owned_codes` ide z `link_rows` a
na kľúč nefiltruje). Prijaté a nikdy neodoslané — presne ten tichý no-op, kvôli ktorému
#242 vzniklo. Guard preto sedí v `link_row_specs` (`if not key: continue`), kde ho
vyzdvihnú VŠETCI TRAJA čitatelia naraz. Test to pinuje aj cez `link_rows(...) == []`,
takže „downstream-only" oprava (filter iba v `code2url`) ho neprejde — over MUTANTOM.
Konzument „ktoré rozhodnutie vlastní tento kód" konzumuje `link_row_specs` PRIAMO
(`build_to_order_rows` to tak robí — jeden prechod dá aj URL aj vlastníka). Obálka
`link_owners` existovala, ale ju **nevolal žiadny produkčný kód** — len anti-drift test,
takže test strážil mŕtvy kód; zmazaná, test prepísaný na `link_row_specs`. Keď píšeš
anti-drift test, over, že mieri na cestu, ktorou appka naozaj ide.

**Split produkt (#174) je na tabe „Na objednanie" SLEPÉ MIESTO, ak `variant_links` nedáš
až do `build_to_order_rows`.** Bez nich `split` vetva `link_row_specs` nevydá nič → riadok
má `supplierUrl=''`/`reviewKey=''` a vykreslí prázdne vkladacie pole pre produkt, ktorý JE
napárovaný per veľkosť; `savePairUrl` to pošle do `order_pairings`, ručný zip to zahodí,
nočná to pošle — a keďže `split_links` má vlastnú `uploaded_variant_links.json`
idempotenciu (nikdy nepushne znova), inline zápis **natrvalo prepíše** už nahraný
veľkostný odkaz. Riadok split produktu preto dostane **✂️ tlačidlo s VLASTNOU triedou
`to-splitedit`** (nie `to-pairedit` — na ten sa viaže `_EDITORS.pair.open`, takže po
prekreslení by sa produktovo-široké pole vrátilo zadnými dvierkami) a to otvorí per-veľkosť
panel v revízii (`openSplitSizes`: `splitOpen.add`, `FILTER='good'`, `switchTab('review')`,
scroll na `.card[data-key]`). Produktovo-široký save tam nemá zmysel: endpoint ho odmietne
409, inline cesta ho skazí.

**Tlačidlo V RIADKU, ktoré ODNAVIGUJE, je tichá strata práce — a nesmie prepísať uloženú
predvoľbu (revízia PR #255, druhá vlna).** ✂️ sedí hneď vedľa ✏️, ktoré edituje NA MIESTE,
takže sa ako navigácia vôbec nečíta — a odchod z tabu postaví `#list` nanovo, čiže zahodí
KAŽDÝ otvorený inline editor aj s rozpísaným textom na VŠETKÝCH ostatných riadkoch, bez
hlášky (trieda strát, ktorú #205/#233 odstraňujú). Pred odchodom preto spočítaj prácu TÝM
ISTÝM predikátom, aký používa prekresľovacia mašinéria — `captureOpenEditors().filter(s =>
editorSnapHasWork(s, ORDERS.find(x => x.key === s.key)))` — a pri nenulovom počte sa spýtaj
`confirm()`-om („zrušiť" = ostávame, nič sa nezmení). Bez toho filtra varuje aj tam, kde
nie je čo stratiť (prázdne default paste-boxy sa počítajú) — over MUTANTOM v oboch smeroch.
A `FILTER = 'good'` nastav LEN v pamäti: `localStorage.setItem('filter', …)` z toho robí
trvalú zmenu manažérovej revíznej predvoľby ako vedľajší účinok jedného kliku (číta sa iba
v `init()`, takže na zobrazenie karty stačí premenná). Platí pre každé ďalšie tlačidlo,
ktoré z riadku odvedie inam.

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

## e2e — vlastný `toorder_server` fixture pre tab „Na objednanie" (#203/#204/#214)

`live_server` je **session-scoped a zdieľaný** — jeho `orders_cache.csv` má hard-koduté
počty/poradie, na ktoré sa viažu `test_order_chips`/`test_order_bulk_stale`/`test_shell`,
takže PRIDANIE riadku kvôli novému testu rozbije cudzie asserty. Nový to-order test preto
píš proti **function-scoped `toorder_server`** (vlastný out-dir → pairings/flagy z testu
nikam nepresakujú a netreba po sebe upratovať). Fixtúra vedome nesie: case varianty
dodávateľa (`CITRADE`/`Citrade`/`citrade`), DVA riadky s tým istým `itemCode` v RÔZNYCH
objednávkach (súrodenci), a jeden riadok BEZ dodávateľa (inline supplier-assign editor sa
zobrazí len takému). Nezabudni ju pridať do `_SERVER_FIXTURES` (auth cookie) a nastaviť
`WEBREVIEW_PRODUCTS` na neexistujúci súbor — inak dev box ťahá reálny `data/products.csv`
a test sa správa inak než na CI.

**Na ŠÍRKU riadku a na recenzované párovanie použi `toorder_wide_server` (#241/#242), nie
tento.** `toorder_server` je all-unpaired a krátky — bunky sa nemajú prečo zmenšovať, takže
šírkový RED nikdy nenastane a riadok s rozhodnutím tam vôbec nie je. Tá fixtúra má
review_data + `decisions.json` (dva riadky s tým istým rozhodnutím, na propagáciu),
realisticky dlhé názvy a poznámku e-shopu, a jeden riadok bez dodávateľa.

## Živé Playwright overenie bez znečistenia dát

To-order flagy píšu do živých stores. Pri overovaní na živom webe **toggluj on→off** (skonči v pôvodnom stave) a potom over `data/out/<store>.json` že je zase `{}` (resp. pôvodný počet) — nikdy nenechaj reálnu objednávku označenú z testu.

## Gotcha — `.card{display:grid}` deti potrebujú `min-width:0`, inak sa button „stratí" na úzkom displeji

`.card` je CSS Grid (`grid-template-columns:1fr 1fr`, mobil `1fr`). Grid ITEM (`.side.left`/`.side.right`) má bez `min-width:0` automatickú minimálnu šírku = min-content jeho OBSAHU — dlhý nezalomiteľný text (candidate name, URL) v `.manualrow`/`.cand` vnútri vie natiahnuť grid TRACK ďaleko za viewport; `.card` samo zostane správne úzke (`overflow:hidden`), ale JEHO VNÚTRO pretečie a zelené tlačidlo („Uložiť URL"/„Vybrať") skončí v odrezanej oblasti — neviditeľné a neklikateľné (#82). Samotné `flex-wrap`/`min-width:0` na `.manualrow`/`.cand` NESTAČÍ, ak `.side` sám o sebe nemá `min-width:0` — fix musí byť na GRID ITEME (`.side{min-width:0}`), flex-level úpravy sú len defense-in-depth. **Krátky test fixture (krátky názov/URL) bug NEREPRODUKUJE** — nič sa nemusí zmenšovať, takže RED nikdy nenastane; na overenie/regression e2e treba REALISTICKY DLHÝ obsah (skutočná dĺžka candidate name + supplier URL). Diagnostika: `page.evaluate("el => ({sw: el.scrollWidth, cw: el.clientWidth})")` na `.card`/`.side.*` — `scrollWidth > clientWidth` = vnútri pretieklo.

## Gotcha — riadok „Na objednanie" ZALAMUJE na každej šírke; nová bunka nesmie čakať jeden riadok (#241/#242)

Sesterský problém k `.card` gotche vyššie, ale vo `flex` riadku a bez `overflow:hidden`,
takže sa NEOREŽE — utečie mimo obrazovku. `.toorder-row` mala `flex-wrap` len pod 760 px,
pričom skoro každá bunka je `flex:0 0 auto` + `white-space:nowrap`. Merané naživo pri
viewport 1280: `document.scrollWidth` **1778** (1862 s otvoreným editorom), `.to-name`
stlačená na **0 px** (názov produktu neviditeľný) a päť buniek vrátane „💬 Komentár",
„✓ Skladom" a „✗ Nedostupné" mimo plochy — teda **nedosiahnuteľné ovládanie**, nie kozmetika.

- **Breakpoint tu nikdy nebol správny nástroj**: koľko buniek riadok nesie (grube čip, Σ čip,
  badge starej objednávky, poznámka e-shopu, komentár, priraďovací editor — všetko voliteľné)
  rozhoduje viac než viewport. Preto `flex-wrap:wrap` NATVRDO, media query len dolaďuje medzery.
- **Pružná textová bunka potrebuje reálny `flex-basis`, nie `flex:1`.** Pri `flex:1`
  (basis 0) dostane `.to-name` len omrvinky z prvého riadku — meranie ju našlo na 0 px.
  `flex:1 1 220px` ju pri stiesnenom riadku pošle na vlastný riadok a je čitateľná.
- **`min-width` bunky s vlastným inputom drž nad použiteľnosťou vstupu**: `.to-pair` mala
  230 px, ale `.to-pairurl` má vlastný 110 px floor → pole na dodávateľskú URL sa scvrklo
  na ~110 px, kde sa adresa nedá ani prečítať, nieto opraviť. Teraz 260 px.
- **Testuj INVARIANT, nie pixel**: „žiadna bunka riadku nekončí za viewportom"
  (`getBoundingClientRect().right > clientWidth`) prežije zmenu palety aj popiskov;
  magické číslo (1777) nie. Parametrizuj cez 1280/1440/1600/1780/1920 a NECHAJ v sade aj
  šírky, ktoré boli zelené už predtým — sú to kontroly, že sa široké šírky nezhoršili.
- Fixtúra na to je **`toorder_wide_server`** (e2e conftest) — zámerne dlhá a plná (recenzované
  párovanie, dlhý názov, dlhá poznámka e-shopu, riadok bez dodávateľa, dva riadky s tým istým
  kódom). Platí tu to isté ako pri #82: **krátka fixtúra šírkový bug NEREPRODUKUJE**, RED by
  nikdy nenastal. `toorder_server` je all-unpaired a krátky — na šírku ho nepoužívaj.

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
- **Keď potrebuješ overiť DOM V MOMENTE hlášky, `page.on("dialog")` NEPOUŽI — spy-uj `window.alert`
  cez `page.add_init_script` (#214).** Natívny `alert()` **blokuje JS thread**, takže `page.evaluate`
  (ani `locator.get_attribute`, ktoré tiež beží injektovaný skript) vnútri dialog handlera NIKDY
  nedobehne — deadlock, nie flake. Vzor: `page.add_init_script("window.__alerts=[]; window.alert=(m)=>
  window.__alerts.push({msg:String(m), cls:(document.querySelector(sel)||{}).className});")` PRED
  `page.goto`, potom `page.wait_for_function("() => window.__alerts.length > 0")` (deterministické,
  žiadny `wait_for_timeout`) a čítaj `page.evaluate("() => window.__alerts")`. Bonus: overí sa AJ text
  hlášky AJ stav DOM v tej sekunde — presne to pinuje pravidlo „najprv rollback + prekreslenie, až
  potom hláška". `page.on("dialog")` nechaj na `confirm()`/`prompt()`, kde len odpovedáš.
- **Chybové cesty testuj Playwright request-interception, nie zmrzačeným serverom:**
  `page.route("**/api/instock", lambda r: r.fulfill(status=500, content_type="application/json",
  body='{"ok": false}'))` pre odmietnutý zápis, `route.abort()` pre mŕtvu sieť, a `body` s reálnym
  `{"error": "..."}` na overenie, že sa dôvod zo servera dostane až k manažérovi.
- **Assert, ktorý NEVIE padnúť, je vata — over to.** „chip musí ostať zelený" na skupine so 4 riadkami
  je vždy pravda (ostatné 3 sú neoznačené), nech sa rollback deje alebo nie; treba **jednoriadkovú
  skupinu** (v `toorder_server` je to `—`/N1), kde by chýbajúci rollback chip naozaj prefarbil. Kto
  píše regresný test na poradie operácií, nech si ho overí dočasným prehodením poradia v kóde.
- **Pure JS helper sa dá unit-testnúť v prehliadačovom realme cez `page.evaluate` — žiadny JS toolchain
  netreba.** `app.js` je plain `<script>` (nie module), takže top-level `function foo(){}` sú GLOBÁLNE
  (na `window`) a `let` globály (`VARIANT_LINKS`, …) sú dosiahnuteľné/priraditeľné holým menom v
  `page.evaluate`. Vzor: `page.evaluate("() => { VARIANT_LINKS={...}; return variantsWithoutLink([...]); }")`
  s viacerými vstupmi (mixed / from-link / fallback / empty / null) — testuje čistú logiku bez DOM/siete.
