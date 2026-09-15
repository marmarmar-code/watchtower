import json
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.aquaculture import AquacultureSource
from watchtower.sources.common import SourceError
from test_change_sources import poll


def config(**options):
    return SourceConfig(id="aqua", kind="aquaculture", label="Aqua", filters=FilterRule(match_all=True),
                        options={"mode": "sites", "nr": "10029", **options})


def response(payload=None, raw=None):
    data = raw if raw is not None else json.dumps(payload).encode()
    return Mock(status_code=200, headers={}, iter_content=Mock(return_value=[data]))


def site(**changes):
    return {"siteNr": 10029, "name": "TUHOLMANE Ø", "capacity": 2340.0, "tempCapacity": 2340.0,
            "capacityUnitType": "TN", "waterTypeValue": "SALT", "firstClearanceTime": "1998-06-18T22:00:00Z",
            "placement": {"prodAreaName": "Karmøy til Sotra", "municipalityName": "KARMØY", "countyName": "ROGALAND"},
            "versionId": 15356, "speciesLimitations": [{"nbNoName": "Laks"}], **changes}


class AquacultureTests(unittest.TestCase):
    def source(self, payload):
        source = AquacultureSource(config())
        source.get = Mock(return_value=response(payload))
        return source

    def test_exact_scope_identity_and_register_date(self):
        source = self.source([site()])
        row = source.read_records()[0]
        self.assertEqual("10029", row["key"])
        self.assertIsNone(row["published"])
        self.assertNotIn("versionId", json.dumps(row))
        self.assertNotIn("species", json.dumps(row))
        query = parse_qs(urlsplit(source.get.call_args.args[0]).query)
        self.assertEqual({"nr": ["10029"], "range": ["0-9"]}, query)

    def test_initial_repeat_and_capacity_change(self):
        source = self.source([site()])
        state, alerts = poll(source)
        self.assertEqual([], alerts)
        next_state, alerts = poll(source, state)
        self.assertEqual([], alerts)
        self.assertEqual(state, next_state)
        source.get.return_value = response([{**site(), "capacity": 2500.0}])
        _, alerts = poll(source, state)
        self.assertEqual(1, len(alerts))
        details = " ".join(alerts[0].item.alert_details)
        self.assertIn("Endret akvakulturlokalitet", details)
        self.assertIn("Kapasitet: 2340.0 → 2500.0", details)

    def test_scope_count_and_identity_fail_closed(self):
        for payload in ([], [site(), site()], [{**site(), "siteNr": 10030}]):
            with self.subTest(payload=payload), self.assertRaises(SourceError): self.source(payload).read_records()

    def test_missing_and_invalid_source_values_fail_closed(self):
        missing = site(); missing.pop("capacity")
        bad = [missing, {**site(), "siteNr": True}, {**site(), "capacity": float("nan")},
               {**site(), "capacityUnitType": None}, {**site(), "firstClearanceTime": "1998-06-18"},
               {**site(), "placement": {"prodAreaName": "x"}}]
        for row in bad:
            with self.subTest(row=row), self.assertRaises(SourceError): self.source([row]).read_records()

    def test_invalid_json_is_source_error(self):
        source = AquacultureSource(config())
        source.get = Mock(return_value=response(raw=b"not-json"))
        with self.assertRaisesRegex(SourceError, "invalid JSON"): source.read_records()

    def test_only_official_exact_site_mode_is_accepted(self):
        configs = [config(mode="licenses"), config(nr="abc"),
                   SourceConfig("a", "aquaculture", urls=("https://example.org",), options={"mode": "sites", "nr": "10029"}),
                   config(events=["removed"], complete_snapshot=True)]
        for value in configs:
            with self.subTest(config=value), self.assertRaises(ValueError): AquacultureSource(value)


if __name__ == "__main__": unittest.main()
