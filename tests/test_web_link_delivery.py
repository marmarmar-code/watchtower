"""Synthetic regression tests; no HTTP or notification transport."""
from dataclasses import replace
import tempfile
import unittest
from unittest.mock import Mock

from watchtower.config import Config, FilterRule, SourceConfig
from watchtower.engine import Alert, _unique_web_link_alerts, run
from watchtower.models import Item
from watchtower.state import StateStore


class WebLinkDeliveryTests(unittest.TestCase):
    def alert(self, source_id, *, kind="web_links", change="new", title="Test article"):
        source = SourceConfig(source_id, kind, filters=FilterRule(match_all=True))
        item = Item(source_id, "record:one", title, "https://example.test/article")
        return Alert(source, item, change, ())

    def test_identical_new_links_keep_first_label_and_order(self):
        first, second = self.alert("all"), self.alert("topic")
        self.assertEqual([first], _unique_web_link_alerts([first, second]))

    def test_snapshot_fingerprints_ignore_only_source_id(self):
        first, second = self.alert("all"), self.alert("topic")
        first.item = replace(first.item, fingerprint="same-record")
        second.item = replace(second.item, fingerprint="same-record")
        self.assertEqual([first], _unique_web_link_alerts([first, second]))
        second.item = replace(second.item, fingerprint="changed-record")
        self.assertEqual([first, second], _unique_web_link_alerts([first, second]))

    def test_different_content_keys_urls_and_revisions_are_preserved(self):
        first = self.alert("all")
        for second in [self.alert("topic", title="Different"), self.alert("topic", change="updated")]:
            self.assertEqual([first, second], _unique_web_link_alerts([first, second]))
        for field in ["url", "key"]:
            second = self.alert("topic")
            second.item = replace(second.item, **{field: "https://example.test/other"})
            self.assertEqual([first, second], _unique_web_link_alerts([first, second]))

    def test_other_adapters_are_not_collapsed(self):
        alerts = [self.alert("a", kind="ssb_data"), self.alert("b", kind="ssb_data")]
        self.assertEqual(alerts, _unique_web_link_alerts(alerts))

    def test_delivery_keeps_all_histories_and_one_receipt(self):
        alerts = [self.alert("all"), self.alert("topic")]
        config = Config(tuple(a.source for a in alerts))
        sources = {}
        for alert in alerts:
            source = Mock()
            source.fetch_with_state.return_value = []
            source.coverage_warnings = []
            source.augment_state.side_effect = lambda value: value
            sources[alert.source.id] = source
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(tmp)
            factory = lambda source: sources[source.id]
            self.assertEqual(2, run(config, state, None, source_factory=factory).baselined_sources)
            for alert in alerts:
                sources[alert.source.id].fetch_with_state.return_value = [alert.item]
            self.assertEqual(1, run(config, state, None, dry_run=True, source_factory=factory).alerts)
            self.assertEqual({}, state.load("all")["seen"])
            sender = Mock(spec=["send"])
            self.assertEqual(1, run(config, state, sender, source_factory=factory).alerts)
            sender.send.assert_called_once()
            self.assertEqual(1, len(state.load("_alert_audit")["entries"]))
            for alert in alerts:
                self.assertEqual(alert.item.content_hash(), state.load(alert.source.id)["seen"][alert.item.key])
            self.assertEqual(0, run(config, state, sender, source_factory=factory).alerts)

    def test_filtered_primary_does_not_hide_topic_alert(self):
        alerts = [self.alert("all"), self.alert("topic")]
        config = Config((replace(alerts[0].source, filters=FilterRule(include_any=("absent",))), alerts[1].source))
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(tmp)
            for alert in alerts:
                state.save(alert.source.id, {"initialized": True, "seen": {}, "order": []})
            def factory(source):
                adapter = Mock()
                adapter.fetch_with_state.return_value = [next(a.item for a in alerts if a.source.id == source.id)]
                adapter.coverage_warnings = []
                adapter.augment_state.side_effect = lambda value: value
                return adapter
            sender = Mock(spec=["send"])
            self.assertEqual(1, run(config, state, sender, source_factory=factory).alerts)
            self.assertEqual("topic", state.load("_latest_alerts")["entries"][0]["source_id"])


if __name__ == "__main__":
    unittest.main()
