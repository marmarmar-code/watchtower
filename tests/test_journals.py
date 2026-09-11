import unittest
import json
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.journals import JournalsSource, _record
from test_change_sources import poll

JP1 = "jp_01m27vjtc0ee9afdp2w7ksgffy"
JP2 = "jp_01m27tns88eg5t5mhxz15gtzev"


def config(**options):
    return SourceConfig(id="journals", kind="journals", label="Journal", urls=(),
                        filters=FilterRule(match_all=True), options={"query": "Equinor", **options})


def response(payload):
    raw = json.dumps(payload).encode()
    return Mock(status_code=200, headers={}, iter_content=Mock(return_value=[raw]))


def item(identity=JP1, title="Søknad"):
    return {"entity": "Journalpost", "id": identity, "offentligTittel": title,
            "publisertDato": "2026-09-11T09:08:08Z", "journalposttype": "inngaaende_dokument",
            "journaldato": "2026-09-03", "saksmappe": "sm_01m27vjtbwesdrp80sfwn3g4xh",
            "korrespondansepart": ["kp_01m27vjtc9eyytx0nkxs7q2k3t"],
            "administrativEnhetObjekt": "enh_01j73r5z2dep1vfvmzxr3f0cn9"}


class JournalTests(unittest.TestCase):
    def test_two_pages_keep_scope_sort_and_duplicate_cursor_values(self):
        source = JournalsSource(config(korrespondansepart_navn="Equinor", tittel="PL1121", limit=2))
        first = {"items": [item()], "next": "/search?limit=2&startingAfter=0.0&startingAfter=" + JP1}
        source.get = Mock(side_effect=[response(first), response({"items": [item(JP2)], "next": None})])
        assert len(source.read_records()) == 2
        query = parse_qs(urlsplit(source.get.call_args_list[1].args[0]).query)
        assert query["startingAfter"] == ["0.0", JP1]
        assert query["entity"] == ["Journalpost"]
        assert query["sortBy"] == ["publisertDato"] and query["sortOrder"] == ["desc"]
        assert query["query"] == ["Equinor"] and query["korrespondansepartNavn"] == ["Equinor"]
        assert query["tittel"] == ["PL1121"]
        assert query["publisertDatoFrom"] and query["publisertDatoTo"]


    def test_identity_public_link_and_publish_time_are_separate(self):
        row = _record(item())
        assert row["key"] == JP1 and row["published"].endswith("Z")
        assert row["url"] == "https://einnsyn.no/journalpost/" + JP1
        assert row["fields"] == {"offentligTittel": "Søknad", "journalposttype": "inngaaende_dokument", "journaldato": "2026-09-03"}


    def test_initial_repeat_and_metadata_timestamp_change_are_quiet(self):
        source = JournalsSource(config())
        source.get = Mock(return_value=response({"items": [item()], "next": None}))
        state, alerts = poll(source)
        assert alerts == []
        changed = item(); changed["oppdatertDato"] = "2026-09-12T00:00:00Z"; changed["publisertDato"] = "2026-09-12T00:00:00Z"
        source.get.return_value = response({"items": [changed], "next": None})
        _, alerts = poll(source, state)
        assert alerts == []


    def test_title_change_has_norwegian_details_without_internal_ids(self):
        source = JournalsSource(config())
        source.get = Mock(return_value=response({"items": [item()], "next": None}))
        state, _ = poll(source)
        source.get.return_value = response({"items": [item(title="Nytt dokument")], "next": None})
        _, alerts = poll(source, state)
        details = " ".join(alerts[0].item.alert_details)
        assert "Offentlig tittel" in details and "jp_" not in details and "kp_" not in details


    def test_invalid_payload_next_and_record_types_fail_closed(self):
        source = JournalsSource(config())
        bad_payloads = [
            {"items": [item()], "unexpected": 1},
            {"items": [item()], "next": "/search?startingAfter=only-one"},
            {"items": [item()], "next": "https://example.org/search?startingAfter=0&startingAfter=x"},
        ]
        for payload in bad_payloads:
            source.get = Mock(return_value=response(payload))
            try: source.read_records()
            except SourceError: pass
            else: raise AssertionError("invalid response was accepted")
        mutations = [
            ("id", "jp_"), ("entity", "Saksmappe"), ("publisertDato", {}),
            ("journaldato", "03.09.2026"), ("journalposttype", []),
            ("saksmappe", {}), ("korrespondansepart", "kp_invalid"),
            ("administrativEnhetObjekt", []),
        ]
        for key, value in mutations:
            row = item(); row[key] = value
            try: _record(row)
            except SourceError: pass
            else: raise AssertionError(f"invalid {key} was accepted")


    def test_page_record_and_removal_limits_are_rejected(self):
        source = JournalsSource(config(max_pages=1))
        source.get = Mock(return_value=response({"items": [item()], "next": "/search?startingAfter=0&startingAfter=" + JP1}))
        try: source.read_records()
        except SourceError as exc: assert "max_pages" in str(exc)
        else: raise AssertionError("pagination limit was ignored")
        for options in ({"events": ["removed"], "complete_snapshot": True}, {"complete_snapshot": True}):
            try: JournalsSource(config(**options))
            except ValueError: pass
            else: raise AssertionError("removal contract accepted")


    def test_official_endpoint_and_explicit_scope_are_required(self):
        for cfg in (SourceConfig("j", "journals", urls=("https://example.org/search",), options={"query": "x"}),
                    SourceConfig("j", "journals", options={})):
            try: JournalsSource(cfg)
            except ValueError: pass
            else: raise AssertionError("invalid source scope accepted")
