from copy import deepcopy
import tempfile
import unittest
from unittest.mock import Mock

from watchtower.config import Config, FilterRule, SourceConfig
from watchtower.engine import evaluate, run
from watchtower.sources.common import SourceError
from watchtower.sources.finanstilsynet_registry import FinanstilsynetRegistrySource
from watchtower.state import StateStore


def licence(code="TEST", holder=101, role="Licensed"):
    return {
        "licensedEntity": {"legalEntityId": holder, "legalEntityName": "Synthetic Holder"},
        "licenceType": {"code": code, "name": {"norwegian": "Syntetisk tillatelse"}},
        "serviceProviderType": role, "licenceClassification": None,
        "registeredDate": "2026-01-01T00:00:00", "hasSecurity": None, "remarks": None,
        "services": [{"serviceId": n, "serviceName": {"norwegian": f"Tjeneste {n}"}} for n in (1, 2)],
    }


def entity(orgnr="123456785", entity_id=101):
    return {"legalEntityId": entity_id, "organisationNumber": orgnr,
            "name": "Synthetic Enterprise", "remarks": None, "licences": [licence()]}


def response(rows, page=1, total=None):
    return Mock(json=Mock(return_value={"page": page, "total": len(rows) if total is None else total,
                                       "hitsReturned": len(rows), "legalEntities": deepcopy(rows)}))


class RegistryTests(unittest.TestCase):
    def source(self, **options):
        config = SourceConfig(id="registry", kind="finanstilsynet_registry", alert_on_update=True,
                              filters=FilterRule(match_all=True), options={"companies": ["123456785"], **options})
        return FinanstilsynetRegistrySource(config)

    def baseline(self, source, rows=None):
        source.get = Mock(return_value=response([entity()] if rows is None else rows))
        items = source.fetch()
        state, alerts, baseline = evaluate(source.config, items, None, max_seen=100)
        self.assertTrue(baseline)
        self.assertEqual([], alerts)
        return source.augment_state(state)

    def test_reordering_and_unmonitored_fields_do_not_generate_updates(self):
        source = self.source()
        row = entity()
        row["licences"].append(licence("OTHER", 102, "Agent"))
        previous = self.baseline(source, [row])
        row["licences"].reverse()
        for lic in row["licences"]:
            lic["services"].reverse()
        row["addresses"] = [{"synthetic": "different"}]
        source.get.return_value = response([row])
        _, alerts, _ = evaluate(source.config, source.fetch_with_state(previous), previous, max_seen=100)
        self.assertEqual([], alerts)

    def test_agent_permission_and_service_changes_have_specific_context(self):
        source = self.source()
        previous = self.baseline(source)
        row = entity()
        row["licences"][0]["services"].append({"serviceId": 3})
        row["licences"].append(licence("AGENT", 202, "Agent"))
        source.get.return_value = response([row])
        _, alerts, _ = evaluate(source.config, source.fetch_with_state(previous), previous, max_seen=100)
        self.assertEqual(1, len(alerts))
        details = "\n".join(alerts[0].item.alert_details)
        self.assertIn("tjenester/instrumenter", details)
        self.assertIn("rolle: Agent", details)
        self.assertIn("innehaver: Synthetic Holder", details)

    def test_missing_company_is_observation_not_claim_of_revocation(self):
        source = self.source()
        previous = self.baseline(source)
        source.get.return_value = response([])
        items = source.fetch_with_state(previous)
        _, alerts, _ = evaluate(source.config, items, previous, max_seen=100)
        self.assertEqual(1, len(alerts))
        self.assertIn("dokumenterer ikke", alerts[0].item.alert_details[0])
        missing = source.augment_state(evaluate(source.config, items, previous, max_seen=100)[0])
        source.get.return_value = response([entity()])
        self.assertIn("nå funnet", source.fetch_with_state(missing)[0].alert_details[0])

    def test_removed_permission_does_not_claim_revocation(self):
        source = self.source()
        previous = self.baseline(source)
        row = entity()
        row["licences"] = []
        source.get.return_value = response([row])
        details = source.fetch_with_state(previous)[0].alert_details
        self.assertIn("Ikke lenger i aktiv tillatelsesliste", details[0])
        self.assertIn("Kontroller årsaken", details[0])

    def test_pagination_and_exact_orgnr_matching(self):
        source = self.source()
        source.get = Mock(side_effect=[response([entity("987654325", 202)], total=2),
                                       response([entity()], page=2, total=2)])
        items = source.fetch()
        self.assertEqual(1, len(items))
        self.assertEqual("org:123456785", items[0].key)
        self.assertEqual([1, 2], [call.kwargs["params"]["page"] for call in source.get.call_args_list])

    def test_partial_malformed_or_shifting_results_preserve_state(self):
        malformed = entity()
        malformed["licences"] = None
        for responses in (
            [response([entity()], total=2), response([], page=2, total=2)],
            [response([entity()], total=2), response([entity("987654325", 202)], page=2, total=3)],
            [response([entity()], total=2), response([entity()], page=2, total=2)],
            [response([malformed])],
        ):
            with self.subTest(responses=responses), tempfile.TemporaryDirectory() as directory:
                source = self.source()
                state = StateStore(directory)
                previous = self.baseline(source)
                state.save("registry", previous)
                source.get = Mock(side_effect=responses)
                result = run(Config((source.config,)), state, None, source_factory=lambda _: source)
                self.assertIn("registry", result.errors)
                self.assertEqual(previous, state.load("registry"))

    def test_result_cap_is_failure_and_new_selection_has_quiet_baseline(self):
        source = self.source(max_pages=1)
        source.get = Mock(return_value=response([entity()], total=2))
        with self.assertRaisesRegex(SourceError, "max_pages"):
            source.fetch()
        source.get.return_value = response([entity()])
        previous = {"initialized": True, "seen": {}, "order": []}
        _, alerts, _ = evaluate(source.config, source.fetch_with_state(previous), previous, max_seen=100)
        self.assertEqual([], alerts)

    def test_configuration_is_explicit_and_bounded(self):
        for options in ({"companies": []}, {"companies": ["123456789"]}, {"max_pages": True}, {"max_pages": 21}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.source(**options)


if __name__ == "__main__":
    unittest.main()
