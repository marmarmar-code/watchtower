from copy import deepcopy
from datetime import date
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from watchtower.config import Config,SourceConfig,FilterRule
from watchtower.engine import run
from watchtower.state import StateStore
from watchtower.sources.rasff_border_rejections import RasffBorderRejectionsSource,URL
from watchtower.sources.common import SourceError
from test_change_sources import response,poll


def notice(ident=101,reference='2026.1001',stamp='14-09-2026 10:00:00',**values):
    return {'notifId':ident,'reference':reference,'ecValidationDate':stamp,'subject':'Example rejected consignment',
            'notifyingCountry':{'isoCode':'NO','organizationName':'Norway'},'originCountries':[{'isoCode':'DK','organizationName':'Denmark'}],
            'productCategory':{'id':1,'description':'Example category'},'productType':{'id':283,'description':'food'},
            'notificationClassification':{'id':305,'description':'border rejection notification'},
            'riskDecision':{'id':2,'description':'potential risk'},'published':False,**values}


def payload(rows=None,total=None,pages=None):
    rows=[notice()] if rows is None else rows;total=len(rows) if total is None else total
    return {'notifications':rows,'totalElements':total,'totalPages':(total+99)//100 if pages is None else pages}


def source(**options):
    return RasffBorderRejectionsSource(SourceConfig(id='rasff',kind='rasff_border_rejections',label='Border rejections',urls=(),filters=FilterRule(match_all=True),options=options))


def install(s,*pages):
    pages=pages or (payload(),);s.post=Mock(side_effect=[response(p) for p in (*pages,*pages)])


class RasffTests(unittest.TestCase):
    def setUp(self):
        p=patch('watchtower.sources.rasff_border_rejections.today',return_value=date(2026,9,15));p.start();self.addCleanup(p.stop)

    def test_baseline_repeat_and_exact_public_request(self):
        s=source();install(s);old,alerts=poll(s);self.assertEqual([],alerts);r=old['source_state']['records']['rows']['101']['row']
        self.assertIsNone(r['published']);self.assertEqual('2026-09-14 10:00:00',r['fields']['validated_at']);self.assertEqual('potential risk',r['fields']['risk']['description'])
        call=s.post.call_args;self.assertEqual(URL,call.args[0]);body=call.kwargs['json'];self.assertEqual([305],body['notificationClassification'])
        self.assertEqual('17-08-2026 00:00:00',body['ecValidDateFrom']);self.assertEqual('15-09-2026 00:00:00',body['ecValidDateTo'])
        install(s);new,alerts=poll(s,old);self.assertEqual(old,new);self.assertEqual([],alerts)

    def test_risk_and_subject_changes_once(self):
        for n in [notice(riskDecision={'id':3,'description':'not serious'}),notice(subject='Corrected consignment description')]:
            s=source();install(s);old,_=poll(s);body=payload([n]);install(s,body);new,alerts=poll(s,old)
            self.assertEqual(1,len(alerts));self.assertEqual('changed',alerts[0].item.metadata['event'])
            self.assertIn('ikke salg eller distribusjon i Norge',' '.join(alerts[0].item.alert_details))
            install(s,body);self.assertEqual([],poll(s,new)[1])

    def test_new_notification_once_and_date_window_absence(self):
        s=source();install(s);old,_=poll(s);body=payload([notice(),notice(102,'2026.1002')]);install(s,body)
        new,alerts=poll(s,old);self.assertEqual(1,len(alerts));self.assertEqual('added',alerts[0].item.metadata['event'])
        install(s);missing,alerts=poll(s,new);self.assertEqual([],alerts);self.assertEqual(new['seen'],missing['seen'])
        install(s,body);self.assertEqual([],poll(s,missing)[1])

    def test_empty_window_is_explicit_and_quiet(self):
        s=source(allow_empty=True);install(s,payload([]));empty,alerts=poll(s);self.assertEqual([],alerts);self.assertEqual({},empty['source_state']['records']['rows'])
        install(s);new,alerts=poll(s,empty);self.assertEqual(1,len(alerts))
        install(s,payload([]));end,alerts=poll(s,new);self.assertEqual([],alerts);self.assertEqual(new['seen'],end['seen'])

    def test_origin_country_selection_is_exact_and_all_rows_validated(self):
        s=source(origin_countries=['DK'],allow_empty=True);install(s);self.assertEqual(1,len(s.read_records()))
        s=source(origin_countries=['NO'],allow_empty=True);install(s);self.assertEqual([],s.read_records())
        bad=notice(102,'2026.1002',originCountries=[{'isoCode':'SE','organizationName':'Sweden'}],riskDecision=None)
        s=source(origin_countries=['DK']);install(s,payload([notice(),bad]))
        with self.assertRaises(SourceError):s.read_records()

    def test_origin_reordering_and_unused_published_flag_are_quiet(self):
        origins=[{'isoCode':'DK','organizationName':'Denmark'},{'isoCode':'SE','organizationName':'Sweden'}]
        s=source();install(s,payload([notice(originCountries=origins)]));old,_=poll(s)
        install(s,payload([notice(originCountries=list(reversed(origins)),published=True)]));new,alerts=poll(s,old)
        self.assertEqual(old,new);self.assertEqual([],alerts)

    def test_full_pagination_and_short_final_page(self):
        first=[notice(100+i,f'2026.{1000+i}') for i in range(100)];last=[notice(200,'2026.1100',stamp='13-09-2026 10:00:00')]
        s=source();install(s,payload(first,total=101),payload(last,total=101));state,_=poll(s)
        self.assertEqual(101,len(state['source_state']['records']['rows']));self.assertEqual(2,s.post.call_args_list[1].kwargs['json']['parameters']['pageNumber'])
        for second in [payload([],total=101),payload(last,total=102),payload([first[0]],total=101)]:
            s=source();install(s,payload(first,total=101),second)
            with self.assertRaises(SourceError):s.read_records()

    def test_mid_read_change_preserves_persisted_history(self):
        s=source();install(s);old,_=poll(s);saved=deepcopy(old)
        s.post=Mock(side_effect=[response(payload()),response(payload([notice(subject='Changed during read')]))])
        with tempfile.TemporaryDirectory() as directory:
            store=StateStore(directory);store.save('rasff',old);outcome=run(Config((s.config,)),store,None,source_factory=lambda _:s)
            self.assertIn('rasff',outcome.errors);self.assertEqual(saved,store.load('rasff'))

    def test_filters_and_order_are_checked_against_records(self):
        bads=[payload([notice(notificationClassification={'id':306,'description':'alert notification'})]),
              payload([notice(stamp='16-08-2026 23:59:59')]),payload([notice(stamp='16-09-2026 00:00:00')]),
              payload([notice(stamp='13-09-2026 10:00:00'),notice(102,'2026.1002')])]
        for body in bads:
            s=source();install(s,body)
            with self.subTest(body=body),self.assertRaises(SourceError):s.read_records()

    def test_invalid_counts_dates_identity_and_country_data(self):
        bads=[payload(total=True),payload(total=2),payload(pages=2),payload([notice(),notice()]),
              payload([notice(),notice(102,'2026.1001')]),payload([notice(notifId=True)]),payload([notice(stamp='31-09-2026 10:00:00')]),
              payload([notice(reference='unknown')]),payload([notice(originCountries=None)]),payload([notice(notifyingCountry={})]),
              payload([notice(originCountries=[{'isoCode':'DK','organizationName':'Denmark'}]*2)]),payload([notice(subject='')])]
        for body in bads:
            s=source();install(s,body)
            with self.subTest(body=body),self.assertRaises(SourceError):s.read_records()

    def test_json_transport_and_limits_fail_closed(self):
        for status,body,opts in [(302,b'{}',{}),(200,b'{"notifications":[],"notifications":[],"totalElements":0,"totalPages":0}',{}),
                                 (200,b'{"notifications":[],"totalElements":NaN,"totalPages":0}',{}),(200,b'\xff',{}),(200,b'x'*2048,{'max_bytes':1024})]:
            s=source(**opts);r=response(body,status=status);s.post=Mock(return_value=r)
            with self.assertRaises(SourceError):s.read_records()
            r.close.assert_called_once()
        for opts in [{'max_records':1},{'max_pages':1}]:
            s=source(**opts);install(s,payload([notice()]*100,total=101))
            with self.assertRaises(SourceError):s.read_records()

    def test_config_rejects_removals_and_ambiguous_countries(self):
        for opts in [{'window_days':True},{'window_days':0},{'window_days':91},{'max_pages':0},{'complete_snapshot':True},{'events':['removed']},{'origin_countries':[]},{'origin_countries':['Norway']},{'origin_countries':['no']}]:
            with self.subTest(opts=opts),self.assertRaises(ValueError):source(**opts)


if __name__=='__main__':unittest.main()
