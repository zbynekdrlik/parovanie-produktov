"""Pure-logic tests for nedostupné tovary (#100) — no network, no SMTP, no file I/O."""
from parovanie import nedostupne as N

ORDERS = (
    "code;date;statusName;email;billFullName;itemName;itemAmount;itemCode;itemVariantName\r\n"
    # two DIFFERENT customers ordered the unavailable variant 40237/3XL (both open)
    "5001;2026-07-10 10:00:00;Vybavuje sa;ada@example.com;Ada Nová;Nohavice FOREST;1;40237/3XL;Veľkosť: 3XL\r\n"
    "5002;2026-07-11 11:00:00;Vybavuje sa;bob@example.com;Bob Starý;Nohavice FOREST;2;40237/3XL;Veľkosť: 3XL\r\n"
    # a DIFFERENT (available) variant 40237/4XL — must NOT be pulled in
    "5003;2026-07-12 09:00:00;Vybavuje sa;cyr@example.com;Cyril;Nohavice FOREST;1;40237/4XL;Veľkosť: 4XL\r\n"
    # a CLOSED order for the unavailable variant — must NOT be pulled in
    "5004;2026-07-09 09:00:00;Vybavené;dan@example.com;Dana;Nohavice FOREST;1;40237/3XL;Veľkosť: 3XL\r\n"
)


def _resolve(code):
    if code == "40237/3XL":
        return ("Nohavice FOREST 1003", [
            {"code": "60116/90", "name": "Opasok FOREST", "url": "https://www.forestshop.sk/opasok/"},
            {"code": "60109", "name": "Ponožky", "url": ""},
        ])
    return ("", [])


def test_unavailable_item_codes_extracts_itemcode_from_line_key():
    store = {"5001|40237/3XL": True, "5002|40237/3XL": True,
             "9999|CANCELLED": False, "malformed": True}
    assert N.unavailable_item_codes(store) == {"40237/3XL"}


def test_unavailable_item_codes_empty_and_none():
    assert N.unavailable_item_codes({}) == set()
    assert N.unavailable_item_codes(None) == set()


def test_affected_orders_exact_variant_open_only():
    got = N.affected_orders(ORDERS, {"40237/3XL"})
    assert set(got) == {"40237/3XL"}
    orders = got["40237/3XL"]
    # only the two OPEN orders for the exact variant — closed 5004 + variant 4XL excluded
    assert [o["orderCode"] for o in orders] == ["5001", "5002"]
    assert orders[0]["email"] == "ada@example.com"
    assert orders[0]["billFullName"] == "Ada Nová"
    assert orders[0]["key"] == "5001|40237/3XL"


def test_affected_orders_empty_codes_short_circuits():
    assert N.affected_orders(ORDERS, set()) == {}


def test_affected_orders_accepts_cp1250_bytes():
    got = N.affected_orders(ORDERS.encode("cp1250"), {"40237/3XL"})
    assert len(got["40237/3XL"]) == 2


def test_build_view_groups_state_alternatives_and_sent_flags():
    unavail = {"5001|40237/3XL": True}
    state = {"40237/3XL": {"nedostupne": True, "alternativa": False,
                           "sent": {"5001|nedostupne": {"at": "x", "email": "ada@example.com"}}}}
    view = N.build_view(ORDERS, unavail, state, _resolve)
    assert len(view) == 1
    p = view[0]
    assert p["code"] == "40237/3XL"
    assert p["itemName"] == "Nohavice FOREST"        # from the order line
    assert p["nedostupne"] is True and p["alternativa"] is False
    assert p["order_count"] == 2
    assert len(p["alternatives"]) == 2
    assert p["alternatives"][0]["name"] == "Opasok FOREST"
    # order 5001 already got the unavailable e-mail; 5002 not
    o5001 = next(o for o in p["orders"] if o["orderCode"] == "5001")
    o5002 = next(o for o in p["orders"] if o["orderCode"] == "5002")
    assert o5001["unavailable_sent"] is True
    assert o5002["unavailable_sent"] is False
    assert p["unavailable_sent_count"] == 1
    assert p["alternative_sent_count"] == 0


def test_build_view_flagged_code_without_open_order_still_listed():
    # flagged variant that no longer has any OPEN order → listed, 0 orders, name from catalog
    unavail = {"7777|40237/3XL": True, "8888|ZZZ999": True}
    view = N.build_view(ORDERS, unavail, {}, _resolve)
    zzz = next(p for p in view if p["code"] == "ZZZ999")
    assert zzz["order_count"] == 0
    assert zzz["itemName"] == ""                       # unknown code → no name


def test_build_view_equal_dates_fall_back_to_name():
    # both have an open order on the SAME date → name/code tiebreak (Alfa before Zebra)
    orders = (
        "code;date;statusName;email;billFullName;itemName;itemAmount;itemCode\r\n"
        "1;2026-07-10 10:00:00;Vybavuje sa;a@x.sk;A;Zebra;1;Z1\r\n"
        "2;2026-07-10 10:00:00;Vybavuje sa;b@x.sk;B;Alfa;1;A1\r\n"
    )
    unavail = {"1|Z1": True, "2|A1": True}
    view = N.build_view(orders, unavail, {}, lambda c: ("", []))
    assert [p["itemName"] for p in view] == ["Alfa", "Zebra"]


def test_build_view_open_order_products_first_newest_on_top():
    # #185 — products WITH an open order come first (newest order on top); no-order product last
    orders = (
        "code;date;statusName;email;billFullName;itemName;itemAmount;itemCode\r\n"
        "10;2026-07-01 08:00:00;Vybavuje sa;a@x.sk;A;AAA;1;A1\r\n"   # older open order
        "11;2026-07-20 09:00:00;Vybavuje sa;b@x.sk;B;BBB;1;B1\r\n"   # newer open order
    )
    unavail = {"10|A1": True, "11|B1": True, "99|C1": True}          # C1 flagged, no open order
    view = N.build_view(orders, unavail, {}, lambda c: ("CCC" if c == "C1" else "", []))
    assert [p["code"] for p in view] == ["B1", "A1", "C1"]           # newest → older → no-order
    assert view[-1]["order_count"] == 0


