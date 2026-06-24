# Verification Workflow (Fáza 2.5)

Input: data/out/match_report.csv (from the match phase).
Output: same file, with verdict / verdict_reason / attempts filled.

Per product row, an agent:
1. Fetch chosen_url (requests; Playwright if JS-rendered).
2. extract_page(html) -> title/code/price.
3. If product has external_code: code_verdict(). OK if code present -> accept.
4. Else (name match): judge whether the page's product is the same product as
   `name` (brand + model + type). Verdict OK / WRONG / UNSURE + reason.
5. Self-repair: if WRONG, take the next candidate from the search for that query
   (re-run client.search, pick the next-best not-yet-tried URL), go to step 1.
   Max 3 attempts. Record attempts count.
6. If finally WRONG/UNSURE with no good candidate: leave chosen_url but mark the
   verdict so the owner can review; optionally blank it in a stricter mode.

Orchestration: Workflow tool, pipeline over rows:
  stage1 = fetch+extract (parallel, capped concurrency, throttled per host)
  stage2 = judge (agent with schema {verdict, reason})
  stage3 = if WRONG -> re-search + retry (bounded)
Merge verdicts back with merge_verdict(), write with write_report_rows().
Then regenerate import CSV from rows whose verdict != WRONG (strict) or all
(loose) — owner's choice; default loose (auto-fill all) per spec.
