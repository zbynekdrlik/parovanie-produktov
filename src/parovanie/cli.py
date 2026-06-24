from __future__ import annotations
import argparse
import json
import logging
import os
from parovanie.csv_loader import load_rows
from parovanie.grouping import group_products
from parovanie.matcher import match_products
from parovanie.client import SearchClient
from parovanie.writer import write_import, write_report, write_unmatched


def _setup_logging(out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.FileHandler(os.path.join(out_dir, "run.log"),
                                      encoding="utf-8"),
                  logging.StreamHandler()],
    )


def run(input_csv: str, out_dir: str, suppliers: set[str], client=None,
        checkpoint: str | None = None):
    _setup_logging(out_dir)
    rows = load_rows(input_csv, suppliers)
    products = group_products(rows)
    client = client or SearchClient()
    matches = match_products(products, client)
    write_import(matches, os.path.join(out_dir, "import_betalov_wetland.csv"))
    write_report(matches, os.path.join(out_dir, "match_report.csv"))
    write_unmatched(matches, os.path.join(out_dir, "unmatched.csv"))
    if checkpoint:
        with open(checkpoint, "w", encoding="utf-8") as f:
            json.dump({m.product.pair_key: (m.chosen.url if m.chosen else None)
                       for m in matches}, f, ensure_ascii=False, indent=2)
    return matches


def main() -> None:
    ap = argparse.ArgumentParser(description="Párovanie produktov → dodávateľ URL")
    ap.add_argument("--input", required=True, help="forestshop products.csv (cp1250)")
    ap.add_argument("--out", default="data/out", help="output directory")
    ap.add_argument("--suppliers", default="BETALOV,WETLAND")
    args = ap.parse_args()
    run(args.input, args.out, set(args.suppliers.split(",")))


if __name__ == "__main__":
    main()
