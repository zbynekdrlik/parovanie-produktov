"""Wiring tests for scripts/shoptet_import.py (#257/#196).

The row-picking LOGIC lives in parovanie.shoptet_import (tested with fixtures in
test_shoptet_import.py); these tests prove the script actually FEEDS it the two
facts that identify this run — the pre-import baseline row and the number of rows
the CSV really carries — and that its read-back polls instead of grabbing a
foreign row. No browser, no network: the page and playwright are fakes, and the
audit dir is redirected to tmp so nothing can touch data/out.
"""
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "shoptet_import_script", ROOT / "scripts" / "shoptet_import.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


script = _load_script()

BASELINE = "#12688 26.07.2026 20:12 Info Import dobehol úspešne. Spracované: 4. Upravené: 1."
OURS_35 = ("#12689 26.07.2026 21:00 Upozornenie Import skončil s chybou. "
           "Spracované: 35. Upravené: 31. Zlyhanie variantov: 2.")
FOREIGN_1 = ("#12704 26.07.2026 21:01 Upozornenie Import skončil s chybou. "
             "Spracované: 1. Zlyhanie variantov: 1.")


class FakePage:
    """Minimal stand-in for a Playwright page: serves one snapshot of Log rows per
    read, advancing to the next snapshot on every reload()."""

    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.reloads = 0
        self.waits = 0

    def evaluate(self, _js):
        return self.snapshots[min(self.reloads, len(self.snapshots) - 1)]

    def wait_for_timeout(self, _ms):
        self.waits += 1

    def reload(self, **_kw):
        self.reloads += 1

    # unused by the tested paths, present so a stray call fails loudly
    def goto(self, *a, **kw):
        raise AssertionError("test page must not navigate")


def test_read_result_polls_past_a_foreign_row_until_our_own_appears():
    # #257: the foreign 1-row import lands first. Reading "the newest row" booked
    # its 'Spracované: 1' as the result of our 35-row import.
    page = FakePage([[FOREIGN_1, BASELINE],
                     [FOREIGN_1, BASELINE],
                     [FOREIGN_1, OURS_35, BASELINE]])
    row = script._read_result(page, baseline=BASELINE, expected_rows=35,
                              retries=5, wait_s=0)
    assert row == OURS_35
    assert page.reloads >= 1          # it really waited instead of taking the top row


def test_read_result_does_not_credit_a_lone_foreign_row_that_matches_the_count():
    # The residual hole of count-only matching: a CONCURRENT import of the same size
    # writes its row first, ours has not appeared yet — on a single read it is the
    # only match and would be taken as ours (and its 300 codes recorded uploaded
    # although nothing landed). The read must settle: a pick is only accepted when
    # two consecutive reads agree, so our own row appearing in between turns the
    # false match into an unattributable result instead of a false success.
    foreign = "#12701 26.07.2026 21:00 Info Import dobehol úspešne. Spracované: 300. Upravené: 12."
    ours = "#12702 26.07.2026 21:00 Info Import dobehol úspešne. Spracované: 300. Upravené: 298."
    page = FakePage([[foreign, BASELINE],
                     [ours, foreign, BASELINE],
                     [ours, foreign, BASELINE]])
    assert script._read_result(page, baseline=BASELINE, expected_rows=300,
                               retries=4, wait_s=0) is None


def test_read_result_settles_on_the_entry_id_not_on_volatile_row_text():
    # The settle check must compare WHICH entry it is, not the rendered text: a Log
    # row whose innerText changes cosmetically between two reads (a late-rendered
    # link, a relative time) would never "agree with itself", so a perfectly good
    # import would end unattributable and its chunk would be booked as failed.
    a = ("#12689 26.07.2026 21:00 Upozornenie Import skončil s chybou. "
         "Spracované: 35. Upravené: 31. Zlyhanie variantov: 2.")
    # the SAME entry, re-rendered with a different volatile tail on every read
    page = FakePage([[a + " pred 1 s", BASELINE], [a + " pred 3 s", BASELINE],
                     [a + " pred 5 s", BASELINE], [a + " pred 7 s", BASELINE]])
    row = script._read_result(page, baseline=BASELINE, expected_rows=35,
                              retries=4, wait_s=0)
    assert row is not None and row.startswith("#12689")


def test_capture_baseline_prints_a_readable_marker_when_the_row_has_no_id(capsys):
    class P:
        def goto(self, *a, **kw):
            pass

        def wait_for_load_state(self, *a, **kw):
            pass

        def evaluate(self, _js):
            return ["26.07.2026 21:00 Import dobehol úspešne. Spracované: 4."]

    script._capture_baseline(P(), {"SHOPTET_ADMIN_URL": "https://x/admin"})
    printed = capsys.readouterr().out
    assert "#None" not in printed
    assert "Spracované" not in printed


