import os
from textwrap import dedent
import time
import statistics

from dotenv import load_dotenv
import boto3
from botocore.exceptions import WaiterError, ClientError

import requests
import matplotlib.pyplot as plt


# ============================================================
# LOG8415E - FINAL ASSIGNMENT
#
# This Python file is our automation script for the whole lab.
# When we run it, it will:
#
#   1) Create 3 t2.micro DB instances (1 manager + 2 workers)
#      - install MySQL + sysbench
#      - load Sakila
#      - run a baseline read-only benchmark
#
#   2) Create 1 t2.large Proxy instance (Trusted Host)
#      - run a Flask API on port 5000 (/sql endpoint)
#      - decide if a query is READ or WRITE
#      - implement 3 forwarding strategies:
#           * direct    : send all traffic (even READ) to manager
#           * random    : READ -> random worker   (default)
#           * customized: READ -> worker with lowest latency
#
#   3) Create 1 t2.large Gatekeeper instance
#      - only public entry point (internet-facing)
#      - checks token and query safety
#      - forwards queries to the Proxy, including the strategy
#
# We also enable a simple firewall (ufw) on each VM so that only
# the needed ports are open.
# ============================================================


# ================================
# 1. Load configuration from .env
# ================================
# We do NOT hard-code AWS credentials or IDs here.
# Instead, we load them from a .env file in the project folder.

load_dotenv()

# Region where all EC2 instances will be launched
REGION = os.getenv("AWS_DEFAULT_REGION")
if not REGION:
    raise RuntimeError(
        "AWS_DEFAULT_REGION is not set. Add it to your .env, "
        "e.g. AWS_DEFAULT_REGION=us-east-1"
    )

# AMI ID to use for all instances (Ubuntu-based image from the lab)
AMI_ID = os.getenv("LOG8415E_AMI_ID")
if not AMI_ID:
    raise RuntimeError(
        "LOG8415E_AMI_ID is not set. Add it to your .env, "
        "e.g. LOG8415E_AMI_ID=ami-xxxxxxxx"
    )

# Name of EC2 key pair (used later to SSH into instances)
KEY_NAME = os.getenv("LOG8415E_KEY_NAME")
if not KEY_NAME:
    raise RuntimeError(
        "LOG8415E_KEY_NAME is not set. Add it to your .env, "
        "e.g. LOG8415E_KEY_NAME=my-keypair"
    )


def ensure_security_group() -> str:
    """
    We do not create security groups from the script (lab IAM restrictions).
    Instead, we reuse an existing SG created manually in the AWS console.

    LOG8415E_SG_ID must be defined in .env and contain a valid sg-xxxx ID.
    """
    sg_id = os.getenv("LOG8415E_SG_ID")
    if not sg_id:
        raise RuntimeError(
            "LOG8415E_SG_ID is not set. Put your Security Group ID "
            "in .env as LOG8415E_SG_ID=sg-xxxx."
        )
    print(f"[INFO] Using existing security group from env: {sg_id}")
    return sg_id


# Instance types per role
DB_INSTANCE_TYPE = "t2.micro"
PROXY_INSTANCE_TYPE = "t2.large"
GATEKEEPER_INSTANCE_TYPE = "t2.large"

# Common project tag so we can filter resources in AWS console
TAG_PROJECT = "LOG8415E"

# Lab-only DB credentials (ok for a temporary environment)
MYSQL_ROOT_PASSWORD = "RootPass123!"
SB_USER = "sbuser"
SB_PASS = "sbpass"

# Simple shared secret used between client and Gatekeeper
GK_TOKEN = "SuperSecretToken123"


# =======================================
# 2. User-data: DB nodes (manager + workers)
# =======================================
# DB_USER_DATA is a bash script that will be executed automatically
# when each DB instance boots. This script:
#   - installs MySQL, sysbench and ufw
#   - imports Sakila
#   - creates a user for sysbench and the Proxy
#   - runs a baseline benchmark
#   - enables a firewall that only allows SSH and MySQL

