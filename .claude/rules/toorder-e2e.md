---
paths:
  - "tests/e2e/**"
  - "webreview/static/app.js"
  - "webreview/templates/index.html"
---

# E2E na tabe „Na objednanie" — čo vedieť skôr, než napíšeš test

Tri veci, ktoré v tomto repozitári stáli cyklus. (Playwright pasce okolo schránky, reloadu a
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
