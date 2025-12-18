import os


def _must_get(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


# URL of the Proxy's /sql endpoint, e.g. "http://10.0.2.50:5000/sql"
PROXY_URL = _must_get("PROXY_URL")

# Very simple shared token for auth from clients to Gatekeeper
API_TOKEN = _must_get("API_TOKEN")  # e.g. "supersecret123"

# Flask debug flag
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