DB_USER_DATA = dedent(f"""\
    #!/bin/bash
    set -xe
    exec > /var/log/user-data.log 2>&1

    echo "=== [BOOTSTRAP-DB] Updating packages and installing MySQL + sysbench ==="
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y mysql-server sysbench wget tar ufw

    echo "=== [BOOTSTRAP-DB] Securing MySQL root account ==="
    mysql -e "ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY '{MYSQL_ROOT_PASSWORD}'; FLUSH PRIVILEGES;"

    echo "=== [BOOTSTRAP-DB] Downloading Sakila sample database ==="
    cd /tmp
    wget https://downloads.mysql.com/docs/sakila-db.tar.gz
    tar xzf sakila-db.tar.gz

    echo "=== [BOOTSTRAP-DB] Importing Sakila schema and data ==="
    mysql -uroot -p'{MYSQL_ROOT_PASSWORD}' < sakila-db/sakila-schema.sql
    mysql -uroot -p'{MYSQL_ROOT_PASSWORD}' < sakila-db/sakila-data.sql

    echo "=== [BOOTSTRAP-DB] Creating sysbench / application user (accessible from VPC) ==="
    mysql -uroot -p'{MYSQL_ROOT_PASSWORD}' -e "CREATE USER IF NOT EXISTS '{SB_USER}'@'%' IDENTIFIED BY '{SB_PASS}';"
    mysql -uroot -p'{MYSQL_ROOT_PASSWORD}' -e "GRANT ALL PRIVILEGES ON sakila.* TO '{SB_USER}'@'%'; FLUSH PRIVILEGES;"

    echo "=== [BOOTSTRAP-DB] Preparing sysbench tables ==="
    sysbench /usr/share/sysbench/oltp_read_only.lua \\
        --mysql-db=sakila \\
        --mysql-user={SB_USER} \\
        --mysql-password={SB_PASS} \\
        prepare

    echo "=== [BOOTSTRAP-DB] Running baseline sysbench benchmark (read-only) ==="
    sysbench /usr/share/sysbench/oltp_read_only.lua \\
        --mysql-db=sakila \\
        --mysql-user={SB_USER} \\
        --mysql-password={SB_PASS} \\
        run | tee /var/log/sysbench_sakila_read_only.log

    echo "SYSBENCH_COMPLETED=1" > /var/log/sysbench_status

    echo "=== [BOOTSTRAP-DB] Enabling ufw firewall (allow SSH + MySQL) ==="
    ufw default deny incoming
    ufw default allow outgoing
    ufw allow 22/tcp       # SSH
    ufw allow 3306/tcp     # MySQL (Proxy / internal only; SG should restrict source)
    ufw --force enable

    echo "=== [BOOTSTRAP-DB] Finished user-data script ==="
""")


