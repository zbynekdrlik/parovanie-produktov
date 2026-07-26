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


# --- the owner map cannot drift away from what link_rows writes ------------- #
def test_link_row_specs_names_the_owner_of_exactly_the_codes_link_rows_writes():
    """`link_row_specs` is the ONE loop behind both the eshop rows and the row's
    `reviewKey` (app.py calls it directly), so this pins the identity rather than a
    copy of the dedup rules ('first pairing wins' must not be reimplemented twice)."""
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
    owners = {c: (key, st) for c, _pc, _url, key, st
              in import_builder.link_row_specs(products, decisions, {})}
    assert set(owners) == {r[0] for r in rows}
    assert owners["shared"] == ("A|1", "good")        # first pairing wins, same as rows
    assert owners["c2"] == ("B|2", "manual")
    assert "c3" not in owners


# --- a split product must not be a blind spot on the to-order tab ----------- #
SPLIT_DECISIONS = {"BETALOV|231": {"status": "split", "url": ""}}
SPLIT_LINKS = {"61247/L": "https://www.huntingshop.eu/velkost-L",
               "61247/XL": "https://www.huntingshop.eu/velkost-XL"}


def test_a_split_row_shows_its_per_size_link_and_names_its_owner():
    """Without variant_links the `split` branch of link_row_specs yields nothing, so
    the row rendered `supplierUrl=''` + `reviewKey=''` — an empty paste box for a
    product that IS paired per size. `savePairUrl` then wrote to order_pairings, which
    the manual zip throws away while the nightly ships it — and because split_links
    keeps its own uploaded_variant_links.json idempotency, that inline write
    permanently clobbers an already-uploaded per-size link."""
    rows = webapp.build_to_order_rows(ORDERS_CSV, PRODUCTS, SPLIT_DECISIONS, CODE2PAIR,
                                      SPLIT_LINKS)
    r = _by_code(rows, "61247/L")
    assert r["supplierUrl"] == "https://www.huntingshop.eu/velkost-L"
    assert r["reviewKey"] == "BETALOV|231"
    assert r["reviewStatus"] == "split"


def test_a_split_variant_with_no_link_of_its_own_still_names_its_owner():
    """A size the manager has not linked yet emits no eshop row (never an empty
    internalNote cell), so it has no owner to name — and must therefore keep the
    plain inline paste box rather than silently pretend to be unpaired-but-owned."""
    rows = webapp.build_to_order_rows(ORDERS_CSV, PRODUCTS, SPLIT_DECISIONS, CODE2PAIR,
                                      {"61247/XL": SPLIT_LINKS["61247/XL"]})
    r = _by_code(rows, "61247/L")
    assert r["supplierUrl"] == "" and r["reviewKey"] == "" and r["reviewStatus"] == ""


def test_an_owner_without_a_usable_key_is_reported_as_no_owner_at_all():
    """`"reviewKey": owner_key or ""` conflated 'no owner' with 'falsy key': the row
    then advertised `reviewStatus:'good'` with an EMPTY `reviewKey`, so `savePairUrl`
    routed the correction to order_pairings — which the write-back discards for a code
    link_rows already covers. That is the very silent no-op #242 exists to remove, so
    a spec with no usable key must report no owner at all (→ plain inline path)."""
    products = [dict(PRODUCTS[0], key="")]
    rows = webapp.build_to_order_rows(ORDERS_CSV, products,
                                      {"": {"status": "good", "url": "https://a.test/x"}},
                                      CODE2PAIR)
    r = _by_code(rows, "61247/L")
    assert r["supplierUrl"] == "https://a.test/x"        # the link is still shown
    assert r["reviewKey"] == "" and r["reviewStatus"] == ""


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


def test_a_status_that_is_not_a_pairing_is_never_overwritten(monkeypatch, tmp_path):
    """`reviewKey` is frozen in the client's ORDERS snapshot, so anything the manager
    changes in the review tab afterwards (another window, a tab left open) was
    clobbered by the next ✏️ save: 'unavailable' silently became 'manual' and the eshop
    stopped getting the Vypredané/stock-0 assertion; 'discontinued' lost its
    „Predaj skončil" row. Only a real pairing may be rewritten from this tab."""
    for st in ("unavailable", "discontinued", "bad"):
        _arm(monkeypatch, tmp_path, {"BETALOV|231": {"status": st, "url": ""}})
        r = _client().post("/api/order-decision-url",
                           json={"key": "BETALOV|231", "url": "https://x.test/y"})
        assert r.status_code == 409, st
        assert webapp._load_decisions()["BETALOV|231"]["status"] == st, st


def test_the_state_rows_of_an_unavailable_product_survive_a_stale_edit(monkeypatch, tmp_path):
    """The concrete damage: the row that tells Shoptet 'Vypredané, stock 0' simply
    disappeared once the decision had been flipped to 'manual'."""
    _arm(monkeypatch, tmp_path, {"BETALOV|231": {"status": "unavailable", "url": ""}})
    before = import_builder.state_rows(PRODUCTS, webapp._load_decisions(), CODE2PAIR)
    assert before, "fixture must produce a state row to begin with"
    _client().post("/api/order-decision-url",
                   json={"key": "BETALOV|231", "url": "https://x.test/y"})
    assert import_builder.state_rows(PRODUCTS, webapp._load_decisions(), CODE2PAIR) == before


