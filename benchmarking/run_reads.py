import os
import time
import requests

GATEKEEPER_URL = os.getenv("GATEKEEPER_URL")
API_TOKEN = os.getenv("API_TOKEN")
STRATEGY = os.getenv("STRATEGY", "direct")

if not GATEKEEPER_URL or not API_TOKEN:
    raise RuntimeError("GATEKEEPER_URL and API_TOKEN env vars required")

N = 1000  # number of operations
start = time.time()
success = 0
fail = 0

for i in range(N):
    payload = {
        "query": f"SELECT * FROM actor LIMIT 1;",
        "strategy": STRATEGY
    }
    headers = {"X-API-TOKEN": API_TOKEN}

    try:
        r = requests.post(GATEKEEPER_URL, json=payload, headers=headers, timeout=5)
        if r.status_code == 200:
            success += 1
        else:
            fail += 1
    except Exception:
        fail += 1

elapsed = time.time() - start
print("=== READ benchmark ===")
print(f"Strategy: {STRATEGY}")
print(f"Total requests: {N}")
print(f"Success: {success}")
print(f"Fail: {fail}")
print(f"Time: {elapsed:.2f}s")
print(f"Throughput: {N/elapsed:.2f} req/s")
