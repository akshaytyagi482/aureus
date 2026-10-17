import base64
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient
from pymongo.errors import PyMongoError

os.environ.setdefault("AUREUS_MASTER_KEY", base64.b64encode(b"k" * 32).decode())

from aureus import config, crypto, db  # noqa: E402
from aureus.api import app  # noqa: E402
from aureus import sample  # noqa: E402


def _mongo_client():
    """A real MongoDB if one answers (CI, local dev), otherwise an in-memory mongomock."""
    uri = os.environ.get("AUREUS_TEST_MONGODB_URI", "mongodb://127.0.0.1:27017")
    try:
        client = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=1500)
        client.admin.command("ping")
        return client, True
    except PyMongoError:
        import mongomock
        return mongomock.MongoClient(tz_aware=True), False


_CLIENT, REAL_MONGO = _mongo_client()


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    name = f"aureus_test_{uuid.uuid4().hex[:12]}"
    monkeypatch.setattr(config, "MONGODB_DB", name)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    db.set_client(_CLIENT)
    crypto._key = None
    db.init()
    yield
    _CLIENT.drop_database(name)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def signup(client, email="owner@example.com", role="msme", firm_name=None):
    res = client.post("/api/auth/signup", json={"email": email, "name": email.split("@")[0], "password": "secret-pass",
                                                "role": role, "firm_name": firm_name})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


@pytest.fixture
def owner(client):
    return signup(client)


@pytest.fixture
def demo_org(client, owner):
    res = client.post("/api/orgs", headers=owner, json={k: sample.BUYER[k] for k in (
        "name", "gstin", "district", "address", "sector", "industry", "udyam", "annual_turnover", "investment_pm",
        "term_loan_amount", "zone", "monthly_electricity_duty", "monthly_power_units")})
    assert res.status_code == 200, res.text
    org_id = res.json()["id"]
    for purpose in ("books_sync", "gstr2b_feed"):
        assert client.post(f"/api/orgs/{org_id}/consent", headers=owner, json={"purpose": purpose}).status_code == 200
    return org_id