# =======================================
# 3. Proxy user-data (with 3 strategies + replication)
# =======================================
def build_proxy_userdata(manager_ip: str, worker1_ip: str, worker2_ip: str) -> str:
    """
    User-data script for the Proxy instance.
    Runs a Flask app on port 5000 that forwards SQL to manager/workers.
    Uses pip to install Flask + mysql-connector-python.
    """

    proxy_py = f"""from flask import Flask, request, jsonify
import random
import time
import mysql.connector

MANAGER = {{
    "host": "{manager_ip}",
    "user": "{SB_USER}",
    "password": "{SB_PASS}",
    "database": "sakila",
    "connection_timeout": 3,
}}

WORKERS = [
    {{
        "host": "{worker1_ip}",
        "user": "{SB_USER}",
        "password": "{SB_PASS}",
        "database": "sakila",
        "connection_timeout": 3,
    }},
    {{
        "host": "{worker2_ip}",
        "user": "{SB_USER}",
        "password": "{SB_PASS}",
        "database": "sakila",
        "connection_timeout": 3,
    }},
]

app = Flask(__name__)

def is_write_query(q: str) -> bool:
    q = q.strip().lower()
    return q.startswith((
        "insert", "update", "delete", "replace",
        "create", "alter", "drop"
    ))

def measure_worker_latency(cfg):
    start = time.perf_counter()
    conn = mysql.connector.connect(**cfg)
    cur = conn.cursor()
    cur.execute("SELECT 1")
    cur.fetchall()
    cur.close()
    conn.close()
    return time.perf_counter() - start

def choose_custom_worker():
    latencies = []
    for cfg in WORKERS:
        try:
            lat = measure_worker_latency(cfg)
        except Exception:
            lat = float("inf")
        latencies.append((lat, cfg))
    latencies.sort(key=lambda x: x[0])
    return latencies[0][1]

def replicate_write_to_workers(query: str):
    results = []
    for cfg in WORKERS:
        try:
            conn = mysql.connector.connect(**cfg)
            cur = conn.cursor()
            cur.execute(query)
            conn.commit()
            cur.close()
            conn.close()
            results.append({{"host": cfg["host"], "status": "OK"}})
        except Exception as e:
            results.append({{"host": cfg.get("host", "unknown"), "status": f"ERROR: {{e}}"}})
    return results

@app.route("/sql", methods=["POST"])
def route_sql():
    data = request.get_json(force=True)
    query = data.get("query", "")
    strategy = data.get("strategy", "auto")

    if not query:
        return jsonify(error="Missing 'query'"), 400

    lowered = query.lower()
    forbidden = ["drop table", "truncate", "shutdown"]
    if any(bad in lowered for bad in forbidden):
        return jsonify(error="Forbidden SQL command at proxy"), 403

    is_write = is_write_query(query)

    if is_write:
        cfg = MANAGER
    else:
        s = strategy.lower()
        if s == "direct":
            cfg = MANAGER
        elif s == "customized":
            cfg = choose_custom_worker()
        else:
            cfg = random.choice(WORKERS)

    try:
        conn = mysql.connector.connect(**cfg)
        cur = conn.cursor()
        cur.execute(query)

        if query.strip().lower().startswith("select"):
            rows = cur.fetchall()
            cur.close()
            conn.close()
            return jsonify(rows=rows)

        conn.commit()
        cur.close()
        conn.close()

        replication_info = None
        if is_write:
            replication_info = replicate_write_to_workers(query)

        return jsonify(status="OK", replication=replication_info)

    except Exception as e:
        return jsonify(error=str(e)), 500
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
"""

    return f"""#!/bin/bash
set -xe
exec > /var/log/proxy-user-data.log 2>&1

echo "=== [BOOTSTRAP-PROXY] Installing Python and dependencies via pip ==="
apt-get update -y
apt-get install -y python3 python3-pip ufw
python3 -m pip install --upgrade pip
python3 -m pip install flask mysql-connector-python

echo "=== [BOOTSTRAP-PROXY] Writing proxy_app.py ==="
cat > /home/ubuntu/proxy_app.py << 'EOF'
{proxy_py}
EOF

chown ubuntu:ubuntu /home/ubuntu/proxy_app.py

echo "=== [BOOTSTRAP-PROXY] Enabling ufw (SSH + port 5000 only) ==="
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 5000/tcp
ufw --force enable

echo "=== [BOOTSTRAP-PROXY] Starting proxy_app.py ==="
sudo -u ubuntu nohup python3 /home/ubuntu/proxy_app.py > /var/log/proxy_app.log 2>&1 &
echo "=== [BOOTSTRAP-PROXY] Proxy start command issued on port 5000 ==="
"""

