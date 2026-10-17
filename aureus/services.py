"""Application services: tenants, consent, encrypted datasets and engine runs.

Raw invoices never sit in plain fields. Each import is sealed as one
AES-256-GCM ciphertext bound to its organisation, stored in chunks, and only
decrypted in memory for the duration of a reconciliation or policy run.
"""

from datetime import date, timedelta

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from . import config, crypto, db, gstin as gstin_mod
from .db import fmt, get_db, next_id
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
CHUNK_CHARS = 4 * 1024 * 1024  # keeps every document far below MongoDB's 16 MB limit


class AccessDenied(Exception):
    pass


class ConsentRequired(Exception):
    pass


class EmailTaken(Exception):
    pass


def audit(org_id, user_id, action, detail=None):
    get_db().audit_log.insert_one({"_id": next_id("audit_log"), "org_id": org_id, "user_id": user_id,
                                   "action": action, "detail": detail, "at": db.now()})


# ---------------------------------------------------------------- users & sessions

def _user_out(doc) -> dict | None:
    if not doc:
        return None
    return {"id": doc["_id"], "email": doc["email"], "name": doc["name"], "role": doc["role"],
            "firm_name": doc.get("firm_name")}


def create_user(email: str, name: str, password: str, role: str, firm_name: str | None = None) -> dict:
    doc = {"email": email.lower().strip(), "name": name.strip(), "password_hash": crypto.hash_password(password),
           "role": role, "firm_name": firm_name, "created_at": db.now()}
    if get_db().users.find_one({"email": doc["email"]}, {"_id": 1}):
        raise EmailTaken(doc["email"])
    doc["_id"] = next_id("users")
    try:
        get_db().users.insert_one(doc)
    except DuplicateKeyError:  # lost a race with a concurrent signup
        raise EmailTaken(doc["email"])
    return _user_out(doc)


def get_user(user_id: int) -> dict | None:
    return _user_out(get_db().users.find_one({"_id": user_id}))


def login(email: str, password: str) -> tuple[str, dict] | None:
    doc = get_db().users.find_one({"email": email.lower().strip()})
    if not doc or not crypto.verify_password(password, doc["password_hash"]):
        return None
    token = crypto.new_token()
    get_db().sessions.insert_one({"_id": crypto.token_hash(token), "user_id": doc["_id"],
                                  "expires_at": db.now() + timedelta(hours=config.SESSION_TTL_HOURS)})
    return token, _user_out(doc)


def user_for_token(token: str) -> dict | None:
    # The TTL index deletes expired sessions within a minute; the date filter closes that gap.
    session = get_db().sessions.find_one({"_id": crypto.token_hash(token), "expires_at": {"$gt": db.now()}})
    return get_user(session["user_id"]) if session else None


def logout(token: str) -> None:
    get_db().sessions.delete_one({"_id": crypto.token_hash(token)})


# ---------------------------------------------------------------- organisations

def _ctx(org_id: int, what: str) -> str:
    return f"org:{org_id}:{what}"


ORG_FIELDS = ("name", "district", "address", "sector", "industry", "annual_turnover", "investment_pm",
              "term_loan_amount", "zone", "monthly_electricity_duty", "monthly_power_units", "filing_frequency")


def _org_out(doc) -> dict:
    org = {k: doc.get(k) for k in ORG_FIELDS}
    org.update(id=doc["_id"], state_code=doc["state_code"], gstin_last4=doc["gstin_last4"],
               created_by=doc.get("created_by"), created_at=fmt(doc.get("created_at")))
    org["sector"] = org["sector"] or "manufacturing"
    org["filing_frequency"] = org["filing_frequency"] or "monthly"
    org["gstin"] = crypto.decrypt_text(doc["gstin_enc"], _ctx(doc["_id"], "gstin"))
    org["udyam"] = crypto.decrypt_text(doc.get("udyam_enc"), _ctx(doc["_id"], "udyam"))
    org["state"] = gstin_mod.state_name(org["state_code"])
    org["category"] = policy.classify_enterprise(org["investment_pm"], org["annual_turnover"])
    ca = get_db().memberships.find_one({"org_id": doc["_id"], "role": "ca"}, sort=[("user_id", ASCENDING)])
    advisor = get_db().users.find_one({"_id": ca["user_id"]}) if ca else None
    org["advisor"] = {"name": advisor["name"], "firm_name": advisor.get("firm_name")} if advisor else None
    return org


