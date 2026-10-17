"""GST compliance calendar: when GSTR-2B lands, when GSTR-3B is due, and ITC time limits."""

from datetime import date, timedelta

# QRMP filers in these states file GSTR-3B by the 22nd; everyone else by the 24th.
_QRMP_22ND_STATES = {"22", "23", "24", "26", "27", "29", "30", "31", "32", "33", "34", "35", "36", "37"}


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def period_of(d: date) -> str:
    return f"{d.month:02d}{d.year}"


def parse_period(period: str) -> tuple[int, int]:
    return int(period[2:]), int(period[:2])


def gstr3b_due(period: str, state_code: str, frequency: str = "monthly") -> date:
    year, month = parse_period(period)
    ny, nm = _next_month(year, month)
    if frequency == "quarterly":
        return date(ny, nm, 22 if state_code in _QRMP_22ND_STATES else 24)
    return date(ny, nm, 20)


def gstr2b_available(period: str) -> date:
    year, month = parse_period(period)
    ny, nm = _next_month(year, month)
    return date(ny, nm, 14)


def open_period(today: date) -> str:
    """The return period whose GSTR-3B is the next one to file."""
    first = today.replace(day=1)
    previous = first - timedelta(days=1)
    return period_of(previous)


def filing_status(today: date, state_code: str, frequency: str = "monthly") -> dict:
    period = open_period(today)
    due = gstr3b_due(period, state_code, frequency)
    return {
        "period": period,
        "period_label": period_label(period),
        "gstr2b_available_on": gstr2b_available(period).isoformat(),
        "gstr3b_due_on": due.isoformat(),
        "days_left": (due - today).days,
    }


def period_label(period: str) -> str:
    year, month = parse_period(period)
    return date(year, month, 1).strftime("%b %Y")


def itc_time_limit(invoice_date: date) -> date:
    """Section 16(4): ITC for an invoice must be claimed by 30 November after the end of its financial year."""
    fy_end_year = invoice_date.year + 1 if invoice_date.month >= 4 else invoice_date.year
    return date(fy_end_year, 11, 30)
