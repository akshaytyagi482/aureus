"""Application services: tenants, consent, encrypted datasets and engine runs.

Raw invoices never sit in plain columns. Each import is sealed as one
AES-256-GCM ciphertext bound to its organisation, and is only decrypted in
memory for the duration of a reconciliation or policy run.
"""

from datetime import date, timedelta

from psycopg.errors import UniqueViolation

from . import config, crypto, db, gstin as gstin_mod
from .db import fmt, tx
from .engines import deadlines, ims_actions, policy, tax
from .ingestion import ims as ingest_ims
from .models import Invoice

PURPOSES = {
    "books_sync": {
        "label": "Read purchase records from accounting software",
        "data": ["Purchase invoices", "Supplier names and GSTINs", "Item descriptions and HSN codes"],
        "dataset_kinds": ["books"],
        "integrations": ["zoho"],
    },
    "gstr2b_feed": {
        "label": "Read GSTR-2B and IMS statements from the GST system",
        "data": ["GSTR-2B and IMS invoice details uploaded by suppliers", "Your IMS accept/reject/pending actions"],
        "dataset_kinds": ["gstr2b", "ims"],
        "integrations": [],
    },
}
CONSENT_DAYS = 365


class AccessDenied(Exception):
    pass


class ConsentRequired(Exception):
    pass


class EmailTaken(Exception):
    pass


def audit(org_id, user_id, action, detail=None, cur=None):
    sql = "INSERT INTO audit_log (org_id, user_id, action, detail) VALUES (%s, %s, %s, %s)"
    params = (org_id, user_id, action, db.json(detail) if detail is not None else None)
    if cur is not None:
        cur.execute(sql, params)
    else:
        db.execute(sql, params)


# ---------------------------------------------------------------- users & sessions

USER_COLUMNS = "id, email, name, role, firm_name"


def create_user(email: str, name: str, password: str, role: str, firm_name: str | None = None) -> dict:
    try:
        return db.one(f"INSERT INTO users (email, name, password_hash, role, firm_name) VALUES (%s, %s, %s, %s, %s) "
                      f"RETURNING {USER_COLUMNS}",
                      (email.lower().strip(), name.strip(), crypto.hash_password(password), role, firm_name))
    except UniqueViolation:
        raise EmailTaken(email)


def get_user(user_id: int) -> dict | None:
    return db.one(f"SELECT {USER_COLUMNS} FROM users WHERE id = %s", (user_id,))


def login(email: str, password: str) -> tuple[str, dict] | None:
    row = db.one("SELECT * FROM users WHERE email = %s", (email.lower().strip(),))
    if not row or not crypto.verify_password(password, row["password_hash"]):
        return None
    token = crypto.new_token()
    db.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
               (crypto.token_hash(token), row["id"], db.now() + timedelta(hours=config.SESSION_TTL_HOURS)))
    return token, {k: row[k] for k in ("id", "email", "name", "role", "firm_name")}


def user_for_token(token: str) -> dict | None:
    return db.one("SELECT u.id, u.email, u.name, u.role, u.firm_name FROM sessions s JOIN users u ON u.id = s.user_id "
                  "WHERE s.token_hash = %s AND s.expires_at > now()", (crypto.token_hash(token),))


def logout(token: str) -> None:
    db.execute("DELETE FROM sessions WHERE token_hash = %s", (crypto.token_hash(token),))


# ---------------------------------------------------------------- organisations

def _ctx(org_id: int, what: str) -> str:
    return f"org:{org_id}:{what}"


ORG_FIELDS = ("name", "district", "address", "sector", "industry", "annual_turnover", "investment_pm",
              "term_loan_amount", "zone", "monthly_electricity_duty", "monthly_power_units", "filing_frequency")


def _org_out(row: dict) -> dict:
    org = {k: row[k] for k in ORG_FIELDS}
    org.update(id=row["id"], state_code=row["state_code"], gstin_last4=row["gstin_last4"],
               created_by=row["created_by"], created_at=fmt(row["created_at"]))
    org["gstin"] = crypto.decrypt_text(row["gstin_enc"], _ctx(row["id"], "gstin"))
    org["udyam"] = crypto.decrypt_text(row["udyam_enc"], _ctx(row["id"], "udyam"))
    org["state"] = gstin_mod.state_name(org["state_code"])
    org["category"] = policy.classify_enterprise(org["investment_pm"], org["annual_turnover"])
    org["advisor"] = db.one("SELECT u.name, u.firm_name FROM memberships m JOIN users u ON u.id = m.user_id "
                            "WHERE m.org_id = %s AND m.role = 'ca' ORDER BY u.id LIMIT 1", (row["id"],))
    return org


