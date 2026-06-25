# Automatický import CSV do Shoptet adminu

Dátum: 2026-06-25
Stav: návrh (pred implementačným plánom)

## 1. Cieľ

Nahradiť ručný krok „stiahni import CSV → prihlás sa do Shoptetu → naimportuj".
Script, ktorý sa **na povel** prihlási do Shoptet adminu, nahrá náš vygenerovaný
import CSV cez **Produkty → Import**, s presne dohodnutými nastaveniami, a prečíta
späť skutočný výsledok z **Logu**. Dôraz na **opatrnosť**: nič sa nezapíše do
ostrého eshopu, kým CSV neprejde kontrolou a kým majiteľ nepotvrdí.

## 2. Rozhodnutia (odsúhlasené v brainstormingu)

- **Cesta importu:** prihlásenie do adminu + nahranie CSV (overený ručný postup,
  len zautomatizovaný). NIE Shoptet API, NIE naplánovaný XML feed.
- **Prihlásenie:** meno + heslo uložené lokálne, script sa prihlasuje plne sám
  (žiadne 2FA na tomto admine).
- **Spúšťanie:** na povel (CLI), nie na rozvrhu.
- **Bezpečnosť:** pred-letová kontrola CSV + potvrdenie pred nahraním + prečítanie
  skutočného výsledku po importe.
- **Záloha:** pred KAŽDÝM ostrým importom sa najprv stiahne export aktuálneho
  katalógu (na vrátenie späť).
