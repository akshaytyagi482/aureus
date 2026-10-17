"""Account security: login throttling, password reset and password change."""

from datetime import timedelta

from . import config, crypto, db, mailer

RESET_TTL_MINUTES = 30


class TooManyAttempts(Exception):
    def __init__(self, minutes: int):
        super().__init__(f"Too many attempts. Try again in {minutes} minutes.")
        self.minutes = minutes


def _window_start():
    return db.now() - timedelta(minutes=config.LOGIN_WINDOW_MINUTES)


def check_throttle(scope: str, key: str, limit: int) -> None:
    """Raise if `key` has `limit` or more recorded failures in the window (scope: 'login-email', 'login-ip', ...)."""
    recent = db.one("SELECT COUNT(*) AS n FROM auth_failures WHERE scope = %s AND key = %s AND at > %s",
                    (scope, key, _window_start()))["n"]
    if recent >= limit:
        raise TooManyAttempts(config.LOGIN_WINDOW_MINUTES)


def record_failure(scope: str, key: str) -> None:
    db.execute("INSERT INTO auth_failures (scope, key) VALUES (%s, %s)", (scope, key))


def clear_failures(scope: str, key: str) -> None:
    db.execute("DELETE FROM auth_failures WHERE scope = %s AND key = %s", (scope, key))


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
    user = db.one("SELECT id, email, name FROM users WHERE email = %s", (email.lower().strip(),))
    if not user:
        return
    token = crypto.new_token()
    db.execute("INSERT INTO password_resets (token_hash, user_id) VALUES (%s, %s)", (crypto.token_hash(token), user["id"]))
    link = f"{config.APP_BASE_URL}/#/reset?token={token}"
    mailer.send(user["email"], "Reset your AureusRecon password",
                f"Hello {user['name']},\n\nUse this link to set a new password. It works once and expires in "
                f"{RESET_TTL_MINUTES} minutes:\n\n{link}\n\nIf you did not ask for this, you can ignore this email.\n")


def confirm_reset(token: str, new_password: str) -> int:
    doc = db.one("DELETE FROM password_resets WHERE token_hash = %s RETURNING user_id, created_at",
                 (crypto.token_hash(token),))
    if not doc or doc["created_at"] < db.now() - timedelta(minutes=RESET_TTL_MINUTES):
        raise ValueError("This reset link is invalid or has expired. Ask for a new one.")
    _set_password(doc["user_id"], new_password)
    return doc["user_id"]


def change_password(user_id: int, current: str, new_password: str) -> None:
    doc = db.one("SELECT password_hash FROM users WHERE id = %s", (user_id,))
    if not doc or not crypto.verify_password(current, doc["password_hash"]):
        raise ValueError("Current password is incorrect")
    _set_password(user_id, new_password)


def _set_password(user_id: int, new_password: str) -> None:
    with db.tx() as cur:
        cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (crypto.hash_password(new_password), user_id))
        cur.execute("DELETE FROM password_resets WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))  # sign out everywhere
        cur.execute("INSERT INTO audit_log (org_id, user_id, action) VALUES (NULL, %s, 'password_changed')", (user_id,))
