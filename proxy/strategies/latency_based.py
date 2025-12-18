from typing import List, Dict
from .base import BaseStrategy
from ..utils.ping import ping_host


class LatencyBasedStrategy(BaseStrategy):
    """
    Custom strategy:
      - WRITE -> manager
      - READ  -> choose worker with lowest ping
    """

    def choose_target(
        self,
        query_type: str,
        manager_host: str,
        worker_hosts: List[str],
        state: Dict,
    ) -> str:
        if query_type == "write" or not worker_hosts:
            return manager_host

        # state["worker_latencies"] is a dict: {host: latency_ms}
        latencies = state.get("worker_latencies", {})

        # If we don't have latencies yet, measure them now
        if not latencies:
            latencies = {}
            for w in worker_hosts:
                latency = ping_host(w)
                if latency is not None:
                    latencies[w] = latency

            state["worker_latencies"] = latencies

        if not latencies:
            # If we couldn't ping any worker, fallback to manager
            return manager_host

        # Choose worker with lowest latency
        best_worker = min(latencies, key=latencies.get)
        return best_worker
