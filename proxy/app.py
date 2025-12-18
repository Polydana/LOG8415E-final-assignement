from flask import Flask, request, jsonify

from . import config
from .router import Router
from .db import execute_query

app = Flask(__name__)
router = Router()


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "role": "proxy"}), 200


@app.route("/sql", methods=["POST"])
def handle_sql():
    """
    Expects JSON:
    {
      "query": "SELECT * FROM actor LIMIT 5;",
      "strategy": "direct" | "random" | "custom"  (optional)
    }
    """
    data = request.get_json(silent=True) or {}
    query = data.get("query")
    strategy = data.get("strategy", config.DEFAULT_STRATEGY)

    if not query:
        return jsonify({"error": "Missing 'query' in body"}), 400

    target_host = router.choose_target(query, strategy)

    result, msg = execute_query(target_host, query)

    return jsonify(
        {
            "target_host": target_host,
            "strategy": strategy,
            "message": msg,
            "result": result,
        }
    ), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=config.DEBUG)
