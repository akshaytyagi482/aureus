"""Application services: tenants, consent, encrypted datasets and engine runs.

Raw invoices never sit in plain columns. Each import is sealed as one
AES-256-GCM blob bound to its organisation, and is only decrypted in memory
for the duration of a reconciliation or policy run.
"""

import json
from datetime import date, datetime, timedelta, timezone

from . import config, crypto, db, gstin as gstin_mod
from .engines import deadlines, policy, tax
from .models import Invoice

PURPOSES = {
    "books_sync": {
        "label": "Read purchase records from accounting software",
        "data": ["Purchase invoices", "Supplier names and GSTINs", "Item descriptions and HSN codes"],
        "dataset_kind": "books",
        "integrations": ["zoho"],
    },
    "gstr2b_feed": {
        "label": "Read GSTR-2B statements from the GST system",
        "data": ["GSTR-2B invoice details uploaded by suppliers"],
        "dataset_kind": "gstr2b",
        "integrations": [],
    },
}
CONSENT_DAYS = 365


class AccessDenied(Exception):
    pass


class ConsentRequired(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def audit(conn, org_id, user_id, action, detail=None):
    conn.execute("INSERT INTO audit_log (org_id, user_id, action, detail) VALUES (?, ?, ?, ?)",
                 (org_id, user_id, action, json.dumps(detail) if detail else None))


# ---------------------------------------------------------------- users & sessions

def create_user(email: str, name: str, password: str, role: str, firm_name: str | None = None) -> dict:
    with db.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO users (email, name, password_hash, role, firm_name) VALUES (?, ?, ?, ?, ?)",
            (email.lower().strip(), name.strip(), crypto.hash_password(password), role, firm_name))
        return get_user(cur.lastrowid, conn)


def get_user(user_id: int, conn=None) -> dict:
    own = conn is None
    conn = conn or db.connect()
    try:
        row = conn.execute("SELECT id, email, name, role, firm_name FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        if own:
            conn.close()


def login(email: str, password: str) -> tuple[str, dict] | None:
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email.lower().strip(),)).fetchone()
        if not row or not crypto.verify_password(password, row["password_hash"]):
            return None
        token = crypto.new_token()
        expires = (datetime.now(timezone.utc) + timedelta(hours=config.SESSION_TTL_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (_now(),))
        conn.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                     (crypto.token_hash(token), row["id"], expires))
        return token, get_user(row["id"], conn)


def user_for_token(token: str) -> dict | None:
    with db.transaction() as conn:
        row = conn.execute("SELECT user_id FROM sessions WHERE token_hash = ? AND expires_at > ?",
                           (crypto.token_hash(token), _now())).fetchone()
        return get_user(row["user_id"], conn) if row else None


def logout(token: str) -> None:
    with db.transaction() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (crypto.token_hash(token),))


# ---------------------------------------------------------------- organisations

def _ctx(org_id: int, what: str) -> str:
    return f"org:{org_id}:{what}"


def _org_from_row(row, conn) -> dict:
    org = dict(row)
    org["gstin"] = crypto.decrypt_text(org.pop("gstin_enc"), _ctx(org["id"], "gstin"))
    org["udyam"] = crypto.decrypt_text(org.pop("udyam_enc"), _ctx(org["id"], "udyam"))
    org["state"] = gstin_mod.state_name(org["state_code"])
    org["category"] = policy.classify_enterprise(org["investment_pm"], org["annual_turnover"])
    advisor = conn.execute(
        "SELECT u.name, u.firm_name FROM memberships m JOIN users u ON u.id = m.user_id "
        "WHERE m.org_id = ? AND m.role = 'ca' ORDER BY u.id LIMIT 1", (org["id"],)).fetchone()
    org["advisor"] = dict(advisor) if advisor else None
    return org


ORG_FIELDS = ("name", "district", "address", "sector", "industry", "annual_turnover", "investment_pm",
              "term_loan_amount", "zone", "monthly_electricity_duty", "monthly_power_units", "filing_frequency")


