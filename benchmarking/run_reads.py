import os
import time
from collections import Counter

import requests

# SMALL NUMBER FOR DEBUGGING
TOTAL_REQUESTS = 5


def main():
    gatekeeper_url = os.getenv("GATEKEEPER_URL")
    api_token = os.getenv("API_TOKEN", "supersecret123")
    strategy = os.getenv("STRATEGY", "direct")

    if not gatekeeper_url:
        print("[ERROR] GATEKEEPER_URL env var is not set.")
        return

    print("=== READ benchmark ===")
    print(f"Strategy        : {strategy}")
    print(f"Gatekeeper URL  : {gatekeeper_url}")
    print(f"Using API_TOKEN : {api_token}")

    success = 0
    fail = 0
    first_error = None
    error_codes = Counter()

    payload = {
        "query": "SELECT * FROM film LIMIT 1;",
        "strategy": strategy,
    }

    headers = {
        "Content-Type": "application/json",
        "X-API-TOKEN": api_token,
    }

    start = time.time()
    for i in range(TOTAL_REQUESTS):
        try:
            resp = requests.post(
                gatekeeper_url, json=payload, headers=headers, timeout=15  
            )
            if resp.status_code == 200:
                success += 1
            else:
                fail += 1
                error_codes[resp.status_code] += 1
                if first_error is None:
                    first_error = f"Status {resp.status_code}: {resp.text}"
        except Exception as e:
            fail += 1
            error_codes["EXCEPTION"] += 1
            if first_error is None:
                first_error = f"Exception: {repr(e)}"

        if (i + 1) % 5 == 0:
            print(f"[DEBUG] Sent {i+1}/{TOTAL_REQUESTS} requests...")

    elapsed = time.time() - start
    throughput = TOTAL_REQUESTS / elapsed if elapsed > 0 else 0.0

    print("\n=== READ benchmark result ===")
    print(f"Total requests: {TOTAL_REQUESTS}")
    print(f"Success      : {success}")
    print(f"Fail         : {fail}")
    print(f"Time         : {elapsed:.2f}s")
    print(f"Throughput   : {throughput:.2f} req/s")

    if error_codes:
        print("\n=== Error status code distribution ===")
        for code, count in error_codes.items():
            print(f"  {code}: {count} times")

    if first_error:
        print("\n*** Example error from first failed request ***")
        print(first_error)


if __name__ == "__main__":
    main()
