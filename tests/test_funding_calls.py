import unittest
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from watchtower.config import SourceConfig, FilterRule
from watchtower.sources.funding_calls import FundingCallsSource, API
from test_change_sources import poll
from watchtower.sources.common import SourceError
from watchtower.sources.funding_calls import _records

def payload(meta):
    return {"totalResults": 1, "pageNumber": 1, "pageSize": 1, "warnings": [], "results": [{"metadata": meta}]}

def meta(**kw):
    base = {"identifier": ["TOPIC-1"], "title": ["Energy call"], "status": ["31094502"], "deadlineDate": ["2026-09-15T00:00:00.000+0000"], "frameworkProgramme": ["43108390"], "language": ["en"], "url": ["https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/topic-details/TOPIC-1"]}
    base.update(kw); return base

class FundingTests(unittest.TestCase):
    def test_parses_scoped_record(self):
        rows = _records(payload(meta()), "2026-09-14T00:00:00.000+0000", "2026-09-21T23:59:59.999+0000", 1)
        self.assertEqual(rows[0]["key"], "TOPIC-1")
        self.assertEqual(rows[0]["fields"]["deadline"], "2026-09-15")
    def test_rejects_incomplete_and_bool_page(self):
        p = payload(meta()); p["totalResults"] = 2
        with self.assertRaisesRegex(SourceError, "incomplete"): _records(p, "2026-09-14T00:00:00.000+0000", "2026-09-21T23:59:59.999+0000", 2)
        p = payload(meta()); p["pageNumber"] = True
        with self.assertRaisesRegex(SourceError, "incomplete"): _records(p, "2026-09-14T00:00:00.000+0000", "2026-09-21T23:59:59.999+0000", 1)
    def test_rejects_scope_status_and_url(self):
        for change, error in [({"language":["de"]}, "English"), ({"status":["closed"]}, "unsupported"), ({"url":["https://example.com/TOPIC-1"]}, "official")]:
            with self.subTest(error=error), self.assertRaisesRegex(SourceError, error): _records(payload(meta(**change)), "2026-09-14T00:00:00.000+0000", "2026-09-21T23:59:59.999+0000", 1)
    def test_rejects_outside_datetime_window(self):
        with self.assertRaisesRegex(SourceError, "outside"): _records(payload(meta(deadlineDate=["2026-09-30T00:00:00.000+0000"])), "2026-09-14T00:00:00.000+0000", "2026-09-21T23:59:59.999+0000", 1)

    def test_duplicate_topics_and_unsafe_urls(self):
        p = payload(meta()); p.update(totalResults=2, pageSize=2); p['results'] *= 2
        with self.assertRaisesRegex(SourceError, 'duplicate'):
            _records(p, '2026-09-14T00:00:00.000+0000', '2026-09-21T23:59:59.999+0000', 2)
        url = meta()['url'][0]
        for bad in [url.replace('ec.europa.eu', 'user@ec.europa.eu'), url.replace('ec.europa.eu', 'ec.europa.eu:443'), url+'?x=1', url+'#x']:
            with self.subTest(url=bad), self.assertRaisesRegex(SourceError, 'official'):
                _records(payload(meta(url=[bad])), '2026-09-14T00:00:00.000+0000', '2026-09-21T23:59:59.999+0000', 1)

    def test_real_fetch_contract_and_change_detection(self):
        source = FundingCallsSource(SourceConfig(id='calls', kind='funding_calls', label='Funding calls',
            urls=(API,), filters=FilterRule(match_all=True), options={'max_records': 1, 'allow_empty': True}))
        day = datetime.now(timezone.utc).date() + timedelta(days=1)
        p = payload(meta(deadlineDate=[day.isoformat()+'T00:00:00.000+0000']))
        def reply(value, status=200):
            return Mock(status_code=status, iter_content=Mock(return_value=[json.dumps(value).encode()]), close=Mock())
        source.post = Mock(side_effect=lambda *a, **kw: reply(p))
        state, alerts = poll(source); self.assertEqual([], alerts)
        self.assertFalse(source.post.call_args.kwargs['allow_redirects'])
        p['results'][0]['metadata']['title'] = ['Cosmetic title correction']
        after, alerts = poll(source, state); self.assertEqual([], alerts)
        p['results'][0]['metadata']['deadlineDate'] = [(day+timedelta(days=1)).isoformat()+'T00:00:00.000+0000']
        after, alerts = poll(source, after); self.assertEqual(1, len(alerts))
        self.assertIn('Søknadsfrist', ' '.join(alerts[0].item.alert_details))
        p['results'][0]['metadata']['status'] = ['31094501']
        after, alerts = poll(source, after); self.assertEqual(1, len(alerts))
        p.update(totalResults=0, results=[])
        _, alerts = poll(source, after); self.assertEqual([], alerts)
        r = reply({}, 302); source.post = Mock(return_value=r)
        with self.assertRaisesRegex(SourceError, 'redirect'): source.read_records()
        r.close.assert_called_once()
        source.max_bytes = 1024; r = reply('x'*1025); source.post.return_value = r
        with self.assertRaisesRegex(SourceError, 'max_bytes'): source.read_records()
        r.close.assert_called_once()
        for options in ({'events': ['removed']}, {'complete_snapshot': True}, {'max_records': 101}):
            with self.assertRaises(ValueError):
                FundingCallsSource(SourceConfig(id='calls', kind='funding_calls', label='Funding calls', options=options))

if __name__ == "__main__": unittest.main()
