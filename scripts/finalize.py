"""Build final outputs from candidates.json + AI verdicts.

Reads:
  data/out/candidates.json   (969 product records, index = position)
  data/out/ai_verdicts.json  (list of {idx, chosen_i, reason})
Writes:
  data/out/import_betalov_wetland.csv  (code;textProperty10, one row per variant of matched products)
  data/out/match_report.csv            (per product, with AI verdict)
  data/out/unmatched.csv               (products the AI rejected / no candidate)
"""
import csv
import json
from parovanie.models import Product, Candidate, Match
from parovanie.writer import write_unmatched
from parovanie.report_io import write_report_rows

csv.field_size_limit(10**9)
OUT = "data/out"
SOURCE = "data/products.csv"
recs = json.load(open(f"{OUT}/candidates.json", encoding="utf-8"))
verds = {v["idx"]: v for v in json.load(open(f"{OUT}/ai_verdicts.json", encoding="utf-8"))}

# code -> raw pairCode from the source export, so the import is the minimal safe
# set (code;pairCode;textProperty10) and never overwrites other product fields.
code2pair = {}
with open(SOURCE, encoding="cp1250", errors="replace", newline="") as _f:
    for _row in csv.DictReader(_f, delimiter=";"):
        _c = (_row.get("code") or "").strip()
        if _c:
            code2pair[_c] = (_row.get("pairCode") or "").strip()

matches, report_rows = [], []
n_ok = 0
for i, r in enumerate(recs):
    cands = r["candidates"]
    v = verds.get(i)
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

# Minimal import: code;pairCode;textProperty10 (one row per matched variant).
with open(f"{OUT}/import_betalov_wetland.csv", "w", encoding="cp1250",
          errors="replace", newline="") as f:
    w = csv.writer(f, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow(["code", "pairCode", "textProperty10"])
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
