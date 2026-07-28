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
`flags` cez tú istú bránu `confirmed`/`confirmedCommit` (staršia odpoveď neprebije novšiu)
a mapu prepíš len keď na ňu nič iné neletí.

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

## 13. Poradie VYDANIA nie je poradie COMMITOV — a dá sa to vyriešiť, len nie na klientovi (#291)

Body 8 a 9 hovoria „odpovede mimo poradia sú z princípu neriešiteľné na strane klienta".
Platilo to, kým server o svojom poradí nič nepovedal. **Odkedy každý zápis príznaku vracia
`commitSeq` (monotónny čítač inkrementovaný VNÚTRI toho istého `with _lock:`, v ktorom sa
zapisuje), riešiteľné to je** — a brána `confirmed` sa riadi ním, nie klientskym `seq`.
Pole sa preto volá `confirmedCommit`; `seq` si ponechal svoju DRUHÚ úlohu (kto vlastní
(príznak, riadok) pre rollback a pre HLÁSENIE), ktorá naozaj je o poslednom ÚMYSLE manažéra.

- **Nikdy nemiešaj tie dve čísla v jednom poli.** `seq` je nezávislý čítač na dvojicu
  (príznak, riadok), `commitSeq` je jedny globálne hodiny — medzi nimi neexistuje
  usporiadanie. Odpoveď BEZ `commitSeq` (úspech, ktorého telo sa nedalo prečítať) preto
  neprijíma NIČ; „fallback na poradie vydania" znie neškodne, ale znamená, že raz stampnuté
  commit-číslo už žiadne issue-číslo neprebije a dovtedy potichu rozhoduje poradie vydania.
  Prvý pokus to tak mal a zhodil ho `test_a_straggler_from_an_older_burst_cannot_poison_a_later_click`.
- **Čítač nasaď na wall-clock v ms, nie na 0.** Reštart služby pod otvorenou kartou by inak
  klientovi s číslom 4 812 posielal samé „staršie" odpovede a ten by ich do konca života
  stránky odmietal — teda zamrznutý `confirmed`, čo je tvar chyby z PR #290 cez iné dvere.
- **Obmedzenie „stampuj len nárokované príznaky" ostáva**, hoci pri globálnych hodinách už
  nie je nosné pre korektnosť: bráni `_flagEntry` založiť účtovníctvo pre príznak, ktorý sa
  nikdy nezapisoval.
- **Stub v cudzom teste kodifikuje DRÔT.** `page.route`, ktorý si vymyslí `{ok, flags}` bez
  `commitSeq`, spadne na tom, že odpoveď bez commit-čísla sa neprijíma — nie na kliente.
  Taký stub uprav v samostatnom `test:` commite (je spätne kompatibilný, prejde aj proti
  neopravenému klientovi) — pozri bod 6.

### Ako divergenciu v teste VYROBIŤ (a čím si zavesíš celý beh)

Klikaním sa nedosiahne — obe poradia si takmer vždy sadnú. Obal `window.fetch` cez
`add_init_script` (teda skôr, než sa načíta `app.js`) a PODRŽ prvý POST na hranici siete,
kým druhý neskončí; server tak commitne B a potom A. Vzor:
`tests/e2e/test_order_flag_commit_order.py` (`window.__posts.issued/done` + `window.__release()`).

**PASCA: `page.evaluate` nemá žiadny predvolený timeout.** Test, ktorý v tej istej stránke
`await`-uje POST, ktorý obal drží, sa nezasekne na 30 s — visí, kým ho nezabije celý beh
(u nás 600 s SIGTERM, bez jediného riadku výstupu). Testy, ktoré si svoje zápisy awaitujú,
drž MIMO tej fixtúry (vlastný `page`), nie „len opatrne".

### Čo z bodu 13 zistila až adversariálna revízia (PR #292)

- **„Nedá sa usporiadať" NIE JE „musí sa ignorovať".** Prvý cut odmietal prijať KAŽDÚ
  odpoveď bez `commitSeq` — a tým znova otvoril chybu z #290: `confirmed` zamrzol a
  nasledujúci ODMIETNUTÝ zápis sa „vrátil" na hodnotu, ktorú server nedrží. Meraním
  potvrdené, že `main` to nerobil, čiže to bola REGRESIA. Usporiadanie treba len vtedy,
  keď je voči čomu usporadúvať: keď je zápis stále posledný vydaný pre svoju dvojicu
  (príznak, riadok) a nič iné pre ňu neletí, je jediným pisateľom a jeho prijatie JE stav
  servera. Prijmi ho — ale hodiny NEPOSÚVAJ (číslo, ktoré si nedostal, nesmie hýbať
  poradím). Dvere k odpovedi bez čísla sú bežné: useknuté telo (appka beží cez tunel) a
  karta, ktorá prežije rollback deploy.
