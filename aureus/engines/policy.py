"""Policy engine: match capital purchases and the business profile against the scheme rule grid.

Deterministic by design: every rupee in an estimate traces to a rule in
data/schemes.json and to specific invoice lines.
"""

import json
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache

from .. import hsn as hsn_mod
from ..config import BASE_DIR
from ..models import Invoice
from ..money import inr

# MSME classification by investment in plant & machinery and turnover (GoI notification of 21 March 2025).
CATEGORY_LIMITS = [("micro", 2.5e7, 1e8), ("small", 2.5e8, 1e9), ("medium", 1.25e9, 5e9)]

TERM_LOAN_SHARE_ASSUMPTION = 0.75
ASSUMED_LOAN_RATE = 0.10  # for subsidies paid as a share of interest, when the actual rate isn't known


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
        return {**self.__dict__, "invoice_date": self.invoice_date.isoformat(),
                "hsn_description": hsn_mod.describe(self.hsn)["description"] if self.hsn else None}


ZONE_MAPS = ("rate_by_zone", "years_by_zone", "cap_by_zone")


CATEGORY_KEYS = {"micro", "small", "medium"}


def _zone_value(mapping: dict | None, zone: str | None, category: str | None = None):
    """Look up by zone, then by enterprise size (some states key years or caps by size), then 'ALL' (statewide)."""
    if not mapping:
        return None
    if zone in mapping:
        return mapping[zone]
    if category in mapping:
        return mapping[category]
    return mapping.get("ALL")


def _needs_zone(scheme: dict) -> bool:
    if scheme.get("zones"):
        return True
    return any(m and "ALL" not in m and not set(m) <= CATEGORY_KEYS
               for m in (scheme["benefit"].get(k) for k in ZONE_MAPS))


def zone_name(state_code: str, zone: str | None) -> str:
    if not zone:
        return ""
    label = rule_grid().get("zones_by_state", {}).get(state_code, {}).get("zones", {}).get(zone)
    return f"{zone}: {label}" if label and label != zone else f"Zone {zone}"


def _lookup_hint(state_code: str) -> str:
    hint = rule_grid().get("zones_by_state", {}).get(state_code, {}).get("lookup")
    return f" ({hint})" if hint else ""


@dataclass
class Profile:
    state_code: str
    sector: str = "manufacturing"
    category: str | None = None
    term_loan_amount: float | None = None
    zone: str | None = None  # Maharashtra taluka group: A, B, C, D, D+, VM, NI
    monthly_electricity_duty: float | None = None
    monthly_power_units: float | None = None
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