def test_capture_baseline_never_echoes_the_previous_runs_counts(capsys):
    """The stdout of this script is parsed by webreview/app.py to learn what the
    import did. Echoing the baseline ROW there put a foreign 'Spracované: N' AHEAD of
    our own result line, and parse_import_log takes the first match — so the app read
    the PREVIOUS entry's numbers as this run's result (#196's 'processed=1/failed=1'
    while 260 rows were really processed). Print the entry id, never the counts."""

    class P:
        def goto(self, *a, **kw):
            pass

        def wait_for_load_state(self, *a, **kw):
            pass

        def evaluate(self, _js):
            return [BASELINE]

    assert script._capture_baseline(P(), {"SHOPTET_ADMIN_URL": "https://x/admin"}) == BASELINE
    printed = capsys.readouterr().out
    assert "12688" in printed                      # still identifies the baseline
    assert "Spracované" not in printed and "Upravené" not in printed


def test_read_result_gives_up_instead_of_reporting_a_foreign_row():
    # Our row never shows up → None → parse_import_log(None) → processed=None →
    # exit code 2 ("výsledok sa nepodarilo prečítať"), never a foreign success.
    page = FakePage([[FOREIGN_1, BASELINE]])
    assert script._read_result(page, baseline=BASELINE, expected_rows=35,
                               retries=3, wait_s=0) is None


def test_read_result_waits_out_the_WHOLE_window_before_crediting_a_same_sized_row():
    """PR #271 review, CRITICAL 1 — the settle check returned on the FIRST two
    consecutive agreeing reads, so the "give our own entry time to appear" guarantee
    covered ~ONE wait_s interval (~2 s), not the 6×2 s poll window the docstring
    promises.

    The realistic collision: a foreign import writes its own 300-row entry between our
    baseline and our first read, and Shoptet writes OUR entry a few seconds later
    (every large batch is chunked to exactly IMPORT_CHUNK_ROWS = 300 rows, so
    "same size" is the normal case). Reads 0+1 both see only the foreign row → the old
    code returned it, chunk_outcome booked `ok`, 300 codes went into success_codes and
    into uploaded_pairings.json, and new_pairing_keys skipped them FOREVER — a
    permanent, silent loss of 300 pairings.

    The window must be exhausted first: our entry then shows up, TWO distinct entries
    matched the count, the read is genuinely ambiguous → None → exit 2 (fail closed),
    the rows are simply re-sent next run."""
    foreign = "#12701 26.07.2026 21:00 Info Import dobehol úspešne. Spracované: 300. Upravené: 12."
    ours = "#12702 26.07.2026 21:00 Info Import dobehol úspešne. Spracované: 300. Upravené: 298."
    page = FakePage([[foreign, BASELINE],
                     [foreign, BASELINE],
                     [foreign, BASELINE],
                     [ours, foreign, BASELINE]])
    assert script._read_result(page, baseline=BASELINE, expected_rows=300,
                               retries=6, wait_s=0) is None


def test_read_result_polls_the_whole_window_even_when_early_reads_agree():
    """Same defect from the other side: with our own row already present the old code
    returned after the SECOND read (one reload), so a foreign entry appearing later in
    the promised window was never seen. The read must use every retry it was given."""
    ours = OURS_35
    page = FakePage([[ours, BASELINE]])          # stable from the very first read
    row = script._read_result(page, baseline=BASELINE, expected_rows=35,
                              retries=4, wait_s=0)
    assert row == ours                            # still attributed — no false negative
    assert page.reloads == 3                      # …but the FULL window was observed


def test_do_import_passes_the_submitted_row_count_to_the_read_back(monkeypatch):
    seen = {}

    def fake_read_result(page, baseline=None, expected_rows=None, **kw):
        seen["baseline"], seen["expected_rows"] = baseline, expected_rows
        return OURS_35

    monkeypatch.setattr(script, "_read_result", fake_read_result)
    monkeypatch.setattr(script, "_ensure_safe_settings", lambda page: None)

    class Chooser:
        value = types.SimpleNamespace(set_files=lambda _p: None)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class P:
        def expect_file_chooser(self):
            return Chooser()

        def locator(self, _sel):
            return types.SimpleNamespace(
                first=types.SimpleNamespace(click=lambda: None))

        def wait_for_timeout(self, _ms):
            pass

        def get_by_test_id(self, _t):
            return types.SimpleNamespace(click=lambda: None)

        def wait_for_url(self, *a, **kw):
            pass

        def wait_for_load_state(self, *a, **kw):
            pass

    assert script._do_import(P(), "x.csv", baseline=BASELINE, expected_rows=35) == OURS_35
    assert seen == {"baseline": BASELINE, "expected_rows": 35}


