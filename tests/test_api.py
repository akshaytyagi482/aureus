import json

import pytest

from aureus import db, sample, services
from aureus.outputs import notice

from .conftest import signup


def test_signup_login_and_auth_required(client):
    signup(client, "a@example.com")
    assert client.post("/api/auth/login", json={"email": "a@example.com", "password": "wrong-pass"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "a@example.com", "password": "secret-pass"}).status_code == 200
    assert client.get("/api/orgs").status_code == 401
    assert client.post("/api/auth/signup", json={"email": "a@example.com", "name": "x", "password": "secret-pass",
                                                 "role": "msme"}).status_code == 409


def test_org_rejects_bad_gstin(client, owner):
    res = client.post("/api/orgs", headers=owner, json={"name": "X", "gstin": "27AAPFU0939F1ZX"})
    assert res.status_code == 400


def test_import_requires_consent(client, owner):
    org = client.post("/api/orgs", headers=owner, json={"name": "X", "gstin": "27AAPFU0939F1ZV"}).json()
    res = client.post(f"/api/orgs/{org['id']}/import/books", headers=owner, data={"source": "tally"},
                      files={"file": ("t.xml", sample.tally_xml())})
    assert res.status_code == 403 and res.json()["code"] == "consent_required"


def test_full_flow(client, owner, demo_org):
    res = client.post(f"/api/orgs/{demo_org}/import/books", headers=owner, data={"source": "tally"},
                      files={"file": ("t.xml", sample.tally_xml())})
    assert res.status_code == 200 and res.json()["records"] == 21
    res = client.post(f"/api/orgs/{demo_org}/import/gstr2b", headers=owner, files={"file": ("2b.json", sample.gstr2b_json())})
    assert res.status_code == 200, res.text

    recon = client.get(f"/api/orgs/{demo_org}/reconciliation", headers=owner, params={"period": "092026"}).json()
    assert recon["summary"]["by_status"]["MISSING_IN_PORTAL"]["count"] == 3
    assert recon["periods"] == ["092026"]

    steel = sample.S["steel"][1]
    reminder = client.get(f"/api/orgs/{demo_org}/suppliers/{steel}/reminder", headers=owner).json()
    assert "BST/2026/1187" in reminder["body"] and reminder["invoices"] == 2

    draft = client.post(f"/api/orgs/{demo_org}/notice-copilot", headers=owner,
                        json={"notice_type": "drc01", "notice_ref": "ZD2710260001"}).json()
    assert "Section 75(4)" in draft["text"] and "BST/2026/1187" in draft["text"]
    allowed = {c["cite"] for group in notice.CITATIONS.values() for c in group}
    assert {c["cite"] for c in draft["citations"]} <= allowed

    subsidies = client.get(f"/api/orgs/{demo_org}/subsidies", headers=owner).json()
    assert subsidies["category"] == "small"
    ids = [s["scheme_id"] for s in subsidies["schemes"]]
    assert "mh-miisp-2025-ips" in ids

    packet = client.get(f"/api/orgs/{demo_org}/subsidies/mh-miisp-2025-ips/application", headers=owner).json()
    assert packet["ready_to_file"] and len(packet["annexure"]) == 5
    assert "31.12.2025" in packet["scheme"]["source_document"]
    html = client.get(f"/api/orgs/{demo_org}/subsidies/mh-miisp-2025-ips/application.html", headers=owner)
    assert html.status_code == 200 and "CNC turning centre" in html.text


def test_gstr2b_for_another_gstin_is_rejected(client, owner):
    org = client.post("/api/orgs", headers=owner, json={"name": "X", "gstin": "27AAPFU0939F1ZV"}).json()
    client.post(f"/api/orgs/{org['id']}/consent", headers=owner, json={"purpose": "gstr2b_feed"})
    res = client.post(f"/api/orgs/{org['id']}/import/gstr2b", headers=owner, files={"file": ("2b.json", sample.gstr2b_json())})
    assert res.status_code == 400 and "belongs to GSTIN" in res.json()["detail"]


