# Webreview — moderný sidebar-dashboard redesign

**Dátum:** 2026-07-22
**Stav:** Schválený smer (manažér/vlastník videl klikací mockup, odpoveď „pecka" = ideme).
**Mockup (schválený, durable):** `docs/design/2026-07-22-dashboard-mockup.html`

## Cieľ

Prerobiť VZHĽAD kontrolného webu (`webreview/`) z „taby hore" na **moderný ľavý sidebar-dashboard**
v štýle referenčnej appky (admin.sterio.app / Chobotničiari), aby sa z nástroja postupne stal
hlavný optimalizovaný dashboard eshopu. **Funkcie ostávajú identické — mení sa len kabát a rozloženie.**

Toto je VIZUÁLNY/LAYOUT redesign. **Žiadna zmena správania, endpointov, ani dát manažéra.**

## Čo sa mení (layout + CSS)

1. **Ľavý sidebar** (biely, sticky, collapsible neskôr): brand „Forestshop · Párovač & dashboard",
   nav = naše funkcie (Kontrola párovania / Na objednanie / Hľadať-opraviť / Poznámky) s outline
   ikonami a počítadlami; sekcia „Čoskoro — rozšírime" s muted placeholdermi (Prehľad eshopu,
   Import do eshopu, Dodávatelia, Štatistiky) = miesta na budúce rozširovanie; dole tmavý-mód
   prepínač + verzia.
2. **Top bar** (v hlavnej ploche): veľký nadpis stránky + podnadpis (počty) + vpravo akčné tlačidlá
   (primárne zelené „Stiahnuť import" na review; „Ručné doobjednanie" na to-order; „Nová poznámka").
3. **Karty/riadky**: zaoblené (radius 12), 1px border + jemný tieň, viac bieleho priestoru;
   status-pilulky so sémantickými farbami (zelená=dobré/hotové, modrá=manuál, červená=zlé/nedostupné,
   sivá=nedostupné/rozpracované, jantár=čaká).
4. **Filtračné pilulky** review-stavov ostávajú, len nový štýl (rounded, s počítadlom).
5. **Tmavý mód** — nový prepínač v sidebar-e, `localStorage` + `[data-theme=dark]` na `<body>`,
   plná paleta cez CSS premenné.
6. **Akcent = zelená** (forestshop/lovecký). Paleta cez CSS premenné, aby sa dala neskôr zmeniť 1 riadkom.

## Tvrdé obmedzenia (nesmie sa porušiť)

- **Zachovať VŠETKY existujúce data-testid / id / class**, na ktoré sa viažu app.js AJ E2E testy
  (`tests/e2e/*`, `tests/test_webreview.py`) — selektorový kontrakt. Ak sa DOM presúva, testid ostáva.
- **Zachovať všetky funkcie a endpointy**: review potvrdiť/zlé/ručne, to-order toggly
  (objednané/počkať/skladom/nedostupné), supplier chips + ich farby (zelená=treba riešiť,
  červená=všetko poriešené, oranžová=vybraná), filtre, progress, „⬇ Stiahnuť import", hľadanie
  cez všetky polia, poznámky (done/delete).
- **Dáta manažéra** (`data/out/*.json`) sa NEDOTÝKAME — žijú cez deploy; over počty pred/po.
- `/api/version` footer/label musí ostať čitateľný (verzia sa overuje z DOM po deployi).
- Kódovanie, CSV a import pravidlá sa netýkajú tohto redesignu (žiadny backend logic change okrem
  prípadného servírovania nového shellu).
- Existujúce E2E MUSIA ostať zelené; nové E2E pre sidebar-nav prepínanie + tmavý-mód persistenciu.

## Design tokens (z mockupu — zdroj pravdy je mockup CSS)

Svetlý: bg `#f6f7f9`, panel/sidebar `#ffffff`, border `#e8eaed`, text `#1f2430`, muted `#6b7280`,
akcent `#15803d` (hover `#116330`, soft `#dcfce7`). Status: done `#16a34a`/`#dcfce7`,
bad `#dc2626`/`#fee2e2`, manual `#2563eb`/`#dbeafe`, wait `#d97706`/`#fef3c7`, grey `#6b7280`/`#f1f3f5`.
Radius 12 (karty) / 8-9 (tlačidlá/pilulky). Tieň `0 1px 3px rgba(16,24,40,.06)`.
Tmavý: `[data-theme=dark]` prepis premenných (viď mockup).

## Rozsah / MVP

Fáza 1 (tento redesign): shell + CSS + prepojenie existujúcich funkcií do nového layoutu + tmavý mód
+ „Čoskoro" placeholdery (neklikateľné). Skutočné nové stránky (Prehľad/Import/Dodávatelia/Štatistiky)
sú BUDÚCE tickety, nie súčasť tejto fázy.
