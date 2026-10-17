"""IMS (Invoice Management System) JSON, as downloaded from the GST portal's IMS dashboard.

Since October 2024 a recipient can Accept, Reject or keep Pending each
supplier document in IMS; only Accepted / No-action documents flow into
GSTR-2B. Field names follow the GSTN IMS Offline Tool v2.0 (download key
`imsDetails`, upload `{"rtin", "reqtyp": "SAVE", "invdata"}`).
"""

import json

from ..gstin import normalize_state
from .common import parse_date, to_amount

# section -> (document-number key, document-date key, document kind)
SECTIONS = {
    "b2b": ("inum", "idt", "invoice"),
    "b2ba": ("inum", "idt", "invoice"),
    "b2bdn": ("nt_num", "nt_dt", "debit_note"),
    "b2bdna": ("nt_num", "nt_dt", "debit_note"),
    "b2bcn": ("nt_num", "nt_dt", "credit_note"),
    "b2bcna": ("nt_num", "nt_dt", "credit_note"),
}
ACTIONS = {"N": "No action", "A": "Accepted", "R": "Rejected", "P": "Pending"}


def _body(data: dict) -> dict:
    return data.get("data", data)


def buyer_gstin(payload: bytes) -> str:
    body = _body(json.loads(payload))
    return str(body.get("rtin") or body.get("gstin") or "").upper()


def parse(payload: bytes) -> list[dict]:
    """Return one plain record per IMS document, keeping the raw portal record for re-export."""
    body = _body(json.loads(payload))
    details = body.get("imsDetails")
    if not isinstance(details, dict):
        raise ValueError("not an IMS file (no 'imsDetails' section)")
    records = []
    for section, (num_key, date_key, kind) in SECTIONS.items():
        for item in details.get(section, []):
            records.append({
                "section": section,
                "kind": kind,
                "supplier_gstin": str(item.get("stin", "")).upper(),
                "supplier_name": item.get("tradenm", ""),
                "doc_no": str(item.get(num_key, "")),
                "doc_date": parse_date(item.get(date_key, "")).isoformat(),
                "value": to_amount(item.get("val")),
                "taxable_value": to_amount(item.get("txval")),
                "igst": to_amount(item.get("iamt")),
                "cgst": to_amount(item.get("camt")),
                "sgst": to_amount(item.get("samt")),
                "cess": to_amount(item.get("cess")),
                "place_of_supply": normalize_state(str(item.get("pos", ""))),
                "action": str(item.get("action", "N")).upper() or "N",
                "return_period": str(item.get("rtnprd", "")),
                "source_form": item.get("srcform", ""),
                "pending_blocked": str(item.get("ispendactblocked", "N")).upper() == "Y",
                "raw": item,
            })
    return records


def upload_json(recipient_gstin: str, changes: list[dict]) -> dict:
    """Build the IMS upload file: each change is a parsed record plus its new `action` (A/R/P/N)."""
    invdata: dict[str, list] = {}
    for change in changes:
        raw = {k: v for k, v in change["raw"].items() if k not in ("tradenm", "supFilingDt", "supFilingPref")}
        raw["action"] = change["action"]
        if change.get("remarks") and change["action"] in ("R", "P"):
            raw["remarks"] = change["remarks"][:150]
        else:
            raw.pop("remarks", None)
        invdata.setdefault(change["section"], []).append(raw)
    return {"rtin": recipient_gstin, "reqtyp": "SAVE", "invdata": invdata}
