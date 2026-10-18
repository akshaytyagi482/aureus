"""Curate raw policy research into files the merge tool accepts.

    python research/curate.py            # raw/*.json -> policies/*.json
    python tools/merge_policies.py research/policies/*.json

Raw files are the researchers' output, kept unchanged as evidence. Every
change made here is written into the curated file's "curation" list, so a
reviewer can see exactly what was adjusted and why.
"""

import copy
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW, OUT = HERE / "policies" / "raw", HERE / "policies"

# Conditions the app cannot check (ownership, sector, location lists...). Such schemes are shown
# with the condition and kept out of the headline total.
CONDITIONS = {
    "jk-ip-2021-dgset": ["Applies only to one new DG set (10-2000 kW)"],
    "jk-ip-2021-automation": ["Applies only to automation, IT or online pollution-monitoring spend"],
    "jk-ip-2021-green": ["Applies only to rainwater harvesting, recycling or waste-management equipment"],
    "jk-ip-2021-pcd": ["Pollution control devices not already claimed under the central CII or a similar scheme"],
    "jk-ip-2021-sgst-existing": ["Unit was GST-registered in J&K on or before 31 Mar 2021"],
    "jk-ncss-2021-cii": ["Registered under the central J&K scheme (NCSS 2021) before registration closed on 30 Sep 2024"],
    "jk-ncss-2021-interest": ["Registered under the central J&K scheme (NCSS 2021) before registration closed on 30 Sep 2024"],
    "pb-ibdp-2026-zld": ["Existing unit installing zero liquid discharge (ZLD)"],
    "hr-msme-2026-capital": ["Thrust-sector manufacturing MSME as listed in the policy"],
    "hr-msme-2026-automation": ["Applies only to industrial automation spend; operational guidelines pending"],
    "rj-rips2024-msme-plastic-alt-capital": ["Unit makes alternatives to plastic products"],
    "rj-rips2024-msme-agro-capital": ["Agro or food processing enterprise"],
    "nl-ntiip2025-women-cis": ["At least 60% owned by women from Nagaland"],
    "mn-iippm2022-cis": ["New unit in a location notified under the policy"],
    "tr-tiipis2022-cis-thrust": ["Thrust-sector unit as listed in the policy"],
    "tr-tiipis2022-interest-thrust": ["Thrust-sector unit as listed in the policy"],
    "tr-tiipis2022-power-above20hp": ["Connected load above 20 HP"],
    "uk-msme-2023-ed": ["Sanctioned electricity load up to 500 kW"],
    "mz-ip2025-captive-power": ["New unit within 5 years of starting production"],
    "rj-msme2024-addl-interest": ["Project selected by the district or high-level task force committee"],
    "od-msme2022-cis-addl-backward": ["Unit in a backward area notified in the policy"],
    "cg-idp2024-msme-fcis-thrust": ["Product is on the thrust-sector list (Annexure-2)"],
    "cg-idp2024-msme-sgst-thrust": ["Product is on the thrust-sector list (Annexure-2)"],
    "cg-idp2024-msme-interest-thrust": ["Product is on the thrust-sector list (Annexure-2)"],
    "cg-idp2024-msme-ed-thrust": ["Product is on the thrust-sector list (Annexure-2)"],
    "ka-ip2025-ms-capital-spl": ["Unit wholly owned by special-category entrepreneurs as defined in the policy"],
    "ka-ip2025-med-capital": ["Chosen instead of the turnover-linked (PLI) option"],
    "ka-ip2025-ed": ["Applies once the Energy Department notifies the exemption"],
    "kl-ip2023-capital-ceiling": ["45% is the policy maximum; the actual rate is set by the Entrepreneur Support Scheme order"],
    "tn-msme2021-cs-sm": ["Unit in one of the 254 backward blocks or a government industrial estate (agro/food: any block)"],
    "tn-msme2021-cs-thrust": ["Thrust-sector unit (Annexure V)"],
    "tn-msme2021-cs-special-cat": ["Special-category entrepreneur; small/medium units only in backward blocks or estates"],
    "tn-msme2021-cs-scaleup": ["Existing micro/small unit moving up a size category through expansion"],
    "tn-msme2021-cs-ev": ["Electric-vehicle related manufacturing; confirm the scheme is still operative"],
    "tn-msme2021-beis-tech": ["Term loan for technology upgradation or modernisation"],
    "tn-msme2021-beis-cgtmse": ["Term loan covered by CGTMSE"],
    "py-ip2016-capital-wscst": ["Unit wholly owned by women or SC/ST entrepreneurs"],
    "tg-msme2024-capital-women": ["Women-owned unit (on top of the general subsidy)"],
    "tg-msme2024-capital-scst": ["Unit owned by SC/ST or persons with disabilities"],
    "ap-medp4-capital-special": ["Unit owned by special-category entrepreneurs as defined in the policy"],
    "ap-medp4-techup": ["Existing unit expanding or diversifying (instead of the capital subsidy for new units)"],
    "ap-medp4-techup-special": ["Existing unit expanding or diversifying (instead of the capital subsidy for new units)",
                                "Unit owned by special-category entrepreneurs as defined in the policy"],
    "ap-medp4-power-special": ["Unit owned by special-category entrepreneurs as defined in the policy"],
    "la-lsip-2022-pollution": ["Existing units only"],
    "mp-msme2025-interest-apparel": ["Apparel or made-ups unit with plant & machinery above Rs 5 crore"],
    "mp-msme2025-interest-pharma": ["Pharmaceutical or medical device unit with plant & machinery above Rs 5 crore"],
    "in-zed-testing-cert": ["Unit holds ZED Bronze or higher", "Certification obtained after ZED registration from an accredited lab or body"],
    "in-zed-zero-effect-tech": ["Unit holds ZED Bronze or higher", "Consultant recommendation and committee (PMAC) approval"],
    "in-pmegp-margin-money": ["New micro enterprise with a bank-financed project of up to Rs 50 lakh",
                              "Rural units and special categories get 25-35% instead of 15%"],
    "in-sclcss-scst": ["SC/ST-owned micro or small enterprise", "Machinery financed by institutional credit"],
    "ne-unnati-cii": ["Registered under UNNATI before registration closed (31 Mar 2026; a 30 Sep 2026 extension is reported)"],
    "ne-unnati-cis-a": ["Registered under UNNATI before registration closed (31 Mar 2026; a 30 Sep 2026 extension is reported)"],
    "ne-unnati-cis-b": ["Registered under UNNATI before registration closed (31 Mar 2026; a 30 Sep 2026 extension is reported)"],
    "ne-unnati-msli": ["Registered under UNNATI before registration closed (31 Mar 2026; a 30 Sep 2026 extension is reported)"],
}

