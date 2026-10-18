# What AureusRecon is, how it flows, and what would make it better

## The product in one line

Every month, AureusRecon reads a factory's purchase records and its GSTR-2B. It tells the factory (or its CA) how much GST credit is at risk and why, which government incentives its investments qualify for, and hands over ready-to-send paperwork: supplier reminders, notice replies and subsidy applications.

## The flow

```
1. Onboard ─> 2. Bring in data ─> 3. Check tax credit ─> 4. Find incentives ─> 5. Act ─> 6. Repeat monthly
```

| Stage | What happens | Status today | What would make it better |
|---|---|---|---|
| **1. Onboard** | Business profile, GSTIN check, taluka group (district/taluka picker for Gujarat and J&K), consent | Built | Read the Udyam certificate PDF to pre-fill the profile |
| **2. Bring in data** | Tally XML / Zoho / Busy CSV / GSTN template + GSTR-2B + IMS JSON | Built; Zoho and Tally connector untested on live systems | Real client exports to harden parsers; licensed GSTR-2B feed (GSP) instead of manual download |
| **3. Check tax credit** | Match every bill to GSTR-2B, explain misses with IMS, suggest IMS actions, HSN checks | Built (paisa-exact, 3-pass matching, GSTN categories, IMS) | GST rate check (was the right rate charged for this HSN?); supplier filing-history score; multi-month carry-forward |
| **4. Find incentives** | Machinery purchases → state/central scheme rules → estimate | Built for every state and UT: 182 schemes, 172 verified against official policies; only West Bengal unverified, 5 with no current policy ([coverage](STATE_COVERAGE.md)) | West Bengal's notification; UP's region list; taluka lists for more states; read building and land costs too |
| **5. Act** | Supplier reminders, notice replies, DIC application packets | Built | Read the notice PDF the officer sent and pre-fill the reply; send reminders by email/WhatsApp |
| **6. Repeat monthly** | New month's data, deadline countdown, CA console | Manual uploads | Scheduled syncs and alerts before the 20th |

## Where AI is used, and where it isn't

**AI sits at the edges, and the core stays deterministic.** Tax credit amounts and subsidy eligibility must be exact and explainable to a tax officer or a DIC official, so the matching and the rules are plain code: the same input always gives the same answer, and every rupee traces back to a rule or an invoice. AI (Gemini, switched on with `GEMINI_API_KEY`) handles the messy edges, and the app checks its output before showing it:

| AI feature | Status | What it does | What still improves it |
|---|---|---|---|
| **A. HSN fixer** | Built | Suggests the 8-digit code for lines with a missing or wrong HSN, picking only from a shortlist drawn from the official GST directory, and flags when the fix makes the item count for subsidies | One real Tally item master to measure accuracy |
| **B. Notice reader** | Built | Reads a DRC-01C / ASMT-10 / DRC-01 PDF or photo, fills Notice Copilot and ticks the invoices in dispute | 5–10 real notices (names blanked) to measure accuracy |
| **C. Policy reader** | Built (command line) | Drafts a rule file from a government order for a person to verify | Nothing; used whenever a state issues a new order |
| **D. Supplier matcher** | Built | Finds "missing" bills that are in GSTR-2B under another name, GSTIN or invoice number | One real client month |
| **E. Ask AI assistant** | Built | Answers questions about the business in English or Hindi, only from its own results | Nothing new |
| Notice reply polish | Built | Gemini (or Claude) tightens a reply's wording without touching facts or citations | — |

No model training is needed: the models work from instructions and the app's own data. Before showing these to paying clients, measure them on a handful of **real, labelled examples per feature**.

**Free vs paid Gemini key.** On the free tier Google may use what is sent to improve its products. With `AUREUS_AI_TIER=free` (the default) the app replaces client names and GSTINs with placeholders in assistant questions, and asks the user to confirm before an uploaded notice is sent. Use a paid key (and set `AUREUS_AI_TIER=paid`) before real client data goes through it.

## Data: what I collected, what I couldn't, and what only you can get

**Collected and used (public, official):**
- **HS 2022 product-code list** (UN Comtrade, 6,939 codes). Used for readable item descriptions.
- **GST portal HSN directory** (sent by you: 21,928 goods codes down to India's 8-digit tariff lines, 681 SAC service codes). Every bill line's HSN is checked against it; a code whose heading is real but whose 8-digit line doesn't exist is flagged `HSN_UNLISTED`. Rebuild with `python tools/build_hsn.py HSN_SAC.xlsx` when the portal updates it.
- **Maharashtra MIISP 2025 government order and its taluka list.** Used for verified subsidy rules.
- **MSME ZED scheme guidelines.** Used for verified certification subsidy figures.
- **PSI-2019 government order.** Used to confirm it no longer applies.

**Collected from the GST portal's offline tools** (downloaded on retry; the portal throttles non-Indian traffic):
- **IMS Offline Tool v2.0.** I decoded the IMS download/upload JSON format from its macros. The app now imports IMS files, uses them to explain *why* an invoice is missing from GSTR-2B (held back by you vs. not uploaded by the supplier), and generates an IMS action file for upload.
- **Matching Offline Tool v2.9.** From its purchase register template v2.4, any accounting software that can fill the template can now feed the app. Its six official match categories (*Exact, Partial, Probable, Unmatched, In 2B not in PR, In PR not in 2B*) are shown beside our own reasons.
- **Not used:** the other offline tools (TRAN, GSTR-4/6/7/8/10/11, SRM, ITC-01/03/04, annual returns, advance ruling, DRC-22A, TDS/TCS) are for other taxpayer types or filings outside this product. The portal's **tax-collection statistics** are useful for market sizing in a pitch, not for the product. The **CBIC and state tax websites** are the sources to use for Notice Copilot citations and GST rate data.

**Could not get from here:**
- **The GST portal** (gst.gov.in) often refuses connections from outside India, so its files have to be downloaded from India and sent over (as was done for the HSN directory).

**Competitor apps:** I only looked at their public pages (features, pricing). Their customers' data is other businesses' confidential financial records, and taking it would breach their terms and the law (DPDP Act). Public positioning:
- **ClearTax (ClearGST):** an established enterprise and CA product, with *exact / suggested / mismatch / missing* matching buckets.
- **KDK ExpressGST:** aimed at CA firms.
- **Low-cost AI tools** (AI Accountant, Munim, around ₹500/month): pull GSTR-2B directly from the portal.
- **Where none of them seem to go:** none of the reviews I found combine reconciliation with **subsidy matching** or a **CA white-label console**. That is AureusRecon's angle.

**Only you can get (needs client permission):**
1. **One month of real data:** a Tally purchase export plus the same month's GSTR-2B. This is the most important item.
2. **Tally item master:** item names with their HSN codes (for the HSN fixer).
3. **5–10 GST notices** the CA has received, names blanked (for the notice reader).
4. **The DIC's implementing scheme order** for MIISP 2025, and one past subsidy application that the DIC accepted.