def _org_changes(org_id: int, data: dict) -> dict:
    sets = {k: data[k] for k in ORG_FIELDS if k in data}
    if "udyam" in data:
        sets["udyam_enc"] = crypto.encrypt_text((data["udyam"] or "").strip().upper() or None, _ctx(org_id, "udyam"))
    return sets


def create_org(user: dict, data: dict) -> dict:
    gstin = data["gstin"].strip().upper()
    if not gstin_mod.is_valid(gstin):
        raise ValueError("GSTIN is not valid (format or checksum)")
    org_id = next_id("orgs")
    doc = {"_id": org_id, "gstin_enc": crypto.encrypt_text(gstin, _ctx(org_id, "gstin")), "gstin_last4": gstin[-4:],
           "state_code": gstin[:2], "created_by": user["id"], "created_at": db.now(),
           "sector": "manufacturing", "filing_frequency": "monthly", **_org_changes(org_id, data)}
    get_db().orgs.insert_one(doc)
    get_db().memberships.insert_one({"org_id": org_id, "user_id": user["id"],
                                     "role": "owner" if user["role"] == "msme" else "ca"})
    audit(org_id, user["id"], "org_created")
    return _org_out(doc)


def update_org(user: dict, org_id: int, data: dict) -> dict:
    require_member(user, org_id)
    changes = _org_changes(org_id, data)
    if changes:
        get_db().orgs.update_one({"_id": org_id}, {"$set": changes})
    audit(org_id, user["id"], "org_updated", {"fields": sorted(k for k in data if k in ORG_FIELDS or k == "udyam")})
    return get_org_by_id(org_id)


def require_member(user: dict, org_id: int) -> str:
    row = get_db().memberships.find_one({"org_id": org_id, "user_id": user["id"]})
    if not row:
        raise AccessDenied("You do not have access to this organisation")
    return row["role"]


def get_org(user: dict, org_id: int) -> dict:
    require_member(user, org_id)
    return get_org_by_id(org_id)


def get_org_by_id(org_id: int) -> dict:
    """Internal lookup without a membership check (connector tokens are already org-scoped)."""
    doc = get_db().orgs.find_one({"_id": org_id})
    if not doc:
        raise AccessDenied("You do not have access to this organisation")
    return _org_out(doc)


def list_orgs(user: dict) -> list[dict]:
    ids = [m["org_id"] for m in get_db().memberships.find({"user_id": user["id"]})]
    return [_org_out(doc) for doc in get_db().orgs.find({"_id": {"$in": ids}}).sort("name", ASCENDING)]


def add_member(user: dict, org_id: int, email: str) -> dict:
    require_member(user, org_id)
    other = get_db().users.find_one({"email": email.lower().strip()})
    if not other:
        raise ValueError("No AureusRecon account uses that email. Ask them to sign up first.")
    role = "owner" if other["role"] == "msme" else "ca"
    get_db().memberships.update_one({"org_id": org_id, "user_id": other["_id"]},
                                    {"$setOnInsert": {"role": role}}, upsert=True)
    audit(org_id, user["id"], "member_added", {"user_id": other["_id"], "role": role})
    return {"user_id": other["_id"], "name": other["name"], "role": role, "firm_name": other.get("firm_name")}


def list_members(user: dict, org_id: int) -> list[dict]:
    require_member(user, org_id)
    rows = list(get_db().memberships.find({"org_id": org_id}))
    users = {u["_id"]: u for u in get_db().users.find({"_id": {"$in": [r["user_id"] for r in rows]}})}
    members = [{"user_id": r["user_id"], "name": users[r["user_id"]]["name"], "email": users[r["user_id"]]["email"],
                "firm_name": users[r["user_id"]].get("firm_name"), "role": r["role"]}
               for r in rows if r["user_id"] in users]
    return sorted(members, key=lambda m: (m["role"] != "owner", m["name"]))


