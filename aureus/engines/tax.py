"""GST reconciliation engine: purchase register vs GSTR-2B.

Every purchase-register invoice ends in one of three statuses (PRD Module B):

* MATCHED_ELIGIBLE  - found in GSTR-2B, every amount within ₹0.99, ITC available
* MISMATCH_WARN     - found (or probably found), but something disagrees
* MISSING_IN_PORTAL - the supplier has not uploaded it, so the ITC is frozen

`reason` refines MISMATCH_WARN (which field disagrees and how it was paired).
GSTR-2B entries with no booked invoice are returned separately as
`portal_only`: credit the business can claim once it books the bill.

All arithmetic is in integer paise so the ₹0.99 threshold is exact.
"""

import re
from collections import defaultdict
from datetime import date

from .. import config, hsn as hsn_mod
from ..gstin import is_valid, state_code
from ..models import Invoice
from . import deadlines

MATCHED = "MATCHED_ELIGIBLE"
MISMATCH = "MISMATCH_WARN"
MISSING = "MISSING_IN_PORTAL"

HEADS = ("taxable_value", "igst", "cgst", "sgst", "cess")
TAX_HEADS = ("igst", "cgst", "sgst", "cess")

# HSN/SAC prefixes whose credit is commonly blocked by Section 17(5). Flagged for review, not auto-denied.
BLOCKED_PREFIXES = {
    "8703": "Motor vehicles for passenger transport (Sec 17(5)(a))",
    "8711": "Motorcycles (Sec 17(5)(a))",
    "2202": "Beverages (Sec 17(5)(b)(i))",
    "2203": "Beverages (Sec 17(5)(b)(i))",
    "2204": "Beverages (Sec 17(5)(b)(i))",
    "2208": "Beverages (Sec 17(5)(b)(i))",
    "9963": "Food, catering and accommodation services (Sec 17(5)(b)(i))",
}

REASON_TEXT = {
    "EXACT": "All amounts agree within ₹0.99",
    "VALUE_MISMATCH": "Taxable value or tax amount differs from the supplier's upload",
    "TAX_HEAD_MISMATCH": "Same total tax, but booked under a different head (IGST vs CGST+SGST)",
    "DATE_MISMATCH": "Amounts agree but the invoice date differs",
    "INVOICE_NO_MISMATCH": "Supplier uploaded it under a slightly different invoice number",
    "SUPPLIER_GSTIN_MISMATCH": "Invoice found under a different supplier GSTIN",
    "ITC_NOT_AVAILABLE": "GSTR-2B marks this credit as not available",
    "NOT_UPLOADED": "Supplier has not uploaded this invoice in GSTR-1/IFF",
}

ACTIONS = {
    "VALUE_MISMATCH": "Ask the supplier to amend the invoice in GSTR-1A/next GSTR-1, or correct your books if they are wrong.",
    "TAX_HEAD_MISMATCH": "Wrong tax type was charged. Supplier must issue a fresh invoice with the correct head; claim refund of the wrong tax (Sec 77).",
    "DATE_MISMATCH": "Confirm the date with the supplier; correct whichever side is wrong before filing.",
    "INVOICE_NO_MISMATCH": "Ask the supplier to amend the invoice number via GSTR-1A, or align your books to the portal reference.",
    "SUPPLIER_GSTIN_MISMATCH": "Check the supplier's GSTIN in your ledger master and correct it.",
    "ITC_NOT_AVAILABLE": "Do not claim this credit in GSTR-3B; check the reason shown in GSTR-2B.",
    "NOT_UPLOADED": "Hold the credit and send the supplier a reminder to upload before the GSTR-3B due date.",
}


def paise(amount: float) -> int:
    return int(round(amount * 100))


def rupees(value: int) -> float:
    return round(value / 100, 2)


def normalize_invoice_no(value: str) -> str:
    """'INV/0042 ' -> 'INV42'. Drops separators, case and leading zeros in numeric runs."""
    text = re.sub(r"(?<![0-9])0+(?=[0-9])", "", (value or "").upper())
    return re.sub(r"[^A-Z0-9]", "", text)


def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def _key(inv: Invoice) -> tuple[str, str]:
    return inv.supplier_gstin.strip().upper(), normalize_invoice_no(inv.invoice_no)


def _variance(books: Invoice, portal: Invoice) -> dict[str, int]:
    return {head: paise(getattr(portal, head)) - paise(getattr(books, head)) for head in HEADS}


def _tax(inv: Invoice) -> int:
    return sum(paise(getattr(inv, h)) for h in TAX_HEADS)