def _org_changes(org_id: int, data: dict) -> dict:
    sets = {k: data[k] for k in ORG_FIELDS if k in data}
    if "udyam" in data:
        sets["udyam_enc"] = crypto.encrypt_text((data["udyam"] or "").strip().upper() or None, _ctx(org_id, "udyam"))
    return sets


def _apply_changes(cur, org_id: int, changes: dict) -> None:
    if changes:
        cur.execute(f"UPDATE orgs SET {', '.join(f'{k} = %s' for k in changes)} WHERE id = %s",
                    (*changes.values(), org_id))


def create_org(user: dict, data: dict) -> dict:
    gstin = data["gstin"].strip().upper()
    if not gstin_mod.is_valid(gstin):
        raise ValueError("GSTIN is not valid (format or checksum)")
    with tx() as cur:
        # The id is part of the encryption context, so insert first, then seal the GSTIN.
        cur.execute("INSERT INTO orgs (name, gstin_enc, gstin_last4, state_code, created_by) "
                    "VALUES (%s, '', %s, %s, %s) RETURNING id", (data["name"], gstin[-4:], gstin[:2], user["id"]))
        org_id = cur.fetchone()["id"]
        changes = _org_changes(org_id, data)
        changes["gstin_enc"] = crypto.encrypt_text(gstin, _ctx(org_id, "gstin"))
        _apply_changes(cur, org_id, changes)
        cur.execute("INSERT INTO memberships (org_id, user_id, role) VALUES (%s, %s, %s)",
                    (org_id, user["id"], "owner" if user["role"] == "msme" else "ca"))
        audit(org_id, user["id"], "org_created", cur=cur)
    return get_org_by_id(org_id)


def update_org(user: dict, org_id: int, data: dict) -> dict:
    require_member(user, org_id)
    with tx() as cur:
        _apply_changes(cur, org_id, _org_changes(org_id, data))
        audit(org_id, user["id"], "org_updated",
              {"fields": sorted(k for k in data if k in ORG_FIELDS or k == "udyam")}, cur=cur)
    return get_org_by_id(org_id)


def require_member(user: dict, org_id: int) -> str:
    row = db.one("SELECT role FROM memberships WHERE org_id = %s AND user_id = %s", (org_id, user["id"]))
    if not row:
        raise AccessDenied("You do not have access to this organisation")
    return row["role"]


def get_org(user: dict, org_id: int) -> dict:
    require_member(user, org_id)
    return get_org_by_id(org_id)


def get_org_by_id(org_id: int) -> dict:
    """Internal lookup without a membership check (connector tokens are already org-scoped)."""
    row = db.one("SELECT * FROM orgs WHERE id = %s", (org_id,))
    if not row:
        raise AccessDenied("You do not have access to this organisation")
    return _org_out(row)


def list_orgs(user: dict) -> list[dict]:
    rows = db.all("SELECT o.* FROM orgs o JOIN memberships m ON m.org_id = o.id WHERE m.user_id = %s "
                  "ORDER BY o.name", (user["id"],))
    return [_org_out(r) for r in rows]


