"""#242 — a wrong pairing URL must be fixable straight from the „Na objednanie" tab.

Reproduced on live data (v0.93.0, 89 order lines): 55 of them (62 %) rendered a
READ-ONLY 🔗 built from a reviewed decision, with no edit affordance at all, so the
manager's only recourse was to find the product in the review tab and pair it again
(„musím to znova nájsť a načítať"). Worse, an inline pairing pasted on such a row is
silently outranked by the decision in BOTH the row render and the eshop write-back
(`order_pairing_rows(..., exclude_codes=<codes covered by link_rows>)`), so the save
looks accepted and changes nothing („som to naparoval, ale tie linky vobec nefungujú").

The fix therefore edits the value that is actually displayed AND shipped: the row names
the decision that owns its link (`reviewKey`/`reviewStatus`), and a dedicated endpoint
rewrites THAT decision — never a parallel store that the write-back would discard.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

from parovanie import import_builder  # noqa: E402

from tests.conftest import authed_client as _client  # noqa: E402 — logged-in session (#91)

# One order line whose product carries a reviewed decision, one whose product does not.
ORDERS_CSV = (
    "code;date;statusName;itemName;itemAmount;itemCode;itemVariantName;itemSupplier\r\n"
    "20261045;2026-04-24 19:14:05;Vybavuje sa;Polokosela HART;1;61247/L;Velkost: L;BETALOV\r\n"
    "20261046;2026-04-25 19:14:05;Vybavuje sa;Nepatriaci do review;1;99999/M;Velkost: M;ORBIS\r\n"
)
PRODUCTS = [{"key": "BETALOV|231", "supplier": "BETALOV", "name": "Polokosela HART",
             "variant_codes": ["61247/L", "61247/XL"], "pairCode": "231"}]
DECISIONS = {"BETALOV|231": {"status": "good", "url": "https://www.huntingshop.eu/stara"}}
CODE2PAIR = {"61247/L": "231", "61247/XL": "231"}


def _rows():
    return webapp.build_to_order_rows(ORDERS_CSV, PRODUCTS, DECISIONS, CODE2PAIR)


def _by_code(rows, code):
    return next(r for r in rows if r["itemCode"] == code)


# --- the row must name the decision that owns its link ---------------------- #
def test_a_decision_backed_row_names_the_decision_that_owns_its_link():
    """Without this the tab can only offer a paste box writing to a PARALLEL store —
    the exact no-op the manager hit. Naming the owner is what lets the edit land on
    the value the row shows and the import ships."""
    r = _by_code(_rows(), "61247/L")
    assert r["supplierUrl"] == "https://www.huntingshop.eu/stara"
    assert r["reviewKey"] == "BETALOV|231"
    assert r["reviewStatus"] == "good"


def test_a_row_outside_the_review_set_names_no_owner():
    """The inline-pairing path must stay exactly as it was for these rows."""
    r = _by_code(_rows(), "99999/M")
    assert r["supplierUrl"] == ""
    assert r["reviewKey"] == "" and r["reviewStatus"] == ""


def test_every_variant_of_the_product_names_the_same_owner():
    """A decision covers ALL variant codes, so a sibling size ordered separately must
    be fixable from its own row too — and the fix must propagate to both."""
    orders = ORDERS_CSV + (
        "20261047;2026-04-26 19:14:05;Vybavuje sa;Polokosela HART;1;61247/XL;Velkost: XL;BETALOV\r\n")
    rows = webapp.build_to_order_rows(orders, PRODUCTS, DECISIONS, CODE2PAIR)
    assert _by_code(rows, "61247/XL")["reviewKey"] == "BETALOV|231"
    assert _by_code(rows, "61247/L")["reviewKey"] == "BETALOV|231"


# --- link_owners cannot drift away from link_rows --------------------------- #
def test_link_owners_covers_exactly_the_codes_link_rows_writes():
    """Both come from ONE loop, so this pins the identity rather than a copy of the
    dedup rules ('first pairing wins' must not be reimplemented twice)."""
    products = [
        {"key": "A|1", "supplier": "BETALOV", "variant_codes": ["c1", "shared"],
         "pairCode": "1"},
        # second product deliberately re-uses `shared` — link_rows keeps the FIRST
        {"key": "B|2", "supplier": "ORBIS", "variant_codes": ["shared", "c2"],
         "pairCode": "2"},
        {"key": "C|3", "supplier": "ORBIS", "variant_codes": ["c3"], "pairCode": "3"},
    ]
    decisions = {"A|1": {"status": "good", "url": "https://a.test/x"},
                 "B|2": {"status": "manual", "url": "https://b.test/y"},
                 "C|3": {"status": "unavailable", "url": ""}}   # no URL → no row
    rows = import_builder.link_rows(products, decisions, {})
    owners = import_builder.link_owners(products, decisions, {})
    assert set(owners) == {r[0] for r in rows}
    assert owners["shared"] == ("A|1", "good")        # first pairing wins, same as rows
    assert owners["c2"] == ("B|2", "manual")
    assert "c3" not in owners


# --- the endpoint that rewrites the reviewed link --------------------------- #
def _arm(monkeypatch, tmp_path, decisions=None):
    monkeypatch.setattr(webapp, "DECISIONS", str(tmp_path / "decisions.json"))
    monkeypatch.setattr(webapp, "PRODUCTS", PRODUCTS)
    webapp._save_decisions(dict(DECISIONS if decisions is None else decisions))


def test_the_endpoint_rewrites_the_reviewed_decision_itself(monkeypatch, tmp_path):
    _arm(monkeypatch, tmp_path)
    c = _client()
    r = c.post("/api/order-decision-url",
               json={"key": "BETALOV|231", "url": "https://www.huntingshop.eu/nova"})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    d = webapp._load_decisions()["BETALOV|231"]
    assert d["url"] == "https://www.huntingshop.eu/nova"
    # a hand-picked link is 'manual' — 'good' renders p.ai_chosen_url, not the stored
    # one, so keeping 'good' here would show the OLD link back (playbook, review tab)
    assert d["status"] == "manual"


def test_the_rewrite_reaches_the_eshop_import_row(monkeypatch, tmp_path):
    """The whole point: the corrected URL must be what link_rows ships, for EVERY
    variant of the product — otherwise the manager fixed only the screen."""
    _arm(monkeypatch, tmp_path)
    _client().post("/api/order-decision-url",
                   json={"key": "BETALOV|231", "url": "https://www.huntingshop.eu/nova"})
    rows = import_builder.link_rows(PRODUCTS, webapp._load_decisions(), CODE2PAIR)
    assert sorted(rows) == [["61247/L", "231", "https://www.huntingshop.eu/nova"],
                            ["61247/XL", "231", "https://www.huntingshop.eu/nova"]]


def test_a_non_http_url_is_refused_and_nothing_changes(monkeypatch, tmp_path):
    """Same authoritative guard /api/order-pair carries — this value becomes an href
    AND an eshop internalNote, so `javascript:` / 'httpfoo' must never land."""
    _arm(monkeypatch, tmp_path)
    for bad in ("javascript:alert(1)", "httpfoo://x", "www.huntingshop.eu/nova"):
        r = _client().post("/api/order-decision-url",
                           json={"key": "BETALOV|231", "url": bad})
        assert r.status_code == 400, bad
    assert webapp._load_decisions()["BETALOV|231"]["url"] == "https://www.huntingshop.eu/stara"


def test_an_empty_url_is_refused_rather_than_wiping_the_pairing(monkeypatch, tmp_path):
    """Clearing a pairing is a review-tab decision ('↩ Vrátiť'), not something a
    stray empty save on the order tab should do."""
    _arm(monkeypatch, tmp_path)
    r = _client().post("/api/order-decision-url", json={"key": "BETALOV|231", "url": ""})
    assert r.status_code == 400
    assert webapp._load_decisions()["BETALOV|231"]["url"] == "https://www.huntingshop.eu/stara"


def test_an_unknown_key_is_refused(monkeypatch, tmp_path):
    """A key that is not a live review product would be pruned away at the next start
    (app.py prune) — accepting it would silently drop the manager's correction."""
    _arm(monkeypatch, tmp_path)
    r = _client().post("/api/order-decision-url",
                       json={"key": "NIEKTO|999", "url": "https://x.test/y"})
    assert r.status_code == 404
    assert "NIEKTO|999" not in webapp._load_decisions()


def test_a_split_decision_is_never_clobbered(monkeypatch, tmp_path):
    """A split product carries a DIFFERENT supplier URL per size (#174). Overwriting it
    with one product-wide 'manual' link would throw every per-size link away."""
    _arm(monkeypatch, tmp_path, {"BETALOV|231": {"status": "split", "url": ""}})
    r = _client().post("/api/order-decision-url",
                       json={"key": "BETALOV|231", "url": "https://www.huntingshop.eu/nova"})
    assert r.status_code == 409
    assert webapp._load_decisions()["BETALOV|231"]["status"] == "split"


def test_the_endpoint_requires_a_login(monkeypatch, tmp_path):
    _arm(monkeypatch, tmp_path)
    anon = webapp.app.test_client()
    r = anon.post("/api/order-decision-url",
                  json={"key": "BETALOV|231", "url": "https://x.test/y"})
    assert r.status_code == 401