def _dump_database() -> str:
    return json.dumps({t: db.all(f"SELECT * FROM {t}") for t in db.TABLES}, default=str)


def test_data_is_encrypted_at_rest(client, owner, demo_org):
    client.post(f"/api/orgs/{demo_org}/import/sample", headers=owner)
    dump = _dump_database()
    assert sample.BUYER["gstin"] not in dump
    assert "BST/2026/1187" not in dump and "Bharat Steel" not in dump
    assert sample.BUYER["udyam"] not in dump
    assert owner["Authorization"].split()[1] not in dump


def test_revoke_consent_purges_immediately(client, owner, demo_org):
    client.post(f"/api/orgs/{demo_org}/import/sample", headers=owner)
    res = client.post(f"/api/orgs/{demo_org}/consent/revoke", headers=owner, json={"purpose": "all"}).json()
    assert res["datasets"] == 3  # books, GSTR-2B and IMS
    org = client.get(f"/api/orgs/{demo_org}", headers=owner).json()
    assert org["datasets"] == [] and org["consents"] == {}
    recon = client.get(f"/api/orgs/{demo_org}/reconciliation", headers=owner).json()
    assert recon["results"] == []
    assert client.post(f"/api/orgs/{demo_org}/import/sample", headers=owner).status_code == 403


def test_tenant_isolation_and_ca_console(client, owner, demo_org):
    stranger = signup(client, "other@example.com")
    assert client.get(f"/api/orgs/{demo_org}", headers=stranger).status_code == 403
    assert client.get(f"/api/orgs/{demo_org}/reconciliation", headers=stranger).status_code == 403

    ca = signup(client, "ca@firm.in", role="ca", firm_name="Mehta & Associates")
    assert client.post(f"/api/orgs/{demo_org}/members", headers=owner, json={"email": "ca@firm.in"}).json()["role"] == "ca"
    client.post(f"/api/orgs/{demo_org}/import/sample", headers=owner)
    client.post("/api/orgs", headers=ca, json={"name": "Second Client", "gstin": "27AAPFU0939F1ZV"})
    console = client.get("/api/console", headers=ca).json()
    assert console["firm_name"] == "Mehta & Associates"
    assert console["totals"]["clients"] == 2 and console["totals"]["awaiting_data"] == 1
    assert console["clients"][0]["tax"]["itc_at_risk"] > 0
    org = client.get(f"/api/orgs/{demo_org}", headers=owner).json()
    assert org["advisor"]["firm_name"] == "Mehta & Associates"
    # The CA cannot delete a client that has an owner.
    assert client.delete(f"/api/orgs/{demo_org}", headers=ca).status_code == 403


def test_connector_token_import(client, owner, demo_org):
    token = client.post(f"/api/orgs/{demo_org}/connector-token", headers=owner).json()["token"]
    res = client.post("/api/connector/import/books", headers={"X-Connector-Token": token}, data={"source": "tally"},
                      files={"file": ("t.xml", sample.tally_xml())})
    assert res.status_code == 200 and res.json()["records"] == 21
    assert client.post("/api/connector/import/books", headers={"X-Connector-Token": "nope"}, data={"source": "tally"},
                       files={"file": ("t.xml", sample.tally_xml())}).status_code == 401


def test_public_endpoints(client):
    res = client.post("/api/public/subsidy-calculator", json={"state_code": "27", "category": "micro", "zone": "C",
                                                             "investments": {"plant_machinery": 5000000}})
    assert res.status_code == 200 and res.json()["total_estimated_benefit"] > 0
    assert client.get("/api/public/gstin/27AAPFU0939F1ZV").json()["valid"]
    meta = client.get("/api/public/meta").json()
    assert meta["states"]
    gj, jk = meta["zones_by_state"]["24"], meta["zones_by_state"]["01"]
    gj_zones = [z for places in gj["places"].values() for z in places.values()]
    assert (gj_zones.count("A"), gj_zones.count("B")) == (130, 138)  # the Oct 2026 classification's counts
    assert gj["places"]["Ahmedabad"]["Sanand"] == "B" and gj["places"]["The Dangs"]["Aahva"] == "A"
    assert jk["places_kind"] == "block" and jk["places"]["Srinagar"]["Khonmoh"] == "B"  # moved by the 2024 amendment
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/").status_code == 200


