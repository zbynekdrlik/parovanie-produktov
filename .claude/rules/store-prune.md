---
paths:
  - "webreview/app.py"
  - "tests/test_webreview_flag_prune.py"
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
#   closed = seen - still_open      ← objednávka JE v exporte a už nie je „Vybavuje sa"
#   NIE:    not in still_open       ← zmazalo by aj všetko mimo 90-dňového okna
```

Tá formulácia navyše dáva **odolnosť voči useknutiu zadarmo**: `statusName` je stav CELEJ
objednávky a je na každom jej riadku, takže useknutý export vie riadky iba ubrať — ubraná
objednávka zmizne, a čo zmizlo, sa nemaže. Poškodený zdroj tak vie prune len ZÚŽIŤ.
(Merateľne: pri #212 to bol rozdiel 4 kľúčov, ktoré by verzia „not in still_open" zmazala.)

Ten istý tvar má aj starší `_prune_orphan_decisions` — „NEVER prunes against an EMPTY
product list", lebo prázdny `PRODUCTS` by označil za osirelé úplne všetko.

## 1b. „Nič nie je otvorené" NIE JE odpoveď — je to signál, že čítaš iný zdroj

Bod 1 odvodzuje zaniknuté ako `videné − stále_otvorené`. Tá formulácia má dieru, ktorú
prah z bodu 2 NECHYTÍ a `protect` guard tiež nie (prune je legitímny read-modify-write):
**čokoľvek, čo vyprázdni `stále_otvorené`, spraví zo VŠETKÉHO v okne „zaniknuté".**

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
druhý raz. Zmazanie preto vyžaduje aj VEK: `ORDERS_PRUNE_MIN_AGE_DAYS` (30 dní) z dátumu
objednávky, ktorý export aj tak nesie — žiadny nový store, nič, čo zastará.

Skontroluj, že odklad a okno zdroja spolu **nenechajú kľúč trčať**: 30 dní odkladu proti
90-dňovému oknu exportu = 60 dní hodinových behov, počas ktorých je každý kľúč ešte
dosiahnuteľný. A **neznámy vek nie je „dosť starý"** — neprečítateľný dátum sa nemaže.

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
  lebo „veď to nič nezmazalo".
