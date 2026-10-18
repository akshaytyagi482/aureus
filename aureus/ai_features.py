"""The AI features: assistant, notice reader, HSN fixer and supplier matcher.

Each one turns deterministic results into a prompt, asks Gemini for structured
JSON, and checks the answer before returning it. Nothing here changes stored
data or engine results; every output is a suggestion for a person to accept.
"""

import json
import math
import re
from collections import Counter
from datetime import date
from functools import lru_cache

from . import ai, hsn
from .engines import policy, tax

# ---------------------------------------------------------------- 1. ask-your-books assistant

ASSISTANT_SYSTEM = (
    "You are the in-app assistant of AureusRecon, a GST input-tax-credit reconciliation and MSME subsidy tool used "
    "by Indian businesses and their Chartered Accountants. Answer ONLY from the JSON data provided; it is the "
    "business's own reconciliation and subsidy results. If the data does not contain the answer, say so and say "
    "which screen or upload would provide it. Quote rupee amounts exactly as given, in Indian format. Explain GST "
    "terms in plain words. Do not give legal opinions or invent rules, sections, rates or deadlines that are not in "
    "the data. Placeholders such as 'Supplier 3' or 'GSTIN 2' stand for real names; use them as they are. Keep "
    "answers under 180 words, use short bullet points for lists, and reply in the language of the question "
    "(English, Hindi or Hinglish). End with one concrete next step in the app."
)


def _assistant_context(org: dict, recon: dict, subsidy: dict, masker: ai.Masker) -> dict:
    s = recon["summary"]
    rows = sorted((r for r in recon["results"] if r["status"] != tax.MATCHED), key=lambda r: -r["itc_at_risk"])[:40]
    return {
        "business": {"name": masker.name(org["name"], "Your business"), "state": org.get("state"),
                     "category": org.get("category"), "zone": org.get("zone")},
        "period": recon.get("period") or "all periods loaded",
        "filing": recon.get("filing"),
        "summary": {k: s.get(k) for k in ("invoices", "itc_books", "itc_claimable", "itc_at_risk",
                                          "itc_unclaimed_in_portal", "match_rate", "by_status", "portal_only")},
        "suppliers_at_risk": [{"supplier": masker.name(x["supplier_name"]), "invoices": x["invoices"],
                               "itc_at_risk": x["itc_at_risk"]} for x in s.get("suppliers_at_risk", [])[:15]],
        "problem_invoices": [{
            "supplier": masker.name(r["books"]["supplier_name"]), "invoice_no": r["books"]["invoice_no"],
            "date": r["books"]["invoice_date"], "status": r["status"], "why": r["reason_text"],
            "itc_at_risk": r["itc_at_risk"], "ims_action": r.get("ims_action"), "what_to_do": r["action"],
            "warnings": [masker.text(f["text"]) for f in r.get("flags", [])]} for r in rows],
        "in_gstr2b_not_in_books": [{"supplier": masker.name(p["portal"]["supplier_name"]),
                                    "invoice_no": p["portal"]["invoice_no"], "itc_unclaimed": p["itc_unclaimed"]}
                                   for p in recon.get("portal_only", [])[:15]],
        "subsidies": {
            "total_estimated_benefit": subsidy.get("total_estimated_benefit"),
            "schemes": [{"name": x["name"], "status": x["status"], "estimated_benefit": x.get("estimated_benefit"),
                         "basis": x.get("basis"), "verified": x.get("verified"), "conditions": x.get("conditions"),
                         "why_not": x.get("reasons"), "needs": x.get("missing_inputs")}
                        for x in subsidy.get("schemes", [])[:25]],
        },
    }


