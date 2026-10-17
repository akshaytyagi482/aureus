"""Notice Copilot: section-specific reply drafts, and supplier follow-up messages.

Drafts are assembled from vetted templates and a fixed citation list, so every
legal reference in the output comes from `CITATIONS` below. When an Anthropic
API key is configured, Claude can tighten the wording, but it is instructed not
to add citations, and the template is returned unchanged if anything goes wrong.
"""

from datetime import date

from .. import config
from ..engines.tax import MISSING
from ..money import inr as _inr

NOTICE_TYPES = {
    "drc01c": {
        "title": "Reply to intimation in Form GST DRC-01C (Rule 88D)",
        "reply_form": "Part B of Form GST DRC-01C",
        "trigger": "input tax credit claimed in GSTR-3B exceeding the credit available in GSTR-2B",
        "provisions": ["Section 16 of the CGST Act, 2017", "Rule 88D of the CGST Rules, 2017"],
    },
    "asmt10": {
        "title": "Reply to scrutiny notice in Form GST ASMT-10 (Section 61)",
        "reply_form": "Form GST ASMT-11",
        "trigger": "discrepancies noticed on scrutiny of our returns",
        "provisions": ["Section 61 of the CGST Act, 2017", "Rule 99 of the CGST Rules, 2017", "Section 16 of the CGST Act, 2017"],
    },
    "drc01": {
        "title": "Reply to show cause notice in Form GST DRC-01 (Section 73)",
        "reply_form": "Form GST DRC-06",
        "trigger": "proposed demand of input tax credit alleged to have been wrongly availed",
        "provisions": ["Section 73 of the CGST Act, 2017", "Section 16 of the CGST Act, 2017", "Section 75(4) of the CGST Act, 2017"],
    },
}

CITATIONS = {
    "bona_fide": [
        {"cite": "Arise India Ltd. v. Commissioner of Trade & Taxes, Delhi (Delhi High Court, 2017); SLP dismissed by the Supreme Court (2018)",
         "holding": "A purchasing dealer who has acted bona fide cannot be denied input credit for the selling dealer's default.",
         "caveat": "Decided under the Delhi VAT Act; persuasive for GST."},
        {"cite": "Suncraft Energy Pvt. Ltd. v. Assistant Commissioner, State Tax (Calcutta High Court, 2023); department's SLP dismissed by the Supreme Court (2023)",
         "holding": "ITC cannot be reversed solely on a GSTR-2A mismatch without first proceeding against the defaulting supplier.",
         "caveat": "Concerned FY 2017-18, before Section 16(2)(aa) took effect on 1 January 2022."},
        {"cite": "D.Y. Beathel Enterprises v. State Tax Officer (Madras High Court, 2021)",
         "holding": "Where the supplier collected tax and did not remit it, the department should first proceed against the supplier.",
         "caveat": "Fact-specific; relied on where payment to the supplier is proven."},
        {"cite": "Diya Agencies v. State Tax Officer (Kerala High Court, 2023)",
         "holding": "The taxpayer must be given an opportunity to prove the genuineness of the transaction by other evidence despite a GSTR-2A mismatch.",
         "caveat": "Concerned the period before Section 16(2)(aa)."},
    ],
    "procedure": [
        {"cite": "Circular No. 183/15/2022-GST dated 27.12.2022",
         "holding": "Prescribes verification of ITC for GSTR-2A vs GSTR-3B differences through supplier or CA certificates.",
         "caveat": "Applies to FY 2017-18 and 2018-19 only; cite by analogy for later periods."},
    ],
    "adverse": [
        {"cite": "State of Karnataka v. Ecom Gill Coffee Trading Pvt. Ltd. (Supreme Court, 2023)",
         "holding": "The burden of proving the genuineness of a transaction and actual receipt of goods lies on the purchasing dealer claiming credit.",
         "caveat": "Adverse authority (KVAT). Address it by attaching full evidence of receipt and payment."},
    ],
}

