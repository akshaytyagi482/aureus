"""Rupee formatting with Indian digit grouping (lakh / crore)."""


def inr(value: float, decimals: int = 2) -> str:
    """inr(220321) -> '₹2,20,321.00'; inr(8700000, 0) -> '₹87,00,000'."""
    sign = "-" if value < 0 else ""
    text = f"{abs(value):.{decimals}f}"
    whole, _, frac = text.partition(".")
    head, tail = whole[:-3], whole[-3:]
    while len(head) > 2:
        tail = f"{head[-2:]},{tail}"
        head = head[:-2]
    grouped = f"{head},{tail}" if head else tail
    return f"{sign}₹{grouped}" + (f".{frac}" if frac else "")
