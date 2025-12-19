# proxy/app.py
import logging
import random

from flask import Flask, request, jsonify
import mysql.connector

from . import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("proxy")

app = Flask(__name__)


def get_connection(host: str):
    return mysql.connector.connect(
        host=host,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        database=config.DB_NAME,
        port=int(config.DB_PORT),
    )


def choose_host(strategy: str, query: str):
    """
    Simple strategy implementation:
    - direct: always manager
    - random: random among manager + workers
    - custom: reads -> random worker, writes -> manager
    """
    manager = config.MANAGER_HOST
    workers = config.WORKER_HOSTS

    if isinstance(workers, str):
        workers = [w for w in workers.split(",") if w.strip()]

    # Fallback if no workers provided
    all_hosts = [manager] + workers if workers else [manager]

    s = (strategy or "direct").lower()

    # Custom simple read/write split
    q_lower = (query or "").strip().lower()
    is_read = q_lower.startswith("select")

    if s == "direct":
        return manager
    elif s == "random":
        return random.choice(all_hosts)
    elif s == "custom":
        if is_read and workers:
            return random.choice(workers)
        return manager
    else:
        # default to manager
        return manager


@app.route("/health", methods=["GET"])
def health():
    logger.info("Proxy health check OK")
    return jsonify({"status": "ok", "role": "proxy"}), 200


@app.route("/sql", methods=["POST"])
def handle_sql():
    data = request.get_json(silent=True) or {}
    query = data.get("query")
    strategy = data.get("strategy")

    logger.info("Received /sql query='%s', strategy='%s'", query, strategy)

    if not query:
        logger.warning("Missing 'query' in body")
        return jsonify({"error": "Missing 'query' in body"}), 400

    target_host = choose_host(strategy, query)
    logger.info("Chosen target host=%s", target_host)

    try:
        conn = get_connection(target_host)
    except mysql.connector.Error as e:
        logger.exception("MySQL connection error")
        return jsonify({"error": "MySQL connection error", "details": str(e)}), 500

    try:
        cursor = conn.cursor()
        cursor.execute(query)

        if query.strip().lower().startswith("select"):
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            result = [dict(zip(columns, row)) for row in rows]
            logger.info("SELECT returned %d rows", len(result))
            return jsonify({"rows": result}), 200
        else:
            affected = cursor.rowcount
            conn.commit()
            logger.info("Write query affected %d rows", affected)
            return jsonify({"affected_rows": affected}), 200
    except mysql.connector.Error as e:
        logger.exception("MySQL query error")
        return jsonify({"error": "MySQL query error", "details": str(e)}), 500
    finally:
        try:
            cursor.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    logger.info(
        "Starting Proxy on 0.0.0.0:5000 with MANAGER_HOST=%s WORKER_HOSTS=%s DEBUG=%s",
        config.MANAGER_HOST,
        config.WORKER_HOSTS,
        config.DEBUG,
    )
    app.run(host="0.0.0.0", port=5000, debug=config.DEBUG)
