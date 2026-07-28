---
paths:
  - "tests/e2e/**"
  - "webreview/static/app.js"
  - "webreview/templates/index.html"
---

# E2E na tabe „Na objednanie" — čo vedieť skôr, než napíšeš test

Veci, ktoré v tomto repozitári stáli cyklus. (Playwright pasce okolo schránky, reloadu a
kontrastu sú v `.claude/skills/webreview` — toto sú tie, ktoré tam nie sú.)

## 1. `#tab-review` ani `#tab-toorder` NEEXISTUJÚ — aktívny tab čítaj z `ACTIVE_TAB`

`index.html` má vlastnú `<section id="tab-*">` pre automatizačné a vedľajšie taby, ale
**„Kontrola párovania" a „Na objednanie" ZDIEĽAJÚ `#list` a `#empty`** (preto existuje
`setEmptyText`, ktorý per-tab text vracia späť na default). Takže:

```python
page.wait_for_selector("#tab-review:not([hidden])")   # ← NIKDY sa nesplní, 30 s timeout
page.wait_for_function("() => ACTIVE_TAB === 'review'")   # ← toto
assert page.evaluate("() => ACTIVE_TAB") == "toorder"
```

`ACTIVE_TAB` je globál v klasickom (non-module) `app.js`, čiže dosiahnuteľný holým menom
z `page.evaluate` — rovnako ako `ORDERS`, `PRODUCTS`, `renderToOrder`, `itemsWord`.

## 2. Riadok s ✂️ (`.to-splitedit`) si v teste musíš vyrobiť — a treba naň DVE veci

`renderOrderRow` stavia „ceruzkový slot" LEN vo vetvách, kde riadok už má odkaz
(`supHref` / `o.supplierUrl` / `pairHref`); nenapárovaný riadok dostane vkladacie pole a
žiadnu ceruzku. Až v tom slote sa `reviewStatus === 'split'` prejaví ako ✂️ namiesto ✏️.
Fixtúra `toorder_server` je celá nenapárovaná, takže samotné `reviewStatus='split'` ✂️
NEVYROBÍ. A `openSplitSizes` sa hneď na prvom riadku pozrie do `PRODUCTS` — bez zhody
`reviewKey` skončí `alert`-om a k ničomu ďalšiemu sa nedostane.

```js
const o = ORDERS.find(x => x.itemCode === 'C2');
o.reviewStatus = 'split';
o.reviewKey = 'SPLITKEY';
o.supplierUrl = 'https://dodavatel.example/ciapka';   // ← inak sa ceruzkový slot nevykreslí
PRODUCTS = [{key: 'SPLITKEY'}];                       // ← inak openSplitSizes len alertne
renderToOrder();
```

`window.confirm` spy-uj cez `add_init_script` a vracaj `false` — test tak prečíta presné
znenie hlášky a tab nikam neodíde (rovnaký dôvod ako pri `alert` spy-i).

## 3. KAŽDÝ počet na tomto tabe ide cez `itemsWord(n, acc)` — vrátane hlavičky skupiny

