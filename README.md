# AureusRecon

**Autonomous Fiscal Operations & Compliance Engine for Indian MSMEs and their Chartered Accountants.**

AureusRecon sits on top of a business's accounting software and does two things:

1. **Tax engine.** Reconciles every purchase invoice against GSTR-2B to protect input tax credit (ITC), drafts supplier reminders, and drafts replies to GST notices.
2. **Policy engine.** Reads the HSN codes on capital purchases, matches them against a rule grid of state and central incentive schemes, computes the benefit with caps applied, and pre-fills District Industries Centre (DIC) application packets.

One system serves three audiences: MSME owners, CA firms (multi-client console, white-labelled), and the public (the 60-second subsidy calculator).

---

**Docs:** [product flow, AI and data plan](docs/PRODUCT.md) · [deploying on Render + MongoDB Atlas](docs/DEPLOY.md)

## Quick start

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
docker compose up -d                  # local MongoDB 8 (or point MONGODB_URI at Atlas / your own server)
python -m aureus.seed                 # demo accounts + sample data
uvicorn aureus.api:app --reload       # http://localhost:8000
```

| Login | Password | Sees |
|---|---|---|
| `owner@demo.in` | `demo-pass-123` | Shree Ganesh Auto Components (Pune), fully loaded |
| `ca@demo.in` | `demo-pass-123` | Mehta & Associates console with two clients |

Or sign up fresh. The onboarding wizard has a **Try with demo data** button. API docs: `/api/docs`. Tests: `pytest`.

## How it works

```
 Tally XML / connector ─┐                         ┌─> Tax engine ──> reconciliation, supplier reminders,
 Zoho Books (OAuth)  ───┼─> ingestion adapters ─> │                  Notice Copilot drafts
 Busy / CSV          ───┤   (normalised Invoice)  │
 GSTR-2B JSON        ───┘          │              └─> Policy engine ─> scheme matches, estimates,
                                   ▼                                   DIC application packets
                    encrypted datasets (AES-256-GCM), decrypted in memory per run
