# Párovanie produktov → odkaz na dodávateľa

Pre produkty dodávateľov **BETALOV** (huntingshop.eu) a **WETLAND** (wetland.sk)
nájde odkaz na produktovú stránku u dodávateľa a zapíše ho do poľa
`textProperty10` (Shoptet pre-import). Pole slúži automatizácii doobjednávania.

## Inštalácia

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Vstup

Shoptet export celého katalógu (cp1250, `;`) ulož do `data/products.csv`:

```bash
mkdir -p data
curl -sL "https://www.forestshop.sk/export/products.csv?patternId=14&partnerId=3&hash=<HASH>" \
  -o data/products.csv
```

## Spustenie — párovanie

```bash
PYTHONPATH=src .venv/bin/python -m parovanie.cli \
  --input data/products.csv --out data/out --suppliers BETALOV,WETLAND
```

Výstupy v `data/out/`:

- `import_betalov_wetland.csv` — Shoptet pre-import (`code;textProperty10`),
  jeden riadok na každý variant produktu. Importuj v Shoptete (Produkty → Import).
- `match_report.csv` — kontrolný report (1 riadok/produkt): dodávateľ, kód, názov,
  query, zvolená URL, istota, počet kandidátov, + stĺpce `verdict`/`verdict_reason`/`attempts`
  z overenia.
- `unmatched.csv` — produkty bez výsledku.
- `run.log` — log každého requestu.
- `checkpoint.json` — priebeh (pri páde sa beh obnoví, nehľadá od nuly).

## Overenie (Fáza 2.5 — AI kontrola každého produktu)

Po napárovaní spustí AI workflow, ktoré otvorí každú zvolenú URL, porovná
s forestshop produktom a vydá verdikt OK / NESPRÁVNE / NEISTÉ; pri NESPRÁVNE
skúsi ďalšieho kandidáta (samooprava). Postup: `docs/runbooks/verification-workflow.md`.

## Smoke (živá kontrola na malej vzorke)

```bash
PYTHONPATH=src .venv/bin/python scripts/smoke.py 10
```

Vypíše napárovanie pre prvých 10 produktov každého dodávateľa proti živým webom.

## Web na ručnú kontrolu (webreview)

Vizuálne porovnanie náš-produkt ↔ dodávateľ s fajka/krížik (a ručným výberom URL
pri nenapárovaných). Rozhodnutia sa ukladajú do `data/out/decisions.json`.

```bash
PYTHONPATH=src .venv/bin/python scripts/build_review_data.py   # postaví review_data.json
pip install flask   # ak nie je
PYTHONPATH=src .venv/bin/python webreview/app.py               # počúva na 0.0.0.0:8801
```

Otvor `http://<LAN-IP>:8801/`. Vľavo náš produkt (názov + obrázky), vpravo dodávateľ
(názov, klikateľná URL, obrázky). Napárované → ✓ Dobré / ✗ Zlé; nenapárované → výber
z kandidátov alebo vlastná URL. `decisions.json` potom drží `{idx: {status, url}}`.

## Auto-import do eshopu (opatrný script)

Namiesto ručného importu v admine: `scripts/shoptet_import.py` sa prihlási do
Shoptet adminu a nahrá import CSV — s poistkami. Prihlásenie + export URL sú v
`data/.shoptet_admin` (chmod 600, mimo gitu).

```bash
.venv/bin/pip install -r requirements-import.txt && .venv/bin/playwright install chromium  # raz
PYTHONPATH=src .venv/bin/python scripts/shoptet_import.py --dry-run     # len overí, NIČ nenahrá
PYTHONPATH=src .venv/bin/python scripts/shoptet_import.py               # ostrý import (pýta potvrdenie)
```

Poistky (v poradí): pred-letová kontrola CSV (kódovanie, `code`+`pairCode`, rozpis
riadkov) → potvrdenie (`--yes` preskočí) → **záloha exportu** do `data/backups/`
(bez zálohy sa neimportuje) → login → nahranie + **read-back kontrola bezpečných
parametrov** (režim „Nemeniť produkty mimo súboru", nie „Zmazať"; „Zmeniť URL podľa
názvu" VYPNUTÉ) → spustí → prečíta skutočný výsledok z Logu. Shoptet kódovanie
auto-detekuje (náš BOM = UTF-8) a páruje podľa `code`. Viac v `.claude/skills/shoptet`.

## Verejné nasadenie (Cloudflare Tunnel)

Web je verejne na **https://parovanie-forestshop.newlevel.media** cez `cloudflared`
tunel (žiadny otvorený port navonok). Dve `systemd --user` služby (Restart=always, linger):

- `parovanie-web.service` — Flask na :8801
- `parovanie-tunnel.service` — cloudflared (tunel token v `data/.cf_env`, gitignored)

```bash
systemctl --user restart parovanie-web parovanie-tunnel   # reštart
journalctl --user -u parovanie-web -f                      # logy
```

Cloudflare tunel/DNS spravované cez API; lokálne tokeny v `data/.cf_*` (gitignored, NIE v gite).

## Architektúra

| Modul | Zodpovednosť |
|---|---|
| `csv_loader` | načítanie Shoptet CSV (cp1250), filter podľa dodávateľa |
| `grouping` | varianty → produkty (kľúč `pairCode`, záloha názov) |
| `normalize` | čistenie názvu, stavba query |
| `suppliers/wetland`, `suppliers/betalov` | parsovanie výsledkov hľadania (PrestaShop / Nette) |
| `client` | HTTP klient: session warmup (cookie), throttle, retry, cache |
| `matcher` | query-rebrík (kód → názov → kratšie), výber kandidáta |
| `ranking` | skóre kandidátov (kód-exact > názov-fuzzy) |
| `verify` | extrakcia produktovej stránky + verdikt (vstup pre AI overenie) |
| `writer`, `report_io` | zápis/čítanie výstupných CSV (cp1250) |
| `cli` | celý beh + checkpoint/resume |

Testy: `.venv/bin/pytest` (na uložených HTML fixtúrach, bez živej siete).

## Pridanie ďalšieho dodávateľa

1. Pridaj záznam do `config.SUPPLIERS` (base_url + search template).
2. Napíš `suppliers/<dodavatel>.py` s `parse_search(html, base_url) -> [Candidate]`.
3. Zaregistruj parser v `client.PARSERS`.