# Either/or benefits: only the larger one counts in the headline total.
EXCLUSIVE = {
    "cg-sgst-or-capital": ["cg-idp2024-msme-sgst-general", "cg-idp2024-msme-sgst-thrust",
                           "cg-idp2024-msme-fcis-general", "cg-idp2024-msme-fcis-thrust"],
    "cg-interest": ["cg-idp2024-msme-interest-general", "cg-idp2024-msme-interest-thrust"],
    "cg-ed": ["cg-idp2024-msme-ed-general", "cg-idp2024-msme-ed-thrust"],
    "ka-ms-capital": ["ka-ip2025-ms-capital-gen", "ka-ip2025-ms-capital-spl"],
    "ar-cii-slab": ["ar-apidip2025-cii-upto25l", "ar-apidip2025-cii-25to50l"],
    "ar-cis-slab": ["ar-apidip2025-cis-upto25l", "ar-apidip2025-cis-above25l"],
    "tr-cis": ["tr-tiipis2022-cis-nonthrust", "tr-tiipis2022-cis-thrust"],
    "tr-interest": ["tr-tiipis2022-interest-nonthrust", "tr-tiipis2022-interest-thrust"],
    "tn-sm-capital": ["tn-msme2021-cs-sm", "tn-msme2021-cs-thrust"],
    "tn-beis": ["tn-msme2021-beis-tech", "tn-msme2021-beis-cgtmse"],
    "py-capital": ["py-ip2016-capital-ms", "py-ip2016-capital-med", "py-ip2016-capital-wscst"],
    "tg-capital": ["tg-msme2024-capital-gen", "tg-msme2024-capital-scst"],
    "ap-capital": ["ap-medp4-capital-gen", "ap-medp4-capital-special", "ap-medp4-techup", "ap-medp4-techup-special"],
    "ap-power": ["ap-medp4-power", "ap-medp4-power-special"],
    "kl-capital": ["kl-ip2023-capital-ceiling", "kl-ess-capital"],
}

