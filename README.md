# AureusRecon

**Autonomous Fiscal Operations & Compliance Engine for Indian MSMEs and their Chartered Accountants.**

AureusRecon sits on top of a business's accounting software and does two things:

1. **Tax engine.** Reconciles every purchase invoice against GSTR-2B to protect input tax credit (ITC), drafts supplier reminders, and drafts replies to GST notices.
2. **Policy engine.** Reads the HSN codes on capital purchases, matches them against a rule grid of state and central incentive schemes, computes the benefit with caps applied, and pre-fills District Industries Centre (DIC) application packets.

One system serves three audiences: MSME owners, CA firms (multi-client console, white-labelled), and the public (the 60-second subsidy calculator).

---

## Quick start

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
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
| `aureus/ingestion/` | Parsers for Tally Prime XML, Zoho Books bills JSON, Busy/generic CSV and GSTR-2B JSON, all producing one `Invoice` model |
| `aureus/engines/tax.py` | Three-pass matcher and status rules (below) |
| `aureus/engines/policy.py` | HSN → asset class, MSME category, scheme eligibility and benefit maths |
| `aureus/engines/deadlines.py` | GSTR-2B / GSTR-3B dates (monthly and QRMP), Sec 16(4) time limit |
| `aureus/data/schemes.json` | The deterministic rule grid |
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

### Policy engine

A purchase-invoice line counts as a capital asset when two things hold:
- **Its HSN maps to an asset class.** Plant & machinery (ch. 84/85 minus IT/solar), testing equipment (9024–9031), solar, pollution control.
- **Its value is high enough.** The value per unit must reach ₹50,000. For solar and pollution control, the line total counts instead, because those are bought by the panel.

Each scheme in `schemes.json` declares states, enterprise categories, sectors, asset classes, an investment window, minimum spend, benefit formula (capital subsidy on cost or term loan, power-tariff exemption, certification support, interest subvention) and cap. MSME category uses the revised investment/turnover limits.

> ⚠️ **The scheme figures are illustrative.** Every scheme carries `"verified": false`. Before relying on an estimate, check the rate, cap and window against the current notification, then set `verified: true`. The UI shows a warning on every estimate and on generated forms until then.

### Notice Copilot

Builds a reply from section-specific templates. It groups invoices by mismatch reason and attaches an annexure list.

Every legal citation comes from a fixed, reviewed list in `notice.py`. Each citation states its caveat, for example when it predates Sec 16(2)(aa). The list also includes the adverse Supreme Court authority on burden of proof, so the reply addresses it instead of ignoring it.

If `ANTHROPIC_API_KEY` is set, ticking *Polish with Claude* refines the wording (model `claude-opus-5-5`, server-side refusal fallback enabled). The prompt forbids changing facts or citations, and the template is kept if any citation goes missing or the call fails. Drafts are marked for professional review either way.

## Security & compliance posture (PRD §4)

- **Data-blind storage.** Each import is stored as one AES-256-GCM blob bound to its organisation (`org:{id}:dataset` as associated data). It is decrypted only in memory for a calculation.
- **Identifiers encrypted at rest.** The organisation's GSTIN and Udyam number are encrypted, and so are OAuth tokens.
- **Nothing reversible for secrets.** Session and connector tokens are stored only as SHA-256 hashes, and passwords as PBKDF2 hashes. A test dumps the database and asserts that no GSTIN, invoice number, supplier name or token appears in it.
- **No credentials collected.** The app never asks for GST portal, bank or accounting passwords. Zoho uses OAuth, and Tally data comes through a local connector key.
- **Consent.** Each purpose (`books_sync`, `gstr2b_feed`) gets a stored consent artefact: data categories, 12-month expiry and the revocation terms. Imports are refused without active consent. **Revoke** deletes every dataset, integration and connector key collected under that consent immediately.
- **Tenancy.** Every organisation route checks membership. CAs see only clients they created or were invited to, and cannot delete a client that has an owner.
- **Audit log.** Imports, consent grants and revocations, and access changes are logged without financial payloads. The app also sends strict security headers, including a CSP with no inline scripts.

Set `AUREUS_MASTER_KEY` (base64 of 32 random bytes) in production. Without it, a development key file is generated under `var/`. `AUREUS_ENV=production` refuses to start without a key.

## Configuration

| Variable | Purpose |
|---|---|
| `AUREUS_MASTER_KEY` | AES-256 key, base64. **Required in production** |
| `AUREUS_ENV` | `production` enforces the master key |
| `AUREUS_DATA_DIR` / `AUREUS_DB_PATH` | Where SQLite and the dev key live |
| `AUREUS_MATCH_TOLERANCE_PAISE` | Matched threshold, default `99` (₹0.99) |
| `ANTHROPIC_API_KEY` | Enables *Polish with Claude* in Notice Copilot |
| `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_REDIRECT_URI` | Enables one-click Zoho Books OAuth (India DC by default; override `ZOHO_ACCOUNTS_URL` / `ZOHO_API_URL`) |

## What is production-ready vs. what needs work

**Built and tested** (29 tests, plus a browser run through every screen):
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
- **Verified scheme data.** See the warning above.
- **Production hardening** beyond the basics: SQLite → Postgres, a KMS-held master key, rate limiting on login, email verification and password reset, background jobs for scheduled syncs, and invitations for users who don't yet have an account.
