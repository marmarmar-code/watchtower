from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from watchtower.config import Config, FilterRule, SourceConfig
from watchtower.delivery import pending
from watchtower.engine import run
from watchtower.health import inspect_health, render_health
from watchtower.models import Item
from watchtower.state import StateStore


class Sender:
    def __init__(self, fail_at=None):
        self.calls = []
        self.fail_at = fail_at

    def send_alerts(self, entries):
        if len(self.calls) == self.fail_at:
            raise RuntimeError("simulated delivery failure")
        self.calls.append(tuple(entries))


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = StateStore(self.tmp.name)
        self.config = Config((SourceConfig(id="news", kind="rss", filters=FilterRule(match_all=True)),))
        self.source = Mock()
        self.source.augment_state.side_effect = lambda state: state
        self.source.coverage_warnings = []
        self.source.fetch_with_state.return_value = []
        self.factory = Mock(return_value=self.source)
        run(self.config, self.state, None, source_factory=self.factory)
        self.source.fetch_with_state.return_value = [
            Item("news", str(i), f"Synthetic item {i}", f"https://example.test/{i}") for i in range(9)
        ]

    def start_partial(self):
        sender = Sender(fail_at=1)
        with self.assertRaisesRegex(RuntimeError, "simulated"):
            run(self.config, self.state, sender, source_factory=self.factory)
        return sender

    def test_partial_send_resumes_only_remaining_batch_without_refetch(self):
        sender = self.start_partial()
        self.assertEqual([8], [len(batch) for batch in sender.calls])
        journal = pending(self.state)
        self.assertTrue(journal["batches"][0]["sent_at"])
        self.assertIsNone(journal["batches"][1]["sent_at"])
        self.assertEqual({}, self.state.load("news")["seen"])
        self.factory.reset_mock()
        restarted = Sender()
        result = run(self.config, self.state, restarted, source_factory=self.factory)
        self.factory.assert_not_called()
        self.assertEqual(9, result.alerts)
        self.assertEqual([1], [len(batch) for batch in restarted.calls])
        self.assertEqual(9, len(self.state.load("news")["seen"]))
        self.assertEqual(9, len(self.state.load("_alert_audit")["entries"]))
        self.assertIsNone(pending(self.state))
        run(self.config, self.state, restarted, source_factory=self.factory)
        self.assertEqual(1, len(restarted.calls))

    def test_crash_during_finalization_replays_receipts_without_duplicate_delivery(self):
        save = self.state.save
        def crash_clear(source_id, value):
            if source_id == "_outbox" and value.get("pending") is False:
                raise OSError("simulated disk failure")
            save(source_id, value)
        sender = Sender()
        with patch.object(self.state, "save", side_effect=crash_clear), self.assertRaises(OSError):
            run(self.config, self.state, sender, source_factory=self.factory)
        self.assertEqual(9, len(self.state.load("_alert_audit")["entries"]))
        health = inspect_health(self.config, self.state)
        self.assertEqual(0, health.pending_batches)
        self.assertTrue(health.pending_delivery)
        self.assertFalse(health.okay)
        restarted = Sender()
        run(self.config, self.state, restarted, source_factory=self.factory)
        self.assertEqual([], restarted.calls)
        self.assertEqual(9, len(self.state.load("_alert_audit")["entries"]))
        self.assertIsNone(pending(self.state))

    def test_dry_run_preserves_pending_journal_and_does_not_send(self):
        self.start_partial()
        def files():
            return {path.name: path.read_bytes() for path in Path(self.tmp.name).iterdir()}
        before = files()
        sender = Sender()
        run(self.config, self.state, sender, dry_run=True, source_factory=self.factory)
        self.assertEqual(before, files())
        self.assertEqual([], sender.calls)

    def test_provider_change_blocks_recovery_and_redacted_status_exposes_no_items(self):
        self.start_partial()
        altered = replace(self.config, notifications=replace(self.config.notifications, provider="teams"))
        with self.assertRaisesRegex(ValueError, "another provider"):
            run(altered, self.state, Sender(), source_factory=self.factory)
        output = render_health(inspect_health(self.config, self.state), redacted=True)
        self.assertIn("pending_batches=1", output)
        self.assertNotIn("Synthetic", output)
        self.assertNotIn("news", output)

    def test_invalid_journal_fails_before_any_delivery(self):
        self.state.save("_outbox", {"version": 99, "pending": True})
        sender = Sender()
        with self.assertRaisesRegex(ValueError, "journal"):
            run(self.config, self.state, sender, source_factory=self.factory)
        self.assertEqual([], sender.calls)


if __name__ == "__main__":
    unittest.main()
