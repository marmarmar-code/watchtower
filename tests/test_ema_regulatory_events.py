from copy import deepcopy
import json
import unittest
from unittest.mock import Mock

from tests.test_change_sources import poll, response
from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.ema_regulatory_events import DATASETS, EmaRegulatoryEventsSource


def config(dataset="dhpc", **options):
    return SourceConfig(id="ema-events", kind="ema_regulatory_events", label="EMA events",
        urls=(DATASETS.get(dataset, DATASETS["dhpc"])["url"],), filters=FilterRule(match_all=True),
        options={"dataset": dataset, "max_records": 5000, "max_bytes": 3000000,
                 "events": ["added", "changed"], **options})


def row(dataset="dhpc"):
    values = {name: "" for name in DATASETS[dataset]["schema"]}
    values.update(first_published_date="10/09/2026", last_updated_date="11/09/2026")
    if dataset == "dhpc":
        values.update(category="Human", name_of_medicine="Medicine A", active_substances="substance",
            dhpc_type="Safety signal", regulatory_outcome="Variation",
            dhpc_url="https://www.ema.europa.eu/en/medicines/dhpc/medicine-a")
    elif dataset == "referrals":
        values.update(category="Human", referral_name="Medicine safety review", current_status="Under evaluation",
            safety_referral="Yes", referral_type="Article 31 referrals",
            referral_url="https://www.ema.europa.eu/en/medicines/human/referrals/medicine-safety-review")
    else:
        values.update(category="", active_substances_in_scope_of_procedure="substance",
            regulatory_outcome="Maintenance", procedure_number="PSUSA/1",
            psusa_url="https://www.ema.europa.eu/en/medicines/psusa/psusa-1")
    return values


def payload(rows, total=None, timestamp="2026-09-11T06:00:00Z"):
    return {"meta": {"total_records": len(rows) if total is None else total, "timestamp": timestamp}, "data": rows}


def source(dataset="dhpc", values=None, **options):
    item = EmaRegulatoryEventsSource(config(dataset, **options))
    item.get = Mock(return_value=response(payload([row(dataset)]) if values is None else values))
    return item


class EmaRegulatoryEventsTests(unittest.TestCase):
    def test_three_dataset_selections_and_date_semantics(self):
        for dataset in DATASETS:
            records = source(dataset).read_records()
            self.assertEqual(1, len(records)); self.assertEqual("2026-09-10", records[0]["published"])
        shortage = row(); shortage["dhpc_type"] = "Medicine shortage"
        with self.assertRaisesRegex(SourceError, "selection is empty"):
            source(values=payload([shortage])).read_records()
        not_safety = row("referrals"); not_safety["safety_referral"] = "No"
        with self.assertRaisesRegex(SourceError, "selection is empty"):
            source("referrals", payload([not_safety])).read_records()

    def test_quiet_baseline_repeat_metadata_and_substantive_change(self):
        current = payload([row()]); state, alerts = poll(source(values=current)); self.assertEqual([], alerts)
        metadata = deepcopy(current); metadata["meta"]["timestamp"] = "2026-09-11T18:00:00Z"
        metadata["data"][0]["last_updated_date"] = "12/09/2026"
        state, alerts = poll(source(values=metadata), state); self.assertEqual([], alerts)
        changed = deepcopy(metadata); changed["data"][0]["regulatory_outcome"] = "Suspension"
        _, alerts = poll(source(values=changed), state)
        self.assertEqual(1, len(alerts)); self.assertIn("Variation → Suspension", " ".join(alerts[0].item.alert_details))

    def test_total_gap_duplicate_url_and_empty_fail_closed(self):
        duplicate = row(); other = deepcopy(duplicate); other["name_of_medicine"] = "Medicine B"
        for value in (payload([row()], total=2), payload([duplicate, other]), payload([])):
            with self.assertRaises(SourceError): source(values=value).read_records()

    def test_invalid_dates_enums_and_urls_fail_closed(self):
        mutations = []
        bad = row(); bad["first_published_date"] = "2026-09-10"; mutations.append(("dhpc", bad))
        bad = row(); bad["first_published_date"] = "1/09/2026"; mutations.append(("dhpc", bad))
        bad = row(); bad["regulatory_outcome"] = "Unknown"; mutations.append(("dhpc", bad))
        bad = row(); bad["dhpc_url"] = "https://example.test/item"; mutations.append(("dhpc", bad))
        bad = row(); bad["dhpc_url"] = "https://www.ema.europa.eu:443/en/medicines/dhpc/item"; mutations.append(("dhpc", bad))
        bad = row("referrals"); bad["current_status"] = "Unknown"; mutations.append(("referrals", bad))
        bad = row("referrals"); bad["safety_referral"] = "true"; mutations.append(("referrals", bad))
        bad = row("psusa"); bad["category"] = "Veterinary"; mutations.append(("psusa", bad))
        for dataset, value in mutations:
            with self.assertRaises(SourceError): source(dataset, payload([value])).read_records()
        with self.assertRaises(SourceError):
            source(values=payload([row()], timestamp="2026-09-11Z")).read_records()

    def test_schema_and_types_are_exact(self):
        missing = row(); missing.pop("active_substances")
        wrong = row(); wrong["active_substances"] = []
        for value in (payload([missing]), payload([wrong]), {"meta": {}, "data": []}, b"not json"):
            item = source(values=value)
            with self.assertRaises(SourceError): item.read_records()

    def test_configuration_is_fixed_and_rejects_removals(self):
        non_text = SourceConfig(id="e2", kind="ema_regulatory_events", label="E",
                   urls=(DATASETS["dhpc"]["url"],), filters=FilterRule(match_all=True), options={"dataset": ["dhpc"]})
        invalid = [config(dataset="unknown"), non_text, config(events=["removed"]), config(complete_snapshot=True),
                   config(allow_empty=True), SourceConfig(id="e", kind="ema_regulatory_events", label="E",
                   urls=("https://example.test/data",), filters=FilterRule(match_all=True), options={"dataset": "dhpc"})]
        for candidate in invalid:
            with self.assertRaises(ValueError): EmaRegulatoryEventsSource(candidate)


if __name__ == "__main__": unittest.main()
