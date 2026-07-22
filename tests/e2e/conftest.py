"""E2E harness: boot webreview/app.py against a fixture data dir on a free port,
drive it with a real Chromium via pytest-playwright.

Auth (#91): the whole app sits behind /login. Every fixture server bootstraps the
same admin (env ADMIN_EMAIL/ADMIN_PW) with a SHARED SECRET_KEY, and an autouse
fixture pre-seeds the browser context with a real session cookie — so all the
pre-auth E2E flows keep running as a logged-in manager. The auth E2E itself opts
out with @pytest.mark.anonymous."""
import csv
import http.cookiejar
import io
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

E2E_ADMIN = "admin@e2e.test"
E2E_ADMIN_PW = "e2e-tajne-heslo-123"
# One shared signing key across the fixture servers: a session cookie minted by
# any of them validates on all (127.0.0.1 cookies are port-agnostic anyway).
# AUTH_COOKIE_SECURE pinned off: fixture servers speak plain http://127.0.0.1, so a
# Secure session cookie would never round-trip → the login POST loses its CSRF session
# → 400 (only bites a dev box that HAS a real data/.auth_env with AUTH_COOKIE_SECURE=1;
# CI has no data/ so it was already off there). Pinning it keeps local E2E deterministic.
_AUTH_ENV = {"ADMIN_EMAIL": E2E_ADMIN, "ADMIN_PW": E2E_ADMIN_PW,
             "SECRET_KEY": "e2e-secret-key", "AUTH_COOKIE_SECURE": "0"}

_COOKIE_CACHE = {}


def _admin_session_cookie(base: str) -> str:
    """Real form login via urllib (GET /login primes the CSRF token, POST logs in)
    → the Flask session cookie value. Cached per server (live_server is
    session-scoped, so one login serves the whole run)."""
    if base in _COOKIE_CACHE:
        return _COOKIE_CACHE[base]
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    html = opener.open(base + "/login", timeout=10).read().decode()
    csrf = re.search(r'name="_csrf" value="([^"]+)"', html).group(1)
    data = urllib.parse.urlencode({
        "email": E2E_ADMIN, "password": E2E_ADMIN_PW, "_csrf": csrf}).encode()
    opener.open(base + "/login", data=data, timeout=10)
    value = next(c.value for c in jar if c.name == "session")
    _COOKIE_CACHE[base] = value
    return value


_SERVER_FIXTURES = ("live_server", "matched_server",
                    "longcontent_matched_server", "search_server",
                    "automations_server")


@pytest.fixture(autouse=True)
def _authenticated_context(request):
    """Pre-authenticate the browser context against every fixture server the test
    uses. @pytest.mark.anonymous (the auth E2E itself) starts logged out."""
    if request.node.get_closest_marker("anonymous"):
        return
    bases = [request.getfixturevalue(n) for n in _SERVER_FIXTURES
             if n in request.fixturenames]
    if not bases:
        return
    context = request.getfixturevalue("context")
    for base in bases:
        context.add_cookies([{"name": "session",
                              "value": _admin_session_cookie(base),
                              "url": base}])


@pytest.fixture(scope="session")
def admin_creds():
    return E2E_ADMIN, E2E_ADMIN_PW


