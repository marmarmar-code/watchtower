from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.check_public_safety import find_problems
from watchtower.config import FilterRule, SourceConfig, load_config
from watchtower.engine import evaluate
from watchtower.models import Item
from watchtower.runtime_safety import validate_runtime


ROOT = Path(__file__).resolve().parents[1]


class DistributionTests(unittest.TestCase):
    def write_runtime(self, root: Path, config: str) -> None:
        (root / "config").mkdir()
        (root / "state").mkdir()
        (root / "config" / "watchtower.toml").write_text(config, encoding="utf-8")

    def test_disabled_placeholders_are_allowed_during_setup(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "watchtower.toml"
            config.write_text(
                '[[source]]\n'
                'id="example"\n'
                'kind="regjeringen"\n'
                'enabled=false\n'
                '[source.filter]\n'
                'include_any=["REPLACE_ME_TOPIC"]\n',
                encoding="utf-8",
            )
            parsed = load_config(config)
            self.assertFalse(parsed.sources[0].enabled)

    def test_enabled_placeholders_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "watchtower.toml"
            config.write_text(
                '[[source]]\n'
                'id="example"\n'
                'kind="regjeringen"\n'
                'enabled=true\n'
                '[source.filter]\n'
                'include_any=["REPLACE_ME_TOPIC"]\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "placeholder"):
                load_config(config)

    def test_enabled_source_requires_positive_filter_or_match_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "watchtower.toml"
            config.write_text(
                '[[source]]\n'
                'id="example"\n'
                'kind="regjeringen"\n'
                'enabled=true\n'
                '[source.filter]\n'
                'exclude_any=["ignore"]\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "requires include rules"):
                load_config(config)

    def test_match_all_is_explicit_and_respects_exclusions(self):
        rule = FilterRule(match_all=True, exclude_any=("blocked",))
        self.assertTrue(rule.matches("ordinary item"))
        self.assertFalse(rule.matches("blocked item"))

    def test_runtime_rejects_teams_workflow_webhook(self):
        webhook = (
            "https://prod-00.westeurope.logic."
            "azure.com/workflows/example/triggers/manual/paths/invoke?sig=secret"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_runtime(root, '[notifications]\nprovider="teams"\n')
            (root / "README.md").write_text(webhook, encoding="utf-8")
            problems = validate_runtime(root)
            self.assertTrue(any("secret-like content" in problem for problem in problems))

    def test_public_safety_rejects_power_platform_webhook(self):
        webhook = (
            "https://default-example.environment.api."
            "powerplatform.com/powerautomate/automations/direct/workflows/example/"
            "triggers/manual/paths/invoke?sig=secret"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "leak.txt").write_text(webhook, encoding="utf-8")
            problems = find_problems(root)
            self.assertEqual(["secret-like content: leak.txt"], problems)

    def test_runtime_rejects_unknown_source_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_runtime(
                root,
                '[[source]]\n'
                'id="example"\n'
                'kind="unsupported"\n'
                'enabled=false\n'
                '[source.filter]\n'
                'include_any=[]\n',
            )
            problems = validate_runtime(root)
            self.assertTrue(any("invalid source configuration" in problem for problem in problems))

    def test_item_fingerprint_is_independent_of_display_text(self):
        first = Item(
            "source",
            "stable-key",
            "Change from A to B",
            "https://example.test/1",
            fingerprint="state-b",
        )
        second = Item(
            "source",
            "stable-key",
            "State unchanged",
            "https://example.test/1",
            fingerprint="state-b",
        )
        self.assertEqual(first.content_hash(), second.content_hash())

    def test_suppressed_state_change_is_persisted_without_alert(self):
        source = SourceConfig(
            id="source",
            kind="regjeringen",
            filters=FilterRule(match_all=True),
        )
        old, _, _ = evaluate(
            source,
            [Item("source", "stable", "Initial", "https://example.test", fingerprint="a")],
            None,
            max_seen=100,
        )
        new, alerts, baseline = evaluate(
            source,
            [
                Item(
                    "source",
                    "stable",
                    "Internal state changed",
                    "https://example.test",
                    fingerprint="b",
                    suppress_alert=True,
                )
            ],
            old,
            max_seen=100,
        )
        self.assertFalse(baseline)
        self.assertEqual([], alerts)
        self.assertNotEqual(old["seen"], new["seen"])

    def test_workflow_uses_fork_local_runtime_default(self):
        workflow = (ROOT / ".github" / "workflows" / "monitor.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("WATCHTOWER_RUNTIME_REPOSITORY", workflow)
        self.assertIn("github.repository_owner", workflow)
        self.assertNotIn("repository: marmarmar-code/watchtower-runtime", workflow)
        self.assertIn("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", workflow)


if __name__ == "__main__":
    unittest.main()
