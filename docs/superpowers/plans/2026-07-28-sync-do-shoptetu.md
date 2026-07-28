# Sync do Shoptetu — hodinový cyklus cez tabuľku čakajúcich zmien

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Každú hodinu preniesť všetky zmeny, ktoré appka spravuje, do Shoptetu jedným importným súborom — a hneď potom si stiahnuť výsledok, aby obe strany držali krok.

**Architecture:** Zápisové automatizácie prestanú samy nahrávať. Namiesto toho odovzdajú želaný stav polí do jednej tabuľky (`pending_shoptet.json`). Nová automatizácia `shoptet_upload` raz za hodinu spustí stiahnutie, spustí zapnutých producentov, z tabuľky postaví JEDEN import, overí ho z hlásenia Shoptetu, potvrdené vyhodí a stiahne znova.

**Tech Stack:** Python 3 + Flask (`webreview/app.py`), čisté moduly v `src/parovanie/`, vanilla JS SPA (`webreview/static/app.js`), pytest + pytest-playwright.

**Spec:** `docs/superpowers/specs/2026-07-28-sync-do-shoptetu-design.md` (ticket #299).

**Rozsah tohto plánu:** produktové polia (odkazy, dodávateľ, kódy, dostupnosť, sklad).
**Mimo tohto plánu:** poznámky k objednávkam — iný mechanizmus (prehliadač na detail objednávky, nie CSV), vlastné úložisko, vlastné riziká. Dostanú vlastný plán po tom, čo tento dobehne (spec §3.4, ticket #189).

## Global Constraints

- Verzia sa bumpuje na `dev` PRED prvým kódovým commitom (`src/parovanie/__init__.py`, `__version__`); CI job `version-check` vyžaduje dev > main.
- Nový store sa deklaruje VÝHRADNE cez `_store("meno.json")` — nikdy `os.path.join(OUT, …)` (#261, stráži `test_no_store_path_is_frozen_at_import`).
- Čítanie storu `_read_json_store` / `_read_json_store_state`, zápis `_atomic_write_json(..., protect=True)`, každý read-modify-write pod `with _lock:` (medziprocesový, #264).
- Každý nový store s neopakovateľnou prácou sa dopĺňa do `scripts/backup_data.sh`.
- Nová automatizácia štartuje VYPNUTÁ (kontrakt #93) — deploy nikdy nič nezapína sám.
- Nová položka v menu vyžaduje ZÁROVEŇ: `SYSTEM_TABS`, `NAV_ICONS`, `PAGE_TITLES` (app.js), `NAV_KEYS`, `AUTOMATION_DESCRIPTIONS` (app.py) — všetkých päť stráži drift-test, vynechané spadne v testoch.
- Nový prvok na karte automatizácie nesmie niesť druhú triedu `.auto*` (`.claude/rules/toorder-e2e.md` bod 14).
- Testy nikdy nesmú siahnuť na `data/out` ani zavolať reálny import — `WEBREVIEW_OUT` na `tmp_path`, `WEBREVIEW_NO_SCHEDULER=1`, import zastúpený dvojníkom.
- Import CSV: UTF-8 s BOM, `;`, CRLF, vždy `code` AJ `pairCode`, prázdna bunka = pole sa nemení.
- Slovenské texty pre manažéra; anglické komentáre v kóde podľa okolia.

---

## Štruktúra súborov

| Súbor | Zodpovednosť |
|---|---|
| `src/parovanie/shoptet_outbox.py` (nový) | Čistá logika tabuľky: zaradenie polí, stavba importu, rozdelenie kreditov. Bez Flasku, bez I/O. |
| `webreview/app.py` | Deklarácia storu, obal nad čistými funkciami, `run_shoptet_upload`, nárok cyklu, registrácia automatizácie, prepnutie producentov. |
| `webreview/static/app.js` | Karta „Sync do Shoptetu" v priečinku System + zoznam čakajúcich a zablokovaných. |
| `webreview/templates/index.html` | `#tab-shoptet_upload` sekcia, bump `?v=N` na `app.js` aj `style.css`. |
| `tests/test_shoptet_outbox.py` (nový) | Jednotkové testy čistej logiky. |
| `tests/test_webreview_shoptet_upload.py` (nový) | Endpoint + orchestrácia + kredity + odolnosť. |
| `tests/e2e/test_shoptet_upload.py` (nový) | Karta, zoznamy, čistá konzola. |
| `scripts/backup_data.sh` | Doplniť `pending_shoptet.json`. |

---

### Task 0: Bump verzie

**Files:**
- Modify: `src/parovanie/__init__.py`

- [ ] **Step 1: Zistiť verziu na main**

```bash
git fetch origin -q
git show origin/main:src/parovanie/__init__.py | grep __version__
grep __version__ src/parovanie/__init__.py
```

- [ ] **Step 2: Bumpnúť minor na dev**

Ak main aj dev nesú `0.104.0`, nastav v `src/parovanie/__init__.py`:

```python
__version__ = "0.105.0"
```

- [ ] **Step 3: Commit**

```bash
git add src/parovanie/__init__.py
git commit -m "chore: bump 0.105.0 for #299 (hourly Shoptet upload cycle)"
```

---

### Task 1: Tabuľka čakajúcich zmien — zaradenie polí

**Files:**
- Create: `src/parovanie/shoptet_outbox.py`
- Create: `tests/test_shoptet_outbox.py`

**Interfaces:**
- Produces: `queue_fields(pending: dict, source: str, header: str, rows: list, credit_group: dict | None = None, credit_value: dict | None = None, now: str = "") -> tuple[dict, int]` — vracia novú tabuľku a počet zaradených polí. `header` je reťazec ako `"code;pairCode;internalNote"`; `rows` je zoznam sekvencií v poradí hlavičky (presne to, čo dnes stavajú `import_builder.link_rows` a spol.).

- [ ] **Step 1: Napísať padajúci test**

```python
# tests/test_shoptet_outbox.py
from parovanie import shoptet_outbox as ob


def test_queue_fields_stores_one_field_per_column_with_its_source():
    pending, n = ob.queue_fields(
        {}, source="parovania_eshop", header="code;pairCode;internalNote",
        rows=[["60648", "60648", "https://dodavatel.sk/x"]],
        now="2026-07-28T14:00:00+02:00")
    assert n == 1
    assert pending["60648"]["pairCode"] == "60648"
    f = pending["60648"]["fields"]["internalNote"]
    assert f["value"] == "https://dodavatel.sk/x"
    assert f["source"] == "parovania_eshop"
    assert f["queued_at"] == "2026-07-28T14:00:00+02:00"
    assert pending["60648"]["blocked"] is None
    assert pending["60648"]["attempts"] == 0


def test_queue_fields_never_queues_the_key_columns_as_fields():
    pending, _ = ob.queue_fields(
        {}, source="s", header="code;pairCode;internalNote",
        rows=[["A", "P", "u"]], now="T")
    assert set(pending["A"]["fields"]) == {"internalNote"}


def test_queue_fields_skips_empty_cells_so_a_blank_never_wipes_a_field():
    pending, n = ob.queue_fields(
        {}, source="s", header="code;pairCode;internalNote;supplier",
        rows=[["A", "P", "", "FOREST"]], now="T")
    assert n == 1
    assert set(pending["A"]["fields"]) == {"supplier"}


def test_a_second_source_adds_its_own_field_to_the_same_code():
    p, _ = ob.queue_fields({}, source="parovania_eshop",
                           header="code;pairCode;internalNote",
                           rows=[["A", "P", "u"]], now="T1")
    p, _ = ob.queue_fields(p, source="restock_skladom",
                           header="code;pairCode;availabilityInStock",
                           rows=[["A", "P", "Skladom"]], now="T2")
    assert set(p["A"]["fields"]) == {"internalNote", "availabilityInStock"}
    assert p["A"]["fields"]["availabilityInStock"]["source"] == "restock_skladom"


def test_the_later_write_of_the_SAME_field_wins_and_keeps_its_own_source():
    p, _ = ob.queue_fields({}, source="restock_skladom",
                           header="code;pairCode;availabilityInStock",
                           rows=[["A", "P", "Skladom"]], now="T1")
    p, _ = ob.queue_fields(p, source="stock_skladom",
                           header="code;pairCode;availabilityInStock",
                           rows=[["A", "P", "Skladom"]], now="T2")
    f = p["A"]["fields"]["availabilityInStock"]
    assert f["source"] == "stock_skladom"
    assert f["queued_at"] == "T2"


def test_credit_group_and_value_ride_with_the_field():
    p, _ = ob.queue_fields(
        {}, source="parovania_eshop", header="code;pairCode;internalNote",
        rows=[["A", "P", "u"]],
        credit_group={"A": "BETALOV|60648"},
        credit_value={"A": "u"},
        now="T")
    c = p["A"]["fields"]["internalNote"]["credit"]
    assert c == {"store": "parovania_eshop", "group": "BETALOV|60648", "value": "u"}


def test_a_row_shorter_than_the_header_is_refused_loudly():
    import pytest
    with pytest.raises(ValueError):
        ob.queue_fields({}, source="s", header="code;pairCode;internalNote",
                        rows=[["A", "P"]], now="T")
```

- [ ] **Step 2: Spustiť a overiť, že padá**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_shoptet_outbox.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'parovanie.shoptet_outbox'`

- [ ] **Step 3: Implementovať**

```python
# src/parovanie/shoptet_outbox.py
"""The ONE pending-changes table between the app and Shoptet (#299).

Every write-side automation used to build its own CSV and run its own import —
five logins and five log read-backs per cycle, and the "already uploaded" mark
was written BEFORE Shoptet confirmed anything (the #257 class of bug). Here the
automations only ever QUEUE the field values they want; one hourly drain builds
a single import out of the whole table, and only a CONFIRMED row is credited.

Pure logic: no Flask, no filesystem, no clock. The caller supplies `now`.
"""

KEY_COLUMNS = ("code", "pairCode")


def queue_fields(pending, source, header, rows, credit_group=None,
                 credit_value=None, now=""):
    """Queue `rows` (sequences in `header` order) as per-field entries.

    Returns `(pending, queued)` — a NEW table plus how many field values landed.
    An empty cell queues nothing: in a Shoptet import an empty cell means "leave
    this field alone", so queueing it would be a promise we cannot keep.

    `credit_group[code]` / `credit_value[code]` carry the producer's dedup
    bookkeeping: the drain records `{group: value}` into the producer's
    uploaded-store only once EVERY queued code of that group is confirmed (the
    #49 rule — a group straddling a failed chunk must stay un-credited).
    """
    cols = [c.strip() for c in header.split(";")]
    out = {k: dict(v) for k, v in pending.items()}
    queued = 0
    for r in rows:
        if len(r) < len(cols):
            raise ValueError(
                f"row has {len(r)} cells but header {header!r} needs {len(cols)}")
        cells = dict(zip(cols, r))
        code = (cells.get("code") or "").strip()
        if not code:
            raise ValueError(f"row without a code: {r!r}")
        entry = dict(out.get(code) or {})
        entry.setdefault("pairCode", (cells.get("pairCode") or "").strip())
        entry.setdefault("blocked", None)
        entry.setdefault("attempts", 0)
        fields = dict(entry.get("fields") or {})
        for col in cols:
            if col in KEY_COLUMNS:
                continue
            val = cells.get(col)
            val = "" if val is None else str(val).strip()
            if not val:
                continue
            field = {"value": val, "source": source, "queued_at": now}
            group = (credit_group or {}).get(code)
            if group is not None:
                field["credit"] = {"store": source, "group": group,
                                   "value": (credit_value or {}).get(code, val)}
            fields[col] = field
            queued += 1
        entry["fields"] = fields
        out[code] = entry
    return out, queued
```

- [ ] **Step 4: Spustiť a overiť, že prechádza**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_shoptet_outbox.py -v`
Expected: PASS (7 testov)

- [ ] **Step 5: Commit**

```bash
git add src/parovanie/shoptet_outbox.py tests/test_shoptet_outbox.py
git commit -m "feat: pending-changes table for the Shoptet upload cycle (#299)"
```

---

### Task 2: Stavba JEDNÉHO importu z tabuľky + brána katalógu

**Files:**
- Modify: `src/parovanie/shoptet_outbox.py`
- Modify: `tests/test_shoptet_outbox.py`

**Interfaces:**
- Consumes: `queue_fields` z Tasku 1.
- Produces: `build_import(pending, absent_codes=frozenset()) -> tuple[str, list[list[str]], dict]` — `(header, rows, blocked)`. `header` vždy začína `code;pairCode`, ďalej abecedne zoradené polia, ktoré sa reálne posielajú. `blocked` je `{code: reason}`.

- [ ] **Step 1: Napísať padajúci test**

```python
# doplniť do tests/test_shoptet_outbox.py
def _pending_two_codes():
    p, _ = ob.queue_fields({}, source="parovania_eshop",
                           header="code;pairCode;internalNote",
                           rows=[["A", "PA", "https://x"]], now="T")
    p, _ = ob.queue_fields(p, source="restock_skladom",
                           header="code;pairCode;availabilityInStock;stock",
                           rows=[["B", "PB", "Skladom", "5"]], now="T")
    return p


def test_build_import_merges_every_queued_field_into_ONE_header():
    header, rows, blocked = ob.build_import(_pending_two_codes())
    assert header == "code;pairCode;availabilityInStock;internalNote;stock"
    assert blocked == {}
    by_code = {r[0]: r for r in rows}
    assert by_code["A"] == ["A", "PA", "", "https://x", ""]
    assert by_code["B"] == ["B", "PB", "Skladom", "", "5"]


def test_a_code_the_catalogue_does_not_carry_is_blocked_not_sent():
    header, rows, blocked = ob.build_import(_pending_two_codes(),
                                            absent_codes={"B"})
    assert [r[0] for r in rows] == ["A"]
    assert blocked == {"B": "not-in-catalog"}
    # the blocked code's own columns must not widen the header of what IS sent
    assert header == "code;pairCode;internalNote"


def test_an_empty_table_builds_nothing_rather_than_an_empty_import():
    header, rows, blocked = ob.build_import({})
    assert rows == []
    assert blocked == {}
    assert header == "code;pairCode"


def test_rows_come_out_in_a_stable_order_so_two_runs_are_comparable():
    p = _pending_two_codes()
    assert [r[0] for r in ob.build_import(p)[1]] == ["A", "B"]
```

- [ ] **Step 2: Spustiť a overiť, že padá**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_shoptet_outbox.py -k build_import -v`
Expected: FAIL — `AttributeError: module 'parovanie.shoptet_outbox' has no attribute 'build_import'`

- [ ] **Step 3: Implementovať**

```python
# doplniť do src/parovanie/shoptet_outbox.py
def build_import(pending, absent_codes=frozenset()):
    """Build ONE Shoptet import out of the whole table.

    Shoptet's import takes every column at once and treats an empty cell as
    "leave this field alone", so all queued fields for all codes ride in a
    single file — one login, one log read-back per cycle.

    A code the catalogue does not carry can never import (Shoptet rejects that
    row on every run, forever — #270), so it is held back and REPORTED, never
    dropped: it stays in the table with a reason and reappears in the import the
    moment the code shows up in the catalogue.
    """
    blocked = {c: "not-in-catalog" for c in sorted(pending) if c in absent_codes}
    sendable = [c for c in sorted(pending) if c not in blocked]
    cols = sorted({f for c in sendable for f in (pending[c].get("fields") or {})})
    header = ";".join([*KEY_COLUMNS, *cols])
    rows = []
    for code in sendable:
        entry = pending[code]
        fields = entry.get("fields") or {}
        if not fields:
            continue
        rows.append([code, entry.get("pairCode") or ""]
                    + [(fields.get(col) or {}).get("value", "") for col in cols])
    return header, rows, blocked
```

- [ ] **Step 4: Spustiť a overiť, že prechádza**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_shoptet_outbox.py -v`
Expected: PASS (11 testov)

- [ ] **Step 5: Commit**

```bash
git add src/parovanie/shoptet_outbox.py tests/test_shoptet_outbox.py
git commit -m "feat: build one Shoptet import from the whole pending table (#299)"
```

---

### Task 3: Vyprázdnenie potvrdených + kredity až po potvrdení

**Files:**
- Modify: `src/parovanie/shoptet_outbox.py`
- Modify: `tests/test_shoptet_outbox.py`

**Interfaces:**
- Consumes: `queue_fields`, `build_import`.
- Produces:
  - `settle(pending, success_codes, blocked, now="") -> tuple[dict, dict]` — `(pending, credits)`. `credits` je `{store_name: {group: value}}`, obsahuje LEN skupiny, ktorých všetky zaradené kódy sú potvrdené.
  - `stale_blocked(pending, min_attempts=3) -> list[str]` — kódy, ktoré čakajú zablokované aspoň `min_attempts` behov. Číta VLASTNÉ počítadlo `blocked_runs` (nuluje sa, len čo kód prestane byť zablokovaný) — nie `attempts`, ktoré rastie každému nepotvrdenému riadku a hlásilo by falošné poplachy (oprava zadania po revízii Task 3).

- [ ] **Step 1: Napísať padajúci test**

```python
# doplniť do tests/test_shoptet_outbox.py
def _pending_group_of_two():
    return ob.queue_fields(
        {}, source="parovania_eshop", header="code;pairCode;internalNote",
        rows=[["A", "P", "u"], ["B", "P", "u"]],
        credit_group={"A": "K", "B": "K"},
        credit_value={"A": "u", "B": "u"}, now="T")[0]


def test_a_confirmed_code_leaves_the_table():
    p, credits = ob.settle(_pending_group_of_two(), success_codes={"A"},
                           blocked={}, now="T2")
    assert set(p) == {"B"}


def test_a_group_is_credited_only_when_ALL_its_codes_are_confirmed():
    _, half = ob.settle(_pending_group_of_two(), success_codes={"A"},
                        blocked={}, now="T2")
    assert half == {}
    _, full = ob.settle(_pending_group_of_two(), success_codes={"A", "B"},
                        blocked={}, now="T2")
    assert full == {"parovania_eshop": {"K": "u"}}


def test_an_unconfirmed_code_stays_and_counts_an_attempt():
    p, _ = ob.settle(_pending_group_of_two(), success_codes=set(),
                     blocked={}, now="T2")
    assert p["A"]["attempts"] == 1
    p2, _ = ob.settle(p, success_codes=set(), blocked={}, now="T3")
    assert p2["A"]["attempts"] == 2


def test_a_blocked_code_records_its_reason_and_since_but_is_never_dropped():
    p, _ = ob.settle(_pending_group_of_two(), success_codes=set(),
                     blocked={"A": "not-in-catalog"}, now="T2")
    assert p["A"]["blocked"] == {"reason": "not-in-catalog", "since": "T2"}
    assert p["A"]["fields"]["internalNote"]["value"] == "u"


def test_a_code_that_stops_being_blocked_clears_its_flag():
    p, _ = ob.settle(_pending_group_of_two(), success_codes=set(),
                     blocked={"A": "not-in-catalog"}, now="T2")
    p, _ = ob.settle(p, success_codes=set(), blocked={}, now="T3")
    assert p["A"]["blocked"] is None


def test_stale_blocked_names_the_codes_that_have_been_stuck_for_three_runs():
    p = _pending_group_of_two()
    for t in ("T2", "T3", "T4"):
        p, _ = ob.settle(p, success_codes=set(),
                         blocked={"A": "not-in-catalog"}, now=t)
    assert ob.stale_blocked(p) == ["A"]
    assert ob.stale_blocked(p, min_attempts=99) == []
```

- [ ] **Step 2: Spustiť a overiť, že padá**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_shoptet_outbox.py -k "settle or stale" -v`
Expected: FAIL — `AttributeError: module 'parovanie.shoptet_outbox' has no attribute 'settle'`

- [ ] **Step 3: Implementovať**

```python
# doplniť do src/parovanie/shoptet_outbox.py
def settle(pending, success_codes, blocked, now=""):
    """Drop what Shoptet confirmed, keep the rest, and hand back the credits.

    A producer's uploaded-store is written HERE — after the import log confirmed
    the rows — never by the producer before the fact. That is the #257 lesson in
    one place: the app used to mark work as uploaded on its own say-so.
    """
    out, credits = {}, {}
    groups = {}
    for code, entry in pending.items():
        for field in (entry.get("fields") or {}).values():
            c = field.get("credit")
            if not c:
                continue
            g = groups.setdefault((c["store"], c["group"]),
                                  {"value": c["value"], "codes": set()})
            g["codes"].add(code)
    for (store, group), g in groups.items():
        if g["codes"] <= set(success_codes):
            credits.setdefault(store, {})[group] = g["value"]
    for code, entry in pending.items():
        if code in success_codes:
            continue
        e = dict(entry)
        if code in blocked:
            prev = e.get("blocked") or {}
            e["blocked"] = {"reason": blocked[code],
                            "since": prev.get("since") or now}
        else:
            e["blocked"] = None
        e["attempts"] = int(e.get("attempts") or 0) + 1
        out[code] = e
    return out, credits


def stale_blocked(pending, min_attempts=3):
    """Codes stuck blocked for at least `min_attempts` runs — the silent-death
    guard for the table itself: a held-back row must never wait forever unseen.
    """
    return sorted(c for c, e in pending.items()
                  if (e.get("blocked") and int(e.get("attempts") or 0) >= min_attempts))
```

- [ ] **Step 4: Spustiť a overiť, že prechádza**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_shoptet_outbox.py -v`
Expected: PASS (17 testov)

- [ ] **Step 5: Commit**

```bash
git add src/parovanie/shoptet_outbox.py tests/test_shoptet_outbox.py
git commit -m "feat: settle the pending table only on confirmed rows (#299)"
```

---

### Task 4: Store + obal v appke

**Files:**
- Modify: `webreview/app.py` (vedľa `PAIRINGS_STATE`, `webreview/app.py:5637`)
- Modify: `scripts/backup_data.sh`
- Create: `tests/test_webreview_shoptet_upload.py`

**Interfaces:**
- Consumes: `shoptet_outbox.queue_fields/build_import/settle/stale_blocked`.
- Produces:
  - `PENDING_SHOPTET = _store("pending_shoptet.json")`
  - `_load_pending() -> dict`, `_save_pending(d) -> None`
  - `queue_shoptet_fields(source, header, rows, credit_group=None, credit_value=None) -> int`

- [ ] **Step 1: Napísať padajúci test**

```python
# tests/test_webreview_shoptet_upload.py
import json
import pytest
from webreview import app as webapp


@pytest.fixture
def pend(tmp_path, monkeypatch):
    p = tmp_path / "pending_shoptet.json"
    monkeypatch.setattr(webapp, "PENDING_SHOPTET", str(p))
    return p


def test_queue_shoptet_fields_persists_and_counts(pend):
    n = webapp.queue_shoptet_fields(
        "parovania_eshop", "code;pairCode;internalNote",
        [["A", "P", "https://x"]])
    assert n == 1
    d = json.loads(pend.read_text(encoding="utf-8"))
    assert d["A"]["fields"]["internalNote"]["value"] == "https://x"
    assert d["A"]["fields"]["internalNote"]["queued_at"]


def test_queueing_twice_keeps_both_sources(pend):
    webapp.queue_shoptet_fields("parovania_eshop", "code;pairCode;internalNote",
                                [["A", "P", "https://x"]])
    webapp.queue_shoptet_fields("restock_skladom",
                                "code;pairCode;availabilityInStock",
                                [["A", "P", "Skladom"]])
    d = webapp._load_pending()
    assert set(d["A"]["fields"]) == {"internalNote", "availabilityInStock"}


def test_an_unreadable_table_refuses_the_write_rather_than_wiping_it(pend):
    pend.write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(Exception):
        webapp.queue_shoptet_fields("s", "code;pairCode;internalNote",
                                    [["A", "P", "u"]])
```

- [ ] **Step 2: Spustiť a overiť, že padá**

Run: `WEBREVIEW_NO_SCHEDULER=1 MAIL_HOST="" WEBREVIEW_OUT=/tmp/wr-t4 .venv/bin/pytest tests/test_webreview_shoptet_upload.py -v`
Expected: FAIL — `AttributeError: module 'webreview.app' has no attribute 'PENDING_SHOPTET'`

- [ ] **Step 3: Implementovať**

```python
# webreview/app.py — vedľa PAIRINGS_STATE (app.py:5637)
from parovanie import shoptet_outbox

# #299 — the ONE table between our decisions and the eshop. Producers queue the
# field values they want; the hourly `shoptet_upload` cycle turns the whole table
# into a single import. protect=True: a queued change that is lost never reaches
# the eshop and nothing notices, which is exactly the silent loss the table exists
# to end.
PENDING_SHOPTET = _store("pending_shoptet.json")


def _load_pending() -> dict:
    return _read_json_store(PENDING_SHOPTET, {})


def _save_pending(d: dict) -> None:
    _atomic_write_json(PENDING_SHOPTET, d, protect=True)


def queue_shoptet_fields(source, header, rows, credit_group=None,
                         credit_value=None) -> int:
    """Queue rows for the next hourly upload. Returns how many field values landed."""
    if not rows:
        return 0
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    with _lock:
        # A table we could not read is NOT an empty table — queueing on top of the
        # default would silently drop everything already waiting. That refusal is
        # NOT hand-rolled here: `_atomic_write_json(protect=True)` in `_save_pending`
        # already refuses the wipe, quarantines the corrupt file and raises
        # StoreWipeRefused, which the app turns into a 503 with instructions. A
        # hand-rolled `raise` in front of it short-circuits exactly that machinery
        # (no quarantine, wrong exception type, and an empty file — a real
        # post-crash shape — would brick queueing for ever).
        pending = _read_json_store(PENDING_SHOPTET, {})
        pending, n = shoptet_outbox.queue_fields(
            pending, source, header, rows,
            credit_group=credit_group, credit_value=credit_value, now=now)
        if not n:
            return 0          # nothing queued → never rewrite a protected store
        _save_pending(pending)
    log.info("outbox: %s zaradil %d polí (%d kódov)", source, n, len(rows))
    return n
```

Do `scripts/backup_data.sh` doplň `pending_shoptet.json` k ostatným chráneným storom (rovnaký riadok ako pri `order_statuses.json` na `scripts/backup_data.sh:35`).

- [ ] **Step 4: Spustiť a overiť, že prechádza**

Run: `WEBREVIEW_NO_SCHEDULER=1 MAIL_HOST="" WEBREVIEW_OUT=/tmp/wr-t4 .venv/bin/pytest tests/test_webreview_shoptet_upload.py -v`
Expected: PASS (3 testy)

- [ ] **Step 5: Commit**

```bash
git add webreview/app.py scripts/backup_data.sh tests/test_webreview_shoptet_upload.py
git commit -m "feat: pending_shoptet store + queue_shoptet_fields wrapper (#299)"
```

---

### Task 5: Nárok cyklu (aby sťahovanie nevliezlo doprostred)

**Files:**
- Modify: `webreview/app.py` (vedľa `_claim_scheduler`, `webreview/app.py:8722`)
- Modify: `tests/test_webreview_shoptet_upload.py`

**Interfaces:**
- Produces: `CYCLE_CLAIM = _store(".shoptet_cycle.lock")`, kontextový manažér `_shoptet_cycle_claim()` (yielduje `True`, keď nárok získal, inak `False`), a `_cycle_busy() -> bool`.

- [ ] **Step 1: Napísať padajúci test**

```python
# doplniť do tests/test_webreview_shoptet_upload.py
@pytest.fixture
def claim(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "CYCLE_CLAIM", str(tmp_path / ".cycle.lock"))


def test_the_claim_is_exclusive_and_reports_busy_while_held(claim):
    with webapp._shoptet_cycle_claim() as got:
        assert got is True
        assert webapp._cycle_busy() is True
        with webapp._shoptet_cycle_claim() as second:
            assert second is False
    assert webapp._cycle_busy() is False


def test_the_claim_is_released_even_when_the_body_raises(claim):
    with pytest.raises(ValueError):
        with webapp._shoptet_cycle_claim():
            raise ValueError("boom")
    assert webapp._cycle_busy() is False
```

- [ ] **Step 2: Spustiť a overiť, že padá**

Run: `WEBREVIEW_NO_SCHEDULER=1 MAIL_HOST="" WEBREVIEW_OUT=/tmp/wr-t5 .venv/bin/pytest tests/test_webreview_shoptet_upload.py -k claim -v`
Expected: FAIL — `AttributeError: module 'webreview.app' has no attribute '_shoptet_cycle_claim'`

- [ ] **Step 3: Implementovať**

```python
# webreview/app.py — vedľa _claim_scheduler (app.py:8722)
import contextlib

# #299 — the whole download → upload → download cycle holds ONE claim, so the
# standalone hourly download cannot land in the middle of it and hand the drain a
# catalogue that changed under its feet. Same flock shape as the scheduler claim.
CYCLE_CLAIM = _store(".shoptet_cycle.lock")


def _cycle_busy() -> bool:
    """True while some process is inside the upload cycle."""
    p = os.fspath(CYCLE_CLAIM)
    fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


@contextlib.contextmanager
def _shoptet_cycle_claim():
    p = os.fspath(CYCLE_CLAIM)
    fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            log.warning("sync do Shoptetu: cyklus už beží inde, preskakujem")
            yield False
            return
        os.ftruncate(fd, 0)
        os.write(fd, f"pid={os.getpid()} "
                     f"started={datetime.now().isoformat(timespec='seconds')}\n".encode())
        yield True
    finally:
        os.close(fd)
```

- [ ] **Step 4: Spustiť a overiť, že prechádza**

Run: `WEBREVIEW_NO_SCHEDULER=1 MAIL_HOST="" WEBREVIEW_OUT=/tmp/wr-t5 .venv/bin/pytest tests/test_webreview_shoptet_upload.py -v`
Expected: PASS (5 testov)

- [ ] **Step 5: Commit**

```bash
git add webreview/app.py tests/test_webreview_shoptet_upload.py
git commit -m "feat: one cross-process claim for the whole Shoptet cycle (#299)"
```

---

### Task 6: Hodinový cyklus `run_shoptet_upload` + registrácia automatizácie

> **ZASTARANÉ — opravné kolo 1 (#299 review I2) zrušilo krok „spustiť
> producentov" tento task nižšie zaviedol.** `CYCLE_PRODUCERS`/`QUEUE_MIGRATED`
> aj celý `RUNNER.run_now(key)`-per-producenta krok sú preč z kódu — cyklus
> NIKDY nespúšťa producenta, len sťahuje, nahrá JEDNÝM importom to, čo je
> vo fronte, overí, vyprázdni potvrdené a stiahne znova. Dôvod: ten krok
> nenápadne premenil `parovania_eshop` (jediný producent už zapnutý na
> forestshop.sk, normálne 1×/deň) na beh 24×/deň hneď, ako manažér zapol túto
> hodinovú automatizáciu — vrátane deštruktívneho prepisovania manuálnych
> priradení dodávateľov 24×/deň namiesto 1×/deň. Nižšie uvedený kód (Step 1–5)
> je PÔVODNÝ návrh Tasku 6 a v tejto podobe už NEZODPOVEDÁ skutočnému kódu —
> aktuálny stav a dôvod zmeny je zdokumentovaný v
> `docs/superpowers/specs/2026-07-28-sync-do-shoptetu-design.md` §3/§3.3 a
> priamo v docstringu `run_shoptet_upload` (`webreview/app.py`). Neimplementuj
> podľa tohto Tasku doslovne — over najprv aktuálny kód.

**Files:**
- Modify: `webreview/app.py` (funkcia vedľa `run_shoptet_sync`, `app.py:6966`; registrácia v `AUTOMATIONS_REG`, `app.py:8578`; popis v `AUTOMATION_DESCRIPTIONS`, `app.py:8521`; `NAV_KEYS`, `app.py:8794`)
- Modify: `tests/test_webreview_shoptet_upload.py`

**Interfaces (pôvodné, PRED opravným kolom 1 — viď poznámka vyššie):**
- Consumes: `_load_pending`, `_save_pending`, `_shoptet_cycle_claim`, `_export_row_verdicts`, `_import_rows_chunked`, `RUNNER.run_now`, `shoptet_outbox.build_import/settle/stale_blocked`.
- Produces: `run_shoptet_upload() -> dict` s kľúčmi `ok, queued, sent, confirmed, blocked, stale_blocked, producers, resynced, skipped_second_sync, unconfirmed, error`.
- Produces: `CYCLE_PRODUCERS = ("parovania_eshop", "grube_externalcode", "split_links", "restock_skladom", "stock_skladom")` — **odstránené opravným kolom 1, viď poznámka vyššie.**

- [ ] **Step 1: Napísať padajúci test**

```python
# doplniť do tests/test_webreview_shoptet_upload.py
@pytest.fixture
def cycle(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "PENDING_SHOPTET", str(tmp_path / "pending.json"))
    monkeypatch.setattr(webapp, "CYCLE_CLAIM", str(tmp_path / ".cycle.lock"))
    calls = {"import": [], "run_now": []}
    monkeypatch.setattr(webapp, "_import_rows_chunked",
                        lambda rows, header, dry, prefix, timeout=900: (
                            calls["import"].append((header, [list(r) for r in rows]))
                            or {"ok": True, "partial": False,
                                "success_codes": {r[0] for r in rows},
                                "partial_codes": set(), "partial_failed": 0,
                                "chunks_total": 1, "chunks_ok": 1,
                                "processed": len(rows), "updated": len(rows),
                                "failed": 0, "rc": 0, "error_detail": None,
                                "stdout_tail": "", "err": "", "unreadable": False}))
    monkeypatch.setattr(webapp, "_export_row_verdicts",
                        lambda rows, note_col=2: {"confirmed": set(), "absent": set()})
    monkeypatch.setattr(webapp.RUNNER, "run_now",
                        lambda key: calls["run_now"].append(key) or True)
    monkeypatch.setattr(webapp.RUNNER, "status", lambda: [])
    return calls


def test_an_empty_table_uploads_nothing_and_skips_the_second_download(cycle):
    res = webapp.run_shoptet_upload()
    assert res["ok"] is True
    assert res["sent"] == 0
    assert cycle["import"] == []
    assert res["skipped_second_sync"] is True
    assert cycle["run_now"].count("shoptet_sync") == 1


def test_a_queued_change_goes_up_in_ONE_import_and_leaves_the_table(cycle):
    webapp.queue_shoptet_fields("parovania_eshop", "code;pairCode;internalNote",
                                [["A", "P", "https://x"]])
    webapp.queue_shoptet_fields("restock_skladom",
                                "code;pairCode;availabilityInStock",
                                [["B", "P2", "Skladom"]])
    res = webapp.run_shoptet_upload()
    assert len(cycle["import"]) == 1, "the whole table must ride in ONE import"
    header, rows = cycle["import"][0]
    assert header == "code;pairCode;availabilityInStock;internalNote"
    assert sorted(r[0] for r in rows) == ["A", "B"]
    assert res["sent"] == 2 and res["confirmed"] == 2
    assert webapp._load_pending() == {}
    assert res["skipped_second_sync"] is False
    assert cycle["run_now"].count("shoptet_sync") == 2


def test_a_code_missing_from_the_catalogue_is_blocked_and_kept(cycle, monkeypatch):
    monkeypatch.setattr(webapp, "_export_row_verdicts",
                        lambda rows, note_col=2: {"confirmed": set(), "absent": {"A"}})
    webapp.queue_shoptet_fields("parovania_eshop", "code;pairCode;internalNote",
                                [["A", "P", "https://x"]])
    res = webapp.run_shoptet_upload()
    assert res["blocked"] == 1
    assert cycle["import"] == []
    assert webapp._load_pending()["A"]["blocked"]["reason"] == "not-in-catalog"


def test_the_cycle_refuses_to_run_twice_at_once(cycle):
    with webapp._shoptet_cycle_claim():
        res = webapp.run_shoptet_upload()
    assert res["ok"] is False
    assert res["error"] == "cycle-busy"
    assert cycle["run_now"] == []
```

- [ ] **Step 2: Spustiť a overiť, že padá**

Run: `WEBREVIEW_NO_SCHEDULER=1 MAIL_HOST="" WEBREVIEW_OUT=/tmp/wr-t6 .venv/bin/pytest tests/test_webreview_shoptet_upload.py -k cycle -v`
Expected: FAIL — `AttributeError: module 'webreview.app' has no attribute 'run_shoptet_upload'`

- [ ] **Step 3: Implementovať**

**ZASTARANÉ SNIPPET — neplatí, viď poznámka na začiatku Tasku 6.** `CYCLE_PRODUCERS`
a krok „spustiť producentov" (`for key in CYCLE_PRODUCERS: ... RUNNER.run_now(key)`
nižšie) opravné kolo 1 zrušilo úplne — cyklus dnes len sťahuje, importuje to, čo
je vo fronte, overí a vyprázdni. Skutočný kód: `run_shoptet_upload` v
`webreview/app.py`.

```python
# webreview/app.py — vedľa run_shoptet_sync (app.py:6966)

# The order matters: pairings first (they define what a product IS), then the
# code/link producers, then the availability ones — so a product that gains a
# supplier link in the same cycle is already linked when it goes on sale.
CYCLE_PRODUCERS = ("parovania_eshop", "grube_externalcode", "split_links",
                   "restock_skladom", "stock_skladom")
SECOND_SYNC_SKIP_WHEN_NOTHING_SENT = True


def _enabled_automations() -> set:
    return {a["key"] for a in RUNNER.status() if a.get("enabled")}


def run_shoptet_upload() -> dict:
    """#299 — the hourly cycle: download → let the producers queue → ONE import →
    settle → download again.

    It writes nothing to the eshop itself: producers queue into pending_shoptet,
    and only rows the import log confirmed are credited and dropped. The second
    download is skipped when nothing went up (most hours) — it would re-fetch the
    57 MB catalogue for no reason.
    """
    with _shoptet_cycle_claim() as got:
        if not got:
            return {"ok": False, "error": "cycle-busy", "queued": 0, "sent": 0,
                    "confirmed": 0, "blocked": 0, "stale_blocked": [],
                    "producers": {}, "resynced": 0,
                    "skipped_second_sync": True, "unconfirmed": 0}

        RUNNER.run_now("shoptet_sync")
        resynced = 1

        enabled = _enabled_automations()
        producers = {}
        for key in CYCLE_PRODUCERS:
            if key not in enabled:
                continue
            producers[key] = bool(RUNNER.run_now(key))

        pending = _load_pending()
        header_all, rows_all, _ = shoptet_outbox.build_import(pending)
        verdicts = _export_row_verdicts(rows_all, note_col=None) if rows_all else \
            {"confirmed": set(), "absent": set()}
        header, rows, blocked = shoptet_outbox.build_import(
            pending, absent_codes=verdicts["absent"])

        res = None
        if rows:
            res = _import_rows_chunked(rows, header, False,
                                       prefix="import_sync_", timeout=900)
        success = set(res["success_codes"]) if res else set()
        now = datetime.now().isoformat(timespec="seconds")
        with _lock:
            fresh = _load_pending()
            settled, credits = shoptet_outbox.settle(fresh, success, blocked, now=now)
            _save_pending(settled)
        for store, entries in credits.items():
            _credit_producer(store, entries)

        sent, confirmed = len(rows), len(success)
        unconfirmed = sent - confirmed
        skipped = SECOND_SYNC_SKIP_WHEN_NOTHING_SENT and confirmed == 0
        if not skipped:
            RUNNER.run_now("shoptet_sync")
            resynced = 2

        stale = shoptet_outbox.stale_blocked(settled)
        ok = (res is None or (res["ok"] and not res["partial"])) and not stale
        if unconfirmed:
            log.error("sync do Shoptetu: %d z %d riadkov Shoptet nepotvrdil — "
                      "ostávajú v tabuľke a pôjdu znova", unconfirmed, sent)
        if stale:
            log.error("sync do Shoptetu: %d kódov je zablokovaných 3 a viac behov "
                      "(eshop ich v katalógu nemá): %s", len(stale), stale[:10])
        return {"ok": ok, "queued": len(pending), "sent": sent,
                "confirmed": confirmed, "blocked": len(blocked),
                "stale_blocked": stale, "producers": producers,
                "resynced": resynced, "skipped_second_sync": skipped,
                "unconfirmed": unconfirmed,
                "error": "" if ok else "nepotvrdené alebo zablokované riadky"}


def _credit_producer(store: str, entries: dict) -> None:
    """Write a producer's uploaded-state for groups the import confirmed."""
    path = {"parovania_eshop": PAIRINGS_STATE}.get(store)
    if path is None:
        log.warning("outbox: neznámy kredit store %s (%d skupín)", store, len(entries))
        return
    _record_uploaded(lambda: _read_json_store(path, {}),
                     lambda d: _atomic_write_json(path, d, protect=True),
                     entries)
```

Poznámka pre implementátora: `_export_row_verdicts(rows, note_col=None)` musí zniesť `note_col=None` (vtedy sa `confirmed` nepočíta, len `absent`) — uprav jeho telo tak, že pri `note_col is None` vráti `confirmed=set()` a `absent` počíta ako dnes. Existujúcich volajúcich to nemení (default ostáva `2`).

Registrácia — do `AUTOMATIONS_REG` (`app.py:8578`) hneď za `shoptet_sync`:

```python
    # #299 — the write-side counterpart of shoptet_sync. Starts DISABLED (#93):
    # it pushes to the live eshop, so the manager turns it on himself.
    Automation(key="shoptet_upload",
               name="Sync do Shoptetu",
               schedule={"interval_minutes": 60, "tz": "Europe/Bratislava"},
               run_fn=run_shoptet_upload),
```

Popis — do `AUTOMATION_DESCRIPTIONS` (`app.py:8521`):

```python
    "shoptet_upload":
        "Každú hodinu stiahne čerstvý stav zo Shoptetu, nechá zapnuté automatizácie "
        "zapísať ich zmeny do tabuľky čakajúcich zmien, všetko naraz nahrá do eshopu "
        "jedným importom a potom stav stiahne znova. Nahraté označí až vtedy, keď to "
        "Shoptet potvrdí; čo eshop v katalógu nemá, ostane čakať a je to tu vidieť.",
```

A kľúč `"shoptet_upload"` doplň do `NAV_KEYS` (`app.py:8794`).

- [ ] **Step 4: Spustiť a overiť, že prechádza**

Run: `WEBREVIEW_NO_SCHEDULER=1 MAIL_HOST="" WEBREVIEW_OUT=/tmp/wr-t6 .venv/bin/pytest tests/test_webreview_shoptet_upload.py tests/test_webreview_ui_labels.py -v`
Expected: PASS — vrátane `test_automations_all_carry_description`

- [ ] **Step 5: Commit**

```bash
git add webreview/app.py tests/test_webreview_shoptet_upload.py
git commit -m "feat: hourly shoptet_upload cycle — download, queue, ONE import, settle (#299)"
```

---

### Task 7: Karta v priečinku System

**Files:**
- Modify: `webreview/static/app.js` (`SYSTEM_TABS:490`, `NAV_ICONS:512`, `PAGE_TITLES`, nová `renderShoptetUpload()`)
- Modify: `webreview/templates/index.html` (sekcia `#tab-shoptet_upload`, bump `?v=N`)
- Modify: `webreview/app.py` (`/api/automations` doplní `pending` súhrn)
- Create: `tests/e2e/test_shoptet_upload.py`
- Modify: `tests/e2e/conftest.py` (`_SERVER_FIXTURES:67`)

**Interfaces:**
- Consumes: `run_shoptet_upload` výsledok z `last_result`.
- Produces: endpoint `GET /api/pending-shoptet` → `{"pending": [...], "blocked": [...]}`, kde položka je `{"code","field","value","source","queued_at","reason"}`.

- [ ] **Step 1: Napísať padajúci e2e test**

```python
# tests/e2e/test_shoptet_upload.py
from playwright.sync_api import expect


def test_the_card_sits_in_the_System_folder_under_the_download_sync(shoptet_upload_server, page):
    page.goto(shoptet_upload_server)
    labels = page.locator("#systemTabs .tlabel")
    expect(labels).to_have_count(2, timeout=15000)
    expect(labels.nth(0)).to_have_text("Sync zo Shoptetu")
    expect(labels.nth(1)).to_have_text("Sync do Shoptetu")


def test_the_card_names_how_many_changes_are_waiting_and_which_are_blocked(
        shoptet_upload_server, page):
    page.goto(shoptet_upload_server)
    page.locator("#systemTabs .tlabel", has_text="Sync do Shoptetu").click()
    expect(page.locator('[data-testid="pending-count"]')).to_have_text(
        "Čaká na nahratie: 2 zmeny", timeout=15000)
    blocked = page.locator('[data-testid="pending-blocked"]')
    expect(blocked).to_contain_text("Zablokované: 1")
    expect(blocked).to_contain_text("eshop tento kód v katalógu nemá")


def test_the_console_stays_clean(shoptet_upload_server, page):
    msgs = []
    page.on("console", lambda m: msgs.append(m))
    page.goto(shoptet_upload_server)
    page.locator("#systemTabs .tlabel", has_text="Sync do Shoptetu").click()
    page.wait_for_timeout(500)
    assert [m.text for m in msgs if m.type in ("error", "warning")] == []
```

Fixture `shoptet_upload_server` sa pridá do `tests/e2e/conftest.py` podľa vzoru `toorder_server` (vlastný `WEBREVIEW_OUT` v `tmp_path_factory`, do ktorého sa pred štartom zapíše `pending_shoptet.json` s dvomi čakajúcimi poľami a jedným zablokovaným kódom), a jeho meno sa DOPLNÍ do `_SERVER_FIXTURES` (`tests/e2e/conftest.py:67`).

- [ ] **Step 2: Spustiť a overiť, že padá**

Run: `.venv/bin/pytest tests/e2e/test_shoptet_upload.py -v`
Expected: FAIL — fixture `shoptet_upload_server` neexistuje / `#systemTabs` má 1 položku

- [ ] **Step 3: Implementovať**

Endpoint (`webreview/app.py`, vedľa `/api/automations`):

```python
@app.route("/api/pending-shoptet")
def api_pending_shoptet():
    """What the next hourly upload will send, and what it cannot send yet."""
    pending = _load_pending()
    waiting, blocked = [], []
    for code in sorted(pending):
        e = pending[code]
        for field, f in sorted((e.get("fields") or {}).items()):
            item = {"code": code, "field": field, "value": f.get("value", ""),
                    "source": f.get("source", ""), "queued_at": f.get("queued_at", "")}
            if e.get("blocked"):
                blocked.append({**item, "reason": e["blocked"].get("reason", "")})
            else:
                waiting.append(item)
    return jsonify({"pending": waiting, "blocked": blocked})
```

Frontend (`webreview/static/app.js`):

```javascript
const SYSTEM_TABS = [['shoptet_sync', 'Sync zo Shoptetu'],
                     ['shoptet_upload', 'Sync do Shoptetu']];
```

```javascript
  shoptet_upload: '<path d="M12 19V5M5 12l7-7 7 7"/>'
    + '<path d="M3 21h18"/>',
```

`renderShoptetUpload()` kopíruje kostru `renderShoptetSync()` (`app.js:4207`) — pill, Štart/Stop, „⚡ Spustiť teraz", `.autodesc`, `.autometa`, `.autoerr` — a pod ňu pridá dva vlastné bloky (vlastné triedy, NIKDY druhá `.auto*`):

```javascript
  const cnt = el('div', 'pendcount', '');
  cnt.dataset.testid = 'pending-count';
  cnt.textContent = 'Čaká na nahratie: ' + pluralZmeny(P.pending.length);
  st.appendChild(cnt);
  if (P.blocked.length) {
    const b = el('div', 'pendblocked', '');
    b.dataset.testid = 'pending-blocked';
    b.textContent = 'Zablokované: ' + P.blocked.length
      + ' — eshop tento kód v katalógu nemá, čakajú, kým sa objaví';
    st.appendChild(b);
  }
```

`pluralZmeny(n)` vracia `1 zmena` / `2 zmeny` / `5 zmien` (rovnaký vzor skloňovania ako `.claude/rules/toorder-e2e.md` bod 4 — použi existujúci helper, ak už v `app.js` je; ak nie, priprav ho vedľa a otestuj hodnoty 1/2/5/0).

V `index.html` doplň `<div id="tab-shoptet_upload" class="tab"></div>` vedľa `#tab-shoptet_sync` a bumpni `?v=N` na `app.js` aj `style.css`.

- [ ] **Step 4: Spustiť a overiť, že prechádza**

Run: `.venv/bin/pytest tests/e2e/test_shoptet_upload.py tests/e2e/test_shell.py -v`
Expected: PASS — vrátane `test_system_folder_holds_shoptet_sync`

- [ ] **Step 5: Commit**

```bash
git add webreview/static/app.js webreview/templates/index.html webreview/app.py \
        tests/e2e/test_shoptet_upload.py tests/e2e/conftest.py
git commit -m "feat: Sync do Shoptetu card with the waiting/blocked lists (#299)"
```

---

### Task 8: Prepnúť dvoch vypnutých producentov (nulové riziko)

**Files:**
- Modify: `webreview/app.py` — `_do_upload_externalcodes` (`app.py:6276`), `_do_upload_variant_links` (`app.py:6404`)
- Modify: `tests/test_webreview_shoptet_upload.py`

**Interfaces:**
- Consumes: `queue_shoptet_fields`.
- Produces: obe funkcie vracajú ten istý tvar výsledku ako dnes, ale s `"queued": N` namiesto importných polí; **nevolajú** `_import_rows_chunked`.

- [ ] **Step 1: Napísať padajúci test**

```python
# doplniť do tests/test_webreview_shoptet_upload.py
def test_grube_producer_queues_instead_of_importing(pend, monkeypatch):
    monkeypatch.setattr(webapp, "_import_rows_chunked",
                        lambda *a, **k: pytest.fail("producer must not import"))
    monkeypatch.setattr(webapp, "_load_grube_codes",
                        lambda: {"A": {"pairCode": "P", "externalCode": "12345"}})
    monkeypatch.setattr(webapp, "_load_uploaded_externalcodes", lambda: {})
    res, status = webapp._do_upload_externalcodes(False)
    assert status == 200
    assert res["queued"] == 1
    d = webapp._load_pending()
    assert d["A"]["fields"]["externalCode"]["value"] == "12345"
    assert d["A"]["fields"]["externalCode"]["source"] == "grube_externalcode"
```

(Presné mená načítavacích funkcií si over v `_do_upload_externalcodes`; ak sa volajú inak, použi tie skutočné — test musí monkeypatchovať to, čo funkcia naozaj volá.)

- [ ] **Step 2: Spustiť a overiť, že padá**

Run: `WEBREVIEW_NO_SCHEDULER=1 MAIL_HOST="" WEBREVIEW_OUT=/tmp/wr-t8 .venv/bin/pytest tests/test_webreview_shoptet_upload.py -k grube -v`
Expected: FAIL — `Failed: producer must not import`

- [ ] **Step 3: Implementovať**

V oboch funkciách nahraď blok, ktorý dnes berie `_import_lock`, volá `_import_rows_chunked` a zapisuje `uploaded_*`, jediným volaním:

```python
        queued = queue_shoptet_fields(
            "grube_externalcode", import_builder.EXTERNALCODE_HEADER, send_rows,
            credit_group={r[0]: r[0] for r in send_rows},
            credit_value={r[0]: r[2] for r in send_rows})
```

a vo výsledkovom dicte nahraď importné polia (`exit_code`, `processed`, `updated`, `failed`, `chunks_*`, `partial`, `rejected`) jediným `"queued": queued`. Zachovaj VŠETKY ostatné polia (`blocked`, `missing_*`, súhrny) — karta ich číta.

`_credit_producer` rozšír o obe úložiská:

```python
    path = {"parovania_eshop": PAIRINGS_STATE,
            "grube_externalcode": EXTERNALCODES_STATE,
            "split_links": VARIANT_LINKS_STATE}.get(store)
```

(presné mená konštánt over v okolí `_do_upload_externalcodes` / `_do_upload_variant_links`).

- [ ] **Step 4: Spustiť a overiť, že prechádza**

Run: `WEBREVIEW_NO_SCHEDULER=1 MAIL_HOST="" WEBREVIEW_OUT=/tmp/wr-t8 .venv/bin/pytest tests/ --ignore=tests/e2e --ignore=tests/test_scheduler_guard.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add webreview/app.py tests/test_webreview_shoptet_upload.py
git commit -m "refactor: GRUBE codes and split links queue instead of importing (#299)"
```

---

### Task 9: Prepnúť reštok a „máme skladom"

**Files:**
- Modify: `webreview/app.py` — `run_restock_skladom` (`app.py:7599`), `run_stock_skladom` (`app.py:7705`)
- Modify: `tests/test_webreview_shoptet_upload.py`

**Interfaces:**
- Consumes: `queue_shoptet_fields`.
- Produces: obe vracajú `{"ok": True, "queued": N, "candidates": M, ...}`; **nevolajú** import, **nemajú** kredit (dedup nepoužívajú — kandidát prestane byť kandidátom tým, že sa stav zmení).

- [ ] **Step 1: Napísať padajúci test**

```python
# doplniť do tests/test_webreview_shoptet_upload.py
def test_restock_queues_availability_and_stock_without_a_credit(pend, monkeypatch):
    monkeypatch.setattr(webapp, "_import_rows_chunked",
                        lambda *a, **k: pytest.fail("producer must not import"))
    monkeypatch.setattr(webapp, "_restock_candidate_rows",
                        lambda: [["A", "P", "Skladom", "Skladom", "visible", "5"]])
    res = webapp.run_restock_skladom()
    assert res["queued"] == 4
    f = webapp._load_pending()["A"]["fields"]
    assert f["availabilityInStock"]["value"] == "Skladom"
    assert f["stock"]["value"] == "5"
    assert "credit" not in f["stock"]
```

(`_restock_candidate_rows` je pomenovanie pre miesto, kde dnes `run_restock_skladom` stavia riadky — ak je logika inline, vytiahni ju do takto pomenovanej funkcie ako súčasť tohto kroku; test na ňu potom monkeypatchuje.)

- [ ] **Step 2: Spustiť a overiť, že padá**

Run: `WEBREVIEW_NO_SCHEDULER=1 MAIL_HOST="" WEBREVIEW_OUT=/tmp/wr-t9 .venv/bin/pytest tests/test_webreview_shoptet_upload.py -k restock -v`
Expected: FAIL — `Failed: producer must not import`

- [ ] **Step 3: Implementovať**

V oboch funkciách nahraď import volaním:

```python
        queued = queue_shoptet_fields("restock_skladom",
                                      import_builder.RESTOCK_HEADER, rows)
```

(pre `run_stock_skladom` použi `"stock_skladom"` a jeho vlastnú hlavičku — over presné meno konštanty; `stock` sa v nej zámerne NEnachádza).

Zachovaj obe existujúce poistky proti starým dátam (vek `supplier_stock.json`, vek exportu) — keď zdroj neprejde, funkcia **nezaraďuje nič** a vracia dnešný chybový tvar.

- [ ] **Step 4: Spustiť a overiť, že prechádza**

Run: `WEBREVIEW_NO_SCHEDULER=1 MAIL_HOST="" WEBREVIEW_OUT=/tmp/wr-t9 .venv/bin/pytest tests/ --ignore=tests/e2e --ignore=tests/test_scheduler_guard.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add webreview/app.py tests/test_webreview_shoptet_upload.py
git commit -m "refactor: restock and in-stock producers queue instead of importing (#299)"
```

---

### Task 10: Prepnúť párovania a dodávateľov (jediný dnes živý zápis)

**Files:**
- Modify: `webreview/app.py` — `_do_upload_pairings` (`app.py:5670-5867`), `_do_upload_suppliers` (`app.py:5956-6227`)
- Modify: `tests/test_webreview_shoptet_upload.py`

**Interfaces:**
- Consumes: `queue_shoptet_fields` s `credit_group` = kľúč rozhodnutia (alebo `order:<code>`), `credit_value` = URL.
- Produces: rovnaký tvar výsledku ako dnes, s `"queued"` namiesto importných polí. Kreditovanie `uploaded_pairings.json` prechádza na drain.

- [ ] **Step 1: Napísať padajúci test**

```python
# doplniť do tests/test_webreview_shoptet_upload.py
def test_a_pairing_key_is_credited_only_after_ALL_its_codes_are_confirmed(cycle):
    webapp.queue_shoptet_fields(
        "parovania_eshop", "code;pairCode;internalNote",
        [["A", "P", "u"], ["B", "P", "u"]],
        credit_group={"A": "BETALOV|P", "B": "BETALOV|P"},
        credit_value={"A": "u", "B": "u"})
    credited = {}
    import webreview.app as w
    w._credit_producer = lambda store, entries: credited.setdefault(store, {}).update(entries)
    webapp.run_shoptet_upload()
    assert credited == {"parovania_eshop": {"BETALOV|P": "u"}}


def test_a_pairing_key_whose_second_code_failed_is_NOT_credited(cycle, monkeypatch):
    monkeypatch.setattr(webapp, "_import_rows_chunked",
                        lambda rows, header, dry, prefix, timeout=900: {
                            "ok": False, "partial": True, "success_codes": {"A"},
                            "partial_codes": {"B"}, "partial_failed": 1,
                            "chunks_total": 1, "chunks_ok": 0, "processed": 1,
                            "updated": 1, "failed": 1, "rc": 1,
                            "error_detail": None, "stdout_tail": "", "err": "",
                            "unreadable": False})
    webapp.queue_shoptet_fields(
        "parovania_eshop", "code;pairCode;internalNote",
        [["A", "P", "u"], ["B", "P", "u"]],
        credit_group={"A": "K", "B": "K"}, credit_value={"A": "u", "B": "u"})
    credited = {}
    webapp._credit_producer = lambda store, entries: credited.setdefault(store, {}).update(entries)
    res = webapp.run_shoptet_upload()
    assert credited == {}
    assert res["unconfirmed"] == 1
    assert "B" in webapp._load_pending()
```

- [ ] **Step 2: Spustiť a overiť, že padá**

Run: `WEBREVIEW_NO_SCHEDULER=1 MAIL_HOST="" WEBREVIEW_OUT=/tmp/wr-t10 .venv/bin/pytest tests/test_webreview_shoptet_upload.py -k pairing -v`
Expected: FAIL

- [ ] **Step 3: Implementovať**

V `_do_upload_pairings` nahraď celý blok od `if not _import_lock.acquire(...)` po `finally: _import_lock.release()` volaním:

```python
    credit_group = {}
    credit_value = {}
    for k in uploadable_keys:
        for c in (written_codes & set(by_key.get(k, {}).get("variant_codes") or [])):
            credit_group[c] = k
            credit_value[c] = (dec[k].get("url") or "").strip()
    for c in order_written_codes:
        credit_group[c] = f"order:{c}"
        credit_value[c] = (order_pairings[c] or "").strip()
    queued = queue_shoptet_fields("parovania_eshop", import_builder.LINK_HEADER,
                                  send_rows, credit_group=credit_group,
                                  credit_value=credit_value)
```

Ponechaj BEZ ZMENY: `_export_row_verdicts` potvrdenia (`confirmed` kódy sa nezaraďujú a kreditujú sa hneď ako dnes), hlásenia o `absent` kódoch, `blocked_keys`, `conflicts` a `owned_codes` vylúčenie. Vo výsledku nahraď importné polia `"queued": queued`.

Rovnako v `_do_upload_suppliers` (jeho vlastná hlavička a jeho `uploaded_suppliers.json` ako kredit store — doplň ho do mapy v `_credit_producer`).

- [ ] **Step 4: Spustiť a overiť, že prechádza**

Run: `WEBREVIEW_NO_SCHEDULER=1 MAIL_HOST="" WEBREVIEW_OUT=/tmp/wr-t10 .venv/bin/pytest tests/ --ignore=tests/e2e --ignore=tests/test_scheduler_guard.py -q && .venv/bin/pytest tests/e2e -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add webreview/app.py tests/test_webreview_shoptet_upload.py
git commit -m "refactor: pairings and suppliers queue; credit moves to the drain (#299)"
```

---

### Task 11: Hlasné tiché smrti

**Files:**
- Modify: `webreview/app.py` (`run_shoptet_upload` výsledok), `webreview/static/app.js` (`navError`, banner na karte)
- Modify: `tests/test_webreview_shoptet_upload.py`, `tests/e2e/test_shoptet_upload.py`

**Interfaces:**
- Consumes: `run_shoptet_upload` výsledok.
- Produces: vo výsledku `degraded: bool` a `warnings: list[str]` (slovenské vety s číslami); `navError('shoptet_upload')` sa rozsvieti pri `last_status==='error' || last_result.degraded`.

- [ ] **Step 1: Napísať padajúci test**

```python
# doplniť do tests/test_webreview_shoptet_upload.py
def test_unconfirmed_rows_make_the_run_degraded_with_a_slovak_warning(cycle, monkeypatch):
    monkeypatch.setattr(webapp, "_import_rows_chunked",
                        lambda rows, header, dry, prefix, timeout=900: {
                            "ok": False, "partial": True, "success_codes": set(),
                            "partial_codes": {"A"}, "partial_failed": 1,
                            "chunks_total": 1, "chunks_ok": 0, "processed": 0,
                            "updated": 0, "failed": 1, "rc": 1, "error_detail": None,
                            "stdout_tail": "", "err": "", "unreadable": False})
    webapp.queue_shoptet_fields("parovania_eshop", "code;pairCode;internalNote",
                                [["A", "P", "u"]])
    res = webapp.run_shoptet_upload()
    assert res["degraded"] is True
    assert any("nepotvrdil" in w for w in res["warnings"])


def test_an_enabled_producer_that_queues_nothing_three_runs_in_a_row_is_reported(cycle):
    for _ in range(3):
        res = webapp.run_shoptet_upload()
    assert any("nezaradil" in w for w in res["warnings"])
```

- [ ] **Step 2: Spustiť a overiť, že padá**

Run: `WEBREVIEW_NO_SCHEDULER=1 MAIL_HOST="" WEBREVIEW_OUT=/tmp/wr-t11 .venv/bin/pytest tests/test_webreview_shoptet_upload.py -k "degraded or queues_nothing" -v`
Expected: FAIL — `KeyError: 'degraded'`

- [ ] **Step 3: Implementovať**

V `run_shoptet_upload` postav `warnings` a `degraded` z týchto stavov (každý so slovenskou vetou a číslom):

```python
        warnings = []
        if unconfirmed:
            warnings.append(f"Shoptet nepotvrdil {unconfirmed} z {sent} riadkov — "
                            f"ostávajú v tabuľke a pôjdu znova.")
        if stale:
            warnings.append(f"{len(stale)} kódov čaká zablokovaných 3 a viac behov — "
                            f"eshop ich v katalógu nemá.")
        for key, ran in producers.items():
            if not ran:
                warnings.append(f"Automatizácia {key} sa nespustila — už bežala.")
        empty = _note_empty_producers(producers, pending)
        if empty:
            warnings.append("Zapnuté automatizácie nezaradili 3 behy po sebe nič: "
                            + ", ".join(empty) + " — ich zdroj je zrejme zamrznutý.")
        if skipped and sent:
            warnings.append("Druhé stiahnutie sa preskočilo, hoci sa niečo posielalo.")
        degraded = bool(warnings)
```

`_note_empty_producers(producers, pending)` je malý pomocník, ktorý si v `AUTOMATIONS_STATE`-nezávislom store `_store("upload_empty_streak.json")` drží počítadlo po producentoch a vracia tých s ≥3 nulovými behmi. Zapisuj ho pod `with _lock:` cez `_atomic_write_json(..., protect=False)` — je to štatistika, nie práca manažéra.

Vo frontende doplň `NAV_AUTOMATION_KEY['shoptet_upload'] = 'shoptet_upload'` (ak sa nav kľúč rovná kľúču automatizácie, over, či to mapovanie treba) a na karte vykresli `.autowarn` blok zo `warnings`.

- [ ] **Step 4: Spustiť a overiť, že prechádza**

Run: `WEBREVIEW_NO_SCHEDULER=1 MAIL_HOST="" WEBREVIEW_OUT=/tmp/wr-t11 .venv/bin/pytest tests/ --ignore=tests/e2e --ignore=tests/test_scheduler_guard.py -q && .venv/bin/pytest tests/e2e -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add webreview/app.py webreview/static/app.js tests/test_webreview_shoptet_upload.py tests/e2e/test_shoptet_upload.py
git commit -m "feat: the upload cycle says out loud what it could not do (#299)"
```

---

### Task 12: Playbook a odovzdanie

**Files:**
- Modify: `.claude/rules/automation-health.md`, `.claude/rules/store-prune.md`, `CLAUDE.md` (router)
- Modify: `docs/superpowers/specs/2026-07-28-sync-do-shoptetu-design.md` (doplniť namerané oneskorenie exportu)

- [ ] **Step 1: Doplniť pravidlá**

Do `.claude/rules/automation-health.md` pridaj sekciu o tabuľke čakajúcich zmien: kredit sa zapisuje AŽ po potvrdení importu (inak vzniká #257); zablokovaný riadok sa nikdy nezahadzuje, ale musí mať strop, po ktorom kričí; producent, ktorý zapnutý nezaradí nič N behov po sebe, má zamrznutý zdroj.

Do `.claude/rules/store-prune.md` pridaj: `pending_shoptet.json` je `protect=True` a je v `backup_data.sh`; nečitateľná tabuľka NESMIE viesť k zaradeniu na prázdno (stratili by sa čakajúce zmeny) — preto `queue_shoptet_fields` radšej vyhodí výnimku.

Do `## Playbook router` v `CLAUDE.md` pridaj riadok:

```markdown
- sync do Shoptetu / tabuľka čakajúcich zmien / kredit po potvrdení → `.claude/rules/automation-health.md`
```

- [ ] **Step 2: Zmerať oneskorenie exportu**

Po prvom skutočnom potvrdenom zápise (krok 3 zavádzania nižšie) zisti, o koľko neskôr sa nahratá hodnota objaví v katalógovom exporte:

```bash
date -Is                     # čas potvrdenia importu
# o 1, 5, 15 a 30 minút neskôr:
.venv/bin/python -c "import csv,sys; [print(r['code'], r['internalNote']) for r in csv.DictReader(open('data/products.csv', encoding='cp1250'), delimiter=';') if r['code']=='<KOD>']"
```

Nameranú hodnotu dopíš do §8 spec súboru a podľa nej rozhodni, či má druhé stiahnutie čakať (ak áno, pridaj `SECOND_SYNC_DELAY_S` a test naň).

- [ ] **Step 3: Commit**

```bash
git add .claude/rules CLAUDE.md docs/superpowers/specs/2026-07-28-sync-do-shoptetu-design.md
git commit -m "docs: playbook for the pending-changes table + measured export lag (#299)"
```

---

## Poradie zavádzania na živom (po zmergovaní)

Toto nie sú kódové kroky — to je postupnosť, ktorou sa automatizácie zapínajú v appke, aby tri bežiace nevypadli:

1. Nasadiť, cyklus nechať VYPNUTÝ. Overiť, že karta ukazuje „Čaká na nahratie: 0".
2. Spustiť cyklus raz ručne (`⚡ Spustiť teraz`) so všetkými producentmi vypnutými — musí prejsť stiahnutie, nič neposlať, druhé stiahnutie preskočiť.
3. Zapnúť `grube_externalcode` a `split_links` (dnes posielajú 0 riadkov). Spustiť cyklus ručne, overiť „Čaká na nahratie: 0".
4. Zapnúť `restock_skladom` a `stock_skladom`. Spustiť cyklus ručne a v administrácii Shoptetu overiť, že sa zmenilo ~19 + 16 riadkov a nič iné.
5. Zapnúť `parovania_eshop`. Spustiť cyklus ručne.
6. Zapnúť samotný `shoptet_upload` (hodinový rozvrh).
7. Odpublikovať/zrušiť n8n workflowy a zneplatniť ich prístupový token (#300).

---

## Self-review

**Pokrytie spec:**

| Spec | Task |
|---|---|
| §3.1 tabuľka, tvar, `blocked`, `attempts` | 1, 3, 4 |
| §3.2 producenti zaraďujú namiesto importu | 8, 9, 10 |
| §3.3 kroky cyklu 1–6, nárok, preskočenie druhého stiahnutia | 5, 6 |
| §3.4 poznámky k objednávkam | **mimo tohto plánu** — vlastný plán (uvedené v hlavičke) |
| §3.5 karta, čakajúce, zablokované | 7 |
| §4 tiché smrti | 11 (a `stale_blocked` už v 3) |
| §5 poradie zavádzania | sekcia „Poradie zavádzania" |
| §6 testy | v každom tasku + e2e v 7 |
| §8 zmerať oneskorenie exportu | 12 |

**Placeholdery:** žiadne „TBD/TODO/doplň si" — miesta, kde plán žiada overiť skutočné meno konštanty (Task 8, 9, 10), sú explicitné inštrukcie s postupom, nie nedopísané kroky.

**Konzistencia typov:** `queue_fields`/`build_import`/`settle`/`stale_blocked` majú rovnaké podpisy v Taskoch 1–3 aj vo volaniach v Tasku 6; `queue_shoptet_fields` má rovnaký podpis v Taskoch 4, 8, 9, 10; `_credit_producer(store, entries)` sa rozširuje v Taskoch 8 a 10 o ďalšie úložiská bez zmeny podpisu.
