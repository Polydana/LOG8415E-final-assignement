#!/usr/bin/env python3
"""
LOG8415E - Final Assignment
Full automation script:
  - Create MySQL manager + 2 workers
  - Configure replication
  - Deploy Proxy (Trusted Host)
  - Deploy Gatekeeper (public entry point)
  - Run read/write benchmarks for each proxy strategy
"""

import os
import time
import subprocess
import sys
import argparse
import boto3


import requests
from botocore.exceptions import ClientError

from dotenv import load_dotenv
load_dotenv()  # Load AWS_* and API_TOKEN from .env BEFORE touching boto3

parser = argparse.ArgumentParser(description="LOG8415E final deployment automation.")
parser.add_argument("--no-cleanup", action="store_true", help="Do not terminate instances at the end.")
args = parser.parse_args()

# This must match the token used in Gatekeeper user-data
API_TOKEN = os.getenv("API_TOKEN", "supersecret123")

# Optional: sanity check for AWS credentials (helps avoid 'Unable to locate credentials')
required_aws_vars = ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]
missing = [v for v in required_aws_vars if not os.getenv(v)]
if missing:
    raise RuntimeError(f"Missing AWS credentials in environment/.env: {', '.join(missing)}")

from aws import config as aws_config
from aws import ec2_utils
from aws import user_data


def wait_for_gatekeeper_http(base_url: str, timeout: int = 300) -> None:
    """
    Poll /health on the gatekeeper until it responds 200 OK or timeout.
    base_url should be like http://<public-ip>
    """
    url = base_url.rstrip("/") + "/health"
    start = time.time()
    while time.time() - start < timeout:
        try:
            print(f"[INFO] Checking Gatekeeper health at {url} ...")
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                print("[INFO] Gatekeeper is healthy and responding.")
                return
            else:
                print(f"[DEBUG] Non-200 response: {resp.status_code} {resp.text}")
        except Exception as e:
            print(f"[DEBUG] Health check failed: {e}")
        print("[INFO] Gatekeeper not ready yet, waiting 5s...")
        time.sleep(5)

    raise RuntimeError("Timed out waiting for Gatekeeper HTTP /health")


def run_benchmarks(gatekeeper_url: str) -> None:
    """
    Run 1000 READ and 1000 WRITE requests for each strategy
    by calling the benchmarking scripts via python -m.
    gatekeeper_url should be 'http://<public-ip>/sql'
    """
    strategies = ["direct", "random", "custom"]

    for strategy in strategies:
        print("\n" + "=" * 70)
        print(f"[BENCHMARK] Strategy = {strategy} (READS)")
        print("=" * 70)

        env = os.environ.copy()
        env["GATEKEEPER_URL"] = gatekeeper_url
        env["API_TOKEN"] = API_TOKEN
        env["STRATEGY"] = strategy

        # READ benchmark
        subprocess.run(
            [sys.executable, "-m", "benchmarking.run_reads"],
            env=env,
            check=False,
        )


        print("\n" + "=" * 70)
        print(f"[BENCHMARK] Strategy = {strategy} (WRITES)")
        print("=" * 70)

        # WRITE benchmark
        subprocess.run(
            [sys.executable, "-m", "benchmarking.run_writes"],
            env=env,
            check=False,
        )

def ensure_mysql_port_open():
    """
    Ensure that the security group used by all instances allows MySQL (3306)
    traffic between instances that share the same SG.
    """
    print("\n=== Ensuring security group allows MySQL (3306) between instances ===")
    ec2 = boto3.client("ec2", region_name=aws_config.REGION)

    try:
        ec2.authorize_security_group_ingress(
            GroupId=aws_config.SECURITY_GROUP_ID,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 3306,
                    "ToPort": 3306,
                    "UserIdGroupPairs": [
                        {"GroupId": aws_config.SECURITY_GROUP_ID}
                    ],
                }
            ],
        )
        print(f"[INFO] Opened port 3306 within SG {aws_config.SECURITY_GROUP_ID}.")
    except ClientError as e:
        # If the rule already exists, AWS throws InvalidPermission.Duplicate
        code = e.response.get("Error", {}).get("Code", "")
        if code == "InvalidPermission.Duplicate":
            print(f"[INFO] Port 3306 rule already exists on SG {aws_config.SECURITY_GROUP_ID}.")
        else:
            print(f"[WARN] Could not modify SG {aws_config.SECURITY_GROUP_ID}: {e}")




