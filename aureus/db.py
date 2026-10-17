"""MongoDB persistence. Financial records live only as encrypted dataset chunks.

Collections and what they hold:

    users             accounts (email unique, PBKDF2 password hash)
    sessions          hashed bearer tokens, removed by a TTL index at expiry
    orgs              businesses; GSTIN and Udyam number encrypted
    memberships       (org_id, user_id) -> owner | ca, unique per pair
    consents          consent artefacts, revoked_at set on revocation
    datasets          import metadata; the payload itself is in dataset_chunks
    dataset_chunks    AES-256-GCM ciphertext split into <= 4 MB pieces
    integrations      encrypted OAuth tokens per (org, provider)
    oauth_states      hashed OAuth state values, removed by a TTL index
    connector_tokens  hashed desktop-connector keys
    audit_log         who did what, without financial payloads
    counters          integer id sequences

Documents use integer `_id`s so URLs and the API stay simple.
"""

from datetime import datetime, timezone

from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument

from . import config

_client: MongoClient | None = None


def set_client(client) -> None:
    """Swap the client (tests use this to point at a throwaway database)."""
    global _client
    _client = client


def get_db():
    global _client
    if _client is None:
        _client = MongoClient(config.MONGODB_URI, tz_aware=True, serverSelectionTimeoutMS=5000)
    return _client[config.MONGODB_DB]


def init() -> None:
    d = get_db()
    d.users.create_index("email", unique=True)
    d.sessions.create_index("expires_at", expireAfterSeconds=0)
    d.sessions.create_index("user_id")
    d.memberships.create_index([("org_id", ASCENDING), ("user_id", ASCENDING)], unique=True)
    d.memberships.create_index("user_id")
    d.consents.create_index([("org_id", ASCENDING), ("purpose", ASCENDING)])
    d.datasets.create_index([("org_id", ASCENDING), ("kind", ASCENDING), ("_id", ASCENDING)])
    d.dataset_chunks.create_index([("dataset_id", ASCENDING), ("n", ASCENDING)], unique=True)
    d.dataset_chunks.create_index("org_id")
    d.integrations.create_index([("org_id", ASCENDING), ("provider", ASCENDING)], unique=True)
    d.oauth_states.create_index("created_at", expireAfterSeconds=15 * 60)
    d.connector_tokens.create_index("org_id")
    d.audit_log.create_index([("org_id", ASCENDING), ("_id", DESCENDING)])


def next_id(name: str) -> int:
    doc = get_db().counters.find_one_and_update(
        {"_id": name}, {"$inc": {"seq": 1}}, upsert=True, return_document=ReturnDocument.AFTER)
    return doc["seq"]


def now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """Mongo returns naive datetimes unless the client is tz-aware; normalise either way."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def fmt(value: datetime | None) -> str | None:
    value = as_utc(value)
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None
