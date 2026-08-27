from __future__ import annotations

import tempfile
import unittest

from watchtower.config import Config, FilterRule, SourceConfig
from watchtower.engine import MAX_DETAILED_ALERTS_PER_RUN, evaluate, run
from watchtower.models import Item
from watchtower.state import StateStore


class StaticSource:
    def __init__(self, items: list[Item]) -> None:
        self.items = items

    def fetch_with_state(self, previous: dict | None) -> list[Item]:
        return list(self.items)


class RecordingNotifier:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.alert_batches: list[tuple] = []

    def send_text(self, text: str) -> None:
        self.texts.append(text)

    def send_alerts(self, alerts) -> None:
        self.alert_batches.append(tuple(alerts))


class AlertStormTests(unittest.TestCase):
    def source(self) -> SourceConfig:
        return SourceConfig(
            id="x",
            kind="regjeringen",
            label="Example source",
            filters=FilterRule(include_any=("alpha-rule",)),
            alert_on_update=True,
        )

    def item(self, key: str = "1", *, details: tuple[str, ...] = ()) -> Item:
        return Item(
            "x",
            key,
            f"Alpha-rule item {key}",
            f"https://example.test/{key}",
            alert_details=details,
        )

    def test_alert_details_do_not_change_canonical_hash(self) -> None:
        plain = self.item()
        detailed = self.item(details=("Presentation-only detail",))
        self.assertEqual(plain.content_hash(), detailed.content_hash())

    def test_transition_digest_migrates_without_alert(self) -> None:
        item = self.item(details=("Presentation-only detail",))
        canonical, transitional = item.compatible_content_hashes()
        self.assertNotEqual(canonical, transitional)
        previous = {
            "initialized": True,
            "updated_at": "old",
            "seen": {"1": transitional},
            "order": ["1"],
        }

        next_state, alerts, baseline = evaluate(
            self.source(),
            [item],
            previous,
            max_seen=100,
        )

        self.assertFalse(baseline)
        self.assertEqual([], alerts)
        self.assertEqual(canonical, next_state["seen"]["1"])

    def test_alert_storm_sends_one_summary(self) -> None:
        source = self.source()
        config = Config((source,), max_seen_per_source=100)
        items: list[Item] = []
        static_source = StaticSource(items)
        notifier = RecordingNotifier()

        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(tmp)
            factory = lambda _: static_source
            run(config, state, notifier, source_factory=factory)

            items.extend(
                self.item(str(index))
                for index in range(MAX_DETAILED_ALERTS_PER_RUN + 1)
            )
            result = run(config, state, notifier, source_factory=factory)

        self.assertEqual(MAX_DETAILED_ALERTS_PER_RUN + 1, result.alerts)
        self.assertEqual([], notifier.alert_batches)
        self.assertEqual(1, len(notifier.texts))
        self.assertIn("WATCHTOWER · SIKKERHETSSTOPP", notifier.texts[0])
        self.assertIn(
            f"{MAX_DETAILED_ALERTS_PER_RUN + 1} varsler",
            notifier.texts[0],
        )


if __name__ == "__main__":
    unittest.main()
