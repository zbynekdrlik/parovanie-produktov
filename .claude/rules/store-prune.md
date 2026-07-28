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

## 2. Fail-closed prah na ZDROJ, aj keď pravidlo z bodu 1 už drží

Opasok k trakám, rovnaký tvar ako `EXPORT_MIN_CODES` pri katalógu: keď zdroj nesie
implauzibilne málo záznamov, **nemaž nič a zaloguj varovanie**. Prah kalibruj na REÁLNEJ
histórii, nie od oka, a napíš do kódu, z čoho vznikol (`ORDERS_PRUNE_MIN_ORDERS = 50` proti
nameraným 521 objednávkam v 90-dňovom okne). Absolútny prah, nie pomer z dávky — pomer sa
odzbrojí, keď dávku tvoria už len odsúdené riadky (#270).

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
  ValueError`): zlyhaný prune nesmie zhodiť beh, ktorý ho hostí.

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