def test_build_view_orders_by_max_open_order_date():
    # #185 — a product with multiple open orders sorts by its NEWEST (max) order date
    orders = (
        "code;date;statusName;email;billFullName;itemName;itemAmount;itemCode\r\n"
        "20;2026-07-05 08:00:00;Vybavuje sa;a@x.sk;A;P1;1;P1\r\n"
        "21;2026-07-15 08:00:00;Vybavuje sa;a2@x.sk;A2;P1;1;P1\r\n"  # P1's newest = 07-15
        "22;2026-07-10 08:00:00;Vybavuje sa;b@x.sk;B;P2;1;P2\r\n"    # P2's only = 07-10
    )
    unavail = {"20|P1": True, "22|P2": True}
    view = N.build_view(orders, unavail, {}, lambda c: ("", []))
    assert [p["code"] for p in view] == ["P1", "P2"]                 # 07-15 > 07-10


def test_build_view_invalid_or_empty_order_date_does_not_crash():
    # #185 — empty / unparseable dates are treated as oldest and never raise
    orders = (
        "code;date;statusName;email;billFullName;itemName;itemAmount;itemCode\r\n"
        "30;2026-07-20 08:00:00;Vybavuje sa;a@x.sk;A;GOOD;1;G1\r\n"  # valid newest
        "31;;Vybavuje sa;b@x.sk;B;EMPTY;1;E1\r\n"                    # empty date
        "32;not-a-date;Vybavuje sa;c@x.sk;C;BADD;1;B1\r\n"           # garbage date
    )
    unavail = {"30|G1": True, "31|E1": True, "32|B1": True}
    view = N.build_view(orders, unavail, {}, lambda c: ("", []))
    codes = [p["code"] for p in view]
    assert set(codes) == {"G1", "E1", "B1"}                         # no crash, all present
    assert codes[0] == "G1"                                          # valid newest date on top


def test_plan_sends_dedups_persistent_batch_and_missing_email():
    rows = [
        {"orderCode": "5001", "email": "ada@example.com", "billFullName": "Ada", "key": "5001|C"},
        {"orderCode": "5002", "email": "bob@example.com", "billFullName": "Bob", "key": "5002|C"},
        {"orderCode": "5005", "email": "ada@example.com", "billFullName": "Ada2", "key": "5005|C"},
        {"orderCode": "5006", "email": "", "billFullName": "NoMail", "key": "5006|C"},
    ]
    sent = {"5002|nedostupne": {"at": "x"}}      # 5002 already sent
    plan = N.plan_sends(rows, sent, N.TYPE_UNAVAILABLE)
    # 5002 already sent (skip), 5006 no email (skip), 5005 dup email of 5001 (skip) -> only 5001
    assert [r["orderCode"] for r in plan] == ["5001"]


def test_plan_sends_all_eligible():
    rows = [
        {"orderCode": "5001", "email": "ada@example.com", "billFullName": "Ada", "key": "k1"},
        {"orderCode": "5002", "email": "bob@example.com", "billFullName": "Bob", "key": "k2"},
    ]
    plan = N.plan_sends(rows, {}, N.TYPE_ALTERNATIVE)
    assert [r["email"] for r in plan] == ["ada@example.com", "bob@example.com"]


def test_build_unavailable_email_uses_boss_exact_wording():
    # #183 — the exact text the boss gave; product name is NOT woven in (generic wording).
    subj, html = N.build_unavailable_email("Ada <b>Nová</b>", "Nohavice & spol")
    assert subj == N.UNAVAILABLE_SUBJECT
    assert "Ada &lt;b&gt;Nová&lt;/b&gt;" in html          # greeting name escaped
    assert "veľmi sa ospravedlňujeme" in html
    assert "momentálne nedostupný" in html
    assert "nevieme kedy bude naskladnený" in html
    assert "Vašu objednávku úspešne vybaviť" in html
    assert "S pozdravom" in html
    assert "Drlík, Forestshop.sk" in html                 # boss's signoff
    # no generic house text, no product name, and the shell's default signature is NOT appended too
    assert "Nohavice" not in html
    assert "Mrzí nás to" not in html
    assert "Tím Forestshop.sk" not in html


def test_build_unavailable_email_blank_name_falls_back():
    _subj, html = N.build_unavailable_email("", "")
    assert "zákazník" in html                             # greeting fallback
    assert "veľmi sa ospravedlňujeme" in html             # boss body present regardless of name


def test_build_alternative_email_lists_links_and_escapes():
    alts = [
        {"code": "60116/90", "name": "Opasok <x>", "url": "https://www.forestshop.sk/opasok/"},
        {"code": "60109", "name": "Ponožky", "url": ""},          # no url → plain text, no <a>
    ]
    subj, html = N.build_alternative_email("Bob", "Nohavice", alts)
    assert subj == N.ALTERNATIVE_SUBJECT
    assert 'href="https://www.forestshop.sk/opasok/"' in html
    assert "Opasok &lt;x&gt;" in html                     # alt name escaped
    assert "<li>Ponožky</li>" in html                     # url-less alt = plain text
    assert "alternatívne produkty" in html


def test_build_alternative_email_no_alts_offers_help():
    _subj, html = N.build_alternative_email("Bob", "Nohavice", [])
    assert "nájsť vhodnú alternatívu" in html
    assert "<ul>" not in html
