"""Zoho Books OAuth connector.

Flow: the user clicks Connect, is sent to Zoho to approve read-only access to
bills, Zoho redirects back with a code, and we exchange it for tokens. Tokens
are stored AES-256-GCM encrypted; the user's Zoho password never touches us.

Not exercised against live Zoho in tests (needs real client credentials);
the HTTP calls follow Zoho's documented OAuth 2.0 and Books v3 endpoints.
"""

import time
from datetime import timedelta
from urllib.parse import urlencode

import httpx

from . import config, crypto, db
from .db import get_db
from .services import _ctx, audit

SCOPE = "ZohoBooks.bills.READ,ZohoBooks.settings.READ"


class NotConfigured(Exception):
    pass


def _require_config():
    if not (config.ZOHO_CLIENT_ID and config.ZOHO_CLIENT_SECRET):
        raise NotConfigured("Zoho Books is not configured on this server (set ZOHO_CLIENT_ID and ZOHO_CLIENT_SECRET)")


def authorize_url(org_id: int, user_id: int) -> str:
    _require_config()
    state = crypto.new_token()
    # A TTL index on created_at removes unused states after 15 minutes.
    get_db().oauth_states.insert_one({"_id": crypto.token_hash(state), "org_id": org_id, "user_id": user_id,
                                      "created_at": db.now()})
    query = urlencode({"scope": SCOPE, "client_id": config.ZOHO_CLIENT_ID, "response_type": "code",
                       "redirect_uri": config.ZOHO_REDIRECT_URI, "access_type": "offline", "prompt": "consent",
                       "state": state})
    return f"{config.ZOHO_ACCOUNTS_URL}/oauth/v2/auth?{query}"


def complete(code: str, state: str) -> int:
    _require_config()
    row = get_db().oauth_states.find_one_and_delete({"_id": crypto.token_hash(state)})
    if row and db.as_utc(row["created_at"]) < db.now() - timedelta(minutes=15):
        row = None
    if not row:
        raise PermissionError("OAuth state is invalid or expired; start the connection again")
    resp = httpx.post(f"{config.ZOHO_ACCOUNTS_URL}/oauth/v2/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": config.ZOHO_CLIENT_ID,
        "client_secret": config.ZOHO_CLIENT_SECRET, "redirect_uri": config.ZOHO_REDIRECT_URI}, timeout=20)
    resp.raise_for_status()
    tokens = resp.json()
    if "access_token" not in tokens:
        raise PermissionError(f"Zoho rejected the authorisation: {tokens.get('error', 'unknown error')}")
    tokens["expires_at"] = time.time() + int(tokens.get("expires_in", 3600)) - 60
    _save_tokens(row["org_id"], tokens)
    audit(row["org_id"], row["user_id"], "integration_connected", {"provider": "zoho"})
    return row["org_id"]


def _save_tokens(org_id: int, tokens: dict, external_org_id: str | None = None):
    changes = {"tokens_enc": crypto.encrypt_json(tokens, _ctx(org_id, "zoho"))}
    if external_org_id:
        changes["external_org_id"] = external_org_id
    get_db().integrations.update_one({"org_id": org_id, "provider": "zoho"},
                                     {"$set": changes, "$setOnInsert": {"connected_at": db.now()}}, upsert=True)


def status(org_id: int) -> dict:
    row = get_db().integrations.find_one({"org_id": org_id, "provider": "zoho"})
    return {"configured": bool(config.ZOHO_CLIENT_ID), "connected": bool(row),
            "connected_at": db.fmt(row["connected_at"]) if row else None}


def _access_token(org_id: int) -> tuple[dict, str | None]:
    row = get_db().integrations.find_one({"org_id": org_id, "provider": "zoho"})
    if not row:
        raise PermissionError("Zoho Books is not connected for this organisation")
    tokens = crypto.decrypt_json(row["tokens_enc"], _ctx(org_id, "zoho"))
    if tokens["expires_at"] <= time.time():
        resp = httpx.post(f"{config.ZOHO_ACCOUNTS_URL}/oauth/v2/token", data={
            "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
            "client_id": config.ZOHO_CLIENT_ID, "client_secret": config.ZOHO_CLIENT_SECRET}, timeout=20)
        resp.raise_for_status()
        fresh = resp.json()
        tokens.update(access_token=fresh["access_token"], expires_at=time.time() + int(fresh.get("expires_in", 3600)) - 60)
        _save_tokens(org_id, tokens)
    return tokens, row.get("external_org_id")


def fetch_bills(org_id: int, date_from: str, date_to: str) -> dict:
    """Pull bills (with line items and taxes) in a date range, in the shape `ingestion.zoho.parse` reads."""
    _require_config()
    tokens, zoho_org = _access_token(org_id)
    headers = {"Authorization": f"Zoho-oauthtoken {tokens['access_token']}"}
    base = f"{config.ZOHO_API_URL}/books/v3"
    with httpx.Client(headers=headers, timeout=30) as client:
        if not zoho_org:
            orgs = client.get(f"{base}/organizations").raise_for_status().json().get("organizations", [])
            if not orgs:
                raise PermissionError("No Zoho Books organisation is available for this login")
            zoho_org = str(orgs[0]["organization_id"])
            _save_tokens(org_id, tokens, zoho_org)
        bills, page = [], 1
        while True:
            listing = client.get(f"{base}/bills", params={"organization_id": zoho_org, "date_start": date_from,
                                                          "date_end": date_to, "page": page, "per_page": 200}
                                 ).raise_for_status().json()
            for summary in listing.get("bills", []):
                detail = client.get(f"{base}/bills/{summary['bill_id']}", params={"organization_id": zoho_org}
                                    ).raise_for_status().json()
                bills.append(detail["bill"])
            if not listing.get("page_context", {}).get("has_more_page"):
                break
            page += 1
    return {"bills": bills}
