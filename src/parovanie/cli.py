from __future__ import annotations
import argparse
import json
import logging
import os
from parovanie.models import Candidate, Match
from parovanie.csv_loader import load_rows
from parovanie.grouping import group_products
from parovanie.matcher import match_one, gather_candidates
from parovanie.client import SearchClient
from parovanie.writer import write_import, write_report, write_unmatched
from parovanie import candidates_io

log = logging.getLogger("parovanie.cli")


def _setup_logging(out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    logger = logging.getLogger("parovanie")
    logger.setLevel(logging.INFO)
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    fh = logging.FileHandler(os.path.join(out_dir, "run.log"), encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    logger.propagate = False


def _match_to_ckpt(m: Match) -> dict:
    return {"url": m.chosen.url if m.chosen else None,
            "confidence": m.confidence, "query": m.query,
            "candidate_count": m.candidate_count}


def _ckpt_to_match(product, rec: dict) -> Match:
    url = rec.get("url")
    chosen = Candidate(name="", url=url) if url else None
    return Match(product=product, query=rec.get("query", ""), chosen=chosen,
                 confidence=rec.get("confidence", "none"),
                 candidate_count=rec.get("candidate_count", 0))


def run(input_csv: str, out_dir: str, suppliers: set[str], client=None,
        checkpoint: str | None = None):
    _setup_logging(out_dir)
    rows = load_rows(input_csv, suppliers)
    products = group_products(rows)
    client = client or SearchClient()
    done: dict = {}
    if checkpoint and os.path.exists(checkpoint):
        with open(checkpoint, encoding="utf-8") as f:
            done = json.load(f)
        log.info("resume: %d products already in checkpoint %s", len(done), checkpoint)
    matches = []
    for i, p in enumerate(products, 1):
        if p.pair_key in done:
            matches.append(_ckpt_to_match(p, done[p.pair_key]))
            continue
        m = match_one(p, client)
        matches.append(m)
        if checkpoint:
            done[p.pair_key] = _match_to_ckpt(m)
            with open(checkpoint, "w", encoding="utf-8") as f:
                json.dump(done, f, ensure_ascii=False, indent=2)
    write_import(matches, os.path.join(out_dir, "import_betalov_wetland.csv"))
    write_report(matches, os.path.join(out_dir, "match_report.csv"))
    write_unmatched(matches, os.path.join(out_dir, "unmatched.csv"))
    return matches


def run_gather(input_csv, out_dir, suppliers, client=None, checkpoint=None, k=8):
    _setup_logging(out_dir)
    rows = load_rows(input_csv, suppliers)
    products = group_products(rows)
    client = client or SearchClient()
    done: dict = {}
    if checkpoint and os.path.exists(checkpoint):
        with open(checkpoint, encoding="utf-8") as f:
            done = json.load(f)
        log.info("resume gather: %d products already gathered", len(done))
    records: list[dict] = []
    for i, p in enumerate(products, 1):
        if p.pair_key in done:
            records.append(done[p.pair_key])
            continue
        queries, cands = gather_candidates(p, client, k=k)
        rec = candidates_io.record(p, queries, cands)
        records.append(rec)
        log.info("[%d/%d] %s %r -> %d candidates", i, len(products), p.supplier,
                 p.name[:40], len(cands))
        if checkpoint:
            done[p.pair_key] = rec
            with open(checkpoint, "w", encoding="utf-8") as f:
                json.dump(done, f, ensure_ascii=False, indent=2)
    candidates_io.write_candidates(records, os.path.join(out_dir, "candidates.json"))
    return records


def main() -> None:
    ap = argparse.ArgumentParser(description="Párovanie produktov → dodávateľ URL")
    ap.add_argument("--input", required=True, help="forestshop products.csv (cp1250)")
    ap.add_argument("--out", default="data/out", help="output directory")
    ap.add_argument("--suppliers", default="BETALOV,WETLAND",
                    help="comma-separated supplier codes (default: BETALOV,WETLAND)")
    ap.add_argument("--checkpoint", default="data/out/checkpoint.json",
                    help="checkpoint file for resume (default: data/out/checkpoint.json)")
    ap.add_argument("--gather", action="store_true",
                    help="zber kandidátov pre AI overenie (candidates.json)")
    ap.add_argument("--k", type=int, default=8)
    args = ap.parse_args()
    if args.gather:
        run_gather(args.input, args.out, set(args.suppliers.split(",")),
                   checkpoint=os.path.join(args.out, "gather_checkpoint.json"),
                   k=args.k)
    else:
        run(args.input, args.out, set(args.suppliers.split(",")),
            checkpoint=args.checkpoint)


if __name__ == "__main__":
    main()
