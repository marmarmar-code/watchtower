from __future__ import annotations

import time
from typing import Protocol

import requests


class Notifier(Protocol):
    def send(self, text: str) -> None: ...


class NotificationError(RuntimeError):
    pass


class SlackError(NotificationError):
    pass


class TeamsError(NotificationError):
    pass


class WebhookNotifier:
    provider_name = "Webhook"

    def __init__(self, webhook_url: str, timeout: float = 20.0) -> None:
        if not webhook_url.startswith("https://"):
            raise ValueError("webhook URL must use https")
        self.webhook_url = webhook_url
        self.timeout = timeout

    def payload(self, text: str) -> dict:
        return {"text": text}

    def send(self, text: str) -> None:
        last: Exception | None = None
        for attempt in range(3):
            try:
                response = requests.post(
                    self.webhook_url,
                    json=self.payload(text),
                    timeout=self.timeout,
                )
                if response.status_code == 429 or 500 <= response.status_code < 600:
                    time.sleep(attempt + 1)
                    continue
                if not 200 <= response.status_code < 300:
                    raise NotificationError(
                        f"{self.provider_name} returned HTTP {response.status_code}"
                    )
                return
            except requests.RequestException as exc:
                last = exc
                time.sleep(attempt + 1)
        raise NotificationError(f"{self.provider_name} notification failed") from last


class SlackNotifier(WebhookNotifier):
    provider_name = "Slack"

    def __init__(self, webhook_url: str, timeout: float = 20.0) -> None:
        if not webhook_url.startswith("https://hooks.slack.com/"):
            raise ValueError("invalid Slack webhook URL")
        super().__init__(webhook_url, timeout)

    def send(self, text: str) -> None:
        try:
            super().send(text)
        except NotificationError as exc:
            raise SlackError(str(exc)) from exc


class TeamsNotifier(WebhookNotifier):
    provider_name = "Microsoft Teams"

    def __init__(self, webhook_url: str, timeout: float = 20.0) -> None:
        if not webhook_url.startswith("https://"):
            raise ValueError("invalid Microsoft Teams webhook URL")
        super().__init__(webhook_url, timeout)

    def payload(self, text: str) -> dict:
        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.2",
                        "body": [
                            {
                                "type": "TextBlock",
                                "text": text,
                                "wrap": True,
                            }
                        ],
                    },
                }
            ],
        }

    def send(self, text: str) -> None:
        try:
            super().send(text)
        except NotificationError as exc:
            raise TeamsError(str(exc)) from exc


def build_notifier(provider: str, *, slack_url: str = "", teams_url: str = "") -> Notifier:
    normalized = provider.strip().lower()
    if normalized == "slack":
        return SlackNotifier(slack_url)
    if normalized == "teams":
        return TeamsNotifier(teams_url)
    raise ValueError(f"unsupported notification provider: {provider}")