def _purge_org_data(org_id: int, kinds: list[str], providers: list[str], connector_tokens: bool) -> dict:
    d = get_db()
    ids = [ds["_id"] for ds in d.datasets.find({"org_id": org_id, "kind": {"$in": kinds}}, {"_id": 1})]
    d.dataset_chunks.delete_many({"dataset_id": {"$in": ids}})
    removed = d.datasets.delete_many({"_id": {"$in": ids}}).deleted_count
    integrations = d.integrations.delete_many({"org_id": org_id, "provider": {"$in": providers}}).deleted_count
    if connector_tokens:
        d.connector_tokens.delete_many({"org_id": org_id})
    return {"datasets": removed, "integrations": integrations}


def delete_org(user: dict, org_id: int) -> None:
    role = require_member(user, org_id)
    d = get_db()
    if role != "owner" and d.memberships.count_documents({"org_id": org_id, "role": "owner"}):
        raise AccessDenied("Only the business owner can delete this organisation")
    # Remove access first, so a half-finished delete never leaves data reachable.
    d.memberships.delete_many({"org_id": org_id})
    _purge_org_data(org_id, ["books", "gstr2b", "ims"], ["zoho"], connector_tokens=True)
    d.dataset_chunks.delete_many({"org_id": org_id})
    for name in ("consents", "integrations", "oauth_states"):
        d[name].delete_many({"org_id": org_id})
    d.orgs.delete_one({"_id": org_id})
    audit(None, user["id"], "org_deleted", {"org_id": org_id})


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
    d = get_db()
    d.consents.update_many({"org_id": org_id, "purpose": purpose, "revoked_at": None}, {"$set": {"revoked_at": granted}})
    consent_id = next_id("consents")
    d.consents.insert_one({"_id": consent_id, "org_id": org_id, "purpose": purpose, "granted_by": user["id"],
                           "granted_at": granted, "expires_at": expires, "revoked_at": None, "artefact": artefact})
    audit(org_id, user["id"], "consent_granted", {"purpose": purpose})
    return {**artefact, "consent_id": consent_id}


def active_consents(org_id: int) -> dict:
    rows = get_db().consents.find({"org_id": org_id, "revoked_at": None, "expires_at": {"$gt": db.now()}})
    return {r["purpose"]: {"consent_id": r["_id"], **r["artefact"]} for r in rows}


def revoke_consent(user: dict, org_id: int, purpose: str) -> dict:
    """Revoke and purge immediately. `purpose='all'` revokes everything."""
    require_member(user, org_id)
    purposes = list(PURPOSES) if purpose == "all" else [purpose]
    for p in purposes:
        if p not in PURPOSES:
            raise ValueError(f"Unknown purpose '{p}'")
    # Revoke first: imports check consent, so no new data can arrive while the purge runs.
    get_db().consents.update_many({"org_id": org_id, "purpose": {"$in": purposes}, "revoked_at": None},
                                  {"$set": {"revoked_at": db.now()}})
    purged = _purge_org_data(
        org_id,
        kinds=[k for p in purposes for k in PURPOSES[p]["dataset_kinds"]],
        providers=[prov for p in purposes for prov in PURPOSES[p]["integrations"]],
        connector_tokens="books_sync" in purposes)
    audit(org_id, user["id"], "consent_revoked", {"purposes": purposes, **purged})
    return {"revoked": purposes, **purged}


def _require_consent(org_id: int, kind: str):
    purpose = next(p for p, spec in PURPOSES.items() if kind in spec["dataset_kinds"])
    if purpose not in active_consents(org_id):
        raise ConsentRequired(f"Consent for '{PURPOSES[purpose]['label']}' is needed before importing this data")


# ---------------------------------------------------------------- datasets

def store_dataset(org_id: int, user_id: int | None, kind: str, source: str, invoices: list) -> dict:
    """Store invoices (books / gstr2b) or IMS records (plain dicts) as one encrypted dataset."""
    if not invoices:
        raise ValueError("The file contained no invoices")
    if kind == "ims":
        periods = sorted({r["return_period"] for r in invoices if r["return_period"]})
        records = invoices
    elif kind == "gstr2b":
        periods = sorted({i.return_period for i in invoices if i.return_period})
        records = [i.to_dict() for i in invoices]
    else:
        periods = sorted({deadlines.period_of(i.invoice_date) for i in invoices})
        records = [i.to_dict() for i in invoices]
    _require_consent(org_id, kind)
    payload = crypto.encrypt_json(records, _ctx(org_id, "dataset"))
    chunks = [payload[i:i + CHUNK_CHARS] for i in range(0, len(payload), CHUNK_CHARS)]
    dataset_id = next_id("datasets")
    d = get_db()
    # Chunks first, metadata last: a dataset only becomes visible once it is complete.
    d.dataset_chunks.insert_many([{"dataset_id": dataset_id, "org_id": org_id, "n": n, "data": part}
                                  for n, part in enumerate(chunks)])
    d.datasets.insert_one({"_id": dataset_id, "org_id": org_id, "kind": kind, "source": source,
                           "periods": periods, "record_count": len(invoices), "chunks": len(chunks),
                           "imported_at": db.now()})
    audit(org_id, user_id, "dataset_imported", {"kind": kind, "source": source, "records": len(invoices)})
    return {"dataset_id": dataset_id, "kind": kind, "source": source, "records": len(invoices), "periods": periods}


