from datetime import datetime, timezone
import json
import unittest
from unittest.mock import Mock

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.financial_decisions import FinancialDecisionsSource
from test_change_sources import poll


class FinancialDecisionTests(unittest.TestCase):
    def cfg(self, **options):
        return SourceConfig(id="finkn", kind="financial_decisions", label="FinKN",
                            filters=FilterRule(match_all=True), options=options)

    def response(self, payload=None, raw=None):
        data = raw if raw is not None else json.dumps(payload).encode()
        response = Mock(status_code=200, headers={}, iter_content=Mock(return_value=[data]))
        return response

    def row(self, **changes):
        now = datetime.now(timezone.utc).date().isoformat()
        return {"sPkStatementId": "2026-927", "sSummaryTitle": "Megleransvar",
                "dtPublishedDate": now + "T00:00:00", "dtNemndClosedDate": now + "T00:00:00",
                "sCompany": "Tryg Forsikring NUF", "sConclusion": "Avvist/ikke avgjort",
                "FullXML": "skal ikke lagres", "Fulltext": {"secret": True}, **changes}

    def source(self, payload, **options):
        source = FinancialDecisionsSource(self.cfg(company="Tryg Forsikring NUF", **options))
        source.post = Mock(return_value=self.response(payload))
        return source

    def test_request_window_and_public_fields_exclude_fulltext(self):
        source = self.source([self.row()])
        item = source.fetch()[0]
        request = source.post.call_args.kwargs
        self.assertEqual("Tryg Forsikring NUF", request["json"]["SelectedCompany"])
        self.assertIn("NemndClosedDateFrom", request["json"])
        self.assertIn("NemndClosedDateTo", request["json"])
        self.assertTrue(request["stream"])
        self.assertFalse(request["allow_redirects"])
        self.assertEqual("record:", item.key[:7])
        self.assertNotIn("FullXML", item.text)
        self.assertNotIn("Fulltext", item.text)
        source.post.return_value.close.assert_called_once()

    def test_initial_repeat_empty_and_timestamp_only_are_quiet(self):
        source = self.source([self.row()])
        state, alerts = poll(source)
        self.assertEqual([], alerts)
        _, alerts = poll(source, state)
        self.assertEqual([], alerts)
        changed = self.row(dtPublishedDate="2026-01-01T00:00:00")
        source.post.return_value = self.response([changed])
        _, alerts = poll(source, state)
        self.assertEqual([], alerts)
        empty = self.source([])
        _, alerts = poll(empty)
        self.assertEqual([], alerts)

    def test_substantive_change_has_norwegian_details(self):
        source = self.source([self.row()])
        state, _ = poll(source)
        source.post.return_value = self.response([self.row(sConclusion="Medhold")])
        _, alerts = poll(source, state)
        details = " ".join(alerts[0].item.alert_details)
        self.assertIn("Endret avgjørelse", details)
        self.assertIn("Konklusjon: Avvist/ikke avgjort → Medhold", details)
        self.assertNotIn("FullXML", details)

    def test_company_window_id_and_dates_are_strict(self):
        bad = [
            self.row(sPkStatementId="../../x"), self.row(sCompany="Annet AS"),
            self.row(dtPublishedDate="2026"), self.row(dtNemndClosedDate="2020-01-01T00:00:00"),
            self.row(sConclusion={}),
        ]
        for row in bad:
            with self.subTest(row=row), self.assertRaises(SourceError):
                self.source([row]).fetch()

    def test_duplicates_bounds_json_and_close_fail_closed(self):
        with self.assertRaises(SourceError): self.source([self.row(), self.row()]).fetch()
        source = FinancialDecisionsSource(self.cfg(company="X", max_bytes=1024))
        source.post = Mock(return_value=self.response(raw=b"x" * 1025))
        with self.assertRaisesRegex(SourceError, "max_bytes"): source.fetch()
        source.post.return_value.close.assert_called_once()
        source.post.return_value = self.response(raw=b"not-json")
        with self.assertRaisesRegex(SourceError, "invalid JSON"): source.fetch()
        source.post.return_value.close.assert_called_once()

    def test_configuration_rejects_ambiguous_or_removal_scope(self):
        for options in ({}, {"company": "X", "free_text": "2026-1"}, {"company": "X", "max_records": True},
                        {"company": "X", "complete_snapshot": True}, {"company": "X", "events": ["removed"]}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                FinancialDecisionsSource(self.cfg(**options))


if __name__ == "__main__": unittest.main()
