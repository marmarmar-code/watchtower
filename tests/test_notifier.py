from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from watchtower.notifier import SlackNotifier, TeamsNotifier, build_notifier


class NotifierTests(unittest.TestCase):
    def test_build_notifier_selects_slack(self):
        notifier = build_notifier("slack", slack_url="https://hooks.slack.com/services/example")
        self.assertIsInstance(notifier, SlackNotifier)

    def test_build_notifier_selects_teams(self):
        notifier = build_notifier("teams", teams_url="https://example.test/teams-webhook")
        self.assertIsInstance(notifier, TeamsNotifier)

    def test_teams_payload_is_adaptive_card(self):
        notifier = TeamsNotifier("https://example.test/teams-webhook")
        payload = notifier.payload("Watchtower test")
        self.assertEqual("message", payload["type"])
        attachment = payload["attachments"][0]
        self.assertEqual("application/vnd.microsoft.card.adaptive", attachment["contentType"])
        self.assertEqual("AdaptiveCard", attachment["content"]["type"])
        self.assertEqual("Watchtower test", attachment["content"]["body"][0]["text"])

    @patch("watchtower.notifier.requests.post")
    def test_teams_accepts_successful_2xx_response(self, post):
        response = Mock(status_code=202)
        post.return_value = response
        notifier = TeamsNotifier("https://example.test/teams-webhook")
        notifier.send("Watchtower test")
        post.assert_called_once()

    def test_slack_rejects_non_slack_webhook(self):
        with self.assertRaisesRegex(ValueError, "invalid Slack webhook URL"):
            SlackNotifier("https://example.test/not-slack")


if __name__ == "__main__":
    unittest.main()
