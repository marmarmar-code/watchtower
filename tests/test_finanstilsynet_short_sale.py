from __future__ import annotations

import unittest
from unittest.mock import Mock

from watchtower.config import FilterRule, SourceConfig
from watchtower.engine import evaluate
from watchtower.sources.common import SourceError
from watchtower.sources.finanstilsynet_short_sale import FinanstilsynetShortSaleSource


ISIN = "BMG9156K1018"


class Response:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def instrument(percent: float = 1.12) -> dict:
    return {
        "isin": ISIN,
        "issuerName": "2020 Bulkers",
        "events": [
            {
                "date": "2026-08-21T00:00:00",
                "shortPercent": 1.42,
                "shares": 328668,
                "activePositions": [
                    {
                        "date": "2026-08-21T00:00:00",
                        "shortPercent": 1.42,
                        "shares": 328668,
                        "positionHolder": "Fund A",
                    }
                ],
            },
            {
                "date": "2026-08-24T00:00:00",
                "shortPercent": percent,
                "shares": 260032,
                "activePositions": [
                    {
                        "date": "2026-08-24T00:00:00",
                        "shortPercent": percent,
                        "shares": 260032,
                        "positionHolder": "Fund A",
                    }
                ],
            },
        ],
    }


class ShortSaleTests(unittest.TestCase):
    def config(self, **options):
        return SourceConfig(
            id="ssr",
            kind="finanstilsynet_short_sale",
            filters=FilterRule(match_all=True),
            options={"isins": [ISIN], **options},
        )

    def source(self, payload, **options):
        source = FinanstilsynetShortSaleSource(self.config(**options))
        source.get = Mock(return_value=Response(payload))
        return source

    def test_requires_explicit_scope(self):
        with self.assertRaisesRegex(ValueError, "requires isins or issuers"):
            FinanstilsynetShortSaleSource(SourceConfig(id="x", kind="finanstilsynet_short_sale"))

    def test_latest_aggregate_event_is_normalized(self):
        item = self.source([instrument()]).fetch()[0]
        self.assertEqual(f"short:{ISIN}", item.key)
        self.assertEqual("1.12", item.metadata["position"])
        self.assertEqual("aktiv", item.metadata["status"])
        self.assertEqual("2026-08-24T00:00:00", item.published)
        self.assertIn("Fund A: 1,12 %", item.text)

    def test_position_change_has_change_detail_and_does_not_repeat(self):
        first = self.source([instrument(1.12)])
        items = first.fetch_with_state(None)
        state, alerts, baseline = evaluate(self.config(), items, None, max_seen=100)
        state = first.augment_state(state)
        self.assertTrue(baseline)
        self.assertEqual([], alerts)

        second = self.source([instrument(1.5)])
        items = second.fetch_with_state(state)
        next_state, alerts, _ = evaluate(self.config(), items, state, max_seen=100)
        next_state = second.augment_state(next_state)
        self.assertEqual(1, len(alerts))
        self.assertIn("Samlet shortandel: 1,12 % → 1,5 %", alerts[0].item.alert_details)

        third = self.source([instrument(1.5)])
        items = third.fetch_with_state(next_state)
        _, repeated, _ = evaluate(self.config(), items, next_state, max_seen=100)
        self.assertEqual([], repeated)

    def test_unmatched_selection_fails_closed(self):
        source = FinanstilsynetShortSaleSource(self.config(isins=["NO0012345678"]))
        source.get = Mock(return_value=Response([instrument()]))
        with self.assertRaisesRegex(SourceError, "did not match"):
            source.fetch()

    def test_malformed_event_fails_closed(self):
        malformed = instrument()
        malformed["events"] = [{}]
        with self.assertRaises(SourceError):
            self.source([malformed]).fetch()


if __name__ == "__main__":
    unittest.main()
