from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

from tests.test_change_sources import poll
from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.clinical_trial_results import ClinicalTrialResultsSource
from watchtower.sources.clinical_trials import API
from watchtower.sources.common import SourceError


def config(**options):
    return SourceConfig(id="ctr", kind="clinical_trial_results", label="Resultater",
                        urls=(API,), filters=FilterRule(match_all=True), options=options)


def study(nct_id="NCT00000123"):
    today = datetime.now(timezone.utc).date().isoformat()
    return {
        "protocolSection": {
            "identificationModule": {"nctId": nct_id, "briefTitle": "Studieresultater"},
            "statusModule": {
                "lastUpdatePostDateStruct": {"date": today},
                "resultsFirstPostDateStruct": {"date": "2024-02-03"},
            },
            "designModule": {"enrollmentInfo": {"count": 40, "type": "ACTUAL"}},
            "contactsLocationsModule": {"locations": [{"country": "Norway"}]},
        },
        "hasResults": True,
        "resultsSection": {
            "participantFlowModule": {
                "groups": [{"id": "FG001", "title": "Behandling"}],
                "periods": [{"title": "Behandlingsperiode", "milestones": []}],
            },
            "outcomeMeasuresModule": {"outcomeMeasures": [{
                "type": "PRIMARY", "title": "Primært mål", "timeFrame": "12 uker",
                "groups": [{"id": "OG001", "title": "Behandling"}], "classes": [],
            }]},
            "adverseEventsModule": {
                "eventGroups": [{"id": "EG001", "title": "Behandling",
                                  "seriousNumAffected": 1, "seriousNumAtRisk": 40}],
                "seriousEvents": [{"term": "Rapportert alvorlig hendelse",
                                    "stats": [{"groupId": "EG001", "numAffected": 1, "numAtRisk": 40}]}],
                "otherEvents": [],
            },
        },
    }


def payload(studies, total=None, token=None):
    value = {"studies": studies, "totalCount": len(studies) if total is None else total}
    if token is not None:
        value["nextPageToken"] = token
    return value


def response(value):
    raw = value if isinstance(value, bytes) else json.dumps(value).encode()
    return Mock(status_code=200, headers={}, iter_content=Mock(return_value=[raw]), close=Mock())


