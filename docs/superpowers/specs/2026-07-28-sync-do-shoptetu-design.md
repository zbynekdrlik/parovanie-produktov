# Sync do Shoptetu — hodinový obojsmerný cyklus cez jednu tabuľku čakajúcich zmien

**Ticket:** #299. Súvisiace: #300 (reštok nebeží nikde), #301 (zaseknuté kódy),
#302 (zamrznutý zdroj GRUBE), #189 (zápis poznámky k objednávke).

**Cieľ (šéf, 28.7.2026):** eshop a naša appka majú byť zosynchronizované každú hodinu
v OBOCH smeroch. Dnes je hodinové len sťahovanie; nahrávanie ide raz za noc a väčšina
zápisových automatizácií nebeží vôbec.

---

## 1. Východiskový stav (merané 28.7.2026, nie odhadované)

### 1.1 Čo dnes beží

| Automatizácia | Rozvrh | Stav v `data/out/automations.json` |
|---|---|---|
| `shoptet_sync` (sťahovanie) | každých 60 min | zapnuté |
| `parovania_eshop` (párovania + dodávatelia) | denne 21:00 | zapnuté |
| `dodavatelsky_sklad` (zber u dodávateľov) | denne 05:00 | zapnuté |
| `grube_externalcode` | denne 03:30 | **nikdy nezapnuté** |
| `split_links` | denne 03:45 | **nikdy nezapnuté** |
| `restock_skladom` | denne 06:00 | **nikdy nezapnuté** |
| `stock_skladom` | denne 06:45 | **nikdy nezapnuté** |

