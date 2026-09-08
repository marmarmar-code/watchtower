from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from watchtower.config import Config, FilterRule, SourceConfig
from watchtower.engine import evaluate, run
from watchtower.sources.changes import SnapshotSource, document, public_url
from watchtower.sources.common import SourceError
from watchtower.sources.structured import StructuredSource
from watchtower.sources.web_changes import WebChangesSource
from watchtower.state import StateStore


def config(kind='json_records', **options):
    return SourceConfig(id='records', kind=kind, label='Synthetic records', urls=('https://example.test/data',),
                        filters=FilterRule(match_all=True), options=options)


def response(data, status=200, headers=None):
    if not isinstance(data, bytes):
        data = json.dumps(data).encode()
    return Mock(status_code=status, headers=headers or {}, iter_content=Mock(return_value=[data]))


def poll(source, previous=None):
    items=source.fetch_with_state(previous)
    state, alerts, baseline=evaluate(source.config, items, previous, max_seen=3000)
    return source.augment_state(state), alerts


class SnapshotTests(unittest.TestCase):
    def source(self, **options):
        source=StructuredSource(config(id_fields=['id'], fields=['value','status'], **options))
        source.get=Mock(return_value=response([{'id':'A','value':100,'status':'active'}]))
        return source

    def test_reorder_and_ignored_fields_do_not_alert_but_selected_changes_do(self):
        source=self.source()
        rows=[{'id':'A','value':100,'status':'active'}, {'id':'B','value':50,'status':'active'}]
        source.get.return_value=response(rows)
        state, alerts=poll(source)
        self.assertEqual([],alerts)
        source.get.return_value=response([{**row,'updated':'a new transport timestamp'} for row in reversed(rows)])
        state, alerts=poll(source,state)
        self.assertEqual([],alerts)
        rows[0]['status']='closed'
        source.get.return_value=response(rows)
        _,alerts=poll(source,state)
        self.assertEqual(1,len(alerts))
        self.assertIn('active → closed',' '.join(alerts[0].item.alert_details))

    def test_threshold_accumulates_without_small_steps_erasing_baseline(self):
        source=self.source(thresholds={'value':{'absolute':10,'percent':5}})
        state,_=poll(source)
        for value,count in ((104,0),(108,0),(111,1),(115,0)):
            source.get.return_value=response([{'id':'A','value':value,'status':'active'}])
            state,alerts=poll(source,state)
            self.assertEqual(count,len(alerts))
            if alerts:self.assertIn('100 → 111',' '.join(alerts[0].item.alert_details))

    def test_non_numeric_and_zero_transitions_are_not_hidden(self):
        source=self.source(thresholds={'value':{'percent':5}})
        state,_=poll(source)
        for value in (0,2,None):
            source.get.return_value=response([{'id':'A','value':value,'status':'active'}])
            state,alerts=poll(source,state)
            self.assertEqual(1,len(alerts))

    def test_confirmed_disappearance_and_reappearance(self):
        source=self.source(events=['added','changed','removed'], complete_snapshot=True, allow_empty=True)
        state,_=poll(source)
        source.get.return_value=response([])
        state,alerts=poll(source,state)
        self.assertEqual([],alerts)
        state,alerts=poll(source,state)
        self.assertEqual('removed',alerts[0].item.metadata['event'])
        state,alerts=poll(source,state)
        self.assertEqual([],alerts)
        source.get.return_value=response([{'id':'A','value':100,'status':'active'}])
        _,alerts=poll(source,state)
        self.assertEqual('added',alerts[0].item.metadata['event'])

    def test_nonempty_failure_does_not_advance_disappearance_confirmation(self):
        source=self.source(events=['removed'],complete_snapshot=True,allow_empty=True)
        previous,_=poll(source)
        source.get.return_value=response([])
        previous,_=poll(source,previous)
        with tempfile.TemporaryDirectory() as tmp:
            store=StateStore(tmp);store.save('records',previous)
            source.get.return_value=response({'unexpected':[]})
            result=run(Config((source.config,)),store,None,source_factory=lambda _:source)
            self.assertIn('records',result.errors)
            self.assertEqual(previous,store.load('records'))

    def test_rolling_window_never_claims_removal_and_selection_changes_rebaseline(self):
        source=self.source(allow_empty=True)
        previous,_=poll(source)
        source.get.return_value=response([])
        _,alerts=poll(source,previous)
        self.assertEqual([],alerts)
        altered=self.source(where={'id':['A']})
        altered.get.return_value=response([{'id':'A','value':200,'status':'active'}])
        _,alerts=poll(altered,previous)
        self.assertEqual([],alerts)
        with self.assertRaisesRegex(ValueError,'complete_snapshot'):
            self.source(events=['removed'])

    def test_transient_changes_require_identical_confirmations(self):
        source=self.source(change_confirmations=2)
        previous,_=poll(source)
        for value,count in ((200,0),(100,0),(200,0),(201,0),(201,1)):
            source.get.return_value=response([{'id':'A','value':value,'status':'active'}])
            previous,alerts=poll(source,previous)
            self.assertEqual(count,len(alerts))

    def test_event_selection_suppresses_updates_but_not_new_records(self):
        source=self.source(events=['added'])
        previous,_=poll(source)
        source.get.return_value=response([{'id':'A','value':200,'status':'active'},{'id':'B','value':3,'status':'active'}])
        _,alerts=poll(source,previous)
        self.assertEqual(1,len(alerts))
        self.assertEqual('added',alerts[0].item.metadata['event'])

    def test_invalid_rules_are_rejected(self):
        for options in ({'thresholds':{'value':{'percent':True}}},{'thresholds':{'value':{'percent':-1}}},
                        {'thresholds':{'missing':{'absolute':1}}},{'events':['anything']},{'max_records':False}):
            with self.subTest(options=options),self.assertRaises(ValueError):self.source(**options)


