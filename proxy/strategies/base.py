from abc import ABC, abstractmethod
from typing import List, Dict


class BaseStrategy(ABC):
    """
    Strategy interface. All strategies must implement choose_target().
    """

    @abstractmethod
    def choose_target(
        self,
        query_type: str,
        manager_host: str,
        worker_hosts: List[str],
        state: Dict,
    ) -> str:
        """
        :param query_type: "read" or "write"
        :param manager_host: manager node host/IP
        :param worker_hosts: list of worker node hosts
        :param state: shared state (e.g., ping times)
        :return: host/IP string to send the query to
        """
        pass