def evaluate_scheme(scheme: dict, profile: Profile, assets: list[CapitalAsset], today: date | None = None) -> dict:
    reasons, missing = [], []
    today = today or date.today()
    window_end = date.fromisoformat(scheme["window"]["to"]) if scheme["window"].get("to") else date.max
    if window_end < today and not scheme.get("continuing"):
        reasons.append(f"Scheme period ended on {window_end:%d %b %Y}")
    states = scheme["states"]
    if "*" not in states and profile.state_code not in states:
        reasons.append("Unit is not located in an eligible state")
    if profile.sector not in scheme["sectors"]:
        reasons.append(f"Sector '{profile.sector}' is not covered")
    if profile.category is None:
        missing.append("enterprise category (enter turnover and plant & machinery investment)")
    elif profile.category not in scheme["categories"]:
        reasons.append(f"{profile.category.title()} enterprises are not covered")

    benefit = scheme["benefit"]
    kind = benefit["type"]
    zone = profile.zone
    zone_label = zone_name(profile.state_code, zone)
    if _needs_zone(scheme) and not reasons:
        if not zone:
            missing.append("industrial zone / area category of your unit" + _lookup_hint(profile.state_code))
        elif scheme.get("zones") and zone not in scheme["zones"]:
            reasons.append(scheme.get("zone_exclusion_reason", f"Not available in {zone_label}"))

    start = date.fromisoformat(scheme["window"]["from"]) if scheme["window"].get("from") else date.min
    end = window_end
    qualifying = [a for a in assets
                  if a.asset_class in scheme["asset_classes"] and start <= a.invoice_date <= end]
    eligible_cost = round(sum(a.value for a in qualifying), 2)

    estimate, basis_text, assumptions = 0.0, "", []
    rate = _rate(benefit["rate"] if benefit.get("rate") is not None
                 else _zone_value(benefit.get("rate_by_zone"), zone, profile.category), profile.category)
    years = benefit.get("years") or _zone_value(benefit.get("years_by_zone"), zone, profile.category)
    cap = _rate(benefit.get("cap") or benefit.get("cap_by_category") or _zone_value(benefit.get("cap_by_zone"), zone, profile.category),
                profile.category)
    where = f" ({zone_label})" if zone and _needs_zone(scheme) else ""

    if scheme.get("max_investment") and eligible_cost > scheme["max_investment"]:
        reasons.append(f"Only for investment up to {inr(scheme['max_investment'], 0)}")
    if kind in ("capital_subsidy", "sgst_refund_ceiling"):
        if not qualifying:
            reasons.append("No qualifying capital purchases inside the scheme window")
        if eligible_cost < scheme.get("min_investment", 0):
            reasons.append(f"Eligible investment below the {inr(scheme['min_investment'], 0)} minimum")
    if kind == "capital_subsidy":
        base = eligible_cost
        if benefit.get("basis") == "term_loan":
            if profile.term_loan_amount:
                base = min(profile.term_loan_amount, eligible_cost)
            else:
                base = eligible_cost * TERM_LOAN_SHARE_ASSUMPTION
                assumptions.append(f"Assumed {TERM_LOAN_SHARE_ASSUMPTION:.0%} of the machinery cost was term-loan financed")
        if rate is not None:
            estimate = base * rate
            basis_text = f"{rate:.0%} of {inr(base, 0)}{where}"
    elif kind == "sgst_refund_ceiling" and rate is None and not reasons:
        basis_text = "Refund of SGST you pay; this policy sets no investment-linked ceiling, so no estimate"
        assumptions.append("Amount depends on the SGST your unit pays on in-state sales")
    elif kind == "sgst_refund_ceiling" and rate is not None:
        estimate = eligible_cost * rate
        basis_text = (f"Up to {rate:.0%} of {inr(eligible_cost, 0)} capital investment{where}, refunded from SGST"
                      + (f" over {years} years" if years else ""))
    elif kind == "electricity_duty_exemption" and years == 0 and not reasons:
        reasons.append(f"No electricity duty exemption{where or ' for your unit'}")
    elif kind == "electricity_duty_exemption" and years is None and not reasons:
        missing.append("exemption period (the policy leaves it to a separate order)")
    elif kind == "electricity_duty_exemption" and years:
        if profile.monthly_electricity_duty:
            estimate = profile.monthly_electricity_duty * 12 * years
            basis_text = f"{inr(profile.monthly_electricity_duty, 0)}/month electricity duty waived for {years} years{where}"
        else:
            missing.append("monthly electricity duty (shown on your electricity bill)")
    elif kind == "power_tariff_per_unit" and not years and not reasons:
        missing.append("subsidy period (the policy leaves it to a separate order)")
    elif kind == "power_tariff_per_unit" and years:
        if profile.monthly_power_units:
            per_unit = _rate(benefit.get("per_unit") or _zone_value(benefit.get("per_unit_by_zone"), zone, profile.category),
                             profile.category) or 0
            yearly = profile.monthly_power_units * 12 * per_unit
            annual_cap = _rate(_zone_value(benefit.get("annual_cap_by_zone"), zone, profile.category), profile.category)
            if annual_cap:
                yearly = min(yearly, annual_cap)
            estimate = yearly * years
            basis_text = f"₹{per_unit:g}/unit on {profile.monthly_power_units:,.0f} units/month for {years} years{where}"
            if annual_cap:
                basis_text += f", up to {inr(annual_cap, 0)} a year"
        else:
            missing.append("monthly electricity consumption in units (kWh)")
    elif kind == "interest_subsidy" and rate is not None:
        if profile.term_loan_amount:
            basis = benefit.get("rate_basis", "")
            if basis.startswith(("share", "fraction")):
                estimate = profile.term_loan_amount * ASSUMED_LOAN_RATE * rate
                assumptions.append(f"Assumed a {ASSUMED_LOAN_RATE:.0%} interest rate on the term loan")
                what = f"{rate:.0%} of the interest on a {inr(profile.term_loan_amount, 0)} term loan"
            elif benefit.get("floor"):
                # The unit bears the first `floor` of interest; the subsidy covers the rest, up to `rate`.
                points = min(rate, max(ASSUMED_LOAN_RATE - benefit["floor"], 0))
                estimate = profile.term_loan_amount * points
                assumptions.append(f"Assumed a {ASSUMED_LOAN_RATE:.0%} interest rate; you bear the first "
                                   f"{benefit['floor']:.0%}")
                what = f"{points:.0%} a year on a {inr(profile.term_loan_amount, 0)} term loan"
            else:
                estimate = profile.term_loan_amount * rate
                what = f"Up to {rate:.2%} a year on a {inr(profile.term_loan_amount, 0)} term loan".replace(".00%", "%")
            annual_cap = _rate(benefit.get("annual_cap") or _zone_value(benefit.get("annual_cap_by_zone"), zone, profile.category),
                               profile.category)
            if annual_cap:
                estimate = min(estimate, annual_cap)
            basis_text = what + " (first-year figure" + (f", for up to {years} years)" if years else ")") + where
        else:
            missing.append("term loan amount")
    elif kind == "certification_support" and rate is not None:
        estimate = benefit["certification_cost"] * rate
        basis_text = f"{rate:.0%} of {inr(benefit['certification_cost'], 0)} certification cost"
    elif kind == "certification_topup":
        central = benefit.get("central_rate", {}).get(profile.category or "")
        if central is not None:
            net_fee = benefit["certification_cost"] * (1 - central)
            estimate = net_fee * rate
            basis_text = f"{rate:.0%} of the {inr(net_fee, 0)} fee left after the central ZED subsidy"
        elif benefit.get("certification_cost") and rate is not None:
            estimate = benefit["certification_cost"] * rate
            basis_text = f"{rate:.0%} of {inr(benefit['certification_cost'], 0)} certification cost"
        elif cap:
            estimate = cap
            basis_text = f"Up to {inr(cap, 0)} of certification costs reimbursed" + (f" ({rate:.0%})" if rate else "")

    pct_cap = benefit.get("total_cap_pct_of_efci")
    if pct_cap:
        # Some policies cap a benefit at a share of the unit's whole capital investment.
        total_capital = sum(a.value for a in assets if start <= a.invoice_date <= end)
        pct_limit = pct_cap * total_capital
        cap = min(cap, pct_limit) if cap else pct_limit
        assumptions.append(f"Capped at {pct_cap:.0%} of the capital investment found in your books")
    capped = False
    if cap is not None and estimate > cap:
        estimate, capped = float(cap), True
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
        # Interest subsidy is a first-year figure, certification reimbursements depend on actual certification
        # spend, and conditional schemes depend on facts we can't check (ownership, exporter status...), so all
        # stay out of the headline unless a scheme says otherwise.
        "in_total": scheme.get("in_total", kind not in ("interest_subsidy", "certification_topup")
                                and not scheme.get("conditions")),
        "exclusive_group": scheme.get("exclusive_group"),
        "conditions": scheme.get("conditions", []),
        "continuing": scheme.get("continuing", False),
        "basis": basis_text,
        "capped_at": cap if capped else None,
        "eligible_cost": eligible_cost,
        "qualifying_assets": [a.to_dict() for a in qualifying],
        "reasons": reasons,
        "missing_inputs": missing,
        "assumptions": assumptions,
        "documents": scheme["documents"],
        "disbursal": scheme.get("disbursal"),
        "notes": scheme.get("notes"),
        "source_url": scheme["source_url"],
        "source_document": scheme.get("source_document"),
        "clause": scheme.get("clause"),
        "verified": scheme["verified"],
        "verified_on": scheme.get("verified_on"),
        "form_template": scheme["form_template"],
    }


