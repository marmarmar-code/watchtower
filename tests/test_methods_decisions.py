from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import Mock

from tests.test_change_sources import poll
from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.methods_decisions import PAGE_URL, MethodsDecisionsSource, _excel_date

PAGE = (Path(__file__).parent / "fixtures/event_sources/nye-metoder-page-2026-09-11.html").read_bytes()
BOOK = (Path(__file__).parent / "fixtures/event_sources/nye-metoder-decisions-2026-09-11.xlsx").read_bytes()


def config(**options):
    return SourceConfig(id="methods", kind="methods_decisions", label="Nye metoder", urls=(PAGE_URL,),
                        filters=FilterRule(match_all=True), options=options)


def response(raw):
    return Mock(status_code=200, headers={}, iter_content=Mock(return_value=[raw]), close=Mock())


class MethodsDecisionsTests(unittest.TestCase):
    def source(self, page=PAGE, book=BOOK, **options):
        source = MethodsDecisionsSource(config(**options))
        source.get = Mock(side_effect=[response(page), response(book)])
        return source

    def test_actual_workbook_discovers_current_link_and_preserves_separate_decisions(self):
        source = self.source(); rows = source.read_records()
        self.assertEqual(1004, len(rows)); self.assertEqual(1004, len({row["key"] for row in rows}))
        duplicates = [row for row in rows if row["method_id"] == "2013_036"]
        self.assertEqual(1, len(duplicates)); self.assertIn("\n\n---\n\n", duplicates[0]["fields"]["decision_text"])
        expanded = [row for row in rows if row["method_id"] == "2022_073"]
        self.assertEqual(2, len(expanded)); self.assertNotEqual(expanded[0]["key"], expanded[1]["key"])
        self.assertTrue(all(row["published"] is None for row in rows))
        self.assertTrue(source.get.call_args_list[1].args[0].endswith(".xlsx"))

    def test_repeat_is_quiet_and_decision_change_alert_is_norwegian(self):
        source = self.source(); state, alerts = poll(source); self.assertEqual([], alerts)
        source = self.source(); _, alerts = poll(source, state); self.assertEqual([], alerts)

    def test_page_link_is_unambiguous_and_official(self):
        for page in (b"<html></html>", b'<a href="a.xlsx">A</a><a href="b.xlsx">B</a>',
                     b'<a href="https://example.test/a.xlsx">A</a>'):
            with self.assertRaises(SourceError): self.source(page=page).read_records()

    def test_excel_serial_uses_declared_date_system_and_rejects_invalid(self):
        self.assertEqual("2019-11-18", _excel_date("43787").isoformat())
        self.assertEqual("2024-11-19", _excel_date("44153", date1904=True).isoformat())
        self.assertIsNone(_excel_date("0"))
        for value in (True, "date", "-1", "999999"):
            with self.assertRaises(SourceError): _excel_date(value)

    def test_bounds_config_and_removals_fail_closed(self):
        for options in ({"max_records": True}, {"max_unpacked_bytes": "3000000"},
                        {"events": ["removed"]}, {"complete_snapshot": True}):
            with self.assertRaises(ValueError): MethodsDecisionsSource(config(**options))
        wrong = SourceConfig(id="m", kind="methods_decisions", label="M", urls=("https://example.test",),
                             filters=FilterRule(match_all=True), options={})
        with self.assertRaises(ValueError): MethodsDecisionsSource(wrong)
        with self.assertRaises(SourceError): self.source(max_records=1000).read_records()


if __name__ == "__main__": unittest.main()
