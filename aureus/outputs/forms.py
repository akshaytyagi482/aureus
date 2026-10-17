"""Pre-filled subsidy application packets (District Industries Centre style).

The packet is returned both as structured data (for the UI to review) and as a
self-contained printable HTML document (for download / print to PDF).
"""

from datetime import date
from html import escape

from ..gstin import state_name
from ..money import inr

TEMPLATES = {
    "dic_capital_subsidy": "Application for Capital Investment Subsidy",
    "dic_power_incentive": "Application for Power Tariff / Electricity Duty Incentive",
    "zed_registration": "ZED Certification - Registration and Subsidy Claim",
}


def build_packet(org: dict, scheme: dict) -> dict:
    def field(label, value, source):
        return {"label": label, "value": value, "source": source, "missing": value in (None, "")}

    applicant = [
        field("Name of enterprise", org["name"], "Business profile"),
        field("GSTIN", org["gstin"], "Business profile"),
        field("PAN", org["gstin"][2:12], "Derived from GSTIN"),
        field("Udyam Registration No.", org.get("udyam"), "Business profile"),
        field("Enterprise category", (org.get("category") or "").title(), "Turnover and P&M investment"),
        field("Sector / industry", f"{org.get('sector', '').title()} - {org.get('industry') or ''}".strip(" -"), "Business profile"),
        field("Unit address", org.get("address"), "Business profile"),
        field("District", org.get("district"), "Business profile"),
        field("State", state_name(org["state_code"]), "Derived from GSTIN"),
    ]
    investment = [
        field("Eligible investment (excl. GST)", scheme["eligible_cost"], "Purchase register"),
        field("Term loan amount", org.get("term_loan_amount"), "Business profile"),
        field("Subsidy claimed (estimate)", scheme["estimated_benefit"], "Policy engine: " + (scheme["basis"] or "")),
    ]
    missing = [f["label"] for f in applicant + investment if f["missing"] and f["label"] != "Term loan amount"]
    return {
        "title": TEMPLATES.get(scheme["form_template"], "Subsidy application"),
        "scheme": {k: scheme[k] for k in ("scheme_id", "name", "authority", "source_url", "verified", "notes")},
        "sections": [{"title": "Part A - Applicant details", "fields": applicant},
                     {"title": "Part B - Investment and claim", "fields": investment}],
        "annexure": scheme.get("qualifying_assets", []),
        "documents": [{"name": d, "auto_attached": d.startswith("Purchase invoices")} for d in scheme["documents"]],
        "missing_fields": missing,
        "ready_to_file": not missing and scheme["status"] == "eligible",
        "generated_on": date.today().isoformat(),
    }


def _fmt(value) -> str:
    if isinstance(value, (int, float)):
        return inr(value)
    return escape(str(value)) if value not in (None, "") else '<span class="missing">To be filled</span>'


def render_html(packet: dict) -> str:
    s = packet["scheme"]
    parts = [f"""<!doctype html><html><head><meta charset="utf-8"><title>{escape(packet['title'])}</title>
<style>
body{{font-family:Georgia,serif;max-width:860px;margin:32px auto;color:#111;line-height:1.45}}
h1{{font-size:20px;text-align:center;margin-bottom:4px}} h2{{font-size:15px;border-bottom:1px solid #333;margin-top:28px}}
.sub{{text-align:center;color:#444;font-size:13px}} table{{width:100%;border-collapse:collapse;font-size:13px}}
td,th{{border:1px solid #888;padding:6px 8px;text-align:left;vertical-align:top}} th{{background:#f1f1f1}}
.missing{{color:#b00020;font-style:italic}} .warn{{border:1px solid #b26a00;background:#fff6e5;padding:8px 12px;font-size:13px}}
.num{{text-align:right}} .sign{{margin-top:48px;display:flex;justify-content:space-between}}
@media print{{.warn{{display:none}}}}
</style></head><body>
<h1>{escape(packet['title'])}</h1>
<div class="sub">{escape(s['name'])}<br>{escape(s['authority'])}</div>"""]
    if not s["verified"]:
        parts.append('<p class="warn">Prepared by AureusRecon from an unverified rule grid. Check the current '
                     'official form and scheme notification before submission. This box does not print.</p>')
    for section in packet["sections"]:
        parts.append(f"<h2>{escape(section['title'])}</h2><table>")
        for f in section["fields"]:
            parts.append(f"<tr><th style='width:38%'>{escape(f['label'])}</th><td>{_fmt(f['value'])}</td></tr>")
        parts.append("</table>")
    parts.append("<h2>Annexure I - Statement of eligible investment</h2><table><tr><th>#</th><th>Invoice</th>"
                 "<th>Date</th><th>Supplier (GSTIN)</th><th>Item</th><th>HSN</th><th class='num'>Value (excl. GST)</th></tr>")
    for i, a in enumerate(packet["annexure"], 1):
        parts.append(f"<tr><td>{i}</td><td>{escape(a['invoice_no'])}</td><td>{escape(a['invoice_date'])}</td>"
                     f"<td>{escape(a['supplier_name'])}<br><small>{escape(a['supplier_gstin'])}</small></td>"
                     f"<td>{escape(a['description'])}</td><td>{escape(a['hsn'])}</td><td class='num'>{_fmt(a['value'])}</td></tr>")
    total = sum(a["value"] for a in packet["annexure"])
    parts.append(f"<tr><th colspan='6'>Total</th><th class='num'>{_fmt(total)}</th></tr></table>")
    parts.append("<h2>Enclosures</h2><ol>")
    for d in packet["documents"]:
        tag = " (attached from purchase register)" if d["auto_attached"] else ""
        parts.append(f"<li>{escape(d['name'])}{tag}</li>")
    parts.append("</ol><h2>Declaration</h2><p>I/We declare that the particulars given above are true to the best of "
                 "my/our knowledge and belief, and that the assets listed have not been claimed under any other "
                 "scheme for the same benefit.</p>")
    parts.append(f"<div class='sign'><div>Place: ________<br>Date: {escape(packet['generated_on'])}</div>"
                 "<div>Signature of proprietor / authorised signatory<br><br>Name & seal: ____________</div></div>")
    parts.append("</body></html>")
    return "".join(parts)
