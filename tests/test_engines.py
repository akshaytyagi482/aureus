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


def test_policy_on_sample():
    assets = policy.detect_capital_assets(parse_books("tally", sample.tally_xml()))
    by_class = {}
    for a in assets:
        by_class[a.asset_class] = by_class.get(a.asset_class, 0) + a.value
    assert by_class["plant_machinery"] == 5000000
    assert by_class["solar"] == 1450000  # modules counted on line total, plus inverter
    prof = policy.Profile("27", category="small", monthly_power_bill=450000, has_udyam=True)
    result = policy.match(prof, assets)
    psi = next(s for s in result["schemes"] if s["scheme_id"] == "mh-psi-2019-ips")
    assert psi["status"] == "eligible"
    assert psi["eligible_cost"] == 5000000 + 1800000 + 450000 + 1450000
    assert psi["estimated_benefit"] == pytest.approx(0.30 * psi["eligible_cost"])
    assert all(s["scheme_id"] != "gj-msme-2022-capital" for s in result["schemes"])


def test_policy_cap_and_missing_inputs():
    big = [policy.CapitalAsset("plant_machinery", "Press line", "8462", 9e7, date(2026, 6, 1))]
    result = policy.match(policy.Profile("27", category="micro"), big)
    psi = next(s for s in result["schemes"] if s["scheme_id"] == "mh-psi-2019-ips")
    assert psi["estimated_benefit"] == 10000000 and psi["capped_at"] == 10000000
    power = next(s for s in result["schemes"] if s["scheme_id"] == "mh-electricity-duty")
    assert power["status"] == "needs_info"
    assert any("Udyam" in m for m in psi["missing_inputs"])


def test_term_loan_basis_assumption():
    assets = [policy.CapitalAsset("plant_machinery", "VMC", "8457", 4e6, date(2026, 6, 1))]
    result = policy.match(policy.Profile("24", category="small", has_udyam=True), assets)
    gj = next(s for s in result["schemes"] if s["scheme_id"] == "gj-msme-2022-capital")
    assert gj["estimated_benefit"] == pytest.approx(0.25 * 0.75 * 4e6)
    assert gj["assumptions"]


def test_calculator():
    result = policy.calculator("27", "manufacturing", {"plant_machinery": 5e6}, turnover=8e7, investment_pm=2e7)
    assert result["category"] == "micro"
    psi = next(s for s in result["schemes"] if s["scheme_id"] == "mh-psi-2019-ips")
    assert psi["estimated_benefit"] == pytest.approx(2e6)
    assert "illustrative" in result["disclaimer"]


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
