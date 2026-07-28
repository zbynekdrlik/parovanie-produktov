---
paths:
  - "webreview/app.py"
  - "tests/test_webreview_flag_prune.py"
  - "tests/test_webreview_shoptet_sync.py"
  - "tests/test_store_safety.py"
  - "tests/test_store_concurrency.py"
---

# Mazanie z manažérových úložísk — pravidlá, ktoré platia pre KAŽDÝ prune

`data/out/*.json` je živá práca manažéra. Zvyšok repozitára rieši, ako do nej bezpečne
ZAPISOVAŤ (#261/#264/#265). Toto je o tom, ako z nej niečo UBRAŤ — jediná operácia, pri
ktorej sa chyba nedá vrátiť. Prečítaj skôr, než napíšeš čokoľvek, čo maže kľúče.

## 1. Maž na POZITÍVNY dôkaz, nikdy na „nevidím to"

Prune sa vždy pýta zdroja (export, katalóg, zoznam produktov), ktorý má **okno**. „Nie je
v zdroji" preto spája dve úplne odlišné veci: *naozaj to zaniklo* a *len to nevidíme*
(mimo okna, useknutý download, zlyhaný fetch degradovaný na `[]`).

Formuluj podmienku tak, aby na mazanie bolo treba dôkaz, že vec zanikla:

```python
# #212, ordered/waiting/instock/unavailable (kľúč '<orderCode>|<itemCode>')
gone = [k for k in d if "|" in k and k.split("|", 1)[0] in closed]
#   closed = seen - unfinished      ← objednávka JE v exporte a KAŽDÝ jej riadok nesie stav
#                                     z ORDERS_TERMINAL_STATUSES (pozitívny dôkaz, bod 1a)
#   NIE:    not in still_open       ← zmazalo by aj všetko mimo 90-dňového okna
#   NIE:    seen - still_open       ← „všetko, čo nie je ten otvorený literál" = neznámy
#                                     stav sa počíta ako zaniknutý (bod 1a, blokujúca 🔴)
```

Tá formulácia navyše dáva **odolnosť voči useknutiu zadarmo**: `statusName` je stav CELEJ
objednávky a je na každom jej riadku, takže useknutý export vie riadky iba ubrať — ubraná
objednávka zmizne, a čo zmizlo, sa nemaže. Poškodený zdroj tak vie prune len ZÚŽIŤ.
(Merateľne: pri #212 to bol rozdiel 4 kľúčov, ktoré by verzia „not in still_open" zmazala.)

Ten istý tvar má aj starší `_prune_orphan_decisions` — „NEVER prunes against an EMPTY
product list", lebo prázdny `PRODUCTS` by označil za osirelé úplne všetko.

## 1a. „Pozitívny dôkaz" musí platiť aj pre STAV, nielen pre PRÍTOMNOSŤ

Bod 1 ustráži, že sa nemaže to, čo v zdroji NEVIDÍME. Nič v ňom ale nebráni tomu, aby sa
zaniknutosť odvodila NEGATÍVNE z druhej strany — `closed = videné − otvorené`, kde
„otvorené" je členstvo v JEDNOM zapečenom literáli. Tá formulácia vyhlási za skončené
**čokoľvek, čo nie je ten literál** — teda aj stav, ktorý kód nikdy nevidel. Je to presne
tá istá chyba ako „nevidím to, tak to zmažem", len o jednu os ďalej.

Strážca z bodu 1b (odmietni, keď je otvorených NULA) chytá len ÚPLNÉ premenovanie.
**ČIASTOČNÁ zmena cezeň prejde:** premenovanie, pri ktorom jedna objednávka ostane na
starom literáli, alebo pridanie nového otvoreného stavu. Otvorených je vtedy „nejako viac
ako nula", takže všetko vyzerá zdravo — a zmažú sa značky živých objednávok. Namerané na
#212: taký scenár zmazal **94 kľúčov** z objednávok, ktoré sú stále otvorené
(`176/12/13/16 → 99/6/9/9`).

**Pravidlo: maž na členstvo v EXPLICITNOM zozname koncových stavov, nikdy na „všetko, čo
nie je ten otvorený literál".** Neznámy alebo novo pridaný stav tak automaticky znamená
NEUKONČENÉ a značky prežijú. Zoznam nehádaj — over ho na živých dátach:

- Živý export nie je dvojstavový. Pri #212 niesol **deväť** stavov: `Vybavená` 387,
  `Stornovaná` 63, `Vybavuje sa` 57, `Vybavená výmena` 4, `Osob. odber` 3,
  `Vybavený Dobropis` 3, `Kompletná` 2, `Vratený tovar` 1, `Výmena tovaru` 1. `Osob. odber`
  aj `Výmena tovaru` sú ŽIVÉ stavy a staré pravidlo ich považovalo za skončené.
- Hľadaj DVA nezávislé signály, ktoré sa zhodujú. Tu to bola menná konvencia obchodu
  (dokončenie má predponu `Vybavená/Vybavený` — `Vybavená výmena` vs `Výmena tovaru`) a
  podacie číslo, čiže „tovar naozaj odišiel": `Vybavená` 250/387, `Vybavená výmena` 4/4,
  `Vybavený Dobropis` 3/3 — proti `Kompletná` 0/2, `Vratený tovar` 0/1, `Osob. odber` 0/3.
- **Pri pochybnosti stav na zoznam NEDÁVAJ.** Asymetria je zdrvujúca: vynechaný stav stojí
  pár kľúčov, ktoré chvíľu ostanú; mylne zaradený stav zmaže nenahraditeľnú prácu. Doplniť
  stav neskôr s dôkazom sa dá, zmazanie sa vrátiť nedá.
- **Zoznam sa nesmie zúžiť potichu.** Beh vráti a zaloguje stavy, ktoré NEPOZNÁ, a karta ich
  ukáže — ako informáciu, nie ako poplach (sú legitímne a trvalé, banner by bol večný šum).
- **„Nepoznám" znamená naozaj NEPOSÚDENÝ.** Veď si aj druhý zoznam — stavy, ktoré si vážil a
  vedome nechal mimo koncových (`ORDERS_KNOWN_OPEN_STATUSES`). Bez neho hlási signál trvale
  tie isté štyri očakávané hodnoty a JEDINÝ prípad, pre ktorý vznikol — naozaj nový stav —
  sa v tom šume stratí. Prázdny stav nie je „falsy, teda ignoruj": je to NEČITATEĽNÝ stav,
  daj mu meno (`(prázdny stav)`), inak zúži prune bez stopy.
- **Ohranič ho.** Stav je needitovaný CSV stĺpec — pri posunutom exporte v ňom je ľubovoľný
  text z riadku, ktorý potom loguješ, ukladáš do `automations.json` a vykresľuješ na karte.
  Strop na počet aj na dĺžku, nech pokazený zdroj nevie natrvalo vysypať svoj obsah (možno aj
  zákaznícke dáta) do manažérovho úložiska.

### A test na ÚPLNÉ premenovanie NEPOKRÝVA čiastočné

`test_an_export_with_NO_open_orders_at_all_prunes_nothing` bol zelený celý čas — premenoval
stav vo VŠETKÝCH riadkoch, čím trafil presne ten strážca, ktorý už existoval. Dieru otvorí
až export, kde je otvorených „nejako viac ako nula". Keď píšeš negatívny test na strážcu,
napíš aj jeho ČIASTOČNÚ verziu; inak testuješ vetvu, ktorú si už ošetril.
Vzor: `test_a_status_the_shop_ADDS_or_renames_into_is_never_pruned` a tabuľkový
`test_only_a_status_that_MEANS_finished_prunes_its_keys` nad všetkými deviatimi stavmi.

## 1b. „Nič nie je otvorené" NIE JE odpoveď — je to signál, že čítaš iný zdroj

Odkedy platí bod 1a, je táto kontrola opasok k trakám — zaniknutosť sa už neodvodzuje od
množiny otvorených, takže samotné premenovanie stavu z toho wipe spraviť nevie. Ostáva
preto, že je to najlacnejší signál, že sa zdroj pod nami zmenil, a že pomenuje poruchu
namiesto toho, aby beh vyzeral zdravo a nemazal nič. (Pôvodná diera, kým sa počítalo
`videné − stále_otvorené`: čokoľvek, čo vyprázdnilo množinu otvorených, spravilo zo VŠETKÉHO
v okne „zaniknuté" — a prah z bodu 2 to NECHYTÍ, ani `protect` guard, lebo prune je
legitímny read-modify-write.)

Reálne spúšťače, oba lacné:

- **Premenovaný stav.** Stavy objednávok v Shoptete sú konfigurovateľný TEXT a literál
  `"Vybavuje sa"` je v tomto repe zapečený na viacerých miestach. Premenovanie v admine =
  nula otvorených.
- **Iný / zmenený export.** Chýbajúci stĺpec `statusName` (zmenená šablóna, alebo URL v
  creds prehodená na iný export, ktorý má tiež stĺpec `code`) → každý riadok číta `None`.

Preto: **over stĺpec v HLAVIČKE** (skôr, než posúdiš čo i len jeden riadok) a **odmietni
prune, keď je množina otvorených PRÁZDNA**. Na zdravom feede je to nemožné (namerané:
57 otvorených z 521), takže to nie je tichý týždeň — je to iný zdroj. Obe odmietnutia
pomenuj ZVLÁŠŤ (`no-status-column` vs `no-open-orders` vs `unparsable-source`): operátora
posielajú na iné miesto.

## 1c. Zánik ≠ koniec — nechaj odklad, lebo veci sa VRACAJÚ

Objednávka „Vybavená" sa môže vrátiť do „Vybavuje sa" — tento repozitár si to sám píše
tam, kde dedup store pripomienok vysvetľuje, prečo záznamy DRŽÍ. Keď prune zmaže značky
v tú istú hodinu, riadok sa vráti bez manažérovho „objednané u dodávateľa" a objedná sa
druhý raz. Zmazanie preto vyžaduje aj VEK: `ORDERS_PRUNE_MIN_ORDER_AGE_DAYS` (30 dní) z dátumu
objednávky, ktorý export aj tak nesie — žiadny nový store, nič, čo zastará.

Skontroluj, že odklad a okno zdroja spolu **nenechajú kľúč trčať**: 30 dní odkladu proti
90-dňovému oknu exportu = 60 dní hodinových behov, počas ktorých je každý kľúč ešte
dosiahnuteľný. A **neznámy vek nie je „dosť starý"** — neprečítateľný dátum sa nemaže.

**Pomenuj konštantu podľa toho, čo MERIA, nie podľa toho, čo si ňou chcel dosiahnuť.**
Odklad má bežať od ZATVORENIA, lenže dátum v exporte je dátum VYTVORENIA objednávky (66
stĺpcov a ani jeden so zmenou stavu či poslednou úpravou). Objednávka vytvorená pred 31
dňami a zatvorená dnes sa teda zmaže hneď pri najbližšom behu, s NULOVÝM odkladom — a to
práve pri dlhom čakaní na dodávateľa, čiže presne tam, kde tie značky najviac chýbajú. Kým
to tak je, nevydávaj vek objednávky za odklad po zatvorení: konštanta sa volá podľa merania
(`ORDERS_PRUNE_MIN_ORDER_AGE_DAYS`), komentár povie, čo dať NEVIE, a skutočné riešenie
(store s dátumom prvého videného zatvorenia) má vlastný ticket (#294). Konštanta pomenovaná
podľa zámeru klame o bezpečnosti, ktorú kód nemá — a klame práve toho, kto sa na ňu spoľahne
pri ďalšej zmene.

## 2. Fail-closed prah na ZDROJ, aj keď pravidlo z bodu 1 už drží

Opasok k trakám, rovnaký tvar ako `EXPORT_MIN_CODES` pri katalógu: keď zdroj nesie
implauzibilne málo záznamov, **nemaž nič a zaloguj varovanie**. Prah kalibruj na REÁLNEJ
histórii, nie od oka, a napíš do kódu, z čoho vznikol (`ORDERS_PRUNE_MIN_ORDERS = 50` proti
nameraným 521 objednávkam v 90-dňovom okne). Absolútny prah, nie pomer z dávky — pomer sa
odzbrojí, keď dávku tvoria už len odsúdené riadky (#270).

### Useknutie: pravidlo z bodu 1 platí pre všetky riadky OKREM toho, v ktorom je rez

„Useknutý export vie riadky iba ubrať" je pravda pre celé riadky. Riadok, do ktorého rez
padne, ale `csv.DictReader` vráti — s useknutým alebo chýbajúcim stavom, takže naozaj
OTVORENÁ objednávka číta ako zaniknutá. Telo, ktoré nekončí novým riadkom, je neúplné z
definície: **posledný riadok zahoď.**

A **`csv.Error` nie je ani `ValueError`, ani `OSError`.** Holý CR v neuvodzovkovanom poli
(a `errors="replace"` zaručí, že sa k parseru dostane hocijaká bajtová kaša) ho vyhodí,
a keď ho nechytíš, prejde cez housekeeping `except` volajúceho a zhodí celý hodinový beh —
presne to, čo ten `except` sľubuje, že sa stať nemôže. Chytaj ho na oboch miestach.

## 3. Mechanika, bez ktorej sa `protect=True` odzbrojí

- Celý read-modify-write v JEDNOM `with _lock:` (medziprocesový, #264).
- **Maž IN-PLACE z načítaného objektu** (`d.pop(k)`), potom `save(d)`. Zápis je tým
  legitímnym chvostom read-modify-write a shrink-guard ostáva ozbrojený. Ak staviaš NOVÚ
  mapu, musíš pridať `prev=d0` (tak to robí `_prune_orphan_decisions`) — inak `StoreWipeRefused`.
- **Keď nie je čo zmazať, súbor NEZAPISUJ.** Naprázdno prepísaný `protect=True` store spáli
  účtenku z čítania a mení súbor, o ktorý nikto nežiadal (rovnaké pravidlo ako
  `_write_status_flag`). Pripni to testom nad bajtmi aj `st_mtime_ns`.
- **Kľúč, ktorý sa nedá priradiť** (chýba `|`, prázdny `orderCode`), sa NEPOSUDZUJE.
- **Zaloguj konkrétne kľúče**, nielen počet — o tri týždne sa z počtu nedá nič obhájiť.
- Volaj to ako **housekeeping v try/except** (`StoreLockTimeout, StoreWipeRefused, OSError,
  ValueError`, **a `csv.Error`** — viď vyššie): zlyhaný prune nesmie zhodiť beh, ktorý ho
  hostí. Než ten `except` napíšeš, over si, že typy, ktoré parser naozaj hádže, sú v ňom.

## 4. Prune patrí k ČERSTVÉMU fetchu, nie na čítaciu cestu

Vešaj ho tam, kde práve prišli nové bajty (`run_shoptet_sync`), nie do `/api/*` GET.
Zápis na čítacej ceste robí z „prečítaj export" mutáciu a nechal by o mazaní rozhodovať
ľubovoľne starú kópiu na disku — presne preto nie je `_export_watermark_observe` schovaný
v čítačke. A keď automatizácii pribudne mazanie, **oprav jej docstring**: `run_shoptet_sync`
roky tvrdil „never touches the manager decision stores".

## 5. Než to pustíš na živé dáta: SUCHÝ BEH nad KÓPIOU

Skopíruj `data/out/*.json` do tmp, ukáž tam `WEBREVIEW_OUT` a pusti **skutočnú** funkciu.
Potom over `md5sum`-om, že originály sú bajt na bajt nedotknuté, a nahlás počty pred/po.

```bash
PYTHONPATH=src WEBREVIEW_OUT=/tmp/kopia/out WEBREVIEW_NO_SCHEDULER=1 \
  WEBREVIEW_PRODUCTS=/tmp/kopia/products.csv .venv/bin/python -c '...'
```

Skript si na začiatku **overí, kam OUT ukazuje** (`assert "/kopia/" in os.fspath(webapp.ORDERED)`)
— `_refuse_live_data_under_pytest` mimo pytestu nechráni nič. Bež cez `.venv/bin/python`
+ `PYTHONPATH=src`; systémový `python3` nemá `requests` ani `parovanie`.

## 6. Otestuj hlavne to, čo prune NESMIE

Testy tejto triedy sú prevažne negatívne: krátky zdroj nezmaže nič, prázdny/nečitateľný
zdroj nezmaže nič, useknutý zdroj zmaže MENEJ, nepriraditeľný kľúč prežije, nezmenený store
sa neprepíše. Každú z tých vlastností over MUTÁCIOU (dočasne ju v kóde zruš a pozri, ktorý
test spadne) — negatívny test, ktorý by prešiel aj bez opravy, nedokazuje nič.
Vzor: `tests/test_webreview_flag_prune.py`.

## 7. Odmietnutie musí byť VIDIEŤ, inak si vymenil rast za ticho

Body 1b/2 hovoria „radšej nemaž nič". Lenže „ani jedna otvorená" / „chýbajúci stĺpec" sú
TRVALÉ stavy: kým sa zdroj neopraví, prune sa nevykoná ani raz a úložiská rastú presne ako
predtým — s jediným riadkom v logu. To je „tichá smrť automatizácie" z
`.claude/rules/automation-health.md` bodu 3, len z druhej strany.

- **Odmietnutie vracia ČÍSLO, na ktorom sa spustilo** (koľko objednávok zdroj niesol,
  koľko otvorených) — nie holé `0`. Bez neho sa nedá napísať veta pre človeka.
- **Každý trvalý dôvod pomenuj zvlášť** a prejdi všetky štyri kroky z automation-health §3
  (stat → ERROR log → červený banner v karte → `navError()` odznak), vrátane E2E.
  Pri #212 to zostalo ako #293 — a to je práve tá časť, ktorú je najľahšie zabudnúť,
  lebo „veď to nič nezmazalo". Uzavreté v tej istej PR, s tromi vecami, ktoré sa oplatí
  zopakovať:
  - **Prihlás sa do EXISTUJÚCEJ degradovanej cesty, nezakladaj druhú.** Beh nastaví
    `source_degraded`, na ktorý sa `navError()` už pýta — odznak sa rozsvieti bez ďalšej
    vetvy, ktorú by musela každá nová automatizácia trafiť (tak vznikla chyba
    `autoByKey('posta')`, `automation-health.md` §3).
  - **Vypisuj aj ÚSPEŠNÝ výsledok, nielen odmietnutie** („vyčistené osirelé značky: 0").
    Bola to práve NEPRÍTOMNOSŤ toho riadku, vďaka ktorej trvalé odmietnutie vyzeralo úplne
    rovnako ako zdravá hodina.
  - **Odlíš POPLACH od INFORMÁCIE.** Neznáme stavy (bod 1a) sú legitímne a trvalé, takže
    idú ako pokojný riadok; červený banner patrí odmietnutiu, ktoré treba ísť opraviť.
    Trvalý banner sa prestane čítať a zoberie so sebou aj ten, čo niečo znamená.
