from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from watchtower.models import NotificationEntry
from watchtower.notifier import (
    SlackNotifier,
    TeamsNotifier,
    build_notifier,
    format_slack_entries,
    format_teams_payload,
    notification_batches,
)


class NotifierTests(unittest.TestCase):
    def entry(self, index: int = 1) -> NotificationEntry:
        return NotificationEntry(
            source_label="Example source",
            status="NY",
            title=f"Example title {index}",
            url=f"https://example.test/items/{index}",
            published="2026-08-26T12:00:00Z",
            matched_terms=("example",),
        )

    def test_build_notifier_selects_slack(self):
        notifier = build_notifier("slack", slack_url="https://hooks.slack.com/services/example")
        self.assertIsInstance(notifier, SlackNotifier)

    def test_build_notifier_selects_teams(self):
        notifier = build_notifier("teams", teams_url="https://example.test/teams-webhook")
        self.assertIsInstance(notifier, TeamsNotifier)

    def test_teams_plain_text_payload_is_adaptive_card(self):
        notifier = TeamsNotifier("https://example.test/teams-webhook")
        payload = notifier.text_payload("Watchtower test")
        self.assertEqual("message", payload["type"])
        attachment = payload["attachments"][0]
        self.assertEqual("application/vnd.microsoft.card.adaptive", attachment["contentType"])
        self.assertEqual("AdaptiveCard", attachment["content"]["type"])
        self.assertEqual("Watchtower test", attachment["content"]["body"][0]["text"])

    def test_teams_alert_payload_has_native_link_action(self):
        payload = format_teams_payload((self.entry(),))
        body = payload["attachments"][0]["content"]["body"]
        action_set = next(row for row in body if row["type"] == "ActionSet")
        action = action_set["actions"][0]
        self.assertEqual("Action.OpenUrl", action["type"])
        self.assertEqual("Åpne kilden", action["title"])
        self.assertEqual("https://example.test/items/1", action["url"])
        self.assertNotIn("<https://", str(payload))

    def test_slack_alert_payload_keeps_slack_link_syntax(self):
        text = format_slack_entries((self.entry(),))
        self.assertIn("*WATCHTOWER · EXAMPLE SOURCE · NY*", text)
        self.assertIn("<https://example.test/items/1|Åpne kilden>", text)

    def test_notification_batches_are_bounded(self):
        entries = tuple(self.entry(index) for index in range(1, 10))
        batches = notification_batches(entries, max_items=8, max_text=100_000)
        self.assertEqual(2, len(batches))
        self.assertEqual(8, len(batches[0]))
        self.assertEqual(1, len(batches[1]))

    @patch("watchtower.notifier.requests.post")
    def test_teams_accepts_successful_2xx_response(self, post):
        post.return_value = Mock(status_code=202)
        notifier = TeamsNotifier("https://example.test/teams-webhook")
        notifier.send_text("Watchtower test")
        post.assert_called_once()

    @patch("watchtower.notifier.requests.post")
    def test_teams_sends_structured_alerts(self, post):
        post.return_value = Mock(status_code=202)
        notifier = TeamsNotifier("https://example.test/teams-webhook")
        notifier.send_alerts((self.entry(),))
        payload = post.call_args.kwargs["json"]
        self.assertEqual("message", payload["type"])
        self.assertIn("Action.OpenUrl", str(payload))

    def test_slack_rejects_non_slack_webhook(self):
        with self.assertRaisesRegex(ValueError, "invalid Slack webhook URL"):
            SlackNotifier("https://example.test/not-slack")


if __name__ == "__main__":
    unittest.main()