REASON_PARAGRAPHS = {
    "NOT_UPLOADED": (
        "The following invoices were received against genuine supplies; the goods/services were received, the tax "
        "invoices are in our possession, and the full consideration including tax has been paid to the suppliers "
        "through banking channels (Annexure A). The difference arises only because the suppliers did not report "
        "these invoices in their GSTR-1/IFF in time. We have followed up with each supplier in writing (Annexure B)."
    ),
    "VALUE_MISMATCH": (
        "For the following invoices, the amounts reported by the supplier differ from the invoice issued to us. "
        "We have restricted / will restrict our claim to the lower of the two amounts and have asked the suppliers "
        "to amend their returns. The reconciliation is at Annexure C."
    ),
    "TAX_HEAD_MISMATCH": (
        "For the following invoices, the total tax matches but was reported under a different head (IGST versus "
        "CGST + SGST). The supplier has been asked to correct the tax type; any credit of tax paid under the wrong "
        "head will be dealt with in accordance with Section 77 of the CGST Act, 2017."
    ),
    "INVOICE_NO_MISMATCH": (
        "For the following invoices, the supply is reflected in GSTR-2B under a marginally different invoice "
        "reference owing to a clerical error. The supplier GSTIN, invoice date and amounts are identical, as "
        "shown in Annexure C. The credit is therefore substantively reflected in GSTR-2B."
    ),
    "SUPPLIER_GSTIN_MISMATCH": (
        "For the following invoices, the supply appears in GSTR-2B under the supplier's correct GSTIN, while our "
        "ledger carried an incorrect GSTIN. The ledger has been corrected; the invoice number, date and amounts "
        "are identical (Annexure C)."
    ),
    "ITC_NOT_AVAILABLE": (
        "For the following invoices, GSTR-2B shows the credit as not available. We have not claimed / have "
        "reversed this credit, as shown in Annexure C."
    ),
    "DATE_MISMATCH": (
        "For the following invoices, the amounts agree with GSTR-2B and only the invoice date differs owing to a "
        "data-entry difference; the supply and tax are identical."
    ),
}


def draft_reply(org: dict, rows: list[dict], notice_type: str, notice_ref: str = "",
                notice_date: str = "", period: str = "") -> dict:
    if notice_type not in NOTICE_TYPES:
        raise ValueError(f"notice_type must be one of {', '.join(NOTICE_TYPES)}")
    meta = NOTICE_TYPES[notice_type]
    rows = [r for r in rows if r["status"] != "MATCHED_ELIGIBLE"]
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["reason"], []).append(row)
    total_risk = sum(r["itc_at_risk"] for r in rows)

    lines = [
        "To,\nThe Proper Officer,\nState/Central Tax Jurisdiction",
        "",
        f"Subject: {meta['title']}" + (f" - Reference {notice_ref}" if notice_ref else "")
        + (f" dated {notice_date}" if notice_date else ""),
        f"Taxpayer: {org['name']} (GSTIN {org['gstin']})" + (f" | Tax period: {period}" if period else ""),
        "",
        "Respected Sir/Madam,",
        "",
        f"1. We refer to the above communication regarding {meta['trigger']}. The difference under "
        f"examination amounts to {_inr(total_risk)} of input tax credit across {len(rows)} invoice(s). We submit "
        f"our reply in {meta['reply_form']} as follows.",
    ]
    para = 2
    for reason, items in groups.items():
        lines += ["", f"{para}. {REASON_PARAGRAPHS.get(reason, 'Details of the following invoices are enclosed.')}"]
        for r in items:
            b = r["books"]
            lines.append(f"   - Invoice {b['invoice_no']} dated {b['invoice_date']} from {b['supplier_name']} "
                         f"(GSTIN {b['supplier_gstin']}): ITC {_inr(r['itc_books'])}, in dispute {_inr(r['itc_at_risk'])}")
        para += 1

    used = []
    if "NOT_UPLOADED" in groups:
        used = CITATIONS["bona_fide"] + CITATIONS["procedure"]
        lines += ["", f"{para}. Section 16(2)(aa) of the CGST Act, 2017 links credit to the details furnished by the "
                  "supplier. We respectfully submit that where the recipient has satisfied every other condition of "
                  "Section 16(2) and has paid the tax to the supplier, recovery should first be pursued against the "
                  "defaulting supplier. We rely on the following:"]
        for c in used:
            lines.append(f"   - {c['cite']}: {c['holding']}")
        lines.append("   We are conscious that the burden of proving the genuineness of the transaction rests on the "
                     "recipient (" + CITATIONS["adverse"][0]["cite"] + "), and have therefore enclosed complete "
                     "evidence of receipt and payment.")
        para += 1

    lines += [
        "",
        f"{para}. In view of the above, we request that the proceedings be dropped"
        + (" and that we be granted a personal hearing under Section 75(4) of the CGST Act, 2017 before any adverse order is passed."
           if notice_type == "drc01" else " and that the explanation be accepted."),
        "",
        "Enclosures:",
        "   Annexure A - Tax invoices, e-way bills, goods receipt notes and bank statements evidencing payment",
        "   Annexure B - Correspondence with suppliers",
        "   Annexure C - Invoice-wise reconciliation of purchase register with GSTR-2B",
        "",
        "Yours faithfully,",
        f"For {org['name']}",
        "",
        "Authorised Signatory",
        f"Date: {date.today().strftime('%d-%m-%Y')}",
    ]
    return {
        "notice_type": notice_type,
        "title": meta["title"],
        "reply_form": meta["reply_form"],
        "provisions": meta["provisions"],
        "citations": used + (CITATIONS["adverse"] if used else []),
        "invoice_count": len(rows),
        "itc_in_dispute": round(total_risk, 2),
        "text": "\n".join(lines),
        "polished": False,
        "review_note": "Draft for professional review. Confirm every citation and the facts before filing.",
    }