Pravidlo (1 → položka / v akuzatíve po „vybaviť" položku, 2–4 → položky, 0 a 5+ → položiek)
má JEDEN helper a druhá implementácia vedľa neho je zakázaná. Hlavička skupiny dodávateľa
mala genitív zapečený v šablóne až do #238/#240 — a prežila to práve preto, že e2e kontrakt
(`test_order_supplier_case.py`) ten zlý tvar PRIPÍNAL. **Existujúci test, ktorý pripína
nesprávny text, nie je dôvod chybu nechať** — uprav ten assert v RED commite; pripína tam
poradie „menovka najprv", nie znenie počtu.

Nový počet → napoj na `itemsWord` a otestuj tabuľkou (0/1/2/4/5/11/21/101 — 11/21/101 sú
tie, ktoré naivné `n > 1 → množné číslo` pokazí). Vzor: `tests/e2e/test_order_group_header.py`.

**A hľadaj VŠETKY počítadlá, nielen to nahlásené.** Revízia tejto PR našla druhé miesto s
tým istým defektom — podtitul tabu (`pageSub`, `app.js` `openItemsPhrase`) — pár pixelov nad
hlavičkou, ktorú ticket opravoval; ticket ju pritom nazval „jediné počítadlo, ktoré obchádza
skloňovanie". Keď meníš skloňovanie, prejdi `grep -n "položiek\|položky\|položka\|položku"
webreview/static/app.js` a posúď KAŽDÝ zásah. Pozor na prípady, kde sa skloňuje aj PRÍDAVNÉ
meno („1 otvorená položka" / „5 otvorených položiek") — tam nestačí `itemsWord`, treba
vlastnú frázovú funkciu, ktorá si podstatné meno stále vypýta od neho.

## 4. Spy na HLÁSENIE inštaluj až PO načítaní — a počítaj SPRÁVY, nie volania

Odkedy #234 nahradilo `alert()` bannerom `#toFails`, sa zlyhania hlásia cez
`toOrderSaveFailed`. Spy naň sa NEDÁ založiť cez `add_init_script`: je to obyčajná
`function` deklarácia v klasickom skripte, a tá zakladá globál priamo
(`CreateGlobalFunctionBinding` → `DefineOwnProperty`), takže **obíde `defineProperty`
setter aj akúkoľvek pascu položenú pred načítaním**. Inštaluj ho `page.evaluate`-om až
za `wait_for_selector(".toorder-row")` (dovtedy nemá čo zlyhať).

A hlavne: **spy, ktorý počíta VOLANIA, nemeria to isté ako počet správ, ktoré manažér
videl.** Dedup per ZÁPIS (5 s) žije VNÚTRI `toOrderSaveFailed`, takže potlačené
duplicitné volanie neprida do banneru nič. Spy preto porovná počet `.tofail` riadkov
pred volaním a po ňom a zaznamená len vtedy, keď riadok naozaj pribudol — inak test
„tá istá odmietnutá zmena sa hlási raz" spadne na správaní, ktoré je správne.
Snapshot DOM ale ber PRED zavolaním pôvodnej funkcie — to je to, čo pinuje poradie
„najprv rollback a prekreslenie, až potom hláška".

Vzor: `_FAIL_SPY` + `_open` v `tests/e2e/test_order_save_errors.py`.

## 5. `page.route` na endpoint chytá aj GET — odmietaj podľa METÓDY

Test, ktorý odmietne zápis a potom si ten istý endpoint **prečíta späť** ako dôkaz
(`fetch('/api/instock')` cez `page.evaluate`), dostane 500 aj na to čítanie a padne
na `Object.keys(undefined)` — vyzerá to ako chyba appky, pritom je to stub.

```python
page.route("**/api/instock", lambda r: r.fulfill(status=500, ...)
           if r.request.method == "POST" else r.continue_())
```

Rovnaká pasca ako `**/api/ordered` vs `/api/ordered/bulk` (bod vyššie): stub si vždy
zúž na to, čo naozaj chceš rozbiť.

## 6. Keď meníš SÉMANTIKU, cudzie testy starú sémantiku PRIPÍNAJÚ — hľadaj ich VOPRED

`test_toorder_instock_and_unavailable_flags_toggle_and_persist` (z #84) doslova
asertoval „nedostupné on too — **independent** of skladom, both stay active together",
teda presne stav, ktorý #211 odstraňuje. Objavil sa až v plnom e2e prebehnutí, po
zelenom GREEN commite. Po zmene invariantu si preto VOPRED vygrepuj testy na starý
invariant (tu stačilo `grep -rn "instock" tests/e2e`) a nahraď ich vo VLASTNOM commite
s odôvodnením — nikdy ich potichu neoslabuj.

Pozor aj na **vlastné RED testy**: pri #211 dva z nich seedovali stav, ktorý nová
sémantika odmieta (všetky tri príznaky osi B naraz), takže merali vlastný setup, nie
opravu. **SETUP red testu musí byť legálny podľa novej sémantiky**, nielen jeho assert.

A **upratovanie na konci testu prežije zmenu sémantiky len zriedka**: „vypni oba
príznaky" po zavedení výlučnosti druhým klikom jeden zase ZAPNE a session-scoped
fixtúru nechá špinavú.

## 7. Hodnotu skopírovanú zo susedného testu treba overiť TIEŽ

Nové #211 testy si prevzali kľúč `<reálny kód objednávky>|<kód produktu>` (hodnota zámerne nezapísaná — #289) z testu o dva riadky vyššie — a
`LC_ALL=C grep -ac` nad `data/out/orders_cache.csv` ho našiel (4, resp. 1 výskyt):
reálny kód objednávky naviazaný na meno, e-mail a telefón zákazníka, vo VEREJNOM
repozitári. „Je to už v tomto súbore" nie je overenie (pred-existujúce výskyty →
#289). Postup je v `.claude/rules/automation-health.md` — plus over aj 57 MB
`data/products.csv`, nielen objednávkovú cache.

## 8. Optimistický zápis, ktorý mení VIAC príznakov naraz — dve pasce (revízia PR #290)

Odkedy jeden POST hýbe viacerými príznakmi riadku (#211: zapnutie stavu zhasne ostatné dva):

- **Zoznam menených príznakov stav VŽDY BEZPODMIENEČNE, nikdy nie podľa toho, čo klient
  práve zobrazuje.** Filter `if (map[key])` vyzerá ako úspora, ale príznak, ktorý klient už
  optimisticky zhasol pre STARŠÍ letiaci zápis, si tak nenárokuje `seq` — a keď ten starší
  zápis server ODMIETNE, je jediným vlastníkom toho príznaku a rollbackom ho VZKRIESI, nad
  novším zápisom, ktorý server prijal. Mazanie neexistujúceho kľúča je no-op, takže nárok
  na všetky nestojí nič.
- **Vlastníctvo pre HLÁSENIE urč z KLIKNUTÉHO príznaku, nie z „vlastním hociktorý z nich".**
  Zápis môže ostať vlastníkom príznaku, ktorý iba ZHASÍNAL (novší zápis sa ho nemusel
  dotknúť) — brať to ako vlastníctvo znamená DVE hlášky pre jeden riadok.

A ešte: **odpoveď servera so stavom riadku treba naozaj PREČÍTAŤ.** Dva zápisy vydané v
jednom round-tripe idú po dvoch spojeniach a ich serverové vlákna si vezmú `with _lock:`
v ľubovoľnom poradí — klientske poradie vydania teda NIE JE poradie commitov. Prijmi
`flags` cez tú istú bránu `confirmed`/`confirmedSeq` (staršia odpoveď neprebije novšiu) a
mapu prepíš len keď na ňu nič iné neletí.

## 9. `confirmedSeq` príznaku smie stampnúť LEN číslo, ktoré si TEN príznak nárokoval

Pokračovanie bodu 8, a najdrahší nález revízie PR #290. `seq` v `_flagWrites` je
**nezávislý čítač na dvojicu (príznak, riadok)** — presne tá nezávislosť je dôvod, prečo
`_flagWrites` existuje (bod 8). Dôsledok, na ktorý sa ľahko zabudne: **čísla NIE SÚ
porovnateľné naprieč príznakmi.** `waiting.seq == 2` a `ordered.seq == 2` spolu nemajú nič
spoločné.

Zrkadlenie serverovej odpovede (bod 8, posledný odsek) dostalo JEDNO číslo — `seqs[0]`,
teda číslo KLIKNUTÉHO príznaku — a vpísalo ho do `confirmedSeq` **všetkých štyroch**
príznakov z odpovede, aj tých, ktoré ten zápis vôbec nenárokoval. Príznak, ktorý bol vo
svojom vlastnom čítaní pozadu, tak dostal CUDZIE, vyššie číslo; jeho vlastný strážca
`seq >= st.confirmedSeq` potom odmietal jeho VLASTNÉ, serverom PRIJATÉ zápisy, `confirmed`
zamrzol na zastaranom údaji a nasledujúci ODMIETNUTÝ zápis sa „vrátil" práve naň — čiže sa
nevrátil vôbec. Web ukazoval neobjednaný riadok, ktorý server drží ako objednaný (objedná
sa druhý raz), alebo objednaný riadok, ktorý server odmietol (neobjedná sa nikdy) — a to
hneď po hláške, že uloženie zlyhalo, teda vtedy, keď riadku manažér verí najmenej.

**Pravidlo:** do `confirmedSeq` príznaku sa smie zapísať iba poradové číslo, ktoré si ten
príznak sám vzal. Zápis si preto postaví `claimed = {príznak: jeho vlastné seq}` a
zrkadlenie preskočí (a ani si cez `_flagEntry` NEVYPÝTA — inak založí záznam pre príznak,
ktorý nikdy nezapisoval) všetko, čo v mape nie je. Nič sa tým nestratí, lebo **žiadny
koncový bod nezapisuje cez os**, takže zápis môže zmeniť len to, čo nárokoval:
`/api/ordered` siaha na `ordered`, `_write_status_flag` na tri stavové úložiská, a
zapnutie stavu osi B už nárokuje všetky tri. Kedykoľvek pridávaš ďalší optimisticky
zapisovaný príznak, over si túto rovnicu — „čo endpoint MÔŽE zmeniť" sa musí rovnať „čo
zápis nárokoval".

**A testová časť toho istého nálezu: test s JEDNÝM klikom nemôže chytiť divergenciu, ktorá
potrebuje DVA.** `test_a_refused_exclusive_write_restores_BOTH_flags` chodil presne po
tejto ceste a bol zelený — diera sa otvorí až druhým klikom jedného stavového tlačidla
(vytlačí cudzie `confirmedSeq` na 2, kým vlastný `seq` je stále 0). Keď testuješ
bookkeeping s čítačom, **klikaj opakovane, nie raz**, a nechaj v súbore jednoklikovú
KONTROLU, ktorá musí prejsť pred aj po oprave — inak sa zaplata na symptóm nedá odlíšiť od
opravy. Vzor: `tests/e2e/test_order_flag_seq_guard.py` (S5 / S7 / S6-kontrola), plus tretí
test, ktorý pripína priamo PRÍČINU na `_flagWrites`, aby budúci refaktor spadol s
čitateľným dôvodom, nie ako záhadná divergencia príznakov.

## 10. Surový bajt NUL v `.js` zdroji oslepí `grep` na CELÝ súbor

Oddeľovač v zloženom kľúči (`field + NUL + key`, `s.kind + NUL + s.key`) sa píše ako
**escape `'\u0000'`**, nikdy ako surový bajt. Surový NUL je platný JavaScript a všetko beží
ďalej, ale `grep` taký súbor klasifikuje ako BINÁRNY a potom **ticho nevráti NIČ** na
žiadny vzor — nie „no match", ale prázdno, exit 1. Revízia PR #290 na to narazila:
vyhľadávanie nad 5240-riadkovým `app.js` vracalo prázdno a súbor vyzeral čisto, lebo bajt
nie je vidieť. Ak sa `grep` nad známym vzorom správa nevysvetliteľne, over si
`LC_ALL=C grep -c "" <súbor>` (u binárneho zlyhá) alebo prečítaj bajty Pythonom.

**Šíri sa KOPÍROVANÍM a nevidíš to.** Pri písaní tohto bodu sa presne ten bajt preniesol
z revízneho hlásenia cez schránku do TOHTO súboru a oslepil `grep` nad playbookom — tri
minúty po tom, čo sa opravil v `app.js`. Keď o oddeľovači píšeš alebo ho odniekiaľ
prenášaš, píš `\u0000` ručne, nikdy neprelepuj hodnotu. Pinnuté testom
`tests/test_static_source_hygiene.py` nad `webreview/static/` **aj `.claude/rules/`.**

## 11. Test, ktorý počíta `_flagWrites` záznamy, po zmene zápisu preráta inak

`test_a_straggler_...` asertoval PRESNE JEDEN záznam. Klik na os B si legitímne nárokuje
tri (flag, riadok) záznamy, takže assert padol na správnom kóde. Pravidlo „záznamy sa
NIKDY nemažú" ostáva pinnuté — mení sa len očakávaná množina. Keď meníš, koľko príznakov
jeden zápis nárokuje, prejdi testy, ktoré `_flagWrites` počítajú.

## 12. Predpoklad na SERVERI naseeduj cez `page.request`, nie klikaním

Keď test potrebuje riadok, ktorý už NEJAKÝ príznak má, a zároveň **čisté klientske
účtovníctvo** (`_flagWrites` prázdne, `seq` na nule — teda stav manažéra pri čerstvo
načítanej karte), naklikať sa k nemu NEDÁ: každý klik si sám nárokuje `seq` a predpoklad
tým znehodnotí. `page.request` chodí mimo stránky, ale zdieľa cookies kontextu (autouse
fixtúra ich seeduje), takže stačí:

```python
page.request.post(toorder_server + "/api/ordered", data={"key": _KEY, "ordered": True})
_open(page, toorder_server)      # až TERAZ sa načíta app.js -> _flagWrites je prázdne
```

Presne to odlišuje S7 od S5 v `test_order_flag_seq_guard.py`. Ekvivalent cez `page.reload()`
po kliku funguje tiež (modulové `const _flagWrites` reload vynuluje), ale je o načítanie
navyše. `/api/*` nie je CSRF-gated (JSON + session cookie stačí — pozri fixtúru `admin_api`).

Drobnosť z tej istej vlny: v **Python** literáli neuzatváraj slovenské `„…“` ASCII
úvodzovkou — `"… „skladom" …"` reťazec ukončí a zhodí zber testov na `SyntaxError:
invalid character '„'`. Buď použi pravú `“`, alebo v hláškach assertu píš bez úvodzoviek.
