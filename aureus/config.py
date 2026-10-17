import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("AUREUS_DATA_DIR", BASE_DIR.parent / "var"))

MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017")
MONGODB_DB = os.environ.get("MONGODB_DB", "aureus")

# 32-byte AES-256 key, base64 encoded. Required in production; a local key file
# is generated for development so the app runs out of the box.
MASTER_KEY_B64 = os.environ.get("AUREUS_MASTER_KEY", "")
ENV = os.environ.get("AUREUS_ENV", "development")

SESSION_TTL_HOURS = int(os.environ.get("AUREUS_SESSION_TTL_HOURS", "12"))

# Variance (in rupees) at or below which an invoice counts as matched (PRD: <= ₹0.99).
MATCH_TOLERANCE_PAISE = int(os.environ.get("AUREUS_MATCH_TOLERANCE_PAISE", "99"))

# Optional: Claude polishes Notice Copilot drafts when a key is configured.
ANTHROPIC_MODEL = os.environ.get("AUREUS_LLM_MODEL", "claude-opus-5-5")

# Optional: Zoho Books OAuth (India data centre by default).
ZOHO_CLIENT_ID = os.environ.get("ZOHO_CLIENT_ID", "")
ZOHO_CLIENT_SECRET = os.environ.get("ZOHO_CLIENT_SECRET", "")
ZOHO_REDIRECT_URI = os.environ.get("ZOHO_REDIRECT_URI", "http://localhost:8000/api/integrations/zoho/callback")
ZOHO_ACCOUNTS_URL = os.environ.get("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.in")
ZOHO_API_URL = os.environ.get("ZOHO_API_URL", "https://www.zohoapis.in")
