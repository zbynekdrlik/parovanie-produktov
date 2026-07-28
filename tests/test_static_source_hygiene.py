"""The browser assets stay TEXT — no raw NUL bytes in `webreview/static/`.

A raw NUL inside a JS string literal is legal JavaScript and runs fine, so nothing in the
test suite or the browser ever complains. `grep`, however, classifies a file holding one
as BINARY and then silently returns NOTHING for every pattern — the whole 5000-line
`app.js` goes dark for every tool built on it (grep/rg sweeps, review tooling, the editor's
own search). A reviewer hit exactly that: searches over `app.js` came back empty, not
"no match", and the file looked clean because the byte is invisible.

The intent is always a separator that cannot occur inside the data (a flag/row composite
key, an editor snapshot key). Write it as the ESCAPE `'\\u0000'`, which is plain ASCII in
the source and produces the same string at runtime.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "webreview", "static")


def _sources():
    for name in sorted(os.listdir(STATIC)):
        path = os.path.join(STATIC, name)
        if os.path.isfile(path):
            yield name, path


def test_no_raw_nul_bytes_in_static_sources():
    """Read as BYTES on purpose — a text read with a permissive codec hands back a
    U+0000 that looks like any other character and the check would pass on the very
    file it exists to catch."""
    offenders = []
    for name, path in _sources():
        with open(path, "rb") as fh:
            data = fh.read()
        if b"\x00" not in data:
            continue
        # report WHERE, so the fix does not start with another binary-blind grep
        lines = [i for i, line in enumerate(data.split(b"\n"), 1) if b"\x00" in line]
        offenders.append(
            f"{name}: {data.count(bytes([0]))} raw NUL byte(s) on line(s) {lines}")
    assert offenders == [], (
        "raw NUL byte(s) in webreview/static/ — grep treats these files as BINARY and "
        "silently matches NOTHING in them. Write the separator as the escape '\\u0000' "
        "instead: " + "; ".join(offenders))


def test_the_static_sources_are_actually_scanned():
    """Guards the guard: an empty or mis-pointed listing would make the check above pass
    unconditionally, which is the classic way a hygiene test rots into decoration."""
    names = [name for name, _ in _sources()]
    assert "app.js" in names, names