# =======================================
# 4. Gatekeeper user-data (forwards strategy)
# =======================================
def build_gatekeeper_userdata(proxy_private_ip: str) -> str:
    """
    User-data for the Gatekeeper instance.

    Exposes /query on port 80 (public) and forwards to Proxy /sql on port 5000 (private).
    """

    gatekeeper_py = f"""from flask import Flask, request, jsonify
import requests

PROXY_HOST = "{proxy_private_ip}"
PROXY_URL = f"http://{{PROXY_HOST}}:5000/sql"
EXPECTED_TOKEN = "{GK_TOKEN}"

app = Flask(__name__)

@app.route("/query", methods=["POST"])
def handle_query():
    data = request.get_json(force=True)
    token = data.get("token")
    query = data.get("query", "")
    strategy = data.get("strategy", "auto")

    # 1) Simple authentication
    if token != EXPECTED_TOKEN:
        return jsonify(error="Unauthorized"), 401

    if not query:
        return jsonify(error="Missing 'query'"), 400

    lowered = query.lower()
    forbidden = ["drop table", "truncate", "shutdown"]
    if any(bad in lowered for bad in forbidden):
        return jsonify(error="Unsafe query blocked by gatekeeper"), 403

    try:
        resp = requests.post(PROXY_URL, json={{"query": query, "strategy": strategy}}, timeout=20)
        return jsonify(upstream_status=resp.status_code, upstream_body=resp.json())
    except Exception as e:
        return jsonify(error=str(e)), 500

if __name__ == "__main__":
    # Listen on port 80 so client can just call http://PUBLIC_IP/query
    app.run(host="0.0.0.0", port=80)
"""

    return f"""#!/bin/bash
set -xe
exec > /var/log/gatekeeper-user-data.log 2>&1

echo "=== [BOOTSTRAP-GK] Installing Python, Flask, requests via pip ==="
apt-get update -y
apt-get install -y python3 python3-pip ufw
python3 -m pip install --upgrade pip
python3 -m pip install flask requests

echo "=== [BOOTSTRAP-GK] Writing gatekeeper_app.py ==="
cat > /home/ubuntu/gatekeeper_app.py << 'EOF'
{gatekeeper_py}
EOF

chown ubuntu:ubuntu /home/ubuntu/gatekeeper_app.py

echo "=== [BOOTSTRAP-GK] Enabling ufw (SSH + 80 only) ==="
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw --force enable

echo "=== [BOOTSTRAP-GK] Starting gatekeeper_app.py as root (needed for port 80) ==="
nohup python3 /home/ubuntu/gatekeeper_app.py > /var/log/gatekeeper_app.log 2>&1 &
echo "=== [BOOTSTRAP-GK] Gatekeeper start command issued on port 80 ==="
"""


# =======================================
# 5. EC2 helpers
# =======================================
# We now define small helper functions that talk to the EC2 API
# using boto3 to launch instances and wait until they are ready.

ec2 = boto3.client("ec2", region_name=REGION)


def launch_ec2(name_tag: str, role_tag: str, instance_type: str, user_data: str) -> str:
    """
    Generic EC2 launcher used for DB, Proxy and Gatekeeper.

    We pass:
      - instance type
      - user-data script (bash)
      - tags (Name, Project, Role)
    """
    sg_id = ensure_security_group()
    print(f"[INFO] Launching instance {name_tag} ({role_tag}, type={instance_type})...")
    resp = ec2.run_instances(
        ImageId=AMI_ID,
        InstanceType=instance_type,
        KeyName=KEY_NAME,
        MinCount=1,
        MaxCount=1,
        SecurityGroupIds=[sg_id],
        UserData=user_data,
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "Name", "Value": name_tag},
                    {"Key": "Project", "Value": TAG_PROJECT},
                    {"Key": "Role", "Value": role_tag},
                ],
            }
        ],
    )
    instance = resp["Instances"][0]
    iid = instance["InstanceId"]
    print(f"[INFO] Launched instance {iid} ({name_tag}, role={role_tag})")
    return iid