def match(profile: Profile, assets: list[CapitalAsset], include_ineligible: bool = False,
          today: date | None = None) -> dict:
    evaluated = [evaluate_scheme(s, profile, assets, today) for s in rule_grid()["schemes"]]
    shown = evaluated if include_ineligible else [e for e in evaluated if e["status"] != "not_eligible"]
    shown.sort(key=lambda e: (e["status"] != "eligible", not e["in_total"], -e["estimated_benefit"]))
    by_class: dict[str, float] = {}
    for a in assets:
        by_class[a.asset_class] = round(by_class.get(a.asset_class, 0) + a.value, 2)
    counted = _best_per_exclusive_group([e for e in shown if e["status"] == "eligible" and e["in_total"]])
    unverified = [e["name"] for e in counted if not e["verified"]]
    disclaimer = ("Maharashtra and ZED figures are checked against the official documents cited on each scheme. "
                  "Estimates are ceilings: actual payouts depend on DIC approval and, for the SGST refund, on tax you pay.")
    if unverified:
        disclaimer = ("Some estimates use an illustrative rule grid not yet verified against official notifications. "
                      "Confirm rates, caps and windows with the DIC before filing. " + disclaimer)
    return {
        "category": profile.category,
        "zone": profile.zone,
        "capital_assets": [a.to_dict() for a in assets],
        "capital_spend_by_class": by_class,
        "schemes": shown,
        "total_estimated_benefit": round(sum(e["estimated_benefit"] for e in counted), 2),
        "unverified_in_total": unverified,
        "disclaimer": disclaimer,
    }


