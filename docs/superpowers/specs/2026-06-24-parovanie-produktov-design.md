# Párovanie produktov → odkaz na dodávateľa

Dátum: 2026-06-24
Stav: schválený návrh (pred implementačným plánom)

## 1. Cieľ

Pre každý produkt vybraných dodávateľov nájsť odkaz (URL) na jeho produktovú
stránku na webe dodávateľa a zapísať tento odkaz do poľa `textProperty10`.

Pole `textProperty10` je interné pole majiteľa pre **automatizáciu doobjednávania**
(automatika hitne odkaz a doobjedná produkt, ktorý je vypredaný). Preto:
- hodnota = **holá URL** (žiadny popis, žiadny `Názov;Hodnota` formát),
- presnosť odkazu má priamy dopad na objednávky.

## 2. Rozsah

Prvá dávka — dvaja dodávatelia:

| Dodávateľ (pole `supplier`) | Web | Platforma | Variantových riadkov | Odhad produktov | externalCode |
|---|---|---|---|---|---|
| `BETALOV` | https://www.huntingshop.eu | Nette (custom PHP) | 2 465 | ~363 | 85 % majú kód |
| `WETLAND` | https://www.wetland.sk | PrestaShop | 2 236 | ~330 | 26 % majú kód |

Ostatní dodávatelia (GRUBE, TTHUNT, LESONA, ODIMON, …) – **mimo rozsah** tejto dávky.
Architektúra má byť rozšíriteľná o ďalších dodávateľov (každý = web + pravidlá hľadania).

## 3. Vstupné dáta

Shoptet export celého katalógu forestshop.sk (CSV):

```
https://www.forestshop.sk/export/products.csv?patternId=14&partnerId=3&hash=<HASH>
```

- ~14 008 produktových riadkov (súbor má ~304 k riadkov kvôli viacriadkovým popisom).
- Oddeľovač `;`, hodnoty v úvodzovkách, kódovanie **Windows-1250 (cp1250)**.
- Kľúčové stĺpce:
  - `code` — interný kód produktu/variantu (napr. `60177/46`). Unikátny kľúč riadku.
  - `pairCode` — zoskupuje varianty toho istého produktu (napr. všetky veľkosti).
  - `name` — názov produktu.
  - `externalCode` — kód produktu v systéme dodávateľa (napr. `OB570`, `BR1015`).
  - `supplier` — názov dodávateľa (už vyplnené: `BETALOV`, `WETLAND`, …).
  - `textProperty10` — cieľové pole (dnes prázdne pre BETALOV/WETLAND;
    inde drží parametre vo formáte `Názov;Hodnota` — tých sa nedotýkame).

Export sa do gitu **necommituje** (veľký + môže obsahovať hash). Ukladá sa do
gitignorovaného `data/`.

## 4. Postup

### Fáza 0 — prieskum stránok (per dodávateľ)
Doladiť presný spôsob hľadania a extrakcie odkazu na produkt:

- **WETLAND (PrestaShop):**
  - Hľadanie: `https://www.wetland.sk/vyhladavanie?controller=search&s=<QUERY>`
  - Výsledok obsahuje odkazy na produkt typu
    `https://www.wetland.sk/<kategoria>/<slug>-<id1>-<id2>#/<variant>` →
    uložiť **bez `#`-fragmentu** (základná stránka produktu).
  - Verejné (ceny viditeľné bez prihlásenia).
- **BETALOV (Nette / huntingshop.eu):**
  - Hľadanie: `https://www.huntingshop.eu/hladanie?search=<QUERY>` (form `action="/hladanie"`, input `name="search"`, GET).
  - Produktové URL = čisté slugy, napr. `https://www.huntingshop.eu/arabuko-gtx-obuv-brown`.
  - Verejné. Pozn.: výpis výsledkov treba izolovať od navigačných/odporúčaných
    odkazov; ak je render cez JavaScript, použiť Playwright.

Výstup Fázy 0: pre každý web zafixovaný (a) URL hľadania, (b) selektor kontajnera
výsledkov, (c) extrakcia produktového odkazu, (d) requests vs. Playwright.

### Fáza 1 — produkty z CSV
- Načítať CSV (cp1250), vyfiltrovať riadky kde `supplier ∈ {BETALOV, WETLAND}`.
- Zoskupiť varianty do **jedného produktu**:
  - primárny kľúč: `pairCode` (v rámci dodávateľa),
  - záloha keď `pairCode` chýba: `(supplier, normalizovaný name)`.
- Pre produkt si pamätať: dodávateľa, `externalCode` (ak je), `name`,
  zoznam všetkých `code` variantov.

### Fáza 2 — hľadanie a výber
Pre každý produkt:
1. Zostaviť query: `externalCode` ak existuje, inak vyčistený `name`
   (odstrániť skladové prefixy/čísla, nadbytočné medzery).
