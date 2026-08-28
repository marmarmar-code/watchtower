from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

from watchtower.config import FilterRule, SourceConfig
from watchtower.engine import evaluate
from watchtower.sources.common import SourceError
from watchtower.sources.patentstyret import PatentstyretSource


ORGNR = "123456785"


class Response:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def case(status: str = "Pending", number: str = "2024001") -> dict:
    return {
        "applicationNumber": number,
        "caseType": "Trademark",
        "markName": "Watchtower",
        "status": status,
        "applicationDate": "2026-08-20",
        "caseUrl": (
            "https://services.patentstyret.no/"
            f"search-details/Trademark/{number}"
        ),
    }


class PatentstyretTests(unittest.TestCase):
    def config(self):
        return SourceConfig(
            id="ps",
            kind="patentstyret",
            label="Patentstyret",
            filters=FilterRule(match_all=True),
            options={"companies": [ORGNR]},
        )

    def source(self, payload):
        source = PatentstyretSource(self.config())
        source.get = Mock(return_value=Response(payload))
        return source

    @patch.dict(os.environ, {"PATENTSTYRET_API_KEY": "test-key"})
    def test_case_is_normalized_and_engine_makes_first_run_silent(self):
        source = self.source({"cases": [case()]})
        items = source.fetch_with_state(None)
        state, alerts, baseline = evaluate(self.config(), items, None, max_seen=100)
        state = source.augment_state(state)

        self.assertTrue(baseline)
        self.assertEqual([], alerts)
        self.assertEqual("trademark:2024001", items[0].key)
        self.assertFalse(items[0].suppress_alert)
        self.assertEqual("Pending", items[0].metadata["status"])
        self.assertIn(ORGNR, items[0].metadata["organisation_numbers"])
        self.assertIn("patentstyret", state["source_state"])
        request = source.get.call_args
        self.assertEqual(ORGNR, request.kwargs["params"]["organizationNumber"])
        self.assertEqual(
            "test-key",
            request.kwargs["headers"]["Ocp-Apim-Subscription-Key"],
        )
        self.assertFalse(request.kwargs["allow_redirects"])

    @patch.dict(os.environ, {"PATENTSTYRET_API_KEY": "test-key"})
    def test_status_change_alerts_and_new_case_is_not_suppressed(self):
        first = self.source({"cases": [case("Pending")]})
        first_items = first.fetch_with_state(None)
        state, _, _ = evaluate(self.config(), first_items, None, max_seen=100)
        state = first.augment_state(state)

        second = self.source({"cases": [case("Registered"), case("Pending", "2024002")]})
        items = second.fetch_with_state(state)
        _, alerts, _ = evaluate(self.config(), items, state, max_seen=100)

        self.assertEqual(2, len(alerts))
        updated = next(alert for alert in alerts if alert.change == "updated")
        new = next(alert for alert in alerts if alert.change == "new")
        self.assertIn("Status: Pending → Registered", updated.item.alert_details)
        self.assertEqual("trademark:2024002", new.item.key)
        self.assertFalse(new.item.suppress_alert)

    def test_missing_key_fails_before_network_call(self):
        source = self.source({"cases": []})
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(SourceError, "PATENTSTYRET_API_KEY"):
                source.fetch()
        source.get.assert_not_called()

    def test_invalid_company_rejected(self):
        with self.assertRaisesRegex(ValueError, "valid organisation numbers"):
            PatentstyretSource(
                SourceConfig(
                    id="x",
                    kind="patentstyret",
                    options={"companies": ["123456786"]},
                )
            )

    def test_custom_api_url_is_rejected_to_protect_the_secret(self):
        with self.assertRaisesRegex(ValueError, "does not accept custom"):
            PatentstyretSource(
                SourceConfig(
                    id="x",
                    kind="patentstyret",
                    urls=("https://example.test/register",),
                    options={"companies": [ORGNR]},
                )
            )


if __name__ == "__main__":
    unittest.main()
