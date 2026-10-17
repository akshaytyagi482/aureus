"""Scheduled work. Run daily (Render cron): `python -m aureus.jobs daily`.

1. Re-sync Zoho Books for organisations that connected it.
2. Email a GSTR-3B deadline alert when credit is still at risk (once per period).
3. Warn owners 14 days before a data consent expires (once per consent).
Each step is independent: one organisation failing never stops the others.
"""

import logging
import sys
from datetime import date, timedelta

from pymongo.errors import DuplicateKeyError

from . import config, db, integrations, mailer, services
from .db import get_db
from .ingestion import zoho
from .money import inr

log = logging.getLogger("aureus.jobs")
ALERT_DAYS_BEFORE_DUE = 5
SYNC_LOOKBACK_DAYS = 45


def _once(org_id: int, kind: str, ref: str) -> bool:
    """Claim a notification slot; False if it was already sent."""
    try:
        get_db().notifications.insert_one({"org_id": org_id, "kind": kind, "ref": ref, "at": db.now()})
        return True
    except DuplicateKeyError:
        return False


def _member_emails(org_id: int) -> list[str]:
    ids = [m["user_id"] for m in get_db().memberships.find({"org_id": org_id})]
    return [u["email"] for u in get_db().users.find({"_id": {"$in": ids}})]


def sync_zoho(today: date) -> int:
    if not config.ZOHO_CLIENT_ID:
        return 0
    synced = 0
    for link in get_db().integrations.find({"provider": "zoho"}):
        org_id = link["org_id"]
        try:
            start = (today - timedelta(days=SYNC_LOOKBACK_DAYS)).isoformat()
            bills = zoho.parse_data(integrations.fetch_bills(org_id, start, today.isoformat()))
            services.replace_by_period(org_id, None, "books", "zoho", bills)
            synced += 1
        except Exception:  # one org's failure must not stop the rest
            log.exception("Zoho sync failed for org %s", org_id)
    return synced


def deadline_alerts(today: date) -> int:
    sent = 0
    for doc in get_db().orgs.find({}, {"_id": 1}):
        try:
            org = services.get_org_by_id(doc["_id"])
            recon = services.run_reconciliation(org, today=today)
            filing, summary = recon["filing"], recon["summary"]
            if not (0 <= filing["days_left"] <= ALERT_DAYS_BEFORE_DUE) or summary["itc_at_risk"] <= 0:
                continue
            if not _once(org["id"], "gstr3b_due", filing["period"]):
                continue
            body = (f"GSTR-3B for {filing['period_label']} is due on {filing['gstr3b_due_on']} "
                    f"({filing['days_left']} days left).\n\n"
                    f"{org['name']} still has {inr(summary['itc_at_risk'])} of input tax credit at risk across "
                    f"{summary['by_status']['MISSING_IN_PORTAL']['count'] + summary['by_status']['MISMATCH_WARN']['count']} "
                    f"invoices.\n\nReview and send supplier reminders: {config.APP_BASE_URL}/#/org/{org['id']}/recon\n")
            for email in _member_emails(org["id"]):
                mailer.send(email, f"GSTR-3B due in {filing['days_left']} days: {inr(summary['itc_at_risk'])} at risk", body)
            sent += 1
        except Exception:
            log.exception("Deadline alert failed for org %s", doc["_id"])
    return sent


def consent_expiry_warnings(today: date) -> int:
    sent = 0
    soon = db.now() + timedelta(days=14)
    for consent in get_db().consents.find({"revoked_at": None, "expires_at": {"$gt": db.now(), "$lt": soon}}):
        if not _once(consent["org_id"], "consent_expiry", str(consent["_id"])):
            continue
        try:
            org = services.get_org_by_id(consent["org_id"])
            body = (f"Your consent for '{consent['artefact']['purpose_text']}' for {org['name']} expires on "
                    f"{consent['artefact']['expires_at'][:10]}. After that AureusRecon stops using this data.\n\n"
                    f"Renew it here: {config.APP_BASE_URL}/#/org/{org['id']}/data\n")
            for email in _member_emails(org["id"]):
                mailer.send(email, f"Consent expiring for {org['name']}", body)
            sent += 1
        except Exception:
            log.exception("Consent warning failed for consent %s", consent["_id"])
    return sent


def daily(today: date | None = None) -> dict:
    today = today or date.today()
    db.init()
    result = {"zoho_synced": sync_zoho(today), "deadline_alerts": deadline_alerts(today),
              "consent_warnings": consent_expiry_warnings(today)}
    log.info("daily job: %s", result)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if sys.argv[1:] != ["daily"]:
        sys.exit("usage: python -m aureus.jobs daily")
    print(daily())
