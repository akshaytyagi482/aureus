# Brief: verify state industrial / MSME incentive rules for AureusRecon

AureusRecon estimates the incentives an Indian MANUFACTURING MSME (micro/small/medium, MSMED Act; P&M investment up to
Rs 125 crore) can claim on its capital investment and running costs. Maharashtra is already done. Your job: for each
state/UT assigned to you, find the CURRENT (in force on 17 Oct 2026) state industrial policy or MSME incentive scheme and
extract its MSME manufacturing incentives, clause by clause, FROM THE OFFICIAL DOCUMENT.

## Rules (important)
1. Primary sources only for verified figures: the state industries/MSME department site, the state gazette, the state
   single-window portal, invest-<state> portals run by the government, or the GR/notification PDF itself. Consultant blogs,
   news, or summaries can help you FIND the document but may not be the source of a verified figure.
2. Read the actual text. Download PDFs (curl -sSL -o file.pdf URL; retry 3-4 times with sleep if the connection resets;
   government sites are flaky from abroad) and extract with `pdftotext -layout file.pdf out.txt`. Many are bilingual or
   only in the state language: read the English part if present, otherwise read the regional text carefully.
3. Never invent or round numbers. If a figure is only in a secondary source, include the scheme with "verified": false
   and say in "notes" where the figure came from. If you cannot find a current policy at all, say so in "unverifiable".
4. Check validity dates. If the policy has expired and no successor exists, report that (status "expired") rather than
   presenting old figures as current. If a newer policy replaced it, use the newer one.
5. Put downloaded files in /tmp/claude-0/policies/<state_code>/ (create it). Work only in /tmp/claude-0/policies.
6. Do not edit anything under /home/user. Do not install system packages (python3 and pdftotext are available).

## What to extract (MSME, manufacturing, new or expansion units)
Only benefits the app can compute from invoices or simple inputs:
- capital_subsidy: % of eligible investment (plant & machinery, or fixed capital), or % of term loan; caps; minimums
- sgst_refund_ceiling: refund/reimbursement of SGST paid, capped at a % of fixed capital investment over N years
  (also called IPS / investment promotion subsidy / net SGST reimbursement)
- interest_subsidy: % per year on term loan interest, annual and total caps, number of years
- electricity_duty_exemption: exemption from electricity duty for N years (record zones/years)
- power_tariff_per_unit: Rs per unit power subsidy, years, cap
- certification_topup: reimbursement of certification costs (ZED, ISO, BIS...) with % and cap
Ignore stamp duty, land rebates, EPF reimbursement, employment subsidies, export freight, patent, training, unless they are
trivially computable; mention them briefly in "other_benefits" (one line each) so we know they exist.

If benefits depend on a zone/area/district/taluka category, capture the categories and HOW a unit finds its category
(annexure name/page). Do not transcribe whole district lists unless short (<=40 entries); give the annexure reference.

## Output: write /tmp/claude-0/policies/<state_code>.json (one file per state/UT), exactly this shape
{
  "state_code": "24",
  "state": "Gujarat",
  "status": "current" | "expired" | "not_found",
  "policy": {"name": "...", "order_no": "...", "date": "YYYY-MM-DD", "valid_from": "YYYY-MM-DD", "valid_to": "YYYY-MM-DD or null",
             "source_url": "direct URL of the official PDF/page", "language": "en|hi|..."},
  "zones": {"<zone_id>": "label / which areas", ...} or null,
  "zone_lookup": "how a unit finds its zone (annexure, page)" or null,
  "schemes": [
    {
      "id": "<2-letter-state>-<short>",            // e.g. "gj-msme-2022-capital"
      "name": "Human-readable scheme name",
      "categories": ["micro","small","medium"],     // who is covered
      "zones": ["A","B"] or null,                   // only if limited to some zones
      "asset_classes": ["plant_machinery","testing_equipment","pollution_control","solar"] or [],
      "window": {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"},   // investment/operative window
      "min_investment": 0,
      "benefit": { "type": "<one of the six types>", ...type fields below... },
      "clause": "section/para/table number in the document",
      "verified": true | false,
      "notes": "conditions that matter (new vs expansion, sector limits, women/SC/ST extras...)"
    }
  ],
  "other_benefits": ["Stamp duty: 100% exemption ... (para 5.2)", ...],
  "unverifiable": ["what you could not confirm and why"],
  "checked_on": "2026-10-17"
}

Benefit fields by type (rates as decimals, money in rupees):
- capital_subsidy: {"type","basis": "eligible_cost"|"term_loan", "rate": 0.25 | {"micro":..,"small":..,"medium":..},
                    "rate_by_zone": {"<zone>": 0.25 or {"micro":..}} (optional, instead of rate), "cap": 2500000 or null,
                    "cap_by_zone": {...} optional}
- sgst_refund_ceiling: {"type", "rate_by_zone": {"<zone>": 0.5}, "years_by_zone": {"<zone>": 7}}
                       (if no zones, use a single zone id "ALL")
- interest_subsidy: {"type", "rate": 0.05, "annual_cap": 2500000 or null, "years": 5 or null}
- electricity_duty_exemption: {"type", "years_by_zone": {"<zone>": 5}}  (or {"ALL": 5})
- power_tariff_per_unit: {"type", "per_unit": 1.0, "years": 3, "cap": 10000000 or null}
- certification_topup: {"type", "rate": 0.5, "cap": 50000}

## Finish
Reply with a short table: state | status | policy name + date | number of verified schemes | main gaps.
