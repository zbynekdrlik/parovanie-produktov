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
def _module_level_str_consts(tree) -> dict:
    """{NAME: "value"} for every module-level `NAME = "literal"`.

    Without this the guard was blind to `open(p, encoding=_EXPORT_ENCODING)` (PR #280
    review): the AST walk matched only `ast.Constant`, and the raw-text cross-check
    counted only the literal `encoding="cp1250"` — so a reader written that way was
    invisible to BOTH halves at once, and the guard reported `1 passed` with an
    unguarded reader present."""
    consts = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    consts[t.id] = node.value.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str) and isinstance(node.target, ast.Name):
            consts[node.target.id] = node.value.value
    return consts


def _is_cp1250(node, consts) -> bool:
    """Resolve the `encoding=` argument SYMBOLICALLY, not just as a literal."""
    if isinstance(node, ast.Constant):
        return node.value == "cp1250"
    if isinstance(node, ast.Name):
        return consts.get(node.id) == "cp1250"
    if isinstance(node, ast.Attribute):          # e.g. webapp._EXPORT_ENCODING
        return consts.get(node.attr) == "cp1250"
    return False


def _names_the_export_path(node) -> bool:
    """Second net, independent of the encoding: does this expression name the catalogue
    export file? Catches a reader that reaches the export while spelling its encoding in
    some shape the resolution above still cannot see."""
    if isinstance(node, ast.Name):
        return node.id in ("SRC", "src")
    if isinstance(node, ast.Attribute):
        return node.attr in ("SRC", "src")
    return False


def _is_binary(call) -> bool:
    """A binary open does NO newline translation at all, so it is never an offender —
    and app.py has one (the export is also read as bytes)."""
    mode = call.args[1] if len(call.args) > 1 else None
    for k in call.keywords:
        if k.arg == "mode":
            mode = k.value
    return isinstance(mode, ast.Constant) and isinstance(mode.value, str) \
        and "b" in mode.value


def unguarded_export_readers(source: str) -> tuple:
    """(offending line numbers, how many export readers were seen).

    Split out of the test so the GUARD ITSELF can be tested against every bypass shape
    — the playbook's rule for drift guards: „Vždy si guard otestuj tým, že mu podhodíš
    každý tvar"."""
    tree = ast.parse(source)
    consts = _module_level_str_consts(tree)
    offenders, seen = [], 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "open"):
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        path = node.args[0] if node.args else kw.get("file")
        reads_export = (_is_cp1250(kw.get("encoding"), consts)
                        or (path is not None and _names_the_export_path(path)))
        if not reads_export or _is_binary(node):
            continue
        seen += 1
        nl = kw.get("newline")
        if not (isinstance(nl, ast.Constant) and nl.value == ""):
            offenders.append(node.lineno)
    return offenders, seen


def test_every_catalogue_export_reader_opens_the_file_with_newline_disabled():
    source = open(APP_PY, encoding="utf-8").read()
    offenders, seen = unguarded_export_readers(source)
    # Cross-check the AST walk against the raw text, so the guard cannot pass by
    # silently matching NOTHING (a helper wrapping `open`, a renamed kwarg, an ast
    # shape this walk does not cover). Deliberately not a hard-coded count: routing a
    # reader away is a legitimate refactor and must not fail this test spuriously —
    # what must never happen is the walk MISSING one that is still there. `>=` because
    # the walk now ALSO catches readers the literal text search cannot see.
    assert seen >= source.count('encoding="cp1250"'), (
        f"the AST walk found {seen} export readers but the file text has "
        f"{source.count('encoding=\"cp1250\"')} literal cp1250 opens — the guard is no "
        "longer seeing them all")
    assert offenders == [], (
        "webreview/app.py opens the catalogue export without newline=\"\" at line(s) "
        f"{offenders} — csv then silently rewrites \\r\\n and \\r inside quoted "
        "fields to \\n, so the value read is not the value the eshop holds")


# ── the guard's OWN test: every bypass shape must be caught ────────────────────
@pytest.mark.parametrize("label,src", [
    ("literal encoding",
     '_ = open(p, encoding="cp1250")'),
    ("module constant encoding",
     '_EXPORT_ENCODING = "cp1250"\n_ = open(p, encoding=_EXPORT_ENCODING)'),
    ("attribute constant encoding",
     '_EXPORT_ENCODING = "cp1250"\n_ = open(p, encoding=webapp._EXPORT_ENCODING)'),
    ("export path, encoding elsewhere",
     'SRC = "x"\n_ = open(SRC, encoding=enc())'),
    ("export path via attribute",
     '_ = open(webapp.SRC, encoding=enc())'),
    ("keyword file= argument",
     'SRC = "x"\n_ = open(file=SRC, encoding=enc())'),
])
def test_the_drift_guard_catches_every_bypass_shape(label, src):
    offenders, seen = unguarded_export_readers(src)
    assert seen == 1, f"{label}: the guard did not even SEE this reader"
    assert offenders, f"{label}: the guard saw it but did not flag the missing newline"


