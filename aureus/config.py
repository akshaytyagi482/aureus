import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("AUREUS_DATA_DIR", BASE_DIR.parent / "var"))

# PostgreSQL connection string (Render, Neon, Supabase, AWS RDS all give one in this form).
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres@127.0.0.1:5432/aureus")
DATABASE_URL_SET = "DATABASE_URL" in os.environ
DB_POOL_SIZE = int(os.environ.get("AUREUS_DB_POOL_SIZE", "10"))

# 32-byte AES-256 key, base64 encoded. Required in production; a local key file
# is generated for development so the app runs out of the box.
MASTER_KEY_B64 = os.environ.get("AUREUS_MASTER_KEY", "")
# Render sets RENDER=true on every service; treat it as production even if AUREUS_ENV was forgotten,
# so a missing key fails the deploy instead of silently using a throwaway key file.
ON_RENDER = bool(os.environ.get("RENDER"))
ENV = os.environ.get("AUREUS_ENV") or ("production" if ON_RENDER else "development")

SESSION_TTL_HOURS = int(os.environ.get("AUREUS_SESSION_TTL_HOURS", "12"))

# Variance (in rupees) at or below which an invoice counts as matched (PRD: <= ₹0.99).
MATCH_TOLERANCE_PAISE = int(os.environ.get("AUREUS_MATCH_TOLERANCE_PAISE", "99"))

# Optional: Claude polishes Notice Copilot drafts when a key is configured (and Gemini is not).
ANTHROPIC_MODEL = os.environ.get("AUREUS_LLM_MODEL", "claude-opus-5-5")

# Optional: Gemini powers the AI features (assistant, notice reader, HSN fixer, supplier matcher, policy reader).
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
GEMINI_MODEL = os.environ.get("AUREUS_GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_LITE_MODEL = os.environ.get("AUREUS_GEMINI_LITE_MODEL", "gemini-3.5-flash-lite")
# "free": Google may use prompts to improve its products, so the app masks client names and GSTINs
# where it can and asks before sending documents. Set to "paid" once billing is enabled on the key.
AI_TIER = os.environ.get("AUREUS_AI_TIER", "free").lower()

# Optional: Zoho Books OAuth (India data centre by default).
ZOHO_CLIENT_ID = os.environ.get("ZOHO_CLIENT_ID", "")
ZOHO_CLIENT_SECRET = os.environ.get("ZOHO_CLIENT_SECRET", "")
ZOHO_REDIRECT_URI = os.environ.get("ZOHO_REDIRECT_URI", "http://localhost:8000/api/integrations/zoho/callback")
ZOHO_ACCOUNTS_URL = os.environ.get("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.in")
ZOHO_API_URL = os.environ.get("ZOHO_API_URL", "https://www.zohoapis.in")

# Public URL of the app, used in emailed links (password reset, alerts).
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8000").rstrip("/")

# Outgoing email (any SMTP provider: Resend, SendGrid, Amazon SES, Gmail Workspace...).
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
MAIL_FROM = os.environ.get("MAIL_FROM", "AureusRecon <no-reply@localhost>")

# Brute-force protection: failed logins allowed per email / per IP within the window.
LOGIN_MAX_FAILURES_PER_EMAIL = int(os.environ.get("AUREUS_LOGIN_MAX_FAILURES", "5"))
LOGIN_MAX_FAILURES_PER_IP = int(os.environ.get("AUREUS_LOGIN_MAX_FAILURES_PER_IP", "20"))
LOGIN_WINDOW_MINUTES = int(os.environ.get("AUREUS_LOGIN_WINDOW_MINUTES", "15"))


def check_production() -> None:
    """Refuse to start in production without the settings that protect client data."""
    problems = []
    if not DATABASE_URL_SET:
        problems.append("DATABASE_URL is not set. Link the Postgres database: create the services from the Blueprint "
                        "(New > Blueprint), or add DATABASE_URL = the database's Internal Database URL.")
    if not MASTER_KEY_B64:
        problems.append("AUREUS_MASTER_KEY is not set. Generate one (see docs/DEPLOY.md) and keep a copy; "
                        "without it encrypted data would be lost on the next deploy.")
    if problems:
        raise RuntimeError("AureusRecon cannot start:\n  - " + "\n  - ".join(problems))
