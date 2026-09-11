from copy import deepcopy
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

from tests.test_change_sources import poll
from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.company_discovery import CompanyDiscoverySource


def config(**options):
    return SourceConfig(id="companies", kind="company_discovery", label="Nye medieselskaper", urls=(),
                        filters=FilterRule(match_all=True), options={"industry_prefixes": ["58"],
                        "organisation_forms": ["AS", "ASA"], **options})


def entity(orgnr="938271526", form="AS", industry="58.130"):
    today = datetime.now(timezone.utc).date().isoformat()
    return {"organisasjonsnummer": orgnr, "navn": "NYTT MEDIESELSKAP AS",
            "organisasjonsform": {"kode": form, "beskrivelse": "Aksjeselskap"},
            "registreringsdatoEnhetsregisteret": today,
            "naeringskode1": {"kode": industry, "beskrivelse": "Utgivelse av blader og tidsskrifter"},
            "forretningsadresse": {"kommune": "OSLO"},
            "_links": {"self": {"href": f"https://data.brreg.no/enhetsregisteret/api/enheter/{orgnr}"}}}


def payload(rows, page=0, total=None, pages=None, size=100):
    total = len(rows) if total is None else total
    pages = (0 if total == 0 else (total + size - 1) // size) if pages is None else pages
    return {"_embedded": {"enheter": rows}, "page": {"size": size, "totalElements": total,
            "totalPages": pages, "number": page}}


def empty_payload_without_embedded(size=100):
    return {"_links": {"self": {"href": "https://data.brreg.no/enhetsregisteret/api/enheter"}},
            "page": {"size": size, "totalElements": 0, "totalPages": 0, "number": 0}}


def response(value):
    raw = value if isinstance(value, bytes) else json.dumps(value).encode()
    return Mock(status_code=200, headers={}, iter_content=Mock(return_value=[raw]), close=Mock())


class CompanyDiscoveryTests(unittest.TestCase):
    def source(self, replies, **options):
        source = CompanyDiscoverySource(config(**options))
        if not isinstance(replies, list): replies = [replies]
        source.get = Mock(side_effect=[response(value) for value in replies])
        return source

    def test_filters_legal_forms_and_preserves_registration_semantics(self):
        source = self.source(payload([entity(), entity("938298475", "ENK", "58.110")]))
        row = source.read_records()[0]
        self.assertEqual("938271526", row["key"]); self.assertIsNone(row["published"])
        self.assertEqual("58.130", row["fields"]["industry_code"])
        self.assertEqual("Utgivelse av blader og tidsskrifter", row["fields"]["industry_description"])
        query = parse_qs(urlparse(source.get.call_args.args[0]).query)
        self.assertEqual(["58"], query["naeringskode"]); self.assertEqual(["100"], query["size"])

    def test_first_baseline_and_repeat_are_quiet_but_name_change_alerts(self):
        current = payload([entity()]); source = self.source(current); state, alerts = poll(source)
        self.assertEqual([], alerts)
        source = self.source(current); state, alerts = poll(source, state); self.assertEqual([], alerts)
        changed = deepcopy(current); changed["_embedded"]["enheter"][0]["navn"] = "NYTT NAVN AS"
        source = self.source(changed); _, alerts = poll(source, state)
        self.assertEqual(1, len(alerts)); self.assertIn("NYTT MEDIESELSKAP AS → NYTT NAVN AS",
                                                       " ".join(alerts[0].item.alert_details))

    def test_strict_complete_pagination_and_duplicate_detection(self):
        source = self.source([payload([entity()], 0, 2, 2, 1),
                              payload([entity("938306753", "AS", "58.110")], 1, 2, 2, 1)],
                             page_size=1, max_pages=2)
        self.assertEqual(2, len(source.read_records()))
        bad_seconds = [payload([], 1, 2, 2, 1), payload([entity("938306753")], 1, 3, 3, 1),
                       payload([entity()], 1, 2, 2, 1)]
        for second in bad_seconds:
            source = self.source([payload([entity()], 0, 2, 2, 1), second], page_size=1, max_pages=2)
            with self.assertRaises(SourceError): source.read_records()

    def test_scope_identity_dates_types_and_links_fail_closed(self):
        base = entity(); mutations = []
        for path, value in [(("organisasjonsnummer",), "123456789"),
                            (("organisasjonsform", "kode"), None),
                            (("naeringskode1", "kode"), "59.130"),
                            (("registreringsdatoEnhetsregisteret",), "2020-01-01"),
                            (("_links", "self", "href"), "https://example.test/entity")]:
            row = deepcopy(base); cursor = row
            for key in path[:-1]: cursor = cursor[key]
            cursor[path[-1]] = value; mutations.append(row)
        for row in mutations:
            with self.assertRaises(SourceError): self.source(payload([row])).read_records()
        malformed_link = entity(); malformed_link["_links"]["self"] = []
        with self.assertRaises(SourceError): self.source(payload([malformed_link])).read_records()

    def test_secondary_industry_match_and_cross_prefix_duplicate_are_supported(self):
        row = entity()
        row["naeringskode1"] = {"kode": "58.110", "beskrivelse": "Utgivelse av bøker"}
        row["naeringskode2"] = {"kode": "59.110", "beskrivelse": "Produksjon av film"}
        source = self.source([payload([row]), payload([deepcopy(row)])],
                             industry_prefixes=["58", "59"])
        records = source.read_records()
        self.assertEqual(1, len(records))
        self.assertEqual("58.110, 59.110", records[0]["fields"]["industry_code"])

        conflict = deepcopy(row); conflict["navn"] = "ANNET NAVN AS"
        source = self.source([payload([row]), payload([conflict])],
                             industry_prefixes=["58", "59"])
        with self.assertRaisesRegex(SourceError, "conflicting duplicate"):
            source.read_records()

    def test_empty_valid_window_and_invalid_payload_or_bounds(self):
        self.assertEqual([], self.source(payload([])).read_records())
        self.assertEqual([], self.source(empty_payload_without_embedded()).read_records())
        for value in (b"not json", {}, {"_embedded": {"enheter": []}, "page": {"size": True,
                      "totalElements": 0, "totalPages": 0, "number": 0}}):
            with self.assertRaises(SourceError): self.source(value).read_records()
        source = self.source(payload([entity()], total=501, pages=6), max_records=500, max_pages=5)
        with self.assertRaisesRegex(SourceError, "bounds"): source.read_records()

    def test_configuration_rejects_ambiguous_scope_and_removals(self):
        invalid = [config(industry_prefixes=[58]), config(industry_prefixes=["58.130"]),
                   config(organisation_forms=["ENK"]), config(page_size=True), config(lookback_days=91),
                   config(organisation_forms=[{"kode": "AS"}]),
                   config(events=["removed"]), config(complete_snapshot=True),
                   SourceConfig(id="c", kind="company_discovery", label="C", urls=("https://example.test",),
                                filters=FilterRule(match_all=True), options={"industry_prefixes": ["58"]})]
        for candidate in invalid:
            with self.assertRaises(ValueError): CompanyDiscoverySource(candidate)


if __name__ == "__main__": unittest.main()