class ClinicalTrialResultsTests(unittest.TestCase):
    def source(self, **options):
        return ClinicalTrialResultsSource(config(**options))

    def test_query_and_result_shape(self):
        source = self.source()
        source.get = Mock(return_value=response(payload([study()])))
        row = source.read_records()[0]
        self.assertEqual("NCT00000123", row["key"])
        self.assertEqual("2024-02-03", row["published"])
        self.assertEqual(40, row["fields"]["actual_enrollment"])
        query = parse_qs(urlparse(source.get.call_args.args[0]).query)
        self.assertEqual(["AREA[HasResults]true"], query["query.term"])
        self.assertIn("OutcomeMeasuresModule", query["fields"][0])

    def test_baseline_repeat_change_and_metadata_only_update(self):
        original = study()
        source = self.source()
        source.get = Mock(return_value=response(payload([original])))
        state, alerts = poll(source)
        self.assertEqual([], alerts)
        source.get = Mock(return_value=response(payload([deepcopy(original)])))
        state, alerts = poll(source, state)
        self.assertEqual([], alerts)

        metadata = deepcopy(original)
        metadata["protocolSection"]["statusModule"]["lastUpdatePostDateStruct"]["date"] = \
            (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
        source.get = Mock(return_value=response(payload([metadata])))
        state, alerts = poll(source, state)
        self.assertEqual([], alerts)

        changed = deepcopy(metadata)
        changed["resultsSection"]["outcomeMeasuresModule"]["outcomeMeasures"][0]["title"] = "Revidert mål"
        source.get = Mock(return_value=response(payload([changed])))
        _, alerts = poll(source, state)
        self.assertEqual(1, len(alerts))
        details = " ".join(alerts[0].item.alert_details)
        self.assertIn("resultatmål er revidert", details)
        self.assertNotIn(changed["resultsSection"]["outcomeMeasuresModule"]["outcomeMeasures"][0]["title"], details)
        self.assertNotRegex(details, r"[0-9a-f]{64}")

    def test_added_alert_is_bounded_and_contains_no_hash(self):
        source = self.source()
        source.get = Mock(return_value=response(payload([study()])))
        _, alerts = poll(source, {"source_state": {"records": {
            "scope": source.scope, "rows": {}}}})
        self.assertEqual(1, len(alerts))
        details = " ".join(alerts[0].item.alert_details)
        self.assertIn("Nyobserverte publiserte studieresultater", details)
        self.assertIn("Faktisk deltakerantall: 40", details)
        self.assertNotRegex(details, r"[0-9a-f]{64}")

    def test_source_id_lists_are_normalized_but_ordered_lists_are_not(self):
        first = study()
        first["resultsSection"]["participantFlowModule"]["groups"].append(
            {"id": "FG000", "title": "Kontroll"})
        second = deepcopy(first)
        second["resultsSection"]["participantFlowModule"]["groups"].reverse()
        source = self.source(); source.get = Mock(return_value=response(payload([first])))
        state, _ = poll(source)
        source.get = Mock(return_value=response(payload([second])))
        _, alerts = poll(source, state)
        self.assertEqual([], alerts)

        duplicate = deepcopy(first)
        duplicate["resultsSection"]["participantFlowModule"]["groups"].append(
            {"id": "FG000", "title": "Annen"})
        source = self.source(); source.get = Mock(return_value=response(payload([duplicate])))
        with self.assertRaisesRegex(SourceError, "source ID"):
            source.read_records()

    def test_outcome_measure_order_is_quiet_and_compound_identity_is_unique(self):
        first = study()
        first["resultsSection"]["outcomeMeasuresModule"]["outcomeMeasures"].append({
            "type": "SECONDARY", "title": "Sekundært mål", "timeFrame": "24 uker",
        })
        second = deepcopy(first)
        second["resultsSection"]["outcomeMeasuresModule"]["outcomeMeasures"].reverse()
        source = self.source(); source.get = Mock(return_value=response(payload([first])))
        state, _ = poll(source)
        source.get = Mock(return_value=response(payload([second])))
        _, alerts = poll(source, state)
        self.assertEqual([], alerts)

        duplicate = deepcopy(first)
        duplicate["resultsSection"]["outcomeMeasuresModule"]["outcomeMeasures"].append(
            deepcopy(duplicate["resultsSection"]["outcomeMeasuresModule"]["outcomeMeasures"][0]))
        source = self.source(); source.get = Mock(return_value=response(payload([duplicate])))
        with self.assertRaisesRegex(SourceError, "compound identity"):
            source.read_records()

    def test_invalid_selected_records_fail_closed(self):
        mutations = []
        for path, value in [
            (("hasResults",), False),
            (("protocolSection", "designModule", "enrollmentInfo", "type"), "ESTIMATED"),
            (("protocolSection", "designModule", "enrollmentInfo", "count"), True),
            (("protocolSection", "contactsLocationsModule", "locations"), [{"country": "Sweden"}]),
            (("resultsSection", "participantFlowModule"), None),
            (("resultsSection", "participantFlowModule", "groups"), []),
            (("resultsSection", "participantFlowModule", "groups"),
             [{"id": "FG001"}, {"title": "uten id"}]),
            (("resultsSection", "participantFlowModule", "periods"), [3]),
            (("resultsSection", "outcomeMeasuresModule", "outcomeMeasures"), []),
            (("resultsSection", "outcomeMeasuresModule", "outcomeMeasures"), [3]),
            (("resultsSection", "outcomeMeasuresModule", "outcomeMeasures"),
             [{"type": "PRIMARY", "title": "Mål"}]),
            (("resultsSection", "adverseEventsModule", "seriousEvents"), {}),
            (("resultsSection", "adverseEventsModule", "seriousEvents"), [3]),
            (("resultsSection", "adverseEventsModule", "seriousEvents"),
             [{"term": "Hendelse", "stats": [{"groupId": "EG001"}, {}]}]),
            (("resultsSection", "adverseEventsModule", "eventGroups"), [{"title": "uten id"}]),
            (("protocolSection", "statusModule", "resultsFirstPostDateStruct", "date"), "2024-02-30"),
        ]:
            row = study(); cursor = row
            for key in path[:-1]:
                cursor = cursor[key]
            cursor[path[-1]] = value
            mutations.append(row)
        for row in mutations:
            source = self.source(); source.get = Mock(return_value=response(payload([row])))
            with self.subTest(path=row), self.assertRaises(SourceError):
                source.read_records()

    def test_total_bounds_duplicate_nct_and_configuration(self):
        source = self.source(max_records=1)
        source.get = Mock(return_value=response(payload([study()], total=2, token="next")))
        with self.assertRaisesRegex(SourceError, "bounds"):
            source.read_records()
        source = self.source(page_size=2)
        source.get = Mock(return_value=response(payload([study(), study()])))
        with self.assertRaisesRegex(SourceError, "repeated an NCT ID"):
            source.read_records()
        for candidate in (config(events=["removed"]), config(complete_snapshot=True),
                          config(allow_empty="yes")):
            with self.assertRaises(ValueError):
                ClinicalTrialResultsSource(candidate)


if __name__ == "__main__":
    unittest.main()
