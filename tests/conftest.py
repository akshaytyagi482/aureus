import base64
import os

import psycopg
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AUREUS_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
TEST_DATABASE_URL = os.environ.get("AUREUS_TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:5432/aureus_test")
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

from aureus import config, crypto, db  # noqa: E402
from aureus.api import app  # noqa: E402
from aureus import sample  # noqa: E402


def pytest_configure(config):
    try:
        psycopg.connect(TEST_DATABASE_URL, connect_timeout=3).close()
    except psycopg.OperationalError as exc:
        pytest.exit(f"PostgreSQL test database not reachable at {TEST_DATABASE_URL}: {exc}\n"
                    "Start Postgres and create the database (createdb aureus_test), or set AUREUS_TEST_DATABASE_URL.",
                    returncode=2)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    crypto._key = None
    db.init()
    with db.tx() as cur:
        cur.execute(f"TRUNCATE {', '.join(db.TABLES)} RESTART IDENTITY CASCADE")
    yield


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


@pytest.fixture(scope="session", autouse=True)
def close_pool_at_end():
    yield
    db.close()
