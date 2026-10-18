"""Merge researched state/central policy files into aureus/data/schemes.json.

    python tools/merge_policies.py /path/to/policies/*.json [--check]

Each input file follows the research brief (state_code, policy, zones, schemes...).
Every scheme is validated before anything is written; any error aborts the merge.
Schemes for a state are replaced wholesale, so re-running after re-verification is safe.
Maharashtra and other hand-maintained entries are kept unless a file for that state is given.
"""

import json
import sys
from datetime import date
from pathlib import Path

GRID = Path(__file__).resolve().parent.parent / "aureus" / "data" / "schemes.json"
TYPES = {
    "capital_subsidy": set(),
    "sgst_refund_ceiling": set(),
    "interest_subsidy": set(),
    "electricity_duty_exemption": set(),
    "power_tariff_per_unit": {"years"},
    "certification_topup": set(),
    "certification_support": {"certification_cost", "rate"},
}
ASSET_CLASSES = {"plant_machinery", "testing_equipment", "pollution_control", "solar", "it_hardware"}
NOT_DETECTABLE = {"building", "land"}  # allowed in research, dropped on merge: invoices can't show them
MAX_RATE = 3.0  # SGST ceilings can exceed 100% of investment (Rajasthan 125%, Assam micro 250%)
CATEGORIES = {"micro", "small", "medium"}
FORM = {"electricity_duty_exemption": "dic_power_incentive", "power_tariff_per_unit": "dic_power_incentive",
        "certification_topup": "zed_registration", "certification_support": "zed_registration"}
DOCS = {
    "dic_capital_subsidy": ["Udyam Registration Certificate", "Purchase invoices with payment proof",
                            "Chartered Engineer certificate for plant & machinery", "Term loan sanction letter (if financed)",
                            "Commencement of production proof"],
    "dic_power_incentive": ["Udyam Registration Certificate", "Eligibility certificate", "Latest 12 months of electricity bills"],
    "zed_registration": ["Udyam Registration Certificate", "Certificate obtained", "Fee payment receipt"],
}


class Invalid(ValueError):
    pass


def _rate_ok(value, where):
    values = value.values() if isinstance(value, dict) else [value]
    for v in values:
        if isinstance(v, dict):
            _rate_ok(v, where)
        elif v is None:
            continue
        elif not isinstance(v, (int, float)) or not 0 <= v <= MAX_RATE:
            raise Invalid(f"{where}: rate {v!r} must be a decimal between 0 and {MAX_RATE}")


def validate(doc: dict, path: str) -> list[str]:
    errors = []

    def check(cond, msg):
        if not cond:
            errors.append(f"{path}: {msg}")

    check(doc.get("status") in ("current", "expired", "not_found"), "status must be current/expired/not_found")
    zones = doc.get("zones") or {}
    for s in doc.get("schemes", []):
        where = s.get("id", "?")
        b = s.get("benefit", {})
        kind = b.get("type")
        check(kind in TYPES, f"{where}: unknown benefit type {kind!r}")
        if kind not in TYPES:
            continue
        for f in TYPES[kind]:
            check(f in b, f"{where}: {kind} needs '{f}'")
        try:
            for key in ("rate", "rate_by_zone"):
                if key in b:
                    _rate_ok(b[key], where)
        except Invalid as exc:
            errors.append(f"{path}: {exc}")
        if kind == "capital_subsidy":
            check("rate" in b or "rate_by_zone" in b, f"{where}: capital_subsidy needs rate or rate_by_zone")
            check(b.get("basis", "eligible_cost") in ("eligible_cost", "term_loan"), f"{where}: bad basis")
        if kind == "sgst_refund_ceiling":
            check("rate_by_zone" in b, f"{where}: sgst_refund_ceiling needs rate_by_zone (null if uncapped)")
        if kind == "interest_subsidy":
            check("rate" in b or "rate_by_zone" in b, f"{where}: interest_subsidy needs rate or rate_by_zone")
        if kind == "power_tariff_per_unit":
            check("per_unit" in b or "per_unit_by_zone" in b, f"{where}: needs per_unit or per_unit_by_zone")
        if kind == "electricity_duty_exemption":
            check("years_by_zone" in b or "years" in b, f"{where}: needs years_by_zone or years")
        for key in ("rate_by_zone", "years_by_zone", "cap_by_zone", "annual_cap_by_zone", "per_unit_by_zone"):
            for z in (b.get(key) or {}):
                check(z == "ALL" or z in zones or z in CATEGORIES, f"{where}: {key} uses zone {z!r} not defined in 'zones'")
        for z in s.get("zones") or []:
            check(z in zones, f"{where}: zone {z!r} not defined in 'zones'")
        check(set(s.get("categories") or []) <= CATEGORIES and s.get("categories"), f"{where}: bad categories")
        check(set(s.get("asset_classes") or []) <= ASSET_CLASSES | NOT_DETECTABLE, f"{where}: bad asset_classes")
        try:
            w = s["window"]
            start = date.fromisoformat(w["from"]) if w.get("from") else date.min
            check(w.get("to") is None or start <= date.fromisoformat(w["to"]), f"{where}: window from > to")
        except (KeyError, ValueError, TypeError):
            errors.append(f"{path}: {where}: window needs an ISO 'from' date and an ISO or null 'to' date")
        check(isinstance(s.get("verified"), bool), f"{where}: verified must be true/false")
        if s.get("verified"):
            check(bool((doc.get("policy") or {}).get("source_url")), f"{where}: verified scheme needs policy.source_url")
            check(bool(s.get("clause")), f"{where}: verified scheme needs a clause reference")
    return errors


CAPITAL_DEFAULT = ["plant_machinery", "testing_equipment", "pollution_control", "solar"]


