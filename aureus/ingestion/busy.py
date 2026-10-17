"""Busy purchase register CSV export, also used as the generic CSV format.

One row per invoice line. Rows sharing supplier GSTIN + invoice number are
grouped into a single invoice; tax columns may be given on every line or only
on the first.
"""

import csv
import io

from ..gstin import normalize_state
from ..models import Invoice, InvoiceLine
from .common import parse_date, to_amount, to_bool

# Accept the header spellings Busy and common spreadsheets use.
_ALIASES = {
    "supplier_gstin": ["supplier_gstin", "gstin", "party gstin", "gstin/uin", "party gstin no"],
    "supplier_name": ["supplier_name", "party", "party name", "supplier"],
    "invoice_no": ["invoice_no", "bill no", "invoice no", "inv no", "voucher no", "bill no."],
    "invoice_date": ["invoice_date", "bill date", "invoice date", "date"],
    "taxable_value": ["taxable_value", "taxable value", "taxable amt", "amount"],
    "igst": ["igst", "igst amount", "igst amt"],
    "cgst": ["cgst", "cgst amount", "cgst amt"],
    "sgst": ["sgst", "sgst amount", "sgst amt", "sgst/utgst"],
    "cess": ["cess", "cess amount"],
    "hsn": ["hsn", "hsn/sac", "hsn code"],
    "description": ["description", "item", "item name", "particulars"],
    "quantity": ["quantity", "qty"],
    "place_of_supply": ["place_of_supply", "place of supply", "pos"],
    "reverse_charge": ["reverse_charge", "reverse charge", "rcm"],
}


def _resolve(headers):
    lookup = {h.strip().lower(): h for h in headers}
    resolved = {}
    for field, names in _ALIASES.items():
        for name in names:
            if name in lookup:
                resolved[field] = lookup[name]
                break
    missing = {"supplier_gstin", "invoice_no", "invoice_date", "taxable_value"} - resolved.keys()
    if missing:
        raise ValueError(f"missing column(s): {', '.join(sorted(missing))}")
    return resolved


def parse(payload: bytes) -> list[Invoice]:
    text = payload.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    cols = _resolve(reader.fieldnames or [])

    def get(row, field):
        return row.get(cols[field], "") if field in cols else ""

    grouped: dict[tuple, Invoice] = {}
    for row in reader:
        if not any((v or "").strip() for v in row.values()):
            continue
        gstin = get(row, "supplier_gstin").strip().upper()
        inv_no = get(row, "invoice_no").strip()
        key = (gstin, inv_no)
        line = InvoiceLine(
            description=get(row, "description").strip(),
            hsn=get(row, "hsn").strip(),
            taxable_value=to_amount(get(row, "taxable_value")),
            quantity=to_amount(get(row, "quantity")) or 1.0,
        )
        inv = grouped.get(key)
        if inv is None:
            inv = grouped[key] = Invoice(
                supplier_gstin=gstin,
                supplier_name=get(row, "supplier_name").strip(),
                invoice_no=inv_no,
                invoice_date=parse_date(get(row, "invoice_date")),
                taxable_value=0.0,
                place_of_supply=normalize_state(get(row, "place_of_supply")),
                reverse_charge=to_bool(get(row, "reverse_charge")),
            )
        inv.lines.append(line)
        inv.taxable_value = round(inv.taxable_value + line.taxable_value, 2)
        for head in ("igst", "cgst", "sgst", "cess"):
            setattr(inv, head, round(getattr(inv, head) + to_amount(get(row, head)), 2))
    return list(grouped.values())
