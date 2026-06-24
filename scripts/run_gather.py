"""Full live candidate gather for all BETALOV/WETLAND products → data/out/candidates.json.
Resumable via data/out/gather_checkpoint.json. Run in background."""
from parovanie.cli import run_gather
from parovanie.client import SearchClient

records = run_gather(
    "data/products.csv",
    "data/out",
    {"BETALOV", "WETLAND"},
    client=SearchClient(throttle=0.35),
    checkpoint="data/out/gather_checkpoint.json",
    k=8,
)
print(f"GATHER DONE: {len(records)} products -> data/out/candidates.json")