```

| Path | Responsibility |
|---|---|
| `aureus/ingestion/` | Parsers for Tally Prime XML, Zoho Books bills JSON, Busy/generic CSV, the GSTN purchase register template, GSTR-2B JSON and IMS JSON |
| `aureus/engines/ims_actions.py` | Suggests IMS actions (Accept / Pending; never auto-Reject) from the reconciliation and builds the IMS upload file |
| `aureus/engines/tax.py` | Three-pass matcher and status rules (below) |
| `aureus/engines/policy.py` | HSN → asset class, MSME category, scheme eligibility and benefit maths |
| `aureus/engines/deadlines.py` | GSTR-2B / GSTR-3B dates (monthly and QRMP), Sec 16(4) time limit |
| `aureus/data/schemes.json` | The deterministic rule grid |
| `aureus/data/hs2022.json.gz`, `aureus/hsn.py` | HS 2022 product codes (UN Comtrade) used to validate and describe HSN codes |
| `aureus/outputs/notice.py` | Notice Copilot (DRC-01C, ASMT-10, DRC-01) and supplier reminders |
| `aureus/outputs/forms.py` | Pre-filled application packets (structured JSON and printable HTML) |
| `aureus/services.py` | Tenancy, consent, encrypted datasets, engine runs, audit log |
| `aureus/integrations.py` | Zoho Books OAuth 2.0 connector |
| `aureus/api.py` | FastAPI app; also serves the web client in `aureus/web/` |
| `connector/tally_push.py` | Stdlib-only desktop connector that pulls the Day Book from Tally's XML port and pushes it up |

### Tax engine

Composite key **supplier GSTIN + invoice number**, all arithmetic in integer paise.

| Status (PRD) | Rule |
|---|---|
| `MATCHED_ELIGIBLE` | In GSTR-2B, every head (taxable value, IGST, CGST, SGST, cess) within ₹0.99, credit available |
| `MISMATCH_WARN` | Paired, but some head differs by more than ₹0.99, the date differs, or 2B marks the credit unavailable |
| `MISSING_IN_PORTAL` | No GSTR-2B record. The supplier hasn't uploaded it and the credit is frozen |

Pairing runs in three passes:
1. Exact key, then a normalised key (`inv-0042` = `INV/42`).
2. Same supplier with a near-identical invoice number and the same amount, to catch supplier typos.
3. Same invoice number and amount under another GSTIN, to catch a wrong GSTIN in the ledger master.

A `reason` code refines each mismatch (`VALUE_MISMATCH`, `TAX_HEAD_MISMATCH`, `INVOICE_NO_MISMATCH`, `SUPPLIER_GSTIN_MISMATCH`, `ITC_NOT_AVAILABLE`, `DATE_MISMATCH`) and comes with a recommended action. Flags cover:
- wrong tax type for the place of supply
- reverse charge
- HSNs commonly blocked under Sec 17(5)
- the Sec 16(4) deadline

GSTR-2B entries with no booked invoice are reported separately as unclaimed credit.

### IMS (Invoice Management System)

Since October 2024, a supplier invoice only reaches GSTR-2B if the buyer accepts it in IMS or leaves it untouched. Upload the IMS JSON and the app does two things:

- **Explains each "missing" invoice.** It tells *held back by you* (Pending or Rejected in IMS) apart from *filed late by the supplier* and *never uploaded*.
- **Suggests an IMS action per document.** It accepts what matches the books and keeps amount mismatches and unbooked documents Pending. It never rejects automatically. The suggestions become an IMS upload file built in the format of GSTN's IMS Offline Tool v2.0. The file has not yet been tried against the live portal, so test it on one document first.

### Policy engine

A purchase-invoice line counts as a capital asset when two things hold:
- **Its HSN maps to an asset class.** Plant & machinery (ch. 84/85 minus IT/solar), testing equipment (9024–9031), solar, pollution control.
- **Its value is high enough.** The value per unit must reach ₹50,000. For solar and pollution control, the line total counts instead, because those are bought by the panel.

Each scheme in `schemes.json` declares its states, enterprise categories, sectors, asset classes, investment window and minimum spend. Maharashtra schemes also declare the eligible zones. The benefit formula is one of: capital subsidy, SGST-refund ceiling, electricity-duty exemption, ₹/unit power tariff subsidy, interest subsidy, or certification support. Caps apply throughout. Benefits that draw on the same zone ceiling are shown but not added to the headline total, so it never double-counts.

**Verified (17 Oct 2026):**

| Source | Section |
|---|---|
| Maharashtra Industries, Investment & Services Policy 2025, GR No. मऔधो-2023/प्र.क्र.187/उद्योग-2 dated 31.12.2025 ([PDF](https://industry.maharashtra.gov.in/sites/default/files/2025-12/202512311332497910.pdf)) | MSME section 4.1.1(A), Table 4, and the Annexure I taluka groups |
| [MSME ZED scheme guidelines](https://dcmsme.gov.in/CLCS_TUS_Scheme/ZED_Scheme/Scheme_Guidelines.aspx) | Key Benefits (fees and subsidy rates) |

Every verified scheme carries `source_document`, `clause` and `verified_on`, which the UI and the printed application show.

| Maharashtra taluka group | A | B | C | D | D+ | Vidarbha/Marathwada etc. | No-industry / aspirational |
|---|---|---|---|---|---|---|---|
| SGST-refund ceiling (% of FCI) | 30% | 40% | 50% | 60% | 70% | 80% | 100% |
| Incentive period (years) | 5 | 7 | 7 | 10 | 10 | 10 | 10 |
| Electricity duty exemption | – | – | ✓ | ✓ | ✓ | ✓ | ✓ |
| Power tariff ₹1/unit, 3 yrs | – | – | – | ✓ | ✓ | ✓ | ✓ |

PSI-2019 is not in the grid. MIISP 2025 replaced it for new investment from 31 Dec 2025, and PSI-2019 put MSMEs in Group A (e.g. Pune City, Haveli in PMR) outside the scheme altogether.

**Still open in Maharashtra:**
- The rules on what counts as eligible fixed capital investment, on expansion units and on cut-off dates are left to a separate implementing scheme order, which I could not find.
- Ceilings use only the capital purchases found in the books. Building and other fixed assets would raise them.

> ⚠️ **Gujarat, Tamil Nadu, Karnataka and Uttar Pradesh are still illustrative** (`"verified": false`). The UI warns whenever an unverified scheme is counted.

### Notice Copilot

Builds a reply from section-specific templates. It groups invoices by mismatch reason and attaches an annexure list.

Every legal citation comes from a fixed, reviewed list in `notice.py`. Each citation states its caveat, for example when it predates Sec 16(2)(aa). The list also includes the adverse Supreme Court authority on burden of proof, so the reply addresses it instead of ignoring it.

If `ANTHROPIC_API_KEY` is set, ticking *Polish with Claude* refines the wording (model `claude-opus-5-5`, server-side refusal fallback enabled). The prompt forbids changing facts or citations, and the template is kept if any citation goes missing or the call fails. Drafts are marked for professional review either way.

## Database (MongoDB)

All state lives in MongoDB (`aureus/db.py` lists every collection).

- **Encrypted imports.** Each import is one AES-256-GCM ciphertext stored in `dataset_chunks` (≤ 4 MB per document, so large imports never reach MongoDB's 16 MB limit). `datasets` holds only the metadata, and is written after the chunks so a half-written import is never visible.
- **Automatic expiry.** Sessions and OAuth states carry TTL indexes, so MongoDB deletes them when they expire.
- **Uniqueness.** Unique indexes enforce one account per email and one membership per person per business.
- **Readable ids.** Integer ids come from a `counters` collection, so URLs stay short.
- **No multi-document transactions.** The app works on a standalone server as well as on a replica set or Atlas. Operations that touch several collections are ordered so that a partial failure is safe: revocation marks consent revoked before purging, and deleting a business removes access before the data.

Tests use a fresh database per test on the server at `AUREUS_TEST_MONGODB_URI` (default `mongodb://127.0.0.1:27017`). Without a server they fall back to `mongomock`, in memory.