def list_datasets(user: dict, org_id: int) -> list[dict]:
    require_member(user, org_id)
    rows = get_db().datasets.find({"org_id": org_id}).sort("_id", DESCENDING)
    return [{"id": r["_id"], "kind": r["kind"], "source": r["source"], "period": ",".join(r["periods"]),
             "record_count": r["record_count"], "imported_at": fmt(r["imported_at"])} for r in rows]


def delete_dataset(user: dict, org_id: int, dataset_id: int) -> None:
    require_member(user, org_id)
    d = get_db()
    if d.datasets.delete_one({"_id": dataset_id, "org_id": org_id}).deleted_count:
        d.dataset_chunks.delete_many({"dataset_id": dataset_id})
    audit(org_id, user["id"], "dataset_deleted", {"dataset_id": dataset_id})


def _read_payload(org_id: int, dataset: dict) -> list[dict]:
    parts = get_db().dataset_chunks.find({"dataset_id": dataset["_id"]}).sort("n", ASCENDING)
    return crypto.decrypt_json("".join(p["data"] for p in parts), _ctx(org_id, "dataset"))


def load_invoices(org_id: int, kind: str) -> list[Invoice]:
    """Decrypt every dataset of a kind into memory; later imports replace earlier copies of the same invoice."""
    merged: dict[tuple, Invoice] = {}
    for dataset in get_db().datasets.find({"org_id": org_id, "kind": kind}).sort("_id", ASCENDING):
        for record in _read_payload(org_id, dataset):
            inv = Invoice.from_dict(record)
            key = (inv.return_period, inv.supplier_gstin, tax.normalize_invoice_no(inv.invoice_no))
            merged[key] = inv
    return list(merged.values())


def load_ims(org_id: int) -> list[dict]:
    """Latest IMS record per document: a newer IMS download supersedes older actions."""
    merged: dict[tuple, dict] = {}
    for dataset in get_db().datasets.find({"org_id": org_id, "kind": "ims"}).sort("_id", ASCENDING):
        for record in _read_payload(org_id, dataset):
            key = (record["section"], record["supplier_gstin"], tax.normalize_invoice_no(record["doc_no"]))
            merged[key] = record
    return list(merged.values())


# ---------------------------------------------------------------- engine runs

def available_periods(org_id: int) -> list[str]:
    periods = set(get_db().datasets.distinct("periods", {"org_id": org_id}))
    return sorted(periods, key=lambda p: (p[2:], p[:2]), reverse=True)


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
    get_db().connector_tokens.insert_one({"_id": crypto.token_hash(token), "org_id": org_id, "created_by": user["id"],
                                          "created_at": db.now(), "last_used_at": None})
    audit(org_id, user["id"], "connector_token_created")
    return token


def org_for_connector_token(token: str) -> int | None:
    row = get_db().connector_tokens.find_one_and_update({"_id": crypto.token_hash(token)},
                                                        {"$set": {"last_used_at": db.now()}})
    return row["org_id"] if row else None


def audit_trail(user: dict, org_id: int, limit: int = 50) -> list[dict]:
    require_member(user, org_id)
    rows = list(get_db().audit_log.find({"org_id": org_id}).sort("_id", DESCENDING).limit(limit))
    names = {u["_id"]: u["name"] for u in get_db().users.find({"_id": {"$in": [r["user_id"] for r in rows]}})}
    return [{"action": r["action"], "detail": r.get("detail"), "at": fmt(r["at"]), "user": names.get(r["user_id"])}
            for r in rows]
