"""GSTN Purchase Register Template v2.4 (shipped with the GST portal's Matching Offline Tool).

Accepts the .xlsx template or a .csv saved from it. Header row has:
GSTIN of Supplier/ECO, Trade/Legal name, Type of inward supplies, Document type,
Document number, Document date (DD/MM/YYYY), Taxable value, Integrated tax,
Central tax, State/UT tax, Cess. Credit notes are stored as negative amounts.
"""

import csv
import io

from ..models import Invoice
from .common import parse_date, to_amount

HEADERS = {
    "gstin of supplier/eco": "supplier_gstin",
    "trade/legal name": "supplier_name",
    "type of inward supplies": "supply_type",
    "document type": "doc_type",
    "document number": "doc_no",
    "document date": "doc_date",
    "taxable value (₹)": "taxable_value",
    "integrated tax (₹)": "igst",
    "central tax (₹)": "cgst",
    "state/ ut tax (₹)": "sgst",
    "cess (₹)": "cess",
}


def _norm(header) -> str:
    return " ".join(str(header or "").replace("*", "").split()).lower()


def _rows(payload: bytes) -> list[list]:
    if payload[:2] == b"PK":  # xlsx is a zip
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
        ws = wb["Purchase Register"] if "Purchase Register" in wb.sheetnames else wb.worksheets[-1]
        return [list(r) for r in ws.iter_rows(values_only=True)]
    return list(csv.reader(io.StringIO(payload.decode("utf-8-sig"))))


def parse(payload: bytes) -> list[Invoice]:
    rows = _rows(payload)
    header_at = next((i for i, r in enumerate(rows) if _norm(r[0] if r else "") == "gstin of supplier/eco"), None)
    if header_at is None:
        raise ValueError("header row 'GSTIN of Supplier/ECO' not found; is this the GSTN purchase register template?")
    cols = {HEADERS[_norm(h)]: i for i, h in enumerate(rows[header_at]) if _norm(h) in HEADERS}
    missing = {"supplier_gstin", "doc_no", "doc_date", "taxable_value"} - cols.keys()
    if missing:
        raise ValueError(f"missing column(s): {', '.join(sorted(missing))}")

    def cell(row, key):
        i = cols.get(key)
        return row[i] if i is not None and i < len(row) else None

    invoices = []
    for row in rows[header_at + 1:]:
        if not row or not cell(row, "supplier_gstin"):
            continue
        sign = -1 if "credit" in str(cell(row, "doc_type") or "").lower() else 1
        doc_date = cell(row, "doc_date")
        invoices.append(Invoice(
            supplier_gstin=str(cell(row, "supplier_gstin")).strip().upper(),
            supplier_name=str(cell(row, "supplier_name") or "").strip(),
            invoice_no=str(cell(row, "doc_no")).strip(),
            invoice_date=doc_date.date() if hasattr(doc_date, "date") else parse_date(str(doc_date)),
            taxable_value=sign * to_amount(cell(row, "taxable_value")) + 0.0,
            igst=sign * to_amount(cell(row, "igst")) + 0.0,
            cgst=sign * to_amount(cell(row, "cgst")) + 0.0,
            sgst=sign * to_amount(cell(row, "sgst")) + 0.0,
            cess=sign * to_amount(cell(row, "cess")) + 0.0,
        ))
    return invoices
