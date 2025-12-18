from flask import Flask, request, jsonify
import requests

from . import config
from .auth import is_authorized
from .sql_validation import validate_sql

app = Flask(__name__)


@app.route("/health", methods=["GET"])
def health():
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
    # 1) Auth
    if not is_authorized(request):
        return jsonify({"error": "Unauthorized"}), 401

    # 2) Input
    data = request.get_json(silent=True) or {}
    query = data.get("query")
    strategy = data.get("strategy")  # optional, can be None

    if not query:
        return jsonify({"error": "Missing 'query' in body"}), 400

    # 3) Validate SQL
    ok, reason = validate_sql(query)
    if not ok:
        return jsonify({"error": "Invalid query", "reason": reason}), 400

    # 4) Forward to Proxy
    try:
        payload = {"query": query}
        if strategy:
            payload["strategy"] = strategy

        resp = requests.post(config.PROXY_URL, json=payload, timeout=10)

        # Pass through status code and json
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
        return jsonify({"error": "Failed to reach proxy", "details": str(e)}), 502


if __name__ == "__main__":
    # Gatekeeper is internet-facing, you probably bind on port 8080 or 80 (through nginx)
    app.run(host="0.0.0.0", port=8080, debug=config.DEBUG)