@pytest.fixture(scope="session")
def admin_api():
    """POST a JSON payload to an /api/* endpoint as the bootstrap admin (urllib —
    no browser). For E2E setup/teardown of extra user accounts."""
    def call(base, path, payload):
        req = urllib.request.Request(
            base + path, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Cookie": "session=" + _admin_session_cookie(base)})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    return call


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
    # field). Crafted so the NEWEST-first sort is observable: BETALOV holds the newest
    # order (1/M = 20260900) so its group sorts ABOVE ORBIS (newest 20260700); within
    # BETALOV 1/M (20260900) precedes 2/M (20260750).
    (out / "orders_cache.csv").write_text(
        "code;date;statusName;itemName;itemAmount;itemCode;itemVariantName;itemSupplier\r\n"
        "20260900;2026-05-20 09:00:00;Vybavuje sa;Bunda Test ALFA;2;1/M;Veľkosť: M;BETALOV\r\n"
        "20260750;2026-05-02 11:30:00;Vybavuje sa;Ciapka Test;1;2/M;Veľkosť: M;BETALOV\r\n"
        "20260700;2026-04-24 19:14:05;Vybavuje sa;Rukavice Test;1;77/X;Veľkosť: X;ORBIS\r\n"
        # 88/Z arrived WITHOUT a supplier (empty itemSupplier) → groups under '—' and
        # shows the inline supplier-assign field; OLDEST order (20260001) so '—' sorts
        # LAST and never disturbs the BETALOV-first / within-BETALOV ordering assertions.
        "20260001;2026-01-05 10:00:00;Vybavuje sa;Bez Dodavatela Test;1;88/Z;Veľkosť: Z;\r\n",
        encoding="cp1250")
    # GRUBE per-size code store: attaches a copyable itemId chip + .de link onto the
    # 1/M order row (its itemCode matches), exercising the Task-10 renderOrderRow path.
    # Keyed by the BETALOV 1/M row so it never adds/removes a row or changes a group
    # count → the existing to-order assertions are untouched.
    (out / "grube_codes.json").write_text(
        json.dumps({"1/M": {"itemId": "1547734519", "size": "M",
                            "deUrl": "https://www.grube.de/p/x/154773/",
                            "productId": "154773"}}, ensure_ascii=False),
        encoding="utf-8")
    env = {
        **os.environ,
        **_AUTH_ENV,
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


@pytest.fixture(scope="function")
def automations_server(tmp_path_factory):
    """Isolated webreview instance for the automations tab E2E (#93).

    Seeded so the tab has content WITHOUT any network: a pre-existing
    posta_uncollected.json (one uncollected shipment + one invalid-format
    package from an earlier 'run'), NO automations.json (→ the runner must
    default to DISABLED = Zastavené), and a FRESH orders_cache.csv whose only
    row has NO packageNumber → a manual 'Spustiť teraz' run finds 0 shipments,
    calls no Pošta API and sends no mail (hermetic green run). Shoptet creds
    are pointed at a nonexistent file so no code path can reach the live shop."""
    out = tmp_path_factory.mktemp("wr_auto_out")
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    (out / "posta_uncollected.json").write_text(json.dumps({
        "escalation": {"2026100": "2|2026-07-18"},
        "last_check": "2026-07-21T09:00:05+02:00",
        "uncollected": [{
            "orderCode": "2026100", "packageNumber": "EF000000002SK",
            "name": "Ján Vzor", "phone": "+421900111222", "email": "jan@example.com",
            "office_name": "Skalica 1", "office_addr": "Potočná 24, 90901 Skalica",
            "retained_till": "2026-08-03", "notified_since": "2026-07-16",
            "days_at_post": 5, "count": 2, "last_sent": "2026-07-18",
            "call_needed": False,
            "tracking_link": "https://www.posta.sk/sledovanie-zasielok#parcel=EF000000002SK",
            "admin_link": "https://www.forestshop.sk/admin/vyhladavanie/?string=2026100&src=orders",
        }],
        "invalid": [{
            "orderCode": "2026101", "packageNumber": "06565700348274",
            "name": "Eva Testová",
            "admin_link": "https://www.forestshop.sk/admin/vyhladavanie/?string=2026101&src=orders",
        }],
        "errors": [],
        "stats": {"checked": 2, "uncollected": 1, "invalid": 1, "errors": 0,
                  "emails_sent": 0, "emails_failed": 0},
    }, ensure_ascii=False), encoding="utf-8")
    (out / "orders_cache.csv").write_text(
        "code;date;statusName;email;phone;billFullName;packageNumber;itemCode\r\n"
        "2026200;2026-07-20 10:00:00;Vybavuje sa;x@example.com;;Bez Balíka;;9/M\r\n",
        encoding="cp1250")
    env = {
        **os.environ,
        **_AUTH_ENV,
        "WEBREVIEW_OUT": str(out),
        "WEBREVIEW_PORT": str(port),
        "PYTHONPATH": os.path.join(ROOT, "src"),
        "SHOPTET_CRED": str(out / "no_creds_here"),   # hermetic: no live-shop access
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


@pytest.fixture(scope="function")
def matched_server(tmp_path_factory):
    """Isolated webreview instance holding ONE undecided matched product, for the
    matched-card 3-button E2E. Function-scoped + its own out-dir so the decisions this
    test writes (unavailable/discontinued, each undone) can never leak into — nor be
    perturbed by — the shared session `live_server` (whose BETALOV|p1 is left decided
    'good' by test_approve_match). Reuses `_fixture_products` for the matched product."""
    out = tmp_path_factory.mktemp("wr_matched_out")
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    (out / "review_data.json").write_text(
        json.dumps(_fixture_products(base), ensure_ascii=False), encoding="utf-8")
    env = {
        **os.environ,
        **_AUTH_ENV,
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


def _longcontent_fixture_products(base: str) -> list:
    """One undecided matched product with a REALISTIC LONG name/candidate/URL — like a
    real supplier product page, not the short 'Bunda ALFA' smoke-test fixture. Needed to
    reproduce #82: with short content the manual-URL/candidate rows already fit a narrow
    viewport (nothing to shrink), so the clipping bug never triggers. Long unbreakable
    content is what forces the .card grid track's automatic min-content width past the
    viewport, so the row (and its green button) overflows into the area .card clips via
    overflow:hidden — exactly what the manager sees on his phone."""
    img_url = f"{base}/favicon.ico"
    long_name = "Poľovnícka bunda Grand Nord Winter Camo XXL Zelená s kapucňou a membránou"
    long_url = (f"{base}/produkty/polovnicka-bunda-grand-nord-winter-camo-xxl-"
                "zelena-s-kapucnou-a-membranou.html")
    return [
        {
            "key": "BETALOV|p1", "idx": 0, "supplier": "BETALOV",
            "name": long_name, "pairCode": "P1",
            "variant_codes": ["1/M", "1/L"], "our_url": "", "our_images": [],
            "ai_status": "matched", "ai_chosen_url": img_url, "ai_reason": "kód sedí",
            "candidates": [{"name": long_name, "url": long_url}],
            "current": {"state": 1, "price": "99", "std": "", "stock": "3",
                        "avail": "Skladom"},
        },
    ]


@pytest.fixture(scope="function")
def longcontent_matched_server(tmp_path_factory):
    """Isolated webreview instance holding ONE undecided matched product with a long,
    realistic name/candidate/URL — used ONLY by the #82 responsive-layout regression
    (see `_longcontent_fixture_products`). Function-scoped + its own out-dir, same
    isolation rationale as `matched_server`."""
    out = tmp_path_factory.mktemp("wr_longcontent_out")
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    (out / "review_data.json").write_text(
        json.dumps(_longcontent_fixture_products(base), ensure_ascii=False), encoding="utf-8")
    env = {
        **os.environ,
        **_AUTH_ENV,
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


# 1x1 transparent PNG — a hermetic data: URI for the catalog product's defaultImage so
# the search row's <img src> loads with NO network request (clean console in CI). The
# value embeds a ';' (image/png;base64) so the ';'-delimited CSV writer quotes it and
# the app's DictReader reads it back as one field.
_PNG_1x1 = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
            "AAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def _write_catalog_csv(path):
    """Write a cp1250 Shoptet-export fixture with TWO catalog products: one NOT in
    the review set (SRCHP9 — /api/search returns it 'nenapárované', exercising the
    manual promote-and-pair path) and one IN review (DUMMY1 — clicking its result must
    open the FULL review card). Columns are exactly the ones app.py reads
    (`code`/`pairCode` for CODE2PAIR; name/supplier/defaultImage for the catalog index;
    the commerce columns feed the search rows' price/stock/state + the promote-time
    `current` snapshot)."""
    header = ["code", "pairCode", "name", "supplier", "productVisibility",
              "availabilityInStock", "availabilityOutOfStock", "price",
              "standardPrice", "stock", "defaultImage"]
    # names carry diacritics → also exercise the accent-insensitive search (query
    # 'hladaci' normalizes to match 'Hľadací …'). pairCodes SRCHP9/DUMMY1 are unique
    # (not order codes) so they can't collide with the other E2E fixtures.
    rows = [
        ["SRCH9001", "SRCHP9", "Hľadací Test Produkt", "TESTSUP", "visible",
         "Skladom", "Vypredané", "12,50", "15,00", "7", _PNG_1x1],
        ["D1", "DUMMY1", "Kontrolný Produkt V Appke", "TESTSUP", "visible",
         "Skladom", "Vypredané", "89,90", "", "2", _PNG_1x1],
    ]
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow(header)
    w.writerows(rows)
    with open(path, "w", encoding="cp1250", newline="") as f:
        f.write(buf.getvalue())


@pytest.fixture(scope="function")
def search_server(tmp_path_factory):
    """Isolated webreview instance for the catalog-search / re-pair E2E. It gets its
    OWN tmp out-dir + products.csv, so promoting a product (a write to review_data.json
    + decisions.json + a mutation of the in-memory PRODUCTS/CATALOG) is fully contained
    and can NEVER leak into the shared session `live_server` the other E2E tests drive —
    no cross-test store reset needed."""
    out = tmp_path_factory.mktemp("wr_search_out")
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    # One in-review MATCHED product (also keeps the review tab's progress off a 0/0
    # division). Its pairCode DUMMY1 IS in the fixture catalog, so /api/search returns
    # it in_review — clicking that result must open the FULL review card (with the
    # '✓ Dobré' decision button). Keyed 'SUPPLIER|pairCode' (the dominant scheme).
    # ai_chosen_url points back at the local server (204 favicon) so the card's lazy
    # /api/images fetch stays hermetic — no outbound network in CI.
    img_url = f"{base}/favicon.ico"
    (out / "review_data.json").write_text(json.dumps([{
        "idx": 0, "supplier": "TESTSUP", "name": "Kontrolný Produkt V Appke",
        "pairCode": "DUMMY1", "variant_codes": ["D1"], "our_images": [],
        "ai_status": "matched", "ai_chosen_url": img_url, "ai_reason": "kód sedí",
        "candidates": [{"name": "Kontrolný u dodávateľa", "url": img_url}],
        "our_url": "", "key": "TESTSUP|DUMMY1",
        "current": {"state": 1, "off": False, "vis": "visible", "avail": "Skladom",
                    "price": "89,90", "std": "", "stock": "2"},
    }], ensure_ascii=False), encoding="utf-8")
    products_csv = out / "products.csv"
    _write_catalog_csv(products_csv)
    env = {
        **os.environ,
        **_AUTH_ENV,
        "WEBREVIEW_OUT": str(out),
        "WEBREVIEW_PRODUCTS": str(products_csv),
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