def ask(org: dict, recon: dict, subsidy: dict, question: str, history: list[dict] | None = None) -> dict:
    masker = ai.Masker()
    context = _assistant_context(org, recon, subsidy, masker)
    turns = "".join(f"\nEarlier question: {masker.text(h.get('q', ''))[:500]}\nEarlier answer: {masker.text(h.get('a', ''))[:1500]}"
                    for h in (history or [])[-3:])
    prompt = (f"DATA:\n{json.dumps(context, ensure_ascii=False, default=str)}\n{turns}\n\n"
              f"QUESTION: {masker.text(question.strip())}")
    answer = ai.generate_text(prompt, ASSISTANT_SYSTEM, max_tokens=1500)
    return {"answer": masker.unmask(answer), "masked": masker.active}


# ---------------------------------------------------------------- 2. notice reader

NOTICE_SCHEMA = {
    "type": "object",
    "properties": {
        "is_gst_notice": {"type": "boolean"},
        "form": {"type": "string", "description": "Form name as printed, e.g. DRC-01C, ASMT-10, DRC-01, DRC-01A"},
        "notice_type": {"type": "string", "enum": ["drc01c", "asmt10", "drc01", "other"]},
        "reference_no": {"type": "string"},
        "notice_date": {"type": "string", "description": "YYYY-MM-DD, or empty"},
        "reply_due_date": {"type": "string", "description": "YYYY-MM-DD, or empty"},
        "gstin": {"type": "string", "description": "GSTIN of the taxpayer the notice is addressed to"},
        "tax_period": {"type": "string", "description": "As printed, e.g. 'September 2026' or 'FY 2024-25'"},
        "period_from": {"type": "string", "description": "Month the notice covers, MMYYYY, or empty"},
        "period_to": {"type": "string", "description": "MMYYYY, or empty"},
        "amounts": {"type": "object", "properties": {k: {"type": "number"} for k in ("igst", "cgst", "sgst", "cess", "total")},
                    "description": "Tax, ITC difference or demand stated in the notice, in rupees"},
        "sections_cited": {"type": "array", "items": {"type": "string"}},
        "issues": {"type": "array", "items": {"type": "string"}, "description": "Each discrepancy alleged, in plain words"},
        "invoices": {"type": "array", "items": {"type": "object", "properties": {
            "supplier_gstin": {"type": "string"}, "invoice_no": {"type": "string"}, "amount": {"type": "number"}}}},
        "summary": {"type": "string", "description": "Two or three sentences: what the officer says and what is asked"},
    },
    "required": ["is_gst_notice", "notice_type", "reference_no", "notice_date", "reply_due_date", "gstin", "amounts",
                 "sections_cited", "issues", "invoices", "summary"],
}

NOTICE_SYSTEM = (
    "You extract facts from Indian GST notices and intimations (Forms DRC-01C, ASMT-10, DRC-01, DRC-01A and similar). "
    "Copy numbers, dates, references and GSTINs exactly as printed; never guess. Leave a field empty (or 0 for "
    "amounts) when it is not in the document. Map the form to notice_type: DRC-01C -> drc01c, ASMT-10 -> asmt10, "
    "DRC-01 -> drc01, anything else -> other. If the document is not a GST notice, set is_gst_notice to false."
)

NOTICE_MIME = {"application/pdf", "image/png", "image/jpeg", "image/webp"}


def _iso(value: str) -> str:
    try:
        return date.fromisoformat((value or "").strip()).isoformat()
    except ValueError:
        return ""


