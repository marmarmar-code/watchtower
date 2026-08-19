from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from watchtower.config import FilterRule, SourceConfig, load_config
from watchtower.engine import evaluate, format_slack
from watchtower.models import Item
from watchtower.runtime_safety import validate_runtime
from watchtower.state import StateStore


class CoreTests(unittest.TestCase):
    def source(self, **kwargs):
        return SourceConfig(
            id="x", kind="regjeringen", label="Test",
            filters=kwargs.pop("filters", FilterRule(include_any=("media",))),
            **kwargs,
        )

    def item(self, title="Media endres", text=""):
        return Item("x", "1", title, "https://example.test/1", text=text)

    def test_filter_requires_private_match(self):
        rule = FilterRule(include_any=("mediestøtte", "NRK"), exclude_any=("kalender",))
        self.assertTrue(rule.matches("Ny ordning for mediestøtte"))
        self.assertFalse(rule.matches("NRK kalender"))
        self.assertFalse(rule.matches("Noe helt annet"))

    def test_first_run_is_silent_baseline(self):
        state, alerts, baseline = evaluate(self.source(), [self.item()], None, max_seen=100)
        self.assertTrue(baseline)
        self.assertEqual([], alerts)
        self.assertIn("1", state["seen"])

    def test_new_matching_item_alerts_after_baseline(self):
        old, _, _ = evaluate(self.source(), [self.item("Gammel media")], None, max_seen=100)
        items = [self.item("Gammel media"), Item("x", "2", "Ny media-sak", "https://example.test/2")]
        _, alerts, baseline = evaluate(self.source(), items, old, max_seen=100)
        self.assertFalse(baseline)
        self.assertEqual(1, len(alerts))
        self.assertEqual("new", alerts[0].change)

    def test_update_can_be_disabled(self):
        source = self.source(alert_on_update=False)
        old, _, _ = evaluate(source, [self.item("Media A")], None, max_seen=100)
        _, alerts, _ = evaluate(source, [self.item("Media B")], old, max_seen=100)
        self.assertEqual([], alerts)

    def test_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(tmp)
            store.save("abc", {"seen": {"1": "x"}})
            self.assertEqual({"seen": {"1": "x"}}, store.load("abc"))

    def test_runtime_safety_accepts_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "state").mkdir()
            (root / "config" / "watchtower.toml").write_text(
                '[[source]]\nid="x"\nkind="regjeringen"\n[source.filter]\ninclude_any=["media"]\n',
                encoding="utf-8",
            )
            self.assertEqual([], validate_runtime(root))

    def test_config_loads_private_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "c.toml"
            p.write_text(
                '[[source]]\nid="r"\nkind="regjeringen"\nlabel="R"\n[source.filter]\ninclude_any=["presse"]\n',
                encoding="utf-8",
            )
            cfg = load_config(p)
            self.assertEqual("presse", cfg.sources[0].filters.include_any[0])

    def test_slack_output_contains_source_and_link(self):
        old, _, _ = evaluate(self.source(), [self.item("Media A")], None, max_seen=100)
        _, alerts, _ = evaluate(self.source(), [self.item("Media B")], old, max_seen=100)
        text = format_slack(alerts)
        self.assertIn("WATCHTOWER", text)
        self.assertIn("https://example.test/1", text)


if __name__ == "__main__":
    unittest.main()
