from copy import deepcopy
from datetime import date
import json
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlparse,parse_qs
from watchtower.config import Config,SourceConfig,FilterRule
from watchtower.engine import run
from watchtower.state import StateStore
from watchtower.sources.common import SourceError
from watchtower.sources.pdmr_transactions import PdmrTransactionsSource,API,CONFIG_URL,decode
from test_change_sources import poll,response


def envelope(data):return {'header':{'result.val':0,'http.code':200,'result.text':'OK'},'data':data}
def message(mid=1,**kw):
    return {'id':mid,'messageId':mid,'issuerId':10,'title':'Example transaction','issuerName':'Example ASA','publishedTime':'2026-09-15T06:00:00.000Z','test':False,'category':[{'id':1102}],'numbAttachments':1,'correctionForMessageId':0,'correctedByMessageId':0,**kw}
def source(**kw):
    return PdmrTransactionsSource(SourceConfig(id='pdmr',kind='pdmr_transactions',label='PDMR',urls=(),filters=FilterRule(match_all=True),options={'issuer_ids':[10],'allow_empty':True,**kw}))
def install(s,messages=None,*,overflow=False,att=None):
    messages=[message()] if messages is None else messages
    attachments=[{'id':20,'name':'form.pdf'}] if att is None else att
    def get(url,**kwargs):
        if url==CONFIG_URL:return response({'api_large':API})
        route=urlparse(url).path.rsplit('/',1)[-1]
        if route=='list':return response(envelope({'overflow':overflow,'messages':messages}))
        if route=='message':
            mid=int(parse_qs(urlparse(url).query)['messageId'][0]);m=next(x for x in messages if x['messageId']==mid)
            return response(envelope({'message':{**m,'attachments':attachments if m['numbAttachments'] else []}}))
        if route=='attachment':return response(b'%PDF-1.4\n%%EOF')
        raise AssertionError(url)
    s.get=Mock(side_effect=get);s.post=Mock(return_value=response(envelope({'categories':[{'id':1102,'category_en':'MANAGERS’ TRANSACTION'}]})))
    return get

def parsed(kind='Lending',status='parsed'):
    return {'status':status,'format':'MAR' if status=='parsed' else None,'transactions':[{'section':1,'transaction_type_text':kind,'price_volume_text':'NOK 0; 50'}] if status=='parsed' else []}


