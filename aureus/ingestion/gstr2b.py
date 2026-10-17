"""GSTR-2B JSON as downloaded from the GST portal (Returns > GSTR-2B > Download JSON).

Only the B2B section is used for invoice matching; credit/debit notes (CDNR)
are folded in as signed records so their tax nets off against the supplier.
"""

import json

from ..gstin import normalize_state
from ..models import Invoice
from .common import parse_date, to_amount

_SECTIONS = {"b2b": "inv", "b2ba": "inv", "cdnr": "nt"}


def parse(payload: bytes) -> list[Invoice]:
    data = json.loads(payload)
    body = data.get("data", data)
    period = str(body.get("rtnprd", ""))
    docdata = body.get("docdata", {})
    invoices = []
    for section, doc_key in _SECTIONS.items():
        for supplier in docdata.get(section, []):
            for doc in supplier.get(doc_key, []):
                sign = -1 if section == "cdnr" and doc.get("typ") == "C" else 1
                invoices.append(Invoice(
                    supplier_gstin=supplier.get("ctin", "").upper(),
                    supplier_name=supplier.get("trdnm", ""),
                    invoice_no=str(doc.get("inum") or doc.get("ntnum", "")),
                    invoice_date=parse_date(doc.get("dt", "")),
                    taxable_value=sign * to_amount(doc.get("txval")),
                    igst=sign * to_amount(doc.get("igst")),
                    cgst=sign * to_amount(doc.get("cgst")),
                    sgst=sign * to_amount(doc.get("sgst")),
                    cess=sign * to_amount(doc.get("cess")),
                    place_of_supply=normalize_state(str(doc.get("pos", ""))),
                    reverse_charge=str(doc.get("rev", "N")).upper() == "Y",
                    source="gstr2b",
                    return_period=period,
                    itc_available=str(doc.get("itcavl", "Y")).upper() != "N",
                    itc_unavailable_reason=doc.get("rsn", "") or "",
                    supplier_filing_date=supplier.get("supfildt", ""),
                ))
    return invoices


def buyer_gstin(payload: bytes) -> str:
    data = json.loads(payload)
    return str(data.get("data", data).get("gstin", "")).upper()
