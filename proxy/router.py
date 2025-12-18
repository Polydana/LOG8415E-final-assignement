from typing import Dict

from . import config
from .strategies.base import BaseStrategy
from .strategies.direct import DirectStrategy
from .strategies.random_choice import RandomChoiceStrategy
from .strategies.latency_based import LatencyBasedStrategy


def classify_query(query: str) -> str:
    """
    Very simple READ/WRITE classifier based on first word.
    """
    first = query.strip().split(None, 1)[0].lower() if query.strip() else ""

    if first in ("select", "show", "describe", "explain"):
        return "read"
    else:
        # INSERT, UPDATE, DELETE, CREATE, DROP, etc.
        return "write"


class Router:
    def __init__(self):
        self.manager_host = config.MANAGER_HOST
        self.worker_hosts = config.WORKER_HOSTS

        self.state: Dict = {
            "worker_latencies": {},
        }

        self.strategies: Dict[str, BaseStrategy] = {
            "direct": DirectStrategy(),
            "random": RandomChoiceStrategy(),
            "custom": LatencyBasedStrategy(),
        }

    def get_strategy(self, name: str) -> BaseStrategy:
        return self.strategies.get(name, self.strategies[config.DEFAULT_STRATEGY])

    def choose_target(self, query: str, strategy_name: str) -> str:
        qtype = classify_query(query)
        strategy = self.get_strategy(strategy_name)
        return strategy.choose_target(
            query_type=qtype,
            manager_host=self.manager_host,
            worker_hosts=self.worker_hosts,
            state=self.state,
        )
