from datetime import date

import pytest

from aureus import gstin, sample
from aureus.engines import deadlines, policy, tax
from aureus.ingestion import parse_books, parse_gstr2b
from aureus.models import Invoice, InvoiceLine

TODAY = date(2026, 10, 17)


def test_gstin_checksum():
    assert gstin.is_valid("27AAPFU0939F1ZV")
    assert gstin.is_valid("29AAGCB7383J1Z4")
    assert not gstin.is_valid("27AAPFU0939F1ZX")
    assert not gstin.is_valid("99AAPFU0939F1ZV")
    assert gstin.describe("27AAPFU0939F1ZV")["state"] == "Maharashtra"


def test_normalize_invoice_no():
    assert tax.normalize_invoice_no("inv-0042") == tax.normalize_invoice_no("INV/42")
    assert tax.normalize_invoice_no("A/2026/007") == "A20267"


@pytest.fixture
def recon():
    books = parse_books("tally", sample.tally_xml())
    portal = parse_gstr2b(sample.gstr2b_json())
    return tax.reconcile(books, portal, "27", today=TODAY)


def test_sample_reconciles_to_planted_outcomes(recon):
    keys = sample.book_key_by_invoice_no()
    got = {}
    for row in recon["results"]:
        got.setdefault(row["status"], set()).add(keys[row["books"]["invoice_no"]])
    assert got == sample.EXPECTED


def test_paisa_boundary(recon):
    by_no = {r["books"]["invoice_no"]: r for r in recon["results"]}
    assert by_no["WL/790"]["status"] == tax.MATCHED  # ₹0.99 off
    assert by_no["WL/790"]["itc_at_risk"] == 0
    assert by_no["SPI/0461"]["status"] == tax.MISMATCH  # ₹1.00 off
    assert by_no["SPI/0461"]["reason"] == "VALUE_MISMATCH"


def test_reasons_and_flags(recon):
    by_no = {r["books"]["invoice_no"]: r for r in recon["results"]}
    assert by_no["BST/2026/1201"]["reason"] == "TAX_HEAD_MISMATCH"
    assert by_no["BST/2026/1201"]["itc_claimable"] == 0
    assert any(f["code"] == "WRONG_TAX_TYPE" for f in by_no["BST/2026/1201"]["flags"])
    assert by_no["DC-0951"]["reason"] == "INVOICE_NO_MISMATCH"
    assert by_no["PIS/2026/333"]["reason"] == "SUPPLIER_GSTIN_MISMATCH"
    assert by_no["WL/801"]["reason"] == "ITC_NOT_AVAILABLE"
    assert by_no["inv-0042"]["match_basis"] == "normalized_invoice_no"
    assert any(f["code"] == "POSSIBLY_BLOCKED" for f in by_no["MM/INV/5521"]["flags"])
    assert by_no["BST/2026/1187"]["itc_at_risk"] == 151200.0


def test_summary_totals(recon):
    s = recon["summary"]
    assert s["by_status"]["MISSING_IN_PORTAL"]["count"] == 3
    assert len(recon["portal_only"]) == 2
    assert s["itc_unclaimed_in_portal"] == 32040.0
    assert s["itc_books"] == pytest.approx(s["itc_claimable"] + s["itc_at_risk"], abs=0.01)
    assert s["suppliers_at_risk"][0]["supplier_name"] == "Bharat Steel Traders"


def test_deadlines():
    status = deadlines.filing_status(TODAY, "27")
    assert status["period"] == "092026"
    assert status["gstr3b_due_on"] == "2026-10-20"
    assert status["days_left"] == 3
    assert deadlines.gstr3b_due("092026", "27", "quarterly") == date(2026, 10, 22)
    assert deadlines.gstr3b_due("092026", "09", "quarterly") == date(2026, 10, 24)
    assert deadlines.itc_time_limit(date(2026, 3, 15)) == date(2026, 11, 30)
    assert deadlines.itc_time_limit(date(2026, 9, 15)) == date(2027, 11, 30)