- **Wall-clock seed sám o sebe monotónnosť NEDÁVA.** `time.time()` je CLOCK_REALTIME:
  reštart + korekcia času dozadu (NTP, snapshot VM, ručne prestavené hodiny) vydá čísla,
  ktoré živá karta už videla — a tá potom odmieta všetko do konca svojho života. To isté
  spraví DRUHÝ proces (každý si seeduje vlastný čítač; #262 zaznamenal druhú inštanciu
  bežiacu štyri dni). Najvyššie vydané číslo preto REZERVUJ na disk (po blokoch, aby klik
  nestál zápis) a seeduj `max(hodiny, rezervácia)`.
- **Umiestnenie `_next_commit_seq()` V ZÁMKU sa e2e testom nedá pripnúť.** Presunutie
  všetkých troch volaní MIMO ich `with _lock:` nechalo 50 e2e testov zelených — testy
  vydávajú zápisy po sebe, takže čísla vyjdú rastúce tak či tak. Pripni to serverovým
  testom, ktorý číta hĺbku zámku priamo (`webapp._lock._depth > 0`) v okamihu, keď sa
  číslo berie.
- **Vymyslené `commitSeq` v stube voľ VYSOKÉ.** `commitSeq: 1` funguje len dovtedy, kým je
  záznam panenský; prvá úprava, ktorá pred neho vloží reálny zápis, ho zhodí ako
  zastaraný — a test spadne z dôvodu, ktorý s jeho menom nemá nič spoločné.
- **`__posts.done` (alebo hocijaký signál z `fetch().then()`) NIE JE „riadok je
  prekreslený".** Fires skôr, než `postToOrder` dočíta telo, než sa prepíše účtovníctvo a
  než `renderToOrder()` zbehne. Čakaj na DOM (triedu riadku), inak test prechádza na
  náhode v časovaní.

### Odpoveď, ktorú sa nedá zaradiť — NEHÁDAJ, spýtaj sa servera (revízia opravy #291)

Prijatý zápis bez `commitSeq` sa nedá zaradiť do serverovej histórie, a **každý spôsob, ako
to UHÁDNUŤ, je poradie vydania v inom kabáte.** Oba sa vyskúšali a oba sú zlé:

- **Odmietnuť ju úplne** → `confirmed` zamrzne a nasledujúci ODMIETNUTÝ zápis sa „vráti" na
  hodnotu, ktorú server nedrží (regresia proti stavu pred #291).
- **Prijať ju, keď „som stále posledný vydaný a nič iné neletí"** → vie prebiť NOVŠIU
  očíslovanú odpoveď, a keď sa dve neočíslované vrátia v opačnom poradí, než commitli,
  neprijme ANI JEDNU: tá, čo je stále posledná vydaná, padne na „druhá ešte letí", a tá,
  čo sa usadí posledná, padne na „už nie som posledný vydaný".

Riešenie je **nerozhodovať sa**: klient si znova načíta príznaky riadku zo servera
(`loadOrders()` + prekreslenie, debouncované — dávka neočíslovaných odpovedí je JEDNA
udalosť). Je to tá istá cesta, akou už chodí prepnutie tabu, takže nič nové nezastará.

**Dôsledok pre stuby v testoch:** vymyslený ÚSPECH musí niesť `commitSeq`, inak spustí
resync a ten zmaže presne to účtovníctvo, ktoré test pripína (zhodilo to
`test_a_straggler_from_an_older_burst_cannot_poison_a_later_click`). Číslo prideľuj pri
ZACHYTENÍ POST-u, nie pri jeho uvoľnení: tie testy sú o odpovediach mimo poradia pre
zápisy, ktoré commitli v bežnom poradí.

### A seeduj LENIVO — import nesmie zapisovať do dátového adresára

Prvá verzia hodín seedovala pri importe, čím `import app` zapísal `flag_commit_seq.json` do
OUT. To je proti invariantu, na ktorom stojí celé #261: suchý beh, ktorý ešte neprehodil
`WEBREVIEW_OUT` na kópiu, by trafil živý adresár skôr, než stihne zabrať jeho vlastný
`assert` — a import by si bral medziprocesový flock (až 30 s za bežiacou službou).
Seeduj pri PRVOM zápise, pod zámkom, ktorý si volajúci aj tak drží.

## 14. Nový prvok na karte automatizácie NESMIE nosiť triedy `.auto*` (#209)

Platí pre KAŽDÝ tab s kartou automatizácie, nielen pre „Na objednanie". Karta má
`.autostatus` / `.autohead` / `.autometa` / `.autodesc` / `.autoerr` a **desiatka e2e testov
na ne lokátoruje STRIKTNE** (`page.locator(".autometa").inner_text()` v `test_automations`,
`test_image_health`, `test_parovania_eshop`, `test_grube_externalcode`, `test_restock_skladom`,
`test_supplier_stock`, `test_riziko_vypadku`, `test_shoptet_sync`…). Druhý prvok v tej istej
triede = `strict mode violation: resolved to 4 elements` a spadnú testy, ktoré s tvojou zmenou
nemajú nič spoločné — pri #209 to boli 4 naraz a vyzeralo to ako regresia karty.

- Novému panelu daj **vlastný menný priestor** (`.statuscfg`, `.statuscfg-head`, …) a
  naštýluj ho zvlášť. Nekopíruj `.auto*` len preto, že „vyzerá to rovnako".
- Rovnako pri hlásení: vlastné `.statuscfg-msg.bad`, nie `.autoerr` — inak sa rozdvojí
  lokátor na banner odmietnutého prunu.
- Vlastné testy vieš odviazať od tried úplne: `data-testid` na paneli aj na každom poli.
- **Zámerne vyprovokovaný non-2xx je konzolová chyba.** Chrome loguje každý neúspešný
  request; test na odmietnutý zápis preto filtruje presne ten riadok
  (`_PROVOKED = re.compile(r"Failed to load resource: .*\b400\b")` + `_unexpected()`),
  ako to už robia `test_ui_labels.py` (403), `test_auth.py` (401/403),
  `test_orders_reminder.py` (502) a `test_image_resilience.py` (404). Nevypínaj kvôli tomu
  kontrolu konzoly celú.

## 15. Nový `confirm()` pred zápisom TICHO prepne cudzí test na vetvu „zrušiť" (#297)

Playwright bez `page.on("dialog", …)` každý dialóg sám ZAMIETNE. Keď teda pridáš potvrdenie
pred uloženie, test, ktorý to uloženie pripína, nespadne na dialógu — spadne o tri riadky
nižšie na tom, že sa nič neuložilo, a vyzerá to ako regresia zápisu. Horšie: keby jeho assert
bol mäkší, ostal by zelený a odvtedy by testoval CANCEL vetvu.

- Cudzí test uprav vo VLASTNOM commite (bod 6) — `page.on("dialog", lambda d: d.accept())` je
  spätne kompatibilné (bez featury sa handler nikdy nezavolá), takže si to over stashnutím
  zdrojáku: celý súbor musí byť zelený aj proti NEOPRAVENÉMU kódu. To je dôkaz, že si cudzí
  test neoslabil, len prestal pripínať tok, ktorý už neexistuje.
- **Pozor, čo urobí zmena FIXTÚRY.** Riadok pridaný do `orders_cache.csv` fixture servera vie
  spraviť z dovtedy neškodnej úpravy v cudzom teste úpravu s reálnym dosahom — a tým mu
  vyvolať dialóg. Keď do fixture exportu pridávaš objednávky, prejdi VŠETKÝCH jej
  konzumentov (`grep -rn "<meno_fixtúry>" tests/e2e`).
- **Dátumy v takom riadku píš RELATÍVNE k `datetime.now()`.** Napevno zapísaný dátum
  prekĺzne cez `MIN_DAYS` bránu a náhľad odvtedy ticho vracia 0 — test prestane testovať
  čokoľvek a nikto sa to nedozvie.
- Spy na dialóg si nechaj vrátiť aj TEXT (`seen.append(d.message)`) — inak overíš, že sa
  niečo spýtalo, ale nie že to povedalo správne číslo.
- Skloňovanie počtu v tej hláške ide cez `pluralWord` (bod 3): assertuj `„2 objednávky"`,
  nie len prítomnosť čísla.

## 16. Klientsky TIMEOUT testuj routou, ktorá NIKDY neodpovie — nie `sleep`-om v handleri

Keď má klient vlastnú hranicu čakania (`AbortController`, #298), treba v teste request, ktorý
sa nevráti. **`time.sleep()` v `page.route` handleri je pasca**: handler beží na tom istom
Python vlákne ako test, takže zablokuje aj dialógový handler a `wait_for_*` volania — test
neskončí timeoutom, ale zavesí sa.

```python
page.route("**/api/order-statuses/impact", lambda route: None)   # zámerne bez odpovede
```

Handler, ktorý request ani nesplní, ani nepustí ďalej, ho nechá visieť na sieti a **nič
neblokuje**: stránka beží ďalej, klientsky `AbortController` po svojom čase vyhodí a test
overí, na ktorej vetve to skončilo. Čakanie na následok daj s explicitným `timeout=` väčším
než tá klientska hranica (5 s hranica → `timeout=15000`).

Zvyšné dva tvary tej istej rodiny:

- **`route.fulfill(status=500, …)`** na overenie vetvy „server odpovedal chybou". Chrome to
  zaloguje ako konzolovú chybu — filtruj presne ten riadok (bod 14), nevypínaj kontrolu celú.
- **`route.fulfill` s VYMYSLENÝM telom** je najlacnejší spôsob, ako pripnúť VYKRESLENIE čísel,
  ktoré server posiela: fixtúra sa nedá vždy prehovoriť, aby vyrobila tri RÔZNE hodnoty
  (`orders` / `mailable` / `customers`), a s tromi rovnakými test nedokáže, že sa zobrazujú
  tie správne. Serverovú stranu pritom pokrýva jednotkový test, takže sa nič nestráca.
