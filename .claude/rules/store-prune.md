---
paths:
  - "webreview/app.py"
  - "tests/test_webreview_flag_prune.py"
  - "tests/test_webreview_prune_grace.py"
  - "tests/test_webreview_parovania_eshop.py"
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

### Prázdny store nie je dôkaz — over, či sa naozaj PREČÍTAL (revízia PR #295)

Bod 1 hovorí „maž na pozitívny dôkaz". Existuje ale druhý, tichší spôsob, ako sa dá dôkaz
predstierať: podmienka `c not in <evidencia>` nad úložiskom, ktoré `_read_json_store`
degradoval na `{}`. Chýbajúci, poškodený aj zle otypovaný súbor vrátia to isté ako
legitímne prázdny — a `{}` spraví z „toto sme nikdy nezapísali" pravdu o KAŽDOM kľúči.
Celý store odsúdený v jednom behu, na nulovom dôkaze.

Nie je to teória: `uploaded_suppliers.json` na živom stroji NEEXISTUJE, takže #215 bolo
jedno objavenie sa kódu v exporte od vymazania všetkých priradení.

- **Loader musí vedieť povedať, ODKIAĽ hodnota je**: `_read_json_store_state(path, default)`
  vracia `(value, from_disk)` a `_read_json_store` je jeho prvý prvok (jeden čítač, žiadna
  skopírovaná logika). `from_disk=False` na KAŽDEJ degradovanej vetve.
- **Mazacia vetva sa pýta na `from_disk` a bez neho sa NEVYKONÁ** — kandidátov nahlás
  (`obsolete_held`, na každej návratovej vetve) a zaloguj ERROR. Prázdny súbor NA DISKU je
  naopak plnohodnotný dôkaz, takže pravidlo po prvom úspešnom behu funguje ďalej.
