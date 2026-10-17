#!/usr/bin/env python3
"""AureusRecon connector for Tally Prime.

Runs on the PC where Tally Prime is open. It asks Tally's built-in XML server
(Help > Settings > Connectivity: "TallyPrime acts as Server", default port
9000) for the Day Book in a date range, then pushes the purchase vouchers to
AureusRecon using an organisation-scoped connector key. No Tally or GST
password is involved.

Standard library only, so it runs on any Windows PC with Python 3.9+:

    python tally_push.py --server https://app.example.com --key arc_... \
        --from 2026-09-01 --to 2026-09-30

Use --file to push an XML export you already saved instead of querying Tally.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from datetime import date

DAYBOOK_REQUEST = """<ENVELOPE>
 <HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Data</TYPE><ID>Day Book</ID></HEADER>
 <BODY><DESC><STATICVARIABLES>
  <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
  <SVFROMDATE>{start}</SVFROMDATE><SVTODATE>{end}</SVTODATE>
  {company}
 </STATICVARIABLES></DESC></BODY>
</ENVELOPE>"""


def fetch_from_tally(tally_url: str, start: date, end: date, company: str | None) -> bytes:
    body = DAYBOOK_REQUEST.format(
        start=start.strftime("%Y%m%d"), end=end.strftime("%Y%m%d"),
        company=f"<SVCURRENTCOMPANY>{company}</SVCURRENTCOMPANY>" if company else "",
    ).encode("utf-8")
    req = urllib.request.Request(tally_url, data=body, headers={"Content-Type": "text/xml"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def push(server: str, key: str, xml: bytes) -> dict:
    boundary = uuid.uuid4().hex
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"source\"\r\n\r\ntally\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"daybook.xml\"\r\n"
        "Content-Type: application/xml\r\n\r\n".encode() + xml + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    req = urllib.request.Request(
        server.rstrip("/") + "/api/connector/import/books", data=b"".join(parts), method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "X-Connector-Token": key})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--server", required=True, help="AureusRecon base URL")
    parser.add_argument("--key", required=True, help="Connector key from Data & consent > Tally auto-sync")
    parser.add_argument("--from", dest="start", type=date.fromisoformat, help="Start date YYYY-MM-DD")
    parser.add_argument("--to", dest="end", type=date.fromisoformat, help="End date YYYY-MM-DD")
    parser.add_argument("--tally", default="http://localhost:9000", help="Tally XML server URL")
    parser.add_argument("--company", help="Tally company name, if more than one is open")
    parser.add_argument("--file", help="Push this saved XML export instead of querying Tally")
    args = parser.parse_args()

    try:
        if args.file:
            with open(args.file, "rb") as fh:
                xml = fh.read()
        else:
            if not (args.start and args.end):
                parser.error("--from and --to are required unless --file is given")
            print(f"Reading Day Book {args.start} to {args.end} from Tally at {args.tally} ...")
            xml = fetch_from_tally(args.tally, args.start, args.end, args.company)
        result = push(args.server, args.key, xml)
    except urllib.error.HTTPError as exc:
        print(f"AureusRecon rejected the upload ({exc.code}): {exc.read().decode(errors='replace')}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Could not connect: {exc.reason}. Is Tally open with the XML server enabled?", file=sys.stderr)
        return 1
    print(f"Pushed {result['records']} purchase invoices for {', '.join(result['periods'])}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