def create_org(user: dict, data: dict) -> dict:
    gstin = data["gstin"].strip().upper()
    if not gstin_mod.is_valid(gstin):
        raise ValueError("GSTIN is not valid (format or checksum)")
    with db.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO orgs (name, gstin_enc, gstin_last4, state_code, created_by) VALUES (?, '', ?, ?, ?)",
            (data["name"], gstin[-4:], gstin[:2], user["id"]))
        org_id = cur.lastrowid
        conn.execute("UPDATE orgs SET gstin_enc = ? WHERE id = ?", (crypto.encrypt_text(gstin, _ctx(org_id, "gstin")), org_id))
        conn.execute("INSERT INTO memberships (org_id, user_id, role) VALUES (?, ?, ?)",
                     (org_id, user["id"], "owner" if user["role"] == "msme" else "ca"))
        _update_org(conn, org_id, data)
        audit(conn, org_id, user["id"], "org_created")
        return _org_from_row(conn.execute("SELECT * FROM orgs WHERE id = ?", (org_id,)).fetchone(), conn)


def _update_org(conn, org_id: int, data: dict):
    sets = {k: data[k] for k in ORG_FIELDS if k in data}
    if "udyam" in data:
        sets["udyam_enc"] = crypto.encrypt_text((data["udyam"] or "").strip().upper() or None, _ctx(org_id, "udyam"))
    if sets:
        conn.execute(f"UPDATE orgs SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), org_id))


def update_org(user: dict, org_id: int, data: dict) -> dict:
    require_member(user, org_id)
    with db.transaction() as conn:
        _update_org(conn, org_id, data)
        audit(conn, org_id, user["id"], "org_updated", {"fields": sorted(k for k in data if k in ORG_FIELDS or k == "udyam")})
        return _org_from_row(conn.execute("SELECT * FROM orgs WHERE id = ?", (org_id,)).fetchone(), conn)


def require_member(user: dict, org_id: int) -> str:
    with db.transaction() as conn:
        row = conn.execute("SELECT role FROM memberships WHERE org_id = ? AND user_id = ?", (org_id, user["id"])).fetchone()
    if not row:
        raise AccessDenied("You do not have access to this organisation")
    return row["role"]


def get_org(user: dict, org_id: int) -> dict:
    require_member(user, org_id)
    with db.transaction() as conn:
        return _org_from_row(conn.execute("SELECT * FROM orgs WHERE id = ?", (org_id,)).fetchone(), conn)


def get_org_by_id(org_id: int) -> dict:
    """Internal lookup without a membership check (connector tokens are already org-scoped)."""
    with db.transaction() as conn:
        return _org_from_row(conn.execute("SELECT * FROM orgs WHERE id = ?", (org_id,)).fetchone(), conn)


def list_orgs(user: dict) -> list[dict]:
    with db.transaction() as conn:
        rows = conn.execute("SELECT o.* FROM orgs o JOIN memberships m ON m.org_id = o.id WHERE m.user_id = ? "
                            "ORDER BY o.name", (user["id"],)).fetchall()
        return [_org_from_row(r, conn) for r in rows]


def add_member(user: dict, org_id: int, email: str) -> dict:
    require_member(user, org_id)
    with db.transaction() as conn:
        other = conn.execute("SELECT id, name, role, firm_name FROM users WHERE email = ?", (email.lower().strip(),)).fetchone()
        if not other:
            raise ValueError("No AureusRecon account uses that email. Ask them to sign up first.")
        role = "owner" if other["role"] == "msme" else "ca"
        conn.execute("INSERT OR IGNORE INTO memberships (org_id, user_id, role) VALUES (?, ?, ?)", (org_id, other["id"], role))
        audit(conn, org_id, user["id"], "member_added", {"user_id": other["id"], "role": role})
        return {"user_id": other["id"], "name": other["name"], "role": role, "firm_name": other["firm_name"]}


def list_members(user: dict, org_id: int) -> list[dict]:
    require_member(user, org_id)
    with db.transaction() as conn:
        rows = conn.execute("SELECT u.id AS user_id, u.name, u.email, u.firm_name, m.role FROM memberships m "
                            "JOIN users u ON u.id = m.user_id WHERE m.org_id = ? ORDER BY m.role DESC, u.name", (org_id,))
        return [dict(r) for r in rows]


def delete_org(user: dict, org_id: int) -> None:
    role = require_member(user, org_id)
    with db.transaction() as conn:
        owners = conn.execute("SELECT COUNT(*) FROM memberships WHERE org_id = ? AND role = 'owner'", (org_id,)).fetchone()[0]
        if role != "owner" and owners:
            raise AccessDenied("Only the business owner can delete this organisation")
        conn.execute("DELETE FROM orgs WHERE id = ?", (org_id,))
        audit(conn, None, user["id"], "org_deleted", {"org_id": org_id})


# ---------------------------------------------------------------- consent

def grant_consent(user: dict, org_id: int, purpose: str) -> dict:
    if purpose not in PURPOSES:
        raise ValueError(f"Unknown purpose '{purpose}'")
    require_member(user, org_id)
    granted = datetime.now(timezone.utc)
    artefact = {
        "version": "1.0",
        "data_fiduciary": "AureusRecon",
        "purpose": purpose,
        "purpose_text": PURPOSES[purpose]["label"],
        "data_categories": PURPOSES[purpose]["data"],
        "granted_by": user["email"],
        "granted_at": granted.isoformat(timespec="seconds"),
        "expires_at": (granted + timedelta(days=CONSENT_DAYS)).isoformat(timespec="seconds"),
        "storage": "Encrypted at rest (AES-256-GCM); portal and bank credentials are never collected",
        "revocation": "Revoke anytime; all data collected under this consent is deleted immediately",
    }
    with db.transaction() as conn:
        conn.execute("UPDATE consents SET revoked_at = ? WHERE org_id = ? AND purpose = ? AND revoked_at IS NULL",
                     (_now(), org_id, purpose))
        cur = conn.execute("INSERT INTO consents (org_id, purpose, granted_by, expires_at, artefact) VALUES (?, ?, ?, ?, ?)",
                           (org_id, purpose, user["id"], artefact["expires_at"], json.dumps(artefact)))
        artefact["consent_id"] = cur.lastrowid
        audit(conn, org_id, user["id"], "consent_granted", {"purpose": purpose})
    return artefact


def active_consents(org_id: int, conn=None) -> dict:
    own = conn is None
    conn = conn or db.connect()
    try:
        rows = conn.execute("SELECT id, purpose, granted_at, expires_at, artefact FROM consents "
                            "WHERE org_id = ? AND revoked_at IS NULL AND expires_at > ?",
                            (org_id, datetime.now(timezone.utc).isoformat(timespec="seconds"))).fetchall()
        return {r["purpose"]: {"consent_id": r["id"], **json.loads(r["artefact"])} for r in rows}
    finally:
        if own:
            conn.close()


def revoke_consent(user: dict, org_id: int, purpose: str) -> dict:
    """Revoke and purge immediately. `purpose='all'` revokes everything."""
    require_member(user, org_id)
    purposes = list(PURPOSES) if purpose == "all" else [purpose]
    purged = {"datasets": 0, "integrations": 0}
    with db.transaction() as conn:
        for p in purposes:
            spec = PURPOSES.get(p)
            if spec is None:
                raise ValueError(f"Unknown purpose '{p}'")
            conn.execute("UPDATE consents SET revoked_at = ? WHERE org_id = ? AND purpose = ? AND revoked_at IS NULL",
                         (_now(), org_id, p))
            purged["datasets"] += conn.execute("DELETE FROM datasets WHERE org_id = ? AND kind = ?",
                                               (org_id, spec["dataset_kind"])).rowcount
            for provider in spec["integrations"]:
                purged["integrations"] += conn.execute("DELETE FROM integrations WHERE org_id = ? AND provider = ?",
                                                       (org_id, provider)).rowcount
            if p == "books_sync":
                conn.execute("DELETE FROM connector_tokens WHERE org_id = ?", (org_id,))
        audit(conn, org_id, user["id"], "consent_revoked", {"purposes": purposes, **purged})
    return {"revoked": purposes, **purged}


def _require_consent(conn, org_id: int, kind: str):
    purpose = next(p for p, spec in PURPOSES.items() if spec["dataset_kind"] == kind)
    if purpose not in active_consents(org_id, conn):
        raise ConsentRequired(f"Consent for '{PURPOSES[purpose]['label']}' is needed before importing this data")


# ---------------------------------------------------------------- datasets

def store_dataset(org_id: int, user_id: int | None, kind: str, source: str, invoices: list[Invoice]) -> dict:
    if not invoices:
        raise ValueError("The file contained no invoices")
    periods = sorted({i.return_period for i in invoices if i.return_period}) if kind == "gstr2b" else \
        sorted({deadlines.period_of(i.invoice_date) for i in invoices})
    with db.transaction() as conn:
        _require_consent(conn, org_id, kind)
        payload = crypto.encrypt_json([i.to_dict() for i in invoices], _ctx(org_id, "dataset"))
        cur = conn.execute("INSERT INTO datasets (org_id, kind, source, period, record_count, payload_enc) "
                           "VALUES (?, ?, ?, ?, ?, ?)", (org_id, kind, source, ",".join(periods), len(invoices), payload))
        audit(conn, org_id, user_id, "dataset_imported", {"kind": kind, "source": source, "records": len(invoices)})
        return {"dataset_id": cur.lastrowid, "kind": kind, "source": source, "records": len(invoices), "periods": periods}


def list_datasets(user: dict, org_id: int) -> list[dict]:
    require_member(user, org_id)
    with db.transaction() as conn:
        rows = conn.execute("SELECT id, kind, source, period, record_count, imported_at FROM datasets "
                            "WHERE org_id = ? ORDER BY id DESC", (org_id,)).fetchall()
        return [dict(r) for r in rows]


def delete_dataset(user: dict, org_id: int, dataset_id: int) -> None:
    require_member(user, org_id)
    with db.transaction() as conn:
        conn.execute("DELETE FROM datasets WHERE id = ? AND org_id = ?", (dataset_id, org_id))
        audit(conn, org_id, user["id"], "dataset_deleted", {"dataset_id": dataset_id})


def load_invoices(org_id: int, kind: str) -> list[Invoice]:
    """Decrypt every dataset of a kind into memory; later imports replace earlier copies of the same invoice."""
    with db.transaction() as conn:
        rows = conn.execute("SELECT payload_enc FROM datasets WHERE org_id = ? AND kind = ? ORDER BY id",
                            (org_id, kind)).fetchall()
    merged: dict[tuple, Invoice] = {}
    for row in rows:
        for record in crypto.decrypt_json(row["payload_enc"], _ctx(org_id, "dataset")):
            inv = Invoice.from_dict(record)
            key = (inv.return_period, inv.supplier_gstin, tax.normalize_invoice_no(inv.invoice_no))
            merged[key] = inv
    return list(merged.values())


# ---------------------------------------------------------------- engine runs

def available_periods(org_id: int) -> list[str]:
    with db.transaction() as conn:
        rows = conn.execute("SELECT period FROM datasets WHERE org_id = ?", (org_id,)).fetchall()
    periods = {p for r in rows for p in (r["period"] or "").split(",") if p}
    return sorted(periods, key=lambda p: (p[2:], p[:2]), reverse=True)


def run_reconciliation(org: dict, period: str | None = None, today: date | None = None) -> dict:
    today = today or date.today()
    books = load_invoices(org["id"], "books")
    portal = load_invoices(org["id"], "gstr2b")
    if period:
        books = [b for b in books if deadlines.period_of(b.invoice_date) == period]
        portal = [p for p in portal if p.return_period == period]
    result = tax.reconcile(books, portal, org["state_code"], today=today)
    result["filing"] = deadlines.filing_status(today, org["state_code"], org.get("filing_frequency") or "monthly")
    result["period"] = period
    result["periods"] = available_periods(org["id"])
    result["has_books"] = bool(books)
    result["has_portal"] = bool(portal)
    return result


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
    with db.transaction() as conn:
        conn.execute("INSERT INTO connector_tokens (token_hash, org_id, created_by) VALUES (?, ?, ?)",
                     (crypto.token_hash(token), org_id, user["id"]))
        audit(conn, org_id, user["id"], "connector_token_created")
    return token


def org_for_connector_token(token: str) -> int | None:
    with db.transaction() as conn:
        row = conn.execute("SELECT org_id FROM connector_tokens WHERE token_hash = ?", (crypto.token_hash(token),)).fetchone()
        if row:
            conn.execute("UPDATE connector_tokens SET last_used_at = ? WHERE token_hash = ?", (_now(), crypto.token_hash(token)))
        return row["org_id"] if row else None


def audit_trail(user: dict, org_id: int, limit: int = 50) -> list[dict]:
    require_member(user, org_id)
    with db.transaction() as conn:
        rows = conn.execute("SELECT a.action, a.detail, a.at, u.name AS user FROM audit_log a LEFT JOIN users u "
                            "ON u.id = a.user_id WHERE a.org_id = ? ORDER BY a.id DESC LIMIT ?", (org_id, limit)).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"]) if r["detail"] else None} for r in rows]
