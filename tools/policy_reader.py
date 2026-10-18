"""AI policy reader: turn a state policy / GR PDF into a draft rule file for review.

    GEMINI_API_KEY=... python tools/policy_reader.py policy.pdf --state-code 24 --state Gujarat [--url <source>]

Gemini reads the PDF and fills the same JSON shape researchers use (research/BRIEF.md).
Every scheme is forced to "verified": false: a person must check each figure against
the document, set "verified": true with the clause, and move the file to
research/policies/raw/ before running curate.py and merge_policies.py.
Policy documents are public, so the free Gemini tier is fine here.
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from aureus import ai, config  # noqa: E402
from merge_policies import validate  # noqa: E402

BRIEF = (ROOT / "research" / "BRIEF.md").read_text()
SYSTEM = (
    "You extract Indian state industrial / MSME incentive rules from an official policy document into JSON, following "
    "the brief below exactly. Copy every rate, cap, period and date exactly as printed; never round or infer. Record "
    "the clause or paragraph number for every scheme. Rates are decimals (25% -> 0.25). If something is unclear, "
    "say so in that scheme's notes or in 'unverifiable'.\n\n" + BRIEF
)
MAX_INLINE = 18 * 1024 * 1024


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pdf")
    ap.add_argument("--state-code", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--url", default="", help="where the document was downloaded from")
    ap.add_argument("--model", default=None, help=f"Gemini model (default {config.GEMINI_MODEL})")
    args = ap.parse_args()

    data = Path(args.pdf).read_bytes()
    if len(data) > MAX_INLINE:
        print(f"{args.pdf} is over 18 MB; split it first, e.g. qpdf --pages in.pdf 1-40 -- out.pdf", file=sys.stderr)
        return 2
    prompt = (f"State: {args.state} (code {args.state_code}). Source URL: {args.url or 'unknown'}. Today: {date.today()}.\n"
              "Read the attached document and return the JSON object described in the brief.")
    try:
        doc = ai.generate_json(prompt, None, SYSTEM, files=[(data, "application/pdf")], max_tokens=32000, model=args.model)
    except (ai.AIError, ai.AIUnavailable) as exc:
        print(f"Policy reader failed: {exc}", file=sys.stderr)
        return 1
    doc["state_code"], doc["state"] = args.state_code, args.state
    doc.setdefault("policy", {})
    if args.url:
        doc["policy"]["source_url"] = args.url
    for s in doc.get("schemes", []):
        s["verified"] = False  # a person verifies; the model never does
    doc["checked_on"] = date.today().isoformat()
    doc["drafted_by"] = f"AI policy reader ({args.model or config.GEMINI_MODEL}); every figure needs human verification"

    out = ROOT / "research" / "policies" / "drafts" / f"{args.state_code}-{date.today():%Y%m%d}.json"
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    errors = validate(doc, out.name)
    print(f"Draft written: {out.relative_to(ROOT)} ({len(doc.get('schemes', []))} schemes)")
    if errors:
        print("Fix these before merging:\n  " + "\n  ".join(errors))
    return 0


if __name__ == "__main__":
    sys.exit(main())
