from __future__ import annotations
import json
from parovanie.models import Product, Candidate


def record(product: Product, queries: list[str], candidates: list[Candidate]) -> dict:
    return {
        "pair_key": product.pair_key,
        "supplier": product.supplier,
        "external_code": product.external_code,
        "name": product.name,
        "variant_codes": product.variant_codes,
        "queries": queries,
        "candidates": [{"name": c.name, "url": c.url} for c in candidates],
    }


def write_candidates(records: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def read_candidates(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def join_verdicts(recs: list[dict], verdicts: list[dict]) -> list[dict | None]:
    """Join AI verdicts (ai_verdicts.json) to candidate records (candidates.json)
    by the stable `pair_key` — NEVER by array position / `idx`.

    `idx` in a verdict is a display/order aid only (produced against whatever order
    candidates.json happened to have when the verdict was written). If candidates.json
    is later reordered or filtered — a re-gather, a supplier removed — a positional
    join (`{v["idx"]: v for v in verdicts}` + `verds.get(i)`, the pre-#43 bug) would
    silently attach a verdict to the WRONG product: a wrong supplier link feeds
    auto-ordering, a catastrophic blast radius. Keying on `pair_key` makes that
    impossible: a verdict only ever attaches to the exact product it was made for.

    Returns a list parallel to `recs`: result[i] is the verdict dict for recs[i], or
    None if no verdict claims that pair_key (same as "no verdict" in the old code).

    Every verdict MUST carry `pair_key` — a verdict without one gives no safe way to
    know which product it belongs to, so it is refused loudly (fail fast, never a
    silent positional guess). A verdict whose pair_key matches no record in `recs`
    is skipped with a warning — never applied to a different product by falling
    back to position (the pipeline's own philosophy: no link beats a wrong link).
    """
    by_key: dict[str, dict] = {}
    for v in verdicts:
        vk = v.get("pair_key")
        if not vk:
            raise SystemExit(
                f"ai_verdicts.json: verdikt bez pair_key (idx={v.get('idx')!r}) — "
                "pozičný join podľa idx je zakázaný (#43), doplň pair_key pri "
                "zápise verdiktu (napr. z verify_input.json)."
            )
        if vk in by_key:
            raise SystemExit(f"ai_verdicts.json: duplicitný pair_key {vk!r}")
        by_key[vk] = v

    result: list[dict | None] = [by_key.pop(r["pair_key"], None) for r in recs]

    for vk in by_key:
        print(f"  [WARN] verdikt pre neznámy pair_key {vk!r} preskočený "
              f"(nie je v candidates.json) — ignorovaný, nenapojený na iný produkt")

    return result
