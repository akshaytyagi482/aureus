"""GSTIN validation and Indian state-code lookups.

A GSTIN is 15 characters: 2-digit state code, 10-character PAN, entity number,
the letter Z by default, and a mod-36 check character.
"""

import re

_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z][A-Z0-9][0-9A-Z]$")

STATES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur", "15": "Mizoram",
    "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh",
    "24": "Gujarat", "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra", "29": "Karnataka", "30": "Goa", "31": "Lakshadweep",
    "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh",
    "38": "Ladakh", "97": "Other Territory",
}

# Two-letter abbreviations used by Zoho Books and e-way bills.
STATE_ABBR = {
    "JK": "01", "HP": "02", "PB": "03", "CH": "04", "UK": "05", "UT": "05",
    "HR": "06", "DL": "07", "RJ": "08", "UP": "09", "BR": "10", "SK": "11",
    "AR": "12", "NL": "13", "MN": "14", "MZ": "15", "TR": "16", "ML": "17",
    "AS": "18", "WB": "19", "JH": "20", "OR": "21", "OD": "21", "CG": "22",
    "CT": "22", "MP": "23", "GJ": "24", "DN": "26", "DD": "26", "MH": "27",
    "KA": "29", "GA": "30", "LD": "31", "KL": "32", "TN": "33", "PY": "34",
    "AN": "35", "TS": "36", "TG": "36", "AP": "37", "LA": "38",
}


def check_char(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        value = _CHARSET.index(ch) * (2 if i % 2 else 1)
        total += value // 36 + value % 36
    return _CHARSET[(36 - total % 36) % 36]


def is_valid(gstin: str) -> bool:
    gstin = (gstin or "").strip().upper()
    if not _PATTERN.match(gstin) or gstin[:2] not in STATES:
        return False
    return check_char(gstin[:14]) == gstin[14]


def state_code(gstin: str) -> str:
    return (gstin or "")[:2]


def state_name(code: str) -> str:
    return STATES.get(code, "Unknown")


def normalize_state(value: str) -> str:
    """Accept '27', '27-Maharashtra', 'MH' or 'Maharashtra' and return '27'."""
    value = (value or "").strip()
    if not value:
        return ""
    head = value[:2]
    if head.isdigit() and head in STATES:
        return head
    if value.upper() in STATE_ABBR:
        return STATE_ABBR[value.upper()]
    for code, name in STATES.items():
        if name.lower() == value.lower():
            return code
    return ""


def describe(gstin: str) -> dict:
    gstin = (gstin or "").strip().upper()
    valid = is_valid(gstin)
    return {
        "gstin": gstin,
        "valid": valid,
        "state_code": gstin[:2] if valid else None,
        "state": state_name(gstin[:2]) if valid else None,
        "pan": gstin[2:12] if valid else None,
    }
