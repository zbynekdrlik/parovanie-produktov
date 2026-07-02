"""Add 21 "Drevo Novák" forestshop products into the LIVE review set
(data/out/review_data.json) as AI-matched review cards (each with a lovuzdar.cz
candidate) so the managers CONFIRM them on the web. NO decision is written —
decisions.json is NOT touched.

Reuses the canonical helpers (per CLAUDE.md "NEkopíruj logiku"):
  * export_helpers.current_of / row_images  — the `current` snapshot + images
  * url_from_marketing_xml.build_code2url    — our_url per variant code

Entry shape mirrors scripts/build_review_data.py exactly.

Idempotent: re-running removes any existing "Drevo Novák|"-keyed entries first,
then re-appends the 21 (so no duplication; stable idx range). Atomic write.

Usage: PYTHONPATH=src .venv/bin/python scripts/add_drevo_novak_reviews.py \
           [--matches <path>] [--no-backup]
"""
import csv
import json
import os
import sys
import time

from parovanie.export_helpers import current_of, row_images

csv.field_size_limit(10**9)

REVIEW = "data/out/review_data.json"
EXPORT = "data/products.csv"
XML = "data/out/marketing.xml"
BACKUP_DIR = "data/backups"
SUPPLIER = "Drevo Novák"
KEY_PREFIX = SUPPLIER + "|"

MATCHES = "/tmp/claude-1000/-home-newlevel-devel-forestshop-parovanie-produktov/" \
          "3cfe2eae-b4ca-4d4e-bf57-54a8e793d2f9/scratchpad/lovuzdar_matches.json"
if "--matches" in sys.argv:
    MATCHES = sys.argv[sys.argv.index("--matches") + 1]


def cand_name(lov_name: str, price: str) -> str:
    p = (price or "").strip()
    if not p:
        return lov_name
    return f"{lov_name} — {p}" if "€" in p else f"{lov_name} — {p} €"


def short_note(note: str) -> str:
    note = " ".join((note or "").split())
    first = note.split(". ", 1)[0]
    if first and not first.endswith((".", ")", "€")):
        first += "."
    return first


def load_export():
    """cp1250 read of the export → per-code snapshot tuple, images, pairCode, and
    pairCode→[codes] grouping (mirrors build_review_data's single-pass load)."""
    code2cur, code2img, code2pair, by_pair = {}, {}, {}, {}
    with open(EXPORT, encoding="cp1250", errors="replace") as f:
        for row in csv.DictReader(f, delimiter=";"):
            c = (row.get("code") or "").strip()
            if not c:
                continue
            pc = (row.get("pairCode") or "").strip()
            code2pair[c] = pc
            if pc:
                by_pair.setdefault(pc, []).append(c)
            code2cur[c] = ((row.get("productVisibility") or "").strip(),
                           (row.get("availabilityInStock") or "").strip(),
                           (row.get("availabilityOutOfStock") or "").strip(),
                           (row.get("price") or "").strip(),
                           (row.get("standardPrice") or "").strip(),
                           (row.get("stock") or "").strip())
            code2img[c] = row_images(row)
    return code2cur, code2img, code2pair, by_pair