# Field-level fixes, each with the reason recorded in the curated file.
PATCHES = {
    "ar-apidip2025-cii-upto25l": ({"min_investment": 500000, "max_investment": 2500000},
                                  "slab applies only to P&M of Rs 5-25 lakh"),
    "ar-apidip2025-cii-25to50l": ({"min_investment": 2500000, "max_investment": 5000000},
                                  "slab applies only to P&M of Rs 25-50 lakh"),
    "ar-apidip2025-cis-upto25l": ({"benefit.floor": 0.02}, "unit must bear at least 2% interest"),
    "ar-apidip2025-cis-above25l": ({"benefit.floor": 0.02}, "unit must bear at least 2% interest"),
    "tg-msme2024-pavala-vaddi": ({"benefit.floor": 0.03},
                                 "reimburses interest above the first 3% a year, up to 9%: modelled as a 3% floor"),
    "wb-wbis2026-interest-a": ({"benefit.rate_basis": "share_of_interest"}, "rate is a share of the interest bill"),
    "wb-wbis2026-interest-bc": ({"benefit.rate_basis": "share_of_interest"}, "rate is a share of the interest bill"),
    "od-msme2022-interest": ({"benefit.rate_basis": "share_of_interest"}, "rate is a share of the interest bill"),
    "py-ip2016-interest": ({"benefit.rate_basis": "share_of_interest"}, "rate is a share of the interest bill"),
    "lk-ciss-capital": ({"max_investment": 2500000}, "scheme is only for units investing under Rs 25 lakh"),
    "kl-ip2023-interest": ({"benefit.annual_cap": 60000}, "loan is capped at Rs 10 lakh, so 6% is at most Rs 60,000 a year"),
    "in-zed-certification": ({"benefit": {"type": "certification_support", "certification_cost": 72000,
                                          "rate": {"micro": 0.8, "small": 0.6, "medium": 0.5}}, "continuing": True},
                             "modelled on the Gold fee (Rs 72,000 per Addendum-2, Sept 2024); marked continuing because the "
                             "Ministry describes ZED as operational in 2026 though no extension order past 31.03.2026 was found"),
    "in-zed-testing-cert": ({"continuing": True}, "continuing on the same basis as ZED"),
    "in-zed-zero-effect-tech": ({"continuing": True}, "continuing on the same basis as ZED"),
    "in-pmegp-margin-money": ({"benefit": {"type": "capital_subsidy", "basis": "eligible_cost", "rate": 0.15, "cap": 750000},
                               "continuing": True},
                              "rural/urban and special category are not zones the app tracks, so the conservative "
                              "urban-general 15% (up to Rs 7.5 lakh) is used; continuing per Ministry statement of July 2026"),
    "in-sclcss-scst": ({"continuing": True}, "continuing per Lok Sabha answer of 23.07.2026"),
    "hr-msme-2026-sgst": ({"benefit.rate_by_zone": {"ALL": None}},
                          "Haryana's rates are shares of net SGST paid, not ceilings on investment, so no estimate"),
}

# Benefits that are not term-loan or capital based, so the app cannot estimate them; kept as text.
DROP = {
    "ar-apidip2025-wc-interest": "working-capital interest subsidy (not a term loan)",
    "mn-iippm2022-wc-interest": "working-capital interest subsidy (not a term loan)",
    "as-iipa2019-wc-interest": "working-capital interest subsidy (not a term loan)",
    "in-epm-export-credit-is": "interest subvention on export working-capital credit (not a term loan)",
    "in-epm-trace-certification": "export certification reimbursement depends on certification spend the app does not track",
}

STATE_PATCHES = {
    "10": ({"zones": {"high_priority": "High-priority sector", "priority": "Priority sector",
                      "non_priority": "Other (non-priority) sector"},
            "zone_lookup": "Bihar's categories are sector tiers listed in the BIIPP; pick the tier of your product"},
           "Bihar's SGST tiers are sectors, declared here as its categories"),
}


def _set(scheme: dict, dotted: str, value):
    target = scheme
    *path, last = dotted.split(".")
    for key in path:
        target = target.setdefault(key, {})
    target[last] = value


def curate(doc: dict) -> dict:
    doc = copy.deepcopy(doc)
    log = []
    patch = STATE_PATCHES.get(doc["state_code"])
    if patch:
        doc.update(patch[0])
        log.append(f"state: {patch[1]}")
    if doc["status"] != "current" and doc.get("schemes"):
        log.append(f"state: status is '{doc['status']}', so its {len(doc['schemes'])} scheme(s) are kept for reference "
                   "but not merged")
    group_of = {sid: g for g, ids in EXCLUSIVE.items() for sid in ids}
    kept = []
    for s in doc.get("schemes", []):
        sid = s["id"]
        if sid in DROP:
            doc.setdefault("other_benefits", []).append(f"{s['name']}: {DROP[sid]}. {s.get('notes', '')[:200]}")
            log.append(f"{sid}: moved to other_benefits ({DROP[sid]})")
            continue
        if sid in CONDITIONS:
            s["conditions"] = CONDITIONS[sid]
            log.append(f"{sid}: conditions added")
        if sid in group_of:
            s["exclusive_group"] = group_of[sid]
            log.append(f"{sid}: either/or group '{group_of[sid]}'")
        if sid in PATCHES:
            changes, why = PATCHES[sid]
            for key, value in changes.items():
                _set(s, key, value)
            log.append(f"{sid}: {why}")
        kept.append(s)
    doc["schemes"] = kept
    doc["curation"] = doc.get("curation", []) + log
    return doc


def main():
    for raw in sorted(RAW.glob("*.json")):
        doc = curate(json.loads(raw.read_text()))
        (OUT / raw.name).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    print(f"curated {len(list(RAW.glob('*.json')))} files into {OUT}")


if __name__ == "__main__":
    main()
