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

Alarm musí ostať **iba počítadlom**: nesmie nič odoslať ani rozšíriť množinu zásielok, ktoré idú
do eskalácie — pripni to testom (`test_posta_source_alarm_never_widens_what_gets_mailed`). Pri
Pošte je to kritické: keď sa zdroj opraví, 130 doteraz neviditeľných zásielok sa objaví naraz.
