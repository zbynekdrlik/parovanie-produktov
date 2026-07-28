"""Nevyzdvihnuté zásielky — Pošta SK (#93): pure logic, no network / no SMTP.

Faithful port of the n8n workflow „Notifikácia nevyzdvihnutých zásielok - Pošta SK"
(2mhrdy0ouHe4VPeH, broken in n8n for ~a month), read node-by-node via MCP:

- source rows: orders with a non-empty packageNumber, newer than 30 days,
  deduped per order code (the app's own Shoptet orders export replaces the
  Google Sheet — it already carries packageNumber/email/phone/billFullName);
- tracking:   GET https://api.posta.sk/tracking?q=<pkg>&l=sk&p=1 per shipment;
- uncollected = LAST event stateCode=='notified' AND detailCode starts 'ZNP';
  post office + days-on-post extracted from the events, retainedTill from the
  RESULT (that is where the live API puts it — #283 — events are a fallback);
- escalation: max 4 customer e-mails, cadence day 0 → +3 → +3 → +7, state
  'count|YYYY-MM-DD' per order (data/out/posta_uncollected.json);
- ALL 4 e-mails go to the CUSTOMER (verbatim n8n HTML templates below);
  the 4th also flags '⚠️ TREBA ZAVOLAŤ' for the team (in-app badge — the
  n8n Discord channel is replaced by the app tab).

Why n8n silently broke: ~1/5 of shipments have 13-14 digit NUMERIC package
numbers (a different carrier label) → the API answers per-result
status:'invalid_format' with no events; continueOnFail hid that as success.
Here those are surfaced explicitly (invalid=True) so the tab can show them.

Carrier filter (#126): the automation covers Pošta SK only — DPD (and other
non-Pošta couriers) are excluded from the shipment source entirely, not shown
as "nesledovateľné". Live export check (2026-07-22, 523 orders): the SHIPPING
pseudo-item's itemName is NEVER literally "Slovenská pošta" — Pošta SK home
delivery is labelled "Kuriér" (~98% of volume, EF...SK tracking numbers), so
the filter is a BLOCKLIST of recognised non-Pošta carrier names (DPD + common
SK couriers), not an allowlist matching "pošta" (that would have excluded
almost every real Pošta shipment). An order with no SHIPPING row at all
(older/partial export) fails OPEN (kept) — never silently drops a shipment.

The Flask app (webreview/app.py) wires this to the network, SMTP and stores.
"""
import csv
import io
from datetime import date, timedelta
from html import escape

from parovanie.export_helpers import norm_status

# Non-Pošta courier names recognised in the SHIPPING pseudo-item's itemName
# (case-insensitive substring match). DPD is the one confirmed live (#126);
# the rest are common SK couriers kept out defensively per the issue's "DPD +
# other couriers" wording.
#
# PERSONAL PICKUP (#287) is on this list too, even though it is not a courier: there is no
# parcel at all, so there is nothing for this automation to track. Live export (28.7.2026)
# carries exactly four SHIPPING names — "Kuriér" (444 orders, = Pošta SK home delivery),
# "DPD doručenie na adresu" (53), "Osobný odber - len na predajni v POPRADE!" (24) and "DPD
# kuriér" (1) — so 8 pickups sat inside the 30-day eligible set and 3 of them counted towards
# the coverage alarm's denominator, where a package number can never appear. Today that costs
# only accuracy (a pickup has no number, so it never reaches a tracking call); the moment one
# is typed into Shoptet by mistake the automation starts chasing a parcel that does not exist.
#
# The keyword is the two-word PHRASE, and both spellings of it, on purpose. A bare "odber"
# would also match a real delivery service ("odberné miesto") — and a wrongly excluded
# shipment is a customer who is never told their parcel is waiting, which is the exact failure
# this automation exists to prevent. Matching is `.lower()` only, with no diacritic folding,
# so the unaccented spelling is listed rather than assumed.
NON_POSTA_CARRIER_KEYWORDS = (
    "dpd", "gls", "packeta", "zásielkov", "zasielkov",
    "in time", "intime", "wedo", "spservis",
    "osobný odber", "osobny odber",
)

