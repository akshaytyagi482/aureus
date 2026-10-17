"""Policy engine: match capital purchases and the business profile against the scheme rule grid.

Deterministic by design: every rupee in an estimate traces to a rule in
data/schemes.json and to specific invoice lines.
"""

import json
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache

from ..config import BASE_DIR
from ..models import Invoice
from ..money import inr

# MSME classification by investment in plant & machinery and turnover (revised limits, FY 2025-26 onward).
CATEGORY_LIMITS = [("micro", 2.5e7, 1e8), ("small", 2.5e8, 1e9), ("medium", 1.25e9, 5e9)]

TERM_LOAN_SHARE_ASSUMPTION = 0.75


@lru_cache
def rule_grid() -> dict:
    return json.loads((BASE_DIR / "data" / "schemes.json").read_text())


def classify_enterprise(investment_pm: float | None, turnover: float | None) -> str | None:
    if investment_pm is None and turnover is None:
        return None
    inv, turn = investment_pm or 0, turnover or 0
    for name, max_inv, max_turnover in CATEGORY_LIMITS:
        if inv <= max_inv and turn <= max_turnover:
            return name
    return "large"


def classify_hsn(hsn: str) -> str | None:
    hsn = (hsn or "").replace(" ", "").replace(".", "")
    best, best_len = None, 0
    for name, spec in rule_grid()["asset_classes"].items():
        if any(hsn.startswith(p) for p in spec.get("exclude_prefixes", [])):
            continue
        for prefix in spec["hsn_prefixes"]:
            if hsn.startswith(prefix) and len(prefix) > best_len:
                best, best_len = name, len(prefix)
    return best


@dataclass
class CapitalAsset:
    asset_class: str
    description: str
    hsn: str
    value: float
    invoice_date: date
    invoice_no: str = ""
    supplier_name: str = ""
    supplier_gstin: str = ""

    def to_dict(self) -> dict:
        return {**self.__dict__, "invoice_date": self.invoice_date.isoformat()}


@dataclass
class Profile:
    state_code: str
    sector: str = "manufacturing"
    category: str | None = None
    term_loan_amount: float | None = None
    monthly_power_bill: float | None = None
    has_udyam: bool = False
    extra: dict = field(default_factory=dict)


def detect_capital_assets(invoices: list[Invoice]) -> list[CapitalAsset]:
    """Pick out invoice lines that look like capital goods: an eligible HSN class and a high enough value."""
    grid = rule_grid()
    floor = grid["capital_item_min_unit_value"]
    assets = []
    for inv in invoices:
        for line in inv.lines:
            asset_class = classify_hsn(line.hsn)
            if not asset_class:
                continue
            by_total = grid["asset_classes"][asset_class].get("count_line_total", False)
            if line.unit_value >= floor or (by_total and line.taxable_value >= floor):
                assets.append(CapitalAsset(asset_class, line.description, line.hsn, line.taxable_value,
                                           inv.invoice_date, inv.invoice_no, inv.supplier_name, inv.supplier_gstin))
    return assets


def _rate(rate, category: str | None):
    if isinstance(rate, dict):
        return rate.get(category or "", None)
    return rate


