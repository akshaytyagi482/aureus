from datetime import date, timedelta

import pytest

from aureus import config, db, jobs, mailer, sample, services
from aureus.ingestion import parse_books



@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, body: sent.append({"to": to, "subject": subject, "body": body}))
    return sent


def _login(client, password="secret-pass", email="owner@example.com"):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_login_locks_after_repeated_failures(client, owner):
    for _ in range(config.LOGIN_MAX_FAILURES_PER_EMAIL):
        assert _login(client, "wrong-pass").status_code == 401
    res = _login(client)  # even the right password is refused while locked
    assert res.status_code == 429 and "Retry-After" in res.headers


def test_successful_login_clears_failures(client, owner):
    for _ in range(config.LOGIN_MAX_FAILURES_PER_EMAIL - 1):
        _login(client, "wrong-pass")
    assert _login(client).status_code == 200
    for _ in range(config.LOGIN_MAX_FAILURES_PER_EMAIL - 1):
        assert _login(client, "wrong-pass").status_code == 401


def test_password_reset_flow(client, owner, outbox):
    res = client.post("/api/auth/password-reset/request", json={"email": "owner@example.com"})
    assert res.status_code == 200 and len(outbox) == 1
    token = outbox[0]["body"].split("token=")[1].split()[0]
    assert client.post("/api/auth/password-reset/confirm", json={"token": token, "password": "brand-new-pass"}).status_code == 200
    assert client.get("/api/me", headers=owner).status_code == 401  # old sessions signed out
    assert _login(client, "brand-new-pass").status_code == 200
    # one-time token
    assert client.post("/api/auth/password-reset/confirm", json={"token": token, "password": "another-pass1"}).status_code == 400


def test_reset_does_not_reveal_accounts(client, outbox):
    res = client.post("/api/auth/password-reset/request", json={"email": "nobody@example.com"})
    assert res.status_code == 200 and outbox == []


def test_reset_requests_are_throttled(client, owner, outbox):
    codes = [client.post("/api/auth/password-reset/request", json={"email": "owner@example.com"}).status_code
             for _ in range(6)]
    assert codes[:5] == [200] * 5 and codes[5] == 429


def test_change_password(client, owner):
    bad = client.post("/api/auth/password", headers=owner, json={"current_password": "nope", "new_password": "x" * 10})
    assert bad.status_code == 400
    ok = client.post("/api/auth/password", headers=owner, json={"current_password": "secret-pass", "new_password": "x" * 10})
    assert ok.status_code == 200 and _login(client, "x" * 10).status_code == 200


def test_reset_needs_smtp_in_production(client, owner, monkeypatch):
    monkeypatch.setattr(config, "ENV", "production")
    monkeypatch.setattr(config, "SMTP_HOST", "")
    res = client.post("/api/auth/password-reset/request", json={"email": "owner@example.com"})
    assert res.status_code == 503


def test_deadline_alert_sent_once(client, owner, demo_org, outbox):
    client.post(f"/api/orgs/{demo_org}/import/sample", headers=owner)
    assert jobs.deadline_alerts(date(2026, 10, 17)) == 1  # GSTR-3B due 20 Oct, credit at risk
    assert "at risk" in outbox[0]["subject"] and outbox[0]["to"] == "owner@example.com"
    assert jobs.deadline_alerts(date(2026, 10, 18)) == 0  # not repeated for the same period
    assert jobs.deadline_alerts(date(2026, 10, 5)) == 0   # too early


def test_consent_expiry_warning(client, owner, demo_org, outbox):
    db.execute("UPDATE consents SET expires_at = %s WHERE org_id = %s", (db.now() + timedelta(days=5), demo_org))
    assert jobs.consent_expiry_warnings(date.today()) == 2
    assert jobs.consent_expiry_warnings(date.today()) == 0


def test_replace_by_period_keeps_one_copy(owner, client, demo_org):
    invoices = parse_books("tally", sample.tally_xml())
    services.replace_by_period(demo_org, None, "books", "zoho", invoices)
    services.replace_by_period(demo_org, None, "books", "zoho", invoices)
    assert db.one("SELECT COUNT(*) AS n FROM datasets WHERE org_id = %s AND source = 'zoho'", (demo_org,))["n"] == 1


def test_signup_is_throttled_per_ip(client):
    codes = [client.post("/api/auth/signup", json={"email": f"u{i}@example.com", "name": "U", "password": "longpassword",
                                                   "role": "msme"}).status_code for i in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429


def test_daily_job_runs_end_to_end(client, owner, demo_org, outbox):
    client.post(f"/api/orgs/{demo_org}/import/sample", headers=owner)
    result = jobs.daily(date(2026, 10, 17))
    assert {k: v for k, v in result.items() if k != "expired_rows_purged"} == \
        {"zoho_synced": 0, "deadline_alerts": 1, "consent_warnings": 0}


def test_purge_expired_rows(client, owner):
    db.execute("UPDATE sessions SET expires_at = now() - interval '1 minute'")
    db.execute("INSERT INTO auth_failures (scope, key, at) VALUES ('login-ip', 'x', now() - interval '2 days')")
    assert jobs.purge_expired() >= 2
    assert client.get("/api/me", headers=owner).status_code == 401


def test_production_refuses_to_start_without_database_or_key(monkeypatch):
    from aureus import config
    monkeypatch.setattr(config, "DATABASE_URL_SET", False)
    monkeypatch.setattr(config, "MASTER_KEY_B64", "")
    with pytest.raises(RuntimeError) as err:
        config.check_production()
    assert "DATABASE_URL is not set" in str(err.value) and "AUREUS_MASTER_KEY is not set" in str(err.value)
    monkeypatch.setattr(config, "DATABASE_URL_SET", True)
    monkeypatch.setattr(config, "MASTER_KEY_B64", "x")
    config.check_production()
