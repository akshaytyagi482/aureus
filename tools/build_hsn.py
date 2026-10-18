"""Build aureus/data/hsn_in.json.gz from the GST portal's HSN directory.

    python tools/build_hsn.py HSN_SAC.xlsx

Download the workbook from services.gst.gov.in -> Search HSN Code -> Download HSN Directory.
Sheet HSN_MSTR holds goods codes (2/4/6/8 digits), SAC_MSTR holds service codes.
Excel drops leading zeros and some rows carry spaces, so codes are cleaned to an even length.
"""

import gzip
import json
import re
import sys
from datetime import date
from pathlib import Path

import openpyxl

OUT = Path(__file__).resolve().parent.parent / "aureus" / "data" / "hsn_in.json.gz"


def _code(value) -> str | None:
    code = re.sub(r"[^0-9]", "", str(value or ""))
    if len(code) % 2:
        code = "0" + code
    return code if len(code) in (2, 4, 6, 8) else None


def _text(value) -> str:
    text = str(value or "").replace("_x000D_", " ")
    text = text.split("~")[-1]  # some rows carry the full chapter~heading~line path
    return " ".join(text.split()).rstrip(" :")


def build(path: str) -> dict:
    wb = openpyxl.load_workbook(path, read_only=True)
    out = {}
    for sheet, key in (("HSN_MSTR", "hsn"), ("SAC_MSTR", "sac")):
        codes = {}
        for raw, desc in list(wb[sheet].iter_rows(values_only=True))[1:]:
            code = _code(raw)
            if code and desc:
                codes.setdefault(code, _text(desc))
        out[key] = codes
    out["source"] = "GST portal HSN directory (services.gst.gov.in)"
    out["built_on"] = date.today().isoformat()
    return out


if __name__ == "__main__":
    data = build(sys.argv[1])
    with gzip.open(OUT, "wt", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    print({"hsn": len(data["hsn"]), "sac": len(data["sac"]), "bytes": OUT.stat().st_size})
