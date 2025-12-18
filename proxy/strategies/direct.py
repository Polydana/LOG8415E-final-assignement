from typing import List, Dict
from .base import BaseStrategy


class DirectStrategy(BaseStrategy):
    """
    Direct hit: always forward to the manager node,
    regardless of READ/WRITE.
    """

    def choose_target(
        self,
        query_type: str,
        manager_host: str,
        worker_hosts: List[str],
        state: Dict,
    ) -> str:
        return manager_host