TRACKING_API = "https://api.posta.sk/tracking?q={q}&l=sk&p=1"
TRACKING_LINK = "https://www.posta.sk/sledovanie-zasielok#parcel={q}"
# Orders export has no internal admin order id (the n8n Sheet had one). The one
# GET deep-link the Shoptet admin supports is the global search — verified live
# 2026-07-22: /admin/vyhladavanie/?string=<code>&src=orders returns exactly that
# order with its detail link (the overview's filter is POST-only; ?query=/?code=
# are silently ignored).
ADMIN_ORDER_LINK = ("https://www.forestshop.sk/admin/vyhladavanie/"
                    "?string={code}&src=orders")
SOURCE_WINDOW_DAYS = 30
MAX_EMAILS = 4

# The two order statuses this automation has to recognise. Both are DEFAULTS: since #296 the web
# app passes the sets the manager configured on the „Sync zo Shoptetu" card (`order_statuses.json`,
# #209), because Shoptet's status names are a text field the shop owner edits and a rename used to
# reach this module unchanged. Measured read-only on the live export (28.7.2026): renaming
# „Vybavená" took `dispatched_orders` from 89 to 0 and flipped `degraded` from True to FALSE — the
# alarm built against silent death went quietly green over a genuinely dead source; renaming
# „Stornovaná" put 16 cancelled orders back into the set this automation chases.
#
# They stay here so the module remains standalone and unit-testable, and so a caller that passes
# nothing behaves exactly as it did before #296.
#
#   DISPATCHED — „už odoslaná": the only orders that MUST already carry a package number. An order
#     still being picked („Vybavuje sa") legitimately has none yet, and counting those would light
#     the coverage alarm below every single day. The app derives this set as `terminal − cancelled`
#     rather than offering a box of its own — see webreview/app.py `_posta_statuses`.
#   CANCELLED — never nag a customer who cancelled (#93). It is a subset of the app's `terminal`
#     set, and the app enforces that at both ends so the two can never drift apart.
#
# Names are compared through `export_helpers.norm_status` (NFC + strip) on BOTH sides — the trap
# #296 names: this module used to lowercase, while the configuration carries the exact names the
# manager typed, and a decomposed name is byte-different, renders identically and matches nothing.
DISPATCHED_STATUSES = frozenset({"Vybavená"})
CANCELLED_STATUSES = frozenset({"Stornovaná"})

# ── the coverage alarm (#282) ────────────────────────────────────────────────────────────────
# The ONLY source of shipments is the export's `packageNumber` column. On 2026-07-02 it stopped
# being filled and the daily run went on reporting `ok` with a quietly shrinking `checked`
# (21 → 13 → 9 → 6 → 4 over five days) while a real parcel sat at the post office until its
# pickup deadline: `checked` counts the orders that DID carry a number, so a source that stops
# feeding them looks like a calm day, not a failure. Both thresholds below are calibrated on the
# real history (read-only recount of the live orders export, 2026-07-27), not guessed:
#
#   coverage — dispatched orders carrying a number: 90.3 % in May, 72.2 % in June; the worst
#     rolling 30-day window in that healthy period was 73.2 %. Tonight: 4.4 %. A 50 % floor
#     therefore has ~23 points of headroom under the worst healthy reading. It is deliberately
#     not tighter: even in a healthy June ~27 % of dispatched orders have no number, so a
#     stricter floor would alarm on normal trading noise.
#   staleness — the longest gap between two days bringing a new package number in 1.5.–1.7. was
#     3 days (54 such days, gaps of 1/2/3). Tonight: 26 days. A 7-day limit is over twice the
#     worst healthy gap. It is measured from the newest DISPATCHED order, never from today, so a
#     week when nothing shipped at all cannot trip it — see source_coverage's docstring.
#
# Against the real outage (source died 1.7.) staleness would have gone red on 8.7. and coverage
# around 10.7. — 17-19 days before the deadline of the shipment that was actually lost.
#
# The third constant is the evidence floor. Roughly 27 % of dispatched orders legitimately carry
# no number even in a healthy month, so on a tiny window both rules above are noise: with a single
# dispatched order the odds of „no numbers at all" happening innocently are 0.27, with two 0.073,
# with three 0.02 — a card that cried wolf at those odds would be trained away within a week. At
# five they are 0.0014 (~1 window in 700), which is where the verdict becomes worth acting on.
# The real shop runs 91-157 dispatched orders per window, so this floor costs no detection power
# whatsoever against the failure it exists for; it only keeps a near-empty window quiet.
MIN_PACKAGE_COVERAGE = 0.5
MAX_PACKAGE_GAP_DAYS = 7
MIN_DISPATCHED_FOR_ALARM = 5


