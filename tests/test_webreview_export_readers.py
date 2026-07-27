"""#279 — every reader of the Shoptet catalogue export must open it with
``newline=""``.

The `csv` module documents that a file handed to `csv.reader`/`csv.DictReader` has
to be opened with ``newline=""``. Without it the TEXT layer rewrites ``\\r\\n`` and a
lone ``\\r`` to ``\\n`` BEFORE csv ever sees them — including inside a QUOTED field,
where they are data, not a record separator. The value the app then reads is not the
value the eshop holds.

`_iter_export_lines` (#272) already gets this right, so until this ticket the same
57 MB file was read under TWO different rules by two halves of the same app.

Measured against the whole-text truth (`io.StringIO(text, newline="")`, the pattern
`test_the_streamed_index_parses_exactly_like_a_whole_text_parse` uses):

    TRUTH / fixed  ->  'riadok1\\r\\nriadok2'
    pre-fix        ->  'riadok1\\nriadok2'

NOT tested here, deliberately: the ticket also claimed a lone ``\\r`` in an UNQUOTED
field raises ``_csv.Error: new-line character seen in unquoted field``. It does not —
universal newline RECOGNITION stays on with ``newline=""``, so both variants split
that record identically and neither raises (verified). A test for it could never go
red, and a green test that proves nothing is worse than no test.
"""
import ast
import csv
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

APP_PY = os.path.join(os.path.dirname(__file__), "..", "webreview", "app.py")

# One product row whose free-text columns carry BOTH hazards inside QUOTED fields:
# a CRLF (a multi-line Shoptet description/label) and a lone CR. Written as cp1250
# bytes exactly like the real export.
CR_EXPORT = (
    'code;pairCode;name;supplier;productVisibility;availabilityInStock;'
    'availabilityOutOfStock;price;standardPrice;stock;defaultImage;variant:Veľkosť;'
    'relatedProduct\r\n'
    '1/M;P1;"Bunda\r\nTest";BETALOV;visible;;"Vypredané\rdoobjednané";'
    '59,90;69,90;5;https://x/a.jpg;"M\r\nveľkosť";2/L\r\n'
    '2/L;P2;Iná bunda;BETALOV;visible;Skladom;;19,90;29,90;1;https://x/b.jpg;L;\r\n'
)


def _write_export(path):
    with open(path, "wb") as f:
        f.write(CR_EXPORT.encode("cp1250"))
    return path


def _truth():
    """What the export REALLY says — parsed with no newline translation at all."""
    return [dict(r) for r in
            csv.DictReader(io.StringIO(CR_EXPORT, newline=""), delimiter=";")]


# ── the drift guard: no FIFTH reader may regress ───────────────────────────────
# Written over the AST, never by counting strings: a guard that greps for a literal
# passes even when it guards nothing (playbook, „Drift guard nad AST"). cp1250 is the
# Shoptet export's encoding and is used for NOTHING else in this tree, so „opened as
# cp1250" is exactly „reads the catalogue export".
def test_every_catalogue_export_reader_opens_the_file_with_newline_disabled():
    source = open(APP_PY, encoding="utf-8").read()
    tree = ast.parse(source)
    offenders = []
    seen = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "open"):
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        enc = kw.get("encoding")
        if not (isinstance(enc, ast.Constant) and enc.value == "cp1250"):
            continue
        seen += 1
        nl = kw.get("newline")
        if not (isinstance(nl, ast.Constant) and nl.value == ""):
            offenders.append(node.lineno)
    # Cross-check the AST walk against the raw text, so the guard cannot pass by
    # silently matching NOTHING (a helper wrapping `open`, a renamed kwarg, an ast
    # shape this walk does not cover). Deliberately not a hard-coded count: routing a
    # reader away is a legitimate refactor and must not fail this test spuriously —
    # what must never happen is the walk MISSING one that is still there.
    assert seen == source.count('encoding="cp1250"'), (
        f"the AST walk found {seen} cp1250 open() calls but the file text has "
        f"{source.count('encoding=\"cp1250\"')} — the guard is no longer seeing them all")
    assert offenders == [], (
        "webreview/app.py opens the catalogue export without newline=\"\" at line(s) "
        f"{offenders} — csv then silently rewrites \\r\\n and \\r inside quoted "
        "fields to \\n, so the value read is not the value the eshop holds")


# ── behaviour, per call site ───────────────────────────────────────────────────
def test_load_catalog_preserves_a_carriage_return_inside_a_quoted_field(tmp_path):
    """`_load_catalog` (app.py:805 — the reader the ticket does NOT name) feeds
    CODE2PAIR, the size labels AND the whole search index, whose blob aggregates the
    multi-line HTML description/short-description. It is the most exposed of the four."""
    src = _write_export(tmp_path / "products.csv")
    truth = _truth()[0]

    code2pair, code2variant, catalog = webapp._load_catalog(str(src), set())

    assert code2pair["1/M"] == "P1"                       # unchanged, sanity
    assert catalog["P1"]["name"] == truth["name"] == "Bunda\r\nTest"
    assert code2variant["1/M"] == truth["variant:Veľkosť"] == "M\r\nveľkosť"


