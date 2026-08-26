from __future__ import annotations

import unittest

from watchtower.config import FilterRule, SourceConfig
from watchtower.engine import SOURCE_TYPES, evaluate
from watchtower.sources.brreg import BrregSource


ORGNR = "999999999"


def entity(*, bankrupt=False):
    return {
        "name": "Example Publishing AS",
        "organisation_form": {"code": "AS", "description": "Aksjeselskap"},
        "industry": {"code": "58.130", "description": "Utgivelse av aviser"},
        "bankrupt": bankrupt,
        "liquidating": False,
        "forced_liquidation": False,
        "deleted": False,
        "removed": False,
        "unknown": False,
    }


def roles(chair="Ada Example"):
    return {
        "DAGL": ["Editor Example"],
        "LEDE": [chair],
        "NEST": [],
        "MEDL": ["Board Example"],
    }


def account(report_id=100, period_to="2025-12-31"):
    return {
        "id": report_id,
        "period_to": period_to,
        "report_type": "AAR",
        "journal_number": "J-EXAMPLE",
    }


class BrregTests(unittest.TestCase):
    def config(self):
        return SourceConfig(
            id="brreg-test",
            kind="brreg",
            label="BRREG",
            filters=FilterRule(include_any=(ORGNR,)),
            options={
                "companies": [ORGNR],
                "events": ["annual_accounts", "company", "roles"],
            },
        )

    def source(self, *, current_entity=None, current_roles=None, current_account=None):
        source = BrregSource(self.config())
        source._entity = lambda _: current_entity or entity()
        source._roles = lambda _: current_roles or roles()
        source._latest_account = lambda _: current_account or account()
        return source

    def test_brreg_source_is_registered(self):
        self.assertIs(SOURCE_TYPES["brreg"], BrregSource)

    def test_first_run_is_silent_and_persists_private_snapshot(self):
        source = self.source()
        items = source.fetch_with_state(None)
        state, alerts, baseline = evaluate(self.config(), items, None, max_seen=100)
        state = source.augment_state(state)

        self.assertTrue(baseline)
        self.assertEqual([], alerts)
        self.assertIn("brreg", state["source_state"])
        self.assertEqual("Ada Example", state["source_state"]["brreg"][ORGNR]["roles"]["LEDE"][0])

    def test_role_change_and_new_annual_account_alert_after_baseline(self):
        first = self.source()
        items = first.fetch_with_state(None)
        state, _, _ = evaluate(self.config(), items, None, max_seen=100)
        state = first.augment_state(state)

        second = self.source(
            current_roles=roles("Grace Example"),
            current_account=account(101, "2026-12-31"),
        )
        items = second.fetch_with_state(state)
        next_state, alerts, baseline = evaluate(self.config(), items, state, max_seen=100)
        next_state = second.augment_state(next_state)

        self.assertFalse(baseline)
        self.assertEqual(2, len(alerts))
        titles = {alert.item.title for alert in alerts}
        self.assertIn("Rolleendring: Example Publishing AS", titles)
        self.assertIn("Nytt årsregnskap: Example Publishing AS", titles)
        role_alert = next(alert for alert in alerts if alert.item.metadata["event"] == "roles")
        self.assertIn("Styreleder: Ada Example → Grace Example", role_alert.item.text)
        self.assertEqual("Grace Example", next_state["source_state"]["brreg"][ORGNR]["roles"]["LEDE"][0])

    def test_company_status_change_is_described(self):
        first = self.source()
        items = first.fetch_with_state(None)
        state, _, _ = evaluate(self.config(), items, None, max_seen=100)
        state = first.augment_state(state)

        second = self.source(current_entity=entity(bankrupt=True))
        items = second.fetch_with_state(state)
        _, alerts, _ = evaluate(self.config(), items, state, max_seen=100)

        company_alerts = [a for a in alerts if a.item.metadata["event"] == "company"]
        self.assertEqual(1, len(company_alerts))
        self.assertIn("Konkurs: ja", company_alerts[0].item.text)


if __name__ == "__main__":
    unittest.main()