def test_enterprise_classification():
    assert policy.classify_enterprise(2e7, 9e7) == "micro"
    assert policy.classify_enterprise(1.8e8, 3.2e8) == "small"
    assert policy.classify_enterprise(5e8, 1e9) == "medium"
    assert policy.classify_enterprise(2e9, 1e9) == "large"


def test_hsn_classification():
    assert policy.classify_hsn("84581100") == "plant_machinery"
    assert policy.classify_hsn("84713010") == "it_hardware"
    assert policy.classify_hsn("85414300") == "solar"
    assert policy.classify_hsn("90318000") == "testing_equipment"
    assert policy.classify_hsn("72142090") is None


def _scheme(result, scheme_id):
    return next((s for s in result["schemes"] if s["scheme_id"] == scheme_id), None)


def test_policy_on_sample_group_a():
    assets = policy.detect_capital_assets(parse_books("tally", sample.tally_xml()))
    by_class = {}
    for a in assets:
        by_class[a.asset_class] = by_class.get(a.asset_class, 0) + a.value
    assert by_class["plant_machinery"] == 5000000
    assert by_class["solar"] == 1450000  # modules counted on line total, plus inverter
    prof = policy.Profile("27", category="small", zone="A", term_loan_amount=6e6,
                          monthly_electricity_duty=28000, monthly_power_units=60000, has_udyam=True)
    result = policy.match(prof, assets, include_ineligible=True)
    ips = _scheme(result, "mh-miisp-2025-ips")
    assert ips["status"] == "eligible" and ips["verified"]
    assert ips["eligible_cost"] == 5000000 + 1800000 + 450000 + 1450000
    assert ips["estimated_benefit"] == pytest.approx(0.30 * ips["eligible_cost"])  # Group A: 30% of FCI
    assert "5 years" in ips["basis"]
    # Group A units get neither the electricity duty exemption nor the power tariff subsidy.
    assert _scheme(result, "mh-miisp-2025-electricity-duty")["status"] == "not_eligible"
    assert _scheme(result, "mh-miisp-2025-power-tariff")["status"] == "not_eligible"
    interest = _scheme(result, "mh-miisp-2025-interest")
    assert interest["estimated_benefit"] == pytest.approx(300000) and not interest["in_total"]
    assert _scheme(result, "mh-miisp-2025-zed")["estimated_benefit"] == pytest.approx(0.5 * 72000 * 0.4)  # Gold fee since Sep 2024
    counted = [s for s in result["schemes"] if s["status"] == "eligible" and s["in_total"]]
    assert result["total_estimated_benefit"] == pytest.approx(sum(s["estimated_benefit"] for s in counted))
    assert result["unverified_in_total"] == []
    assert not [x for x in result["schemes"] if x["scheme_id"].startswith("gj-") and x["status"] == "eligible"]


@pytest.mark.parametrize("zone,ceiling,years", [("B", 0.40, 7), ("C", 0.50, 7), ("D", 0.60, 10), ("D+", 0.70, 10),
                                                ("VM", 0.80, 10), ("NI", 1.00, 10)])
def test_miisp_zone_ceilings(zone, ceiling, years):
    assets = [policy.CapitalAsset("plant_machinery", "VMC", "8457", 1e7, date(2026, 6, 1))]
    result = policy.match(policy.Profile("27", category="micro", zone=zone, has_udyam=True), assets)
    ips = _scheme(result, "mh-miisp-2025-ips")
    assert ips["estimated_benefit"] == pytest.approx(ceiling * 1e7)
    assert f"{years} years" in ips["basis"]


def test_miisp_power_benefits_by_zone():
    prof = policy.Profile("27", category="small", zone="D", monthly_electricity_duty=10000,
                          monthly_power_units=50000, has_udyam=True)
    result = policy.match(prof, [])
    assert _scheme(result, "mh-miisp-2025-electricity-duty")["estimated_benefit"] == 10000 * 12 * 10
    assert _scheme(result, "mh-miisp-2025-power-tariff")["estimated_benefit"] == 50000 * 12 * 3
    prof.zone = "C"
    result = policy.match(prof, [], include_ineligible=True)
    assert _scheme(result, "mh-miisp-2025-electricity-duty")["estimated_benefit"] == 10000 * 12 * 7
    assert _scheme(result, "mh-miisp-2025-power-tariff")["status"] == "not_eligible"