def _parse_date(s) -> date | None:
    """Lenient 'YYYY-MM-DD...' prefix → date; None (never raises) on junk."""
    try:
        return date.fromisoformat(str(s or "").strip()[:10])
    except ValueError:
        return None


def _order_carriers(rows: list[dict]) -> dict[str, str]:
    """order code -> SHIPPING pseudo-item name (itemCode starting 'SHIPPING'),
    scanning ALL rows of the export — the SHIPPING line need not be the first
    row per order. An order with no such row is simply absent (caller treats
    that as 'unknown carrier' -> fail-open, kept)."""
    out: dict[str, str] = {}
    for r in rows:
        code = (r.get("code") or "").strip()
        item_code = (r.get("itemCode") or "").strip()
        if code and item_code.upper().startswith("SHIPPING"):
            out[code] = (r.get("itemName") or "").strip()
    return out


def _is_non_posta_carrier(name: str) -> bool:
    """True when the SHIPPING itemName names a recognised non-Pošta courier
    (see NON_POSTA_CARRIER_KEYWORDS). Empty/unknown name -> False (fail-open)."""
    n = (name or "").lower()
    return any(k in n for k in NON_POSTA_CARRIER_KEYWORDS)


def _status_set(statuses, default: frozenset) -> frozenset:
    """A caller-supplied set of status names → the ONE form both sides compare in, or the
    module default when the caller passed nothing. `None` and the default are deliberately
    different from an EMPTY set: the app never sends an empty one (its endpoint and loader
    both refuse it), so an empty set here means „the caller really means nothing matches"."""
    if statuses is None:
        return default
    return frozenset(norm_status(s) for s in statuses) - {""}


