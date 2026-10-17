"""Suggest an IMS action (Accept / Pending / Reject) for each supplier document, from the reconciliation.

The suggestions are conservative: accept only what agrees with the books,
keep anything doubtful Pending, and never auto-reject. Rejecting is a
decision for the business, so it is only ever proposed for a person to confirm.
"""

from .tax import MATCHED, MISMATCH, MISSING, normalize_invoice_no, paise

AMOUNT_ONLY_REASONS = {"VALUE_MISMATCH", "TAX_HEAD_MISMATCH"}
CLERICAL_REASONS = {"INVOICE_NO_MISMATCH", "SUPPLIER_GSTIN_MISMATCH", "DATE_MISMATCH"}


def _same_amounts(record: dict, books: dict, tolerance: int = 99) -> bool:
    heads = (("taxable_value", "taxable_value"), ("igst", "igst"), ("cgst", "cgst"), ("sgst", "sgst"), ("cess", "cess"))
    return all(abs(paise(record[a]) - paise(books[b])) <= tolerance for a, b in heads)


def suggest(record: dict, row: dict | None) -> tuple[str, str]:
    """Return (suggested action code, reason)."""
    current = record["action"]
    if record["kind"] != "invoice":
        return current, "Credit and debit notes change your credit; review them yourself"
    if row is None:
        if record["pending_blocked"]:
            return current, "Not in your books, and Pending is not allowed for this document; check it and decide"
        return "P", "Not in your books yet: keep Pending until you confirm the purchase"
    reason = row["reason"]
    if any(f["code"] == "POSSIBLY_BLOCKED" for f in row.get("flags", [])) and row["status"] != MISSING:
        return "A", "Genuine purchase, so accept it, but don't claim the credit: likely blocked under Sec 17(5)"
    if row["status"] == MATCHED:
        return "A", "Matches your books"
    if row["status"] == MISMATCH and reason in CLERICAL_REASONS:
        return "A", "Amounts match your books; only a reference differs (clerical)"
    if row["status"] == MISMATCH and reason in AMOUNT_ONLY_REASONS:
        if record["pending_blocked"]:
            return current, "Amounts differ from your books and Pending is not allowed; ask the supplier to amend or reject"
        return "P", "Amounts differ from your books: keep Pending until the supplier amends"
    if row["status"] == MISSING and current in ("P", "R"):
        if _same_amounts(record, row["books"]):
            return "A", "You held this back, but it matches your books: accepting releases the credit"
        return current, "You held this back and the amounts differ from your books"
    return current, "No change suggested"


def recommend(recon: dict, ims: list[dict]) -> dict:
    by_books_key = {_key_of(r["books"]): r for r in recon["results"]}
    by_portal_key = {_key_of(r["portal"]): r for r in recon["results"] if r.get("portal")}
    rows, counts = [], {"A": 0, "P": 0, "R": 0, "N": 0}
    for idx, record in enumerate(ims):
        key = (record["supplier_gstin"], normalize_invoice_no(record["doc_no"]))
        row = by_portal_key.get(key) or by_books_key.get(key)
        suggested, why = suggest(record, row)
        counts[suggested] = counts.get(suggested, 0) + 1
        rows.append({
            "id": f"i{idx}",
            "section": record["section"],
            "kind": record["kind"],
            "supplier_gstin": record["supplier_gstin"],
            "supplier_name": record["supplier_name"],
            "doc_no": record["doc_no"],
            "doc_date": record["doc_date"],
            "taxable_value": record["taxable_value"],
            "tax": round(record["igst"] + record["cgst"] + record["sgst"] + record["cess"], 2),
            "return_period": record["return_period"],
            "current": record["action"],
            "suggested": suggested,
            "reason": why,
            "changes": suggested != record["action"],
            # High priority = the change moves credit; N -> A only confirms what GSTR-3B filing would deem accepted.
            "priority": "high" if suggested != record["action"] and not (record["action"] == "N" and suggested == "A")
            else ("low" if suggested != record["action"] else None),
            "books_status": row["status"] if row else None,
        })
    return {"rows": rows, "changes": sum(r["changes"] for r in rows), "suggested_counts": counts}


def _key_of(doc: dict) -> tuple:
    return (doc["supplier_gstin"].strip().upper(), normalize_invoice_no(doc["invoice_no"]))
