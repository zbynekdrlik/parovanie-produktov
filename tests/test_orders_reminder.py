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
        "20261000;2026-07-10 10:00:00;Vybavuje sa;;a@x.sk;+421900;Ján Vzor;Bunda;1;99,90",
        # >4d, with note → AI candidate
        "20261001;2026-07-12 09:00:00;Vybavuje sa;volať zákazníka;b@x.sk;;Eva Nová;Nohavice;2;50,00",
        # too fresh (<4d) → excluded
        "20261002;2026-07-21 09:00:00;Vybavuje sa;nemáme;c@x.sk;;Fero Mladý;Čiapka;1;12,00",
        # wrong status → excluded
        "20261003;2026-07-01 09:00:00;Vybavená;;d@x.sk;;Hotový Klient;Nôž;1;30,00",
    ])
    sel = ordrem.select_orders(csv, now=NOW)
    codes = {o["code"] for o in sel}
    assert codes == {"20261000", "20261001"}
    by = {o["code"]: o for o in sel}
    assert by["20261000"]["has_note"] is False
    assert by["20261001"]["has_note"] is True
    assert by["20261001"]["shopRemark"] == "volať zákazníka"
    assert by["20261000"]["days"] == 11    # 2026-07-10 10:00 → 2026-07-22 08:00 = 11 full days
    assert by["20261000"]["admin_link"].endswith("string=20261000&src=orders")


def test_dedup_first_row_per_code_wins():
    csv = _csv([
        "20261010;2026-07-10 10:00:00;Vybavuje sa;prvá poznámka;a@x.sk;;Ján Vzor;Bunda;1;99,90",
        "20261010;2026-07-10 10:00:00;Vybavuje sa;druhý riadok;a@x.sk;;Ján Vzor;Čiapka;1;12,00",
    ])
    sel = ordrem.select_orders(csv, now=NOW)
    assert len(sel) == 1
    assert sel[0]["shopRemark"] == "prvá poznámka"
    assert sel[0]["itemName"] == "Bunda"


def test_exactly_4_days_is_not_yet_included():
    # (now - date) == 4d exactly → n8n „before now-4d" is False → excluded
    csv = _csv(["20261020;2026-07-18 08:00:00;Vybavuje sa;;a@x.sk;;Ján;Bunda;1;9,90"])
    assert ordrem.select_orders(csv, now=NOW) == []


def test_unparseable_date_is_skipped():
    csv = _csv(["20261030;neplatný dátum;Vybavuje sa;;a@x.sk;;Ján;Bunda;1;9,90"])
    assert ordrem.select_orders(csv, now=NOW) == []


def test_whitespace_only_note_is_no_note():
    csv = _csv(["20261040;2026-07-10 10:00:00;Vybavuje sa;   ;a@x.sk;;Ján;Bunda;1;9,90"])
    (o,) = ordrem.select_orders(csv, now=NOW)
    assert o["has_note"] is False


def test_accepts_cp1250_bytes():
    csv = _csv(["20261050;2026-07-10 10:00:00;Vybavuje sa;nemáme;a@x.sk;;Žofia Ďurková;Bunda;1;9,90"])
    (o,) = ordrem.select_orders(csv.encode("cp1250"), now=NOW)
    assert o["billFullName"] == "Žofia Ďurková"


# ── build_reminder_email ───────────────────────────────────────────────────────
def test_reminder_email_subject_and_body():
    subj, html = ordrem.build_reminder_email("Ján Vzor", "20261000")
    assert subj == "📦 Stav vašej objednávky z Forestshop.sk"
    assert "Ján Vzor" in html
    assert "20261000" in html
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
