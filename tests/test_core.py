from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from watchtower.cli import result_exit_code
from watchtower.config import FilterRule, SourceConfig, load_config
from watchtower.engine import (
    SOURCE_TYPES,
    RunResult,
    _should_save_status,
    evaluate,
    format_slack,
)
from watchtower.models import Item
from watchtower.runtime_safety import validate_runtime
from watchtower.sources.doffin import _item as doffin_item
from watchtower.state import StateStore


class CoreTests(unittest.TestCase):
    def source(self, **kwargs):
        return SourceConfig(
            id="x", kind="regjeringen", label="Test",
            filters=kwargs.pop("filters", FilterRule(include_any=("alpha-rule",))),
            **kwargs,
        )

    def item(self, title="Alpha-rule endres", text=""):
        return Item("x", "1", title, "https://example.test/1", text=text)

    def test_filter_requires_private_match(self):
        rule = FilterRule(include_any=("alpha-rule", "bravo-rule"), exclude_any=("blocked-rule",))
        self.assertTrue(rule.matches("Ny sak om alpha-rule"))
        self.assertFalse(rule.matches("bravo-rule blocked-rule"))
        self.assertFalse(rule.matches("Noe helt annet"))

    def test_short_terms_default_to_whole_word_matching(self):
        rule = FilterRule(include_any=("QX",))
        self.assertTrue(rule.matches("Ny satsing på QX i redaksjonen"))
        self.assertTrue(rule.matches("QX-basert verktøy"))
        self.assertFalse(rule.matches("AQX-verktøy"))
        self.assertFalse(rule.matches("aqxsystem"))

    def test_explicit_substring_mode_preserves_legacy_matching(self):
        rule = FilterRule(include_any=("QX",), match_mode="substring")
        self.assertTrue(rule.matches("AQX-verktøy"))

    def test_first_run_is_silent_baseline(self):
        state, alerts, baseline = evaluate(self.source(), [self.item()], None, max_seen=100)
        self.assertTrue(baseline)
        self.assertEqual([], alerts)
        self.assertIn("1", state["seen"])

    def test_new_matching_item_alerts_after_baseline(self):
        old, _, _ = evaluate(self.source(), [self.item("Gammel alpha-rule")], None, max_seen=100)
        items = [self.item("Gammel alpha-rule"), Item("x", "2", "Ny alpha-rule-sak", "https://example.test/2")]
        _, alerts, baseline = evaluate(self.source(), items, old, max_seen=100)
        self.assertFalse(baseline)
        self.assertEqual(1, len(alerts))
        self.assertEqual("new", alerts[0].change)

    def test_unchanged_source_preserves_existing_state_timestamp(self):
        old, _, _ = evaluate(self.source(), [self.item()], None, max_seen=100)
        next_state, alerts, baseline = evaluate(self.source(), [self.item()], old, max_seen=100)
        self.assertFalse(baseline)
        self.assertEqual([], alerts)
        self.assertEqual(old, next_state)

    def test_update_can_be_disabled(self):
        source = self.source(alert_on_update=False)
        old, _, _ = evaluate(source, [self.item("Alpha-rule A")], None, max_seen=100)
        _, alerts, _ = evaluate(source, [self.item("Alpha-rule B")], old, max_seen=100)
        self.assertEqual([], alerts)

    def test_duplicate_key_does_not_create_false_update_alerts(self):
        source = self.source()
        first = self.item("Alpha-rule variant A")
        second = self.item("Alpha-rule variant B")
        old, baseline_alerts, baseline = evaluate(source, [first, second], None, max_seen=100)
        self.assertTrue(baseline)
        self.assertEqual([], baseline_alerts)

        next_state, alerts, next_baseline = evaluate(source, [first, second], old, max_seen=100)
        self.assertFalse(next_baseline)
        self.assertEqual([], alerts)
        self.assertEqual(old, next_state)

    def test_status_is_saved_on_change_or_new_utc_day(self):
        previous = {
            "last_run_at": "2026-08-21T08:00:00+00:00",
            "checked_sources": 6,
            "baselined_sources": 0,
            "alerts": 0,
            "errors": {},
        }
        same_day = dict(previous, last_run_at="2026-08-21T12:00:00+00:00")
        next_day = dict(previous, last_run_at="2026-08-22T00:05:00+00:00")
        changed = dict(same_day, errors={"example-source": "SourceError"})
        self.assertFalse(_should_save_status(previous, same_day))
        self.assertTrue(_should_save_status(previous, next_day))
        self.assertTrue(_should_save_status(previous, changed))

    def test_any_source_error_produces_nonzero_exit_code(self):
        healthy = RunResult(6, 0, 0, {})
        partial = RunResult(5, 0, 0, {"example-source": "SourceError"})
        self.assertEqual(0, result_exit_code(healthy))
        self.assertEqual(2, result_exit_code(partial))

    def test_doffin_production_hit_is_normalized_for_filtering(self):
        item = doffin_item("doffin", {
            "id": "2026-123456",
            "heading": "Synthetic procurement example",
            "description": "Alpha-rule procurement description",
            "buyer": [{"name": "Example Buyer"}],
            "publicationDate": "2026-08-20T09:00:00Z",
            "type": "COMPETITION",
            "status": "ACTIVE",
            "cpvCodes": ["00000000"],
            "doffinClassicUrl": "https://example.test/doffin/2026-123456",
        })
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual("2026-123456", item.key)
        self.assertEqual("Synthetic procurement example", item.title)
        self.assertEqual("2026-08-20T09:00:00Z", item.published)
        self.assertEqual("https://example.test/doffin/2026-123456", item.url)
        self.assertIn("Example Buyer", item.searchable_text())
        self.assertIn("Alpha-rule", item.searchable_text())

    def test_new_source_types_are_registered(self):
        self.assertIn("doffin", SOURCE_TYPES)
        self.assertIn("hoyesterett", SOURCE_TYPES)

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
                '[[source]]\nid="x"\nkind="regjeringen"\n[source.filter]\ninclude_any=["alpha-rule"]\n',
                encoding="utf-8",
            )
            self.assertEqual([], validate_runtime(root))

    def test_config_loads_private_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "c.toml"
            p.write_text(
                '[[source]]\nid="r"\nkind="regjeringen"\nlabel="R"\n[source.filter]\ninclude_any=["bravo-rule"]\n',
                encoding="utf-8",
            )
            cfg = load_config(p)
            self.assertEqual("bravo-rule", cfg.sources[0].filters.include_any[0])
            self.assertEqual("smart", cfg.sources[0].filters.match_mode)

    def test_config_can_require_whole_word_matching(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "c.toml"
            p.write_text(
                '[[source]]\nid="r"\nkind="regjeringen"\n[source.filter]\nmatch_mode="whole_word"\ninclude_any=["alpha-rule"]\n',
                encoding="utf-8",
            )
            cfg = load_config(p)
            self.assertEqual("whole_word", cfg.sources[0].filters.match_mode)

    def test_slack_output_contains_source_and_link(self):
        old, _, _ = evaluate(self.source(), [self.item("Alpha-rule A")], None, max_seen=100)
        _, alerts, _ = evaluate(self.source(), [self.item("Alpha-rule B")], old, max_seen=100)
        text = format_slack(alerts)
        self.assertIn("WATCHTOWER", text)
        self.assertIn("https://example.test/1", text)


if __name__ == "__main__":
    unittest.main()
