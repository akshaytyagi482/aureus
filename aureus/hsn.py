"""HSN code lookups against the international HS 2022 nomenclature.

India's 8-digit HSN codes extend the 6-digit Harmonized System, so the first
2/4/6 digits can be checked against the HS list published by UN Comtrade.
Chapter 99 is India's SAC (services) range, which HS does not cover.
"""

import gzip
import json
import re
from functools import lru_cache

from .config import BASE_DIR


@lru_cache
def _codes() -> dict[str, str]:
    with gzip.open(BASE_DIR / "data" / "hs2022.json.gz", "rb") as fh:
        return json.load(fh)["codes"]


def clean(hsn: str) -> str:
    return re.sub(r"[^0-9]", "", hsn or "")


def is_service(hsn: str) -> bool:
    return clean(hsn).startswith("99")


def describe(hsn: str) -> dict:
    """Most specific HS description for a code, e.g. '84581100' -> the 6-digit heading for CNC lathes."""
    code = clean(hsn)
    if not code:
        return {"hsn": hsn, "status": "missing", "level": None, "description": None}
    if is_service(code):
        return {"hsn": code, "status": "service", "level": None, "description": "Services (SAC)"}
    for length in (6, 4, 2):
        if len(code) >= length and code[:length] in _codes():
            # A 2-digit match alone means the heading itself is not a real code.
            status = "valid" if length >= 4 else "unknown_heading"
            return {"hsn": code, "status": status, "level": length, "description": _codes()[code[:length]]}
    return {"hsn": code, "status": "unknown", "level": None, "description": None}
