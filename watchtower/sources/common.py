from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any
import time

import requests

from ..config import SourceConfig
from ..models import Item


_TRANSIENT_STATUS_CODES = {408, 425, 429}


class SourceError(RuntimeError):
    pass


def _is_retryable_status(status_code: int) -> bool:
    return status_code in _TRANSIENT_STATUS_CODES or 500 <= status_code < 600


def _retry_delay(response: requests.Response | None, attempt: int) -> float:
    if response is not None:
        raw = response.headers.get("Retry-After", "").strip()
        if raw:
            try:
                return min(max(float(raw), 0.0), 30.0)
            except ValueError:
                pass
    return float(min(attempt, 5))


class Source(ABC):
    def __init__(
        self,
        config: SourceConfig,
        timeout: float = 30.0,
        *,
        retry_attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.timeout = timeout
        self.retry_attempts = max(1, int(retry_attempts))
        self.sleep = sleep
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "watchtower/0.3 (+public-source-monitor)"})

    def get(
        self,
        url: str,
        *,
        accepted_statuses: tuple[int, ...] = (),
        **kwargs,
    ) -> requests.Response:
        for attempt in range(1, self.retry_attempts + 1):
            response: requests.Response | None = None
            try:
                response = self.session.get(url, timeout=self.timeout, **kwargs)
            except requests.RequestException as exc:
                if attempt >= self.retry_attempts:
                    raise SourceError(f"request failed for {self.config.id}") from exc
                self.sleep(_retry_delay(None, attempt))
                continue

            if response.status_code == 200 or response.status_code in accepted_statuses:
                return response

            if not _is_retryable_status(response.status_code) or attempt >= self.retry_attempts:
                raise SourceError(f"{self.config.id} returned HTTP {response.status_code}")

            delay = _retry_delay(response, attempt)
            response.close()
            self.sleep(delay)

        raise SourceError(f"request failed for {self.config.id}")

    def fetch_with_state(self, previous: dict[str, Any] | None) -> list[Item]:
        return self.fetch()

    def augment_state(self, state: dict[str, Any]) -> dict[str, Any]:
        """Add source-specific private state after generic item evaluation."""
        return state

    @abstractmethod
    def fetch(self) -> list[Item]:
        raise NotImplementedError