def test_power_tariff_cap():
    prof = policy.Profile("27", category="small", zone="D+", monthly_power_units=5e6, has_udyam=True)
    assert _scheme(policy.match(prof, []), "mh-miisp-2025-power-tariff")["estimated_benefit"] == 10000000


def test_missing_zone_and_udyam():
    assets = [policy.CapitalAsset("plant_machinery", "Press", "8462", 9e6, date(2026, 6, 1))]
    result = policy.match(policy.Profile("27", category="micro"), assets)
    ips = _scheme(result, "mh-miisp-2025-ips")
    assert ips["status"] == "needs_info" and ips["estimated_benefit"] == 0
    assert any("zone" in m for m in ips["missing_inputs"])
    assert any("Udyam" in m for m in ips["missing_inputs"])


def test_purchases_before_policy_window_do_not_count():
    assets = [policy.CapitalAsset("plant_machinery", "Old lathe", "8458", 5e6, date(2025, 6, 1))]
    result = policy.match(policy.Profile("27", category="small", zone="C", has_udyam=True), assets, include_ineligible=True)
    assert _scheme(result, "mh-miisp-2025-ips")["status"] == "not_eligible"


def test_unverified_schemes_flagged_in_disclaimer():
    # Andhra Pradesh figures could only be taken from secondary sources, so they stay unverified.
    assets = [policy.CapitalAsset("plant_machinery", "VMC", "8457", 4e6, date(2026, 6, 1))]
    result = policy.match(policy.Profile("37", category="small", has_udyam=True), assets, today=TODAY)
    ap = _scheme(result, "ap-medp4-capital-gen")
    assert ap["estimated_benefit"] == pytest.approx(0.25 * 4e6) and not ap["verified"]
    assert ap["name"] in result["unverified_in_total"]
    assert "not yet verified" in result["disclaimer"]


def test_either_or_benefits_count_once():
    # Chhattisgarh: SGST refund OR capital subsidy, never both.
    assets = [policy.CapitalAsset("plant_machinery", "VMC", "8457", 2e7, date(2026, 6, 1))]
    result = policy.match(policy.Profile("22", category="small", zone="G2", has_udyam=True), assets, today=TODAY)
    group = [s for s in result["schemes"] if s.get("exclusive_group") == "cg-sgst-or-capital" and s["in_total"]
             and s["status"] == "eligible"]
    counted = sum(s["estimated_benefit"] for s in result["schemes"] if s["status"] == "eligible" and s["in_total"])
    assert len(group) == 2  # general SGST and general capital both qualify...
    assert result["total_estimated_benefit"] == pytest.approx(counted - min(s["estimated_benefit"] for s in group))


def test_lapsed_states_have_no_live_schemes():
    coverage = policy.rule_grid()["coverage"]
    for code in ("02", "07", "20", "35"):  # Himachal, Delhi, Jharkhand, Andaman & Nicobar
        assert coverage[code]["status"] != "current"
        assert not [s for s in policy.rule_grid()["schemes"] if s["states"] == [code]]


def test_conditional_schemes_stay_out_of_total():
    assets = [policy.CapitalAsset("plant_machinery", "VMC", "8457", 4e6, date(2026, 6, 1))]
    result = policy.match(policy.Profile("36", category="small", has_udyam=True), assets, today=TODAY)
    women = _scheme(result, "tg-msme2024-capital-women")
    assert women["conditions"] and not women["in_total"]


def test_calculator():
    result = policy.calculator("27", "manufacturing", {"plant_machinery": 5e6}, turnover=8e7, investment_pm=2e7, zone="B")
    assert result["category"] == "micro"
    assert _scheme(result, "mh-miisp-2025-ips")["estimated_benefit"] == pytest.approx(2e6)