def _best_per_exclusive_group(schemes: list[dict]) -> list[dict]:
    """Either/or benefits (e.g. SGST refund OR capital subsidy) count only once: the larger one."""
    best: dict[str, dict] = {}
    out = []
    for e in schemes:
        group = e.get("exclusive_group")
        if not group:
            out.append(e)
        elif group not in best or e["estimated_benefit"] > best[group]["estimated_benefit"]:
            best[group] = e
    return out + list(best.values())


def calculator(state_code: str, sector: str, investments: dict[str, float], category: str | None = None,
               turnover: float | None = None, investment_pm: float | None = None,
               term_loan_amount: float | None = None, zone: str | None = None,
               monthly_electricity_duty: float | None = None, monthly_power_units: float | None = None,
               today: date | None = None) -> dict:
    """The public 60-second calculator: same rules, but assets come from typed-in totals."""
    today = today or date.today()
    category = category or classify_enterprise(investment_pm, turnover)
    classes = rule_grid()["asset_classes"]
    assets = [CapitalAsset(name, classes[name]["label"], "", float(amount), today)
              for name, amount in investments.items() if name in classes and amount and amount > 0]
    profile = Profile(state_code=state_code, sector=sector, category=category,
                      term_loan_amount=term_loan_amount, zone=zone, monthly_electricity_duty=monthly_electricity_duty,
                      monthly_power_units=monthly_power_units, has_udyam=True)
    result = match(profile, assets)
    for scheme in result["schemes"]:
        scheme["missing_inputs"] = [m for m in scheme["missing_inputs"] if "Udyam" not in m]
        scheme.pop("qualifying_assets", None)
    result.pop("capital_assets")
    return result
