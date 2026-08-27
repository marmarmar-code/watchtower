import unittest

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.ssb import SsbSource


def metadata(period="2025", label="Folkemengde"):
    return {
        "label": label,
        "firstPeriod": "1986",
        "lastPeriod": period,
        "variableNames": ["Region", "Tid", "Statistikkvariabel"],
        "discontinued": False,
    }


class Response:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class SsbTests(unittest.TestCase):
    def config(self, tables=None):
        return SourceConfig(
            id="ssb-test",
            kind="ssb",
            label="SSB",
            filters=FilterRule(match_all=True),
            options={"tables": tables or ["07459"]},
        )

    def test_requires_explicit_numeric_tables(self):
        empty = SourceConfig(
            id="ssb-test",
            kind="ssb",
            label="SSB",
            filters=FilterRule(match_all=True),
            options={"tables": []},
        )
        with self.assertRaises(ValueError):
            SsbSource(empty)
        with self.assertRaises(ValueError):
            SsbSource(self.config(["latest"]))
        with self.assertRaises(ValueError):
            SsbSource(self.config(["1234"]))

    def test_metadata_only_request_has_no_api_key(self):
        source = SsbSource(self.config())
        calls = []
        source.get = lambda url: (calls.append(url) or Response(metadata()))
        source.fetch()
        self.assertIn("/tables/07459?lang=no", calls[0])
        self.assertNotIn("key", calls[0].casefold())

    def test_baseline_and_period_change_are_deterministic(self):
        source = SsbSource(self.config())
        source.get = lambda url: Response(metadata("2025"))
        first = source.fetch_with_state(None)
        state = source.augment_state({"source_state": {}})
        self.assertTrue(first[0].suppress_alert)
        source.get = lambda url: Response(metadata("2026"))
        changed = source.fetch_with_state(state)[0]
        self.assertFalse(changed.suppress_alert)
        self.assertIn("Ny periode: 2026", changed.alert_details)

    def test_malformed_response_fails_closed(self):
        source = SsbSource(self.config())
        source.get = lambda url: Response({"label": "missing variable metadata"})
        with self.assertRaises(SourceError):
            source.fetch()

    def test_fingerprint_is_stable_for_key_order(self):
        a = SsbSource(self.config())
        b = SsbSource(self.config())
        a.get = lambda url: Response(metadata())
        b.get = lambda url: Response(
            {
                "variableNames": metadata()["variableNames"],
                "lastPeriod": "2025",
                "label": "Folkemengde",
                "discontinued": False,
                "firstPeriod": "1986",
            }
        )
        self.assertEqual(a.fetch()[0].fingerprint, b.fetch()[0].fingerprint)

    def test_unrelated_response_fields_do_not_change_fingerprint(self):
        a = SsbSource(self.config())
        b = SsbSource(self.config())
        a.get = lambda url: Response(metadata())
        b.get = lambda url: Response({**metadata(), "transportTimestamp": "later"})
        self.assertEqual(a.fetch()[0].fingerprint, b.fetch()[0].fingerprint)

    def test_shortened_time_series_is_reported(self):
        source = SsbSource(self.config())
        source.get = lambda url: Response(metadata())
        source.fetch_with_state(None)
        state = source.augment_state({"source_state": {}})
        source.get = lambda url: Response({**metadata(), "firstPeriod": "2000"})
        changed = source.fetch_with_state(state)[0]
        self.assertFalse(changed.suppress_alert)
        self.assertIn("Første periode i tidsserien endret", changed.alert_details)


if __name__ == "__main__":
    unittest.main()