def _asset_classes(s: dict) -> list[str]:
    """Investment-linked schemes with no asset list count all capital assets the app can detect."""
    classes = [a for a in s.get("asset_classes") or [] if a not in NOT_DETECTABLE]
    if not classes and s["benefit"]["type"] in ("capital_subsidy", "sgst_refund_ceiling"):
        return list(CAPITAL_DEFAULT)
    return classes


def to_grid_scheme(doc: dict, s: dict) -> dict:
    pol = doc.get("policy") or {}
    kind = s["benefit"]["type"]
    form = FORM.get(kind, "dic_capital_subsidy")
    central = doc["state_code"] in ("IN", "NE")
    states = ["*"] if doc["state_code"] == "IN" else (s.get("states") or doc.get("states") or [doc["state_code"]])
    source_doc = ", ".join(x for x in (pol.get("name"), pol.get("order_no")) if x)
    if pol.get("date"):
        source_doc += f" dated {pol['date']}"
    out = {
        "id": s["id"],
        "name": s["name"],
        "authority": s.get("authority") or (f"Government of India: {pol.get('name')}" if central
                                            else f"Government of {doc['state']} (via District Industries Centre)"),
        "level": "central" if central else "state",
        "states": states,
        "categories": s["categories"],
        "sectors": ["manufacturing"],
        "asset_classes": _asset_classes(s),
        "window": s["window"],
        "min_investment": s.get("min_investment") or 0,
        "benefit": s["benefit"],
        "disbursal": s.get("disbursal"),
        "form_template": form,
        "documents": s.get("documents") or DOCS[form],
        "clause": s.get("clause"),
        "notes": s.get("notes"),
        "source_document": source_doc or None,
        "source_url": s.get("source_url") or pol.get("source_url") or "",
        "verified": bool(s.get("verified")),
        "verified_on": doc.get("checked_on") if s.get("verified") else None,
    }
    if s.get("zones"):
        out["zones"] = s["zones"]
        if s.get("zone_exclusion_reason"):
            out["zone_exclusion_reason"] = s["zone_exclusion_reason"]
    for key in ("in_total", "conditions", "continuing", "exclusive_group", "max_investment"):
        if key in s:
            out[key] = s[key]
    return out


def merge(paths: list[str], check_only: bool = False) -> dict:
    docs = [(p, json.loads(Path(p).read_text())) for p in paths]
    errors = [e for p, d in docs for e in validate(d, p)]
    if errors:
        raise SystemExit("Validation failed:\n  " + "\n  ".join(errors))
    grid = json.loads(GRID.read_text())
    replaced_states = {d["state_code"] for _, d in docs}
    ids = {s["id"] for _, d in docs for s in d.get("schemes", [])}  # includes lapsed states' ids, so stale copies go
    keep = []
    for s in grid["schemes"]:
        st = s["states"]
        own = ("*" in st and "IN" in replaced_states and s.get("level") == "central") or \
              (len(st) == 1 and st[0] in replaced_states)
        if s["id"] in ids or own:
            continue
        keep.append(s)
    new = [to_grid_scheme(d, s) for _, d in docs if d["status"] == "current" for s in d.get("schemes", [])]
    grid["schemes"] = keep + new
    status = grid.setdefault("coverage", {})
    for _, d in docs:
        pol = d.get("policy") or {}
        status[d["state_code"]] = {"state": d.get("state"), "status": d["status"], "policy": pol.get("name"),
                                   "valid_to": pol.get("valid_to"), "checked_on": d.get("checked_on"),
                                   "schemes": len(d.get("schemes", [])) if d["status"] == "current" else 0,
                                   "verified_schemes": sum(1 for s in d.get("schemes", []) if s.get("verified"))
                                   if d["status"] == "current" else 0,
                                   "gaps": d.get("unverifiable", [])}
        if d.get("zones") and d["state_code"] not in ("IN", "NE"):
            grid.setdefault("zones_by_state", {})[d["state_code"]] = {
                "zones": d["zones"], "lookup": d.get("zone_lookup"),
                "source": pol.get("name"), "source_url": pol.get("source_url")}
    if not check_only:
        GRID.write_text(json.dumps(grid, indent=2, ensure_ascii=False) + "\n")
        COVERAGE_DOC.write_text(coverage_markdown(grid))
    return {"files": len(docs), "schemes_added": len(new), "schemes_kept": len(keep)}


COVERAGE_DOC = GRID.parent.parent.parent / "docs" / "STATE_COVERAGE.md"


def _short(text: str, limit: int = 140) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def coverage_markdown(grid: dict) -> str:
    rows = sorted(grid["coverage"].items(), key=lambda kv: (kv[0] in ("IN", "NE"), kv[1].get("state") or ""))
    lines = ["# Subsidy rule coverage by state", "",
             "Generated by `tools/merge_policies.py` from `aureus/data/schemes.json`; do not edit by hand.",
             "Verified = figure read from the official policy document, with its clause recorded in the rule grid.",
             "Raw research and curation notes: `research/policies/`.", "",
             "| Code | State / scheme | Status | Policy | Valid to | Schemes | Verified | Open gaps |",
             "|---|---|---|---|---|---|---|---|"]
    for code, c in rows:
        gaps = "; ".join(_short(g) for g in (c.get("gaps") or [])[:2]).replace("|", "/")
        lines.append(f"| {code} | {c.get('state') or code} | {c['status']} | {(c.get('policy') or '-').replace('|', '/')} | "
                     f"{c.get('valid_to') or '-'} | {c.get('schemes', 0)} | {c.get('verified_schemes', 0)} | {gaps or '-'} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--check"]
    print(merge(args, check_only="--check" in sys.argv))
