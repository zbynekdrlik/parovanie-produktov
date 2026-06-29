"""E2E harness: boot webreview/app.py against a fixture data dir on a free port,
drive it with a real Chromium via pytest-playwright."""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _fixture_products(base: str) -> list:
    """One matched (AI-paired) product so the '✓ Dobré' flow is exercisable. The
    supplier URL points back at the local server (a 204 favicon) so the lazy
    image fetch stays hermetic — no outbound network in CI."""
    img_url = f"{base}/favicon.ico"
    return [
        {
            "key": "BETALOV|p1", "idx": 0, "supplier": "BETALOV",
            "name": "Bunda Test ALFA", "pairCode": "P1",
            "variant_codes": ["1/M", "1/L"], "our_url": "", "our_images": [],
            "ai_status": "matched", "ai_chosen_url": img_url, "ai_reason": "kód sedí",
            "candidates": [{"name": "Bunda ALFA", "url": img_url}],
            "current": {"state": 1, "price": "99", "std": "", "stock": "3",
                        "avail": "Skladom"},
        },
    ]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_ready(url: str, proc: subprocess.Popen, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"webreview exited early (rc={proc.returncode})")
        try:
            urllib.request.urlopen(url, timeout=1)  # noqa: S310 — localhost only
            return
        except OSError:
            time.sleep(0.3)
    raise RuntimeError("webreview did not become ready in time")


@pytest.fixture(scope="session")
def live_server(tmp_path_factory):
    out = tmp_path_factory.mktemp("wr_out")
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    (out / "review_data.json").write_text(
        json.dumps(_fixture_products(base), ensure_ascii=False), encoding="utf-8")
    # Fresh orders cache so /api/orders serves it (no live forestshop fetch in CI).
    # Order codes are chronological (lower = older). 1/M maps to the fixture product
    # (pairable); 2/M and 77/X are NOT in the review set → unpaired (inline-pairing
    # field). Crafted so the OLDEST-first sort is observable: ORBIS holds the single
    # oldest order (20260700) so its group sorts ABOVE BETALOV (oldest 20260750),
    # beating the old alphabetical order; within BETALOV 2/M (20260750) precedes 1/M.
    (out / "orders_cache.csv").write_text(
        "code;date;statusName;itemName;itemAmount;itemCode;itemVariantName;itemSupplier\r\n"
        "20260900;2026-05-20 09:00:00;Vybavuje sa;Bunda Test ALFA;2;1/M;Veľkosť: M;BETALOV\r\n"
        "20260750;2026-05-02 11:30:00;Vybavuje sa;Ciapka Test;1;2/M;Veľkosť: M;BETALOV\r\n"
        "20260700;2026-04-24 19:14:05;Vybavuje sa;Rukavice Test;1;77/X;Veľkosť: X;ORBIS\r\n",
        encoding="cp1250")
    env = {
        **os.environ,
        "WEBREVIEW_OUT": str(out),
        "WEBREVIEW_PORT": str(port),
        "PYTHONPATH": os.path.join(ROOT, "src"),
    }
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "webreview", "app.py")], env=env)
    try:
        _wait_ready(base + "/api/version", proc)
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