SYSTEM_PROMPT = (
    "You edit reply letters that Indian businesses send to GST officers. You receive a draft built from "
    "verified templates. Improve clarity, flow and formality only. Keep every fact, amount, invoice number, "
    "GSTIN, date, annexure reference and paragraph number exactly as given. Do not add, remove or alter any "
    "legal citation, section, rule or case name, and do not introduce new legal arguments. Return only the "
    "revised letter as plain text."
)


def polish_with_claude(draft: dict) -> dict:
    """Optionally refine wording with Claude. Falls back to the template on any problem."""
    try:
        import anthropic
    except ImportError:
        return {**draft, "polish_error": "anthropic package not installed"}
    try:
        client = anthropic.Anthropic()
        response = client.beta.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=16000,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": draft["text"]}],
        )
    except anthropic.APIError as exc:
        return {**draft, "polish_error": f"Claude API error: {exc.__class__.__name__}"}
    except Exception as exc:  # missing credentials, network
        return {**draft, "polish_error": str(exc)}
    if response.stop_reason != "end_turn":
        return {**draft, "polish_error": f"Claude stopped early ({response.stop_reason}); template kept"}
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        return {**draft, "polish_error": "Empty response; template kept"}
    # Guard: keep the template if any citation was dropped.
    for c in draft["citations"]:
        if c["cite"].split(" (")[0].split(" v. ")[0] not in text:
            return {**draft, "polish_error": "Polished text dropped a citation; template kept"}
    return {**draft, "text": text, "polished": True}


def supplier_reminder(org: dict, supplier: dict, rows: list[dict], due_date: str) -> dict:
    pending = [r for r in rows if r["books"]["supplier_gstin"] == supplier["supplier_gstin"] and r["itc_at_risk"] > 0]
    total = sum(r["itc_at_risk"] for r in pending)
    items = []
    for r in pending:
        b = r["books"]
        issue = "not uploaded in your GSTR-1/IFF" if r["status"] == MISSING else r["reason_text"].lower()
        items.append(f"- Invoice {b['invoice_no']} dated {b['invoice_date']}, value {_inr(b['invoice_value'])}: {issue}")
    body = "\n".join([
        f"Dear {supplier['supplier_name']},",
        "",
        f"While reconciling our purchases with GSTR-2B, we found the following invoice(s) from your GSTIN "
        f"{supplier['supplier_gstin']} that need your attention:",
        "",
        *items,
        "",
        f"Input tax credit of {_inr(total)} is held up on our side until these are corrected. Please upload or "
        f"amend them (GSTR-1/IFF or GSTR-1A) before {due_date} so that they reflect in our GSTR-2B.",
        "",
        "Thank you,",
        org["name"],
        f"GSTIN {org['gstin']}",
    ])
    return {
        "supplier": supplier,
        "invoices": len(pending),
        "itc_at_risk": round(total, 2),
        "subject": f"GST invoices pending upload - {org['name']}",
        "body": body,
        "short": f"{org['name']}: {len(pending)} invoice(s) worth ITC {_inr(total)} are missing or mismatched in "
                 f"GSTR-1. Please upload/amend before {due_date}.",
    }
