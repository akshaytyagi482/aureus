"""SQLite persistence. Financial records live only as encrypted dataset blobs."""

import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('msme', 'ca')),
    firm_name TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orgs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    gstin_enc TEXT NOT NULL,
    gstin_last4 TEXT NOT NULL,
    state_code TEXT NOT NULL,
    district TEXT,
    address TEXT,
    sector TEXT NOT NULL DEFAULT 'manufacturing',
    industry TEXT,
    udyam_enc TEXT,
    annual_turnover REAL,
    investment_pm REAL,
    term_loan_amount REAL,
    monthly_power_bill REAL,
    filing_frequency TEXT NOT NULL DEFAULT 'monthly',
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS memberships (
    org_id INTEGER NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('owner', 'ca')),
    PRIMARY KEY (org_id, user_id)
);
CREATE TABLE IF NOT EXISTS consents (
    id INTEGER PRIMARY KEY,
    org_id INTEGER NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    purpose TEXT NOT NULL,
    granted_by INTEGER REFERENCES users(id),
    granted_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT,
    revoked_at TEXT,
    artefact TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS datasets (
    id INTEGER PRIMARY KEY,
    org_id INTEGER NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('books', 'gstr2b')),
    source TEXT NOT NULL,
    period TEXT,
    record_count INTEGER NOT NULL,
    imported_at TEXT NOT NULL DEFAULT (datetime('now')),
    payload_enc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS integrations (
    org_id INTEGER NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    tokens_enc TEXT NOT NULL,
    external_org_id TEXT,
    connected_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (org_id, provider)
);
CREATE TABLE IF NOT EXISTS oauth_states (
    state_hash TEXT PRIMARY KEY,
    org_id INTEGER NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS connector_tokens (
    token_hash TEXT PRIMARY KEY,
    org_id INTEGER NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_used_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    org_id INTEGER,
    user_id INTEGER,
    action TEXT NOT NULL,
    detail TEXT,
    at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def transaction():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
