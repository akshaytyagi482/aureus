"""HSN and SAC code lookups.

Two sources: the GST portal's HSN directory (hsn_in.json.gz, India's 8-digit
tariff lines and SAC service codes, rebuilt with tools/build_hsn.py) and the
international HS 2022 nomenclature from UN Comtrade (hs2022.json.gz, 6-digit,
with readable descriptions). India's 8-digit codes extend the 6-digit HS code.
Chapter 99 is India's SAC (services) range.
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


@lru_cache
def _india() -> dict[str, dict[str, str]]:
    with gzip.open(BASE_DIR / "data" / "hsn_in.json.gz", "rt", encoding="utf-8") as fh:
        return json.load(fh)


def clean(hsn: str) -> str:
    return re.sub(r"[^0-9]", "", hsn or "")


def is_service(hsn: str) -> bool:
    return clean(hsn).startswith("99")


def _tariff_line(code: str) -> str | None:
    """The GST directory's text for an 8-digit line, when it adds to the 6-digit heading."""
    hsn = _india()["hsn"]
    line = hsn.get(code)
    if not line or line.casefold() == (hsn.get(code[:6]) or "").casefold():
        return None
    return line.capitalize()


def describe(hsn: str) -> dict:
    """Most specific description for a code, e.g. '84581100' -> the HS heading for CNC lathes.

    status: missing / valid / unlisted (8 digits, heading real but line not in the GST
    directory) / unknown_heading (only the chapter exists) / unknown / service.
    """
    code = clean(hsn)
    if not code:
        return {"hsn": hsn, "status": "missing", "level": None, "description": None}
    if is_service(code):
        sac = _india()["sac"]
        for length in (6, 4, 2):
            if len(code) >= length and code[:length] in sac:
                return {"hsn": code, "status": "service", "level": length, "description": sac[code[:length]]}
        return {"hsn": code, "status": "unknown", "level": None, "description": None}
    for length in (6, 4, 2):
        if len(code) >= length and code[:length] in _codes():
            description = _codes()[code[:length]]
            # A 2-digit match alone means the heading itself is not a real code.
            status = "valid" if length >= 4 else "unknown_heading"
            if len(code) > length == 4 and code not in _india()["hsn"]:
                status = "unlisted"  # the heading is real but the subheading/tariff line typed after it is not
            elif len(code) == 8 and length == 6:
                if code in _india()["hsn"]:
                    length = 8
                    line = _tariff_line(code)
                    if line:
                        description = f"{description} — {line}"
                else:
                    status = "unlisted"
            return {"hsn": code, "status": status, "level": length, "description": description}
    return {"hsn": code, "status": "unknown", "level": None, "description": None}
