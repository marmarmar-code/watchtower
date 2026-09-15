from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.eu_merger_decisions import EUMergerDecisionsSource, API_BASE, _records, _type_text
from tests.test_change_sources import poll


def document(ref=None):
    today = datetime.now(timezone.utc).date().isoformat() + "T00:00:00.000+0000"
    return {"metadata": {"esST_REFERENCE": [ref or "M.12345-DEC{12345678-1234-1234-1234-123456789012}"],
            "caseNumber": ["M.12345"], "caseTitle": ["Example merger"], "caseInstrument": ["M"],
            "caseLastDecisionDate": [today], "decisionAdoptionDate": ["2026-08-01T00:00:00.000+0000"],
            "decisionTypes": ["DecisionType20310"], "esST_DATASOURCE": ["CS_PROD_ODSE_PROD"]}}


def payload(rows):
    return {"totalResults": len(rows), "pageNumber": 1, "pageSize": 100, "results": rows, "warnings": []}


def response(value):
    raw = value if isinstance(value, bytes) else json.dumps(value).encode()
    return Mock(status_code=200, iter_content=Mock(return_value=[raw]), close=Mock())


def source(**options):
    config = SourceConfig(id="eu_merger", kind="eu_merger_decisions", label="EU merger decisions",
                          urls=(API_BASE,), filters=FilterRule(match_all=True), options=options)
    result = EUMergerDecisionsSource(config)
    result.get = Mock(return_value=response({"modules": {"odse": {"cs": {"apikey": "public-client-test"}}}}))
    return result


class MergerDecisionTests(unittest.TestCase):
    def test_full_window_validates_then_selects_decision_and_uses_case_link(self):
        s = source()
        s.post = Mock(return_value=response(payload([document(), document("M.12345-ATT1"), document("M.12345")])))
        rows = s.read_records()
        self.assertEqual(1, len(rows))
        self.assertEqual("https://competition-cases.ec.europa.eu/cases/M.12345", rows[0]["url"])
        self.assertEqual("2026-08-01", rows[0]["published"])
        self.assertFalse(s.post.call_args.kwargs["allow_redirects"])
        self.assertEqual(100, s.post.call_args.kwargs["params"]["pageSize"])
        self.assertNotIn("public-client-test", repr(rows))
        s.post.return_value.close.assert_called_once()

    def test_baseline_repeat_metadata_noise_and_substantive_revision(self):
        s = source(); original = document()
        s.post = Mock(return_value=response(payload([original])))
        state, alerts = poll(s); self.assertEqual([], alerts)
        changed = deepcopy(original)
        changed["metadata"]["caseTitle"] = ["Corrected case title"]
        yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
        changed["metadata"]["caseLastDecisionDate"] = [yesterday.isoformat() + "T00:00:00.000+0000"]
        s.post.return_value = response(payload([changed]))
        state, alerts = poll(s, state); self.assertEqual([], alerts)
        changed["metadata"]["decisionTypes"] = ["DecisionType201316"]
        s.post.return_value = response(payload([changed]))
        _, alerts = poll(s, state)
        self.assertEqual(1, len(alerts))
        self.assertIn("Vedtakstype", " ".join(alerts[0].item.alert_details))
        self.assertIn("139/2004 Art. 4(5) referral", " ".join(alerts[0].item.alert_details))
        self.assertEqual("DecisionType999", _type_text(["DecisionType999"]))

    def test_incomplete_total_warning_duplicate_and_wrong_scope_fail_closed(self):
        bad = []
        for key, value in [("totalResults", 101), ("totalResults", -1), ("pageNumber", True),
                           ("pageSize", 50), ("warnings", ["partial"]), ("results", [])]:
            p = payload([document()]); p[key] = value; bad.append(p)
        bad.append(payload([document(), document()]))
        for key, value in [("caseInstrument", ["AT"]), ("caseNumber", ["M.999"]),
                           ("caseLastDecisionDate", ["2000-01-01T00:00:00.000+0000"]),
                           ("esST_REFERENCE", ["M.12345-unknown"]),
                           ("decisionAdoptionDate", ["2026-02-30T00:00:00.000+0000"]),
                           ("decisionTypes", []), ("decisionTypes", ["DecisionType1", "DecisionType1"]),
                           ("esST_DATASOURCE", ["other"])]:
            row = document(); row["metadata"][key] = value; bad.append(payload([row]))
        for p in bad:
            s = source(); s.post = Mock(return_value=response(p))
            with self.subTest(payload=p), self.assertRaises(SourceError):
                s.read_records()

    def test_decision_attachment_and_parent_coalesce_without_rebaseline(self):
        s = source(); parent = document(); attached = deepcopy(parent)
        attached["metadata"]["esST_REFERENCE"][0] += "-ATT71"
        s.post = Mock(return_value=response(payload([attached])))
        state, alerts = poll(s); self.assertEqual([], alerts)
        s.post.return_value = response(payload([parent, attached]))
        _, alerts = poll(s, state); self.assertEqual([], alerts)
        self.assertEqual(1, len(s._next["rows"]))
        attached["metadata"]["decisionTypes"] = ["DecisionType999"]
        s.post.return_value = response(payload([parent, attached]))
        with self.assertRaisesRegex(SourceError, "disagree"):
            s.read_records()

    def test_empty_window_config_and_response_guards(self):
        s = source(); s.post = Mock(return_value=response(payload([])))
        self.assertEqual([], s.read_records())
        s.get.return_value = response({})
        with self.assertRaises(SourceError): s.read_records()
        s = source(max_bytes=1024); s.post = Mock(return_value=response(b"x" * 1025))
        with self.assertRaisesRegex(SourceError, "max_bytes"): s.read_records()
        s.post.return_value.close.assert_called_once()
        s = source(); reply = response({}); reply.status_code = 302; s.post = Mock(return_value=reply)
        with self.assertRaisesRegex(SourceError, "redirect"): s.read_records()
        reply.close.assert_called_once()
        for options in ({"events": ["removed"]}, {"complete_snapshot": True}, {"lookback_days": 0}):
            with self.assertRaises(ValueError): source(**options)


if __name__ == "__main__":
    unittest.main()
