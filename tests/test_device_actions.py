from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import Mock

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.device_actions import API, DeviceActionsSource
from test_change_sources import poll


def row(ident='Z-1000-2026', internal='12345'):
    today=datetime.now(timezone.utc).date().isoformat()
    return {'product_res_number':ident,'cfres_id':internal,'res_event_number':'50000','product_code':'ABC',
            'event_date_posted':today,'event_date_initiated':today,'recalling_firm':'Example manufacturer',
            'reason_for_recall':'Faulty component','root_cause_description':'Component design',
            'product_description':'Synthetic device','action':'Return affected units','code_info':'Lot A',
            'distribution_pattern':'Example region','product_quantity':'10 units'}


def page(rows, skip=0, limit=100, total=None, updated=None):
    return {'meta':{'results':{'skip':skip,'limit':limit,'total':len(rows) if total is None else total},
                    'last_updated':updated or datetime.now(timezone.utc).date().isoformat()},'results':rows}


def response(value, status=200):
    raw=value if isinstance(value,bytes) else json.dumps(value).encode()
    return Mock(status_code=status,iter_content=Mock(return_value=[raw]),close=Mock())


def source(**options):
    return DeviceActionsSource(SourceConfig(id='devices',kind='device_actions',label='Device actions',
        urls=(API,),filters=FilterRule(match_all=True),options=options))


