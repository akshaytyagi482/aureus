from datetime import date, datetime

_DATE_FORMATS = ("%Y%m%d", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d.%m.%Y")


def parse_date(value: str) -> date:
    value = (value or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date '{value}'")


def to_amount(value) -> float:
    """Parse '1,23,456.50', '-1500', '(1500)' or numbers into a positive rupee amount."""
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return round(abs(float(value)), 2)
    text = str(value).strip().replace(",", "").replace("₹", "")
    text = text.strip("()")
    return round(abs(float(text or 0)), 2)


def to_bool(value) -> bool:
    return str(value).strip().upper() in {"Y", "YES", "TRUE", "1"}


def tax_head(name: str) -> str | None:
    upper = (name or "").upper()
    for head in ("IGST", "CGST", "SGST", "UTGST", "CESS"):
        if head in upper:
            return "sgst" if head == "UTGST" else head.lower()
    return None
