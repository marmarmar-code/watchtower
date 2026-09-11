from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

from tests.test_change_sources import poll
from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.industrial_environment import FIELD_TYPES, IndustrialEnvironmentSource

YARA = json.loads((Path(__file__).parent / "fixtures/event_sources/industrial-yara-2026-09-11.json").read_text())


def config(ids=(5447,), **options):
    return SourceConfig(id="industry", kind="industrial_environment", label="Industrianlegg", urls=(),
                        filters=FilterRule(match_all=True),
                        options={"installation_ids": list(ids), **options})


def response(payload):
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return Mock(status_code=200, headers={}, iter_content=Mock(return_value=[raw]), close=Mock())


def selected(*ids):
    result = deepcopy(YARA)
    result["fields"] = [{"name": name, "type": kind, "alias": name} for name, kind in FIELD_TYPES.items()]
    result["features"] = [feature for feature in result["features"]
                          if feature["attributes"]["anlegg_id"] in ids]
    for feature in result["features"]:
        attributes = feature["attributes"]
        attributes.update({"forurensningsmyndighet": "Miljødirektoratet",
                           "har_utslipp_luft": 1, "har_utslipp_vann": 1,
                           "har_krav_til_overvaking": 1})
        feature["attributes"] = {name: attributes[name] for name in FIELD_TYPES}
    return result


class IndustrialEnvironmentTests(unittest.TestCase):
    def source(self, ids=(5447,), payload=None, **options):
        source = IndustrialEnvironmentSource(config(ids, **options))
        source.get = Mock(return_value=response(selected(*ids) if payload is None else payload))
        return source

    def test_exact_selected_scope_query_identity_and_semantics(self):
        source = self.source(); row = source.read_records()[0]
        self.assertEqual("5447", row["key"]); self.assertEqual("Yara Porsgrunn", row["fields"]["name"])
        self.assertEqual(2023, row["fields"]["reporting_year"]); self.assertIsNone(row["published"])
        self.assertTrue(row["fields"]["emissions_air"]); self.assertTrue(row["fields"]["emissions_water"])
        query = parse_qs(urlparse(source.get.call_args.args[0]).query)
        self.assertEqual(["anlegg_id IN (5447)"], query["where"])
        self.assertEqual(["false"], query["returnGeometry"]); self.assertEqual(["1"], query["resultRecordCount"])

    def test_repeat_is_quiet_but_status_change_alerts_in_norwegian(self):
        source = self.source(); state, alerts = poll(source); self.assertEqual([], alerts)
        source = self.source(); state, alerts = poll(source, state); self.assertEqual([], alerts)
        changed = selected(5447); changed["features"][0]["attributes"]["driftsstatus"] = "Etterdrift"
        source = self.source(payload=changed); _, alerts = poll(source, state)
        self.assertEqual(1, len(alerts)); self.assertIn("Aktiv → Etterdrift", " ".join(alerts[0].item.alert_details))

    def test_missing_extra_duplicate_and_truncated_selection_fail_closed(self):
        cases = [selected(), selected(5447, 5832), selected(5447), selected(5447)]
        cases[2]["features"].append(deepcopy(cases[2]["features"][0]))
        cases[3]["exceededTransferLimit"] = True
        for payload in cases:
            with self.assertRaises(SourceError): self.source(payload=payload).read_records()

    def test_schema_types_status_year_flags_and_fact_link_fail_closed(self):
        mutations = []
        bad_schema = selected(5447); bad_schema["fields"][0]["type"] = "esriFieldTypeString"; mutations.append(bad_schema)
        for key, value in [("anlegg_id", True), ("navn", None), ("driftsstatus", "Ukjent"),
                           ("forurensningsmyndighet", ""), ("siste_rapportering_aar", "2023"),
                           ("har_utslipp_luft", 2),
                           ("faktaark", "https://www.norskeutslipp.no/Templates/NorskeUtslipp/Pages/company.aspx?CompanyID=5832")]:
            payload = selected(5447); payload["features"][0]["attributes"][key] = value; mutations.append(payload)
        for payload in mutations:
            with self.assertRaises(SourceError): self.source(payload=payload).read_records()

    def test_config_rejects_weak_ids_urls_and_removals(self):
        invalid = [config(ids=(True,)), config(ids=(5447, 5447)), config(ids=()), config(ids=(5447,), max_records=True),
                   config(ids=(5447,), events=["removed"]), config(ids=(5447,), complete_snapshot=True),
                   SourceConfig(id="i", kind="industrial_environment", label="I", urls=("https://example.test",),
                                filters=FilterRule(match_all=True), options={"installation_ids": [5447]})]
        for candidate in invalid:
            with self.assertRaises(ValueError): IndustrialEnvironmentSource(candidate)


if __name__ == "__main__": unittest.main()
