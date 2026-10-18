"""Deterministic demo data: a Pune auto-component maker's September 2026 purchases.

Produces a Tally Prime XML export and the matching GSTR-2B JSON with known,
deliberately planted problems (see EXPECTED) so the demo and the tests can
assert exact outcomes.
"""

import json
from datetime import date
from xml.sax.saxutils import escape

from .gstin import check_char


def gstin(state: str, pan: str, entity: str = "1") -> str:
    base = f"{state}{pan}{entity}Z"
    return base + check_char(base)


BUYER = {
    "name": "Shree Ganesh Auto Components Pvt Ltd",
    "gstin": gstin("27", "AAGCS4821K"),
    "district": "Pune",
    "address": "Plot 14, MIDC Bhosari, Pune 411026",
    "sector": "manufacturing",
    "industry": "Auto components (precision machined parts)",
    "udyam": "UDYAM-MH-26-0048213",
    "annual_turnover": 320000000,
    "investment_pm": 180000000,
    "term_loan_amount": 6000000,
    "zone": "A",  # Haveli taluka, within the Pune Metropolitan Region (Annexure I)
    "monthly_electricity_duty": 28000,
    "monthly_power_units": 60000,
}

S = {
    "steel": ("Bharat Steel Traders", gstin("27", "AAFFB2231M")),
    "castings": ("Deccan Castings LLP", gstin("27", "AAKFD7720P")),
    "cnc": ("Ahmedabad Machine Tools Pvt Ltd", gstin("24", "AAECA5512Q")),
    "metrology": ("Bengaluru Metrology Systems", gstin("29", "AAHCB9013R")),
    "solar": ("Sahyadri Solar Solutions", gstin("27", "ABMFS3381H")),
    "tools": ("Precision Tooling Co", gstin("33", "AAJCP4410C")),
    "packaging": ("Shiv Packaging Industries", gstin("27", "AASFS7781D")),
    "oil": ("Western Lubricants", gstin("27", "AACCW1201E")),
    "it": ("Pune Infotech Systems", gstin("27", "AAKCP6612F")),
    "auto": ("Metro Motors Pvt Ltd", gstin("27", "AABCM3390G")),
    "envirotech": ("Enviro Dust Control Pvt Ltd", gstin("27", "AADCE5521J")),
    "freight": ("Konkan Freight Carriers", gstin("27", "AAGFK8812L")),
}

# (key, supplier, invoice_no, day, lines[(desc, hsn, qty, value)], gst_rate, interstate)
_BOOKS = [
    ("ok1", "steel", "BST/2026/1101", 2, [("EN8 round bars", "72142090", 12000, 1260000)], 18, False),
    ("ok2", "castings", "DC-0912", 3, [("Grey iron castings", "73251000", 4000, 540000)], 18, False),
    ("cnc", "cnc", "AMT/26-27/0457", 4, [("CNC turning centre LT-450", "84581100", 1, 5000000)], 18, True),
    ("cmm", "metrology", "BMS/INV/2231", 6, [("Coordinate measuring machine", "90318000", 1, 1800000)], 18, True),
    ("solar", "solar", "SSS/0098", 8, [("Mono PERC solar modules 540Wp", "85414300", 220, 1200000),
                                        ("String inverter 100kW", "85044010", 1, 250000)], 12, False),
    ("tools", "tools", "PTC/1188", 9, [("Carbide inserts", "82090090", 600, 96000)], 18, True),
    ("pack", "packaging", "inv-0042", 10, [("Corrugated boxes", "48199900", 5000, 85000)], 18, False),  # wrong HSN
    ("oil", "oil", "WL/772", 11, [("Cutting oil", "27101980", 400, 62000)], 18, False),
    ("laptop", "it", "PIS/2026/310", 12, [("Engineering workstation", "84713010", 1, 85000)], 18, False),
    ("car", "auto", "MM/INV/5521", 14, [("Company car (sedan)", "87032391", 1, 900000)], 28, False),
    ("dust", "envirotech", "EDC/26/061", 15, [("Dust collector system", "84213920", 1, 450000)], 18, False),
    ("missing1", "steel", "BST/2026/1187", 18, [("EN19 bright bars", "72155090", 8000, 840000)], 18, False),
    ("missing2", "freight", "KFC/0931", 20, [("Freight charges", "996511", 1, 48000)], 12, False),
    ("missing3", "tools", "PTC/1214", 24, [("Drill bits", "82075000", 300, 54000)], 18, True),
    ("value", "castings", "DC-0937", 21, [("SG iron castings", "73251000", 2500, 410000)], 18, False),
    ("paisa_ok", "oil", "WL/790", 22, [("Hydraulic oil", "27101980", 200, 31000)], 18, False),
    ("paisa_warn", "packaging", "SPI/0461", 23, [("Wooden pallets", "", 100, 42000)], 18, False),  # no HSN
    ("taxhead", "steel", "BST/2026/1201", 25, [("MS plates", "72085110", 3000, 270000)], 18, False),
    ("typo", "castings", "DC-0951", 26, [("Aluminium die castings", "76169990", 1800, 324000)], 18, False),
    ("wronggstin", "it", "PIS/2026/333", 27, [("Network switches", "85176290", 4, 64000)], 18, False),
    ("itcnot", "oil", "WL/801", 28, [("Grease", "27101990", 100, 18000)], 18, False),
]