def test_the_nedostupne_catalogue_preserves_a_carriage_return_inside_a_quoted_field(
        tmp_path, monkeypatch):
    """`_ensure_nedostupne_catalog` (app.py:2403) resolves the product NAME shown for
    every alternative on the „Nedostupné tovary" tab."""
    src = _write_export(tmp_path / "products.csv")
    monkeypatch.setattr(webapp, "SRC", str(src))
    monkeypatch.setattr(webapp, "_NEDOSTUPNE_CAT", None)

    code2name, code2related = webapp._ensure_nedostupne_catalog()

    assert code2name["1/M"] == _truth()[0]["name"] == "Bunda\r\nTest"
    assert code2related["1/M"] == ["2/L"]                 # unchanged, sanity


def test_the_promoted_current_snapshot_preserves_a_carriage_return_inside_a_quoted_field(
        tmp_path, monkeypatch):
    """`_current_for_entry` (app.py:2319) copies the eshop availability LABEL verbatim
    into the review card's `current` snapshot — free text the shop owner writes, so a
    multi-line one is the shop's data, not ours to rewrite."""
    src = _write_export(tmp_path / "products.csv")
    monkeypatch.setattr(webapp, "SRC", str(src))

    cur = webapp._current_for_entry({"key": "K", "pairCode": "P1", "variant_codes": ["1/M"]})

    assert cur["avail"] == _truth()[0]["availabilityOutOfStock"] == "Vypredané\rdoobjednané"
    assert cur["state"] == 2                              # unchanged, sanity


def test_the_hourly_resync_matches_a_product_whose_name_carries_a_carriage_return(
        tmp_path, monkeypatch):
    """`run_shoptet_sync` (app.py:5588) feeds `resync_current`, which joins the export
    to review_data.json on (supplier, NAME). A rewritten name breaks that join, so the
    card silently goes `stale` — its price/stock stop refreshing — instead of syncing.
    This is the sharpest observable consequence of the whole ticket."""
    src = tmp_path / "products.csv"
    data = tmp_path / "review_data.json"
    products = [{"key": "BETALOV|P1", "idx": 0, "supplier": "BETALOV",
                 "name": "Bunda\r\nTest", "pairCode": "P1", "variant_codes": ["1/M"],
                 "our_url": "", "our_images": [], "ai_status": "matched",
                 "ai_chosen_url": "", "ai_reason": "", "candidates": [], "current": {}}]
    data.write_text(json.dumps(products, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(webapp, "SRC", str(src))
    monkeypatch.setattr(webapp, "DATA", str(data))
    monkeypatch.setattr(webapp, "ORDERS_CACHE", str(tmp_path / "orders_cache.csv"))
    monkeypatch.setattr(webapp, "CUSTOMERS_CACHE", str(tmp_path / "customers_cache.csv"))
    monkeypatch.setattr(webapp, "PRODUCTS", products)
    monkeypatch.setattr(webapp, "CODE2PAIR", {})
    monkeypatch.setattr(webapp, "CATALOG", {})
    monkeypatch.setattr(webapp, "_fetch_orders_csv", lambda: b"code;date\r\n")
    monkeypatch.setattr(webapp, "_fetch_export_csv", lambda: CR_EXPORT.encode("cp1250"))
    monkeypatch.setattr(webapp, "_fetch_customers_csv", lambda: b"guid;email\r\n")

    result = webapp.run_shoptet_sync()

    assert result["review_synced"] == 1 and result["review_stale"] == 0
    assert json.loads(data.read_text(encoding="utf-8"))[0]["current"]["price"] == "59,90"


@pytest.mark.parametrize("reader", ["_load_catalog", "_ensure_nedostupne_catalog"])
def test_the_readers_agree_with_a_whole_text_parse(tmp_path, monkeypatch, reader):
    """The invariant behind all of the above, stated once: reading the file must give
    the same field values as parsing the whole decoded text — the same equality
    `test_the_streamed_index_parses_exactly_like_a_whole_text_parse` pins for the
    streaming reader."""
    src = _write_export(tmp_path / "products.csv")
    monkeypatch.setattr(webapp, "SRC", str(src))
    monkeypatch.setattr(webapp, "_NEDOSTUPNE_CAT", None)
    names_truth = {r["code"]: r["name"] for r in _truth()}

    if reader == "_load_catalog":
        _c2p, _c2v, catalog = webapp._load_catalog(str(src), set())
        got = {e["variant_codes"][0]: e["name"] for e in catalog.values()}
    else:
        code2name, _rel = webapp._ensure_nedostupne_catalog()
        got = {c: n for c, n in code2name.items() if c in names_truth}

    assert got == names_truth