def wait_for_instances(instance_ids):
    """
    Wait until instances are at least in 'running' state, then return details.
    If the waiter times out:
      - we check if any instance is shutting-down/terminated -> then we STOP.
      - otherwise we just log a warning and continue.
    """
    waiter = ec2.get_waiter("instance_running")
    print("[INFO] Waiting for instances to reach 'running' state...")

    try:
        waiter.wait(
            InstanceIds=instance_ids,
            WaiterConfig={"Delay": 15, "MaxAttempts": 10},
        )
        print("[INFO] Instances reached 'running' state.")
    except WaiterError as e:
        print("[WARN] Waiter instance_running timed out or failed:")
        print(f"       {e}")

        # Inspect instance states
        desc = ec2.describe_instances(InstanceIds=instance_ids)
        bad_states = []
        for reservation in desc.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                state = inst.get("State", {}).get("Name", "unknown")
                if state in ("shutting-down", "terminated"):
                    bad_states.append((inst["InstanceId"], state))

        if bad_states:
            print("[ERROR] Some instances are shutting down or terminated:")
            for iid, state in bad_states:
                print(f"   - {iid}: {state}")
            raise RuntimeError(
                "Instances in shutting-down/terminated state. Fix AWS limits/permissions and rerun."
            ) from e

        print("[WARN] Continuing anyway and reading current instance details.")

    # If we reach here, describe whatever we have
    desc = ec2.describe_instances(InstanceIds=instance_ids)
    rows = []
    for reservation in desc["Reservations"]:
        for inst in reservation["Instances"]:
            iid = inst["InstanceId"]
            public_ip = inst.get("PublicIpAddress", "N/A")
            private_ip = inst.get("PrivateIpAddress", "N/A")
            name = next((t["Value"] for t in inst.get("Tags", []) if t["Key"] == "Name"), "N/A")
            role = next((t["Value"] for t in inst.get("Tags", []) if t["Key"] == "Role"), "N/A")
            itype = inst.get("InstanceType", "N/A")
            state = inst.get("State", {}).get("Name", "unknown")
            reason = inst.get("StateTransitionReason") or inst.get("StateReason", {}).get("Message", "")

            rows.append((iid, name, role, itype, state, public_ip, private_ip, reason))

    print("\n=== Current instance states ===")
    print("InstanceId\tName\tRole\tType\tState\tPublicIP\tPrivateIP\tReason")
    for iid, name, role, itype, state, pub_ip, priv_ip, reason in rows:
        print(f"{iid}\t{name}\t{role}\t{itype}\t{state}\t{pub_ip}\t{priv_ip}\t{reason}")

    # Keep the old return shape (without state/reason)
    return [
        (iid, name, role, itype, pub_ip, priv_ip)
        for iid, name, role, itype, state, pub_ip, priv_ip, reason in rows
    ]


def get_private_ip(instance_id: str, max_retries: int = 10, delay: int = 6) -> str:
    """
    Return only the private IP of a single instance (used for Proxy/GK wiring).
    Retries if the instance is not yet visible (InvalidInstanceID.NotFound).
    """
    for attempt in range(max_retries):
        try:
            desc = ec2.describe_instances(InstanceIds=[instance_id])
            reservations = desc.get("Reservations", [])
            if not reservations or not reservations[0].get("Instances"):
                raise RuntimeError(f"No instance data returned for {instance_id}")
            inst = reservations[0]["Instances"][0]
            priv = inst.get("PrivateIpAddress")
            if not priv:
                raise RuntimeError(f"Instance {instance_id} has no private IP yet")
            return priv
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == "InvalidInstanceID.NotFound":
                print(f"[WARN] Instance {instance_id} not found yet. Retry {attempt+1}/{max_retries}")
                time.sleep(delay)
                continue
            # any other AWS error: re-raise
            raise

    raise RuntimeError(f"Instance {instance_id} was never found after {max_retries} retries")


