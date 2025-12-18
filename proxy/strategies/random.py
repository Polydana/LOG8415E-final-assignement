import random
from typing import List, Dict
from .base import BaseStrategy


class RandomChoiceStrategy(BaseStrategy):
    """
    Random strategy:
      - READ  -> randomly pick a worker
      - WRITE -> go to manager
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

        return random.choice(worker_hosts)