def add_member(user: dict, org_id: int, email: str) -> dict:
    require_member(user, org_id)
    other = db.one("SELECT id, name, role, firm_name FROM users WHERE email = %s", (email.lower().strip(),))
    if not other:
        raise ValueError("No AureusRecon account uses that email. Ask them to sign up first.")
    role = "owner" if other["role"] == "msme" else "ca"
    with tx() as cur:
        cur.execute("INSERT INTO memberships (org_id, user_id, role) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (org_id, other["id"], role))
        audit(org_id, user["id"], "member_added", {"user_id": other["id"], "role": role}, cur=cur)
    return {"user_id": other["id"], "name": other["name"], "role": role, "firm_name": other["firm_name"]}


def list_members(user: dict, org_id: int) -> list[dict]:
    require_member(user, org_id)
    return db.all("SELECT u.id AS user_id, u.name, u.email, u.firm_name, m.role FROM memberships m "
                  "JOIN users u ON u.id = m.user_id WHERE m.org_id = %s ORDER BY m.role DESC, u.name", (org_id,))


def delete_org(user: dict, org_id: int) -> None:
    role = require_member(user, org_id)
    with tx() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM memberships WHERE org_id = %s AND role = 'owner'", (org_id,))
        if role != "owner" and cur.fetchone()["n"]:
            raise AccessDenied("Only the business owner can delete this organisation")
        cur.execute("DELETE FROM orgs WHERE id = %s", (org_id,))  # cascades to every owned row
        audit(None, user["id"], "org_deleted", {"org_id": org_id}, cur=cur)


# ---------------------------------------------------------------- consent

def grant_consent(user: dict, org_id: int, purpose: str) -> dict:
    if purpose not in PURPOSES:
        raise ValueError(f"Unknown purpose '{purpose}'")
    require_member(user, org_id)
    granted = db.now()
    expires = granted + timedelta(days=CONSENT_DAYS)
    artefact = {
        "version": "1.0",
        "data_fiduciary": "AureusRecon",
        "purpose": purpose,
        "purpose_text": PURPOSES[purpose]["label"],
        "data_categories": PURPOSES[purpose]["data"],
        "granted_by": user["email"],
        "granted_at": granted.isoformat(timespec="seconds"),
        "expires_at": expires.isoformat(timespec="seconds"),
        "storage": "Encrypted at rest (AES-256-GCM); portal and bank credentials are never collected",
        "revocation": "Revoke anytime; all data collected under this consent is deleted immediately",
    }
    with tx() as cur:
        cur.execute("UPDATE consents SET revoked_at = now() WHERE org_id = %s AND purpose = %s AND revoked_at IS NULL",
                    (org_id, purpose))
        cur.execute("INSERT INTO consents (org_id, purpose, granted_by, granted_at, expires_at, artefact) "
                    "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                    (org_id, purpose, user["id"], granted, expires, db.json(artefact)))
        consent_id = cur.fetchone()["id"]
        audit(org_id, user["id"], "consent_granted", {"purpose": purpose}, cur=cur)
    return {**artefact, "consent_id": consent_id}


def active_consents(org_id: int) -> dict:
    rows = db.all("SELECT id, purpose, artefact FROM consents WHERE org_id = %s AND revoked_at IS NULL "
                  "AND expires_at > now()", (org_id,))
    return {r["purpose"]: {"consent_id": r["id"], **r["artefact"]} for r in rows}


def revoke_consent(user: dict, org_id: int, purpose: str) -> dict:
    """Revoke and purge in one transaction. `purpose='all'` revokes everything."""
    require_member(user, org_id)
    purposes = list(PURPOSES) if purpose == "all" else [purpose]
    for p in purposes:
        if p not in PURPOSES:
            raise ValueError(f"Unknown purpose '{p}'")
    kinds = [k for p in purposes for k in PURPOSES[p]["dataset_kinds"]]
    providers = [prov for p in purposes for prov in PURPOSES[p]["integrations"]]
    with tx() as cur:
        cur.execute("UPDATE consents SET revoked_at = now() WHERE org_id = %s AND purpose = ANY(%s) "
                    "AND revoked_at IS NULL", (org_id, purposes))
        cur.execute("DELETE FROM datasets WHERE org_id = %s AND kind = ANY(%s)", (org_id, kinds))
        purged = {"datasets": cur.rowcount}
        cur.execute("DELETE FROM integrations WHERE org_id = %s AND provider = ANY(%s)", (org_id, providers))
        purged["integrations"] = cur.rowcount
        if "books_sync" in purposes:
            cur.execute("DELETE FROM connector_tokens WHERE org_id = %s", (org_id,))
        audit(org_id, user["id"], "consent_revoked", {"purposes": purposes, **purged}, cur=cur)
    return {"revoked": purposes, **purged}


def _require_consent(org_id: int, kind: str):
    purpose = next(p for p, spec in PURPOSES.items() if kind in spec["dataset_kinds"])
    if purpose not in active_consents(org_id):
        raise ConsentRequired(f"Consent for '{PURPOSES[purpose]['label']}' is needed before importing this data")


# ---------------------------------------------------------------- datasets

def _periods_and_records(kind: str, invoices: list) -> tuple[list[str], list[dict]]:
    if kind == "ims":
        return sorted({r["return_period"] for r in invoices if r["return_period"]}), invoices
    if kind == "gstr2b":
        return sorted({i.return_period for i in invoices if i.return_period}), [i.to_dict() for i in invoices]
    return sorted({deadlines.period_of(i.invoice_date) for i in invoices}), [i.to_dict() for i in invoices]


def store_dataset(org_id: int, user_id: int | None, kind: str, source: str, invoices: list, cur=None) -> dict:
    """Store invoices (books / gstr2b) or IMS records (plain dicts) as one encrypted dataset."""
    if not invoices:
        raise ValueError("The file contained no invoices")
    periods, records = _periods_and_records(kind, invoices)
    _require_consent(org_id, kind)
    payload = crypto.encrypt_json(records, _ctx(org_id, "dataset"))

    def write(c):
        c.execute("INSERT INTO datasets (org_id, kind, source, periods, record_count, payload_enc) "
                  "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id", (org_id, kind, source, periods, len(records), payload))
        new_id = c.fetchone()["id"]
        audit(org_id, user_id, "dataset_imported", {"kind": kind, "source": source, "records": len(records)}, cur=c)
        return new_id

    if cur is not None:
        dataset_id = write(cur)
    else:
        with tx() as c:
            dataset_id = write(c)
    return {"dataset_id": dataset_id, "kind": kind, "source": source, "records": len(records), "periods": periods}


def replace_by_period(org_id: int, user_id: int | None, kind: str, source: str, invoices: list) -> list[dict]:
    """Store one dataset per period, replacing that source's earlier copy of the same period (used by syncs)."""
    by_period: dict[str, list] = {}
    for inv in invoices:
        by_period.setdefault(deadlines.period_of(inv.invoice_date), []).append(inv)
    stored = []
    with tx() as cur:  # all periods swap together, or none do
        for period, group in sorted(by_period.items()):
            cur.execute("DELETE FROM datasets WHERE org_id = %s AND kind = %s AND source = %s AND periods = %s",
                        (org_id, kind, source, [period]))
            stored.append(store_dataset(org_id, user_id, kind, source, group, cur=cur))
    return stored


def list_datasets(user: dict, org_id: int) -> list[dict]:
    require_member(user, org_id)
    rows = db.all("SELECT id, kind, source, periods, record_count, imported_at FROM datasets WHERE org_id = %s "
                  "ORDER BY id DESC", (org_id,))
    return [{"id": r["id"], "kind": r["kind"], "source": r["source"], "period": ",".join(r["periods"]),
             "record_count": r["record_count"], "imported_at": fmt(r["imported_at"])} for r in rows]


def delete_dataset(user: dict, org_id: int, dataset_id: int) -> None:
    require_member(user, org_id)
    with tx() as cur:
        cur.execute("DELETE FROM datasets WHERE id = %s AND org_id = %s", (dataset_id, org_id))
        audit(org_id, user["id"], "dataset_deleted", {"dataset_id": dataset_id}, cur=cur)


def _payloads(org_id: int, kind: str):
    for row in db.all("SELECT payload_enc FROM datasets WHERE org_id = %s AND kind = %s ORDER BY id", (org_id, kind)):
        yield from crypto.decrypt_json(row["payload_enc"], _ctx(org_id, "dataset"))


def load_invoices(org_id: int, kind: str) -> list[Invoice]:
    """Decrypt every dataset of a kind into memory; later imports replace earlier copies of the same invoice."""
    merged: dict[tuple, Invoice] = {}
    for record in _payloads(org_id, kind):
        inv = Invoice.from_dict(record)
        merged[(inv.return_period, inv.supplier_gstin, tax.normalize_invoice_no(inv.invoice_no))] = inv
    return list(merged.values())


def load_ims(org_id: int) -> list[dict]:
    """Latest IMS record per document: a newer IMS download supersedes older actions."""
    merged: dict[tuple, dict] = {}
    for record in _payloads(org_id, "ims"):
        merged[(record["section"], record["supplier_gstin"], tax.normalize_invoice_no(record["doc_no"]))] = record
    return list(merged.values())


# ---------------------------------------------------------------- engine runs

def available_periods(org_id: int) -> list[str]:
    rows = db.all("SELECT DISTINCT unnest(periods) AS p FROM datasets WHERE org_id = %s", (org_id,))
    return sorted({r["p"] for r in rows}, key=lambda p: (p[2:], p[:2]), reverse=True)


def run_reconciliation(org: dict, period: str | None = None, today: date | None = None) -> dict:
    today = today or date.today()
    books = load_invoices(org["id"], "books")
    portal = load_invoices(org["id"], "gstr2b")
    if period:
        books = [b for b in books if deadlines.period_of(b.invoice_date) == period]
        portal = [p for p in portal if p.return_period == period]
    ims = load_ims(org["id"])
    result = tax.reconcile(books, portal, org["state_code"], today=today, ims=ims)
    result["filing"] = deadlines.filing_status(today, org["state_code"], org.get("filing_frequency") or "monthly")
    result["period"] = period
    result["periods"] = available_periods(org["id"])
    result["has_books"] = bool(books)
    result["has_portal"] = bool(portal)
    result["has_ims"] = bool(ims)
    return result


def ims_recommendations(org: dict, period: str | None = None) -> dict:
    ims = load_ims(org["id"])
    recon = run_reconciliation(org, period)
    return {"has_ims": bool(ims), **ims_actions.recommend(recon, ims)}


def ims_upload(org: dict, ids: list[str], overrides: dict[str, str] | None = None, period: str | None = None) -> dict:
    """Build the IMS upload JSON for the chosen rows, using the suggested (or overridden) action."""
    ims = load_ims(org["id"])
    recs = ims_actions.recommend(run_reconciliation(org, period), ims)["rows"]
    chosen, overrides = set(ids), overrides or {}
    changes = []
    for rec, row in zip(ims, recs):
        if row["id"] not in chosen:
            continue
        action = overrides.get(row["id"], row["suggested"])
        if action not in ingest_ims.ACTIONS:
            raise ValueError(f"Unknown IMS action '{action}'")
        if action == "P" and rec["pending_blocked"]:
            raise ValueError(f"Pending is not allowed for {rec['doc_no']} from {rec['supplier_gstin']}")
        if action != rec["action"]:
            changes.append({**rec, "action": action, "remarks": row["reason"]})
    if not changes:
        raise ValueError("None of the selected documents change their IMS action")
    return ingest_ims.upload_json(org["gstin"], changes)


def profile_for(org: dict) -> policy.Profile:
    return policy.Profile(
        state_code=org["state_code"], sector=org.get("sector") or "manufacturing", category=org.get("category"),
        term_loan_amount=org.get("term_loan_amount"), zone=org.get("zone"),
        monthly_electricity_duty=org.get("monthly_electricity_duty"), monthly_power_units=org.get("monthly_power_units"),
        has_udyam=bool(org.get("udyam")))


def run_policy(org: dict) -> dict:
    assets = policy.detect_capital_assets(load_invoices(org["id"], "books"))
    return policy.match(profile_for(org), assets)


def org_summary(org: dict, today: date | None = None) -> dict:
    recon = run_reconciliation(org, today=today)
    subsidy = run_policy(org)
    return {
        "org": {k: org[k] for k in ("id", "name", "gstin", "state", "district", "category")},
        "filing": recon["filing"],
        "has_data": recon["has_books"] and recon["has_portal"],
        "tax": {k: recon["summary"][k] for k in ("invoices", "itc_at_risk", "itc_unclaimed_in_portal", "match_rate")}
        | {"by_status": recon["summary"]["by_status"]},
        "subsidy": {"eligible_schemes": sum(1 for s in subsidy["schemes"] if s["status"] == "eligible"),
                    "total_estimated_benefit": subsidy["total_estimated_benefit"]},
        "consents": sorted(active_consents(org["id"])),
    }


# ---------------------------------------------------------------- connector tokens

def create_connector_token(user: dict, org_id: int) -> str:
    require_member(user, org_id)
    token = "arc_" + crypto.new_token()
    with tx() as cur:
        cur.execute("INSERT INTO connector_tokens (token_hash, org_id, created_by) VALUES (%s, %s, %s)",
                    (crypto.token_hash(token), org_id, user["id"]))
        audit(org_id, user["id"], "connector_token_created", cur=cur)
    return token


def org_for_connector_token(token: str) -> int | None:
    row = db.one("UPDATE connector_tokens SET last_used_at = now() WHERE token_hash = %s RETURNING org_id",
                 (crypto.token_hash(token),))
    return row["org_id"] if row else None


def audit_trail(user: dict, org_id: int, limit: int = 50) -> list[dict]:
    require_member(user, org_id)
    rows = db.all("SELECT a.action, a.detail, a.at, u.name AS user FROM audit_log a LEFT JOIN users u "
                  "ON u.id = a.user_id WHERE a.org_id = %s ORDER BY a.id DESC LIMIT %s", (org_id, limit))
    return [{**r, "at": fmt(r["at"])} for r in rows]
