"""Settings, read from environment variables (and from .env for local use)."""
import os
from pathlib import Path

from dotenv import load_dotenv

# .env lives in the project root, one folder above app/. An explicit path
# means it is found no matter which folder the server is started from.
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)

PORTAL_BASE_URL = os.getenv(
    "PORTAL_BASE_URL", "https://urja-ops.flockenergy.tech"
).rstrip("/")
PORTAL_USERNAME = os.getenv("PORTAL_USERNAME")
PORTAL_PASSWORD = os.getenv("PORTAL_PASSWORD")
REQUEST_TIMEOUT_SECONDS = float(os.getenv("PORTAL_TIMEOUT_SECONDS", "30"))