def read_notice(org: dict, recon: dict, data: bytes, mime: str) -> dict:
    if mime not in NOTICE_MIME:
        raise ValueError("Upload the notice as a PDF or a photo (PNG/JPEG)")
    raw = ai.generate_json("Extract the notice details.", NOTICE_SCHEMA, NOTICE_SYSTEM, files=[(data, mime)])
    if not raw.get("is_gst_notice"):
        return {"is_gst_notice": False, "summary": raw.get("summary", ""), "warnings": ["This does not look like a GST notice."]}
    warnings = []
    notice_gstin = (raw.get("gstin") or "").strip().upper()
    if notice_gstin and notice_gstin != org["gstin"]:
        warnings.append(f"The notice is addressed to GSTIN {notice_gstin}, not this business ({org['gstin']}).")
    # Tie the invoices the officer lists to this business's reconciliation rows.
    by_no = {}
    for r in recon["results"]:
        by_no.setdefault(tax.normalize_invoice_no(r["books"]["invoice_no"]), []).append(r)
    matched, unmatched = [], []
    for inv in raw.get("invoices") or []:
        rows = by_no.get(tax.normalize_invoice_no(inv.get("invoice_no", "")), [])
        if inv.get("supplier_gstin"):
            rows = [r for r in rows if r["books"]["supplier_gstin"] == inv["supplier_gstin"].strip().upper()] or rows
        if rows:
            matched.append(rows[0]["id"])
        else:
            unmatched.append(inv.get("invoice_no", ""))
    period = (raw.get("period_to") or raw.get("period_from") or "").strip()
    out = {
        "is_gst_notice": True,
        "form": raw.get("form", ""),
        "notice_type": raw.get("notice_type") if raw.get("notice_type") in ("drc01c", "asmt10", "drc01") else "other",
        "reference_no": (raw.get("reference_no") or "")[:80],
        "notice_date": _iso(raw.get("notice_date")),
        "reply_due_date": _iso(raw.get("reply_due_date")),
        "gstin": notice_gstin,
        "tax_period": raw.get("tax_period", ""),
        "period": period if re.fullmatch(r"\d{6}", period) else None,
        "amounts": {k: round(float((raw.get("amounts") or {}).get(k) or 0), 2) for k in ("igst", "cgst", "sgst", "cess", "total")},
        "sections_cited": raw.get("sections_cited") or [],
        "issues": raw.get("issues") or [],
        "summary": raw.get("summary", ""),
        "result_ids": matched,
        "invoices_not_in_books": unmatched,
        "warnings": warnings,
    }
    if out["notice_type"] == "other":
        warnings.append(f"Form {out['form'] or 'unknown'} has no reply template yet; the closest one can still be used.")
    if out["reply_due_date"]:
        days = (date.fromisoformat(out["reply_due_date"]) - date.today()).days
        out["days_to_reply"] = days
        if days < 0:
            warnings.append("The reply date on the notice has passed. Reply at once and ask for condonation.")
    return out


# ---------------------------------------------------------------- 3. HSN fixer

STOP = set("the and for with from of in on to a an by as or at per pcs nos no set kg ltr mtr other others "
           "type size new item items part parts".split())


# Shop-floor shorthand -> words used in the tariff text.
SYNONYMS = {
    "ms": "iron non-alloy steel", "mild": "iron non-alloy", "gi": "galvanised zinc", "ss": "stainless steel",
    "solar": "photovoltaic", "cnc": "numerically controlled", "vmc": "machining centres", "hmc": "machining centres",
    "vfd": "static converters", "inverter": "static converters", "ups": "static converters", "plc": "programmable controller",
    "dg": "generating sets", "genset": "generating sets", "pump": "pumps liquids", "motor": "electric motors",
    "led": "light-emitting diode", "pcb": "printed circuits", "ac": "air conditioning", "hvac": "air conditioning",
    "etp": "filtering purifying water", "stp": "filtering purifying water", "effluent": "filtering purifying water",
    "compressor": "compressors", "bearing": "ball bearings", "ppe": "protective", "hdpe": "polyethylene",
}


def _tokens(text: str) -> list[str]:
    raw = re.findall(r"[a-z]{2,}", (text or "").lower())
    words = [w for r in raw for w in ([r] + SYNONYMS.get(r, "").split()) if len(w) >= 3]
    return [w[:-1] if w.endswith("s") and len(w) > 4 else w for w in words if w not in STOP]


@lru_cache
def _hsn_index():
    """Every 8-digit tariff line with its readable description and token set, plus token rarity weights."""
    lines = {code: hsn.describe(code)["description"] or "" for code in hsn._india()["hsn"] if len(code) == 8}
    toks = {code: set(_tokens(text)) for code, text in lines.items()}
    df = Counter(t for ts in toks.values() for t in ts)
    idf = {t: math.log(len(toks) / n) for t, n in df.items()}
    return lines, toks, idf


