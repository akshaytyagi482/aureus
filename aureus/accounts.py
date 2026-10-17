"""Account security: login throttling, password reset and password change."""

from datetime import timedelta

from . import config, crypto, db, mailer
from .db import get_db

RESET_TTL_MINUTES = 30


class TooManyAttempts(Exception):
    def __init__(self, minutes: int):
        super().__init__(f"Too many attempts. Try again in {minutes} minutes.")
        self.minutes = minutes


def _window_start():
    return db.now() - timedelta(minutes=config.LOGIN_WINDOW_MINUTES)


def check_throttle(scope: str, key: str, limit: int) -> None:
    """Raise if `key` has `limit` or more recorded failures in the window (scope: 'login-email', 'login-ip', ...)."""
    recent = get_db().auth_failures.count_documents({"scope": scope, "key": key, "at": {"$gt": _window_start()}})
    if recent >= limit:
        raise TooManyAttempts(config.LOGIN_WINDOW_MINUTES)


def record_failure(scope: str, key: str) -> None:
    get_db().auth_failures.insert_one({"scope": scope, "key": key, "at": db.now()})


def clear_failures(scope: str, key: str) -> None:
    get_db().auth_failures.delete_many({"scope": scope, "key": key})


def guarded_login(login_fn, email: str, password: str, ip: str):
    """Wrap services.login with per-email and per-IP throttling."""
    email_key = email.lower().strip()
    check_throttle("login-email", email_key, config.LOGIN_MAX_FAILURES_PER_EMAIL)
    check_throttle("login-ip", ip, config.LOGIN_MAX_FAILURES_PER_IP)
    result = login_fn(email, password)
    if result is None:
        record_failure("login-email", email_key)
        record_failure("login-ip", ip)
        return None
    clear_failures("login-email", email_key)
    return result


def request_reset(email: str, ip: str) -> None:
    """Always behaves the same whether or not the account exists, so emails can't be probed."""
    check_throttle("reset-ip", ip, 5)
    record_failure("reset-ip", ip)  # counts requests, not failures
    user = get_db().users.find_one({"email": email.lower().strip()})
    if not user:
        return
    token = crypto.new_token()
    get_db().password_resets.insert_one({"_id": crypto.token_hash(token), "user_id": user["_id"], "created_at": db.now()})
    link = f"{config.APP_BASE_URL}/#/reset?token={token}"
    mailer.send(user["email"], "Reset your AureusRecon password",
                f"Hello {user['name']},\n\nUse this link to set a new password. It works once and expires in "
                f"{RESET_TTL_MINUTES} minutes:\n\n{link}\n\nIf you did not ask for this, you can ignore this email.\n")


def confirm_reset(token: str, new_password: str) -> int:
    doc = get_db().password_resets.find_one_and_delete({"_id": crypto.token_hash(token)})
    if not doc or db.as_utc(doc["created_at"]) < db.now() - timedelta(minutes=RESET_TTL_MINUTES):
        raise ValueError("This reset link is invalid or has expired. Ask for a new one.")
    _set_password(doc["user_id"], new_password)
    return doc["user_id"]


def change_password(user_id: int, current: str, new_password: str) -> None:
    doc = get_db().users.find_one({"_id": user_id})
    if not doc or not crypto.verify_password(current, doc["password_hash"]):
        raise ValueError("Current password is incorrect")
    _set_password(user_id, new_password)


def _set_password(user_id: int, new_password: str) -> None:
    get_db().users.update_one({"_id": user_id}, {"$set": {"password_hash": crypto.hash_password(new_password)}})
    get_db().password_resets.delete_many({"user_id": user_id})
    get_db().sessions.delete_many({"user_id": user_id})  # sign out everywhere
    get_db().audit_log.insert_one({"_id": db.next_id("audit_log"), "org_id": None, "user_id": user_id,
                                   "action": "password_changed", "detail": None, "at": db.now()})
