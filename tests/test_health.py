from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from watchtower.config import Config, FilterRule, SourceConfig
from watchtower.health import inspect_health, render_health
from watchtower.state import StateStore


AT = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)


def config() -> Config:
    return Config(
        (
            SourceConfig(
                id="healthy",
                kind="rss",
                filters=FilterRule(match_all=True),
                options={"interval_minutes": 60},
            ),
            SourceConfig(
                id="late",
                kind="rss",
                filters=FilterRule(match_all=True),
                options={"interval_minutes": 60},
            ),
            SourceConfig(
                id="failed",
                kind="rss",
                filters=FilterRule(match_all=True),
                options={"interval_minutes": 60},
            ),
            SourceConfig(
                id="new",
                kind="rss",
                filters=FilterRule(match_all=True),
            ),
        )
    )


class HealthTests(unittest.TestCase):
    def test_health_is_derived_without_writing_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state = StateStore(directory)
            state.save("healthy", {"last_checked_at": "2026-08-27T09:10:00+00:00"})
            state.save("late", {"last_checked_at": "2026-08-27T08:30:00+00:00"})
            state.save("failed", {"last_checked_at": "2026-08-27T09:30:00+00:00"})
            state.save("_status", {"errors": {"failed": "SourceError"}})
            before = {
                path.name: path.read_bytes()
                for path in Path(directory).glob("*.json")
            }

            report = inspect_health(config(), state, at=AT)

            self.assertEqual(
                ["healthy", "late", "error", "not_started"],
                [entry.status for entry in report.entries],
            )
            self.assertFalse(report.okay)
            after = {
                path.name: path.read_bytes()
                for path in Path(directory).glob("*.json")
            }
            self.assertEqual(before, after)

    def test_redacted_output_contains_no_source_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            report = inspect_health(config(), StateStore(directory), at=AT)
            rendered = render_health(report, redacted=True)
            self.assertIn("enabled_sources=4", rendered)
            self.assertNotIn("healthy\t", rendered)
            self.assertNotIn("failed", rendered)

    def test_zero_enabled_sources_is_not_healthy(self):
        report = inspect_health(Config(()), StateStore("unused"), at=AT)
        self.assertFalse(report.okay)


if __name__ == "__main__":
    unittest.main()