def test_zoho_not_configured(client, owner, demo_org):
    assert client.get(f"/api/orgs/{demo_org}/integrations/zoho/connect", headers=owner).status_code == 501


def test_polish_falls_back_without_credentials(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    draft = {"text": "x", "citations": []}
    out = notice.polish_with_claude(draft)
    assert out["text"] == "x" and "polish_error" in out


def test_delete_org_removes_everything(client, owner, demo_org):
    client.post(f"/api/orgs/{demo_org}/import/sample", headers=owner)
    client.post(f"/api/orgs/{demo_org}/connector-token", headers=owner)
    assert client.delete(f"/api/orgs/{demo_org}", headers=owner).status_code == 200
    for table in ("memberships", "consents", "datasets", "connector_tokens", "integrations", "notifications"):
        assert db.one(f"SELECT COUNT(*) AS n FROM {table} WHERE org_id = %s", (demo_org,))["n"] == 0, table
    assert db.one("SELECT COUNT(*) AS n FROM orgs WHERE id = %s", (demo_org,))["n"] == 0


def test_revoke_is_atomic(client, owner, demo_org, monkeypatch):
    """If the purge fails half-way, consent must not end up revoked with data still present (or vice versa)."""
    client.post(f"/api/orgs/{demo_org}/import/sample", headers=owner)

    def boom(*args, **kwargs):
        raise RuntimeError("audit write failed")
    monkeypatch.setattr(services, "audit", boom)
    user = services.user_for_token(owner["Authorization"].split()[1])
    with pytest.raises(RuntimeError):
        services.revoke_consent(user, demo_org, "all")
    assert db.one("SELECT COUNT(*) AS n FROM datasets WHERE org_id = %s", (demo_org,))["n"] == 3
    assert len(services.active_consents(demo_org)) == 2


def test_migrations_are_idempotent():
    db.init()
    db.init()
    assert db.one("SELECT MAX(version) AS v FROM schema_migrations")["v"] == len(db.MIGRATIONS)



def test_ims_flow(client, owner, demo_org):
    client.post(f"/api/orgs/{demo_org}/import/sample", headers=owner)
    recon = client.get(f"/api/orgs/{demo_org}/reconciliation", headers=owner).json()
    assert recon["has_ims"] and recon["summary"]["by_reason"]["PENDING_IN_IMS"] == 1
    recs = client.get(f"/api/orgs/{demo_org}/ims", headers=owner).json()
    high = [r["id"] for r in recs["rows"] if r["priority"] == "high"]
    res = client.post(f"/api/orgs/{demo_org}/ims/upload-file", headers=owner, json={"ids": high})
    assert res.status_code == 200
    body = res.json()
    assert body["reqtyp"] == "SAVE" and body["rtin"] == sample.BUYER["gstin"]
    assert len(body["invdata"]["b2b"]) == len(high)
    # Nothing changes for an unchanged row
    same = [r["id"] for r in recs["rows"] if not r["changes"]]
    assert client.post(f"/api/orgs/{demo_org}/ims/upload-file", headers=owner, json={"ids": same}).status_code == 400


def test_ims_for_another_gstin_is_rejected(client, owner):
    org = client.post("/api/orgs", headers=owner, json={"name": "X", "gstin": "27AAPFU0939F1ZV"}).json()
    client.post(f"/api/orgs/{org['id']}/consent", headers=owner, json={"purpose": "gstr2b_feed"})
    res = client.post(f"/api/orgs/{org['id']}/import/ims", headers=owner, files={"file": ("ims.json", sample.ims_json())})
    assert res.status_code == 400 and "IMS file belongs to GSTIN" in res.json()["detail"]
