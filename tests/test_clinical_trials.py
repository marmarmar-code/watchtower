from copy import deepcopy
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

from tests.test_change_sources import poll
from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.clinical_trials import API, ClinicalTrialsSource
from watchtower.sources.common import SourceError


def config(**options):
    return SourceConfig(id="ct", kind="clinical_trials", label="ClinicalTrials.gov",
                        urls=(API,), filters=FilterRule(match_all=True), options=options)


def study(nct_id="NCT00000123"):
    today = datetime.now(timezone.utc).date().isoformat()
    return {"protocolSection": {
        "identificationModule": {"nctId": nct_id, "briefTitle": "Studie av behandling"},
        "statusModule": {"overallStatus": "RECRUITING",
                         "studyFirstPostDateStruct": {"date": "2020-01-02"},
                         "lastUpdatePostDateStruct": {"date": today}},
        "designModule": {"phases": ["PHASE1", "PHASE2"]},
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Universitetssykehuset"}},
        "contactsLocationsModule": {"locations": [{"country": "Sweden"}, {"country": "Norway"}]},
    }, "hasResults": False}


def payload(studies, total=None, token=None):
    result = {"studies": studies, "totalCount": len(studies) if total is None else total}
    if token is not None:
        result["nextPageToken"] = token
    return result


def response(value):
    raw = value if isinstance(value, bytes) else json.dumps(value).encode()
    return Mock(status_code=200, headers={}, iter_content=Mock(return_value=[raw]), close=Mock())


class ClinicalTrialsTests(unittest.TestCase):
    def source(self, **options):
        return ClinicalTrialsSource(config(**options))

    def test_real_shape_scope_and_query_contract(self):
        source = self.source()
        reply = response(payload([study()]))
        source.get = Mock(return_value=reply)
        row = source.read_records()[0]
        self.assertEqual("NCT00000123", row["key"])
        self.assertEqual("Rekrutterer", row["fields"]["status"])
        self.assertEqual("Fase 1/Fase 2", row["fields"]["phases"])
        self.assertEqual("2020-01-02", row["published"])
        query = parse_qs(urlparse(source.get.call_args.args[0]).query)
        self.assertEqual(["AREA[LocationCountry]Norway"], query["query.locn"])
        self.assertEqual(["true"], query["countTotal"])
        self.assertIn("LastUpdatePostDate", query["filter.advanced"][0])
        self.assertEqual(["100"], query["pageSize"])
        reply.close.assert_called_once()

    def test_update_timestamp_noise_is_quiet_but_status_and_results_change(self):
        source = self.source()
        original = study()
        source.get = Mock(return_value=response(payload([original])))
        state, alerts = poll(source)
        self.assertEqual([], alerts)
        source.get = Mock(return_value=response(payload([deepcopy(original)])))
        state, alerts = poll(source, state)
        self.assertEqual([], alerts)
        changed = deepcopy(original)
        changed["protocolSection"]["statusModule"]["overallStatus"] = "COMPLETED"
        changed["hasResults"] = True
        source.get = Mock(return_value=response(payload([changed])))
        _, alerts = poll(source, state)
        self.assertEqual(1, len(alerts))
        details = " ".join(alerts[0].item.alert_details)
        self.assertIn("Rekrutterer → Fullført", details)
        self.assertIn("False → True", details)

    def test_strict_two_page_total_and_encoded_token(self):
        source = self.source(page_size=1, max_pages=2)
        source.get = Mock(side_effect=[response(payload([study("NCT00000123")], 2, "token +/=")),
                                       response({"studies": [study("NCT00000124")]} )])
        self.assertEqual(2, len(source.read_records()))
        second_url = source.get.call_args_list[1].args[0]
        self.assertIn("pageToken=token+%2B%2F%3D", second_url)
        bad_seconds = [payload([study("NCT00000124")], 3, "more"),
                       payload([], 2), payload([study("NCT00000123")], 2),
                       payload([study("NCT00000124")], 2, "extra")]
        for second in bad_seconds:
            source = self.source(page_size=1, max_pages=2)
            source.get = Mock(side_effect=[response(payload([study()], 2, "next")), response(second)])
            with self.assertRaises(SourceError):
                source.read_records()

    def test_bounds_reject_before_truncation(self):
        source = self.source(page_size=100, max_pages=1, max_records=500)
        source.get = Mock(return_value=response(payload([study()] * 100, 101, "next")))
        with self.assertRaisesRegex(SourceError, "bounds"):
            source.read_records()
        source = self.source(max_records=1)
        source.get = Mock(return_value=response(payload([study()], 2, "next")))
        with self.assertRaisesRegex(SourceError, "bounds"):
            source.read_records()

    def test_invalid_identity_types_dates_and_scope_fail_closed(self):
        base = study()
        mutations = []
        for path, value in [
            (("protocolSection", "identificationModule", "nctId"), "123"),
            (("protocolSection", "identificationModule", "briefTitle"), 3),
            (("protocolSection", "statusModule", "overallStatus"), "NEW_STATUS"),
            (("protocolSection", "statusModule", "studyFirstPostDateStruct", "date"), "2020-02-30"),
            (("protocolSection", "statusModule", "lastUpdatePostDateStruct", "date"), "2020-01-01"),
            (("protocolSection", "designModule", "phases"), ["PHASE9"]),
            (("hasResults",), "false"),
        ]:
            row = deepcopy(base); cursor = row
            for key in path[:-1]: cursor = cursor[key]
            cursor[path[-1]] = value; mutations.append(row)
        outside = deepcopy(base)
        outside["protocolSection"]["contactsLocationsModule"]["locations"] = [{"country": "Sweden"}]
        mutations.append(outside)
        for row in mutations:
            source = self.source(); source.get = Mock(return_value=response(payload([row])))
            with self.subTest(row=row), self.assertRaises(SourceError):
                source.read_records()

    def test_invalid_payload_bytes_and_configuration(self):
        for value in (b"not-json", {"studies": []}, {"studies": {}, "totalCount": 0},
                      {"studies": [], "totalCount": True}, payload([], token="unexpected")):
            source = self.source(); source.get = Mock(return_value=response(value))
            with self.assertRaises(SourceError): source.read_records()
        reply = response(b"x" * 1025)
        source = self.source(max_bytes=1024); source.get = Mock(return_value=reply)
        with self.assertRaisesRegex(SourceError, "max_bytes"): source.read_records()
        reply.close.assert_called_once()
        invalid = [config(page_size=True), config(max_pages="5"), config(last_update_days=0),
                   config(events=["removed"]), config(complete_snapshot=True), config(allow_empty="yes"),
                   SourceConfig(id="ct", kind="clinical_trials", label="CT", urls=("https://example.test",),
                                filters=FilterRule(match_all=True), options={})]
        for candidate in invalid:
            with self.assertRaises(ValueError): ClinicalTrialsSource(candidate)


if __name__ == "__main__":
    unittest.main()
