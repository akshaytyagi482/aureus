"""AES-256-GCM encryption for data at rest, and hashing for secrets we only verify.

Invoice datasets, organisation identifiers (GSTIN, PAN, Udyam number) and
third-party OAuth tokens are stored encrypted. Session tokens and passwords are
never stored at all, only their hashes.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import config

_key: bytes | None = None


def _load_key() -> bytes:
    global _key
    if _key is not None:
        return _key
    if config.MASTER_KEY_B64:
        key = base64.b64decode(config.MASTER_KEY_B64)
    elif config.ENV == "production":
        raise RuntimeError("AUREUS_MASTER_KEY must be set in production")
    else:
        key_file = config.DATA_DIR / "dev-master.key"
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not key_file.exists():
            key_file.write_bytes(base64.b64encode(AESGCM.generate_key(bit_length=256)))
            os.chmod(key_file, 0o600)
        key = base64.b64decode(key_file.read_bytes())
    if len(key) != 32:
        raise RuntimeError("AUREUS_MASTER_KEY must decode to 32 bytes (AES-256)")
    _key = key
    return key


def check_key() -> None:
    _load_key()


def encrypt(plaintext: bytes, context: str = "") -> str:
    """Encrypt and bind to `context` (e.g. 'org:12'), so a blob can't be replayed under another org."""
    nonce = os.urandom(12)
    sealed = AESGCM(_load_key()).encrypt(nonce, plaintext, context.encode())
    return base64.b64encode(nonce + sealed).decode()


def decrypt(token: str, context: str = "") -> bytes:
    raw = base64.b64decode(token)
    return AESGCM(_load_key()).decrypt(raw[:12], raw[12:], context.encode())


def encrypt_json(value, context: str = "") -> str:
    return encrypt(json.dumps(value, separators=(",", ":")).encode(), context)


def decrypt_json(token: str, context: str = ""):
    return json.loads(decrypt(token, context))


def encrypt_text(value: str | None, context: str = "") -> str | None:
    return encrypt(value.encode(), context) if value else None


def decrypt_text(token: str | None, context: str = "") -> str | None:
    return decrypt(token, context).decode() if token else None


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return f"pbkdf2_sha256$310000${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
    return hmac.compare_digest(digest.hex(), digest_hex)
