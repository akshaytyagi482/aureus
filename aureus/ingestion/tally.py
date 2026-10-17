"""Tally Prime purchase-voucher XML (Display > Day Book > Export, or the XML server)."""

import xml.etree.ElementTree as ET

from ..gstin import normalize_state
from ..models import Invoice, InvoiceLine
from .common import parse_date, tax_head, to_amount


def _text(node, *tags) -> str:
    for tag in tags:
        found = node.find(tag)
        if found is not None and found.text:
            return found.text.strip()
    return ""


def parse(payload: bytes) -> list[Invoice]:
    root = ET.fromstring(payload)
    invoices = []
    for voucher in root.iter("VOUCHER"):
        vch_type = voucher.get("VCHTYPE") or _text(voucher, "VOUCHERTYPENAME")
        if vch_type and "purchase" not in vch_type.lower():
            continue
        taxes = {"igst": 0.0, "cgst": 0.0, "sgst": 0.0, "cess": 0.0}
        for entry in voucher.iter("ALLLEDGERENTRIES.LIST"):
            head = tax_head(_text(entry, "LEDGERNAME"))
            if head:
                taxes[head] += to_amount(_text(entry, "AMOUNT"))
        lines = []
        for item in voucher.iter("ALLINVENTORYENTRIES.LIST"):
            qty_text = _text(item, "BILLEDQTY", "ACTUALQTY").split(" ")[0]
            lines.append(InvoiceLine(
                description=_text(item, "STOCKITEMNAME"),
                hsn=_text(item, "GSTHSNCODE", "HSNCODE"),
                taxable_value=to_amount(_text(item, "AMOUNT")),
                quantity=to_amount(qty_text) or 1.0,
            ))
        taxable = round(sum(line.taxable_value for line in lines), 2)
        invoices.append(Invoice(
            supplier_gstin=_text(voucher, "PARTYGSTIN").upper(),
            supplier_name=_text(voucher, "PARTYLEDGERNAME", "PARTYNAME"),
            invoice_no=_text(voucher, "SUPPLIERINVOICENO", "REFERENCE", "VOUCHERNUMBER"),
            invoice_date=parse_date(_text(voucher, "REFERENCEDATE", "DATE")),
            taxable_value=taxable,
            place_of_supply=normalize_state(_text(voucher, "PLACEOFSUPPLY")),
            reverse_charge=_text(voucher, "ISREVERSECHARGEAPPLICABLE").lower() == "yes",
            lines=lines,
            **{k: round(v, 2) for k, v in taxes.items()},
        ))
    return invoices
