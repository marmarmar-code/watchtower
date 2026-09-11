from datetime import datetime, timezone
import json
import unittest
from unittest.mock import Mock

from tests.test_change_sources import poll
from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.press_cases import ATTRIBUTES, BASE_URL, PressCasesSource


def config(**options):
    return SourceConfig(id="pfu", kind="press_cases", label="PFU",
                        urls=(BASE_URL,), filters=FilterRule(match_all=True), options=options)


def hit(number="26-089", **changes):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    row = {"caseNumber": number, "complainant": "Ola Nordmann", "mediumId": "medium-1",
           "mediumName": "Eksempelavisen", "processedAt": now.isoformat().replace("+00:00", "Z"),
           "processedAtTimestamp": round(now.timestamp() * 1000), "status": "violation",
           "conclusion": None, "isViolation": True}
    row.update(changes)
    return row


def payload(hits, page=0, total=None, pages=None, page_size=100):
    total = len(hits) if total is None else total
    pages = (0 if total == 0 else (total + page_size - 1) // page_size) if pages is None else pages
    return {"hits": hits, "nbHits": total, "page": page, "nbPages": pages,
            "hitsPerPage": page_size}


def response(value, chunks=None):
    raw = value if isinstance(value, bytes) else json.dumps(value).encode()
    result = Mock()
    result.iter_content.return_value = chunks if chunks is not None else [raw]
    return result


class PressCasesTests(unittest.TestCase):
    def source(self, **options):
        return PressCasesSource(config(**options))

    def test_discovers_stable_case_and_requests_only_bounded_fields(self):
        source = self.source()
        reply = response(payload([hit()]))
        source.post = Mock(return_value=reply)
        item = source.fetch()[0]
        self.assertTrue(item.key.startswith("record:"))
        self.assertEqual(BASE_URL + "26-089", item.url)
        self.assertIsNone(item.published)
        self.assertIn("Brudd", item.text)
        call = source.post.call_args
        self.assertEqual(ATTRIBUTES, call.kwargs["json"]["attributesToRetrieve"])
        self.assertIn("processedAtTimestamp >=", call.kwargs["json"]["filters"])
        self.assertTrue(call.kwargs["stream"])
        reply.close.assert_called_once()

    def test_repeat_and_timestamp_noise_are_quiet_but_outcome_change_alerts(self):
        source = self.source()
        base = hit()
        source.post = Mock(return_value=response(payload([base])))
        state, alerts = poll(source)
        self.assertEqual([], alerts)
        later = datetime.fromisoformat(base["processedAt"].replace("Z", "+00:00")).replace(hour=12)
        noisy = {**base, "processedAt": later.isoformat().replace("+00:00", "Z"),
                 "processedAtTimestamp": round(later.timestamp() * 1000)}
        source.post = Mock(return_value=response(payload([noisy])))
        state2, alerts = poll(source, state)
        self.assertEqual([], alerts)
        changed = {**noisy, "status": "criticism", "isViolation": False}
        source.post = Mock(return_value=response(payload([changed])))
        _, alerts = poll(source, state2)
        self.assertEqual(1, len(alerts))
        text = " ".join(alerts[0].item.alert_details)
        self.assertIn("Endret PFU-avgjørelse", text)
        self.assertIn("Brudd → Kritikk", text)

    def test_paging_is_complete_and_rejects_duplicates_or_changed_total(self):
        source = self.source(page_size=1, max_pages=2)
        source.post = Mock(side_effect=[response(payload([hit("26-089")], 0, 2, 2, 1)),
                                        response(payload([hit("26-090")], 1, 2, 2, 1))])
        source.fetch()
        self.assertEqual({"26-089", "26-090"}, set(source._next["rows"]))
        for second in (payload([hit("26-089")], 1, 2, 2, 1),
                       payload([hit("26-090")], 1, 3, 3, 1),
                       payload([], 1, 2, 2, 1)):
            source.post = Mock(side_effect=[response(payload([hit("26-089")], 0, 2, 2, 1)), response(second)])
            with self.assertRaises(SourceError):
                source.fetch()

    def test_invalid_identity_status_and_treatment_time_fail_closed(self):
        now = hit()
        bad_rows = [
            {**now, "caseNumber": "case-89"},
            {**now, "mediumId": 1},
            {**now, "status": "unknown"},
            {**now, "isViolation": "true"},
            {**now, "processedAtTimestamp": now["processedAtTimestamp"] + 10},
            {**now, "complainant": []},
            {**now, "conclusion": "x" * 4001},
        ]
        for row in bad_rows:
            with self.subTest(row=row):
                source = self.source()
                source.post = Mock(return_value=response(payload([row])))
                with self.assertRaises(SourceError):
                    source.fetch()

    def test_invalid_json_schema_size_and_empty_window(self):
        source = self.source(max_bytes=1024)
        reply = response(b"x" * 1025)
        source.post = Mock(return_value=reply)
        with self.assertRaisesRegex(SourceError, "max_bytes"):
            source.fetch()
        reply.close.assert_called_once()
        for value in (b"not json", {"hits": []}, payload([], pages=1)):
            source = self.source()
            source.post = Mock(return_value=response(value))
            with self.assertRaises(SourceError):
                source.fetch()
        source = self.source()
        source.post = Mock(return_value=response(payload([])))
        self.assertEqual([], source.fetch())

    def test_configuration_rejects_manual_ids_and_removal_claims(self):
        invalid = [
            config(case_numbers=["25-076"]),
            config(events=["removed"]),
            config(complete_snapshot=True),
            SourceConfig(id="pfu", kind="press_cases", label="PFU",
                         urls=("https://example.test/",), filters=FilterRule(match_all=True), options={}),
            config(page_size=True),
            config(allow_empty="yes"),
        ]
        for candidate in invalid:
            with self.subTest(options=candidate.options), self.assertRaises(ValueError):
                PressCasesSource(candidate)


if __name__ == "__main__":
    unittest.main()
