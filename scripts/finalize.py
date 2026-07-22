"""Build final outputs from candidates.json + AI verdicts.

Reads:
  data/out/candidates.json   (969 product records, index = position)
  data/out/ai_verdicts.json  (list of {idx, chosen_i, reason})
Writes:
  data/out/import_betalov_wetland.csv  (code;pairCode;internalNote, one row per variant of matched products)
  data/out/match_report.csv            (per product, with AI verdict)
  data/out/unmatched.csv               (products the AI rejected / no candidate)
"""
import csv
import json
from parovanie import candidates_io
from parovanie.models import Product, Candidate, Match
from parovanie.csv_loader import load_code2pair
from parovanie.writer import write_unmatched, shoptet_writer
from parovanie.report_io import write_report_rows

csv.field_size_limit(10**9)
OUT = "data/out"
SOURCE = "data/products.csv"
recs = json.load(open(f"{OUT}/candidates.json", encoding="utf-8"))
verdicts = json.load(open(f"{OUT}/ai_verdicts.json", encoding="utf-8"))
# keyed by pair_key, never by array position — see candidates_io.join_verdicts (#43)
verds_by_rec = candidates_io.join_verdicts(recs, verdicts)

# code -> pairCode from the source export, so the import is the minimal safe set
# (code;pairCode;internalNote — reorder URL in the private field).
code2pair = load_code2pair(SOURCE)

matches, report_rows = [], []
n_ok = 0
for i, r in enumerate(recs):
    cands = r["candidates"]
    v = verds_by_rec[i]
    ci = v["chosen_i"] if v else -1
    reason = (v["reason"] if v else "bez verdiktu")[:200]
    chosen = None
    if isinstance(ci, int) and 0 <= ci < len(cands):
        c = cands[ci]
        chosen = Candidate(name=c.get("name", ""), url=c["url"])
        n_ok += 1
    p = Product(r["supplier"], r["pair_key"], r["external_code"], r["name"], r["variant_codes"])
    matches.append(Match(p, query="", chosen=chosen,
                         confidence="OK" if chosen else "none",
                         candidate_count=len(cands)))
    report_rows.append({
        "supplier": r["supplier"],
        "external_code": r["external_code"] or "",
        "name": r["name"],
        "query": "",
        "chosen_url": chosen.url if chosen else "",
        "confidence": "OK" if chosen else "NONE",
        "candidate_count": len(cands),
        "variant_count": len(r["variant_codes"]),
        "verdict": "OK" if chosen else "NONE",
        "verdict_reason": reason,
        "attempts": "1",
    })

# Minimal import: code;pairCode;internalNote (one row per matched variant).
# UTF-8 with BOM (documented import contract; avoids cp1250 mojibake). No
# errors= handler: an un-encodable char aborts loudly instead of shipping '?'.
with open(f"{OUT}/import_betalov_wetland.csv", "w", encoding="utf-8-sig",
          newline="") as f:
    w = shoptet_writer(f)
    w.writerow(["code", "pairCode", "internalNote"])
    for m in matches:
        if not m.chosen:
            continue
        for code in m.product.variant_codes:
            w.writerow([code, code2pair.get(code, ""), m.chosen.url])

write_report_rows(report_rows, f"{OUT}/match_report.csv")
write_unmatched(matches, f"{OUT}/unmatched.csv")

n_import = sum(len(m.product.variant_codes) for m in matches if m.chosen)
print(f"products: {len(recs)}  matched(OK): {n_ok}  unmatched: {len(recs)-n_ok}")
print(f"import rows (variants): {n_import}")
print(f"wrote {OUT}/import_betalov_wetland.csv, match_report.csv, unmatched.csv")