def test_a_product_with_no_decision_is_not_marked_reviewed(monkeypatch, tmp_path):
    """The manager pressed „↩ Vrátiť" meanwhile: the endpoint used to CREATE a
    'manual' decision, marking an unreviewed product reviewed behind his back."""
    _arm(monkeypatch, tmp_path, {})
    r = _client().post("/api/order-decision-url",
                       json={"key": "BETALOV|231", "url": "https://x.test/y"})
    assert r.status_code == 409
    assert "BETALOV|231" not in webapp._load_decisions()


def test_correcting_a_reviewed_link_drops_the_superseded_inline_pairing(monkeypatch, tmp_path):
    """The nightly can only exclude what the decision covers; the inline value for the
    same code is dead weight that used to sit there waiting to be shipped. At THIS
    moment it is provably superseded, so drop it — other codes are untouched."""
    _arm(monkeypatch, tmp_path)
    monkeypatch.setattr(webapp, "ORDER_PAIRINGS", str(tmp_path / "order_pairings.json"))
    webapp._save_order_pairings({"61247/L": "https://stale.test/inline",
                                 "61247/XL": "https://stale.test/inline-xl",
                                 "99999/M": "https://other.test/keep"})
    r = _client().post("/api/order-decision-url",
                       json={"key": "BETALOV|231", "url": "https://www.huntingshop.eu/nova"})
    assert r.status_code == 200
    left = webapp._load_order_pairings()
    assert "61247/L" not in left and "61247/XL" not in left
    assert left == {"99999/M": "https://other.test/keep"}


def test_an_absurdly_long_url_is_refused(monkeypatch, tmp_path):
    """A 300 000-char URL was accepted and grew decisions.json to 300 kB — a store
    re-read on every /api/orders, whose value also lands in a Shoptet internalNote
    cell. The same cap guards /api/order-pair and /api/decision."""
    _arm(monkeypatch, tmp_path)
    monkeypatch.setattr(webapp, "ORDER_PAIRINGS", str(tmp_path / "order_pairings.json"))
    long_url = "https://x.test/" + "a" * 300000
    assert _client().post("/api/order-decision-url",
                          json={"key": "BETALOV|231", "url": long_url}).status_code == 400
    assert webapp._load_decisions()["BETALOV|231"]["url"] == "https://www.huntingshop.eu/stara"
    assert _client().post("/api/order-pair",
                          json={"code": "99999/M", "url": long_url}).status_code == 400
    assert webapp._load_order_pairings() == {}
    assert _client().post("/api/decision",
                          json={"key": "BETALOV|231", "status": "manual",
                                "url": long_url}).status_code == 400
    assert webapp._load_decisions()["BETALOV|231"]["url"] == "https://www.huntingshop.eu/stara"


def test_a_crlf_in_the_url_cannot_forge_a_log_line(monkeypatch, tmp_path, caplog):
    """`log.info("order-decision-url key=%s url=%s", …)` echoed the raw value, so
    posting `https://x.test/a\\r\\nSet-Cookie: x` produced a bare `Set-Cookie: x` log
    line of its own."""
    _arm(monkeypatch, tmp_path)
    monkeypatch.setattr(webapp, "ORDER_PAIRINGS", str(tmp_path / "order_pairings.json"))
    forged = "https://x.test/a\r\nSet-Cookie: x"
    with caplog.at_level("INFO"):
        _client().post("/api/order-decision-url", json={"key": "BETALOV|231", "url": forged})
        _client().post("/api/order-pair", json={"code": "99999/M", "url": forged})
    for rec in caplog.records:
        assert "\r" not in rec.getMessage() and "\n" not in rec.getMessage(), rec.getMessage()


def test_the_manager_reads_the_endpoints_refusals_in_slovak(monkeypatch, tmp_path):
    """`postToOrder` surfaces `j.error` verbatim into a Slovak alert, so an English
    'unknown review key' (the reachable one — a stale tab after a resync pruned the
    product) reached him as-is."""
    _arm(monkeypatch, tmp_path)
    cases = [
        {"key": "", "url": "https://x.test/y"},
        {"key": "BETALOV|231", "url": ""},
        {"key": "BETALOV|231", "url": "javascript:alert(1)"},
        {"key": "NIEKTO|999", "url": "https://x.test/y"},
    ]
    for payload in cases:
        err = _client().post("/api/order-decision-url", json=payload).get_json()["error"]
        assert err and not err.isascii(), f"not Slovak: {err!r} for {payload}"


def test_the_endpoint_requires_a_login(monkeypatch, tmp_path):
    _arm(monkeypatch, tmp_path)
    anon = webapp.app.test_client()
    r = anon.post("/api/order-decision-url",
                  json={"key": "BETALOV|231", "url": "https://x.test/y"})
    assert r.status_code == 401
