"""PROBE — the ONE behavioural difference between df88c43 (`isinstance` → dict(done) fallback)
and the uncommitted `st.get("orders") or {}`: the final save finds NO `orders` key.
Temporary; delete after review.
"""
import json
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402
from parovanie import orders_reminder  # noqa: E402

TODAY = date.today()
OLD = (TODAY - timedelta(days=10)).isoformat()
HEADER = ("code;date;statusName;shopRemark;email;phone;billFullName;"
          "itemName;itemAmount;totalPriceWithVat")
ORDERS_CSV = ("\r\n".join([
    HEADER,
    f"20261001;{OLD} 09:00:00;Vybavuje sa;volať zákazníka;b@x.sk;;Eva Nová;Nohavice;2;50,00",
    f"20261002;{OLD} 08:00:00;Vybavuje sa;volané so zákazníkom, počká;c@x.sk;;Iva Stará;Čiapka;1;12,00",
]) + "\r\n").encode("cp1250")
_CLASSIFY = {"volať zákazníka": False, "volané so zákazníkom, počká": True}
VER = os.environ.get("PROBE_VER", "?")


@pytest.fixture
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp.RUNNER, "state_path", str(tmp_path / "automations.json"))
    monkeypatch.setattr(webapp, "ORDERS_REMINDER_STATE", str(tmp_path / "orders_reminder.json"))
    monkeypatch.setattr(webapp, "_orders_csv_cached", lambda: ORDERS_CSV)
    monkeypatch.setattr(webapp, "_classify_contacted", lambda note: _CLASSIFY[note])
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("MAIL_BCC", "owner@example.com")
    sent = []
    monkeypatch.setattr(webapp, "_send_mail_html",
                        lambda to, s, b, bcc=None, **kw: sent.append({"to": to}) or True)
    if hasattr(webapp, "_quarantined"):
        webapp._quarantined.clear()
    yield {"tmp": tmp_path, "sent": sent}
    if hasattr(webapp, "_quarantined"):
        webapp._quarantined.clear()


def _p(iso):
    return iso["tmp"] / "orders_reminder.json"


def _orders(iso):
    return sorted(json.loads(_p(iso).read_text()).get("orders") or {})


def test_absent_orders_map_at_final_save(iso, monkeypatch):
    """Run 1 records both orders. Run 2 processes NOTHING new (both terminal → fast path), and
    the store file vanishes between the start-of-run read and the final save. No `_persist_done`
    runs afterwards, so nothing recreates the `orders` key."""
    webapp.run_orders_reminder()
    before = _orders(iso)
    iso["sent"].clear()

    real_codes = orders_reminder.all_order_codes

    def kill_then(csv_bytes):                 # called AFTER the store read, BEFORE the final save
        _p(iso).unlink(missing_ok=True)
        return real_codes(csv_bytes)
    monkeypatch.setattr(orders_reminder, "all_order_codes", kill_then)
    monkeypatch.setattr(webapp.orders_reminder, "all_order_codes", kill_then, raising=False)

    webapp.run_orders_reminder()
    after = _orders(iso)

    # what a NORMAL next run then does
    iso["sent"].clear()
    webapp.run_orders_reminder()
    print(f"\n[{VER}] absent-map at final save -> before: {before} -> after: {after} "
          f"| NEXT RUN re-mails: {[m['to'] for m in iso['sent']]}")
