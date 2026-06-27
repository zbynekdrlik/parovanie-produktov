"""Gather top-K candidates for one (or more) suppliers into a SEPARATE out dir,
so the live data/out/candidates.json (BETALOV/WETLAND) is never clobbered.

Usage:  PYTHONPATH=src .venv/bin/python scripts/gather_supplier.py ODIMON data/out_odimon

Resumable via <out>/gather_checkpoint.json. The result <out>/candidates.json is
later merged into the master + AI-verified (see .claude/skills/suppliers).
"""
import os
import sys

from parovanie.cli import run_gather
from parovanie.client import SearchClient

suppliers = {s.strip().upper() for s in sys.argv[1].split(",") if s.strip()}
out_dir = sys.argv[2]
os.makedirs(out_dir, exist_ok=True)

records = run_gather(
    "data/products.csv",
    out_dir,
    suppliers,
    client=SearchClient(throttle=0.6),
    checkpoint=os.path.join(out_dir, "gather_checkpoint.json"),
    k=8,
)
print(f"GATHER DONE {sorted(suppliers)}: {len(records)} products -> {out_dir}/candidates.json")
