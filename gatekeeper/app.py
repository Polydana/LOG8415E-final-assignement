# gatekeeper/app.py
import logging

from flask import Flask, request, jsonify
import requests

from . import config
from .auth import is_authorized
from .sql_validation import validate_sql

# Basic logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("gatekeeper")

app = Flask(__name__)


@app.route("/health", methods=["GET"])
def health():
    logger.info("Health check OK")
    return jsonify({"status": "ok", "role": "gatekeeper"}), 200


@app.route("/sql", methods=["POST"])
def handle_sql():
    """
    Public endpoint:
    - Checks auth (X-API-TOKEN header)
    - Validates SQL
    - Forwards to Proxy's /sql endpoint
    - Returns Proxy's response
    """
    logger.info(
        "Incoming /sql request from %s, headers=%s",
        request.remote_addr,
        {k: v for k, v in request.headers.items() if k.lower().startswith("x-")}
    )

    # 1) Auth
    if not is_authorized(request):
        logger.warning("Unauthorized request rejected")
        return jsonify({"error": "Unauthorized"}), 401

    # 2) Input
    data = request.get_json(silent=True) or {}
    query = data.get("query")
    strategy = data.get("strategy")  # optional, can be None

    logger.info("Received query='%s', strategy='%s'", query, strategy)

    if not query:
        logger.warning("Missing 'query' in body")
        return jsonify({"error": "Missing 'query' in body"}), 400

    # 3) Validate SQL
    ok, reason = validate_sql(query)
    if not ok:
        logger.warning("SQL validation failed: %s", reason)
        return jsonify({"error": "Invalid query", "reason": reason}), 400

    # 4) Forward to Proxy
    try:
        payload = {"query": query}
        if strategy:
            payload["strategy"] = strategy

        logger.info("Forwarding to proxy at %s", config.PROXY_URL)
        resp = requests.post(config.PROXY_URL, json=payload, timeout=10)
        logger.info("Proxy response status=%s", resp.status_code)

        try:
            resp_json = resp.json()
        except Exception:
            resp_json = {"raw_text": resp.text}

        return jsonify(
            {
                "via": "gatekeeper",
                "proxy_status": resp.status_code,
                "proxy_response": resp_json,
            }
        ), resp.status_code

    except requests.RequestException as e:
        logger.exception("Exception while contacting proxy")
        return jsonify({"error": "Failed to reach proxy", "details": str(e)}), 502


if __name__ == "__main__":
    logger.info(
        "Starting Gatekeeper on 0.0.0.0:80 with PROXY_URL=%s DEBUG=%s",
        config.PROXY_URL,
        config.DEBUG,
    )
    app.run(host="0.0.0.0", port=80, debug=config.DEBUG)
