"""AI features, with Gemini replaced by a stub: these test the plumbing and the checks, not the model."""

import json
from datetime import date

import pytest

from aureus import ai, ai_features, config, sample
from aureus.engines import tax
from aureus.models import Invoice, InvoiceLine

TODAY = date(2026, 10, 10)


class FakeGemini:
    """Records each call and answers by feature (picked from the system prompt)."""

    def __init__(self):
        self.calls = []
        self.answers = {}

    def __call__(self, model, contents, system, schema, max_tokens, json_mode=False):
        self.calls.append({"model": model, "contents": contents, "system": system, "schema": schema})
        for key, answer in self.answers.items():
            if key in system:
                return answer(contents) if callable(answer) else answer
        return "{}" if (schema or json_mode) else "ok"


@pytest.fixture
def gemini(monkeypatch):
    fake = FakeGemini()
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(config, "AI_TIER", "free")
    monkeypatch.setattr(ai, "_call", fake)
    monkeypatch.setattr(ai, "_contents", lambda prompt, files: [prompt, *[f"<{m} {len(d)} bytes>" for d, m in files]])
    return fake


def test_ai_off_without_key(client, owner, demo_org, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    assert client.get("/api/public/meta").json()["ai"]["enabled"] is False
    res = client.post(f"/api/orgs/{demo_org}/ai/ask", headers=owner, json={"question": "why?"})
    assert res.status_code == 503 and res.json()["code"] == "ai_not_configured"


def test_masker_round_trip():
    m = ai.Masker(active=True)
    assert m.name("Bharat Steel Traders") == "Supplier 1" and m.name("Kalyani Forge") == "Supplier 2"
    sent = m.text("Bharat Steel Traders (27AAPFU0939F1ZV) owes us")
    assert "Bharat" not in sent and "27AAPFU0939F1ZV" not in sent
    assert m.unmask("Chase Supplier 2 first, then Supplier 1 (GSTIN 1). Supplier 10 is fine.") == \
        "Chase Kalyani Forge first, then Bharat Steel Traders (27AAPFU0939F1ZV). Supplier 10 is fine."
    assert ai.Masker(active=False).text("Bharat Steel Traders") == "Bharat Steel Traders"


def test_assistant_masks_names_on_free_tier(client, owner, demo_org, gemini):
    client.post(f"/api/orgs/{demo_org}/import/sample", headers=owner)
    gemini.answers["in-app assistant"] = "Chase Supplier 1 first: it holds the most credit."
    res = client.post(f"/api/orgs/{demo_org}/ai/ask", headers=owner, json={"question": "Which supplier should I chase?"})
    assert res.status_code == 200, res.text
    sent = gemini.calls[-1]["contents"][0]
    assert sample.BUYER["gstin"] not in sent and sample.BUYER["name"] not in sent
    assert not ai.GSTIN_RE.search(sent)  # no GSTIN leaves the server on the free tier
    top = client.get(f"/api/orgs/{demo_org}/reconciliation", headers=owner).json()["summary"]["suppliers_at_risk"][0]
    assert res.json()["answer"] == f"Chase {top['supplier_name']} first: it holds the most credit."
    trail = client.get(f"/api/orgs/{demo_org}/audit", headers=owner).json()
    assert any(a["action"] == "ai_used" and a["detail"]["feature"] == "assistant" for a in trail)


def test_notice_reader_fills_form_and_finds_invoices(client, owner, demo_org, gemini):
    client.post(f"/api/orgs/{demo_org}/import/sample", headers=owner)
    recon = client.get(f"/api/orgs/{demo_org}/reconciliation", headers=owner).json()
    missing = next(r for r in recon["results"] if r["status"] == "MISSING_IN_PORTAL")["books"]
    gemini.answers["GST notices"] = json.dumps({
        "is_gst_notice": True, "form": "DRC-01C", "notice_type": "drc01c", "reference_no": "ZD2710260012345",
        "notice_date": "2026-10-05", "reply_due_date": "2026-10-12", "gstin": sample.BUYER["gstin"],
        "tax_period": "September 2026", "period_from": "092026", "period_to": "092026",
        "amounts": {"igst": 0, "cgst": 9000, "sgst": 9000, "cess": 0, "total": 18000},
        "sections_cited": ["Rule 88D"], "issues": ["ITC in 3B exceeds 2B"],
        "invoices": [{"supplier_gstin": missing["supplier_gstin"], "invoice_no": missing["invoice_no"].lower(), "amount": 1},
                     {"supplier_gstin": "", "invoice_no": "NOT-OURS-1", "amount": 1}],
        "summary": "ITC claimed exceeds GSTR-2B."})
    upload = {"file": ("notice.pdf", b"%PDF-1.4 fake", "application/pdf")}
    assert client.post(f"/api/orgs/{demo_org}/ai/read-notice", headers=owner, files=upload).status_code == 409  # free tier
    res = client.post(f"/api/orgs/{demo_org}/ai/read-notice", headers=owner, files=upload,
                      data={"acknowledge_free_tier": "true"})
    assert res.status_code == 200, res.text
    r = res.json()
    assert (r["notice_type"], r["reference_no"], r["period"], r["amounts"]["total"]) == ("drc01c", "ZD2710260012345", "092026", 18000)
    assert len(r["result_ids"]) == 1 and r["invoices_not_in_books"] == ["NOT-OURS-1"]
    bad = client.post(f"/api/orgs/{demo_org}/ai/read-notice", headers=owner, data={"acknowledge_free_tier": "true"},
                      files={"file": ("notice.txt", b"hello", "text/plain")})
    assert bad.status_code == 400


def test_hsn_fixer_only_accepts_codes_from_the_shortlist(gemini):
    books = [Invoice("27AAACB1234C1Z5", "S", "1", TODAY, 300000, lines=[
        InvoiceLine("CNC vertical machining centre", "", 250000),
        InvoiceLine("Corrugated carton box", "48199999", 50000),  # real heading, made-up tariff line
        InvoiceLine("Lathe", "84581100", 1000)])]  # valid: not a problem
    items = ai_features.hsn_problems(books)
    assert [i["description"] for i in items] == ["CNC vertical machining centre", "Corrugated carton box"]
    gemini.answers["8-digit GST HSN"] = json.dumps({"suggestions": [
        {"item": 0, "hsn": "8457 10 20", "confidence": "high", "reason": "vertical machining centre"},
        {"item": 1, "hsn": "99999999", "confidence": "high", "reason": "invented"}]})
    out = ai_features.suggest_hsn(items)
    assert out[0]["suggestion"]["hsn"] == "84571020" and out[0]["suggestion"]["asset_class"] == "plant_machinery"
    assert out[1]["suggestion"] is None  # a code outside the official shortlist is dropped
    assert gemini.calls[-1]["model"] == config.GEMINI_LITE_MODEL


def test_supplier_matcher_suggests_same_bill(gemini):
    books = [Invoice("27AAACB1234C1Z5", "Bharat Steel", "BS/77", date(2026, 9, 3), 100000, cgst=9000, sgst=9000)]
    portal = [Invoice("27AAACB1234C2Z4", "BHARAT STEEL TRADERS", "77", date(2026, 9, 3), 100000, cgst=9000, sgst=9000,
                      return_period="092026"),
              Invoice("29AAACZ9999Z1Z1", "Unrelated Co", "X1", date(2026, 9, 20), 5000, igst=900, return_period="092026")]
    recon = tax.reconcile(books, portal, "27", today=TODAY)
    pairs = ai_features.candidate_pairs(recon)
    assert len(pairs) == 1 and pairs[0]["same_pan"]  # the unrelated invoice is never sent
    gemini.answers["did not match automatically"] = json.dumps({"verdicts": [{"pair": 0, "verdict": "same", "reason": "trade name"}]})
    matches = ai_features.match_suppliers(recon)
    assert matches[0]["verdict"] == "same" and "27AAACB1234C2Z4" in matches[0]["fix"] and "BS/77 -> 77" in matches[0]["fix"]


def test_notice_polish_uses_gemini_and_keeps_citations(client, owner, demo_org, gemini):
    client.post(f"/api/orgs/{demo_org}/import/sample", headers=owner)
    gemini.answers["edit reply letters"] = lambda contents: contents[0].replace("Dear Sir", "Respected Sir")
    res = client.post(f"/api/orgs/{demo_org}/notice-copilot", headers=owner, json={"notice_type": "drc01c", "polish": True})
    assert res.status_code == 200 and res.json()["polished"] and res.json()["polished_by"] == "Gemini"
    gemini.answers["edit reply letters"] = "A short letter with every citation removed."
    res = client.post(f"/api/orgs/{demo_org}/notice-copilot", headers=owner, json={"notice_type": "drc01c", "polish": True})
    assert not res.json()["polished"] and "citation" in res.json()["polish_error"]


def test_rate_limit_is_reported(client, owner, demo_org, gemini, monkeypatch):
    client.post(f"/api/orgs/{demo_org}/import/sample", headers=owner)

    def limited(*a, **k):
        raise ai.AIError("Gemini usage limit reached", 429)
    monkeypatch.setattr(ai, "_call", limited)
    res = client.post(f"/api/orgs/{demo_org}/ai/ask", headers=owner, json={"question": "why?"})
    assert res.status_code == 429 and res.headers["Retry-After"] == "60"


def test_demo_data_gives_the_ai_tools_something_to_find():
    from aureus import ingestion
    books = ingestion.parse_books("tally", sample.tally_xml())
    recon = tax.reconcile(books, ingestion.parse_gstr2b(sample.gstr2b_json()), "27", today=TODAY)
    assert [(p["books"]["invoice_no"], p["portal"]["invoice_no"]) for p in ai_features.candidate_pairs(recon)] == [("PTC/1214", "1214")]
    assert {i["description"] for i in ai_features.hsn_problems(books)} == {"Corrugated boxes", "Wooden pallets"}