- **Kontrola parametrov:** tesne pred spustením importu script PREČÍTA skutočný
  stav importačného formulára a overí, že sú správne parametre nastavené
  (UTF-8, „nahradiť prázdne" VYPNUTÉ, párovať podľa Kódu); ak nesedí → NEimportuje.

## 3. Architektúra

Dva celky s jasnou hranicou:

| Časť | Súbor | Zodpovednosť | Testovateľné |
|---|---|---|---|
| Čistá logika | `src/parovanie/shoptet_import.py` | načítať údaje, pred-letová kontrola CSV, zostaviť „plán importu" (počty, rozpis), naparsovať výsledný log | **áno** — unit testy, bez siete/prehliadača |
| Obálka (I/O) | `scripts/shoptet_import.py` | CLI; riadenie prehliadača (Playwright): login → import → čítanie Logu | nie (integračné, overí sa naživo) |

Generovanie CSV ostáva oddelené (web „⬇ Stiahnuť import" alebo
`scripts/build_decisions_import.py`). Tento script len **importuje súbor**, na
ktorý ho ukážeš (default `data/out/import.csv`).

## 4. Údaje (secret)

`data/.shoptet_admin` — **gitignored, `chmod 600`** (rovnaký vzor ako Cloudflare
tokeny `data/.cf_*`). Tvar `KEY=value`:

```
SHOPTET_ADMIN_URL=https://www.forestshop.sk/admin/
SHOPTET_USER=...
SHOPTET_PASS=...
SHOPTET_EXPORT_URL=https://www.forestshop.sk/export/products.csv?patternId=14&partnerId=3&hash=...
```

`SHOPTET_EXPORT_URL` = priamy export celého katalógu (pattern 14, obsahuje
`hash` = partner credential) — používa sa na **zálohu pred importom**. Bez neho
sa ostrý import NESPUSTÍ (nemali by sme zálohu).

**Čestná poznámka:** nejde o „šifrovanie" v pravom zmysle — plne automatické
prihlásenie znamená, že heslo musí byť čitateľné pre script. Chránime ho právami
(600) a tým, že nie je v gite — presne ako ostatné naše tajomstvá. Silnejšia
voľba (mimo rozsahu): heslo cez `systemd EnvironmentFile` / vault. Načítanie
zlyhá nahlas, ak súbor chýba alebo nemá všetky tri kľúče.

## 5. Priebeh (krok po kroku)

1. **Pred-letová kontrola CSV** (hlavná poistka, bez prehliadača):
   - súbor existuje, dá sa prečítať ako **UTF-8 (s BOM)**,
   - hlavička obsahuje aspoň `code` a `pairCode` (a očakávané stĺpce),
   - spočíta riadky a **rozpíše** podľa typu: napárované (link), „nie je skladom",
     „už sa nebude predávať",
   - akákoľvek chyba → **stop, nič sa nenahrá**, návratový kód ≠ 0.
2. **Plán + potvrdenie:** vypíše súbor, počty a rozpis; čaká na `ano` v termináli.
   Prepínač `--yes` preskočí pýtanie (pre neobsluhované spustenie).
3. **Záloha exportu** (pred ostrým importom, VŽDY): stiahne `SHOPTET_EXPORT_URL`
   do `data/backups/export_<čas>.csv`. Ak sa nepodarí (chýba URL, sieť, prázdny
   súbor) → **stop, NEimportuje** — radšej žiadny import ako import bez zálohy.
   Pri `--dry-run` sa preskakuje.
4. **Login:** headless Chromium (Playwright), prihlásenie údajmi zo súboru.
   Overí, že je naozaj prihlásený (inak stop).
5. **Nahranie + nastavenie:** Produkty → Import → nahrá CSV → nastaví **kódovanie
   UTF-8**, **„nahradiť prázdne hodnoty" = VYPNUTÉ**, **párovať podľa Kódu**.
6. **Kontrola parametrov (read-back):** PREČÍTA skutočný stav formulára a overí,
   že kódovanie = UTF-8, „nahradiť prázdne" = VYPNUTÉ, párovanie = Kód. Ak
   čokoľvek nesedí → **stop, NEspustí import** a povie, ktorý parameter je zlý.
7. **Spustenie + výsledok:** spustí import, prečíta záložku **Log** → naparsuje a
   vypíše skutočný výsledok (Spracované / Upravené / Zlyhania variantov). Uloží
   screenshot + textový log do `data/out/shoptet_import_<čas>.{png,log}` na audit.
   Ak Shoptet hlási zlyhania → návratový kód ≠ 0 a jasná správa.

## 6. Prepínače CLI

- `--file PATH` — ktoré CSV (default `data/out/import.csv`).
- `--yes` — bez interaktívneho potvrdenia.
- `--dry-run` — len pred-letová kontrola + login + overenie, že vie dôjsť na
  import; **nič nenahrá ani nespustí**. Na bezpečné otestovanie plumbingu.
- `--headful` — ukáže okno prehliadača (na ladenie / prvé overenie).

## 7. Bezpečnosť / „opatrne"

- Žiadny zápis do eshopu pred kontrolou CSV a potvrdením.
- **Záloha exportu pred KAŽDÝM ostrým importom** — bez úspešnej zálohy sa
  neimportuje (dá sa vrátiť späť re-importom zálohy).
- **Kontrola importačných parametrov tesne pred spustením** (read-back UTF-8 /
  „nahradiť prázdne" VYPNUTÉ / párovať podľa Kódu) — nesedí → import sa nespustí.
- `--dry-run` overí celý reťazec okrem zálohy a ostrého zápisu.
- Po importe sa číta **skutočný** výsledok — nie „tváril sa OK".
- Heslo nikdy do gitu; zálohy do `data/backups/`, logy/screenshoty do `data/out/`
  (oboje gitignored).
- Komplexné logovanie každého kroku (záloha, login, upload, kontrola parametrov,
  výsledok).

## 8. Testovanie

- **Unit testy** (CI, bez siete) na čistú logiku:
  - načítanie údajov: kompletný súbor prejde; chýbajúci kľúč / súbor → jasná chyba,
  - pred-letová kontrola: dobré CSV prejde; CSV bez `code`/`pairCode` spadne;
    počty a rozpis sedia (fixtúra),
  - parser výsledného Logu: z ukážkového textu vytiahne Spracované/Upravené/Zlyhania.
- **Riadenie adminu** sa v CI testovať NEDÁ (treba živé heslo a menilo by ostrý
  eshop). Overí sa **naživo** oproti ozajstnému adminu: `--dry-run`, potom skutočný
  import a kontrola výsledku z Logu — nahlásené s dôkazom (autonomous verification).

## 9. Závislosti

- `playwright` (Python) + `playwright install chromium` (runtime pre script;
  do CI sa nepridáva ako test browseru — riadenie adminu nie je v CI).
- Bez novej DB, bez zmeny schémy, bez zmeny generovania importu.

## 10. Mimo rozsah (YAGNI)

- Rozvrh / cron (zatiaľ na povel).
- Shoptet API cesta.
- XML feed.
- Šifrovanie hesla passphrase-om (odporuje plne automatickému behu).