def test_run_browser_feeds_the_preflight_row_count_into_the_import(monkeypatch, tmp_path):
    """The end of the chain: the CSV's real row count (preflight `total`) is what the
    read-back correlates on. Also pins AUDIT_DIR to tmp — the script writes its result
    log there and must never reach the live data/out."""
    calls = {}
    fake_pw = types.ModuleType("playwright.sync_api")

    class Ctx:
        def new_page(self):
            return "PAGE"

    class Browser:
        def new_context(self, **kw):
            return Ctx()

        def close(self):
            calls["closed"] = True

    class PW:
        chromium = types.SimpleNamespace(launch=lambda **kw: Browser())

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    fake_pw.sync_playwright = lambda: PW()
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_pw)
    monkeypatch.setattr(script, "AUDIT_DIR", tmp_path)
    monkeypatch.setattr(script, "_login", lambda page, creds: None)
    monkeypatch.setattr(script, "_capture_baseline", lambda page, creds: BASELINE)
    monkeypatch.setattr(script, "_goto_import", lambda page, creds: None)

    def fake_do_import(page, csv_path, baseline=None, expected_rows=None):
        calls["baseline"], calls["expected_rows"] = baseline, expected_rows
        return OURS_35

    monkeypatch.setattr(script, "_do_import", fake_do_import)
    args = types.SimpleNamespace(file="x.csv", dry_run=False, headful=False)
    rc = script._run_browser(args, {"SHOPTET_ADMIN_URL": "https://x/admin"},
                             {"total": 35})
    assert calls["expected_rows"] == 35 and calls["baseline"] == BASELINE
    # partial result (2 variant failures) is still a non-zero script exit code —
    # the app classifies it as PARTIAL from the stdout counts, not from rc.
    assert rc == 2
    written = list(tmp_path.glob("shoptet_import_*.log"))
    assert written and OURS_35 in written[0].read_text(encoding="utf-8")


def test_read_result_survives_a_last_read_that_never_rendered_the_log():
    """MINOR (revízia PR #271) — the final `reload(wait_until="networkidle")` can
    return BEFORE Shoptet renders the Log table, so the LAST read of the poll window
    sees no rows at all. Every read that COULD see the Log had settled on our own
    entry, yet that one empty read made the verdict None → the chunk was booked
    UNREADABLE (exit 2) → `break` skipped the rest of the night's chunks and the
    manager was told the result „sa nepodarilo prečítať" for a run that succeeded.
    It fails closed (the rows are simply re-sent next run), but the whole remaining
    batch is lost to one transient render."""
    for tail in ([], ["Dátum Výsledok"]):        # nothing rendered / page chrome only
        page = FakePage([[OURS_35, BASELINE],
                         [OURS_35, BASELINE],
                         [OURS_35, BASELINE],
                         tail])
        assert script._read_result(page, baseline=BASELINE, expected_rows=35,
                                   retries=4, wait_s=0) == OURS_35


def test_read_result_still_fails_closed_when_the_last_read_is_ambiguous():
    """The other half of that distinction, and the reason the empty-read fallback may
    NOT simply drop the `row is not None` clause: on a LATE ambiguity (a second
    same-sized entry appears at the end of the window) pick_result_row returns None
    WITHOUT adding anything to `seen`, so `len(seen) == 1` alone still holds from the
    earlier reads and would credit a run Shoptet never confirmed. A read that really
    rendered log rows and still could not attribute them stays fatal."""
    twin = ("#12703 26.07.2026 21:00 Upozornenie Import skončil s chybou. "
            "Spracované: 35. Upravené: 4. Zlyhanie variantov: 1.")
    page = FakePage([[OURS_35, BASELINE],
                     [OURS_35, BASELINE],
                     [twin, OURS_35, BASELINE]])
    assert script._read_result(page, baseline=BASELINE, expected_rows=35,
                               retries=3, wait_s=0) is None


def test_read_result_stays_none_when_the_log_never_rendered_at_all():
    """A window in which NO read ever saw a log row proves nothing — there is no
    earlier verdict to fall back to, so the result stays unreadable."""
    page = FakePage([[], ["Dátum Výsledok"], []])
    assert script._read_result(page, baseline=BASELINE, expected_rows=35,
                               retries=3, wait_s=0) is None
