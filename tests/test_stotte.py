from __future__ import annotations

import unittest
from unittest.mock import Mock

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.stotte import API_URL, StotteSource


ORGNR = "934189698"
PROVIDER_ORGNR = "983609155"


class Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def allocation(number: str = "1000251908") -> dict:
    return {
        "stoettetiltaksnummer": number,
        "status": "stoettetiltaksstatus.registrert",
        "mottattDato": "2026-08-25",
        "endret": "2026-08-25T07:11:18+02:00",
        "region": "50",
        "stoetteinstrument": "stoetteinstrumenttype.tilskudd",
        "naering": "68.3",
        "tildeltBeloep": {"beloep": 60000.0, "valuta": "NOK"},
        "tilknyttetStoetteordning": "1000000472",
        "tildelingsdato": "2026-04-09",
        "rolle": [
            {
                "type": "rolletype.stoettegiver",
                "rolleinnehaverVirksomhet": {
                    "organisasjonsnummer": PROVIDER_ORGNR,
                    "navn": "Enova SF",
                },
            },
            {
                "type": "rolletype.stoettemottaker",
                "rolleinnehaverVirksomhet": {
                    "organisasjonsnummer": ORGNR,
                    "navn": "Mottaker AS",
                },
            },
        ],
    }


class StotteTests(unittest.TestCase):
    def config(self, **options):
        return SourceConfig(
            id="stotte-test",
            kind="stotte",
            label="Støtteregister",
            filters=FilterRule(match_all=True),
            options={"recipient_orgnrs": [ORGNR], "page_size": 10, **options},
        )

    def test_scope_is_required(self):
        with self.assertRaisesRegex(ValueError, "explicit scope"):
            StotteSource(SourceConfig(id="x", kind="stotte"))

    def test_invalid_organisation_number_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "valid organisation numbers"):
            StotteSource(self.config(recipient_orgnrs=["934189699"]))

    def test_official_post_search_is_normalized(self):
        source = StotteSource(self.config())
        source.post = Mock(
            return_value=Response({"pageSize": 10, "page": 1, "stoettetildeling": [allocation()]})
        )

        item = source.fetch()[0]

        self.assertEqual(API_URL, source.endpoint)
        self.assertEqual("allocation:1000251908", item.key)
        self.assertEqual(ORGNR, item.metadata["orgnr"])
        self.assertEqual("2026-08-25T07:11:18+02:00", item.published)
        self.assertIn("60 000 NOK", item.text)
        call = source.post.call_args
        self.assertEqual(
            [
                {
                    "field": "STOETTEMOTTAKER_ORGNR",
                    "value": ORGNR,
                    "matchType": "EXACT_MATCH",
                }
            ],
            call.kwargs["json"],
        )
        self.assertEqual("ENDRET", call.kwargs["params"]["sortBy"])

    def test_market_scopes_use_official_multi_value_fields(self):
        config = SourceConfig(
            id="x",
            kind="stotte",
            options={"industries": ["68.3"], "regions": ["50"]},
        )
        source = StotteSource(config)
        self.assertEqual(
            [
                {"field": "NAERING", "value": ["68.3"], "matchType": "ONE_OF"},
                {"field": "REGION", "value": ["50"], "matchType": "ONE_OF"},
            ],
            source._queries()[0],
        )

    def test_missing_id_fails_closed(self):
        source = StotteSource(self.config())
        malformed = allocation()
        malformed.pop("stoettetiltaksnummer")
        source.post = Mock(return_value=Response({"stoettetildeling": [malformed]}))
        with self.assertRaisesRegex(SourceError, "stable allocation id"):
            source.fetch()

    def test_page_limit_fails_instead_of_silently_truncating(self):
        source = StotteSource(self.config(page_size=1, max_pages=1))
        source.post = Mock(
            side_effect=[
                Response({"stoettetildeling": [allocation("1")]}),
                Response({"stoettetildeling": [allocation("2")]}),
            ]
        )
        with self.assertRaisesRegex(SourceError, "page limit"):
            source.fetch()


if __name__ == "__main__":
    unittest.main()
