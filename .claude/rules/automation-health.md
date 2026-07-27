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

Tri veci, ktoré tento repozitár už stáli reálnu škodu. Prečítaj ich skôr, než pridáš alebo
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

**Každý takýto stav si zaslúži E2E test.** Celý prínos alarmu je to, ČO manažér uvidí; jednotkový
test na `stats` dokazuje len polovicu. Vzor: fixture server so zaseknutým `last_result`
(`posta_degraded_server` v `tests/e2e/conftest.py` — nový server treba pridať aj do
`_SERVER_FIXTURES`), potom skontroluj text karty, banner aj odznak. Práve tento test odhalil
nefunkčný odznak vyššie.

Alarm musí ostať **iba počítadlom**: nesmie nič odoslať ani rozšíriť množinu zásielok, ktoré idú
do eskalácie — pripni to testom (`test_posta_source_alarm_never_widens_what_gets_mailed`). Pri
Pošte je to kritické: keď sa zdroj opraví, 130 doteraz neviditeľných zásielok sa objaví naraz.
