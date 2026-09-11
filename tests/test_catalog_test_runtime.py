import json
from pathlib import Path
import tempfile
import unittest

from scripts.catalog_test_runtime import (
    MARKER, TEST_REPOSITORY, config_text, expected_ids, generate, guard,
)
from watchtower.config import load_config


class CatalogTestRuntimeTests(unittest.TestCase):
    def test_generated_runtime_contains_exactly_all_catalog_setup_ids(self):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / "runtime"
            generate(root, "main", TEST_REPOSITORY)
            config_path, state_path = guard(root, "main", TEST_REPOSITORY)
            config = load_config(config_path)
            self.assertEqual(len(expected_ids()), len(config.sources))
            self.assertEqual(expected_ids(), {source.id for source in config.sources})
            self.assertEqual([], [path for path in state_path.iterdir() if path.name != ".gitkeep"])
            self.assertIn(MARKER, (root / "README.md").read_text(encoding="utf-8"))

    def test_main_or_unmarked_runtime_is_rejected(self):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / "runtime"
            generate(root, "main", TEST_REPOSITORY)
            with self.assertRaisesRegex(ValueError, "main ref"):
                guard(root, "catalog-test", TEST_REPOSITORY)
            with self.assertRaisesRegex(ValueError, "fixed test repository"):
                guard(root, "main", "example-owner/watchtower-runtime")
            (root / "README.md").write_text("ordinary runtime", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "marker"):
                guard(root, "main", TEST_REPOSITORY)

    def test_generator_refuses_existing_runtime(self):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / "runtime"
            root.mkdir()
            (root / "private.txt").write_text("preserve", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "new empty"):
                generate(root, "main", TEST_REPOSITORY)
            self.assertEqual("preserve", (root / "private.txt").read_text(encoding="utf-8"))

    def test_config_generation_contains_no_webhook_or_private_selection(self):
        content = config_text()
        self.assertNotIn("WEBHOOK", content.upper())
        self.assertNotIn("entity", content.lower())

    def test_delivery_workflow_is_ephemeral_and_uses_only_the_test_secret(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / ".github" / "workflows" / "catalog-delivery-test.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("WATCHTOWER_TEST_SLACK_WEBHOOK_URL", text)
        self.assertEqual(1, text.count("secrets."))
        self.assertNotIn("RUNTIME_DEPLOY_KEY", text)
        self.assertNotIn("WATCHTOWER_RUNTIME_REPOSITORY", text)
        self.assertNotIn("git push", text)
        self.assertNotIn("schedule:", text)
        self.assertIn("contents: read", text)
        self.assertIn("runner.temp", text)
        self.assertIn("example-owner/watchtower-test-runtime", text)
        self.assertIn("--runtime-repository", text)
        self.assertIn("--runtime-ref main", text)
        self.assertIn("codex/source-packs-expansion", text)
        self.assertIn('REPOSITORY_PRIVATE: ${{ github.event.repository.private }}', text)
        self.assertIn('os.environ["REPOSITORY"] != "example-owner/watchtower"', text)
        self.assertIn('os.environ["REPOSITORY_PRIVATE"] != "false"', text)
        self.assertIn('REPOSITORY_PRIVATE: ${{ github.event.repository.private }}', text)
        self.assertIn("fetch-depth: 2", text)
        self.assertIn('parents != [sha, before]', text)
        self.assertIn('changed != [request_name]', text)
        self.assertEqual(5, text.count("if: steps.gate.outputs.authorized == 'true'"))
        self.assertIn('value != {"operation": "notification-test"}', text)
        request = root / ".github" / "catalog-delivery-test-request.json"
        if request.exists():
            self.assertEqual(
                {"operation": "notification-test"},
                json.loads(request.read_text(encoding="utf-8")),
            )


if __name__ == "__main__":
    unittest.main()
