from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts.check_private_leaks import collect_protected_values
from watchtower.config import Config, FilterRule, SourceConfig, load_config
from watchtower.engine import run
from watchtower.health import inspect_health, render_health
from watchtower.models import Item
from watchtower.runtime_safety import validate_runtime
from watchtower.setup import PRESETS, make_config, write_runtime
from watchtower.sources.common import SourceError
from watchtower.sources.doffin import DoffinSource
from watchtower.sources.rss import RssSource
from watchtower.state import StateStore


ENTITY = '''
[[entity]]
id = "sample"
name = "Synthetic Enterprise"
orgnr = "123456785"
aliases = ["Synthetic Brand"]
isins = ["NO0000000000"]
'''


class EntityTests(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "watchtower.toml"
            path.write_text(text, encoding="utf-8")
            return load_config(path)

    def test_entity_name_and_alias_supplement_topics_and_respect_exclusions(self):
        config = self.load(ENTITY + '''
[[source]]
id = "news"
kind = "rss"
[source.filter]
entity_refs = ["sample"]
include_any = ["Synthetic Topic"]
exclude_any = ["Unwanted"]
''')
        rules = config.sources[0].filters
        for text in ("Synthetic Enterprise reported", "Synthetic Brand reported", "Synthetic Topic reported"):
            self.assertTrue(rules.matches(text))
        self.assertFalse(rules.matches("Unwanted Synthetic Brand"))
        self.assertFalse(rules.matches("Unrelated"))

    def test_shared_identifiers_resolve_for_each_supported_register(self):
        for kind, key, values in (
            ("brreg", "companies", ["123456785"]),
            ("finanstilsynet_registry", "companies", ["123456785"]),
            ("patentstyret", "companies", ["123456785"]),
            ("stotte", "recipient_orgnrs", ["123456785"]),
            ("finanstilsynet_short_sale", "isins", ["NO0000000000"]),
        ):
            with self.subTest(kind=kind):
                config = self.load(ENTITY + f'''
[[source]]
id = "register"
kind = "{kind}"
entity_refs = ["sample"]
[source.filter]
match_all = true
''')
                self.assertEqual(values, config.sources[0].options[key])

    def test_references_preserve_existing_legacy_selection(self):
        config = self.load(ENTITY + '''
[[source]]
id = "support"
kind = "stotte"
recipients = ["987654325"]
entity_refs = ["sample"]
[source.filter]
match_all = true
''')
        self.assertEqual(["987654325", "123456785"], config.sources[0].options["recipient_orgnrs"])

    def test_unknown_refs_invalid_identifiers_and_future_versions_fail(self):
        bad_configs = (
            ENTITY.replace('123456785', '123456789'),
            ENTITY.replace('id = "sample"', 'id = "../sample"'),
            '[general]\nconfig_version = 99\n',
            ENTITY + '[[source]]\nid="x"\nkind="rss"\n[source.filter]\nentity_refs=["missing"]\n',
        )
        for text in bad_configs:
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.load(text)

    def test_entities_are_included_in_public_leak_checks(self):
        import tomllib
        protected = collect_protected_values(tomllib.loads(ENTITY))
        self.assertTrue({"Synthetic Enterprise", "Synthetic Brand", "123456785", "NO0000000000"}.issubset(protected))


class SetupTests(unittest.TestCase):
    def test_all_presets_produce_valid_runtime_with_one_feed_per_source(self):
        for preset in PRESETS:
            with self.subTest(preset=preset), tempfile.TemporaryDirectory() as directory:
                target = write_runtime(Path(directory), make_config(
                    preset, ["Synthetic Topic"], ["123456785=Synthetic Enterprise"], "teams"
                ))
                self.assertEqual([], validate_runtime(directory))
                config = load_config(target)
                self.assertEqual("teams", config.notifications.provider)
                self.assertGreaterEqual(len(config.sources), 4)
                for source in config.sources:
                    if source.kind == "rss":
                        self.assertEqual(1, len(RssSource(source).feed_urls))

    def test_active_runtime_and_state_are_never_overwritten(self):
        content = make_config("general", ["Synthetic Topic"], [], "teams")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = write_runtime(root, content)
            before = target.read_bytes()
            with self.assertRaisesRegex(ValueError, "active"):
                write_runtime(root, content)
            self.assertEqual(before, target.read_bytes())
            (root / "state" / "existing.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "already has state"):
                write_runtime(root, content)

    def test_disabled_template_is_backed_up_before_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            target = root / "config" / "watchtower.toml"
            original = '[[source]]\nid="example"\nkind="brreg"\nenabled=false\n'
            target.write_text(original)
            write_runtime(root, make_config("general", ["Synthetic Topic"], [], "slack"))
            self.assertEqual(original, (root / "config" / "watchtower.before-setup.toml").read_text())
            self.assertEqual("slack", load_config(target).notifications.provider)

    def test_empty_selection_and_invalid_company_do_not_create_runtime(self):
        for topics, companies in (([], []), ([], ["123456789=Invalid"])):
            with self.assertRaises(ValueError):
                make_config("general", topics, companies, "teams")

    def test_private_values_are_toml_escaped(self):
        with tempfile.TemporaryDirectory() as directory:
            term = 'Synthetic "quoted"\nsecond line'
            target = write_runtime(Path(directory), make_config("general", [term], [], "teams"))
            self.assertIn(term, load_config(target).sources[0].filters.include_any)

    def test_symlink_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").symlink_to(root)
            with self.assertRaisesRegex(ValueError, "symbolic"):
                write_runtime(root, make_config("general", ["Synthetic Topic"], [], "teams"))


class CoverageTests(unittest.TestCase):
    def test_previous_error_is_not_erased_when_source_waits_for_its_interval(self):
        at = datetime(2026, 9, 8, tzinfo=timezone.utc)
        config = SourceConfig(id="waiting", kind="rss", filters=FilterRule(match_all=True))
        with tempfile.TemporaryDirectory() as directory:
            state = StateStore(directory)
            state.save("waiting", {
                "initialized": True, "seen": {}, "order": [],
                "last_checked_at": at.isoformat(),
            })
            state.save("_status", {"errors": {"waiting": "SourceError"}})
            factory = Mock()
            result = run(Config((config,)), state, None, source_factory=factory,
                         run_at=at + timedelta(minutes=5), respect_intervals=True)
            factory.assert_not_called()
            self.assertEqual({"waiting": "SourceError"}, result.errors)
            self.assertEqual(result.errors, state.load("_status")["errors"])

    def test_surge_keeps_complete_latest_batch_beyond_rolling_audit_limit(self):
        source_config = SourceConfig(id="news", kind="rss", filters=FilterRule(match_all=True))
        source = Mock()
        source.fetch_with_state.return_value = []
        source.augment_state.side_effect = lambda value: value
        source.coverage_warnings = []
        notifier = Mock()
        with tempfile.TemporaryDirectory() as directory:
            state = StateStore(directory)
            run(Config((source_config,)), state, notifier, source_factory=lambda _: source)
            source.fetch_with_state.return_value = [
                Item("news", str(index), "Synthetic item", f"https://example.test/{index}")
                for index in range(501)
            ]
            run(Config((source_config,)), state, notifier, source_factory=lambda _: source)
            latest = state.load("_latest_alerts")["entries"]
            self.assertEqual(501, len(latest))
            self.assertEqual(500, len(state.load("_alert_audit")["entries"]))
            self.assertTrue(all(entry["delivery"] == "summary" for entry in latest))
            self.assertEqual(501, len({entry["alert_id"] for entry in latest}))
            # Unchanged polls neither resend nor replace the last non-empty batch.
            result = run(Config((source_config,)), state, notifier, source_factory=lambda _: source)
            self.assertEqual(0, result.alerts)
            self.assertEqual(latest, state.load("_latest_alerts")["entries"])
            notifier.send.assert_called_once()

    @patch.dict(os.environ, {"DOFFIN_API_KEY": "synthetic-key"})
    def test_doffin_cap_reports_partial_window_without_discarding_hits(self):
        source = DoffinSource(SourceConfig(
            id="procurement", kind="doffin", options={"page_size": 1, "max_pages": 2},
        ))
        responses = []
        for key in ("one", "two"):
            response = Mock()
            response.json.return_value = {"hits": [{"id": key, "title": "Synthetic notice"}]}
            responses.append(response)
        source.get = Mock(side_effect=responses)
        items = source.fetch_with_state({"seen": {"older": "digest"}})
        self.assertEqual(["one", "two"], [item.key for item in items])
        self.assertEqual([1, 2], [call.kwargs["params"]["page"] for call in source.get.call_args_list])
        self.assertEqual(["result_window_full", "no_overlap_with_previous_window"], source.coverage_warnings)

    @patch.dict(os.environ, {"DOFFIN_API_KEY": "synthetic-key"})
    def test_doffin_short_final_page_clears_previous_warning(self):
        source = DoffinSource(SourceConfig(id="x", kind="doffin"))
        response = Mock()
        response.json.return_value = {"hits": []}
        source.get = Mock(return_value=response)
        source.coverage_warnings = ["result_window_full"]
        self.assertEqual([], source.fetch())
        self.assertEqual([], source.coverage_warnings)

    def test_legitimate_empty_feed_requires_opt_in_and_malformed_items_still_fail(self):
        config = SourceConfig(id="feed", kind="rss", urls=("https://example.test/feed",), options={"allow_empty": True})
        source = RssSource(config)
        source.get = Mock(return_value=Mock(content=b"<rss><channel /></rss>"))
        self.assertEqual([], source.fetch())
        for xml in (b"<rss/>", b"<rss><channel><item><title>Missing identity</title></item></channel></rss>"):
            source.get = Mock(return_value=Mock(content=xml))
            with self.assertRaises(SourceError):
                source.fetch()

    def test_warning_persists_across_skipped_runs_and_clears_after_full_fetch(self):
        at = datetime(2026, 9, 8, tzinfo=timezone.utc)
        source_config = SourceConfig(id="private-source", kind="rss", filters=FilterRule(match_all=True))
        source = Mock()
        source.fetch_with_state.return_value = []
        source.augment_state.side_effect = lambda value: value
        source.coverage_warnings = ["result_window_full"]
        with tempfile.TemporaryDirectory() as directory:
            state = StateStore(directory)
            run(Config((source_config,)), state, None, source_factory=lambda _: source, run_at=at)
            result = run(Config((source_config,)), state, None, source_factory=lambda _: source, run_at=at + timedelta(minutes=5), respect_intervals=True)
            self.assertEqual(0, result.checked_sources)
            self.assertIn("private-source", result.warnings)
            rendered = render_health(inspect_health(Config((source_config,)), state, at=at), redacted=True)
            self.assertIn("LIMITED COVERAGE", rendered)
            self.assertNotIn("private-source", rendered)
            source.coverage_warnings = []
            run(Config((source_config,)), state, None, source_factory=lambda _: source, run_at=at + timedelta(hours=1))
            self.assertEqual({}, state.load("_status")["warnings"])


if __name__ == "__main__":
    unittest.main()
