"""Adapters that turn accounting exports and GST portal files into `Invoice` records.

Each adapter takes the raw file bytes and returns a list of invoices. Live API
connectors (Zoho OAuth, Tally ODBC/XML server, GSP access to GSTR-2B) would
produce the same records; the parsers here handle the exported-file path.
"""

from . import busy, gstr2b, tally, zoho

BOOK_PARSERS = {
    "tally": tally.parse,
    "zoho": zoho.parse,
    "busy": busy.parse,
    "csv": busy.parse,
}


class IngestionError(ValueError):
    pass


def parse_books(source: str, payload: bytes):
    parser = BOOK_PARSERS.get(source)
    if parser is None:
        raise IngestionError(f"Unknown accounting source '{source}'. Use one of: {', '.join(BOOK_PARSERS)}")
    try:
        invoices = parser(payload)
    except IngestionError:
        raise
    except Exception as exc:  # malformed files surface as a clean 400, not a 500
        raise IngestionError(f"Could not read {source} file: {exc}") from exc
    for inv in invoices:
        inv.source = source
    return invoices


def parse_gstr2b(payload: bytes):
    try:
        return gstr2b.parse(payload)
    except Exception as exc:
        raise IngestionError(f"Could not read GSTR-2B file: {exc}") from exc
