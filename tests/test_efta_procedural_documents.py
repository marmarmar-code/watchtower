from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse
import tempfile
import unittest
from unittest.mock import Mock

from watchtower.config import Config, FilterRule, SourceConfig
from watchtower.engine import run
from watchtower.sources.common import SourceError
from watchtower.sources.efta_procedural_documents import EftaProceduralDocumentsSource, PATH, ORIGIN, ATTACHMENT_PATH, attachment_url
from watchtower.state import StateStore
from test_change_sources import poll, response


def row(number='101', **changes):
    value={'title':'Formal notice on example rules','number':number,'type':'Letter of Formal Notice',
           'caseNumber':'501','caseName':'Example rules','date':int(datetime(2026,5,1,12,tzinfo=timezone.utc).timestamp()),
           'country':{'code':'NO','name':'Norway'},'attachment':{'url':ATTACHMENT_PATH+number+'.pdf','description':'Example document'},
           'state':'NOR','collegeDecision':'001/26/COL'}
    value.update(changes)
    return value


def source(**options):
    return EftaProceduralDocumentsSource(SourceConfig(id='procedural-documents',kind='efta_procedural_documents',
        label='Procedural documents',urls=(),filters=FilterRule(match_all=True),options={'allow_empty':True,**options}))


def page(rows, number=1, per=2, years=(2026,2025)):
    return {'type':'listing_page','listingId':'documents','alias':PATH,'title':'Public document database',
        'listing':{'fields':{'years':list(years),'countries':['NO|Norway','IS|Iceland','LI|Liechtenstein']},
                   'data':{'page':number,'nodesPerPage':per,'nodesCount':len(rows),'nodes':deepcopy(rows[(number-1)*per:number*per])}}}


