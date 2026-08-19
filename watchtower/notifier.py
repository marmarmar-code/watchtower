from __future__ import annotations

import time
import requests


class SlackError(RuntimeError):
    pass


class SlackNotifier:
    def __init__(self, webhook_url: str, timeout: float = 20.0) -> None:
        if not webhook_url.startswith("https://hooks.slack.com/"):
            raise ValueError("invalid Slack webhook URL")
        self.webhook_url = webhook_url
        self.timeout = timeout

    def send(self, text: str) -> None:
        last: Exception | None = None
        for attempt in range(3):
            try:
                response = requests.post(self.webhook_url, json={"text": text}, timeout=self.timeout)
                if response.status_code == 429 or 500 <= response.status_code < 600:
                    time.sleep(attempt + 1)
                    continue
                if response.status_code != 200:
                    raise SlackError(f"Slack returned HTTP {response.status_code}")
                return
            except requests.RequestException as exc:
                last = exc
                time.sleep(attempt + 1)
        raise SlackError("Slack notification failed") from last