@pytest.mark.parametrize("label,src", [
    ("literal encoding", '_ = open(p, encoding="cp1250", newline="")'),
    ("module constant", '_E = "cp1250"\n_ = open(p, encoding=_E, newline="")'),
    ("export path", 'SRC = "x"\n_ = open(SRC, encoding=enc(), newline="")'),
])
def test_the_drift_guard_accepts_a_correctly_opened_reader(label, src):
    offenders, seen = unguarded_export_readers(src)
    assert seen == 1 and offenders == [], f"{label}: false positive"


def test_the_drift_guard_ignores_a_binary_read_of_the_export():
    """A binary open does no newline translation at all — app.py has one and it is not
    an offender. Without this exemption the new path-based net would flag it."""
    offenders, seen = unguarded_export_readers('SRC = "x"\n_ = open(SRC, "rb")')
    assert (offenders, seen) == ([], 0)


def test_the_drift_guard_ignores_unrelated_files():
    offenders, seen = unguarded_export_readers('_ = open("notes.json", encoding="utf-8")')
    assert (offenders, seen) == ([], 0)


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


# ── #280 review, item 7: what the \r does to the SEARCH INDEX ─────────────────
# #279 changed 88% of the blobs (3862/4378 `search_blob_norm` entries now carry a raw
# \r that the text layer used to rewrite). An unmentioned, untested change to the search
# index does not ship unexamined — so the claim „inert" is PINNED here rather than
# asserted in prose. It is inert for a structural reason, not a lucky one:
#
#   • `search_catalog` tokenizes the query with `_words` (`[^a-z0-9]+`), so a query term
#     is ALWAYS alnum-only — it can never contain \r, \n or a space, and can therefore
#     never span the boundary where the two variants differ;
#   • `_words(name_norm)` splits on that same class, so \r and \n are BOTH separators →
#     the tokens feeding the whole-word (5) and prefix (4) tiers are identical;
#   • the whole-query bonuses (`qn == name_norm`, `qn in name_norm`) match under NEITHER
#     variant, so they cannot differ either.
#
# Normalising the \r away was rejected: #279 exists precisely to keep the eshop's bytes
# intact, and `normalize_text` is shared with other callers.
def _catalog_with(name_sep):
    from parovanie.catalog_index import build_catalog_index
    rows = [{"code": "1/M", "pairCode": "P1", "name": f"Bunda{name_sep}Zelena Test",
             "description": f"Popis{name_sep}dlhy text", "supplier": "BETALOV",
             "externalCode": "", "shortDescription": "", "manufacturer": "", "ean": "",
             "productNumber": "", "price": "10", "stock": "1",
             "productVisibility": "visible", "availabilityInStock": "Skladom",
             "availabilityOutOfStock": ""}]
    return build_catalog_index(rows, set())


@pytest.mark.parametrize("q", [
    "bunda", "zelena", "bunda zelena", "test", "popis", "dlhy", "unda", "nda zel",
    "bunda test", "popis dlhy", "betalov", "1/m",
])
def test_a_carriage_return_in_the_blob_changes_no_search_result(q):
    """The CRLF-carrying index (post-#279) must answer every query exactly like the
    LF-carrying one (pre-#279) — same hits, same order."""
    from parovanie.catalog_index import search_catalog
    crlf = search_catalog(_catalog_with("\r\n"), q)
    lf = search_catalog(_catalog_with("\n"), q)
    assert [e["key"] for e in crlf] == [e["key"] for e in lf], f"query {q!r} diverged"


def test_the_blobs_really_do_differ_so_the_test_above_is_not_vacuous():
    """Guard against the pin proving nothing: the two indexes must genuinely hold
    different bytes, otherwise the equality above is trivially true."""
    a = _catalog_with("\r\n")["P1"]["search_blob_norm"]
    b = _catalog_with("\n")["P1"]["search_blob_norm"]
    assert a != b and "\r" in a and "\r" not in b


def test_a_query_term_can_never_contain_the_separator_that_differs():
    """The structural reason the above holds for ALL queries, not just the sampled ones:
    the tokenizer cannot emit a term carrying \\r, \\n or a space."""
    from parovanie.catalog_index import _words, normalize_text
    for raw in ["bunda\rzelena", "bunda\nzelena", "bunda zelena", "bunda\r\nzelena"]:
        terms = _words(normalize_text(raw))
        assert terms == ["bunda", "zelena"], f"{raw!r} tokenized as {terms}"
        assert all(c.isalnum() for t in terms for c in t)