def _flags(books: Invoice, buyer_state: str, today: date) -> list[dict]:
    flags = []
    if not is_valid(books.supplier_gstin):
        flags.append({"code": "INVALID_GSTIN", "text": "Supplier GSTIN in books fails the checksum"})
    supplier_state = state_code(books.supplier_gstin)
    pos = books.place_of_supply or buyer_state
    intra = supplier_state == pos
    if books.igst and intra:
        flags.append({"code": "WRONG_TAX_TYPE", "text": "Intra-state supply charged IGST; should be CGST+SGST"})
    if (books.cgst or books.sgst) and not intra and supplier_state:
        flags.append({"code": "WRONG_TAX_TYPE", "text": "Inter-state supply charged CGST+SGST; should be IGST"})
    if books.reverse_charge:
        flags.append({"code": "REVERSE_CHARGE", "text": "Reverse charge: pay the tax in cash before claiming this credit"})
    for line in books.lines:
        code = hsn_mod.describe(line.hsn)
        if code["status"] == "missing":
            flags.append({"code": "HSN_MISSING", "text": f"{line.description or 'Item'}: no HSN code, so it cannot be "
                                                        "checked for blocked credit or matched to subsidies"})
        elif code["status"] in ("unknown", "unknown_heading"):
            flags.append({"code": "HSN_UNKNOWN", "text": f"{line.description or 'Item'}: HSN {line.hsn} is not a valid "
                                                        "HS heading; check the item master"})
        for prefix, why in BLOCKED_PREFIXES.items():
            if line.hsn.startswith(prefix):
                flags.append({"code": "POSSIBLY_BLOCKED", "text": f"{line.description or line.hsn}: {why}"})
    limit = deadlines.itc_time_limit(books.invoice_date)
    days = (limit - today).days
    if days < 0:
        flags.append({"code": "TIME_BARRED", "text": f"Sec 16(4) limit passed on {limit.isoformat()}"})
    elif days <= 60:
        flags.append({"code": "TIME_LIMIT_NEAR", "text": f"Claim before {limit.isoformat()} (Sec 16(4))"})
    return flags


def _pair_result(books: Invoice, portal: Invoice, basis: str, tolerance: int) -> dict:
    variance = _variance(books, portal)
    off = {h: v for h, v in variance.items() if abs(v) > tolerance}
    books_tax, portal_tax = _tax(books), _tax(portal)
    if basis == "fuzzy_invoice_no":
        reason = "INVOICE_NO_MISMATCH"
    elif basis == "gstin_corrected":
        reason = "SUPPLIER_GSTIN_MISMATCH"
    elif off and set(off) <= set(TAX_HEADS) and abs(books_tax - portal_tax) <= tolerance:
        reason = "TAX_HEAD_MISMATCH"
    elif off:
        reason = "VALUE_MISMATCH"
    elif not portal.itc_available:
        reason = "ITC_NOT_AVAILABLE"
    elif books.invoice_date != portal.invoice_date:
        reason = "DATE_MISMATCH"
    else:
        reason = "EXACT"
    status = MATCHED if reason == "EXACT" else MISMATCH

    if not portal.itc_available or reason == "TAX_HEAD_MISMATCH":
        claimable = 0
    elif status == MATCHED:
        claimable = books_tax
    else:
        claimable = max(0, min(books_tax, portal_tax))
    return {
        "status": status,
        "reason": reason,
        "match_basis": basis,
        "variance": {h: rupees(v) for h, v in variance.items()},
        "max_variance": rupees(max((abs(v) for v in variance.values()), default=0)),
        "itc_books": rupees(books_tax),
        "itc_portal": rupees(portal_tax),
        "itc_claimable": rupees(claimable),
        "itc_at_risk": rupees(max(0, books_tax - claimable)),
    }