def terminate_instances(instance_ids):
    """Terminate all EC2 instances passed in the list."""
    print("\n[INFO] Terminating instances:", instance_ids)
    try:
        ec2.terminate_instances(InstanceIds=instance_ids)
        print("[INFO] Termination command sent. They will shut down shortly.")
    except Exception as e:
        print("[ERROR] Could not terminate instances:", str(e))

# =======================================
# 6. Benchmark helpers (Section 4)
# =======================================

# How many requests per strategy
N_READ = 1000
N_WRITE = 1000

READ_QUERY = "SELECT * FROM actor LIMIT 10;"
WRITE_QUERY = "UPDATE actor SET last_name = last_name WHERE actor_id = 1;"

def wait_for_gatekeeper_ready(gatekeeper_url: str, token: str,
                              max_wait_s: int = 300, interval_s: int = 5):
    """
    Poll the Gatekeeper HTTP endpoint until it responds to a test request,
    or fail after max_wait_s seconds.
    """
    print("[INFO] Waiting for Gatekeeper HTTP service to become ready...")
    deadline = time.time() + max_wait_s
    attempt = 0

    # simple test payload – valid token + trivial query
    test_payload = {
        "token": token,
        "query": "SELECT 1",
        "strategy": "direct",
    }

    while time.time() < deadline:
        attempt += 1
        try:
            resp = requests.post(gatekeeper_url, json=test_payload, timeout=15)
            print(f"  [INFO] Probe attempt {attempt}: HTTP {resp.status_code}")
            # Any HTTP response means the Flask app is listening on port 5000
            return
        except requests.exceptions.RequestException as e:
            print(f"  [INFO] Gatekeeper not ready yet ({e}); retrying in {interval_s}s")
            time.sleep(interval_s)

    raise RuntimeError(
        f"Gatekeeper did not respond on {gatekeeper_url} within {max_wait_s} seconds"
    )

def send_request(gatekeeper_url: str, token: str, mode: str, strategy: str):
    """
    Send one request to the Gatekeeper and measure latency.
    mode: 'read' or 'write'
    strategy: 'direct' | 'random' | 'customized'
    """
    query = READ_QUERY if mode == "read" else WRITE_QUERY

    payload = {
        "token": token,
        "query": query,
        "strategy": strategy,
    }

    t0 = time.perf_counter()
    try:
        resp = requests.post(gatekeeper_url, json=payload, timeout=15)
    except requests.exceptions.RequestException as e:
        t1 = time.perf_counter()
        latency_ms = (t1 - t0) * 1000.0
        # Treat as a failed request, but do NOT crash the benchmark loop
        return False, latency_ms, f"Network error: {e}"

    t1 = time.perf_counter()
    latency_ms = (t1 - t0) * 1000.0

    ok = False
    err_msg = ""

    try:
        body = resp.json()
    except Exception as e:
        body = {}
        err_msg = f"Non-JSON response: {e}"

    if resp.status_code == 200:
        upstream_status = body.get("upstream_status", 500)
        if upstream_status == 200:
            ok = True
        else:
            err_msg = f"Upstream error: {body}"
    else:
        err_msg = f"HTTP {resp.status_code}: {body}"

    return ok, latency_ms, err_msg


def benchmark_mode(gatekeeper_url: str, token: str, strategy: str, mode: str, n_requests: int):
    """
    Run n_requests of (mode, strategy) and compute metrics.
    Returns a dict with aggregated stats and raw latencies.
    """
    print(f"\n[INFO] Benchmarking {mode.upper()} - strategy={strategy} ({n_requests} requests)")
    latencies = []
    errors = 0
    error_examples = set()

    start = time.perf_counter()
    for i in range(1, n_requests + 1):
        ok, lat_ms, err = send_request(gatekeeper_url, token, mode, strategy)
        latencies.append(lat_ms)
        if not ok:
            errors += 1
            if len(error_examples) < 3:
                error_examples.add(err)
        if i % 100 == 0:
            print(f"  ... {i}/{n_requests} done")
    end = time.perf_counter()

    success = n_requests - errors
    total_time = end - start
    avg_latency = statistics.mean(latencies)
    p95_latency = sorted(latencies)[int(0.95 * len(latencies)) - 1]
    throughput = success / total_time if total_time > 0 else 0.0

    result = {
        "strategy": strategy,
        "mode": mode,
        "requests": n_requests,
        "success": success,
        "errors": errors,
        "total_time_s": total_time,
        "avg_latency_ms": avg_latency,
        "p95_latency_ms": p95_latency,
        "throughput_req_s": throughput,
        "error_samples": list(error_examples),
        "latencies_ms": latencies,
    }
    return result


