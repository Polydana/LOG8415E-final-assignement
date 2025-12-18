import os


def _must_get(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


# Manager + workers
MANAGER_HOST = _must_get("MANAGER_HOST")           # e.g. "10.0.1.10"
WORKER_HOSTS = _must_get("WORKER_HOSTS").split(",")  # e.g. "10.0.1.11,10.0.1.12"

# DB credentials
DB_USER = _must_get("DB_USER")
DB_PASSWORD = _must_get("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME", "sakila")
DB_PORT = int(os.getenv("DB_PORT", "3306"))

# Proxy strategy: "direct" | "random" | "custom"
DEFAULT_STRATEGY = os.getenv("PROXY_STRATEGY", "direct")

# Flask debug
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
