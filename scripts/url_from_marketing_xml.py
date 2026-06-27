"""Set each review product's forestshop `our_url` from the AUTHORITATIVE source:
the marketing XML's <ORIG_URL> (the eshop's own product URL), matched by exact
product/variant CODE. This replaces the sitemap-resolver GUESS (which returned
None for ~600 products — drop-ship/detailOnly pages absent from the public
sitemap — leaving the UI to fall back to a ?string= search link the manager saw).

Exact code match → no wrong-link risk. Products with no XML match keep their
existing our_url (resolver) or the search fallback.

The marketing XML URL lives in the n8n export workflow (partner-hash credential) —
fetch it to data/out/marketing.xml first, or pass --xml <path>.

Usage: PYTHONPATH=src .venv/bin/python scripts/url_from_marketing_xml.py [--xml data/out/marketing.xml]
"""
import json
import os
import sys
import time

from lxml import etree

XML = "data/out/marketing.xml"
if "--xml" in sys.argv:
    XML = sys.argv[sys.argv.index("--xml") + 1]
REVIEW = "data/out/review_data.json"
CRED = "data/.shoptet_admin"
MAX_AGE = 6 * 3600   # refetch the 59 MB XML at most every 6 h


def _fetch_if_stale(path):
    """Download the marketing XML from the partner-hash URL in the gitignored creds
    (SHOPTET_MARKETING_XML_URL) if the local copy is missing or older than MAX_AGE.
    The hash stays in the creds file — never in this script or git."""
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < MAX_AGE and "--fetch" not in sys.argv:
        return
    url = None
    try:
        for line in open(CRED, encoding="utf-8"):
            if line.startswith("SHOPTET_MARKETING_XML_URL="):
                url = line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    if not url:
        if os.path.exists(path):
            return
        raise SystemExit("chýba SHOPTET_MARKETING_XML_URL v data/.shoptet_admin a žiadny lokálny XML")
    import requests
    print("fetching marketing XML (~59 MB) …")
    r = requests.get(url, timeout=240)
    r.raise_for_status()
    if not r.content:
        raise SystemExit("marketing XML prázdny — neaktualizujem URL")
    open(path, "wb").write(r.content)


def _local(tag):
    return tag.rsplit("}", 1)[-1]  # strip namespace


def _own_codes(el):
    """The product's OWN codes only: the SHOPITEM's direct-child <CODE> plus each
    <VARIANT>'s direct-child <CODE>. Must NOT descend into <RELATED_PRODUCTS> (cross-sell
    references to OTHER products) or <FLAGS> — that pollution mapped an unrelated
    product's code to this product's URL (manager's AH5→Nitecore-P30 bug)."""
    codes = []
    for c in el:                       # direct children of SHOPITEM
        t = _local(c.tag)
        if t == "CODE" and (c.text or "").strip():
            codes.append(c.text.strip())
        elif t == "VARIANTS":
            for v in c:
                if _local(v.tag) != "VARIANT":
                    continue
                for vc in v:           # direct children of VARIANT
                    if _local(vc.tag) == "CODE" and (vc.text or "").strip():
                        codes.append(vc.text.strip())
    return codes


def build_code2url(path):
    """Stream the marketing XML → {code: ORIG_URL} for every product + variant code.
    lxml recover=True tolerates the malformed tokens the strict parser chokes on."""
    code2url = {}
    ctx = etree.iterparse(path, events=("end",), recover=True, huge_tree=True)
    for _ev, el in ctx:
        if _local(el.tag) != "SHOPITEM":
            continue
        orig = ""
        for c in el:                   # ORIG_URL is a direct child
            if _local(c.tag) == "ORIG_URL" and (c.text or "").strip():
                orig = c.text.strip()
        if orig:
            for code in _own_codes(el):
                code2url.setdefault(code, orig)
        el.clear()
    return code2url


def main():
    _fetch_if_stale(XML)
    code2url = build_code2url(XML)
    print(f"marketing XML: {len(code2url)} codes → ORIG_URL")

    rd = json.load(open(REVIEW, encoding="utf-8"))
    fixed = changed = had = 0
    for p in rd:
        url = next((code2url[c] for c in p.get("variant_codes", []) if c in code2url), None)
        if not url:
            continue
        had += 1
        if not p.get("our_url"):
            fixed += 1           # was None (search fallback) → now a real URL
        elif p["our_url"] != url:
            changed += 1         # resolver guess corrected to the authoritative URL
        p["our_url"] = url
    json.dump(rd, open(REVIEW, "w", encoding="utf-8"), ensure_ascii=False)
    still_none = sum(1 for p in rd if not p.get("our_url"))
    print(f"review_data: {had} products matched in XML | {fixed} were search-fallback→fixed | "
          f"{changed} resolver-guess→corrected | still None: {still_none}/{len(rd)}")


if __name__ == "__main__":
    main()