2. Zavolať hľadanie na webe príslušného dodávateľa.
3. Zparsovať kandidátov (názov + URL, prípadne kód/cena).
4. Ohodnotiť a zoradiť kandidátov, vybrať najlepšieho:
   - zhoda podľa kódu (kód sa vyskytuje v kandidátovi) → **vysoká istota**,
   - zhoda podľa názvu → skóre podobnosti (prekryv slov / fuzzy) → **stredná/nízka**.
5. Doplniť **vždy** najlepší výsledok (voľba: auto-fill všetko), istotu zapísať do reportu.

### Fáza 2.5 — overenie každého produktu (AI kontrola)
Po napárovaní **skontrolovať každý jeden produkt**, či je odkaz na správny produkt:

1. Otvoriť zvolenú URL u dodávateľa a prečítať z nej: názov, kód (ak je),
   cenu, prípadne obrázok.
2. Porovnať s forestshop produktom (`name`, `externalCode`) a vydať verdikt:
   - **OK** — to isté (napr. kód sa zhoduje, alebo názov/atribúty sedia),
   - **NESPRÁVNE** — zjavne iný produkt,
   - **NEISTÉ** — nedá sa jednoznačne určiť.
   + krátky dôvod.
3. **Samoopravná slučka:** ak verdikt = NESPRÁVNE, skúsiť ďalšieho kandidáta
   z hľadania a overiť znova (max N pokusov). Ak nič nesedí → označiť ako
   neoverené/nenapárované.
4. Verdikt + dôvod + počet pokusov zapísať do `match_report.csv`.

Realizácia: pri ~700 produktoch paralelný **Workflow** (fan-out agentov;
pipeline hľadanie → overenie → prípadné prehľadanie). Lacná skratka pre
zhodu podľa kódu (kód prítomný na stránke = OK bez ďalšieho čítania);
hlbšie čítanie len pre zhody podľa názvu.

### Fáza 3 — výstupy
Zapísať tri súbory (cp1250, `;`).

## 5. Výstupy

1. **`import_betalov_wetland.csv`** — Shoptet čiastočný pre-import.
   - Stĺpce: `code;textProperty10`.
   - Jeden riadok na **každý variant** produktu (tá istá URL pre všetky veľkosti).
   - Len napárované produkty.
2. **`match_report.csv`** — kontrolný report, jeden riadok na produkt:
   `supplier, externalCode, name, query, chosen_url, confidence, candidate_count, variant_count, verdict, verdict_reason, attempts`.
   (`verdict` = OK / NESPRÁVNE / NEISTÉ z Fázy 2.5.)
3. **`unmatched.csv`** — produkty bez výsledku (na ručné dohľadanie).

## 6. Robustnosť a slušnosť

- Throttle medzi requestmi (napr. 0,5–1 s), realistický User-Agent.
- Cache podľa query (rovnaký dotaz sa nehľadá dvakrát).
- Retry s backoffom pri chybe/timeoute.
- **Checkpoint/resume** — priebežne ukladať hotové produkty, pád nereštartuje
  všetkých ~700 hľadaní od nuly.
- Komplexné logovanie každého requestu (query, URL, počet kandidátov, výsledok).

## 7. Technika

- Python 3, `requests` + `BeautifulSoup`; `playwright` ako záloha pri JS renderi.
- Štruktúra: oddelené moduly — načítanie CSV / zoskupenie / klient dodávateľa
  (rozhranie `search(query) -> [candidate]`) / ranking / zápis výstupov.
  Pridanie ďalšieho dodávateľa = nový klient implementujúci rozhranie.
- Git: dve vetvy `main` (produkčná) a `dev` (vývoj).

## 8. Testovanie

- Jednotkové testy (bez živej siete v CI, na fixtúrach uložených HTML):
  - zoskupenie variantov do produktov (pairCode + záloha názov),
  - ranking/výber kandidáta (kód-exact > názov-fuzzy),
  - parsovanie výsledkov hľadania pre každý web (uložené HTML vzorky),
  - CSV vstup/výstup vrátane kódovania cp1250.
- Voliteľný živý smoke test (pár produktov) mimo CI.

## 9. Riziká

- **Párovanie podľa názvu** (WETLAND 74 % bez kódu) sa môže trafiť na zlý produkt
  → mitigácia: overenie každého produktu (Fáza 2.5) + samooprava + verdikt
  v `match_report.csv` na spätnú kontrolu.
- Web môže blokovať časté requesty → throttle + retry + cache.
- Štruktúra výsledkov hľadania sa môže líšiť / byť JS-renderovaná → Fáza 0 + Playwright.
- Kódovanie cp1250 (vstup aj výstup) – dôsledne držať, inak rozbité diakritiky.

## 10. Rozhodnutia (potvrdené s majiteľom)

- Zdroj katalógu dodávateľa: **sťahovanie z webu** (žiadny feed).
- Cieľové pole: **`textProperty10`**, hodnota = **holá URL**.
- Neisté zhody: **doplniť všetko automaticky** (+ kontrolný report).
- **Overiť každý produkt** AI kontrolou (Fáza 2.5) + samooprava + verdikt v reporte.
- Prvá dávka: **BETALOV + WETLAND**.