def evaluate_scheme(scheme: dict, profile: Profile, assets: list[CapitalAsset]) -> dict:
    reasons, missing = [], []
    states = scheme["states"]
    if "*" not in states and profile.state_code not in states:
        reasons.append("Unit is not located in an eligible state")
    if profile.sector not in scheme["sectors"]:
        reasons.append(f"Sector '{profile.sector}' is not covered")
    if profile.category is None:
        missing.append("enterprise category (enter turnover and plant & machinery investment)")
    elif profile.category not in scheme["categories"]:
        reasons.append(f"{profile.category.title()} enterprises are not covered")

    start = date.fromisoformat(scheme["window"]["from"])
    end = date.fromisoformat(scheme["window"]["to"])
    qualifying = [a for a in assets
                  if a.asset_class in scheme["asset_classes"] and start <= a.invoice_date <= end]
    eligible_cost = round(sum(a.value for a in qualifying), 2)

    benefit = scheme["benefit"]
    estimate, basis_text, assumptions = 0.0, "", []
    rate = _rate(benefit.get("rate"), profile.category)
    kind = benefit["type"]

    if kind == "capital_subsidy":
        if not qualifying:
            reasons.append("No qualifying capital purchases inside the scheme window")
        if eligible_cost < scheme.get("min_investment", 0):
            reasons.append(f"Eligible investment below the {inr(scheme['min_investment'], 0)} minimum")
        base = eligible_cost
        if benefit.get("basis") == "term_loan":
            if profile.term_loan_amount:
                base = min(profile.term_loan_amount, eligible_cost)
            else:
                base = eligible_cost * TERM_LOAN_SHARE_ASSUMPTION
                assumptions.append(f"Assumed {TERM_LOAN_SHARE_ASSUMPTION:.0%} of the machinery cost was term-loan financed")
        if rate is not None:
            estimate = base * rate
            basis_text = f"{rate:.0%} of {inr(base, 0)}"
    elif kind == "power_tariff":
        if profile.monthly_power_bill:
            estimate = profile.monthly_power_bill * 12 * rate * benefit["years"]
            basis_text = f"{rate:.1%} of a {inr(profile.monthly_power_bill, 0)}/month bill for {benefit['years']} years"
        else:
            missing.append("average monthly electricity bill")
    elif kind == "certification_support":
        if rate is not None:
            estimate = benefit["certification_cost"] * rate
            basis_text = f"{rate:.0%} of {inr(benefit['certification_cost'], 0)} certification cost"
    elif kind == "interest_subvention" and rate is not None:
        principal = profile.term_loan_amount or eligible_cost * TERM_LOAN_SHARE_ASSUMPTION
        estimate = principal * rate * benefit["years"]
        basis_text = f"{rate:.0%} a year on {inr(principal, 0)} for {benefit['years']} years"

    capped = False
    if benefit.get("cap") and estimate > benefit["cap"]:
        estimate, capped = float(benefit["cap"]), True
    if not profile.has_udyam:
        missing.append("Udyam registration number (required to apply)")

    status = "not_eligible" if reasons else ("needs_info" if missing and not estimate else "eligible")
    return {
        "scheme_id": scheme["id"],
        "name": scheme["name"],
        "authority": scheme["authority"],
        "level": scheme["level"],
        "benefit_type": kind,
        "status": status,
        "estimated_benefit": round(estimate, 2) if status != "not_eligible" else 0.0,
        "basis": basis_text,
        "capped_at": benefit.get("cap") if capped else None,
        "eligible_cost": eligible_cost,
        "qualifying_assets": [a.to_dict() for a in qualifying],
        "reasons": reasons,
        "missing_inputs": missing,
        "assumptions": assumptions,
        "documents": scheme["documents"],
        "disbursal": scheme.get("disbursal"),
        "notes": scheme.get("notes"),
        "source_url": scheme["source_url"],
        "verified": scheme["verified"],
        "form_template": scheme["form_template"],
    }


def match(profile: Profile, assets: list[CapitalAsset], include_ineligible: bool = False) -> dict:
    evaluated = [evaluate_scheme(s, profile, assets) for s in rule_grid()["schemes"]]
    shown = evaluated if include_ineligible else [e for e in evaluated if e["status"] != "not_eligible"]
    shown.sort(key=lambda e: (e["status"] != "eligible", -e["estimated_benefit"]))
    by_class: dict[str, float] = {}
    for a in assets:
        by_class[a.asset_class] = round(by_class.get(a.asset_class, 0) + a.value, 2)
    return {
        "category": profile.category,
        "capital_assets": [a.to_dict() for a in assets],
        "capital_spend_by_class": by_class,
        "schemes": shown,
        "total_estimated_benefit": round(sum(e["estimated_benefit"] for e in shown if e["status"] == "eligible"), 2),
        "disclaimer": "Estimates use an illustrative rule grid that has not been verified against current "
                      "official notifications. Confirm rates, caps and windows with the DIC before filing.",
    }


def calculator(state_code: str, sector: str, investments: dict[str, float], category: str | None = None,
               turnover: float | None = None, investment_pm: float | None = None,
               term_loan_amount: float | None = None, monthly_power_bill: float | None = None,
               today: date | None = None) -> dict:
    """The public 60-second calculator: same rules, but assets come from typed-in totals."""
    today = today or date.today()
    category = category or classify_enterprise(investment_pm, turnover)
    classes = rule_grid()["asset_classes"]
    assets = [CapitalAsset(name, classes[name]["label"], "", float(amount), today)
              for name, amount in investments.items() if name in classes and amount and amount > 0]
    profile = Profile(state_code=state_code, sector=sector, category=category,
                      term_loan_amount=term_loan_amount, monthly_power_bill=monthly_power_bill, has_udyam=True)
    result = match(profile, assets)
    for scheme in result["schemes"]:
        scheme["missing_inputs"] = [m for m in scheme["missing_inputs"] if "Udyam" not in m]
        scheme.pop("qualifying_assets", None)
    result.pop("capital_assets")
    return result
