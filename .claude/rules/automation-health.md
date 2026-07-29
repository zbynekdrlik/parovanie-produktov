---
paths:
  - "src/parovanie/posta_uncollected.py"
  - "src/parovanie/orders_reminder.py"
  - "webreview/app.py"
  - "tests/fixtures/**"
  - "tests/test_posta_uncollected.py"
  - "tests/test_webreview_automations.py"
  - "tests/test_webreview_orders_reminder.py"
---

# Automatizácie — ako nezomrieť potichu (a ako neveriť fixtúram)

Štyri veci, ktoré tento repozitár už stáli reálnu škodu. Prečítaj ich skôr, než pridáš alebo
zmeníš automatizáciu, jej `stats`, alebo fixtúru odpovede z cudzieho API.

## 1. Fixtúra, ktorá vznikla v tom istom commite ako feature, nedokazuje NIČ o tvare API

`tests/fixtures/posta/tracking_notified_znp.json` sa narodila v `f487a4d` spolu s featurou, mala
vymyslené číslo zásielky a `retainedTill` na EVENTE. Živé api.posta.sk to pole vracia na úrovni
VÝSLEDKU (`results[0]`). Test teda roky overoval tvar, ktorý si sám vymyslel, kód čítal pole
z nesprávnej úrovne a každý eskalačný mail zákazníkovi zamlčal termín vyzdvihnutia (#283).

**Než sa o fixtúru oprieš, over jej pôvod:**

```bash
git log --oneline --diff-filter=A -- tests/fixtures/<cesta>   # vznikla s featurou = podozrivá
```

- Fixtúra, ktorá je REÁLNA (anonymizovaná) odpoveď, to má napísané v docstringu testu alebo
  v module — napr. `tracking_collected_at_office.json` (reálne `detailCode` `ZNP1AN`, `OKP`).
- Vymyslená fixtúra sa pozná aj podľa detailov, ktoré v realite neexistujú (`ZNPOK` namiesto
  `ZNP1AN`, „pekné" okrúhle dátumy, číslo typu `EF000000002SK` bez zdroja).
- Keď čítaš pole z cudzej odpovede, čítaj ho **na oboch úrovniach, ak si nie si istý** (výsledok
  primárne, event ako fallback) — pri poli, ktoré len formuluje text a nerozhoduje o odoslaní,
  to nestojí nič a je to odolné voči obom tvarom.
- Anonymizuj: čísla zásielok drž zjavne fiktívne, ale TVAR odpovede reálny. Do fixtúry NIKDY
  meno, e-mail, telefón ani adresu zákazníka.

### Fiktívnu hodnotu treba DOKÁZAŤ grepom, nie „vyzerá vymyslene"

Repozitár je VEREJNÝ. „Toto číslo vyzerá ako testovacie" nie je dôkaz — v tomto repe takto
prežilo v testoch reálne, v tej chvíli sledovateľné podacie číslo (3× v `orders_cache.csv`,
naviazané na objednávku s menom, e-mailom, telefónom a mestom zákazníka), a druhé sa k nemu
pridalo o issue neskôr, lebo ho niekto skopíroval z toho istého bloku.

Než pridáš do `tests/` akúkoľvek hodnotu, ktorá by mohla pochádzať zo živých dát (podacie číslo,
kód objednávky, meno, e-mail, telefón, adresa), over ju:

```bash
# POZOR: LC_ALL=C — export je cp1250 a v UTF-8 locale grep časť riadkov ticho preskočí
LC_ALL=C grep -ac "<hodnota>" data/out/orders_cache.csv     # musí byť 0
```

- **`0` nestačí, ak hodnota vyzerá reálne.** Cache je kĺzavé 30-dňové okno — hodnota z mája v nej
  dnes nie je. Pozri sa aj na PREFIX (blok dopravcu): keď `LC_ALL=C grep -aoE '\b0[0-9]{13}\b'
  data/out/orders_cache.csv | cut -c1-10 | sort | uniq -c` ukáže ten istý blok medzi živými
  číslami, hodnota sa nedá vyhlásiť za vymyslenú → anonymizuj ju.
- **Voľ hodnotu, ktorá nemôže kolidovať:** `00000000000001`, `EF000000002SK` — zachovaj len TVAR
  (dĺžka, číselnosť), na ktorom test naozaj stojí. Žiadny kód u nás formát podacieho čísla
  nevaliduje (dopravca sa určuje z `SHIPPING` položky), takže anonymizácia nič nerozbije.
- **Plošná kontrola** (oplatí sa pri každom väčšom PR): načítaj z `orders_cache.csv` stĺpce
  `packageNumber/code/email/phone/bill*/delivery*` a hľadaj ich ako podreťazce v celom `tests/`.
  Zásah v mene/e-maile/telefóne je vždy únik; mesto, ulica či PSČ býva náhoda (v HTML fixtúrach
  dodávateľov ide o ich vlastné kontakty) — ale musíš to vedieť zdôvodniť, nie predpokladať.

### Kód objednávky v testoch má TVAR, ktorý reálny mať nemôže (#289)

Vyššie uvedené „over grepom" nestačí ako TRVALÉ riešenie — pri kóde objednávky sa dá
kolízia vylúčiť raz a navždy tvarom. Shoptet ich tvorí ako `<rok><4-ciferné poradie>`
(`2026nnnn`), takže testová konvencia je opačná: **kód objednávky v testoch začína `9900`**
(`2026nnnn` → `9900nnnn`). To nie je rok, takže sa netrafí do žiadneho minulého ani
budúceho exportu. Pripnuté testom `tests/test_orders_pii_hygiene.py` (kontroluje TVAR, nie
zoznam známych únikov — zoznam zastará pri prvom ďalšom prilepení).

- **Kontrola sa netýka len `tests/`.** Rovnaký únik bol aj v `.claude/rules/`, v
  `.claude/skills/` a v `docs/` — všetko je to verejné. Guard preto skenuje `tests/**/*.py`,
  `.claude/**/*.md` a `docs/**/*.{md,html}`. Jediná výnimka je `tests/fixtures/` (uložené
  cudzie stránky; ich 8-ciferné čísla sú časové pečiatky obrázkov dodávateľa a prepis by
  zabil presne to, čo fixtúra pripína — reálny tvar stránky).
- **Pri KÓDE PRODUKTU je substring-grep šum, nie dôkaz.** Nad 57 MB `products.csv` má
  ľubovoľné 5-ciferné číslo desiatky zásahov (`77777` = 18, `55555` = 81), takže „0 výskytov"
  sa tam nedá dosiahnuť ani pre zjavne vymyslený kód. Kódy produktov sú navyše verejné
  katalógové identifikátory bez väzby na zákazníka — nie sú to úniky. Anonymizuj len ten,
  ktorý bol skopírovaný SPOLU s reálnym riadkom objednávky (konvencia `TESTKOD/…`), a
  `SHIPPING11/23/26` nechaj tak: z nich sa určuje dopravca.
- **Pozor na hodnotu, ktorá kódom objednávky len VYZERÁ.** V `test_posta_uncollected.py`
  bol taký zásah DÁTUM (`retainedTill` v ISO-basic tvare `RRRRMMDD`), nie kód — ale kolízia
  je štrukturálna: každý dátum v prevádzkovom roku sa trafí do číslovania kódov. Prepísaný
  na `99000803` (= `9900-08-03`, stále platný ISO-basic dátum, `date.fromisoformat` ho číta
  rovnako), takže test dokazuje presne to isté a sweep ostáva čistý. Nezdôvodňuj takú
  hodnotu „veď je to dátum" — prepíš ju. (Guard nižšie ju chytí aj v tomto súbore; presne
  tak sa na ňu prišlo pri písaní tejto poznámky.)

## 2. Prah alarmu kalibruj na REÁLNEJ histórii, nie od oka

Pri `source_coverage` (#282) sa prahy dali zmerať za pár minút read-only prepočtom nad živou
cache exportu — a čísla vyšli úplne inak, než by človek hádal (v zdravom júni ~27 % odoslaných
objednávok podacie číslo legitímne nemá, takže „chýba čo i len jedno" by pískalo denne).

```bash
# read-only: NIKDY nič nezapisuj do data/out
PYTHONPATH=src python3 -c "...čítaj data/out/orders_cache.csv (cp1250) a použi TIE ISTÉ filtre..."
```

Postup, ktorý sa oplatí zopakovať pri každom novom alarme:

1. Prepočítaj metriku nad živými dátami **rovnakými filtrami, aké používa produkčný kód**
   (u nás cez zdieľaný `_eligible_orders` — alarm nesmie merať inú množinu, než akú
   automatizácia obsluhuje).
2. Odsimuluj KĹZAVÉ okno cez zdravé aj choré obdobie a nájdi **najhoršiu zdravú hodnotu**
   (u nás pokrytie 73,2 %, najdlhšia medzera medzi novými číslami 3 dni).
3. Prah polož s rezervou pod/nad ňu (50 %, 7 dní) a napíš do kódu, **z čoho to číslo vzniklo** —
   bez toho ho o pol roka niekto „vyladí" naslepo.
4. Dopočítaj **dôkazný prah**: pri malej vzorke je pomer šum. Ak p(jav nastane náhodne)
   = `baseline**N`, zisti, pri akom N klesne pod ~0,1 % (u nás 5 objednávok) a pod tým alarm
   nevyhodnocuj. Prázdne/tiché okno nikdy nesmie kričať.

## 3. Tichá smrť automatizácie — kánonický tvar (bcc_missing → store_corrupt → source_degraded)

Opakovaná trieda chyby: automatizácia beží, skončí `ok`, a pritom NIKOHO neupozornila — chýbal
BCC (#126), evidencia bola poškodená (#225), alebo prestal existovať zdroj zásielok (#282, päť
dní zelených behov, kým na pošte ležal reálny balík do termínu). `checked`/`sent` to nikdy
neodhalia — počítajú prácu, ktorú sa PODARILO spraviť.

Keď pridávaš automatizáciu alebo nový spôsob, akým môže oslepnúť, urob všetky štyri kroky:

1. **Príznak do `stats`** (`bcc_missing`, `store_corrupt`, `source_degraded`) — plus surové čísla,
   z ktorých sa dá napísať veta pre človeka. `stats` sa v testoch porovnáva ako PRESNÝ dict, takže
   nový kľúč je vždy vedomá zmena, nie niečo, čo sa ticho objaví.
2. **ERROR do logu** s konkrétnymi číslami.
3. **Červený banner v karte** (`el('div', 'autoerr', …)`) — po slovensky, s číslami a s tým, ČO má
   manažér ísť skontrolovať. Nie prídavné mená, ale „87 z 91 odoslaných objednávok nemá podacie
   číslo".
4. **Stav karty a bočný ⚠ odznak** — `navError()` musí svietiť aj pre degradovaný beh, inak sa to
   manažér dozvie len keď sám otvorí tú konkrétnu záložku (presne preto vznikol #153). Beh, ktorý
   nevidí vlastný vstup, ZLYHAL, aj keď nespadol.
   **Pozor na kľúče:** kľúč záložky v `AUTOMATION_TABS` NIE JE vždy kľúč automatizácie —
   pošta má v menu legacy `posta`, ale `Automation.key` je `posta_uncollected`. `autoByKey('posta')`
   preto roky nenašiel nič a odznak sa pri tejto automatizácii nerozsvietil ANI pri zlyhaní
   (mapa `NAV_AUTOMATION_KEY`). Keď pridávaš signál do bočného menu, over ho E2E testom — táto
   chyba prežila len preto, že ju nikdy nikto neklikol.

### Fail-closed pre MAZANIE ešte neznamená fail-closed pre MAIL (revízia PR #295)

Najzradnejší tvar tichej smrti: jedno spoločné nastavenie, dvaja konzumenti a KAŽDÝ má inú
cenu za omyl. `_order_statuses_state()` počítal dôvod `bad-status-config`, prune ho čítal a
odmietal mazať — a `_order_statuses()` ten istý dôvod ZAHADZOVAL, takže pripomienkové maily
sa potichu vrátili na predvolené „Vybavuje sa", čiže presne tým zákazníkom, ktorých manažér
zúžením zoznamu vyradil. Ani stat, ani banner, ani ⚠ odznak.

- **Keď loader vracia `(hodnota, dôvod)`, wrapper bez dôvodu je nová fail-open cesta.**
  Konzument, ktorý niečo ODOSIELA alebo MAŽE, musí volať verziu s dôvodom. Zobrazovacia
  cesta smie kresliť na predvolbách — ale musí dôvod NIESŤ ĎALEJ (pridaj ho do `/api/*`
  odpovede), inak sa manažér o stave dozvie len z automatizácie, ktorú práve neotvoril.
- **Nová zábrana pri odosielaní kopíruje tvar existujúcich** (`have_key`, `bcc_missing`):
  lacná kontrola pred drahým volaním, žiadny claim, žiadne OpenAI, dôvod na KAŽDOM
  dotknutom riadku — a daj ju za tie existujúce, nech si `ai_unavailable`/`bcc_missing`
  ďalej hlásia vlastnú medzeru pravdivo.
- **Odznak: prihlás sa do `source_degraded`, nezakladaj druhý príznak.** `navError()` sa
  pýta práve naň; vlastný nový kľúč znamená, že si ho každá budúca automatizácia musí
  pamätať (a `autoByKey('posta')` ukázal, ako to dopadne). Vlastný kľúč (`bad_status_config`)
  pridaj NAVYŠE — na text bannera.
- **Nový prvok na týchto kartách nesmie nosiť triedu `.auto*`** — druhý `.autoerr` na
  záložke rozbije prísne E2E lokátory (#209). Vlastná trieda, štýlovaná z `.autoerr`.

**Každý takýto stav si zaslúži E2E test.** Celý prínos alarmu je to, ČO manažér uvidí; jednotkový
test na `stats` dokazuje len polovicu. Vzor: fixture server so zaseknutým `last_result`
(`posta_degraded_server` v `tests/e2e/conftest.py` — nový server treba pridať aj do
`_SERVER_FIXTURES`), potom skontroluj text karty, banner aj odznak. Práve tento test odhalil
nefunkčný odznak vyššie.

Alarm musí ostať **iba počítadlom**: nesmie nič odoslať ani rozšíriť množinu zásielok, ktoré idú
do eskalácie — pripni to testom (`test_posta_source_alarm_never_widens_what_gets_mailed`). Pri
Pošte je to kritické: keď sa zdroj opraví, 130 doteraz neviditeľných zásielok sa objaví naraz.

### Signál slepoty napísaný pre JEDNOPRVKOVÚ množinu po zovšeobecnení PRESTANE platiť

Najdrahší nález revízie PR #298 a trieda chyby, ktorá sa bude opakovať pri každej ďalšej
konštante, z ktorej sa stane množina. `dispatched_status_unknown` detegoval premenovanie
stavu podmienkou „množina rozpoznaných je PRÁZDNA" — a bola to správna detekcia presne
dovtedy, kým bola množina jednoprvková (`DISPATCHED_STATUS = "Vybavená"`), lebo vtedy
„obchod premenoval stav" a „nič sa nezhoduje" boli JEDNA udalosť. Po odvodení množiny
(`terminal − cancelled`, tri živé názvy) sa udalosti rozišli: stačí, aby prežil jeden
zriedkavý názov, a podmienka je nesplniteľná navždy. Namerané: 100 objednávok premenovaných
+ 1 dobropis + 1 výmena = `dispatched_orders=2`, alarm ticho, hoci na maine zaznel.

- **Keď z konštanty robíš množinu, prejdi KAŽDÝ test „je to prázdne / je to rovné X" nad ňou**
  a spýtaj sa, čo ten test naozaj meria. Prázdnota jednoprvkovej množiny je „vokabulár sa
  rozpadol"; prázdnota trojprvkovej je len „rozpadol sa CELÝ", čo je najmenej pravdepodobný
  spôsob, akým sa rozpadá.
- **Formuluj signál cez to, čo alarm potrebuje na PRÁCU, nie cez tvar konfigurácie.** Tu:
  „mám okno, ktoré sa oplatí posúdiť, a nerozpoznám v ňom dosť objednávok, aby som ho
  posúdil" — čiže tá istá dôkazná hranica, pod ktorou už alarm odmieta počítať. Dve výroky
  sa tým stanú VYČERPÁVAJÚCE (okno je buď posúdené, alebo vyhlásené za slepé) a medzi nimi
  nezostane pásmo, ktoré je ticho v oboch smeroch. Pripni to testom nad ROZSAHOM hodnôt
  (`for n in range(0, 9)`), nie jedným prípadom — diera bola práve v intervale 1–4.
- **Porovnanie „konfigurované názvy vs. názvy v exporte" znie ako riešenie a nie je ním.**
  Pri premenovaní hlavného stavu sú zvyšné dva v exporte stále prítomné (prienik neprázdny =
  ticho), a opačná verzia („chýba hociktorý") je trvalý šum, lebo zriedkavý stav v 30-dňovom
  okne legitímne chýba (`Vybavený Dobropis` = 10 riadkov za 90 dní).
- **Nová hranica sa kalibruje ako každá iná (bod 2), meraním nad kĺzavými oknami** — tu
  `MIN_ELIGIBLE_FOR_BLIND_SPOT = 20` proti nameranému pomeru, ktorý cez 120 okien neklesol
  pod 0,63 — a v teste nechaj KONTROLU na pokojné malé okno, inak sa „hlás vždy slepotu"
  nedá odlíšiť od opravy.
- **Hláška musí ísť s tým** — keď signál po zovšeobecnení pokrýva aj 1–4 rozpoznaných, veta
  „ANI JEDNA nemá stav…" je nepravdivá práve v novej vetve (bod 4, posledný odsek).

### Test na FALLBACK musí mať vo fixtúre riadok v tej PREDVOLENEJ hodnote

Z tej istej revízie (B-F1), a je to všeobecnejšie než jeden test. Test menom
„…lights_the_blind_spot_instead_of_falling_back" mal brániť tichému návratu na zabudované
„Vybavená" — a jeho fixtúra niesla len riadky `Zrušená`. Pri fallbacku sa teda nezhodovalo
nič, výsledok bol IDENTICKÝ a celý balík 126 testov prešiel aj s vrátenou chybou
(`return out or default`).

**Pravidlo: fixtúra musí obsahovať hodnotu, ktorú by fallback DOSADIL** — inak test meria
dve rôzne cesty, ktoré náhodou vracajú to isté. Over to mutáciou (store-prune §6): zruš
opravu, pusti presne ten test a pozri sa, či spadne a NA ČOM. Tu to je `1 == 0` na
`dispatched_orders`. „Prejde 126 testov" nie je dôkaz, že niektorý z nich stráži práve túto
vetvu.

### Druhá cesta zákazníckeho mailu musí byť fail-closed ROVNAKO ako prvá

Odsek vyššie („Fail-closed pre MAZANIE ešte neznamená fail-closed pre MAIL") uzavrel
`run_orders_reminder`. Revízia PR #298 našla, že tá istá konfigurácia má DRUHÉHO
odosielateľa — eskalácie Pošty — a ten ostal fail-OPEN, lebo si sety bral cez
`_posta_statuses()`, teda cez ďalší wrapper nad `_order_statuses()`. Oprava jednej cesty
teda nie je oprava triedy chyby.

- **Keď zavrieš jednu cestu, VYGREPUJ všetkých konzumentov toho istého nastavenia**
  (`grep -n "_order_statuses()" webreview/app.py`) a rozhodni o KAŽDOM: posiela, maže, alebo
  len kreslí? Prvé dve musia dostať dôvod.
- **Wrapper, ktorý dôvod nenesie, je nová fail-open cesta — aj keď ho píšeš ty sám o dva
  tickety neskôr.** Preto `_posta_statuses()` vracia `(cancelled, dispatched, reason)`:
  bezdôvodová verzia jednoducho neexistuje, takže sa na ňu nedá zabudnúť. Cudzie testy,
  ktoré tú n-ticu rozbaľujú, uprav vo vlastnom commite (`toorder-e2e.md` bod 6).
- **Zablokovaný mail nie je zlyhaný mail.** Nepripočítavaj ho k `emails_failed` — schová to
  príčinu za číslo, ktoré vyzerá ako problém SMTP. Vlastné počítadlo (`emails_blocked`) plus
  príznak na banner, a prihlás sa do `source_degraded` (bod 3), nie do nového kľúča.

### Náhľad dopadu porovnávaj proti REÁLNE ÚČINNEJ konfigurácii

Tretí nález tej istej revízie a najzradnejší z nich: náhľad z bodu 5 odpočítaval to, čo
loader VYKRESLÍ (predvolby), nie to, čo reálne UČINKUJE. Pri pokazenej konfigurácii sa
neposiela nič, čiže účinná množina je PRÁZDNA a celý kandidát je nový — náhľad však hlásil
„nič nepribudne" presne pri najväčšej vlne, akú vie appka vypustiť (namerané: 0 vs. 37).

- **Spýtaj sa „čo by sa stalo, keby to teraz bežalo", nie „čo je v konfigurácii".** Keď je
  odosielateľ fail-closed, jeho účinná množina pri pokazenom nastavení je prázdna.
- **`unknown` tu NIE JE bezpečná odpoveď.** Číslo sa dá spočítať; schovať ho práve vtedy,
  keď je najväčšie, je tá istá slepota v inom kabáte.
- **Pošli so sebou aj DÔVOD** (`config_broken`) a nechaj dialóg povedať, prečo je to číslo
  celý zoznam a nie rozdiel. Nevysvetlené veľké číslo si manažér preloží ako nadhodnotenie —
  a náhľad, ktorému neverí, je náhľad, ktorý preklikáva.
- **Brána sama nesmie zlyhať OTVORENE.** `if (!r.ok) return true` (a rovnako `catch`) spraví
  z potvrdenia pri 403/500/výpadku siete no-op — čiže presne to tiché prepustenie, proti
  ktorému brána vznikla. Nedá sa zistiť = pýtaj sa. A keďže náhľad vie siahnuť na stiahnutie
  exportu, ohranič čakanie (`AbortController`) a napíš do karty, že pracuješ — inak manažér
  pozerá na mŕtve tlačidlo.

### `ok: False` bez `raise` necháva `last_status` na „ok" — čítaj `last_result`, nie len ju (#299)

`AutomationRunner._execute` má JEDNU podmienku pre zlyhanie: `run_fn` VYHODÍ výnimku →
`last_status='error'`. Keď `run_fn` namiesto toho normálne VRÁTI `{"ok": False, "error": …}`
(napr. `run_shoptet_upload` pri „cyklus už beží" / „iný import práve beží" / nepotvrdené
či zablokované riadky), runner to zapíše ako `last_status='ok'` — presne tak, ako keby beh
prešiel čisto. Je to ten istý tvar ako `source_degraded` (beh, ktorý „neprešiel", ale
nevyhodil), len o úroveň nižšie: tu je celý VÝSLEDOK zlyhaný, nielen jeden jeho zdroj.

- **Karta aj bočný ⚠ odznak (`navError()`) musia čítať `last_result.ok`/`last_result.error`**,
  nikdy len `last_status`/`last_error` — kopírovanie `if (a.last_status === 'error' &&
  a.last_error)` z inej karty (napr. `renderShoptetSync`) na automatizáciu s týmto tvarom
  necháva TRI reálne zlyhania vyzerať ako zdravú hodinu, navždy.
- **Rozšírenie `navError()` o `(a.last_result || {}).ok === false` je bezpečné len vtedy, keď
  ŽIADNA iná automatizácia nepoužíva kľúč `"ok"` vo svojom výsledku s iným významom** — over si
  to `grep`-om cez všetky `run_*()` funkcie predtým, než zdieľanú funkciu takto rozšíriš.
  **Kolízia REÁLNE existuje — `run_image_health` má vo svojich `stats` tiež kľúč `ok`, ale
  je to POČET obrázkov, čo prešli kontrolou (`ok_n`, číslo), nie príznak (opravné kolo 1 k
  Tasku 7, #299 — pôvodná verzia tohto komentára tvrdila opak a bolo to nepravdivé). Kód je
  dnes bezpečný LEN vďaka striktnému `=== false` — číslo sa mu nikdy nerovná. Keby to niekto
  prepísal na pravdivostné porovnanie (`!a.last_result.ok`), `image_health` s `ok: 0`
  (žiadny obrázok neprešiel) by odznak zapol falošne. Pri KAŽDOM ďalšom `run_*()`, čo prejde
  cez tento wrapper, over si nielen prítomnosť kľúča `ok`, ale aj jeho VÝZNAM.
- **Test na to musí mutovať PRÁVE `last_status`, nie `last_result`** — fixtúra/`page.evaluate`
  nastaví `last_status: 'ok'` A ZÁROVEŇ `last_result.ok: false`, inak sa nedá odlíšiť „karta
  číta last_result" od „karta číta last_status a náhodou to vyšlo".

## 4. Hodnota, ktorá ide zákazníkovi do mailu — typuj ju, neprepisuj ju „fail-soft"

Pri `retainedTill` (#283) sa neznáma hodnota držala „radšej ukázať než zahodiť". To je pri DÁTUME
zlé hneď dvakrát a oboje sa ukázalo až v review:

- **Šablóna má natvrdo predponu.** „Prosím vyzdvihnite si ju **do** {hodnota}" spraví z čohokoľvek,
  čo nie je dátum, nezmysel priamo v maile zákazníkovi („do do odvolania", „do True").
- **Neparsovateľná hodnota obíde KAŽDÚ kontrolu, ktorá ju parsuje.** Strážca „termín už uplynul"
  používal ten istý `_parse_date`, takže `'20.07.2026'` (týždeň po termíne) prešiel ako budúci.

Pravidlá, ktoré z toho platia pre každé pole idúce do zákazníckeho textu:

1. **Fail-soft pri poli s typom = ZAHOĎ hodnotu a použi bezhodnotovú formuláciu** („čo najskôr"),
   nikdy neprepisuj neznámy reťazec. Bezhodnotová veta je vždy pravdivá; neznámy reťazec nie.
2. **Kontroluj to aj v builderi, nielen v čítačke.** `build_email` má druhého volajúceho — preview
   endpoint mu podáva hodnotu priamo z `posta_uncollected.json`, takže pokazený zápis staršieho
   behu by prešiel aj s opravenou čítačkou. A rob to BEZPODMIENEČNE (nie `if hodnota:`), aby typ
   z pokazeného storu nespadol v `escape()` a nezobral so sebou celý denný beh.
3. **Nezúž fail-soft na „čo nepoznám, to je prázdne".** ISO **basic** tvar (`99000803`) je platný
   dátum a `date.fromisoformat` ho číta jednoznačne — pripni ho testom, nech ho nikto nezahodí.
4. **Zákazník číta po slovensky: `3. 8. 2026`, nie `2026-08-03`.** ISO drž interne (tvar API,
   JSON store, tabuľka na webe) a prekladaj až pri renderovaní mailu. Pozor na tichý prípad: kým
   bolo pole vždy prázdne, ISO tvar sa nikdy reálne neodoslal — „veď to tak bolo vždy" neplatí.

**A ešte jedno, z toho istého review:** alarm musí VRACAŤ to číslo, na ktorom sa spúšťa.
`dispatched_status_unknown` sa spúšťal na počte eligible objednávok, ale ten sa nevracal — log si
ho preto aproximoval (`missing_package + dispatched_orders`) a v jedinej vetve, ktorá vôbec
nastane, to spadlo na nulu: „v okne je 0 objednávok, ale ANI JEDNA nemá stav Vybavená". Keď
pridávaš príznak, pridaj vedľa neho aj surové číslo, ktorým sa dá napísať pravdivá veta.

## 5. Tichá smrť má zrkadlo: tiché ROZŠÍRENIE (#297)

Bod 3 stráži automatizáciu, ktorá prestane robiť čokoľvek. Rovnako drahý je opačný smer:
jedno nastavenie, ktoré ticho ROZŠÍRI množinu ľudí, ktorým niečo odíde. `to_order` vedie
záložku, „Nedostupné" AJ pripomienkové maily (zámerne — jedna predstava o „otvorenej"
objednávke, nie štyri), takže pridanie stavu spraví zo VŠETKÝCH objednávok v ňom starších
než 4 dni okamžite mailovateľné. Dedup store zastaví až DRUHÝ mail, prvú vlnu nikdy.
Namerané nad živým exportom: pridanie `Vybavená` = 387 objednávok, z toho 250 s poznámkou
aj adresou = **237 rôznych zákazníkov** naraz, pod kartou, ktorá odpovie „✅ Uložené".
(370 je počet rôznych adries BEZ filtra „má poznámku" — čiže presne to nadhodnotenie,
proti ktorému tento bod píše „rozlíš, koľko toho pribudne, od toho, koľkým to reálne
môže odísť". Revízia PR #298, B-F2: aj tento playbook to číslo raz uviedol zle.)

Keď nastavenie rozhoduje o tom, komu sa niečo POŠLE, ukáž dôsledok PRED uložením:

- **Číslo si vypýtaj pre KANDIDÁTA, nie pre uložený stav** — samostatný read-only endpoint,
  ktorý dostane navrhovanú množinu a nič nezapisuje.
- **Rozlíš „koľko toho pribudne" od „koľkým to reálne môže odísť".** Horná hranica vlny je
  užšia množina (má poznámku, má adresu, nie je už vybavená v evidencii) a nikdy nebude
  presná — o odoslaní rozhoduje až AI klasifikátor. Náhľad, ktorý nadhodnocuje, je náhľad,
  ktorý sa prestane čítať; rátaj RÔZNYCH zákazníkov, nie objednávky.
- **Keď sa to nedá spočítať, pýtaj sa TAKISTO.** Nula na nulovom dôkaze zmenu potichu
  prepustí — to je presne to, proti čomu náhľad vznikol.
- **Keď zmena nikoho nového nezasiahne, MLČ.** Dialóg pri každom uložení sa preklikáva bez
  čítania a zoberie so sebou aj ten, ktorý niečo znamená (rovnaká logika ako „trvalý banner"
  v store-prune §7). Nechaj v testoch KONTROLU na túto vetvu — musí prejsť pred aj po oprave,
  inak sa záplata „nikdy nezobrazuj dialóg" nedá odlíšiť od opravy.
- Nič nezakazuj. Cieľ je, aby sa to nedalo spraviť omylom — nie aby sa to nedalo spraviť.

## 6. Tabuľka čakajúcich zmien — kredit AŽ po potvrdení, nikdy pri zaradení (#299)

Piatim producentom (`parovania_eshop`, `grube_externalcode`, `split_links`, `restock_skladom`,
`stock_skladom`) pribudla spoločná tabuľka `data/out/pending_shoptet.json`
(`src/parovanie/shoptet_outbox.py`): producent už do eshopu NEPÍŠE, len ZARADÍ polia; hodinový
`shoptet_upload` postaví jeden import, overí ho z Logu a AŽ POTOM zapíše dedup kredit
(`_credit_producer`, `webreview/app.py`). To je priama oprava #257 (kredit pred potvrdením) —
ale rovnaká trieda chyby sa dá spraviť aj OKOLO tohto pravidla, nielen jeho porušením:

- **Kredituj presne ten TVAR hodnoty, ktorý dedup POROVNÁVA, nie tú, ktorú vidí eshop.**
  `_do_upload_variant_links` kreditoval normalizovanú GRUBE `.de` URL (to, čo ide do importu),
  kým `new_variant_link_keys` porovnáva SUROVÚ hodnotu z `variant_links.json` — tie dve sa
  nikdy nestretli, takže riadok sa nikdy neoznačil za nahraný a posielal by sa do eshopu KAŽDÝ
  hodinový cyklus navždy, s `total_uploaded` zamrznutým na nule (#299 Task 8 review C1). Keď
  pridávaš `credit_value`, over ho proti tomu istému poľu, ktoré incremental-check číta — nie
  proti poľu, ktoré ide do CSV.
- **Kód, ktorého niektorý variant katalóg nemá, sa nesmie kreditovať ako súčasť skupiny.**
  `settle()` kredituje skupinu len keď `g["codes"] <= success_codes` — jeden `blocked` kód v
  skupine zadrží kredit CELEJ skupiny navždy, nikdy len svoje pole. Je to zámerne fail-closed
  (mierne draho — zvyšok skupiny čaká), nie fail-open (lacno, ale ticho zapíše kredit za kód,
  ktorý sa v skutočnosti nikdy neposlal).
- **Zablokovaný riadok (`blocked`) sa NIKDY nezahadzuje** — ostáva v tabuľke a je vidieť v
  karte (#270, #301) — **ale musí mať strop, po ktorom kričí.** `stale_blocked()` sleduje
  `blocked_runs` (počet PO SEBE IDÚCICH blokovaných behov, resetne sa len keď riadok naozaj
  vyjde z `blocked`), nikdy `attempts` (ten rastie pri KAŽDOM nepotvrdenom behu, aj keď riadok
  blokovaný nebol) — inak by sa dlho čakajúci blokovaný riadok nedal odlíšiť od riadku, ktorý
  bol chvíľu len nepotvrdený.
- **Producent zapnutý, ktorý N behov po sebe nezaradí nič DO SPOLOČNEJ FRONTY, nie je dôkaz
  zamrznutého zdroja — meraj to z JEHO VLASTNÉHO rozvrhu, nikdy z prázdnoty fronty.** Prvá
  verzia tohto poplachu (`_note_empty_producers`, zrušená v opravnom kole 1) počítala „3
  hodinové cykly po sebe s 0 poľami" — a keďže producenti bežia DENNE a fronta sa vyprázdňuje
  HODINOVO, to je normálny stav KAŽDÉHO zdravého producenta, nie symptóm (namerané: pískalo na
  3. cykle pre 4 z 5 producentov na úplne zdravom systéme). **Trvalý poplach = žiadny poplach**
  — meraj namiesto toho, čo poplach naozaj potrebuje vedieť: `_stale_producer_warnings` číta
  `RUNNER.status()` (`enabled` + `last_run` proti VLASTNÉMU `interval_minutes`/`daily_at` toho
  producenta), nikdy obsah frontu. Rovnaký princip ako automation-health §5's "signál sformuluj
  cez to, čo alarm potrebuje na PRÁCU, nie cez tvar vedľajšieho javu".
- **Poistka „fronta rastie, ale cyklus, ktorý ju drénuje, je vypnutý" sa NESMIE počítať z
  výsledku toho cyklu** — vypnutý cyklus nikdy nebeží, takže vlastný `last_result` by mlčal
  navždy (poistka, ktorá stráži samu seba). `_queue_stale_while_disabled_warning` sa preto
  počíta na KAŽDOM `/api/automations` polli, priamo z `RUNNER.status()`-ovho `enabled` a z
  timestampov VO FRONTE — nikdy z toho, čo `run_shoptet_upload` napísal do svojho výsledku.
- **Alarm, ktorý sa dá umlčať, je horší než žiadny.** `queued_at` sa nesmie posunúť pri
  bežnom RE-zaradení tej istej hodnoty (`parovania_eshop` beží denne a znovu pošle celý svoj
  zoznam, kým to vypnutý cyklus nepotvrdí) — inak by opakovaný beh producenta reštartoval hore
  uvedenú „ako dlho čaká" poistku na nulu pri KAŽDOM svojom tiku, a poplach na trvalo vypnutý
  cyklus by nikdy nenaskočil. Nový timestamp dostane len GENUINE nová/zmenená hodnota
  (`queue_fields`, rovnaká disciplína ako `settle()`-ova `since` pri `blocked`).
- **Orchestrátor, ktorý „pre pohodlie" spustí aj svoje komponenty, im ticho zmení VLASTNÝ
  rozvrh.** Prvá verzia hodinového cyklu spúšťala aj producentov, čím by z `parovania_eshop`
  (dnes 1×/deň o 21:00, jediný živý zápis) spravila beh 24×/deň — vrátane deštruktívneho
  prepisovania manuálne priradených dodávateľov 24× namiesto 1×. Cyklus preto NIKDY nespúšťa
  producentov (`run_shoptet_upload` len sťahuje/nahráva/overuje/vyprázdňuje frontu); každý
  producent beží výhradne na svojom vlastnom rozvrhu.
- **Dve poistky nad JEDNÝM timestampom sa vylúčia — rozdeľ ho.** Poplach „ako dlho to už čaká"
  potrebuje čas ZMRAZENÝ (inak ho bežné re-zaradenie umlčí, viď bod vyššie), ale brána „neposielaj
  staré rozhodnutie" potrebuje čas OBNOVOVANÝ (inak sa pole, ktoré raz prekročí prah, odmieta
  NAVŽDY — producenti bez dedupu zaraďujú tú istú konštantu denne a nikdy ho neomladia; namerané
  probe: 10 dní denných re-zaradení, `held: True` v každom behu). Preto `first_queued_at`
  (zmrazený, číta ho poplach) + `queued_at` (obnovovaný, číta ho veková brána). Starý záznam bez
  `first_queued_at` padá späť na `queued_at` a pri najbližšom zaradení ho zdedí — migrácia bez
  tichého zhasnutia poplachu.
- **Prah poplachu MUSÍ pripínať test s LITERÁLNOU hodnotou** (`assert X == 24*3600`), nie test,
  ktorý si vek z tej istej konštanty odvodí (`X + 3600`) — taký je voči jej zmene slepý. V #299
  to zlyhalo TRIKRÁT: mutácia prahu na 10× (resp. 1000 dní) nechala 125, resp. 159 testov zelených.
  Poistka bez pripnutého prahu je poistka, ktorú vie ktokoľvek ticho vypnúť jedným číslom.
- **Popis poistky, ktorý nie je doslova pravdivý, je horší než žiadny.** Zrušená úniková vetva
  v #299 mala tri komentáre, ktoré tvrdili, že sa kód „od tohto behu neposiela" a že „`stale_blocked`
  na to o 3 behy upozorní" — ani jedno neplatilo (vetva sa každý druhý beh sama zrušila). Keď rušíš
  polovične fungujúcu poistku, ZRUŠ aj jej texty a over, čo je NÁHRADNÁ záruka — v #299 ňou nie je
  `stale_blocked` (nepotvrdený riadok sa nikdy nestane `blocked`), ale `ok=False` + `degraded` + ⚠.