# How each planted invoice should be treated on the GSTR-2B side.
_PORTAL_TWEAKS = {
    "missing1": None, "missing2": None, "missing3": None,
    "value": {"txval": -10000.0, "tax": -1800.0},
    "paisa_ok": {"tax_paise": -99},
    "paisa_warn": {"tax_paise": -100},
    "taxhead": {"swap_head": True},
    "pack": {"inum": "INV/42"},
    "typo": {"inum": "DC-0915"},
    "wronggstin": {},
    "itcnot": {"itcavl": "N", "rsn": "P"},
}

_PORTAL_ONLY = [
    ("Bharat Steel Traders", S["steel"][1], "BST/2026/1150", 16, 156000, 18, False),
    ("Shiv Packaging Industries", S["packaging"][1], "SPI/0455", 19, 22000, 18, False),
    # missing3 as the supplier actually filed it: second registration, trade name, bare number (for the AI matcher)
    ("PRECISION TOOLING COMPANY", gstin("33", "AAJCP4410C", "2"), "1214", 24, 54000, 18, True),
]

EXPECTED = {
    "MATCHED_ELIGIBLE": {"ok1", "ok2", "cnc", "cmm", "solar", "tools", "pack", "oil", "laptop", "car", "dust", "paisa_ok"},
    "MISMATCH_WARN": {"value", "paisa_warn", "taxhead", "typo", "wronggstin", "itcnot"},
    "MISSING_IN_PORTAL": {"missing1", "missing2", "missing3"},
}

PERIOD = "092026"


def _taxes(value: float, rate: int, interstate: bool) -> dict:
    tax = round(value * rate / 100, 2)
    if interstate:
        return {"igst": tax, "cgst": 0.0, "sgst": 0.0}
    return {"igst": 0.0, "cgst": round(tax / 2, 2), "sgst": round(tax / 2, 2)}


def _books_records():
    for key, sup, inv_no, day, lines, rate, interstate in _BOOKS:
        name, ctin = S[sup]
        value = sum(l[3] for l in lines)
        taxes = _taxes(value, rate, interstate)
        if key == "taxhead":  # booked as IGST though the supplier is in-state
            taxes = {"igst": round(value * rate / 100, 2), "cgst": 0.0, "sgst": 0.0}
        yield key, name, ctin, inv_no, date(2026, 9, day), lines, value, taxes


def tally_xml() -> bytes:
    vouchers = []
    for key, name, ctin, inv_no, d, lines, value, taxes in _books_records():
        if key == "wronggstin":  # ledger master holds a mistyped GSTIN
            ctin = ctin[:12] + "3" + ctin[13:]
            ctin = ctin[:14] + check_char(ctin[:14])
        ledgers = "".join(
            f"<ALLLEDGERENTRIES.LIST><LEDGERNAME>Input {head.upper()}</LEDGERNAME><AMOUNT>-{amt:.2f}</AMOUNT></ALLLEDGERENTRIES.LIST>"
            for head, amt in taxes.items() if amt)
        items = "".join(
            f"<ALLINVENTORYENTRIES.LIST><STOCKITEMNAME>{escape(desc)}</STOCKITEMNAME><GSTHSNCODE>{hsn}</GSTHSNCODE>"
            f"<BILLEDQTY>{qty} Nos</BILLEDQTY><AMOUNT>-{amt:.2f}</AMOUNT></ALLINVENTORYENTRIES.LIST>"
            for desc, hsn, qty, amt in lines)
        vouchers.append(
            f'<TALLYMESSAGE><VOUCHER VCHTYPE="Purchase" ACTION="Create"><DATE>{d:%Y%m%d}</DATE>'
            f"<VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME><VOUCHERNUMBER>P-{len(vouchers) + 1:04d}</VOUCHERNUMBER>"
            f"<REFERENCE>{escape(inv_no)}</REFERENCE><REFERENCEDATE>{d:%Y%m%d}</REFERENCEDATE>"
            f"<PARTYLEDGERNAME>{escape(name)}</PARTYLEDGERNAME><PARTYGSTIN>{ctin}</PARTYGSTIN>"
            f"<PLACEOFSUPPLY>Maharashtra</PLACEOFSUPPLY>{ledgers}{items}</VOUCHER></TALLYMESSAGE>")
    return ('<?xml version="1.0" encoding="UTF-8"?><ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>'
            "<BODY><IMPORTDATA><REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME></REQUESTDESC><REQUESTDATA>"
            + "".join(vouchers) + "</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>").encode()


