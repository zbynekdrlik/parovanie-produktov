"""Pure-logic tests for the „Pripomienky objednávok" automation (#105).

No network / no SMTP / no OpenAI — select_orders over a fixture CSV, the reminder-email builder,
and the classifier prompt + reply parser (mirroring the n8n „Kontaktovany?" node).
"""
from datetime import datetime

import pytest

from parovanie import orders_reminder as ordrem

NOW = datetime(2026, 7, 22, 8, 0, 0)


def _csv(rows: list[str]) -> str:
    header = ("code;date;statusName;shopRemark;email;phone;billFullName;"
              "itemName;itemAmount;totalPriceWithVat")
    return "\r\n".join([header, *rows]) + "\r\n"


# ── select_orders ──────────────────────────────────────────────────────────────
def test_selects_vybavuje_sa_older_than_4_days():
    csv = _csv([
        # >4d, no note → red candidate
        "99001000;2026-07-10 10:00:00;Vybavuje sa;;a@x.sk;+421900;Ján Vzor;Bunda;1;99,90",
        # >4d, with note → AI candidate
        "99001001;2026-07-12 09:00:00;Vybavuje sa;volať zákazníka;b@x.sk;;Eva Nová;Nohavice;2;50,00",
        # too fresh (<4d) → excluded
        "99001002;2026-07-21 09:00:00;Vybavuje sa;nemáme;c@x.sk;;Fero Mladý;Čiapka;1;12,00",
        # wrong status → excluded
        "99001003;2026-07-01 09:00:00;Vybavená;;d@x.sk;;Hotový Klient;Nôž;1;30,00",
    ])
    sel = ordrem.select_orders(csv, now=NOW)
    codes = {o["code"] for o in sel}
    assert codes == {"99001000", "99001001"}
    by = {o["code"]: o for o in sel}
    assert by["99001000"]["has_note"] is False
    assert by["99001001"]["has_note"] is True
    assert by["99001001"]["shopRemark"] == "volať zákazníka"
    assert by["99001000"]["days"] == 11    # 2026-07-10 10:00 → 2026-07-22 08:00 = 11 full days
    assert by["99001000"]["admin_link"].endswith("string=99001000&src=orders")


def test_dedup_first_row_per_code_wins():
    csv = _csv([
        "99001010;2026-07-10 10:00:00;Vybavuje sa;prvá poznámka;a@x.sk;;Ján Vzor;Bunda;1;99,90",
        "99001010;2026-07-10 10:00:00;Vybavuje sa;druhý riadok;a@x.sk;;Ján Vzor;Čiapka;1;12,00",
    ])
    sel = ordrem.select_orders(csv, now=NOW)
    assert len(sel) == 1
    assert sel[0]["shopRemark"] == "prvá poznámka"
    assert sel[0]["itemName"] == "Bunda"


def test_exactly_4_days_is_not_yet_included():
    # (now - date) == 4d exactly → n8n „before now-4d" is False → excluded
    csv = _csv(["99001020;2026-07-18 08:00:00;Vybavuje sa;;a@x.sk;;Ján;Bunda;1;9,90"])
    assert ordrem.select_orders(csv, now=NOW) == []


def test_unparseable_date_is_skipped():
    csv = _csv(["99001030;neplatný dátum;Vybavuje sa;;a@x.sk;;Ján;Bunda;1;9,90"])
    assert ordrem.select_orders(csv, now=NOW) == []


def test_whitespace_only_note_is_no_note():
    csv = _csv(["99001040;2026-07-10 10:00:00;Vybavuje sa;   ;a@x.sk;;Ján;Bunda;1;9,90"])
    (o,) = ordrem.select_orders(csv, now=NOW)
    assert o["has_note"] is False


def test_accepts_cp1250_bytes():
    csv = _csv(["99001050;2026-07-10 10:00:00;Vybavuje sa;nemáme;a@x.sk;;Žofia Ďurková;Bunda;1;9,90"])
    (o,) = ordrem.select_orders(csv.encode("cp1250"), now=NOW)
    assert o["billFullName"] == "Žofia Ďurková"


# ── build_reminder_email ───────────────────────────────────────────────────────
def test_reminder_email_subject_and_body():
    subj, html = ordrem.build_reminder_email("Ján Vzor", "99001000")
    assert subj == "📦 Stav vašej objednávky z Forestshop.sk"
    assert "Ján Vzor" in html
    assert "99001000" in html
    assert "eshop@forestshop.sk" in html
    assert html.lstrip().startswith("<!DOCTYPE html>")


def test_reminder_email_escapes_free_text():
    _, html = ordrem.build_reminder_email('<script>x</script>', "1&2")
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html
    assert "1&amp;2" in html


# ── classifier ─────────────────────────────────────────────────────────────────
def test_classifier_messages_carry_note_and_rules():
    msgs = ordrem.build_classifier_messages("volané so zákazníkom, počká")
    assert msgs[0]["role"] == "system"
    assert "volané" in msgs[0]["content"] and "budeme volať" in msgs[0]["content"]
    assert "volané so zákazníkom, počká" in msgs[1]["content"]