def build_entries(matches, code2cur, code2img, code2pair, by_pair, code2url, base_idx):
    out = []
    gaps = {"our_url_none": [], "no_images": []}
    for j, m in enumerate(matches):
        code = m["code"]
        pc = code2pair.get(code, "")
        # variant_codes: matched code FIRST, then any pairCode siblings (sorted).
        if pc:
            sibs = sorted(set(by_pair.get(pc, [])) - {code})
            vcodes = [code] + sibs
        else:
            vcodes = [code]

        # images: first variant code that has any (mirrors build_review_data), cap 6
        our_imgs = []
        for c in vcodes:
            imgs = code2img.get(c, [])
            for u in imgs:
                if u not in our_imgs:
                    our_imgs.append(u)
            if our_imgs:
                break
        if not our_imgs:
            gaps["no_images"].append(code)

        # our_url: best-effort from marketing XML by first matching variant code
        our_url = next((code2url[c] for c in vcodes if c in code2url), None)
        if not our_url:
            gaps["our_url_none"].append(code)

        # current: OUR eshop snapshot from vcodes[0] (exact build_review_data call)
        _vis, _ais, _aos, _price, _std, _stock = code2cur.get(
            vcodes[0], ("?", "", "", "", "", ""))

        eur = m.get("eur_price", "")
        eur_clean = (eur or "").replace("€", "").strip()
        conf = m.get("confidence", "")
        lov_url = m.get("lovuzdar_url", "")
        lov_name = m.get("lovuzdar_name", "")
        key = KEY_PREFIX + (pc if pc else code)

        out.append({
            "idx": base_idx + j,
            "key": key,
            "supplier": SUPPLIER,
            "name": m.get("name", ""),
            "pairCode": pc,
            "variant_codes": vcodes,
            "our_images": our_imgs[:6],
            "our_url": our_url,
            "current": current_of(_vis, _ais, _aos, _price, _std, _stock),
            "ai_status": "matched",
            "ai_chosen_url": lov_url,
            "ai_reason": f"lovuzdar.cz VO (EUR {eur_clean}) — {conf}; {short_note(m.get('note',''))}",
            "candidates": [{"name": cand_name(lov_name, eur), "url": lov_url}],
        })
    return out, gaps


def main():
    matches = json.load(open(MATCHES, encoding="utf-8"))
    assert len(matches) == 21, f"expected 21 matches, got {len(matches)}"

    code2cur, code2img, code2pair, by_pair = load_export()

    print("streaming marketing XML for our_url …")
    from url_from_marketing_xml import build_code2url
    code2url = build_code2url(XML)
    print(f"marketing XML: {len(code2url)} codes → ORIG_URL")

    # BACKUP first (mirror shoptet_import.py naming: <name>_<ts>)
    rd = json.load(open(REVIEW, encoding="utf-8"))
    before = len(rd)
    backup_path = None
    if "--no-backup" not in sys.argv:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        backup_path = os.path.join(BACKUP_DIR, f"review_data_{ts}.json")
        with open(backup_path, "w", encoding="utf-8") as bf:
            json.dump(rd, bf, ensure_ascii=False)
        print(f"backup: {backup_path} ({before} entries)")

    # Fresh-read again (defensive) and strip any prior Drevo Novák entries (idempotent)
    rd = json.load(open(REVIEW, encoding="utf-8"))
    kept = [r for r in rd if not str(r.get("key", "")).startswith(KEY_PREFIX)]
    removed = len(rd) - len(kept)
    base_idx = (max((r["idx"] for r in kept), default=-1)) + 1

    entries, gaps = build_entries(
        matches, code2cur, code2img, code2pair, by_pair, code2url, base_idx)

    # unique-key sanity
    new_keys = [e["key"] for e in entries]
    assert len(new_keys) == len(set(new_keys)), "duplicate keys among new entries!"
    existing = {r.get("key") for r in kept}
    clash = existing & set(new_keys)
    assert not clash, f"new keys collide with existing: {clash}"

    final = kept + entries

    tmp = REVIEW + ".tmp"
    with open(tmp, "w", encoding="utf-8") as tf:
        json.dump(final, tf, ensure_ascii=False)
    # validate parse before swap
    json.load(open(tmp, encoding="utf-8"))
    os.replace(tmp, REVIEW)

    print(f"review_data.json: {before} → {len(final)} "
          f"(removed {removed} prior Drevo Novák, added {len(entries)})")
    print(f"Drevo Novák entries now: "
          f"{sum(1 for r in final if r.get('supplier') == SUPPLIER)}")
    print(f"our_url gaps (None): {len(gaps['our_url_none'])} {gaps['our_url_none']}")
    print(f"image gaps (empty):  {len(gaps['no_images'])} {gaps['no_images']}")


if __name__ == "__main__":
    main()