def _eligible_orders(orders_csv, today: date | None = None,
                     cancelled_statuses=None) -> list[tuple[dict, date, str]]:
    """Every order this automation is responsible for, WITHOUT the packageNumber filter →
    [(row, order_date, packageNumber), …].

    The shared definition of „naša objednávka": first row per order code wins (the export repeats
    order fields on every item line), the order is not cancelled, it did not ship with a non-Pošta
    courier, and its date is inside the 30-day source window. Both the shipment source below and
    the coverage alarm read it, so the alarm can never end up counting a different set of orders
    than the automation actually chases.

    `cancelled_statuses` defaults to CANCELLED_STATUSES — see there for why it is configuration."""
    text = (orders_csv.decode("cp1250", errors="replace")
            if isinstance(orders_csv, bytes) else orders_csv)
    today = today or date.today()
    cancelled = _status_set(cancelled_statuses, CANCELLED_STATUSES)
    cutoff = today - timedelta(days=SOURCE_WINDOW_DAYS)
    rows = list(csv.DictReader(io.StringIO(text), delimiter=";"))
    carriers = _order_carriers(rows)
    out, seen = [], set()
    for r in rows:
        code = (r.get("code") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        if norm_status(r.get("statusName")) in cancelled:
            continue
        if _is_non_posta_carrier(carriers.get(code, "")):
            continue
        od = _parse_date(r.get("date"))
        if od is None or od < cutoff:
            continue
        out.append((r, od, (r.get("packageNumber") or "").strip()))
    return out


def shipments_from_orders_csv(orders_csv, today: date | None = None,
                              cancelled_statuses=None) -> list[dict]:
    """Shoptet orders.csv (cp1250 bytes or str) → one shipment per ORDER.

    Port of the n8n 'Filter' + 'Remove Duplicates' nodes: packageNumber
    non-empty AND order date within the last 30 days, first row per order code
    wins (the export repeats order fields on every item line). One deliberate
    deviation from n8n: a cancelled order (`cancelled_statuses`, „Stornovaná" by
    default) is skipped — nagging a customer who cancelled would be wrong
    (documented on #93; configurable since #296). Second
    deviation (#126): orders shipped via a non-Pošta courier (DPD etc., see
    _is_non_posta_carrier) are excluded entirely — this automation is Pošta
    SK only."""
    return [{
        "code": (r.get("code") or "").strip(),
        "date": od.isoformat(),
        "packageNumber": pkg,
        "email": (r.get("email") or "").strip(),
        "phone": (r.get("phone") or "").strip(),
        "billFullName": (r.get("billFullName") or "").strip(),
    } for r, od, pkg in _eligible_orders(orders_csv, today, cancelled_statuses) if pkg]


def source_coverage(orders_csv, today: date | None = None,
                    cancelled_statuses=None, dispatched_statuses=None) -> dict:
    """Is the shipment SOURCE still alive? → counts + a `degraded` verdict (#282).

    Everything here is counted over the same eligible orders the automation chases
    (_eligible_orders). `degraded` is True when the window holds enough dispatched orders AND
    either fewer than MIN_PACKAGE_COVERAGE of them carry a package number, or we have gone on
    DISPATCHING for MAX_PACKAGE_GAP_DAYS since the last number arrived (calibration: see each
    constant).

    Note what the staleness rule measures: the gap between the newest DISPATCHED order and the
    newest order carrying a number — NOT the gap to today. Measured against today it would trip on
    any quiet stretch (shop holiday, a slow week): orders dispatched earlier stay in the 30-day
    window, nothing new ships, and a perfectly healthy source reads as dead. Against the newest
    dispatched order it says the thing we actually mean — „we keep sending parcels out and no
    number has come back" — which is exactly the outage, and stays silent when nothing shipped at
    all. A window with NO numbers whatsoever needs no staleness rule: 0 % coverage already trips.

    Three things it deliberately does NOT do. It never degrades on a window holding fewer than
    MIN_DISPATCHED_FOR_ALARM dispatched orders — too little evidence to tell a dead source from a
    quiet shop; it never counts orders that are still being picked, which have no number yet for
    entirely normal reasons; and it never touches the escalation. This is a pure counter: it reads
    the export, sends nothing, and never widens the set of shipments that get e-mailed."""
    today = today or date.today()
    eligible = _eligible_orders(orders_csv, today, cancelled_statuses)
    wanted = _status_set(dispatched_statuses, DISPATCHED_STATUSES)
    dispatched = [(od, pkg) for r, od, pkg in eligible
                  if norm_status(r.get("statusName")) in wanted]
    with_pkg = sum(1 for _, pkg in dispatched if pkg)
    # Only orders dated TODAY OR EARLIER count, for the numbers AND for the dispatch reference. A
    # future-dated row (a mistyped order date, a clock jump on the export side) would otherwise
    # make a gap NEGATIVE — which renders as nonsense („pred -3 dňami") and, far worse, reads as
    # fresher than any threshold, silently switching the staleness rule off for as long as that
    # row stays in the window. Same fail-safe the terminal cache uses for its `at` stamp: a date
    # we cannot believe degrades to „no evidence", never to „everything is fine".
    pkg_dates = [od for _, od, pkg in eligible if pkg and od <= today]
    last_pkg = max(pkg_dates) if pkg_dates else None
    disp_dates = [od for od, _ in dispatched if od <= today]
    last_dispatch = max(disp_dates) if disp_dates else None
    out = {
        # Every order the automation is responsible for in this window. This is the number
        # `dispatched_status_unknown` below actually triggers on, so it has to be published:
        # without it the blind-spot ERROR could only approximate the window size, and in the one
        # branch that fires (nothing dispatched) every other count here is 0 or numberless-only —
        # which is how it came to report „v okne je 0 objednávok, ale ANI JEDNA nemá stav
        # Vybavená" over six orders that all carried a number.
        "eligible_orders": len(eligible),
        "dispatched_orders": len(dispatched),
        "dispatched_with_package": with_pkg,
        "dispatched_without_package": len(dispatched) - with_pkg,
        # every eligible order with no number, dispatched or not — reported for context, NOT used
        # as the rule (the not-yet-shipped ones are a normal, permanent part of it)
        "missing_package": sum(1 for _, _, pkg in eligible if not pkg),
        # for the human reading the banner: „when did we last see a number at all". The RULE uses
        # the dispatch-relative gap below, which is a different (and fairer) question.
        "days_since_last_package": (today - last_pkg).days if last_pkg else None,
        # The alarm's own blind spot, surfaced rather than hidden: every count here hangs off the
        # dispatched status NAMES. Since #296 those come from the manager's configuration rather
        # than from a literal in this file, so a rename is now a one-line edit on the card — but
        # the signal stays, because a name can still be edited WRONG (a typo, a status renamed in
        # Shoptet and not here). If the vocabulary stops matching, `dispatched_orders` silently
        # falls to 0 and this alarm — built against silent death — would itself go quietly green
        # forever. Eligible orders but not one of them dispatched is the signature.
        "dispatched_status_unknown": (len(eligible) >= MIN_DISPATCHED_FOR_ALARM
                                      and not dispatched),
        "degraded": False,
    }
    if len(dispatched) >= MIN_DISPATCHED_FOR_ALARM:
        thin = with_pkg / len(dispatched) < MIN_PACKAGE_COVERAGE
        stale = (last_pkg is not None and last_dispatch is not None
                 and (last_dispatch - last_pkg).days >= MAX_PACKAGE_GAP_DAYS)
        out["degraded"] = thin or stale
    return out


def classify_tracking(api_json, today: date | None = None) -> dict:
    """Pošta SK tracking response → classification (port of the n8n Code node's
    detection half). status: 'ok' | 'invalid_format' | 'no_results' |
    'no_events' | whatever the API said per-result."""
    today = today or date.today()
    out = {"status": "no_results", "uncollected": False, "office_name": "",
           "office_addr": "", "retained_till": "", "notified_since": "",
           "days_at_post": 1}
    results = (api_json or {}).get("results") or []
    if not results:
        return out
    p = results[0] or {}
    out["status"] = p.get("status") or "no_results"
    if out["status"] != "ok":
        return out                      # invalid_format lands here (no events)
    events = p.get("events") or []
    if not events:
        out["status"] = "no_events"
        return out
    last = events[-1]
    state = (last.get("stateCode") or "").lower()
    detail = (last.get("detailCode") or "").upper()
    if state == "notified" and detail.startswith("ZNP"):
        out["uncollected"] = True
        po = last.get("postOffice") or {}
        out["office_name"] = po.get("name") or ""
        if po.get("street"):
            out["office_addr"] = (f"{po['street']}, {po.get('postcode', '')} "
                                  f"{po.get('city', '')}").strip()
        ld = (last.get("localDate") or "")[:10]
        out["notified_since"] = ld
        nd = _parse_date(ld)
        if nd is not None:              # unparsable date keeps the n8n default of 1
            out["days_at_post"] = max(1, (today - nd).days)
        # The pickup deadline. The LIVE api.posta.sk returns it on the RESULT (#283) — no event
        # ever carries it — so the result is read first. The per-event lookup below is what the
        # ported n8n workflow did and stays as a fallback: we have one live sample of the
        # result-level shape, and this field only ever shapes the mail's wording (it never
        # decides whether a mail goes out), so honouring BOTH shapes costs nothing and cannot be
        # wrong-footed by whichever one the API answers with. Empty here is not harmless: it
        # silently downgrades „vyzdvihnite si ju do <dátum>" to a vague „čo najskôr", which is
        # what the customer of the shipment behind #283 was told instead of their real deadline.
        raw_till = p.get("retainedTill") or next(
            (e["retainedTill"] for e in events if e.get("retainedTill")), "")
        if raw_till:
            # Normalised to a bare YYYY-MM-DD: we have one live sample of the result-level shape,
            # so a timestamped value („2026-08-03T00:00:00") is entirely possible and would
            # otherwise land verbatim in a customer's e-mail.
            #
            # Anything we cannot read as a date is DROPPED, not kept for display. This field is a
            # DATE, and the mail template hard-codes the „…vyzdvihnite si ju do <hodnota>" prefix
            # around it, so an unrecognised string reads as nonsense at a real customer („do do
            # odvolania", „do True"). It is not merely cosmetic either: build_email's „deadline
            # already passed" guard runs the SAME parser, so a value the parser rejects is
            # silently EXEMPT from it — a garbled and long-expired date would be presented as if
            # it were still ahead. Fail-soft for a date field is the dateless „čo najskôr"
            # wording, which is always true; a string we do not understand is not.
            d = _parse_date(raw_till)
            out["retained_till"] = d.isoformat() if d else ""
    return out


# A shipment in one of these states can never change again, so its tracking never
# has to be fetched a second time (#222 — the daily run re-queried every parcel in
# the 30-day window, including long-delivered ones, sequentially at up to 180 s each:
# 60 s timeout × 3 tries).
#
# ONLY live-verified state codes belong here. A live probe of api.posta.sk over the
# real shipment set (2026-07-25) returned exactly four codes — received, transit,
# notified, delivered — and showed that 'delivered' covers BOTH outcomes that end an
# escalation: „Doručená" (detailCode OK, home delivery) AND „Prevzatá na pošte"
# (detailCode OKP — the customer finally collected it, i.e. the natural end of the
# uncollected-parcel chase; see tests/fixtures/posta/tracking_collected_at_office.json,
# a real anonymized response whose events go notified/ZNP1AN → delivered/OKP).
#
# 'notified' is deliberately ABSENT: that is the state this automation exists to
# chase, and it still changes. A 'returned' code was NOT observed and is therefore
# NOT trusted (#226): if Pošta SK were to use it for „vrátená na dodaciu poštu" — a
# parcel that is back at the office and still collectible — caching it as final would
# silently freeze a genuinely uncollected shipment out of the escalation and the
# customer would never be told. Anything unrecognised is NOT terminal (fail-safe:
# keep checking rather than act on a guess about the API's vocabulary).
TERMINAL_STATE_CODES = frozenset({"delivered"})


def terminal_state(api_json) -> str:
    """The FINAL tracking state of a shipment ('delivered' — delivered at home OR collected at
    the post office), or '' when the shipment can still change and must be re-checked. Never
    raises: any unexpected shape (no results, a per-result status other than 'ok' such as
    invalid_format, no events, an unknown stateCode) reads as 'not final'."""
    if not isinstance(api_json, dict):
        return ""
    results = api_json.get("results") or []
    if not results:
        return ""
    p = results[0] or {}
    if (p.get("status") or "") != "ok":
        return ""                       # invalid_format & friends: nothing final to cache
    events = p.get("events") or []
    if not events:
        return ""
    state = str((events[-1] or {}).get("stateCode") or "").strip().lower()
    return state if state in TERMINAL_STATE_CODES else ""


def parse_notified(value) -> tuple[int, date | None]:
    """Escalation state 'count|YYYY-MM-DD' → (count, last_sent). Legacy n8n
    value (bare date) counts as one notification; junk counts as none."""
    v = str(value or "").strip()
    if not v:
        return 0, None
    if "|" in v:
        c, _, d = v.partition("|")
        try:
            count = int(c)
        except ValueError:
            count = 0
        return count, _parse_date(d)
    d = _parse_date(v)
    return (1, d) if d is not None else (0, None)


def should_send(count: int, last_sent: date | None, today: date | None = None) -> bool:
    """n8n cadence: 4 mails max, day 0 → +3 → +3 → +7."""
    today = today or date.today()
    if count >= MAX_EMAILS:
        return False
    if count == 0:
        return True
    if last_sent is None:
        return False
    needed = 3 if count < 3 else 7
    return (today - last_sent).days >= needed


def build_email(count: int, name: str, track_num: str, office_name: str,
                office_addr: str, retained_till: str,
                today: date | None = None) -> tuple[str, str]:
    """(subject, html_body) for customer e-mail #count — the verbatim n8n
    templates. Free-text fields (customer name, office) are HTML-escaped here
    (the one hardening added over n8n).

    A retention date that has already PASSED — or that cannot be read as a date at all — is
    treated as no date. Until #283 the field was never populated, so every mail used the dateless
    wording; now that it is read correctly, a parcel first seen late in its retention (or chased
    through the day 0 → +3 → +3 → +7 cadence) could otherwise be told „ak si ju nevyzdvihnete do
    <a date last week>". The dateless „čo najskôr" wording is the honest thing to send once the
    deadline is behind us — or whenever we do not actually know it.

    The unparsable case is checked HERE and not only in classify_tracking because this builder has
    a second caller: the preview endpoint hands it `retained_till` straight out of the JSON store,
    so a garbled value written by an older run would still reach a customer. And because the
    expiry check below is the same parser, an unreadable value would be silently exempt from it —
    i.e. a garbled AND long-expired deadline would be presented as if it were still ahead.

    A named deadline is rendered the way a Slovak reader writes a date („3. 8. 2026"), never the
    ISO form the API, the JSON store and the web table use internally."""
    today = today or date.today()
    # Unconditional, so `retained_till` is ALWAYS a str from here on: the preview endpoint reads
    # this argument out of the JSON store, where a corrupt entry could be any type at all, and a
    # TypeError in escape() below would take the whole daily run (and the customer's mail) with it.
    d = _parse_date(retained_till) if retained_till else None
    retained_till = f"{d.day}. {d.month}. {d.year}" if d is not None and d >= today else ""
    link = TRACKING_LINK.format(q=track_num)
    name_h = escape(name)
    num_h = f"<strong>{escape(track_num)}</strong>"
    office_h = f"<strong>{escape(office_name)}</strong>" + (
        f" ({escape(office_addr)})" if office_addr else "")
    till_h = f"<strong>{escape(retained_till)}</strong>"
    if count == 1:
        subject = f"Vaša zásielka čaká na vyzdvihnutie | {track_num}"
        intro = (f"vaša zásielka č. {num_h} je uložená na pošte {office_h} "
                 "a čaká na vyzdvihnutie.")
        urgency = (f"Prosím vyzdvihnite si ju do {till_h}, aby nebola vrátená späť odosielateľovi."
                   if retained_till else
                   "Prosím vyzdvihnite si ju čo najskôr, aby nebola vrátená späť odosielateľovi.")
    elif count == 2:
        subject = f"Pripomienka: zásielka stále čaká | {track_num}"
        intro = (f"opätovne vás upozorňujeme, že vaša zásielka č. {num_h} je stále "
                 f"uložená na pošte {office_h} a zatiaľ nebola vyzdvihnutá.")
        urgency = (f"Termín na vyzdvihnutie sa blíži: {till_h}. Po tomto dátume bude zásielka vrátená."
                   if retained_till else
                   "Prosím vyzdvihnite si ju čo najskôr. Zásielka bude čoskoro vrátená odosielateľovi.")
    elif count == 3:
        subject = f"Posledné upozornenie: zásielka bude vrátená | {track_num}"
        intro = (f"toto je naše posledné upozornenie — vaša zásielka č. {num_h} "
                 f"je stále na pošte {office_h}.")
        urgency = (f"Ak si ju nevyzdvihnete do {till_h}, bude vrátená späť odosielateľovi."
                   if retained_till else
                   "Ak si ju čoskoro nevyzdvihnete, bude vrátená späť odosielateľovi.")
    else:
        subject = f"Posledná výzva: zásielka bude vrátená | {track_num}"
        intro = (f"napriek opakovaným upozorneniam vaša zásielka č. {num_h} "
                 f"je stále nevyzdvihnutá na pošte {office_h}.")
        urgency = ("Zásielka bude v najbližších dňoch vrátená späť. Ak ju stále chcete, "
                   'prosím kontaktujte nás čo najskôr na '
                   '<a href="mailto:eshop@forestshop.sk">eshop@forestshop.sk</a> '
                   "alebo telefonicky.")
    body = (
        '<!DOCTYPE html>\n<html>\n'
        '  <body style="font-family: Arial, sans-serif; font-size: 16px; color: #333;">\n'
        f'    <p>Dobrý deň, <strong>{name_h}</strong>,</p>\n\n'
        f'    <p>{intro}</p>\n\n'
        f'    <p>{urgency}</p>\n\n'
        f'    <p>👉 Stav zásielky môžete sledovať tu: <a href="{link}">{link}</a></p>\n\n'
        '    <p>\n'
        '      Ak máte akékoľvek otázky, pokojne nás kontaktujte na\n'
        '      <a href="mailto:eshop@forestshop.sk">eshop@forestshop.sk</a>.\n'
        '    </p>\n\n'
        '    <p style="margin-top: 30px;">\n'
        '      S pozdravom,<br>\n'
        '      <strong>Tím Forestshop.sk</strong><br>\n'
        '      <a href="https://www.forestshop.sk" target="_blank">www.forestshop.sk</a>\n'
        '    </p>\n'
        '  </body>\n</html>'
    )
    return subject, body


def evaluate_shipment(shipment: dict, tracking_json, state_value,
                      today: date | None = None) -> dict:
    """One shipment + its tracking response + its escalation state → the full
    verdict: display row for the tab, whether to e-mail now (with the prepared
    subject/body), and the new escalation state value."""
    today = today or date.today()
    cls = classify_tracking(tracking_json, today)
    count, last_sent = parse_notified(state_value)
    send = cls["uncollected"] and should_send(count, last_sent, today)
    new_count = count + 1 if send else count
    r = {
        "orderCode": shipment["code"],
        "packageNumber": shipment["packageNumber"],
        "email": shipment.get("email", ""),
        "phone": shipment.get("phone", ""),
        "name": shipment.get("billFullName", ""),
        "status": cls["status"],
        "uncollected": cls["uncollected"],
        "invalid": cls["status"] == "invalid_format",
        "office_name": cls["office_name"],
        "office_addr": cls["office_addr"],
        "retained_till": cls["retained_till"],
        "notified_since": cls["notified_since"],
        "days_at_post": cls["days_at_post"],
        "send": send,
        "count": new_count,
        "last_sent": today.isoformat() if send else
                     (last_sent.isoformat() if last_sent else ""),
        "new_state_value": (f"{new_count}|{today.isoformat()}" if send
                            else str(state_value or "")),
        "call_needed": cls["uncollected"] and new_count >= MAX_EMAILS,
        "tracking_link": TRACKING_LINK.format(q=shipment["packageNumber"]),
        "admin_link": ADMIN_ORDER_LINK.format(code=shipment["code"]),
        "email_subject": "",
        "email_body": "",
    }
    if send:
        r["email_subject"], r["email_body"] = build_email(
            new_count, r["name"], r["packageNumber"],
            cls["office_name"], cls["office_addr"], cls["retained_till"], today)
    return r
