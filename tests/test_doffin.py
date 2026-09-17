from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from watchtower.config import Config, FilterRule, SourceConfig
from watchtower.engine import evaluate, run
from watchtower.sources.common import SourceError
from watchtower.sources.doffin import DEFAULT_URL, DoffinSource, _item, _rows
from watchtower.state import StateStore


class DoffinTests(unittest.TestCase):
    def config(self, *, urls=()):
        return SourceConfig(
            id="doffin",
            kind="doffin",
            urls=urls,
            filters=FilterRule(match_all=True),
        )

    def test_custom_api_host_is_rejected_before_credentials_are_used(self):
        with self.assertRaisesRegex(ValueError, "official API URL"):
            DoffinSource(self.config(urls=("https://example.test/search",)))

    def test_explicit_official_api_url_is_accepted(self):
        source = DoffinSource(self.config(urls=(DEFAULT_URL,)))
        self.assertEqual(DEFAULT_URL, source.endpoint)

    def test_malformed_notice_row_fails_closed(self):
        with self.assertRaisesRegex(SourceError, "invalid row"):
            _rows({"hits": [{"id": "one"}, "not a notice"]})

    def test_notice_without_identity_or_title_fails_closed(self):
        with self.assertRaisesRegex(SourceError, "identity or title"):
            _item("doffin", {"id": "one"})

    @patch.dict(os.environ, {"DOFFIN_API_KEY": "test-key"})
    def test_credentialed_request_does_not_follow_redirects(self):
        response = Mock()
        response.json.return_value = {"hits": []}
        source = DoffinSource(self.config())
        source.get = Mock(return_value=response)

        self.assertEqual([], source.fetch())
        self.assertFalse(source.get.call_args.kwargs["allow_redirects"])

    def source_with_rows(self, rows, **options):
        source = DoffinSource(replace(self.config(), options=options))
        response = Mock()
        response.json.return_value = {"hits": rows}
        source.get = Mock(return_value=response)
        return source

    def poll(self, source, previous=None):
        items = source.fetch_with_state(previous)
        state, alerts, _ = evaluate(source.config, items, previous, max_seen=10000)
        return source.augment_state(state), alerts

    @patch.dict(os.environ, {"DOFFIN_API_KEY": "test-key"})
    def test_legacy_query_migration_is_quiet_preserves_history_and_future_alerts(self):
        source = self.source_with_rows([{"id": "historical", "title": "Example notice"}],
                                       search_queries=["example"])
        legacy = {"initialized": True, "seen": {"older": "saved-digest"}, "order": ["older"]}
        untouched = deepcopy(legacy)
        migrated, alerts = self.poll(source, legacy)
        self.assertEqual([], alerts)
        self.assertEqual(untouched, legacy)
        self.assertEqual("saved-digest", migrated["seen"]["older"])
        self.assertIn("historical", migrated["seen"])
        self.assertEqual(64, len(migrated["doffin_query_scope"]))
        repeated, alerts = self.poll(source, migrated)
        self.assertEqual([], alerts)
        self.assertEqual(migrated, repeated)
        source.get.return_value.json.return_value = {"hits": [
            {"id": "historical", "title": "Example notice"},
            {"id": "future", "title": "Future example notice"},
        ]}
        _, alerts = self.poll(source, migrated)
        self.assertEqual(["future"], [alert.item.key for alert in alerts])

    @patch.dict(os.environ, {"DOFFIN_API_KEY": "test-key"})
    def test_query_page_size_and_page_count_changes_each_migrate_quietly(self):
        first = self.source_with_rows([{"id": "old", "title": "Example notice"}],
                                      search_queries=["alpha"], page_size=20, max_pages=1)
        previous, _ = self.poll(first)
        for options in [
            {"search_queries": ["alpha", "beta"], "page_size": 20, "max_pages": 1},
            {"search_queries": ["alpha"], "page_size": 30, "max_pages": 1},
            {"search_queries": ["alpha"], "page_size": 20, "max_pages": 2},
        ]:
            source = self.source_with_rows([{"id": "exposed-history", "title": "Older notice"}], **options)
            with self.subTest(options=options):
                migrated, alerts = self.poll(source, previous)
                self.assertEqual([], alerts)
                self.assertNotEqual(previous["doffin_query_scope"], migrated["doffin_query_scope"])
                self.assertIn("old", migrated["seen"])
                self.assertIn("exposed-history", migrated["seen"])

    @patch.dict(os.environ, {"DOFFIN_API_KEY": "test-key"})
    def test_query_reordering_whitespace_and_effective_limits_do_not_remigrate(self):
        first = self.source_with_rows([{"id": "old", "title": "Old notice"}],
                                      search_queries=["alpha", "beta"], page_size=100, max_pages=5)
        previous, _ = self.poll(first)
        source = self.source_with_rows([{"id": "new", "title": "New notice"}],
                                       search_queries=[" beta ", "alpha", "alpha"], page_size=200, max_pages=10)
        current, alerts = self.poll(source, previous)
        self.assertEqual(previous["doffin_query_scope"], current["doffin_query_scope"])
        self.assertEqual(["new"], [alert.item.key for alert in alerts])

    @patch.dict(os.environ, {"DOFFIN_API_KEY": "test-key"})
    def test_partial_query_failure_does_not_commit_scope_or_seen_keys(self):
        source = self.source_with_rows([], search_queries=["alpha", "beta"])
        successful = Mock()
        successful.json.return_value = {"hits": [{"id": "historical", "title": "Older notice"}]}
        source.get.side_effect = [successful, SourceError("Example query unavailable")]
        previous = {"initialized": True, "seen": {"old": "saved"}, "order": ["old"]}
        with tempfile.TemporaryDirectory() as directory:
            state = StateStore(Path(directory))
            state.save(source.config.id, previous)
            result = run(Config((source.config,)), state, Mock(), source_factory=lambda _: source)
            self.assertIn(source.config.id, result.errors)
            self.assertEqual(previous, state.load(source.config.id))
            self.assertEqual(previous, source.augment_state(previous))

    @patch.dict(os.environ, {"DOFFIN_API_KEY": "test-key"})
    def test_empty_query_selection_cannot_establish_completed_scope(self):
        source = self.source_with_rows([], search_queries=[])
        with self.assertRaisesRegex(SourceError, "non-empty"):
            source.fetch_with_state({"initialized": True, "seen": {}, "order": []})
        self.assertEqual({}, source.augment_state({}))


if __name__ == "__main__":
    unittest.main()