class StructuredTests(unittest.TestCase):
    def test_nested_fields_and_exact_row_selection(self):
        source=StructuredSource(config(id_fields=['meta.id'],fields=['meta.value'],records_path='data.rows',where={'meta.group':['A']}))
        source.get=Mock(return_value=response({'data':{'rows':[{'meta':{'id':'one','value':1,'group':'A'}},{'meta':{'id':'two','value':2,'group':'B'}}]}}))
        items=source.fetch()
        self.assertEqual(1,len(items))
        self.assertIn('"meta.value":1',items[0].text)

    def test_complete_pagination_and_duplicate_or_shifting_totals_fail(self):
        source=StructuredSource(config(id_fields=['id'],fields=['value'],records_path='rows',next_path='next',total_path='total',max_pages=2))
        first={'rows':[{'id':'A','value':1}],'next':'?page=2','total':2}
        second={'rows':[{'id':'B','value':2}],'next':None,'total':2}
        source.get=Mock(side_effect=[response(first),response(second)])
        self.assertEqual(2,len(source.fetch()))
        for bad in ({**second,'total':3},{**second,'rows':[{'id':'A','value':2}]},{**second,'rows':[]}):
            source.get=Mock(side_effect=[response(first),response(bad)])
            with self.assertRaises(SourceError):source.fetch()
        source.get=Mock(return_value=response({**first,'next':'https://another.test/page'}))
        with self.assertRaisesRegex(SourceError,'host'):source.fetch()

    def test_pagination_cap_missing_fields_and_empty_results_fail(self):
        source=StructuredSource(config(id_fields=['id'],fields=['value'],records_path='rows',next_path='next'))
        for payload in ({'rows':[{'id':'A','value':1}],'next':'?page=2'},
                        {'rows':[{'id':'A'}],'next':None},{'rows':[],'next':None}):
            source.get=Mock(return_value=response(payload))
            with self.assertRaises(SourceError):source.fetch()

    def test_csv_handles_quoted_fields_but_rejects_ambiguous_rows(self):
        source=StructuredSource(config('csv_records',id_fields=['id'],fields=['value'],delimiter=';'))
        source.get=Mock(return_value=response(b'id;value;ignored\nA;"value; with separator";x\n'))
        self.assertEqual(1,len(source.fetch()))
        for raw in (b'id;value;value\nA;1;2\n',b'id;value\nA\n',b'id;value\nA;1;2\n',b'id;value\nA;1\nA;2\n'):
            source.get.return_value=response(raw)
            with self.assertRaises(SourceError):source.fetch()

    def test_response_size_and_redirect_destinations_are_checked_before_parsing(self):
        source=StructuredSource(config(id_fields=['id'],fields=['value'],max_bytes=1024))
        source.get=Mock(return_value=response(b'x'*1025))
        with self.assertRaisesRegex(SourceError,'max_bytes'):source.fetch()
        source.get=Mock(return_value=response(b'',302,{'Location':'https://127.0.0.1/private'}))
        with self.assertRaises(ValueError):source.fetch()
        self.assertEqual(1,source.get.call_count)
        for url in ('http://example.test/data','https://user:password@example.test/data','https://localhost/data'):
            with self.assertRaises(ValueError):public_url(url)


class WebTests(unittest.TestCase):
    def test_selected_text_ignores_chrome_and_reports_change_at_end_of_long_page(self):
        source=WebChangesSource(config('web_page',selector='main',ignore_selectors=['.clock']))
        def page(number):return ('<nav>Different</nav><main><span class="clock">now</span><p>'+('Synthetic context '*500)+f'Price {number}</p></main>').encode()
        source.get=Mock(return_value=response(page(10)))
        previous,_=poll(source)
        source.get.return_value=response(page(20))
        previous,alerts=poll(source,previous)
        self.assertEqual([],alerts)
        _,alerts=poll(source,previous)
        self.assertEqual(1,len(alerts))
        detail=' '.join(alerts[0].item.alert_details)
        self.assertIn('Price 10',detail)
        self.assertIn('Price 20',detail)
        self.assertLess(len(detail),1000)

    def test_missing_selector_is_failure_and_links_use_stable_absolute_urls(self):
        source=WebChangesSource(config('web_links',selector='main a',title_selector='h2',events=['added']))
        source.get=Mock(return_value=response(b'<main><a href="/one#anchor"><h2>One</h2>ignored date</a></main>'))
        previous,_=poll(source)
        source.get.return_value=response(b'<main><a href="/one#new"><h2>One</h2>new date</a><a href="/two"><h2>Two</h2></a></main>')
        _,alerts=poll(source,previous)
        self.assertEqual(1,len(alerts))
        self.assertEqual('https://example.test/two',alerts[0].item.url)
        self.assertEqual('Two',alerts[0].item.title)
        source.get.return_value=response(b'<main>Layout changed</main>')
        with self.assertRaises(SourceError):source.fetch()


if __name__=='__main__':unittest.main()
