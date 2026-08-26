from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Protocol
from urllib.parse import urlparse

import requests

from .models import NotificationEntry


MAX_ALERTS_PER_MESSAGE = 8
MAX_ALERT_TEXT_PER_MESSAGE = 12_000


class Notifier(Protocol):
    def send_text(self, text: str) -> None: ...

    def send_alerts(self, alerts: Sequence[NotificationEntry]) -> None: ...


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

    def text_payload(self, text: str) -> dict:
        return {"text": text}

    def _post(self, payload: dict) -> None:
        last: Exception | None = None
        for attempt in range(1, 4):
            try:
                response = requests.post(
                    self.webhook_url,
                    json=payload,
                    timeout=self.timeout,
                )
                if response.status_code == 429 or 500 <= response.status_code < 600:
                    if attempt < 3:
                        time.sleep(float(attempt))
                        continue
                    raise NotificationError(
                        f"{self.provider_name} remained unavailable "
                        f"(HTTP {response.status_code})"
                    )
                if not 200 <= response.status_code < 300:
                    raise NotificationError(
                        f"{self.provider_name} returned HTTP {response.status_code}"
                    )
                return
            except requests.RequestException as exc:
                last = exc
                if attempt < 3:
                    time.sleep(float(attempt))
                    continue
        raise NotificationError(f"{self.provider_name} notification failed") from last

    def send_text(self, text: str) -> None:
        self._post(self.text_payload(text))

    def send(self, text: str) -> None:
        """Backward-compatible alias for plain text notifications."""
        self.send_text(text)

    def send_alerts(self, alerts: Sequence[NotificationEntry]) -> None:
        raise NotImplementedError


class SlackNotifier(WebhookNotifier):
    provider_name = "Slack"

    def __init__(self, webhook_url: str, timeout: float = 20.0) -> None:
        parsed = urlparse(webhook_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"hooks.slack.com", "hooks.slack-gov.com"}
            or not parsed.path.startswith("/services/")
        ):
            raise ValueError("invalid Slack webhook URL")
        super().__init__(webhook_url, timeout)

    def send_text(self, text: str) -> None:
        try:
            super().send_text(text)
        except NotificationError as exc:
            raise SlackError(str(exc)) from exc

    def send_alerts(self, alerts: Sequence[NotificationEntry]) -> None:
        for batch in notification_batches(alerts):
            self.send_text(format_slack_entries(batch))


class TeamsNotifier(WebhookNotifier):
    provider_name = "Microsoft Teams"

    def __init__(self, webhook_url: str, timeout: float = 20.0) -> None:
        if not webhook_url.startswith("https://"):
            raise ValueError("invalid Microsoft Teams webhook URL")
        super().__init__(webhook_url, timeout)

    def text_payload(self, text: str) -> dict:
        return adaptive_card_payload(
            [
                {
                    "type": "TextBlock",
                    "text": text,
                    "wrap": True,
                }
            ]
        )

    def send_text(self, text: str) -> None:
        try:
            super().send_text(text)
        except NotificationError as exc:
            raise TeamsError(str(exc)) from exc

    def send_alerts(self, alerts: Sequence[NotificationEntry]) -> None:
        for batch in notification_batches(alerts):
            try:
                self._post(format_teams_payload(batch))
            except NotificationError as exc:
                raise TeamsError(str(exc)) from exc


def build_notifier(provider: str, *, slack_url: str = "", teams_url: str = "") -> Notifier:
    normalized = provider.strip().lower()
    if normalized == "slack":
        return SlackNotifier(slack_url)
    if normalized == "teams":
        return TeamsNotifier(teams_url)
    raise ValueError(f"unsupported notification provider: {provider}")


def notification_batches(
    alerts: Sequence[NotificationEntry],
    *,
    max_items: int = MAX_ALERTS_PER_MESSAGE,
    max_text: int = MAX_ALERT_TEXT_PER_MESSAGE,
) -> tuple[tuple[NotificationEntry, ...], ...]:
    if max_items < 1 or max_text < 1:
        raise ValueError("notification batch limits must be positive")

    batches: list[tuple[NotificationEntry, ...]] = []
    current: list[NotificationEntry] = []
    current_size = 0

    for alert in alerts:
        size = _entry_size(alert)
        if current and (len(current) >= max_items or current_size + size > max_text):
            batches.append(tuple(current))
            current = []
            current_size = 0
        current.append(alert)
        current_size += size

    if current:
        batches.append(tuple(current))
    return tuple(batches)


def format_slack_entries(alerts: Sequence[NotificationEntry]) -> str:
    blocks: list[str] = []
    for alert in alerts:
        lines = [
            f"*WATCHTOWER · {_slack_escape(alert.source_label.upper())} · "
            f"{_slack_escape(alert.status)}*",
            f"*{_slack_escape(alert.title)}*",
        ]
        lines.extend(f"• {_slack_escape(detail)}" for detail in alert.details)
        if alert.published:
            lines.append(f"Publisert: {_slack_escape(alert.published)}")
        if alert.matched_terms:
            lines.append("Treff: " + _slack_escape(", ".join(alert.matched_terms)))
        lines.append(f"<{alert.url}|Åpne kilden>")
        blocks.append("\n".join(lines))
    return "\n\n——————————\n\n".join(blocks)


def format_teams_payload(alerts: Sequence[NotificationEntry]) -> dict:
    body: list[dict] = []
    for index, alert in enumerate(alerts):
        header = {
            "type": "TextBlock",
            "text": f"WATCHTOWER · {alert.source_label.upper()} · {alert.status}",
            "weight": "Bolder",
            "size": "Medium",
            "wrap": True,
        }
        if index:
            header["separator"] = True
            header["spacing"] = "Large"
        body.append(header)
        body.append(
            {
                "type": "TextBlock",
                "text": alert.title,
                "weight": "Bolder",
                "wrap": True,
                "spacing": "Small",
            }
        )

        if alert.details:
            body.append(
                {
                    "type": "TextBlock",
                    "text": "\n".join(f"• {detail}" for detail in alert.details),
                    "wrap": True,
                    "spacing": "Small",
                }
            )

        facts = []
        if alert.published:
            facts.append({"title": "Publisert", "value": alert.published})
        if alert.matched_terms:
            facts.append({"title": "Treff", "value": ", ".join(alert.matched_terms)})
        if facts:
            body.append(
                {
                    "type": "FactSet",
                    "facts": facts,
                    "spacing": "Small",
                }
            )

        body.append(
            {
                "type": "ActionSet",
                "actions": [
                    {
                        "type": "Action.OpenUrl",
                        "title": "Åpne kilden",
                        "url": alert.url,
                    }
                ],
                "spacing": "Small",
            }
        )

    return adaptive_card_payload(body)


def adaptive_card_payload(body: list[dict]) -> dict:
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
                    "body": body,
                },
            }
        ],
    }


def _entry_size(alert: NotificationEntry) -> int:
    return sum(
        len(value)
        for value in (
            alert.source_label,
            alert.status,
            alert.title,
            alert.url,
            alert.published or "",
            ", ".join(alert.matched_terms),
            "\n".join(alert.details),
        )
    )


def _slack_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