def gstr2b_json() -> bytes:
    suppliers: dict[str, dict] = {}

    def add(ctin, name, doc):
        entry = suppliers.setdefault(ctin, {"ctin": ctin, "trdnm": name, "supfildt": "11-10-2026", "supprd": PERIOD, "inv": []})
        entry["inv"].append(doc)

    for key, name, ctin, inv_no, d, lines, value, taxes in _books_records():
        tweak = _PORTAL_TWEAKS.get(key, {})
        if tweak is None:
            continue
        taxes = dict(taxes)
        txval = value + tweak.get("txval", 0.0)
        if "tax" in tweak:
            for head in taxes:
                if taxes[head]:
                    taxes[head] = round(taxes[head] + tweak["tax"] / sum(1 for v in taxes.values() if v), 2)
        if "tax_paise" in tweak:
            head = next(h for h in ("cgst", "igst") if taxes[h])
            taxes[head] = round(taxes[head] + tweak["tax_paise"] / 100, 2)
        if tweak.get("swap_head"):
            total = taxes["igst"]
            taxes = {"igst": 0.0, "cgst": round(total / 2, 2), "sgst": round(total / 2, 2)}
        doc = {"inum": tweak.get("inum", inv_no), "typ": "R", "dt": d.strftime("%d-%m-%Y"),
               "val": round(txval + sum(taxes.values()), 2), "pos": "27", "rev": "N",
               "itcavl": tweak.get("itcavl", "Y"), "rsn": tweak.get("rsn", ""), "diffprcnt": 1,
               "srctyp": "", "txval": txval, "cess": 0, **taxes}
        add(tweak.get("ctin", ctin), name, doc)

    for name, ctin, inv_no, day, value, rate, interstate in _PORTAL_ONLY:
        taxes = _taxes(value, rate, interstate)
        add(ctin, name, {"inum": inv_no, "typ": "R", "dt": date(2026, 9, day).strftime("%d-%m-%Y"),
                         "val": round(value + sum(taxes.values()), 2), "pos": "27", "rev": "N", "itcavl": "Y",
                         "rsn": "", "diffprcnt": 1, "srctyp": "", "txval": value, "cess": 0, **taxes})

    return json.dumps({"chksum": "", "data": {
        "gstin": BUYER["gstin"], "rtnprd": PERIOD, "version": "1.0", "gendt": "14-10-2026",
        "docdata": {"b2b": list(suppliers.values())}}}, indent=1).encode()


def book_key_by_invoice_no() -> dict[str, str]:
    return {inv_no: key for key, _, inv_no, *_ in _BOOKS}


# IMS: every GSTR-2B invoice is in IMS with no action yet; two "missing" invoices
# were actually held back by the buyer (one Pending, one Rejected).
IMS_HELD_BACK = {"missing1": "P", "missing2": "R"}


def ims_json() -> bytes:
    portal = json.loads(gstr2b_json())["data"]["docdata"]["b2b"]
    items = []
    for supplier in portal:
        for doc in supplier["inv"]:
            items.append({"stin": supplier["ctin"], "tradenm": supplier["trdnm"], "inum": doc["inum"], "inv_typ": "R",
                          "idt": doc["dt"], "val": doc["val"], "action": "N", "pos": doc["pos"], "txval": doc["txval"],
                          "iamt": doc["igst"], "camt": doc["cgst"], "samt": doc["sgst"], "cess": doc["cess"],
                          "srcform": "R1", "rtnprd": PERIOD, "srcfilstatus": "Filed", "supFilingDt": "11-10-2026",
                          "ispendactblocked": "N", "isRemarksBlocked": "N", "isEdit": "N"})
    for key, name, ctin, inv_no, d, lines, value, taxes in _books_records():
        if key not in IMS_HELD_BACK:
            continue
        items.append({"stin": ctin, "tradenm": name, "inum": inv_no, "inv_typ": "R", "idt": d.strftime("%d-%m-%Y"),
                      "val": round(value + sum(taxes.values()), 2), "action": IMS_HELD_BACK[key], "pos": "27",
                      "txval": value, "iamt": taxes["igst"], "camt": taxes["cgst"], "samt": taxes["sgst"], "cess": 0,
                      "srcform": "R1", "rtnprd": PERIOD, "srcfilstatus": "Filed", "supFilingDt": "11-10-2026",
                      "ispendactblocked": "N", "isRemarksBlocked": "N", "isEdit": "N"})
    return json.dumps({"rtin": BUYER["gstin"], "imsDetails": {"b2b": items}}, indent=1).encode()