def hsn_candidates(description: str, limit: int = 30) -> list[dict]:
    lines, toks, idf = _hsn_index()
    want = set(_tokens(description))
    if not want:
        return []
    scored = []
    for code, ts in toks.items():
        score = sum(idf.get(t, 0) for t in want & ts)
        if score:
            scored.append((score, code))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [{"hsn": code, "description": lines[code]} for _, code in scored[:limit]]


HSN_SCHEMA = {
    "type": "object",
    "properties": {"suggestions": {"type": "array", "items": {"type": "object", "properties": {
        "item": {"type": "integer"}, "hsn": {"type": "string", "description": "8-digit code from that item's candidates, or empty"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]}, "reason": {"type": "string"}},
        "required": ["item", "hsn", "confidence", "reason"]}}},
    "required": ["suggestions"],
}

HSN_SYSTEM = (
    "You classify goods bought by Indian manufacturers under India's 8-digit GST HSN tariff. For each item, choose "
    "the single best code ONLY from that item's candidate list, or return an empty code if none fits. Judge by what "
    "the item is (material, function), not by shared words. Keep each reason under 20 words."
)

PROBLEM_STATUSES = {"missing", "unknown", "unknown_heading", "unlisted"}


def hsn_problems(books: list) -> list[dict]:
    """Distinct invoice lines whose HSN is missing, invalid or not a real tariff line."""
    found: dict[str, dict] = {}
    for inv in books:
        for line in inv.lines:
            status = hsn.describe(line.hsn)["status"]
            if status not in PROBLEM_STATUSES or not line.description:
                continue
            key = line.description.strip().lower()
            item = found.setdefault(key, {"description": line.description.strip(), "current_hsn": line.hsn or "",
                                          "status": status, "invoices": 0, "value": 0.0})
            item["invoices"] += 1
            item["value"] = round(item["value"] + line.taxable_value, 2)
    return sorted(found.values(), key=lambda x: -x["value"])


def suggest_hsn(items: list[dict], limit: int = 20) -> list[dict]:
    items = items[:limit]
    blocks, cands = [], []
    for i, item in enumerate(items):
        c = hsn_candidates(item["description"])
        cands.append({x["hsn"] for x in c})
        listed = "\n".join(f"  {x['hsn']}: {x['description'][:160]}" for x in c) or "  (no candidates)"
        blocks.append(f"Item {i}: \"{item['description']}\" (code in books: {item['current_hsn'] or 'none'})\n{listed}")
    picks = {}
    if any(cands):
        raw = ai.generate_json("\n\n".join(blocks), HSN_SCHEMA, HSN_SYSTEM, lite=True)
        picks = {s.get("item"): s for s in raw.get("suggestions", []) if isinstance(s.get("item"), int)}
    out = []
    for i, item in enumerate(items):
        pick = picks.get(i) or {}
        code = re.sub(r"\D", "", pick.get("hsn") or "")
        suggestion = None
        if code in cands[i]:  # the model may only pick from the shortlist built from the official directory
            asset = policy.classify_hsn(code)
            suggestion = {"hsn": code, "description": hsn.describe(code)["description"], "confidence": pick.get("confidence"),
                          "reason": pick.get("reason", ""), "asset_class": asset,
                          "subsidy_note": "Counts as a capital asset for subsidies" if asset else None}
        out.append({**item, "suggestion": suggestion})
    return out


# ---------------------------------------------------------------- 4. supplier matcher

MATCH_SCHEMA = {
    "type": "object",
    "properties": {"verdicts": {"type": "array", "items": {"type": "object", "properties": {
        "pair": {"type": "integer"}, "verdict": {"type": "string", "enum": ["same", "different", "unsure"]},
        "reason": {"type": "string"}}, "required": ["pair", "verdict", "reason"]}}},
    "required": ["verdicts"],
}

