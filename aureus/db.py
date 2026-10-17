"""PostgreSQL persistence. Financial records live only as encrypted dataset payloads.

Tables:
    users             accounts (email unique, PBKDF2 password hash)
    sessions          hashed bearer tokens with expiry
    orgs              businesses; GSTIN and Udyam number encrypted
    memberships       (org_id, user_id) -> owner | ca
    consents          consent artefacts (JSONB), revoked_at set on revocation
    datasets          one encrypted import each (AES-256-GCM ciphertext in payload_enc)
    integrations      encrypted OAuth tokens per (org, provider)
    oauth_states      hashed OAuth state values (15-minute life)
    connector_tokens  hashed desktop-connector keys
    audit_log         who did what, without financial payloads (kept after an org is deleted)
    auth_failures     failed logins / reset requests for throttling
    password_resets   hashed one-time reset tokens (30-minute life)
    notifications     alerts already sent, so jobs never repeat them

Deleting an org cascades through foreign keys to everything it owns.
Expired rows are filtered out by queries and purged by the daily job.
Schema changes are numbered migrations applied once, under an advisory lock.
"""

import atexit
from contextlib import contextmanager
from datetime import datetime, timezone

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from . import config

MIGRATIONS = [
    # 1: initial schema
    """
    CREATE TABLE users (
        id BIGSERIAL PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('msme', 'ca')),
        firm_name TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        expires_at TIMESTAMPTZ NOT NULL
    );
    CREATE INDEX sessions_user ON sessions(user_id);
    CREATE TABLE orgs (
        id BIGSERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        gstin_enc TEXT NOT NULL,
        gstin_last4 TEXT NOT NULL,
        state_code TEXT NOT NULL,
        district TEXT,
        address TEXT,
        sector TEXT NOT NULL DEFAULT 'manufacturing',
        industry TEXT,
        udyam_enc TEXT,
        annual_turnover DOUBLE PRECISION,
        investment_pm DOUBLE PRECISION,
        term_loan_amount DOUBLE PRECISION,
        zone TEXT,
        monthly_electricity_duty DOUBLE PRECISION,
        monthly_power_units DOUBLE PRECISION,
        filing_frequency TEXT NOT NULL DEFAULT 'monthly' CHECK (filing_frequency IN ('monthly', 'quarterly')),
        created_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE TABLE memberships (
        org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        role TEXT NOT NULL CHECK (role IN ('owner', 'ca')),
        PRIMARY KEY (org_id, user_id)
    );
    CREATE INDEX memberships_user ON memberships(user_id);
    CREATE TABLE consents (
        id BIGSERIAL PRIMARY KEY,
        org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
        purpose TEXT NOT NULL,
        granted_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
        granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        expires_at TIMESTAMPTZ NOT NULL,
        revoked_at TIMESTAMPTZ,
        artefact JSONB NOT NULL
    );
    CREATE INDEX consents_org ON consents(org_id, purpose);
    CREATE TABLE datasets (
        id BIGSERIAL PRIMARY KEY,
        org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
        kind TEXT NOT NULL CHECK (kind IN ('books', 'gstr2b', 'ims')),
        source TEXT NOT NULL,
        periods TEXT[] NOT NULL DEFAULT '{}',
        record_count INTEGER NOT NULL,
        imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        payload_enc TEXT NOT NULL
    );
    CREATE INDEX datasets_org_kind ON datasets(org_id, kind, id);
    CREATE TABLE integrations (
        org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
        provider TEXT NOT NULL,
        tokens_enc TEXT NOT NULL,
        external_org_id TEXT,
        connected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (org_id, provider)
    );
    CREATE TABLE oauth_states (
        state_hash TEXT PRIMARY KEY,
        org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE TABLE connector_tokens (
        token_hash TEXT PRIMARY KEY,
        org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
        created_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_used_at TIMESTAMPTZ
    );
    CREATE TABLE audit_log (
        id BIGSERIAL PRIMARY KEY,
        org_id BIGINT,
        user_id BIGINT,
        action TEXT NOT NULL,
        detail JSONB,
        at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX audit_org ON audit_log(org_id, id DESC);
    CREATE TABLE auth_failures (
        id BIGSERIAL PRIMARY KEY,
        scope TEXT NOT NULL,
        key TEXT NOT NULL,
        at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX auth_failures_lookup ON auth_failures(scope, key, at);
    CREATE TABLE password_resets (
        token_hash TEXT PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE TABLE notifications (
        org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        ref TEXT NOT NULL,
        at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (org_id, kind, ref)
    );
    """,
]

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(config.DATABASE_URL, min_size=1, max_size=config.DB_POOL_SIZE,
                               kwargs={"row_factory": dict_row}, open=True)
        atexit.register(close)  # scripts (seed, daily job) exit cleanly
    return _pool


def close() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def tx():
    """A cursor inside one transaction: committed on success, rolled back on any exception."""
    with get_pool().connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                yield cur


def one(sql: str, params=()) -> dict | None:
    with tx() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def all(sql: str, params=()) -> list[dict]:  # noqa: A001 - mirrors cursor.fetchall
    with tx() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def execute(sql: str, params=()) -> int:
    with tx() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def init() -> None:
    """Apply pending migrations. The advisory lock stops the web app and the cron job racing."""
    with tx() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(724113)")
        cur.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, "
                    "applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        cur.execute("SELECT COALESCE(MAX(version), 0) AS v FROM schema_migrations")
        current = cur.fetchone()["v"]
        for version, sql in enumerate(MIGRATIONS, start=1):
            if version > current:
                cur.execute(sql)
                cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))


TABLES = ["notifications", "password_resets", "auth_failures", "audit_log", "connector_tokens", "oauth_states",
          "integrations", "datasets", "consents", "memberships", "orgs", "sessions", "users"]


def json(value) -> Jsonb:
    return Jsonb(value)


def now() -> datetime:
    return datetime.now(timezone.utc)


def fmt(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
