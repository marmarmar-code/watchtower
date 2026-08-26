from __future__ import annotations

import unittest

from watchtower.config import FilterRule, SourceConfig
from watchtower.engine import SOURCE_TYPES, evaluate
from watchtower.sources.brreg import BrregSource
from watchtower.sources.common import SourceError


ORGNR = "999999999"


class Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


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
    def config(self, *, companies=None):
        return SourceConfig(
            id="brreg-test",
            kind="brreg",
            label="BRREG",
            filters=FilterRule(match_all=True),
            options={
                "companies": companies or [ORGNR],
                "events": ["annual_accounts", "company", "roles"],
            },
        )

    def source(self, *, current_entity=None, current_roles=None, current_account=None):
        source = BrregSource(self.config())
        source._entity = lambda _: current_entity or entity()
        source._roles = lambda _: current_roles or roles()
        source._latest_account = lambda _: current_account or account()
        return source

    def baseline(self):
        source = self.source()
        items = source.fetch_with_state(None)
        state, alerts, baseline = evaluate(self.config(), items, None, max_seen=100)
        return source.augment_state(state), alerts, baseline

    def test_brreg_source_is_registered(self):
        self.assertIs(SOURCE_TYPES["brreg"], BrregSource)

    def test_valid_norwegian_organisation_number_is_accepted(self):
        source = BrregSource(self.config(companies=[ORGNR]))
        self.assertEqual((ORGNR,), source.companies)

    def test_invalid_check_digit_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "valid 9-digit"):
            BrregSource(self.config(companies=["999999998"]))

    def test_missing_entity_fails_closed(self):
        source = BrregSource(self.config())
        source.get = lambda *_args, **_kwargs: Response(404)
        with self.assertRaisesRegex(SourceError, "not found"):
            source._entity(ORGNR)

    def test_removed_entity_is_canonicalized(self):
        source = BrregSource(self.config())
        source.get = lambda *_args, **_kwargs: Response(410)
        result = source._entity(ORGNR)
        self.assertTrue(result["removed"])

    def test_first_run_is_silent_and_persists_private_snapshot(self):
        state, alerts, baseline = self.baseline()

        self.assertTrue(baseline)
        self.assertEqual([], alerts)
        self.assertIn("brreg", state["source_state"])
        self.assertEqual("Ada Example", state["source_state"]["brreg"][ORGNR]["roles"]["LEDE"][0])
        self.assertIn(f"company:{ORGNR}", state["seen"])
        self.assertIn(f"roles:{ORGNR}", state["seen"])

    def test_role_change_and_new_annual_account_alert_after_baseline(self):
        state, _, _ = self.baseline()

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
        self.assertIn(
            "Styreleder: Ada Example → Grace Example",
            role_alert.item.alert_details,
        )
        self.assertEqual("Grace Example", next_state["source_state"]["brreg"][ORGNR]["roles"]["LEDE"][0])

    def test_unchanged_run_after_change_does_not_repeat_alert(self):
        state, _, _ = self.baseline()

        changed = self.source(
            current_roles=roles("Grace Example"),
            current_account=account(101, "2026-12-31"),
        )
        changed_items = changed.fetch_with_state(state)
        changed_state, alerts, _ = evaluate(self.config(), changed_items, state, max_seen=100)
        changed_state = changed.augment_state(changed_state)
        self.assertEqual(2, len(alerts))

        unchanged = self.source(
            current_roles=roles("Grace Example"),
            current_account=account(101, "2026-12-31"),
        )
        unchanged_items = unchanged.fetch_with_state(changed_state)
        final_state, repeated, baseline = evaluate(
            self.config(), unchanged_items, changed_state, max_seen=100
        )
        final_state = unchanged.augment_state(final_state)

        self.assertFalse(baseline)
        self.assertEqual([], repeated)
        self.assertEqual(
            changed_state["source_state"]["brreg"],
            final_state["source_state"]["brreg"],
        )

    def test_company_status_change_is_described(self):
        state, _, _ = self.baseline()

        second = self.source(current_entity=entity(bankrupt=True))
        items = second.fetch_with_state(state)
        _, alerts, _ = evaluate(self.config(), items, state, max_seen=100)

        company_alerts = [a for a in alerts if a.item.metadata["event"] == "company"]
        self.assertEqual(1, len(company_alerts))
        self.assertIn("Konkurs: ja", company_alerts[0].item.text)
        self.assertIn("Konkurs: ja", company_alerts[0].item.alert_details)


if __name__ == "__main__":
    unittest.main()