class DeviceActionsTests(unittest.TestCase):
    def test_quiet_baseline_noise_and_action_scope_changes(self):
        s=source();r=row();s.get=Mock(return_value=response(page([r])));state,alerts=poll(s);self.assertEqual([],alerts)
        r.update(additional_info_contact='Ignored person',recall_status='Terminated',event_date_terminated='2026-01-01')
        s.get.return_value=response(page([r]));same,alerts=poll(s,state);self.assertEqual(state,same);self.assertEqual([],alerts)
        for field,value in [('action','Inspect and replace affected units'),('code_info','Lots A and B'),('reason_for_recall','Expanded defect assessment')]:
            r[field]=value;s.get.return_value=response(page([r]));state,alerts=poll(s,state);self.assertEqual(1,len(alerts))
            self.assertNotIn('sha256',' '.join(alerts[0].item.alert_details))
            s.get.return_value=response(page([r]));same,alerts=poll(s,state);self.assertEqual(state,same);self.assertEqual([],alerts)

    def test_multiple_products_per_event_and_pagination_anchor(self):
        a,b=row(),row('Z-1001-2026','12346');s=source(page_size=1)
        first,second=page([a],0,1,2),page([b],1,1,2)
        s.get=Mock(side_effect=[response(first),response(second),response(first)])
        records=s.read_records();self.assertEqual(2,len(records));self.assertEqual(3,s.get.call_count)
        self.assertEqual(records[0]['fields']['event_number'],records[1]['fields']['event_number'])
        self.assertIn('skip=1',s.get.call_args_list[1].args[0])
        changed=deepcopy(first);changed['results'][0]['action']='Different action'
        s.get=Mock(side_effect=[response(first),response(second),response(changed)])
        with self.assertRaisesRegex(SourceError,'first page changed'):s.read_records()

    def test_partial_duplicate_wrong_order_and_publication_change_fail(self):
        a,b=row(),row('Z-1001-2026','12346')
        sequences=[ [page([a],0,1,2),page([a],1,1,2)],
                    [page([b],0,1,2),page([a],1,1,2)],
                    [page([a],0,1,2),page([],1,1,2)],
                    [page([a],0,1,2),page([b],1,1,3)],
                    [page([a],0,1,2),page([b],0,1,2)],
                    [page([a],0,1,2),page([b],1,1,2,updated='2000-01-01')]]
        for replies in sequences:
            s=source(page_size=1);s.get=Mock(side_effect=[response(v) for v in replies])
            with self.assertRaises(SourceError):s.read_records()
        b['cfres_id']=a['cfres_id'];s=source();s.get=Mock(return_value=response(page([a,b])))
        with self.assertRaisesRegex(SourceError,'internal identifiers'):s.read_records()

    def test_empty_response_is_recognized_but_later_missing_page_fails(self):
        missing={'error':{'code':'NOT_FOUND','message':'No matches found!'}}
        s=source(allow_empty=True);s.get=Mock(return_value=response(missing,404));state,alerts=poll(s)
        self.assertEqual([],alerts);self.assertEqual({},state['source_state']['records']['rows'])
        s=source(page_size=1);s.get=Mock(side_effect=[response(page([row()],0,1,2)),response(missing,404)])
        with self.assertRaisesRegex(SourceError,'later page'):s.read_records()
        s.get=Mock(return_value=response({'error':{'code':'NOT_FOUND','message':'Wrong route'}},404))
        with self.assertRaisesRegex(SourceError,'unrecognized'):s.read_records()

    def test_no_disappearance_claim_and_bounded_large_code_change(self):
        s=source(allow_empty=True);r=row();r['code_info']='Lot A '*20000;s.get=Mock(return_value=response(page([r])))
        state,_=poll(s);self.assertLess(len(json.dumps(state)),10000)
        r['code_info']+='Lot B';s.get.return_value=response(page([r]));state,alerts=poll(s,state)
        self.assertEqual(1,len(alerts));self.assertLess(len(' '.join(alerts[0].item.alert_details)),2000)
        s.get.return_value=response({'error':{'code':'NOT_FOUND','message':'No matches found!'}},404)
        _,alerts=poll(s,state);self.assertEqual([],alerts)

    def test_classification_days_counts_today_inclusively(self):
        from urllib.parse import parse_qs, urlsplit
        s=source(classification_days=1);s.get=Mock(return_value=response(page([row()])))
        s.read_records();query=parse_qs(urlsplit(s.get.call_args.args[0]).query)
        today=datetime.now(timezone.utc).date().isoformat()
        self.assertEqual([f'event_date_posted:[{today} TO {today}]'],query['search'])

    def test_nullable_fields_and_malformed_records(self):
        r=row();r.pop('product_quantity');r.pop('event_date_initiated');s=source();s.get=Mock(return_value=response(page([r])))
        self.assertIsNone(s.read_records()[0]['fields']['product_quantity'])
        for field,value in [('product_res_number','bad'),('cfres_id',True),('res_event_number',''),('product_code','bad'),
                            ('event_date_posted','2000-01-01'),('event_date_initiated','not-a-date'),
                            ('action',None),('product_description',{}),('code_info','x'*500001)]:
            r=row();r[field]=value;s.get=Mock(return_value=response(page([r])))
            with self.subTest(field=field),self.assertRaises(SourceError):s.read_records()
        r=row();r['event_date_initiated']=(datetime.now(timezone.utc).date()+timedelta(days=1)).isoformat()
        s.get=Mock(return_value=response(page([r])))
        with self.assertRaises(SourceError):s.read_records()

    def test_limits_counts_json_and_redirects_preserve_snapshot(self):
        s=source(max_bytes=1024);reply=response(b'x'*1025);s.get=Mock(return_value=reply)
        with self.assertRaisesRegex(SourceError,'max_bytes'):s.read_records()
        reply.close.assert_called_once()
        for options,data in [({'max_records':1},page([row()],total=2)),({'page_size':1,'max_pages':1},page([row()],limit=1,total=2)),
                             ({},page([],total=0)),({},page([row()],updated='2000-01-01')),({},page([row()],skip=True)),({},page([row()],limit=1)),
                             ({},{'meta':{}}),({},b'{"meta":{},"meta":{}}')]:
            s=source(**options);s.get=Mock(return_value=response(data))
            with self.assertRaises(SourceError):s.read_records()
        s=source();reply=response({},302);s.get=Mock(return_value=reply)
        with self.assertRaisesRegex(SourceError,'redirect'):s.read_records()
        reply.close.assert_called_once()
        s=source();s.get=Mock(return_value=response(page([row()])));state,_=poll(s);prior=deepcopy(state);next_prior=deepcopy(s._next)
        s.get.return_value=response(b'{')
        with self.assertRaises(SourceError):poll(s,state)
        self.assertEqual(prior,state);self.assertEqual(next_prior,s._next)
        for options in [{'complete_snapshot':True},{'classification_days':0},{'max_pages':11},{'page_size':1001}]:
            with self.assertRaises(ValueError):source(**options)


if __name__=='__main__':unittest.main()
