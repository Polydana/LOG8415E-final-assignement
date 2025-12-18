import subprocess
import re
from typing import Optional


def ping_host(host: str, timeout: int = 2) -> Optional[float]:
    """
    Ping a host once and return latency in ms, or None if it fails.
    Works on Linux.
    """
    try:
        # -c 1 = 1 packet, -W timeout in seconds (Linux)
        result = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout), host],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            return None

        # Look for "time=XX ms" in output
        match = re.search(r"time=([\d\.]+)\s*ms", result.stdout)
        if match:
            return float(match.group(1))

        return None
    except Exception:
        return None
