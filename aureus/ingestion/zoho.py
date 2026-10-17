"""Zoho Books bills, as returned by GET /books/v3/bills (with line items expanded)."""

import json

from ..gstin import normalize_state
from ..models import Invoice, InvoiceLine
from .common import parse_date, tax_head, to_amount, to_bool


def parse(payload: bytes) -> list[Invoice]:
    return parse_data(json.loads(payload))


def parse_data(data) -> list[Invoice]:
    bills = data.get("bills", data) if isinstance(data, dict) else data
    invoices = []
    for bill in bills:
        taxes = {"igst": 0.0, "cgst": 0.0, "sgst": 0.0, "cess": 0.0}
        for tax in bill.get("taxes", []):
            head = tax_head(tax.get("tax_name", ""))
            if head:
                taxes[head] += to_amount(tax.get("tax_amount"))
        lines = [
            InvoiceLine(
                description=item.get("name") or item.get("description", ""),
                hsn=str(item.get("hsn_or_sac", "")),
                taxable_value=to_amount(item.get("item_total")),
                quantity=to_amount(item.get("quantity")) or 1.0,
            )
            for item in bill.get("line_items", [])
        ]
        taxable = to_amount(bill.get("sub_total")) or round(sum(l.taxable_value for l in lines), 2)
        invoices.append(Invoice(
            supplier_gstin=str(bill.get("gst_no", "")).upper(),
            supplier_name=bill.get("vendor_name", ""),
            invoice_no=str(bill.get("bill_number", "")),
            invoice_date=parse_date(bill.get("date", "")),
            taxable_value=taxable,
            place_of_supply=normalize_state(bill.get("destination_of_supply", "")),
            reverse_charge=to_bool(bill.get("is_reverse_charge_applied", False)),
            lines=lines,
            source="zoho",
            **{k: round(v, 2) for k, v in taxes.items()},
        ))
    return invoices