def reconcile(books: list[Invoice], portal: list[Invoice], buyer_state: str,
              today: date | None = None, tolerance_paise: int | None = None) -> dict:
    today = today or date.today()
    tolerance = config.MATCH_TOLERANCE_PAISE if tolerance_paise is None else tolerance_paise

    portal_by_key: dict[tuple, list[int]] = defaultdict(list)
    for idx, inv in enumerate(portal):
        portal_by_key[_key(inv)].append(idx)
    used: set[int] = set()
    pairs: dict[int, tuple[int, str]] = {}

    # Pass 1: composite key (GSTIN + invoice number), raw then normalised.
    for b_idx, inv in enumerate(books):
        for p_idx in portal_by_key.get(_key(inv), []):
            if p_idx not in used:
                raw_same = portal[p_idx].invoice_no.strip().upper() == inv.invoice_no.strip().upper()
                pairs[b_idx] = (p_idx, "exact" if raw_same else "normalized_invoice_no")
                used.add(p_idx)
                break

    # Pass 2: same supplier, near-identical invoice number and amount (supplier typo).
    for b_idx, inv in enumerate(books):
        if b_idx in pairs:
            continue
        b_no = normalize_invoice_no(inv.invoice_no)
        for p_idx, p in enumerate(portal):
            if p_idx in used or p.supplier_gstin.upper() != inv.supplier_gstin.upper():
                continue
            close_amount = abs(paise(p.taxable_value) - paise(inv.taxable_value)) <= tolerance
            close_date = abs((p.invoice_date - inv.invoice_date).days) <= 7
            if close_amount and close_date and _levenshtein(b_no, normalize_invoice_no(p.invoice_no)) <= 2:
                pairs[b_idx] = (p_idx, "fuzzy_invoice_no")
                used.add(p_idx)
                break

    # Pass 3: same invoice number and amount under a different GSTIN (wrong GSTIN in ledger master).
    for b_idx, inv in enumerate(books):
        if b_idx in pairs:
            continue
        for p_idx, p in enumerate(portal):
            if p_idx in used:
                continue
            if (normalize_invoice_no(p.invoice_no) == normalize_invoice_no(inv.invoice_no)
                    and abs(paise(p.taxable_value) - paise(inv.taxable_value)) <= tolerance
                    and abs(_tax(p) - _tax(inv)) <= tolerance):
                pairs[b_idx] = (p_idx, "gstin_corrected")
                used.add(p_idx)
                break

    results = []
    for b_idx, inv in enumerate(books):
        row = {"id": f"b{b_idx}", "books": inv.to_dict(), "flags": _flags(inv, buyer_state, today)}
        if b_idx in pairs:
            p_idx, basis = pairs[b_idx]
            row["portal"] = portal[p_idx].to_dict()
            row.update(_pair_result(inv, portal[p_idx], basis, tolerance))
        else:
            tax = _tax(inv)
            row.update({
                "portal": None, "status": MISSING, "reason": "NOT_UPLOADED", "match_basis": None,
                "variance": None, "max_variance": None,
                "itc_books": rupees(tax), "itc_portal": 0.0, "itc_claimable": 0.0, "itc_at_risk": rupees(tax),
            })
        row["reason_text"] = REASON_TEXT[row["reason"]]
        row["action"] = ACTIONS.get(row["reason"], "Ready to claim in GSTR-3B.")
        results.append(row)

    portal_only = []
    for p_idx, p in enumerate(portal):
        if p_idx in used:
            continue
        tax = _tax(p)
        portal_only.append({
            "id": f"p{p_idx}",
            "portal": p.to_dict(),
            "itc_unclaimed": rupees(tax if p.itc_available and tax > 0 else 0),
            "action": "Book this invoice (or confirm it isn't yours) to claim the credit.",
        })

    return {"results": results, "portal_only": portal_only, "summary": summarize(results, portal_only)}


def summarize(results: list[dict], portal_only: list[dict]) -> dict:
    by_status = {s: {"count": 0, "itc": 0} for s in (MATCHED, MISMATCH, MISSING)}
    by_reason: dict[str, int] = defaultdict(int)
    suppliers: dict[str, dict] = {}
    totals = {"itc_books": 0, "itc_claimable": 0, "itc_at_risk": 0}
    for row in results:
        bucket = by_status[row["status"]]
        bucket["count"] += 1
        bucket["itc"] += paise(row["itc_books"])
        by_reason[row["reason"]] += 1
        for k in totals:
            totals[k] += paise(row[k])
        if row["itc_at_risk"] > 0:
            gstin = row["books"]["supplier_gstin"]
            s = suppliers.setdefault(gstin, {"supplier_gstin": gstin, "supplier_name": row["books"]["supplier_name"],
                                             "invoices": 0, "itc_at_risk": 0})
            s["invoices"] += 1
            s["itc_at_risk"] += paise(row["itc_at_risk"])
    unclaimed = sum(paise(p["itc_unclaimed"]) for p in portal_only)
    ranked = sorted(suppliers.values(), key=lambda s: -s["itc_at_risk"])
    for s in ranked:
        s["itc_at_risk"] = rupees(s["itc_at_risk"])
    total = len(results) or 1
    return {
        "invoices": len(results),
        "portal_only": len(portal_only),
        "by_status": {k: {"count": v["count"], "itc": rupees(v["itc"])} for k, v in by_status.items()},
        "by_reason": dict(by_reason),
        "itc_books": rupees(totals["itc_books"]),
        "itc_claimable": rupees(totals["itc_claimable"]),
        "itc_at_risk": rupees(totals["itc_at_risk"]),
        "itc_unclaimed_in_portal": rupees(unclaimed),
        "match_rate": round(by_status[MATCHED]["count"] / total * 100, 1),
        "suppliers_at_risk": ranked,
    }
