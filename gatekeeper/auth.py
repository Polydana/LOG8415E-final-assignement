from flask import Request
from . import config


def is_authorized(request: Request) -> bool:
    """
    Simple token-based auth.
    Client must send header: X-API-TOKEN: <token>
    """
    token = request.headers.get("X-API-TOKEN")
    if not token:
        return False
    return token == config.API_TOKEN