def main():
    print("=== LOG8415E – Full cloud deployment (DB + Proxy + Gatekeeper + Benchmark) ===")
    print(f"[INFO] Region        : {aws_config.REGION}")
    print(f"[INFO] AMI           : {aws_config.AMI_ID}")
    print(f"[INFO] Key pair      : {aws_config.KEY_NAME}")
    print(f"[INFO] Security group: {aws_config.SECURITY_GROUP_ID}")

    ensure_mysql_port_open()
    # --------------------------------------------------------------------------------
    # 1) Launch MySQL manager
    # --------------------------------------------------------------------------------
    print("\n=== Step 1: Launching MySQL manager ===")
    manager_user_data = user_data.render_mysql_manager_user_data()
    manager_id = ec2_utils.create_instance(
        name="mysql-manager-1",
        role="manager",
        instance_type=aws_config.INSTANCE_TYPE_MANAGER,
        user_data=manager_user_data,
    )

    # Wait for manager to be running and get its private IP
    ec2_utils.wait_for_instances([manager_id])
    manager_private_ip = ec2_utils.get_private_ip(manager_id)
    print(f"[INFO] Manager private IP: {manager_private_ip}")

    # --------------------------------------------------------------------------------
    # 2) Launch MySQL workers (replicas)
    # --------------------------------------------------------------------------------
    print("\n=== Step 2: Launching MySQL workers ===")
    worker_ids = []
    worker_private_ips = []

    # Here, we create exactly 2 workers: server-id 2 and 3
    for idx in range(2):
        server_id = 2 + idx
        name = f"mysql-worker-{idx + 1}"
        print(f"[INFO] Launching {name} with server-id={server_id} ...")

        worker_ud = user_data.render_mysql_worker_user_data(
            server_id=server_id,
            manager_private_ip=manager_private_ip,
        )
        wid = ec2_utils.create_instance(
            name=name,
            role="worker",
            instance_type=aws_config.INSTANCE_TYPE_WORKER,
            user_data=worker_ud,
        )
        worker_ids.append(wid)

    ec2_utils.wait_for_instances(worker_ids)

    for wid in worker_ids:
        ip = ec2_utils.get_private_ip(wid)
        worker_private_ips.append(ip)
        print(f"[INFO] Worker {wid} private IP: {ip}")

    # --------------------------------------------------------------------------------
    # 3) Launch Proxy (Trusted Host)
    # --------------------------------------------------------------------------------
    print("\n=== Step 3: Launching Proxy (Trusted Host) ===")
    proxy_user_data = user_data.render_proxy_user_data(
        manager_ip=manager_private_ip,
        worker_ips=worker_private_ips,
    )

    proxy_id = ec2_utils.create_instance(
        name="proxy-1",
        role="proxy",
        instance_type=aws_config.INSTANCE_TYPE_PROXY,
        user_data=proxy_user_data,
    )

    ec2_utils.wait_for_instances([proxy_id])
    proxy_private_ip = ec2_utils.get_private_ip(proxy_id)
    print(f"[INFO] Proxy private IP: {proxy_private_ip}")

    # --------------------------------------------------------------------------------
    # 4) Launch Gatekeeper (public entry point)
    # --------------------------------------------------------------------------------
    print("\n=== Step 4: Launching Gatekeeper ===")
    gatekeeper_user_data = user_data.render_gatekeeper_user_data(
        proxy_private_ip=proxy_private_ip
    )

    gatekeeper_id = ec2_utils.create_instance(
        name="gatekeeper-1",
        role="gatekeeper",
        instance_type=aws_config.INSTANCE_TYPE_GATEKEEPER,
        user_data=gatekeeper_user_data,
    )

    ec2_utils.wait_for_instances([gatekeeper_id])
    gatekeeper_public_ip = ec2_utils.get_public_ip(gatekeeper_id)
    if not gatekeeper_public_ip:
        raise RuntimeError("Gatekeeper does not have a public IP address.")

    # 👇 IMPORTANT: now using port 80 → base URL has NO :8080
    gatekeeper_base_url = f"http://{gatekeeper_public_ip}"
    gatekeeper_sql_url = gatekeeper_base_url + "/sql"

    print(f"[INFO] Gatekeeper public base URL: {gatekeeper_base_url}")
    print(f"[INFO] Gatekeeper /sql endpoint  : {gatekeeper_sql_url}")

    # --------------------------------------------------------------------------------
    # 5) Wait for Gatekeeper HTTP /health
    # --------------------------------------------------------------------------------
    print("\n=== Step 5: Waiting for Gatekeeper HTTP health ===")
    wait_for_gatekeeper_http(gatekeeper_base_url)

    # --------------------------------------------------------------------------------
    # 6) Run benchmarks via benchmarking scripts
    # --------------------------------------------------------------------------------
    print("\n=== Step 6: Running benchmarks (READ + WRITE, each strategy) ===")
    run_benchmarks(gatekeeper_sql_url)

    print("\n=== Deployment & Benchmarking Complete ===")
    print("[INFO] You now have:")
    print(f"  - Manager   : {manager_private_ip} (id={manager_id})")
    print(f"  - Workers   : {', '.join(worker_private_ips)} (ids={', '.join(worker_ids)})")
    print(f"  - Proxy     : {proxy_private_ip} (id={proxy_id})")
    print(f"  - Gatekeeper: {gatekeeper_public_ip} (id={gatekeeper_id})")
    print("[INFO] Benchmarks were executed against the Gatekeeper /sql endpoint.")
    print("[INFO] Check console output and logs on instances for detailed results.")

    # --------------------------------------------------------------------------------
    # 7) Automatic cleanup unless --no-cleanup was passed
    # --------------------------------------------------------------------------------
    all_ids = [manager_id] + worker_ids + [proxy_id] + [gatekeeper_id]

    if args.no_cleanup:
        print("[INFO] --no-cleanup passed. Instances will remain running.")
        print("[INFO] Make sure to terminate them manually in AWS to avoid charges.")
    else:
        print("[INFO] Terminating all EC2 instances automatically...")
        ec2_utils.terminate_instances(all_ids)
        print("[INFO] Termination requested. Verify status in AWS console.")


if __name__ == "__main__":
    try:
        main()
    except ClientError as e:
        print(f"[ERROR] AWS client error: {e}")
    except Exception as e:
        print(f"[ERROR] {e}")