def test_classifier_messages_empty_note_becomes_bez_poznamky():
    msgs = ordrem.build_classifier_messages("   ")
    assert "BEZ POZNAMKY" in msgs[1]["content"]


@pytest.mark.parametrize("content,expected", [
    ('{"kategoria": "kontaktovany"}', True),
    ('{"kategoria": "nekontaktovany"}', False),
    ('```json\n{"kategoria": "kontaktovany"}\n```', True),
    ('{"category": "nekontaktovany"}', False),
    ('"kontaktovany"', True),
    ('nekontaktovany', False),
])
def test_parse_classification(content, expected):
    assert ordrem.parse_classification(content) is expected


@pytest.mark.parametrize("bad", ['{"kategoria": "možno"}', "úplný nezmysel", "{}", ""])
def test_parse_classification_rejects_junk(bad):
    with pytest.raises(ValueError):
        ordrem.parse_classification(bad)


# ── incremental processing (#153) ────────────────────────────────────────────────
def _order(code, date="2026-07-10 10:00:00", note="volať zákazníka"):
    return {"code": code, "date": date, "days": 12, "shopRemark": note, "has_note": bool(note),
            "email": "a@x.sk", "phone": "", "billFullName": "Ján Vzor", "itemName": "Bunda",
            "itemAmount": "1", "totalPriceWithVat": "9,90", "admin_link": "https://x/" + code}


def test_fingerprint_ignores_days_but_not_note():
    a = _order("1", date="2026-07-10 10:00:00", note="volať")
    b = dict(a, days=99)                       # only 'days' differs — days is derived, not stable
    assert ordrem.order_fingerprint(a) == ordrem.order_fingerprint(b)
    c = dict(a, shopRemark="iná poznámka")
    assert ordrem.order_fingerprint(a) != ordrem.order_fingerprint(c)


def test_partition_unchanged_only_when_terminal_and_same_fingerprint():
    o1 = _order("1")   # already terminal, unchanged fingerprint → unchanged
    o2 = _order("2")   # already terminal, but note CHANGED → to_process (re-evaluate)
    prev_fp = {"1": ordrem.order_fingerprint(o1), "2": "old-fingerprint-does-not-match"}
    done_codes = {"1", "2"}
    to_process, unchanged, fp = ordrem.partition_incremental([o1, o2], prev_fp, done_codes)
    assert [o["code"] for o in unchanged] == ["1"]
    assert [o["code"] for o in to_process] == ["2"]
    assert fp == {"1": ordrem.order_fingerprint(o1), "2": ordrem.order_fingerprint(o2)}


def test_partition_never_skips_a_newly_eligible_order():
    # a code with NO prior fingerprint at all (just crossed the 4-day threshold today) —
    # even if by coincidence its fingerprint matched something, it isn't in done_codes yet.
    o = _order("new")
    to_process, unchanged, fp = ordrem.partition_incremental([o], {}, set())
    assert [x["code"] for x in to_process] == ["new"]
    assert unchanged == []


def test_partition_retries_not_yet_terminal_even_if_unchanged():
    # code seen before (fingerprint matches) but NEVER reached done (e.g. OPENAI_API_KEY was
    # missing last run) — must be retried, not silently treated as 'unchanged'.
    o = _order("pending")
    prev_fp = {"pending": ordrem.order_fingerprint(o)}
    to_process, unchanged, fp = ordrem.partition_incremental([o], prev_fp, set())
    assert [x["code"] for x in to_process] == ["pending"]
    assert unchanged == []


# ── #220 — the dedup store must stay bounded, and must never lose a live record ────
def _rec(date_str, status="emailed"):
    return {"status": status, "date": date_str, "email": "x@y.sk"}


def test_prune_keeps_every_code_still_in_the_source_window():
    """The one invariant that must never bend: a code the export still carries can come back
    round as „Vybavuje sa" at any moment, so dropping its dedup record means the customer gets a
    SECOND reminder. Age is irrelevant for those."""
    done = {"IN": _rec("2019-01-01T08:00:00+02:00"), "OUT": _rec("2019-01-01T08:00:00+02:00")}
    kept, dropped = ordrem.prune_done(done, {"IN"}, now=datetime(2026, 7, 25))
    assert set(kept) == {"IN"}
    assert dropped == ["OUT"]                          # …and the drop is reported, never silent


def test_prune_keeps_recent_records_that_left_the_window():
    """A grace period after an order leaves the export — a partial/short export must not
    instantly forget that the customer was already mailed."""
    done = {"OLD": _rec("2019-01-01T08:00:00+02:00"),
            "RECENT": _rec("2026-07-01T08:00:00+02:00")}
    kept, _ = ordrem.prune_done(done, {"SOMETHING-ELSE"}, now=datetime(2026, 7, 25),
                                retention_days=180)
    assert set(kept) == {"RECENT"}