MATCH_SYSTEM = (
    "You compare a purchase bill recorded in a buyer's books with an invoice in the buyer's GSTR-2B that did not "
    "match automatically. Decide if they are the same supply. Supplier names may be abbreviated, misspelt or use a "
    "trade name; invoice numbers may have different prefixes, separators or leading zeros. 'same_pan' says whether "
    "both GSTINs belong to the same PAN (same legal entity). Answer 'same' only when name, amount and date all fit. "
    "Keep each reason under 20 words."
)


def _name_similarity(a: str, b: str) -> float:
    ta, tb = set(_tokens(a)), set(_tokens(b))
    return round(len(ta & tb) / len(ta | tb), 2) if ta and tb else 0.0


def candidate_pairs(recon: dict, limit: int = 30) -> list[dict]:
    books = [r for r in recon["results"] if r["status"] == tax.MISSING]
    pairs = []
    for b in books:
        bi = b["books"]
        for p in recon.get("portal_only", []):
            pi = p["portal"]
            days = abs((date.fromisoformat(bi["invoice_date"]) - date.fromisoformat(pi["invoice_date"])).days)
            close = abs(bi["taxable_value"] - pi["taxable_value"]) <= max(1.0, 0.01 * bi["taxable_value"]) \
                or abs(bi["total_tax"] - pi["total_tax"]) <= 1.0
            if close and days <= 60:
                pairs.append({"books_id": b["id"], "portal_id": p["id"], "books": bi, "portal": pi, "days_apart": days,
                              "same_pan": bi["supplier_gstin"][2:12] == pi["supplier_gstin"][2:12],
                              "name_similarity": _name_similarity(bi["supplier_name"], pi["supplier_name"])})
    pairs.sort(key=lambda x: (-x["same_pan"], -x["name_similarity"], x["days_apart"]))
    return pairs[:limit]


def match_suppliers(recon: dict) -> list[dict]:
    pairs = candidate_pairs(recon)
    if not pairs:
        return []
    lines = []
    for i, x in enumerate(pairs):
        b, p = x["books"], x["portal"]
        lines.append(f"Pair {i}: BOOKS '{b['supplier_name']}' inv {b['invoice_no']} {b['invoice_date']} taxable "
                     f"{b['taxable_value']} tax {b['total_tax']} | GSTR-2B '{p['supplier_name']}' inv {p['invoice_no']} "
                     f"{p['invoice_date']} taxable {p['taxable_value']} tax {p['total_tax']} | same_pan={x['same_pan']} "
                     f"days_apart={x['days_apart']}")
    raw = ai.generate_json("\n".join(lines), MATCH_SCHEMA, MATCH_SYSTEM, lite=True)
    verdicts = {v.get("pair"): v for v in raw.get("verdicts", [])}
    out = []
    for i, x in enumerate(pairs):
        v = verdicts.get(i)
        if not v or v.get("verdict") == "different":
            continue
        b, p = x["books"], x["portal"]
        fix = []
        if b["supplier_gstin"] != p["supplier_gstin"]:
            fix.append(f"supplier GSTIN in books {b['supplier_gstin']} -> {p['supplier_gstin']}")
        if b["invoice_no"] != p["invoice_no"]:
            fix.append(f"invoice number {b['invoice_no']} -> {p['invoice_no']}")
        out.append({"books_id": x["books_id"], "portal_id": x["portal_id"], "verdict": v["verdict"], "reason": v.get("reason", ""),
                    "books": {k: b[k] for k in ("supplier_name", "supplier_gstin", "invoice_no", "invoice_date", "invoice_value")},
                    "portal": {k: p[k] for k in ("supplier_name", "supplier_gstin", "invoice_no", "invoice_date", "invoice_value")},
                    "itc": b["total_tax"], "fix": "Correct the " + " and ".join(fix) + " in your books, then re-import." if fix
                    else "Same details; check the tax period it was booked in."})
    return out