def save_results_csv(results, filename="benchmark_results.csv"):
    """Save aggregated benchmark stats to a CSV file for the report."""
    import csv

    fields = [
        "strategy",
        "mode",
        "requests",
        "success",
        "errors",
        "total_time_s",
        "avg_latency_ms",
        "p95_latency_ms",
        "throughput_req_s",
    ]
    with open(filename, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in results:
            writer.writerow({k: r[k] for k in fields})
    print(f"[INFO] Saved CSV results to {filename}")


def plot_results(results):
    """Create bar charts for latency and throughput and save as PNGs."""
    strategies = ["direct", "random", "customized"]
    modes = ["read", "write"]

    # Map (mode,strategy) -> result
    res_map = {(r["mode"], r["strategy"]): r for r in results}

    # Latency plots
    for mode in modes:
        vals = [res_map[(mode, s)]["avg_latency_ms"] for s in strategies]
        plt.figure()
        plt.bar(strategies, vals)
        plt.ylabel("Average latency (ms)")
        plt.title(f"Average latency – {mode.upper()} (1000 requests)")
        out = f"latency_{mode}.png"
        plt.savefig(out, bbox_inches="tight")
        plt.close()
        print(f"[INFO] Saved {out}")

    # Throughput plots
    for mode in modes:
        vals = [res_map[(mode, s)]["throughput_req_s"] for s in strategies]
        plt.figure()
        plt.bar(strategies, vals)
        plt.ylabel("Throughput (requests/sec)")
        plt.title(f"Throughput – {mode.upper()} (1000 requests)")
        out = f"throughput_{mode}.png"
        plt.savefig(out, bbox_inches="tight")
        plt.close()
        print(f"[INFO] Saved {out}")


def run_benchmarks(gatekeeper_url: str, token: str):
    """High-level: run all required benchmarks and return results list."""
    print("\n=== LOG8415E – Benchmarking cluster through Gatekeeper ===")
    print(f"[INFO] Gatekeeper URL : {gatekeeper_url}")
    print(f"[INFO] Token          : {token}")
    print(f"[INFO] Read requests  : {N_READ} per strategy")
    print(f"[INFO] Write requests : {N_WRITE} per strategy")

    strategies = ["direct", "random", "customized"]
    all_results = []

    for s in strategies:
        all_results.append(benchmark_mode(gatekeeper_url, token, s, "read", N_READ))
        all_results.append(benchmark_mode(gatekeeper_url, token, s, "write", N_WRITE))

    print("\n=== Aggregated benchmark results ===")
    print("strategy\tmode\trequests\tsuccess\terrors\tavg_ms\tp95_ms\tthroughput(req/s)")
    for r in all_results:
        print(
            f"{r['strategy']}\t{r['mode']}\t{r['requests']}\t"
            f"{r['success']}\t{r['errors']}\t"
            f"{r['avg_latency_ms']:.2f}\t{r['p95_latency_ms']:.2f}\t"
            f"{r['throughput_req_s']:.2f}"
        )

    save_results_csv(all_results)
    plot_results(all_results)

    return all_results


# =======================================
# 7. Main orchestration
# =======================================
def main():
    print("=== LOG8415E – Full cloud deployment (DB + Proxy + Gatekeeper + Benchmark) ===")
    print(f"[INFO] Region   : {REGION}")
    print(f"[INFO] AMI      : {AMI_ID}")
    print(f"[INFO] Key pair : {KEY_NAME}")

    # 1) Launch DB cluster (manager + 2 workers)
    manager_id = launch_ec2("mysql-manager-1", "manager", DB_INSTANCE_TYPE, DB_USER_DATA)
    worker1_id = launch_ec2("mysql-worker-1", "worker", DB_INSTANCE_TYPE, DB_USER_DATA)
    worker2_id = launch_ec2("mysql-worker-2", "worker", DB_INSTANCE_TYPE, DB_USER_DATA)

    db_ids = [manager_id, worker1_id, worker2_id]
    db_info = wait_for_instances(db_ids)

    roles_to_ip = {role: priv for (_iid, _name, role, _t, _pub, priv) in db_info}
    manager_priv = roles_to_ip.get("manager")
    worker_privs = [priv for (_iid, _name, role, _t, _pub, priv) in db_info if role == "worker"]
    worker1_priv, worker2_priv = worker_privs

    print("\n=== DB Instance summary (baseline ready) ===")
    print("InstanceId\tName\tRole\tType\tPublicIP\tPrivateIP")
    for iid, name, role, itype, pub_ip, priv_ip in db_info:
        print(f"{iid}\t{name}\t{role}\t{itype}\t{pub_ip}\t{priv_ip}")

    # 2) Launch Proxy (Trusted Host)
    proxy_user_data = build_proxy_userdata(manager_priv, worker1_priv, worker2_priv)
    proxy_id = launch_ec2("proxy-1", "proxy", PROXY_INSTANCE_TYPE, proxy_user_data)

    # Wait for the proxy to be running and visible before asking for its IP
    proxy_info = wait_for_instances([proxy_id])
    proxy_priv = proxy_info[0][5]  # tuple: (iid, name, role, type, pub_ip, priv_ip)

    # 3) Launch Gatekeeper (public entrypoint)
    gatekeeper_user_data = build_gatekeeper_userdata(proxy_priv)
    gatekeeper_id = launch_ec2("gatekeeper-1", "gatekeeper", GATEKEEPER_INSTANCE_TYPE, gatekeeper_user_data)

    extra_ids = [proxy_id, gatekeeper_id]
    extra_info = wait_for_instances(extra_ids)


    print("\n=== Proxy & Gatekeeper summary ===")
    print("InstanceId\tName\tRole\tType\tPublicIP\tPrivateIP")
    for iid, name, role, itype, pub_ip, priv_ip in extra_info:
        print(f"{iid}\t{name}\t{role}\t{itype}\t{pub_ip}\t{priv_ip}")

    # Determine Gatekeeper public IP for benchmarking
    gatekeeper_pub_ip = next(
        pub for (_iid, _name, role, _itype, pub, _priv) in extra_info if role == "gatekeeper"
    )
    gatekeeper_url = f"http://{gatekeeper_pub_ip}:80/query"

    # Wait until the Gatekeeper Flask app is actually listening on port 5000
    wait_for_gatekeeper_ready(gatekeeper_url, GK_TOKEN)

    # 4) Run automated benchmark (Section 4 requirement)
    run_benchmarks(gatekeeper_url, GK_TOKEN)


    print(
        "\n[INFO] Benchmark complete. CSV + PNG graphs are in the current folder.\n"
        "      You can directly use them in your report.\n"
    )

    # 5) Optional cleanup
    answer = input("Do you want to TERMINATE ALL INSTANCES now? (y/N): ").strip().lower()
    if answer == "y":
        all_ids = db_ids + extra_ids
        terminate_instances(all_ids)
    else:
        print("[INFO] Instances kept running. Remember to terminate them manually later!")


if __name__ == "__main__":
    main()