def test_prune_never_drops_a_dated_record_on_COUNT_alone():
    """A count cap on dated records is what would re-mail a customer: a truncated export makes
    the window look small, so records for orders that are very much still live fall „outside"
    it — and a cap would then drop them purely because there are many. Retention is the only
    criterion, and it is twice the 90-day export window, so a droppable record cannot belong to
    an order still in the export. (PR #224 adversarial review, reproduced.)"""
    done = {f"C{i}": _rec(f"2026-07-{i:02d}T08:00:00+02:00") for i in range(1, 11)}
    kept, dropped = ordrem.prune_done(done, {"IRRELEVANT"}, now=datetime(2026, 7, 25),
                                      retention_days=180, max_undated=3)
    assert set(kept) == set(done)                      # all ten are inside retention → all stay
    assert dropped == []


def test_prune_caps_records_it_cannot_date_at_all():
    """Undated records (a partial write) can never age out, so they are the one thing a count
    cap must bound — otherwise the store still grows forever."""
    done = {f"U{i}": {"status": "emailed"} for i in range(10)}
    kept, dropped = ordrem.prune_done(done, {"IRRELEVANT"}, now=datetime(2026, 7, 25),
                                      max_undated=3)
    assert len(kept) == 3 and len(dropped) == 7


def test_prune_does_nothing_when_the_window_is_unknown():
    """Fail-closed: an empty/unreadable export gives an EMPTY window set, and pruning against it
    would drop records for orders that are very much still live. Same shape as the fail-closed
    supplier upload — no source of truth, no destructive action."""
    done = {"A": _rec("2019-01-01T08:00:00+02:00")}
    assert ordrem.prune_done(done, set(), now=datetime(2026, 7, 25)) == (done, [])


def test_prune_survives_garbage_records():
    done = {"IN": "not-a-dict", "OUT": {"status": "emailed"}, "TRANSIT": {"status": "sending",
            "claimed_at": "2026-07-25T08:00:00+02:00"}}
    kept, _ = ordrem.prune_done(done, {"IN"}, now=datetime(2026, 7, 25))
    assert kept["IN"] == "not-a-dict"                   # window codes are kept verbatim
    assert "TRANSIT" in kept                            # a fresh claim is not garbage-collected


def test_all_order_codes_returns_every_code_in_the_export():
    """The window set is EVERY code in the export — not just the ones select_orders picks.
    A „Vybavená" order can be reopened to „Vybavuje sa" tomorrow; its record must survive."""
    csv_text = ("code;date;statusName;shopRemark;email;phone;billFullName;itemName\r\n"
                "111;2026-07-01 10:00:00;Vybavuje sa;;a@x.sk;;A;X\r\n"
                "111;2026-07-01 10:00:00;Vybavuje sa;;a@x.sk;;A;Y\r\n"
                "222;2026-07-02 10:00:00;Vybavená;;b@x.sk;;B;X\r\n")
    assert ordrem.all_order_codes(csv_text.encode("cp1250")) == {"111", "222"}


def test_prune_at_production_defaults_never_drops_a_live_record_at_scale():
    """The shipped defaults (180 d / undated-only cap) are what actually runs, but the live store
    holds ~24 records today, so pruning is a no-op there and a regression would stay invisible
    for months. Drive the REAL defaults with a store far bigger than any cap: every code the
    export still carries must survive, however ancient its record claims to be."""
    now = datetime(2026, 7, 25, 9, 0, 0)
    rows = ["code;date;statusName;shopRemark;email;phone;billFullName;itemName"]
    live = [f"2026{i:04d}" for i in range(900)]          # far above any count cap
    for c in live:
        rows.append(f"{c};2026-05-20 08:00:00;Vybavuje sa;nota;x@y.sk;;Meno;Vec")
    raw = ("\r\n".join(rows) + "\r\n").encode("cp1250")

    done = {c: _rec("2019-01-01T08:00:00+02:00") for c in live}      # all ancient on paper
    done.update({f"GONE{i}": _rec("2019-01-01T08:00:00+02:00") for i in range(50)})
    kept, dropped = ordrem.prune_done(done, ordrem.all_order_codes(raw), now=now)

    assert set(live) <= set(kept)                        # not one live record lost
    assert set(dropped) == {f"GONE{i}" for i in range(50)}
    # …and the orders the run would actually act on are all still protected
    assert {o["code"] for o in ordrem.select_orders(raw, now=now)} <= set(kept)


def test_prune_never_drops_a_dated_record_on_count_at_ANY_scale():
    """The count-cap ban has to hold for a cap of ANY size, not just one that reuses
    `max_undated`. test_prune_never_drops_a_dated_record_on_COUNT_alone drives ten records
    against max_undated=3, and the at-scale test's 900 records are all still INSIDE the window —
    a re-introduced cap with its own constant (a hard 500, say) slips past both, and every
    record it drops for a live order costs a duplicate customer mail. This one bites regardless
    of the constant chosen: thousands of records, all dated well inside retention, none of them
    in the window. (PR #224 adversarial review.)"""
    done = {f"C{i}": _rec("2026-04-27T08:00:00+02:00") for i in range(5000)}
    kept, dropped = ordrem.prune_done(done, {"ONE-SURVIVING-CODE"}, now=datetime(2026, 7, 25))
    assert dropped == []
    assert len(kept) == 5000
