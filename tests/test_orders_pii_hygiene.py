"""#289 — no test source may carry a value that is (or could be) a LIVE order code.

The repository is PUBLIC and `data/out/orders_cache.csv` ties every `code` to a customer
name, e-mail, phone and address. Twice already a real code survived in `tests/` because
it „looked made up" and because it was copied from the test two lines above
(`.claude/rules/toorder-e2e.md` point 7, `.claude/rules/automation-health.md`).

„0 hits in today's cache" can never settle it: the cache is a sliding 30-day window, so a
code from two months ago is absent today and real all the same. The only thing that
settles it is a FORM a real code cannot have.

Shoptet builds an order code as `<year><4-digit sequence>` — `2026nnnn`. So the test
convention is the reverse: an order code in a test starts with `9900`
(`2026nnnn` → `9900nnnn`), which is not a year and therefore cannot collide with any past
or future export. This test pins that convention, so the next author who pastes a code
off the live tab fails here with a readable reason instead of publishing it.

Scope is every HAND-WRITTEN source we publish and could paste a code into: `tests/**/*.py`
plus the committed playbook and docs (`.claude/**`, `docs/**`). The leak is the same in a
rule file as in a test — the playbook itself carried one until this ticket. The supplier
HTML fixtures under `tests/fixtures/` are the one exclusion: they are saved third-party
pages whose 8-digit numbers are the SUPPLIERS' own (image timestamps such as
`photoroom-20251002-090256188`), they carry nothing of our customers, and re-minting them
would destroy the very thing they exist to pin — the real page shape.

The example values below are `2099…`: shape-valid for the regex, and a year no export of
this shop will reach — a self-test must not smuggle back in what the test forbids.
"""
import pathlib
import re

# `<year><4 digits>` — the shape a forestshop order code actually has.
_SHOPTET_ORDER_CODE = re.compile(r"\b20\d{6}\b")

_TESTS = pathlib.Path(__file__).resolve().parent
_ROOT = _TESTS.parent
# Hand-written, published sources. `tests/fixtures/` is excluded wholesale (see module
# docstring); everything else here is something a person types.
_SCANNED = (("tests", ("*.py",)),
            (".claude", ("*.md",)),
            ("docs", ("*.md", "*.html")))


def _sources():
    out = []
    for sub, globs in _SCANNED:
        base = _ROOT / sub
        if not base.exists():
            continue
        for pat in globs:
            out += [p for p in base.rglob(pat)
                    if "__pycache__" not in p.parts and "fixtures" not in p.parts]
    return sorted(out)


def test_no_published_source_carries_a_shoptet_shaped_order_code():
    """Every order code we write down must be of the fictitious `9900…` form.

    The check is on the FORM, never on a list of known-leaked values: a list goes stale
    the moment someone adds a new one, and the sliding cache window means a fresh paste
    would not show up in a grep against today's export either.
    """
    offenders = {}
    for p in _sources():
        if p.name == pathlib.Path(__file__).name:      # this file names the shape itself
            continue
        hits = sorted(set(_SHOPTET_ORDER_CODE.findall(p.read_text(encoding="utf-8"))))
        if hits:
            offenders[p.relative_to(_ROOT).as_posix()] = hits
    assert not offenders, (
        "published sources carry year-prefixed, order-code-shaped values — they may be "
        "REAL codes from the live export (public repo, customer-linked). Re-mint them to "
        "the fictitious 9900nnnn form: " + repr(offenders))


def test_the_guard_recognises_a_real_shaped_code():
    """The guard itself must be able to fail — a regex that matches nothing would let the
    whole rule rot silently green."""
    assert _SHOPTET_ORDER_CODE.search("key = '20991234|TESTKOD/L'")
    assert _SHOPTET_ORDER_CODE.search('"20990001;2026-05-20 09:00:00;Vybavuje sa"')
    # …and must not fire on the anonymised form, nor on an ordinary ISO date
    assert not _SHOPTET_ORDER_CODE.search("key = '99001234|TESTKOD/L'")
    assert not _SHOPTET_ORDER_CODE.search('"date": "2026-05-20 09:00:00"')
