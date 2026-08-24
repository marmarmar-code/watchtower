from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET
from unittest.mock import Mock

import requests

from watchtower.config import FilterRule, SourceConfig
from watchtower.diagnostics import failure_detail
from watchtower.engine import evaluate
from watchtower.models import Item
from watchtower.sources.common import Source, SourceError
from watchtower.sources.stortinget import _stable_text


class StubSource(Source):
    def fetch(self) -> list[Item]:
        return []


def response(status: int, *, retry_after: str | None = None) -> requests.Response:
    value = requests.Response()
    value.status_code = status
    value._content = b"ok"
    value.raw = Mock()
    value.url = "https://example.test/source"
    if retry_after is not None:
        value.headers["Retry-After"] = retry_after
    return value


class ResilienceTests(unittest.TestCase):
    def source_config(self) -> SourceConfig:
        return SourceConfig(
            id="example-source",
            kind="regjeringen",
            filters=FilterRule(include_any=("alpha",)),
        )

    def test_transient_http_failure_is_retried(self):
        delays: list[float] = []
        source = StubSource(
            self.source_config(),
            retry_attempts=3,
            sleep=delays.append,
        )
        source.session.get = Mock(side_effect=[response(500), response(200)])

        result = source.get("https://example.test/source")

        self.assertEqual(200, result.status_code)
        self.assertEqual(2, source.session.get.call_count)
        self.assertEqual([1.0], delays)

    def test_retry_after_is_bounded_and_honoured(self):
        delays: list[float] = []
        source = StubSource(
            self.source_config(),
            retry_attempts=2,
            sleep=delays.append,
        )
        source.session.get = Mock(side_effect=[response(429, retry_after="60"), response(200)])

        source.get("https://example.test/source")

        self.assertEqual([30.0], delays)

    def test_permanent_http_failure_is_not_retried(self):
        delays: list[float] = []
        source = StubSource(
            self.source_config(),
            retry_attempts=3,
            sleep=delays.append,
        )
        source.session.get = Mock(return_value=response(404))

        with self.assertRaisesRegex(SourceError, "HTTP 404"):
            source.get("https://example.test/source")

        self.assertEqual(1, source.session.get.call_count)
        self.assertEqual([], delays)

    def test_shuffled_unchanged_items_do_not_rewrite_state(self):
        config = self.source_config()
        first = Item("example-source", "1", "Alpha one", "https://example.test/1")
        second = Item("example-source", "2", "Alpha two", "https://example.test/2")
        previous, _, _ = evaluate(config, [first, second], None, max_seen=100)

        current, alerts, baseline = evaluate(config, [second, first], previous, max_seen=100)

        self.assertFalse(baseline)
        self.assertEqual([], alerts)
        self.assertEqual(previous, current)

    def test_stortinget_text_is_stable_when_nested_xml_order_changes(self):
        first = ET.fromstring(
            """
            <sporsmal>
              <id>123</id>
              <tittel>Alpha question</tittel>
              <emne_liste><emne><navn>Media</navn></emne><emne><navn>Technology</navn></emne></emne_liste>
            </sporsmal>
            """
        )
        shuffled = ET.fromstring(
            """
            <sporsmal>
              <emne_liste><emne><navn>Technology</navn></emne><emne><navn>Media</navn></emne></emne_liste>
              <tittel>Alpha question</tittel>
              <id>123</id>
            </sporsmal>
            """
        )

        self.assertEqual(_stable_text(first), _stable_text(shuffled))
        self.assertIn("Alpha question", _stable_text(first))

    def test_real_content_update_moves_item_to_recent_end(self):
        config = self.source_config()
        first = Item("example-source", "1", "Alpha one", "https://example.test/1")
        second = Item("example-source", "2", "Alpha two", "https://example.test/2")
        previous, _, _ = evaluate(config, [first, second], None, max_seen=100)
        updated_first = Item(
            "example-source",
            "1",
            "Alpha one updated",
            "https://example.test/1",
        )

        current, alerts, _ = evaluate(config, [second, updated_first], previous, max_seen=100)

        self.assertEqual(["2", "1"], current["order"])
        self.assertEqual(1, len(alerts))
        self.assertEqual("updated", alerts[0].change)

    def test_failure_detail_exposes_source_and_status_only(self):
        detail = failure_detail(
            {
                "errors": {
                    "euronext": "SourceError: euronext returned HTTP 500",
                    "other": "ParserError: private and unnecessary detail",
                }
            }
        )

        self.assertEqual("euronext (HTTP 500), other (ParserError)", detail)
        self.assertNotIn("private", detail)


if __name__ == "__main__":
    unittest.main()