def test_csv_and_zoho_parsers():
    csv_text = ("Party GSTIN,Party Name,Bill No,Bill Date,HSN,Item,Qty,Taxable Value,CGST,SGST,IGST\n"
                "27AAPFU0939F1ZV,Acme,A-1,05/09/2026,8458,Lathe,1,100000,9000,9000,0\n"
                "27AAPFU0939F1ZV,Acme,A-1,05/09/2026,8482,Bearings,10,5000,450,450,0\n")
    invs = parse_books("busy", csv_text.encode())
    assert len(invs) == 1 and invs[0].taxable_value == 105000 and invs[0].cgst == 9450
    zoho = b'{"bills":[{"bill_number":"Z-9","date":"2026-09-02","vendor_name":"V","gst_no":"27AAPFU0939F1ZV",' \
           b'"destination_of_supply":"MH","sub_total":1000,"taxes":[{"tax_name":"CGST9","tax_amount":90},' \
           b'{"tax_name":"SGST9","tax_amount":90}],"line_items":[{"name":"x","hsn_or_sac":"8458","item_total":1000}]}]}'
    z = parse_books("zoho", zoho)[0]
    assert (z.cgst, z.sgst, z.place_of_supply) == (90, 90, "27")


def test_bad_files_raise_clean_errors():
    from aureus.ingestion import IngestionError
    with pytest.raises(IngestionError):
        parse_books("tally", b"<not xml")
    with pytest.raises(IngestionError):
        parse_books("busy", b"foo,bar\n1,2\n")
    with pytest.raises(IngestionError):
        parse_books("sap", b"")


def test_fuzzy_requires_same_supplier_and_amount():
    sup = sample.S["steel"][1]
    b = Invoice(sup, "S", "X-100", date(2026, 9, 1), 1000, cgst=90, sgst=90, lines=[InvoiceLine("a", "7214", 1000)])
    p = Invoice(sup, "S", "X-109", date(2026, 9, 1), 2000, cgst=180, sgst=180)
    result = tax.reconcile([b], [p], "27", today=TODAY)
    assert result["results"][0]["status"] == tax.MISSING
    assert len(result["portal_only"]) == 1


def test_indian_rupee_grouping():
    from aureus.money import inr
    assert inr(220321) == "₹2,20,321.00"
    assert inr(12345678.9) == "₹1,23,45,678.90"
    assert inr(8700000, 0) == "₹87,00,000"
    assert inr(-999.5) == "-₹999.50"


def test_hsn_lookup():
    from aureus import hsn
    assert hsn.describe("84581100")["status"] == "valid"
    assert "Lathes" in hsn.describe("8458 11 00")["description"]
    assert hsn.describe("")["status"] == "missing"
    assert hsn.describe("996511")["status"] == "service"
    assert hsn.describe("98765432")["status"] in ("unknown", "unknown_heading")


def test_hsn_flags_in_reconciliation():
    sup = sample.S["steel"][1]
    b = Invoice(sup, "S", "X-1", date(2026, 9, 1), 1000, cgst=90, sgst=90,
                lines=[InvoiceLine("Mystery part", "98765432", 500), InvoiceLine("No code", "", 500)])
    flags = {f["code"] for f in tax.reconcile([b], [], "27", today=TODAY)["results"][0]["flags"]}
    assert {"HSN_UNKNOWN", "HSN_MISSING"} <= flags


def _with_ims():
    books = parse_books("tally", sample.tally_xml())
    portal = parse_gstr2b(sample.gstr2b_json())
    from aureus.ingestion import parse_ims
    ims = parse_ims(sample.ims_json())
    return tax.reconcile(books, portal, "27", today=TODAY, ims=ims), ims


def test_ims_explains_missing_invoices():
    recon, _ = _with_ims()
    by_no = {r["books"]["invoice_no"]: r for r in recon["results"]}
    assert by_no["BST/2026/1187"]["reason"] == "PENDING_IN_IMS"
    assert by_no["KFC/0931"]["reason"] == "REJECTED_IN_IMS"
    assert by_no["PTC/1214"]["reason"] == "NOT_UPLOADED"
    assert all(by_no[n]["status"] == tax.MISSING for n in ("BST/2026/1187", "KFC/0931", "PTC/1214"))
    assert by_no["DC-0951"]["gstn_category"] == "Partial match"
    assert by_no["PIS/2026/333"]["gstn_category"] == "Probable match"
    assert recon["portal_only"][0]["gstn_category"] == "In GSTR-2B not in PR"


