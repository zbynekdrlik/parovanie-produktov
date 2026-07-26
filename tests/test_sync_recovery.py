"""PR #265 review — the hourly Shoptet sync must survive the documented supplier
onboarding, and the two writers of orders_cache.csv must not share a temp file.

`PRODUCTS` is read ONCE, at import, and `resync_current` only ever mutates it in place —
so `len(PRODUCTS)` is pinned at the boot count for the process lifetime. The documented
`scripts/add_supplier_review_data.py` appends a whole new supplier to the live
review_data.json WHILE the app runs. The hourly sync then tries to write the boot-time
list back over the bigger file: the #261 guard correctly refuses the clobber (the old
code silently discarded the new supplier), but nothing ever re-read the file, so the
automation went red EVERY hour until someone restarted the service by hand.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

EXPORT_HEADER = ("code;pairCode;name;supplier;productVisibility;availabilityInStock;"
                 "availabilityOutOfStock;price;standardPrice;stock;defaultImage\r\n")


def _export_row(code, name, supplier):
    return (f"{code};;{name};{supplier};visible;Skladom;Vypredané;10;12;3;"
            "https://cdn.test/x.jpg\r\n")


def _product(key, name, supplier, code):
    return {"key": key, "idx": 0, "supplier": supplier, "name": name, "pairCode": "",
            "variant_codes": [code], "our_url": "", "our_images": [],
            "ai_status": "unmatched", "candidates": [], "current": {}}


@pytest.fixture
def sync_env(tmp_path, monkeypatch):
    """The app pointed at a throwaway data dir, with every Shoptet fetch stubbed."""
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    monkeypatch.setattr(webapp, "SRC", str(tmp_path / "products.csv"))
    export = (EXPORT_HEADER
              + _export_row("A1", "Bunda A", "BETALOV")
              + _export_row("B1", "Bunda B", "BETALOV")
              + _export_row("C1", "Bunda C", "ORBIS"))
    monkeypatch.setattr(webapp, "_fetch_orders_csv", lambda: b"code;date\r\n")
    monkeypatch.setattr(webapp, "_fetch_export_csv", lambda: export.encode("cp1250"))
    monkeypatch.setattr(webapp, "_fetch_customers_csv", lambda: b"email\r\n")
    monkeypatch.setattr(webapp.config, "SUPPLIERS", ["BETALOV", "ORBIS"])
    return tmp_path


def test_the_hourly_sync_picks_up_a_supplier_appended_while_the_app_runs(sync_env):
    """The documented onboarding flow, end to end: the app booted with one supplier, the
    script appended a second, the sync runs. It must succeed AND serve the new supplier
    without a restart — not abort every hour with a browser-shaped error message."""
    booted = [_product("BETALOV|A1", "Bunda A", "BETALOV", "A1")]
    webapp.PRODUCTS = booted
    webapp._note_store_read(webapp.DATA, booted)          # the receipt the boot records
    on_disk = booted + [_product("ORBIS|C1", "Bunda C", "ORBIS", "C1")]
    (sync_env / "review_data.json").write_text(
        json.dumps(on_disk, ensure_ascii=False), encoding="utf-8")

    result = webapp.run_shoptet_sync()

    assert result["catalog_products"] >= 1
    assert len(webapp.PRODUCTS) == 2, "the appended supplier never reached the app"
    stored = json.loads((sync_env / "review_data.json").read_text(encoding="utf-8"))
    assert len(stored) == 2, "the sync wrote the stale boot-time list back"
    assert {p["supplier"] for p in stored} == {"BETALOV", "ORBIS"}


def test_the_sync_still_resyncs_when_nothing_changed_underneath(sync_env):
    """The ordinary hourly run: same file, prices refreshed from the fresh export."""
    products = [_product("BETALOV|A1", "Bunda A", "BETALOV", "A1")]
    webapp.PRODUCTS = products
    webapp._note_store_read(webapp.DATA, products)
    (sync_env / "review_data.json").write_text(
        json.dumps(products, ensure_ascii=False), encoding="utf-8")

    webapp.run_shoptet_sync()

    stored = json.loads((sync_env / "review_data.json").read_text(encoding="utf-8"))
    assert len(stored) == 1
    assert stored[0]["current"].get("price") == "10", "the export price was not resynced"


def test_a_missing_review_file_never_blanks_the_products_in_memory(sync_env):
    """review_data.json vanished (a botched restore) — the sync must keep serving what
    it has rather than adopt an empty list."""
    products = [_product("BETALOV|A1", "Bunda A", "BETALOV", "A1")]
    webapp.PRODUCTS = products
    webapp._note_store_read(webapp.DATA, products)

    webapp.run_shoptet_sync()

    assert len(webapp.PRODUCTS) == 1


def test_two_writers_of_the_orders_cache_never_share_a_temp_file(tmp_path, monkeypatch):
    """`orders_cache.csv` is written by a request thread (stale 30-min cache) and by the
    hourly sync thread. With a pid-derived temp name both build the SAME path, so one
    renames the inode the other is still writing into place — and the loser keeps
    appending into the LIVE cache before its own replace fails."""
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(os.fspath(src))
        real_replace(src, dst)

    monkeypatch.setattr(webapp.os, "replace", spy)
    p = str(tmp_path / "orders_cache.csv")
    webapp._atomic_write_bytes(p, b"first")
    webapp._atomic_write_bytes(p, b"second")

    assert len(set(seen)) == 2, f"both writers built the same temp path: {seen}"
    with open(p, "rb") as f:
        assert f.read() == b"second"