class PdmrTests(unittest.TestCase):
    def setUp(self):
        p=patch('watchtower.sources.pdmr_transactions.today',return_value=date(2026,9,15));p.start();self.addCleanup(p.stop)
        self.decoder=patch('watchtower.sources.pdmr_transactions.parse_attachment',return_value=parsed()).start();self.addCleanup(patch.stopall)
    def test_baseline_repeat_and_single_transaction_change(self):
        s=source();install(s);old,alerts=poll(s);self.assertFalse(alerts);self.assertEqual(old,poll(s,old)[0])
        self.decoder.return_value=parsed('Redelivery');new,alerts=poll(s,old);self.assertEqual(1,len(alerts));self.assertEqual('changed',alerts[0].item.metadata['event']);self.assertFalse(poll(s,new)[1])
        with tempfile.TemporaryDirectory() as d:
            store=StateStore(d);store.save('pdmr',new);self.assertEqual(new,store.load('pdmr'))
    def test_correction_links_keep_originals_and_forms_independent(self):
        s=source();messages=[message(1,numbAttachments=0,correctedByMessageId=2),message(2,correctionForMessageId=1)];install(s,messages)
        state,alerts=poll(s);self.assertEqual(2,len(s._next['rows']));self.assertFalse(alerts)
        fields=s._next['rows']['1']['row']['fields'];self.assertEqual(2,fields['corrected_by_message_id']);self.assertIsNone(fields['correction_for_message_id']);self.assertEqual('limited',fields['coverage']);self.assertEqual(1,len(s.coverage_warnings))
    def test_scans_have_warning_and_never_claim_complete_forms(self):
        s=source();install(s);self.decoder.return_value=parsed(status='unreadable_scan');poll(s)
        self.assertEqual('limited',s._next['rows']['1']['row']['fields']['coverage']);self.assertEqual(1,len(s.coverage_warnings))
    def test_parsed_to_scan_preserves_persistent_state(self):
        s=source();install(s);old,_=poll(s);saved=deepcopy(old);self.decoder.return_value=parsed(status='unreadable_scan')
        with tempfile.TemporaryDirectory() as d:
            store=StateStore(d);store.save('pdmr',old);result=run(Config((s.config,)),store,None,source_factory=lambda _:s)
            self.assertIn('pdmr',result.errors);self.assertEqual(saved,store.load('pdmr'))
    def test_removed_parsed_attachment_preserves_state(self):
        s=source();install(s);old,_=poll(s);install(s,[message(numbAttachments=0)])
        with self.assertRaises(SourceError):poll(s,old)
    def test_overflow_duplicate_and_bounds_fail(self):
        for messages,overflow in [([message()],True),([message(),message()],False)]:
            s=source();install(s,messages,overflow=overflow)
            with self.assertRaises(SourceError):poll(s)
        s=source(max_messages=1);install(s,[message(),message(2)])
        with self.assertRaises(SourceError):poll(s)
        s=source(max_attachments=1);install(s,[message(numbAttachments=2)])
        with self.assertRaises(SourceError):poll(s)
    def test_message_contract_rejects_bad_identity_category_and_timestamp(self):
        for kw in [{'id':True},{'test':True},{'publishedTime':'2026-09-15T06:00:00'},{'category':[{'id':1103}]},{'numbAttachments':True},{'correctionForMessageId':1}]:
            s=source();install(s,[message(**kw)])
            with self.subTest(kw=kw),self.assertRaises(SourceError):poll(s)
    def test_manifest_change_between_reads_fails(self):
        s=source();get=install(s);calls=0
        def changed(url,**kw):
            nonlocal calls
            if '/message?' in url:
                calls+=1
                if calls==2:return response(envelope({'message':{**message(),'attachments':[{'id':21,'name':'form.pdf'}]}}))
            return get(url,**kw)
        s.get.side_effect=changed
        with self.assertRaises(SourceError):poll(s)
    def test_index_change_between_reads_fails(self):
        s=source();get=install(s);calls=0
        def changed(url,**kw):
            nonlocal calls
            if '/list?' in url:
                calls+=1
                if calls==2:return response(envelope({'overflow':False,'messages':[]}))
            return get(url,**kw)
        s.get.side_effect=changed
        with self.assertRaises(SourceError):poll(s)
    def test_download_and_json_guards(self):
        for body in [b'{"data":1,"data":2}',b'{"data":NaN}',b'<html>failed</html>']:
            with self.assertRaises(SourceError):decode(body)
        for status,body in [(302,b''),(200,b'x'*1025)]:
            s=source(max_bytes=1024);r=response(body,status);s.get=Mock(return_value=r)
            with self.assertRaises(SourceError):s._download(CONFIG_URL)
            r.close.assert_called_once()
    def test_empty_window_and_nonselected_issuer_do_not_cancel(self):
        s=source();install(s);old,_=poll(s);install(s,[]);new,alerts=poll(s,old);self.assertFalse(alerts);self.assertFalse(s._next['rows']);self.assertEqual(old['seen'],new['seen'])
        install(s,[message(issuerId=99)]);poll(s);self.assertFalse(s._next['rows'])
    def test_config_rejects_implicit_or_invalid_selections(self):
        for kw in [{'issuer_ids':[]},{'issuer_ids':[True]},{'complete_snapshot':True},{'events':['removed']},{'lookback_days':32},{'max_messages':0}]:
            with self.subTest(kw=kw),self.assertRaises(ValueError):source(**kw)
    def test_engine_persists_coverage_warning(self):
        s=source();install(s);self.decoder.return_value=parsed(status='unreadable_scan')
        with tempfile.TemporaryDirectory() as d:
            store=StateStore(d);result=run(Config((s.config,)),store,None,source_factory=lambda _:s)
            self.assertFalse(result.errors);self.assertIn('pdmr',result.warnings);self.assertEqual(s.coverage_warnings,store.load('pdmr')['coverage_warnings'])