- **Testová predpríprava**: fixtúra, ktorá takú vetvu testuje, musí evidenciu na disk
  naozaj položiť (`_save_uploaded_suppliers({})`). Test, ktorý prešiel bez nej, testoval
  fail-open.

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
- **Od #209 ten zoznam NIE JE v kóde — je to nastavenie.** Názvy stavov si obchod v Shoptete
  edituje, takže `order_statuses.json` nesie tri množiny (`to_order` / `terminal` /
  `known_open`) a konštanty v `app.py` sú už len ich PREDVOLBY. Čo z toho platí, keď na tom
  robíš:
  - Čítaj cez `_order_statuses()` a **rozhoduj sa PER VOLANIE** — modulový dict by zamrzol
    pri importe a zmenu by uvidel až po reštarte (tá istá pasca ako `_line_flag_stores`).
  - **Nepoužiteľná množina padá na predvolbu, nikdy na prázdnu.** Prázdne `to_order`
    vyprázdni záložku, „Nedostupné" aj pripomienky zákazníkom; prázdne `terminal` potichu
    odzbrojí prune. Súbor, ktorý sa nedá prečítať, nie je povolenie vymyslieť si správanie.
  - **ALE: „nič nie je nastavené" a „nastavené je, len sa to nedá prečítať" musia dostať INÉ
    odpovede** (revízia PR #295). `_read_json_store` vráti na oboje `{}`, takže ich rozlíši
    len existencia súboru. Prvé je čerstvá inštalácia → predvolby. Druhé znamená, že
    nevieme, čo manažér rozhodol — a vrátiť tam zabudovaný `terminal` zoznam znovu OZBROJÍ
    prune presne na stavoch, ktoré možno zámerne vyhodil (karta mu pritom píše, nech ten
    zoznam zužuje, len keď si je istý). To je fail-OPEN na mazaní. Preto: sety sa vrátia
    (záložka musí niečo vykresliť), ale beh dostane dôvod `bad-status-config`, prune
    odmietne a karta ukáže červený banner.
  - **Kontroluj prienik VŠETKÝCH troch zoznamov, nielen `to_order ∩ terminal`.** Pri troch
    textových poliach je najpravdepodobnejší preklep „presunul som stav, ale zo starého
    poľa som ho nezmazal" — a `terminal ∩ known_open` skončí mazaním, hoci nápoveda toho
    poľa sľubuje, že značky ostanú.
  - **Prienik `to_order ∩ terminal` zahoď CELÝ konfig**, nie jednu stranu: stav, ktorý
    znamená „rieši sa" aj „skončené", zmaže značky živých objednávok, a záplata jednej
    strany nechá manažéra bežať na nastavení, ktoré nikdy nenapísal. Endpoint to odmieta na
    vstupe, loader je poistka pre ručne upravený súbor.
  - **Jedna množina, nie štyri kópie.** „Objednávka sa rieši" pohýna záložku, „Nedostupné"
    AJ pripomienkové maily — každá vlastná kópia literálu je ďalšia automatizácia, ktorá po
    premenovaní stavu potichu prestane robiť čokoľvek.
  - **Hláška odmietnutia menuje NASTAVENÉ stavy, nie literál.** Beh vracia `open_statuses`;
    banner „ani jedna otvorená" bez toho posiela manažéra hľadať názov, ktorý obchod už
    nepoužíva.
  - **Čo endpoint PRIJME, to musí appka aj POUŽIŤ (revízia PR #295).** Validovať payload
    „ako prišiel" nestačí: loader súbor znovu prečíta a za každú množinu, ktorú považuje za
    nepoužiteľnú, dosadí PREDVOLBU — a tá sa vie biť s množinami, ktoré manažér naozaj
    napísal. Prienik pritom zahadzuje konfiguráciu CELÚ. Karta potom napíše „✅ Uložené.
    Platí to hneď pre celú appku.", premenovanie sa potichu vráti, maily nechodia nikomu a
    prune je odzbrojený pod bannerom, ktorý menuje „protirečivý zoznam", čo panel vykresľuje
    ako PRÁZDNY — stav neopraviteľný z tej istej obrazovky, ktorá ho spôsobila. Preto:
    **rozhodovaciu logiku vyčleň do čistej funkcie (`_resolve_status_sets`) a pusti ju v
    POSTe na KANDIDÁTSKY súbor** (v tom istom `with _lock:`, tesne pred zápisom); čokoľvek
    by neprežilo, odmietni vetou pre človeka. „Prijaté a potom zahodené" je jediná odpoveď,
    ktorá nesmie existovať.
  - **„Chýba" a „vyprázdnené naschvál" sú RÔZNE odpovede.** Čistič, ktorý vracia `None` aj
    pre `[]`, ich zlieva — a `known_open: []` (endpoint ho výslovne povoľuje, znamená
    „hlás mi KAŽDÝ nezaradený stav") sa tichom vráti na štyri predvolby, čiže presný opak.
    Vracaj `[]` ako `[]` a nech o povolenej prázdnote rozhoduje volajúci
    (`ORDER_STATUS_REQUIRED`). Pozor aj na TEST, ktorý taký `[]` posiela: ten náš prechádzal
    len preto, že jeho sonda náhodou nebola v obnovených predvolbách — netestoval nič.
  - **Porovnávaj v JEDNOM tvare (NFC + strip), na oboch stranách.** Meno stavu je voľný text
    z dvoch nezávislých vstupov (panel manažéra a stĺpec `statusName`); rozložený zápis je
    bajtovo iný, na obrazovke identický a nesedí s ničím — a pri `to_order` to NIČ
    nezasignalizuje, len sa vyprázdni záložka aj maily. Normalizátor patrí do zdieľaného
    modulu (`export_helpers.norm_status`), lebo ho musia použiť všetci traja konzumenti.
    A **vracaj do API aj stavy, ktoré export REÁLNE nesie** — inak sa meno, čo nesedí s
    ničím, nedá odhaliť; panel na ne upozorní pokojnou (nie červenou) hláškou.
  - **Meno stavu je text, ktorý ide do LOGU** — zakáž riadiace znaky (endpoint 400,
    loader ich zahodí s ERROR riadkom), inak sa cez API alebo ručnú úpravu dá do logu
    podvrhnúť celý riadok.
  - **`_read_json_store` na nečitateľnom (nie pokazenom) súbore ZÁMERNE prepúšťa `OSError`.**
    Pri väčšine úložísk to zhodí jednu záložku; tento súbor ale čítajú `/api/orders`,
    `/api/nedostupne`, `/api/nedostupne/<code>` aj prune — teda štyri cesty naraz. Chyť ho a
    použi tú istú odpoveď, ktorú funkcia už má pre „je tu, ale nedá sa použiť".

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

## 1c. Zánik ≠ koniec — odklad, a MERAJ HO OD ZATVORENIA (#294)

Objednávka „Vybavená" sa môže vrátiť do „Vybavuje sa" — tento repozitár si to sám píše
tam, kde dedup store pripomienok vysvetľuje, prečo záznamy DRŽÍ. Keď prune zmaže značky
v tú istú hodinu, riadok sa vráti bez manažérovho „objednané u dodávateľa" a objedná sa
druhý raz.

**Pomenuj konštantu podľa toho, čo MERIA, nie podľa toho, čo si ňou chcel dosiahnuť.** Prvá
verzia sa volala „odklad", ale merala vek OBJEDNÁVKY — jediný dátum, ktorý export nesie
(67 stĺpcov, `date` = vytvorenie, žiadna zmena stavu ani posledná úprava; overené znova pri
#294). Objednávka vytvorená pred 40 dňami a zatvorená DNES sa tak zmazala hneď pri
najbližšom behu, s NULOVÝM odkladom — a to práve pri dlhom čakaní na dodávateľa, čiže presne
tam, kde tie značky najviac chýbajú. Namerané na živých dátach v deň opravy: staré pravidlo
by v tej hodine zmazalo **22 kľúčov**, nové ani jeden.

**Keď zdroj údaj nemá, ZMERAJ SI HO SÁM — a napíš si ho skôr, než sa rozhoduješ.**
`orders_closed_seen.json` = `{kód objednávky: deň, keď sme ju PRVÝ RAZ videli v koncovom
stave}`. Štyri veci, na ktorých to celé stojí:

- **Poradie je celý návrh: najprv ZAPÍŠ, potom rozhoduj.** Čerstvo zatvorená objednávka tak
  dostane plný odklad hneď pri prvom behu, ktorý ju vidí. Opačné poradie znamená, že KAŽDÁ
  čerstvo zatvorená objednávka je „neznáma" a padne na staré pravidlo — čiže nulový odklad
  navždy, nie raz. Ten istý poriadok robí zo straty storu fail-CLOSED zadarmo: nič sa
  neprečítalo → všetko sa zapíše dneškom → ten beh nezmaže nič.
- **Reopen maže záznam** (objednávka JE v exporte a NIE JE koncová — pozitívny dôkaz).
  Objednávka mimo exportu je NEVIDENÁ, nie znovuotvorená, a záznam si drží; useknutý
  download nesmie reštartovať odklad všetkým.
- **Over, či odklad a OKNO ZDROJA nenechajú kľúč trčať — a keď áno, priznaj to.** Okno je
  90 dní od DÁTUMU objednávky, takže objednávka zatvorená po ~60. dni z neho vypadne skôr,
  než 30-dňový odklad uplynie, a jej kľúč tam ostane navždy. Obe cesty von sa v revízii PR
  #295 vyskúšali a OBE sú horšie ako ten zvyšok:
  - **„Zmaž tesne pred tým, než zmizne"** (prvý cut mal `LAST_CHANCE = 80 dní`) dá NULOVÝ
    odklad práve tým objednávkam, kvôli ktorým odklad existuje — a keďže rozhodoval podľa
    veku objednávky, mazal aj so STRATENÝM grace storom, čím potichu zneplatnil celý
    argument „stratiť tento store nemôže stáť značku".
  - **„Rozhoduj ďalej podľa záznamu, aj keď objednávka z exportu vypadla"** znie dobre, kým
    si nevšimneš, že „nie je v exporte" vyzerá rovnako ako USEKNUTÝ download. Beh by tak
    mazal kľúče objednávok, ktoré v pokazenom súbore len chýbajú, a padla by vlastnosť, na
    ktorej #212 stojí: poškodený zdroj vie prune len ZÚŽIŤ
    (`test_losing_rows_from_the_export_can_only_prune_FEWER_keys` to pripína a spadne).
  Zvyšok teda nechaj ležať a napíš prečo. Asymetria z bodu 1 to rozhoduje: pár kľúčov, čo
  ostanú, nestojí nikoho nič; zmazaná značka pošle manažéra objednať ten istý riadok druhý
  raz. A zvyšok je malý — sú to len objednávky, ktoré NAVYŠE nesú značky a zatvoria sa v
  posledných týždňoch svojho okna.
- **Rast ohranič cez to, čo store OBSLUHUJE:** záznam vzniká len pre objednávku, ktorá má
  aspoň jeden kľúč v značkových úložiskách, a zaniká s jej posledným kľúčom. Store je tak
  veľký ako „čo má manažér označené" (namerané: 66 objednávok pri 176 kľúčoch), nie ako okno
  exportu.

**STRATIŤ store a VERIŤ store sú dve rôzne otázky (revízia PR #295).** Argument
„`protect=False`, lebo strata vie mazanie len ODLOŽIŤ" platí pre store, ktorý ZMIZOL —
a je nepravdivý pre store, ktorý je POKAZENÝ. Krok hodín dozadu (oprava NTP, obnova VM zo
snapshotu, ručná úprava) zapíše dnešný záznam s dátumom v minulosti a hneď NASLEDUJÚCI
zdravý beh ho prečíta ako „odklad dávno uplynul" a maže s nulovým odkladom. Veková podlaha
to nechytí — objednávka naozaj JE stará.

Formuluj preto podmienku, ktorú skutočné pozorovanie nemôže porušiť: **záznam nesmie byť
starší než samotná objednávka** (nemohli sme ju vidieť zatvorenú skôr, než vznikla).
Pokazený záznam = ŽIADNY záznam, čiže odmietnutie. A ten istý predikát (`_closed_seen_day`)
používa aj ZAPISOVAČ, inak sa odmietnutý záznam nikdy neopraví a kľúč ostane nezmazateľný
navždy. Rovnaká pasca s inou osou: **odklad je per OBJEDNÁVKA, ale prácu pridáva manažér
per ZNAČKA.** Záznam vzniká len keď žiadny nie je, takže značka spravená AŽ POTOM zdedí
zvyšok cudzích hodín — a ten môže byť nulový (objednávka sa zatvorila pred 30 dňami, medzi
dvoma hodinovými behmi sa znovu otvorila a zavrela, alebo mal manažér otvorenú starú
záložku). Riešenie bez prekľúčovania storu: **zapnutie príznaku ruší záznam tej objednávky**
— v tom istom `with _lock:`, na VŠETKÝCH zapisovacích cestách (`_write_status_flag`,
`/api/ordered`, `/api/ordered/bulk`), len pri zapnutí, bez zápisu keď niet čo zmazať a ako
housekeeping (klik manažéra nesmie spadnúť na 500 kvôli nášmu účtovníctvu).

**`protect=` daj podľa toho, ČIA práca v store je.** Tento nesie NAŠE pozorovanie, nie prácu
manažéra, a jeho strata vie mazanie iba ODLOŽIŤ, nikdy spôsobiť → `protect=False` a nepatrí
ani do `backup_data.sh` (ten je pre „stratiť = neopakovateľná práca"). `protect=True` by z
legitímneho „posledné záznamy odišli s poslednými kľúčmi" spravil hodinový
`StoreWipeRefused`, ktorý zhodí vlastné účtovníctvo prunu. Naopak manažérom EDITOVANÝ store
(`order_statuses.json`) `protect=True` mať má — a potom ho musíš dopísať aj do
`backup_data.sh`, lebo pokrytie sa odvodzuje z výskytov `protect=` v `app.py`
(`test_the_backup_script_covers_every_irreplaceable_store` spadne, ak zabudneš).

**A neznámy vek stále nie je „dosť starý"** — neprečítateľný dátum sa nemaže, aj keď máš
záznam o zatvorení: vekovú podlahu (`ORDERS_PRUNE_MIN_ORDER_AGE_DAYS`) aj vetvu „posledná
šanca" počítaš z toho istého dátumu.

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

## 3a. Mazanie z manažérovho storu MIMO prunu — tie isté pravidlá (#215)

Nie každé mazanie sa volá „prune". `_do_upload_suppliers` maže priradenia, ktoré eshop
medzitým PREDBEHOL (produkt už má vlastného dodávateľa), a je to rovnako nezvratné mazanie
manažérovho vstupu — takže platia body 1, 2 aj 3 bez zľavy:

- **Pozitívny dôkaz na OBOCH osiach:** kód JE v exporte (prítomnosť) A export pri ňom nesie
  vlastného dodávateľa (stav). „Nevidím pri ňom nášho dodávateľa" nedokazuje nič.
- **Fail-closed brána musí byť NAD tým.** Tu ju netreba písať druhý raz — dosiahnutie toho
  riadku už dokazuje, že export je prítomný, dosť veľký a ČERSTVÝ, lebo brána vyššie inak
  skončí `return`-om. Overené mutáciou: presun mazania NAD bránu zhodí testy `small`/`stale`
  (variant `empty` prežije — tam ho nezávisle kryje pozitívny dôkaz, čo je v poriadku, ale
  vedieť to treba).
- **Nemaž iný typ zadržania.** „Katalóg taký kód nemá" (#275) je INÉ zdržanie s iným osudom:
  je samoliečivé a kód sa môže objaviť zajtra, takže to priradenie musí prežiť. Nad živými
  dátami je to celý rozdiel — jediné dnešné priradenie je zadržané práve takto, čiže suchý
  beh nad kópiou zmazal **0**.
- **A NAJVÄČŠIA pasca (revízia PR #295): „eshop tam má vlastného dodávateľa" NEZNAMENÁ „naše
  priradenie je neaktuálne".** `new_supplier_keys` vracia aj kód, ktorému manažér meno
  dodávateľa práve ZMENIL — a pri ňom export legitímne nesie „vlastného dodávateľa": tú
  STARÚ hodnotu, ktorú sme tam zapísali MY. Index z exportu si drží len kód, nie hodnotu,
  takže tieto dva prípady rozlíšiť nevie a mazanie by manažérovu opravu cez noc zahodilo a
  v obchode nechalo starý názov. Rozlišuje ich záznam o nahratí: #215 je o priradení, ktoré
  sa NIKDY nezapísalo (`c not in uploaded`). Overené spustením — bez tej podmienky store
  skončí prázdny a beh k tomu vypíše upokojujúce „0 new codes".
- **Suchý beh nesmie mazať** (`dry` vetva), in-place `pop` pod jedným `with _lock:`, žiadny
  zápis keď niet čo mazať, a do logu konkrétne kódy AJ hodnoty.
- **Zabaľ to ako housekeeping** (§3 posledná odrážka platí aj tu): `StoreWipeRefused` z
  paralelného kliku by inak zhodil CELÝ nočný zápis dodávateľov skôr, než sa postaví prvý
  importný riadok — čiže priradenia manažéra by prestali chodiť do eshopu úplne.
- **Hlás, čo naozaj odišlo, nie čo si chcel zmazať** (`sorted(dropped)`, nie zamýšľaná
  množina) — a nové pole daj do KAŽDEJ návratovej vetvy vrátane tej, ktorá je AŽ ZA
  mazaním (409 „iný import beží"), inak sa beh, ktorý práve mazal, o tom nezmieni.
- **Rozlišuj „nič sa nezmenilo" podľa OBSAHU, nie podľa príznaku.** Booleovské „niečo sa
  stalo" sa dá nastaviť dvakrát (pridám záznam, o pár riadkov ho zase zmažem) a potom
  vyrobí súbor len preto, aby doň zapísal to isté, čo v ňom bolo — porovnaj snapshot.
- **PASCA: zmazaný záznam musí odísť aj z POHĽADU TOHO BEHU.** Po zmazaní sa `assigns`
  načíta znova, ale `new_codes` a `products` sú staré zoznamy — a `{c: assigns[c] for c in
  new_codes}` o dva riadky nižšie potom spadne na `KeyError`. Prefiltruj oboje, inak beh
  spadne presne v tej chvíli, keď prvý raz naozaj niečo zmaže.
- **Nové pole vracaj na KAŽDEJ vetve** (`obsolete_removed: []` aj v skorých `return`-och) —
  volajúci nemá ako odlíšiť „nič sa nezmazalo" od „táto verzia to nehlási".

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
