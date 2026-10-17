# AureusRecon

**Autonomous Fiscal Operations & Compliance Engine for Indian MSMEs and their Chartered Accountants.**

AureusRecon sits on top of a business's accounting software and does two things:

1. **Tax engine.** Reconciles every purchase invoice against GSTR-2B to protect input tax credit (ITC), drafts supplier reminders, and drafts replies to GST notices.
2. **Policy engine.** Reads the HSN codes on capital purchases, matches them against a rule grid of state and central incentive schemes, computes the benefit with caps applied, and pre-fills District Industries Centre (DIC) application packets.

One system serves three audiences: MSME owners, CA firms (multi-client console, white-labelled), and the public (the 60-second subsidy calculator).

---

**Docs:** [product flow, AI and data plan](docs/PRODUCT.md) · [deploying on Render + PostgreSQL](docs/DEPLOY.md)

## Quick start

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
docker compose up -d                  # local PostgreSQL 16 (or set DATABASE_URL to any Postgres)
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

**Coverage: every state and union territory (checked 17 Oct 2026).** See [docs/STATE_COVERAGE.md](docs/STATE_COVERAGE.md) or the app's *State coverage* page.

| | States / UTs |
|---|---|
| All schemes verified against the official policy | 20 |
| Partly verified | 4 (Bihar, Kerala, Puducherry, Rajasthan) |
| Current policy, but no official text reachable (figures unverified, shown with a warning) | 7 (Andhra Pradesh, Jammu and Kashmir, Madhya Pradesh, Mizoram, Tripura, Uttarakhand, West Bengal) |
| No current policy (lapsed or not found; only central schemes apply) | 5 (Andaman and Nicobar Islands, Chandigarh, Delhi, Himachal Pradesh, Jharkhand) |

174 schemes in total, 137 verified. Central schemes (ZED at the September 2024 fees, PMEGP, SC/ST capital subsidy, UNNATI for the North-East) are included.

**How a state is added or re-checked:**
1. **Research.** A researcher reads the official policy and writes a file in the format set by `research/BRIEF.md`. The raw file goes in `research/policies/raw/` unchanged, as evidence.
2. **Curation.** `research/curate.py` applies the documented adjustments: conditions the app can't check (ownership, sector lists), either/or groups, investment slabs, interest floors, and benefits it can't compute. Every change is written into the curated file's `curation` list.
3. **Merge.** `tools/merge_policies.py` validates every rule and merges it into `aureus/data/schemes.json`. It refuses unknown benefit types, impossible rates, undefined zones, bad dates, and verified figures without a source. It also regenerates the coverage table.
4. **Safety net.** A test runs every scheme against every state, zone and business size, so no rule can crash the engine or produce an absurd figure.

**How amounts are shown:**
- **Headline total.** Only benefits the unit clearly qualifies for are added up. Either/or benefits count once, at the larger amount.
- **Kept out of the total:** schemes with eligibility conditions (shown with the condition), first-year interest figures, and "up to" certification reimbursements.
- **Ceilings.** SGST refunds are shown as "up to" the policy's ceiling, because the actual payout depends on tax paid.

Maharashtra was verified by hand earlier and stays in the rule grid as it was. One correction from that work: the ZED fees were cut in September 2024 to ₹8,000 / ₹32,000 / ₹72,000, and the estimates now use the Gold fee.

### Notice Copilot

Builds a reply from section-specific templates. It groups invoices by mismatch reason and attaches an annexure list.

Every legal citation comes from a fixed, reviewed list in `notice.py`. Each citation states its caveat, for example when it predates Sec 16(2)(aa). The list also includes the adverse Supreme Court authority on burden of proof, so the reply addresses it instead of ignoring it.

If `ANTHROPIC_API_KEY` is set, ticking *Polish with Claude* refines the wording (model `claude-opus-5-5`, server-side refusal fallback enabled). The prompt forbids changing facts or citations, and the template is kept if any citation goes missing or the call fails. Drafts are marked for professional review either way.

## Database (PostgreSQL)

All state lives in PostgreSQL 16 (`aureus/db.py` lists every table).

- **Encrypted imports.** Each import is one AES-256-GCM ciphertext in `datasets.payload_enc`. GSTIN, Udyam number and OAuth tokens are encrypted too; tokens and passwords are stored only as hashes.
- **Foreign keys.** Deleting a business cascades to everything it owns. The audit log is kept, since it holds no financial data.
- **Transactions.** Revoking consent and purging its data happen together or not at all, and so do sync swaps and org creation.
- **Migrations.** Numbered migrations run once at start-up, under an advisory lock so the web app and the cron job never race.
- **Expiry.** Sessions, OAuth states, reset tokens and login-failure records are filtered by time in every query, and purged by the daily job.

Tests run against a real Postgres database, `AUREUS_TEST_DATABASE_URL` (default `postgresql://postgres@127.0.0.1:5432/aureus_test`), and empty every table between tests. `docker compose up -d` creates both databases.

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
| `DATABASE_URL` | PostgreSQL connection string, default `postgresql://postgres@127.0.0.1:5432/aureus` |
| `AUREUS_DB_POOL_SIZE` | Maximum database connections per process, default 10 |
| `AUREUS_DATA_DIR` | Where the development key file lives |
| `AUREUS_MATCH_TOLERANCE_PAISE` | Matched threshold, default `99` (₹0.99) |
| `APP_BASE_URL` | Public URL used in emailed links |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM` | Outgoing email for password resets and alerts. In development, emails are printed to the log |
| `AUREUS_LOGIN_MAX_FAILURES`, `AUREUS_LOGIN_MAX_FAILURES_PER_IP`, `AUREUS_LOGIN_WINDOW_MINUTES` | Login lockout: by default 5 failures per email or 20 per IP within 15 minutes |
| `ANTHROPIC_API_KEY` | Enables *Polish with Claude* in Notice Copilot |
| `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_REDIRECT_URI` | Enables one-click Zoho Books OAuth (India DC by default; override `ZOHO_ACCOUNTS_URL` / `ZOHO_API_URL`) |

## What is production-ready vs. what needs work

**Built and tested** (67 tests against PostgreSQL 16, plus a browser run through every screen):
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
- **Official texts that couldn't be reached.** Seven states have current policies whose official text couldn't be reached from the research environment (see the coverage table). Their figures are shown with a warning until someone in India downloads the official text.
- **Production hardening** still to do: a KMS-held master key, database backup policy, email verification at signup, and invitations for users who don't yet have an account. Done already: login lockout, password reset and change, and a daily job for Zoho sync, deadline alerts and consent-expiry warnings.