def test_ims_suggestions_are_conservative():
    from aureus.engines import ims_actions
    recon, ims = _with_ims()
    rows = {r["doc_no"]: r for r in ims_actions.recommend(recon, ims)["rows"]}
    assert rows["BST/2026/1187"]["suggested"] == "A" and rows["BST/2026/1187"]["priority"] == "high"
    assert rows["DC-0937"]["suggested"] == "P" and rows["DC-0937"]["priority"] == "high"  # amounts differ
    assert rows["SPI/0455"]["suggested"] == "P"  # in 2B, not in books
    assert rows["BST/2026/1101"]["suggested"] == "A" and rows["BST/2026/1101"]["priority"] == "low"
    assert "17(5)" in rows["MM/INV/5521"]["reason"]
    assert all(r["suggested"] != "R" for r in rows.values())  # never auto-reject


def test_ims_upload_json_shape():
    from aureus.ingestion import ims as ims_mod, parse_ims
    records = parse_ims(sample.ims_json())
    rec = next(r for r in records if r["doc_no"] == "DC-0937")
    out = ims_mod.upload_json("27AAGCS4821K1Z4", [{**rec, "action": "P", "remarks": "Amounts differ"}])
    assert out["rtin"] == "27AAGCS4821K1Z4" and out["reqtyp"] == "SAVE"
    doc = out["invdata"]["b2b"][0]
    assert doc["action"] == "P" and doc["inum"] == "DC-0937" and doc["remarks"] == "Amounts differ"
    assert "tradenm" not in doc


def test_gst_purchase_register_template():
    import io
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Purchase Register"
    ws.append(["GSTIN of recipient* : "])
    for _ in range(3):
        ws.append([])
    ws.append(["GSTIN of Supplier/ECO *", "Trade/Legal name", "Type of inward supplies *", "Document type *",
               "Document number *", "Document date *", "Taxable value (₹) *", "Integrated tax (₹) ", "Central tax (₹) ",
               "State/ UT tax (₹) ", "Cess (₹) "])
    ws.append(["27AAPFU0939F1ZV", "Acme", "B2B", "Invoice", "A/1", "05/09/2026", 1000, 0, 90, 90, 0])
    ws.append(["27AAPFU0939F1ZV", "Acme", "B2B", "Credit Note", "CN/1", "06/09/2026", 100, 0, 9, 9, 0])
    buf = io.BytesIO()
    wb.save(buf)
    inv, cn = parse_books("gst_template", buf.getvalue())
    assert (inv.invoice_no, inv.invoice_date, inv.cgst) == ("A/1", date(2026, 9, 5), 90)
    assert cn.taxable_value == -100 and cn.sgst == -9


def test_every_scheme_evaluates_for_every_state_zone_and_size():
    """No researched rule may crash the engine or produce a negative or absurd estimate."""
    grid = policy.rule_grid()
    assets = [policy.CapitalAsset(c, c, "", 4e6, date(2026, 6, 1)) for c in
              ("plant_machinery", "testing_equipment", "pollution_control", "solar")]
    codes = {c for s in grid["schemes"] for c in s["states"] if c != "*"}
    for code in sorted(codes):
        zones = [None] + list(grid.get("zones_by_state", {}).get(code, {}).get("zones", {}))
        for zone in zones:
            for category in ("micro", "small", "medium"):
                for loan, duty, units in ((None, None, None), (2e7, 25000, 80000)):
                    prof = policy.Profile(code, category=category, zone=zone, term_loan_amount=loan,
                                          monthly_electricity_duty=duty, monthly_power_units=units, has_udyam=True)
                    result = policy.match(prof, assets, include_ineligible=True, today=TODAY)
                    for s in result["schemes"]:
                        assert 0 <= s["estimated_benefit"] <= 5e9, (code, zone, category, s["scheme_id"])
                    assert result["total_estimated_benefit"] >= 0


def test_every_state_has_coverage_status():
    from aureus.gstin import STATES
    coverage = policy.rule_grid()["coverage"]
    missing = [name for code, name in STATES.items() if code not in coverage and code not in ("27", "97")]
    assert not missing, missing