def sweep(rows, per=2, years=(2026,2025)):
    return [page(rows,per=per,years=years)]+[page(rows,n,per,years) for n in range(1,max(1,(len(rows)+per-1)//per)+1)]


def install(src, rows, second=None, **kw):
    payloads=sweep(rows,**kw)+sweep(rows if second is None else second,**kw)
    src.get=Mock(side_effect=[response(p) for p in payloads])


class EftaProceduralDocumentTests(unittest.TestCase):
    def test_paginated_baseline_repeat_and_exact_type_selection(self):
        src=source(); rows=[row(),row('102',type='General'),row('103',type='Reasoned Opinion'),row('104',type='Referral to EFTA Court')]
        install(src,rows); previous,alerts=poll(src)
        self.assertEqual([],alerts); self.assertEqual(3,len(previous['source_state']['records']['rows']))
        self.assertEqual(6,src.get.call_count)
        queries=[parse_qs(parse_qs(urlparse(c.args[0]).query)['search'][0]) for c in src.get.call_args_list]
        self.assertEqual(['1','1','2','1','1','2'],[q['page'][0] for q in queries])
        self.assertTrue(all(q['country']==['NO'] for q in queries))
        install(src,list(reversed(rows))); repeat,alerts=poll(src,previous)
        self.assertEqual([],alerts);self.assertEqual(previous,repeat)

    def test_document_and_case_changes_emit_once_and_preserve_explicit_semantics(self):
        src=source();install(src,[row()]);previous,_=poll(src)
        changed=row(title='Updated document',caseNumber='502',caseName='Changed case',collegeDecision='002/26/COL',type='Reasoned Opinion')
        install(src,[changed]);state,alerts=poll(src,previous)
        self.assertEqual(1,len(alerts));self.assertEqual('changed',alerts[0].item.metadata['event'])
        details=' '.join(alerts[0].item.alert_details)
        for value in ('502','Changed case','Reasoned Opinion','002/26/COL','ikke nåværende saksstatus'):
            self.assertIn(value,details)
        install(src,[changed]);repeat,alerts=poll(src,state)
        self.assertEqual([],alerts);self.assertEqual(state,repeat)

    def test_same_document_number_different_attachment_is_distinct(self):
        src=source();other=row(attachment={'url':ATTACHMENT_PATH+'other.pdf','description':'Other attachment'})
        install(src,[row(),other]);state,alerts=poll(src)
        self.assertEqual(2,len(state['source_state']['records']['rows']));self.assertEqual([],alerts)
        newer=row(attachment={'url':ATTACHMENT_PATH+'new.pdf','description':'New attachment'})
        install(src,[row(),other,newer]);_,alerts=poll(src,state)
        self.assertEqual(['added'],[a.item.metadata['event'] for a in alerts])
        self.assertIn('ikke nødvendigvis en ny sak',' '.join(alerts[0].item.alert_details))

    def test_geographic_state_and_order_are_not_case_status_changes(self):
        src=source();install(src,[row()]);state,_=poll(src)
        changed=row();changed.pop('state')
        install(src,[changed]);repeat,alerts=poll(src,state)
        self.assertEqual([],alerts);self.assertEqual(state,repeat)

    def test_absence_and_empty_selection_do_not_claim_closure(self):
        src=source();install(src,[row()]);state,_=poll(src)
        install(src,[]);after,alerts=poll(src,state)
        self.assertEqual([],alerts);self.assertEqual(state['seen'],after['seen'])
        install(src,[row()]);_,alerts=poll(src,after);self.assertEqual([],alerts)
        empty=source();install(empty,[row(type='General')]);empty_state,alerts=poll(empty)
        self.assertEqual([],alerts);self.assertEqual({},empty_state['source_state']['records']['rows'])

    def test_year_advance_is_new_document_and_regression_preserves_state(self):
        src=source();old=row(date=int(datetime(2025,6,1,12,tzinfo=timezone.utc).timestamp()))
        install(src,[old],years=(2025,2024));previous,_=poll(src)
        install(src,[row('102')]);current,alerts=poll(src,previous)
        self.assertEqual(['added'],[a.item.metadata['event'] for a in alerts]);self.assertEqual(2026,current['source_state']['records']['latest_year'])
        saved=deepcopy(current);install(src,[old],years=(2025,2024))
        with self.assertRaisesRegex(SourceError,'regressed'):poll(src,current)
        self.assertEqual(saved,current)

    def test_latest_two_years_are_fully_read(self):
        src=source(latest_years=2);old=row('102',date=int(datetime(2025,6,1,12,tzinfo=timezone.utc).timestamp()))
        pages=[page([row()]),page([row()]),page([old])]
        src.get=Mock(side_effect=[response(p) for p in pages+pages])
        self.assertEqual(2,len(src.read_records()))
        queries=[parse_qs(parse_qs(urlparse(c.args[0]).query)['search'][0]) for c in src.get.call_args_list]
        self.assertEqual([None,['2026'],['2025']]*2,[q.get('year') for q in queries])

    def test_duplicate_across_pages_and_changed_sweep_are_rejected(self):
        src=source();install(src,[row(),row('102'),row()])
        with self.assertRaisesRegex(SourceError,'identity repeats'):src.read_records()
        install(src,[row()],second=[row(caseName='Concurrent change')])
        with self.assertRaisesRegex(SourceError,'between complete reads'):src.read_records()

    def test_failure_preserves_persisted_state(self):
        src=source();install(src,[row()]);previous,_=poll(src)
        install(src,[row()],second=[row(caseName='Concurrent change')])
        with tempfile.TemporaryDirectory() as directory:
            store=StateStore(directory);store.save(src.config.id,previous)
            result=run(Config((src.config,)),store,None,source_factory=lambda _:src)
            self.assertIn(src.config.id,result.errors);self.assertEqual(previous,store.load(src.config.id))

    def test_wrong_pagination_metadata_or_partial_page_is_rejected(self):
        for field,value in [('page',True),('page',2),('nodesCount',3),('nodesPerPage',0),('nodes',[row(),row('102')])]:
            with self.subTest(field=field,value=value):
                payload=page([row()]);payload['listing']['data'][field]=value
                src=source();src.get=Mock(return_value=response(payload))
                with self.assertRaises(SourceError):src.read_records()
        payloads=sweep([row(),row('102'),row('103')]);payloads[-1]['listing']['data']['nodesCount']=4
        src=source();src.get=Mock(side_effect=[response(p) for p in payloads])
        with self.assertRaises(SourceError):src.read_records()

    def test_wrong_identity_selection_metadata_and_bounds_rejected(self):
        for change in ({'title':'Other database'},{'alias':'/other'},{'listingId':'other'}):
            src=source();src.get=Mock(return_value=response({**page([row()]),**change}))
            with self.assertRaises(SourceError):src.read_records()
        for years,countries in [([2026,2026],['NO|Norway']),([True],['NO|Norway']),([2026],['IS|Iceland']),([2026],['NO|Norway','NO|Duplicate'])]:
            src=source();p=page([row()]);p['listing']['fields']={'years':years,'countries':countries};src.get=Mock(return_value=response(p))
            with self.assertRaises(SourceError):src.read_records()
        for opts in ({'max_pages':1},{'max_register_records':2},{'max_records':2}):
            src=source(**opts);install(src,[row(),row('102'),row('103')])
            with self.assertRaises(SourceError):src.read_records()

    def test_invalid_rows_including_unselected_types_fail_closed(self):
        bads=[row(number='x'),row(caseNumber=''),row(date=True),row(date=0),row(country={'code':'IS','name':'Iceland'}),
              row(title=None),row(state=None),row(collegeDecision=None),row(type='General',caseName=''),row(extra='field'),
              row(date=int(datetime(2025,5,1,tzinfo=timezone.utc).timestamp())),row(attachment={'url':'https://example.test/file','description':'File'})]
        missing=row();missing.pop('caseNumber');bads.append(missing)
        for bad in bads:
            with self.subTest(bad=bad):
                src=source();install(src,[bad])
                with self.assertRaises(SourceError):src.read_records()

    def test_attachment_canonicalization_and_invalid_paths(self):
        self.assertEqual(ORIGIN+ATTACHMENT_PATH+'example%20file.pdf',attachment_url(ATTACHMENT_PATH+'example file.pdf'))
        for url in ['https://example.test/file','http://www.eftasurv.int'+ATTACHMENT_PATH+'x',ATTACHMENT_PATH+'%2e%2e/x',
                    ATTACHMENT_PATH+'%FF',ATTACHMENT_PATH+'%',ATTACHMENT_PATH+'x?token=1',ATTACHMENT_PATH+'x#part',ATTACHMENT_PATH+'%00.pdf']:
            with self.subTest(url=url),self.assertRaises(SourceError):attachment_url(url)

    def test_transport_limits_redirect_and_malformed_json_close_response(self):
        for body,status in [(b'x'*2048,200),(b'{}',302),(b'invalid',200)]:
            src=source(max_bytes=1024);r=response(body,status=status);src.get=Mock(return_value=r)
            with self.assertRaises(SourceError):src.read_records()
            r.close.assert_called_once();self.assertFalse(src.get.call_args.kwargs['allow_redirects'])

    def test_invalid_configuration_is_rejected(self):
        for opts in ({'country':'XX'},{'latest_years':0},{'latest_years':True},{'document_types':[]},{'document_types':[' General ']},
                     {'max_pages':0},{'events':['removed']},{'complete_snapshot':True}):
            with self.subTest(opts=opts),self.assertRaises(ValueError):source(**opts)


if __name__=='__main__':unittest.main()
