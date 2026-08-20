from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any
import requests

from ..config import SourceConfig
from ..models import Item


class SourceError(RuntimeError):
    pass


class Source(ABC):
    def __init__(self, config: SourceConfig, timeout: float = 30.0) -> None:
        self.config = config
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "watchtower/0.1 (+public-source-monitor)"})

    def get(self, url: str, **kwargs) -> requests.Response:
        try:
            response = self.session.get(url, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise SourceError(f"request failed for {self.config.id}") from exc
        if response.status_code != 200:
            raise SourceError(f"{self.config.id} returned HTTP {response.status_code}")
        return response

    def fetch_with_state(self, previous: dict[str, Any] | None) -> list[Item]:
        return self.fetch()

    @abstractmethod
    def fetch(self) -> list[Item]:
        raise NotImplementedError