## Security & compliance posture (PRD §4)

- **Data-blind storage.** Each import is stored as one AES-256-GCM blob bound to its organisation (`org:{id}:dataset` as associated data). It is decrypted only in memory for a calculation.
- **Identifiers encrypted at rest.** The organisation's GSTIN and Udyam number are encrypted, and so are OAuth tokens.
- **Nothing reversible for secrets.** Session and connector tokens are stored only as SHA-256 hashes, and passwords as PBKDF2 hashes. A test dumps the database and asserts that no GSTIN, invoice number, supplier name or token appears in it.
- **No credentials collected.** The app never asks for GST portal, bank or accounting passwords. Zoho uses OAuth, and Tally data comes through a local connector key.
- **Consent.** Each purpose (`books_sync`, `gstr2b_feed`) gets a stored consent artefact: data categories, 12-month expiry and the revocation terms. Imports are refused without active consent. **Revoke** deletes every dataset, integration and connector key collected under that consent immediately.
- **Tenancy.** Every organisation route checks membership. CAs see only clients they created or were invited to, and cannot delete a client that has an owner.
- **Audit log.** Imports, consent grants and revocations, and access changes are logged without financial payloads. The app also sends strict security headers, including a CSP with no inline scripts.

Set `AUREUS_MASTER_KEY` (base64 of 32 random bytes) in production. Without it, a development key file is generated under `var/` (it never goes into the database). `AUREUS_ENV=production` refuses to start without a key.

## Configuration

| Variable | Purpose |
|---|---|
| `AUREUS_MASTER_KEY` | AES-256 key, base64. **Required in production** |
| `AUREUS_ENV` | `production` enforces the master key |
| `MONGODB_URI` | MongoDB connection string, default `mongodb://127.0.0.1:27017`. Atlas `mongodb+srv://` URIs work |
| `MONGODB_DB` | Database name, default `aureus` |
| `AUREUS_DATA_DIR` | Where the development key file lives |
| `AUREUS_MATCH_TOLERANCE_PAISE` | Matched threshold, default `99` (₹0.99) |
| `ANTHROPIC_API_KEY` | Enables *Polish with Claude* in Notice Copilot |
| `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_REDIRECT_URI` | Enables one-click Zoho Books OAuth (India DC by default; override `ZOHO_ACCOUNTS_URL` / `ZOHO_API_URL`) |

## What is production-ready vs. what needs work

**Built and tested** (49 tests against MongoDB 8.0, plus a browser run through every screen):
- ingestion parsers and the reconciliation engine
- paisa-level matching
- the policy maths
- consent with purge-on-revoke
- encryption at rest
- multi-tenant access and the CA console
- application packets and the notice drafts

**Coded to documented interfaces but not run against live systems:**
- **Zoho Books OAuth** needs a registered Zoho client.
- **The Tally connector** needs Tally Prime with the XML server enabled. Tally's Day Book export fields vary by TDL customisation, so test with a real company file.

**Not built yet:**
- **Licensed GSTR-2B feed.** Pulling GSTR-2B automatically requires a GSP licence or a GST Suvidha Provider partner (the PRD mentions Account Aggregators). Until then the user downloads the JSON from the portal and uploads it; the parser reads the portal's own format.
- **Verified scheme data for states other than Maharashtra.** See the warning above.
- **Production hardening** beyond the basics: a KMS-held master key, MongoDB authentication and backups, rate limiting on login, email verification and password reset, background jobs for scheduled syncs, and invitations for users who don't yet have an account.