n8n workflow „Forestshop — Vypredané → Skladom v2" (`KN1BE18HLdM8mfTc`) je
`active: false` — odovzdanie z n8n do appky sa nikdy nedokončilo (#300).

### 1.2 Rozsah prvého behu (výpočtom nad živými dátami, bez zápisu)

| Cesta | Riadkov | Poznámka |
|---|---|---|
| Párovania | 0 | 2 riadky zadržané, kód nie je v katalógu (#301) |
| Dodávatelia | 0 | 1 priradenie zadržané z rovnakého dôvodu |
| GRUBE kódy | 0 | 260/260 už nahratých; zdroj starý 5 dní (#302) |
| Veľkostné linky | 0 | `variant_links.json` je prázdny |
| Vypredané → Skladom | **19** | 19 produktov sa vráti do predaja |
| Máme skladom → Skladom | **16** | z toho 9 kódov spoločných s reštokom |

Spolu 35 riadkov, 2 dávky, ~60 s zápisu. **Zapnutie všetkého naraz je bezpečné.**

### 1.3 Jediný kanál zápisu

Všetkých 7 zápisových miest ide cez `_import_rows_chunked()`
(`webreview/app.py:5138`) → `run_import()` (`app.py:5073`) → subprocess
`scripts/shoptet_import.py` → Playwright login + upload na
`/admin/import-produktov/` → parsovanie Shoptet Logu
(`src/parovanie/shoptet_import.py`). Žiadne REST API. Dávka max 300 riadkov
(~30 s/dávka, timeout 900 s), globálny `_import_lock` (`app.py:228`).

Shoptet **nemá import objednávok cez CSV** (overené, 404) — poznámka k objednávke
sa dá zapísať len cez detail objednávky v administrácii, jedna po druhej.

---

## 2. Rozhodnutia šéfa (28.7.2026) — záväzné

1. **Appka je pán nad svojimi poľami.** Čo appka spravuje, to hodinový sync nastaví
   podľa nás a ručnú zmenu spravenú priamo v administrácii Shoptetu prepíše.
2. **Poradie cyklu: stiahnuť → nahrať → stiahnuť.** Okno na rozladenie oboch strán
   má byť čo najkratšie.
3. **Poznámka z appky sa PRIPÍŠE POD** existujúcu poznámku pri objednávke, neprepíše ju,
   a musí byť označená, aby ju ďalší beh vedel nájsť a prepísať len svoju časť.
4. **Jedna tabuľka čakajúcich zmien**, nie dirigent nad piatimi samostatnými importami.

---

## 3. Architektúra

```
automatizácie (výpočty ostávajú)          jedna tabuľka            hodinový cyklus
─────────────────────────────────         ──────────────           ────────────────
parovania_eshop  ─┐
dodavatelia      ─┤                                                1. stiahnuť
grube_externalcode├─ queue_shoptet_fields ─► pending_shoptet.json ─ 2. spustiť producentov
split_links      ─┤                          (kód → polia)          3. JEDEN import
restock_skladom  ─┤                                                 4. overiť z Logu
stock_skladom    ─┘                                                 5. stiahnuť znova

order_comments.json ──────────────────────► vlastná dráha ────────  6. poznámky per objednávka
```

### 3.1 Tabuľka čakajúcich zmien — `data/out/pending_shoptet.json`

Nový store deklarovaný cez `_store("pending_shoptet.json")` (nikdy
`os.path.join(OUT, …)` — #261), čítaný `_read_json_store`, zapisovaný
`_atomic_write_json(..., protect=True)`, read-modify-write vždy pod `with _lock:`,
doplnený do `scripts/backup_data.sh`.

Tvar — kľúč je variantný `code`, lebo import potrebuje `code` aj `pairCode`:

```json
{
  "60648": {
    "pairCode": "60648",
    "fields": {
      "internalNote": {
        "value": "https://…",
        "source": "parovania_eshop",
        "dedup_key": "review:BETALOV|60648",
        "queued_at": "2026-07-28T14:05:00+02:00"
      },
      "availabilityInStock": { "value": "Skladom", "source": "restock_skladom", "dedup_key": null, "queued_at": "…" }
    },
    "blocked": null,
    "attempts": 0
  }
}
```

- `source` — kľúč automatizácie, ktorá hodnotu vložila. Slúži na (a) zobrazenie
  „kto to tam dal", (b) spätné označenie v dedup storoch po potvrdenom zápise.
- `dedup_key` — kľúč do príslušného `uploaded_*.json` (alebo `null`, keď producent
  dedup nepoužíva, napr. reštok). Po potvrdenom importe drain označí kľúče ako nahraté.
- `blocked` — `null` alebo `{"reason": "not-in-catalog", "since": "…"}`. Blokovaný
  riadok **zostáva v tabuľke** a je vidieť v karte (nikdy sa ticho nezahodí — #270, #301).
- `attempts` — počet neúspešných pokusov; po 3 sa riadok označí ako problémový
  a vyzdvihne v karte (nie zmaže).

**Konflikt dvoch zdrojov na to isté pole:** vyhráva posledný zápis, do logu ide
`WARNING` s oboma zdrojmi. Reálne sa prekrývajú len `restock_skladom` a
`stock_skladom` (9 spoločných kódov) a obe nastavujú rovnakú hodnotu.

### 3.2 Producenti

Každá zo šiestich zápisových ciest si ponecháva svoj výpočet a svoje dedup úložisko,
ale **posledný krok sa mení**: namiesto `_import_rows_chunked(...)` volá

```python
queue_shoptet_fields(source: str, rows: list[dict], dedup_keys: dict[str, str] | None) -> int
```

ktorá riadky rozloží na polia a zapíše do tabuľky. Vracia počet zaradených polí.
Producent naďalej hlási svoj vlastný výsledok do svojej karty (koľko zaradil,
koľko preskočil a prečo) — mení sa len to, že už sám nič nenahráva.

Producenti ostávajú samostatné automatizácie s vlastným zapnutím. Cyklus spustí
len tie, ktoré sú zapnuté.

### 3.3 Hodinový cyklus — automatizácia `shoptet_upload` („Sync do Shoptetu")

`Automation(key="shoptet_upload", name="Sync do Shoptetu",
schedule={"interval_minutes": 60, "tz": "Europe/Bratislava"},
run_fn=run_shoptet_upload)`, registrovaná v `AUTOMATIONS_REG`
(`webreview/app.py:8578`), **default vypnutá**.

Kroky jedného behu:

1. **Stiahnuť** — zavolá `RUNNER.run_now("shoptet_sync")`, aby si `shoptet_sync`
   zapísal vlastný stav do svojej karty. Preskočí sa, ak jeho posledný úspešný beh
   je mladší než 10 minút (zbytočne by sťahoval 57 MB katalógu druhý raz).
2. **Spustiť producentov** — postupne `RUNNER.run_now(key)` pre každého zapnutého
   producenta v poradí `parovania_eshop`, `grube_externalcode`, `split_links`,
   `restock_skladom`, `stock_skladom`. Beží sekvenčne; zlyhanie jedného producenta
   cyklus nezastaví, len sa zapíše do jeho karty a do súhrnu behu.
3. **Postaviť JEDEN importný súbor** z tabuľky: stĺpce = `code;pairCode` + zjednotenie
   všetkých čakajúcich polí, prázdna bunka = pole sa nemení. Riadky, ktorých kód
   katalóg nemá, sa cez existujúcu bránu `_export_row_verdicts` (`app.py:5495`)
   **nezaradia** a dostanú `blocked`.
4. **Nahrať** cez `_import_rows_chunked` (dávky ≤300) a **overiť z Logu** — počet
   `Spracované: N` musí sedieť s počtom odoslaných riadkov (`pick_result_row`,
   `src/parovanie/shoptet_import.py:248`). Nepotvrdená dávka = riadky ostávajú
   v tabuľke, `attempts += 1`.
5. **Vyprázdniť potvrdené** — potvrdené polia sa z tabuľky odstránia a ich
   `dedup_key` sa dopíšu do príslušných `uploaded_*.json` (teraz to robí drain,
   nie producent — dedup tak nikdy nepredbehne skutočný zápis).
6. **Stiahnuť znova** — `RUNNER.run_now("shoptet_sync")`. **Preskočí sa, keď sa
   nahralo 0 riadkov** (väčšina hodín) — ušetrí 57 MB katalógu za beh.
7. **Poznámky k objednávkam** (vlastná dráha, viď 3.4).

Celý cyklus drží **jeden nárok** (`fcntl.flock` na `data/out/.shoptet_cycle.lock`,
rovnaký vzor ako `.scheduler.lock`, `app.py:8722`), aby sa samostatne spustené
hodinové sťahovanie nedostalo doprostred cyklu. `shoptet_sync` si pred behom nárok
overí a keď ho drží cyklus, preskočí sa (nie zlyhá).

### 3.4 Poznámky k objednávkam (#189)

Vlastný krok cyklu, lebo Shoptet nemá CSV import objednávok:

- Kandidáti = objednávky, ktorých `order_comments.json` sa zmenil od posledného
  úspešného zápisu (nový store `pushed_order_comments.json`: `orderCode → hash`).
- Zápis cez Playwright na detail objednávky v administrácii (deep-link
  `/admin/vyhladavanie/?string=<orderCode>&src=orders`, overené v #189).
- **Idempotencia:** naša časť je ohraničená značkou, napr.

  ```
  <text, ktorý tam napísal človek — nikdy sa nemení>
  --- z appky (needitovať) ---
  <naša poznámka>
  --- koniec z appky ---
  ```

  Ďalší beh nájde blok podľa značiek a prepíše **len jeho obsah**. Text nad aj pod
  blokom ostáva. Keď blok neexistuje, pripíše sa na koniec.
- Strop na beh (napr. 30 objednávok/hodinu), aby jedna dávka nezablokovala cyklus;
  zvyšok ide nasledujúcu hodinu. Prekročený strop je vidieť v karte, nie je tichý.

### 3.5 UI — karta v priečinku System

Druhá položka `SYSTEM_TABS` (`webreview/static/app.js:490`), plus záznamy v
`NAV_ICONS`, `PAGE_TITLES`, `NAV_KEYS` (`app.py:8794`), `AUTOMATION_DESCRIPTIONS`
(`app.py:8521`) — všetky štyri strážia drift-testy, takže vynechaný krok padne testom.

Karta ukazuje:

- štandardné veci automatizácie (zapnuté/vypnuté, posledný beh, ďalší beh, ⚡ spustiť teraz)
- **„Čaká na nahratie: N zmien"** s rozbaliteľným zoznamom: kód, pole, nová hodnota,
  kto to zaradil, odkedy čaká
- **„Zablokované: M"** červeno, s dôvodom pri každom (napr. „kód nie je v katalógu")
- súhrn posledného behu: nahraté / potvrdené / zablokované / poznámky zapísané

---

## 4. Tiché smrti, ktoré musia byť hlasné (`.claude/rules/automation-health.md`)

Každý z týchto stavov dostane príznak v `last_result`, ERROR/WARNING do logu,
farebný banner v karte so slovenským textom a číslami, a zdvihne `navError()`:

| Stav | Prečo je nebezpečný |
|---|---|
| import prebehol, ale Log nepotvrdil počet riadkov | mysleli by sme si, že je nahraté, a dedup by to už nikdy neposlal |
| riadok je `blocked` viac než 3 behy | ticho by čakal navždy (dnešný stav #301) |
| producent zapnutý, ale zaradil 0 polí N behov po sebe | jeho zdroj zamrzol (dnešný stav GRUBE, #302) |
| zdroj producenta je starší než jeho limit | reštok by naskladňoval podľa starých dodávateľských dát |
| cyklus nedokončil krok „stiahnuť znova" | naša kópia je pozadu za tým, čo sme sami zapísali |
| poznámky prekročili strop na beh | manažér by nevedel, že časť čaká |

Fail-closed pravidlo z `.claude/rules/store-prune.md` platí aj tu: keď je katalógový
export neprávoplatný (prázdny/implauzibilný), cyklus **nenahráva** — radšej nič než
nahrať podľa nedôveryhodného stavu.

---

## 5. Poradie zavádzania (aby tri bežiace automatizácie nevypadli)

1. Tabuľka + `queue_shoptet_fields` + drain + karta, **žiadny producent zatiaľ neprepnutý**.
   Cyklus beží naprázdno (0 čakajúcich), overí sa krok stiahnuť → nič → stiahnuť.
2. Prepnúť **vypnutých** producentov (`grube_externalcode`, `split_links`) — nulové
   riziko, dnes neposielajú nič.
3. Prepnúť `restock_skladom` a `stock_skladom`, spustiť ich **ručne** cez „⚡ Spustiť
   teraz" a v Shoptet administrácii overiť, že sa zmenilo presne ~19 + 16 riadkov.
4. Prepnúť `parovania_eshop` (dnes jediný živý zápis) — až keď kroky 1–3 prebehli.
5. Zapnúť hodinový cyklus.
6. Poznámky k objednávkam (3.4) ako posledné.

---

## 6. Testy

- **Jednotkové:** tvar tabuľky, zaradenie/zlúčenie polí od dvoch zdrojov na jeden kód,
  stavba jedného CSV zo zmiešaných polí (prázdne bunky), potvrdenie z Logu vs počet
  riadkov, `blocked` cesta, spätné označenie dedup kľúčov až po potvrdení,
  idempotencia bloku poznámky (dva behy nesmú text zduplikovať).
- **Odolnosť:** nedokončený import nechá riadky v tabuľke; poškodená tabuľka je
  fail-closed (503 s návodom, nie tiché „0 čakajúcich"); nárok cyklu blokuje súbežné
  sťahovanie; `StoreLockTimeout` počas drainu nezasekne automatizáciu.
- **E2E:** karta v priečinku System pod „Sync zo Shoptetu", zoznam čakajúcich zmien,
  červený zoznam zablokovaných, čistá konzola.
- Testy nikdy nesmú siahnuť na `data/out` ani zavolať reálny import — fixture server
  s vlastným `WEBREVIEW_OUT`, import zastúpený dvojníkom.

---

## 7. Mimo rozsah

- Migrácia `data/out/*.json` na skutočnú SQL databázu — tabuľka čakajúcich zmien je
  jeden nový JSON store v rovnakom vzore ako ostatné.
- Zrušenie n8n workflowov a zneplatnenie ich prístupového tokenu — samostatná úloha
  po tom, čo appková cesta beží (#300).
- Riešenie príčiny zaseknutých kódov (#301) a zamrznutého zdroja GRUBE (#302).

## 8. Otvorené, čo sa doplní meraním počas implementácie

- **Oneskorenie exportu po importe** — o koľko neskôr export zo Shoptetu odráža
  práve nahraté zmeny. Zmeria sa pri prvom potvrdenom zápise (krok 3 zavádzania).
  Dovtedy platí: dôkazom zápisu je Log, nie druhé stiahnutie.
- **Trvanie jednej dávky naživo** (predpoklad ~30 s z komentára v kóde).
