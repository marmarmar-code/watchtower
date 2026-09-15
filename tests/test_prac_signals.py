from copy import deepcopy
from datetime import datetime, timezone
import unittest
import tempfile
from watchtower.config import Config
from watchtower.engine import run
from watchtower.state import StateStore
from unittest.mock import Mock, patch

from watchtower.config import SourceConfig, FilterRule
from watchtower.sources.common import SourceError
from watchtower.sources.prac_signals import PracSignalsSource, PAGE
from test_change_sources import response, poll


def index(*titles, published='15/09/2026'):
    cards=[]
    for i,title in enumerate(titles or ('PRAC recommendations on signals adopted at the 6-9 July 2026 PRAC meeting',)):
        ref='EMA/PRAC/155652/2026' if 'July' in title else 'EMA/PRAC/124080/2026'
        pdf='prac-recommendations-signals-adopted-6-9-july-2026-prac-meeting_en.pdf'
        cards.append(f'''<article data-ema-document-type="prac-recommendation"><span class="file-title">{title}</span><a href="/en/documents/prac-recommendation/{pdf}">English</a><div class="file-metadata-row"><span class="value">{ref}</span></div><div class="first-publishedfw-normal"><time datetime="2026-09-15">{published}</time></div></article>''')
    return ('<html><h1>PRAC recommendations on safety signals</h1>'+''.join(cards)+'</html>').encode()


def source(**options):
    return PracSignalsSource(SourceConfig(id='prac',kind='prac_signals',label='PRAC signals',urls=(),filters=FilterRule(match_all=True),options=options))


def install(s, body=None, second=None):
    body=index() if body is None else body
    pdf=b'%PDF-1.7\n%%EOF'
    s.get=Mock(side_effect=[response(body), response(pdf), response(body if second is None else second), response(pdf)])


def row(epitt='12345', category=1):
    return {'category':category,'epitt':epitt,'substance_text':'Example medicine','signal_text':'Example signal','rapporteur_text':'Example rapporteur','action_text':'Example action','mah_text':None,'authorisation_procedure':'Centralised' if category==1 else None,'adoption_date':'2026-07-09' if category==1 else None,'page':3}


class PracSignalsTests(unittest.TestCase):
    def setUp(self):
        p=patch('watchtower.sources.prac_signals.today',return_value=datetime(2026,9,15,tzinfo=timezone.utc).date());p.start();self.addCleanup(p.stop)

    def test_index_metadata_and_rolling_selection(self):
        s=source(max_documents=1)
        docs=s._index(index('PRAC recommendations on signals adopted at the 8-11 June 2026 PRAC meeting','PRAC recommendations on signals adopted at the 6-9 July 2026 PRAC meeting'))
        self.assertEqual(1,len(docs));self.assertEqual('2026-07-09',docs[0]['meeting_end']);self.assertEqual('2026-09-15',docs[0]['first_published_date'])

    def test_index_rejects_external_duplicate_or_stale(self):
        cases=[index().replace(b'href="/en/documents/',b'href="https://evil.example/en/documents/'), index('PRAC recommendations on signals adopted at the 6-9 July 2026 PRAC meeting','PRAC recommendations on signals adopted at the 6-9 July 2026 PRAC meeting'), index().replace(b'15/09/2026',b'01/01/2026')]
        for body in cases:
            s=source(max_documents=2)
            with self.subTest():
                with self.assertRaises(SourceError):s._index(body)

    def test_index_rejects_heading_and_metadata_drift(self):
        for body in [index().replace(b'<h1>PRAC recommendations on safety signals</h1>',b'<h1>Changed</h1>'), index().replace(b'EMA/PRAC/155652/2026',b'BAD')]:
            with self.assertRaises(SourceError): source()._index(body)

    def test_two_reads_and_quiet_poll(self):
        s=source(); install(s)
        with patch('watchtower.sources.prac_signals.parse_pdf',return_value=[row()]):
            old,alerts=poll(s)
        self.assertEqual([],alerts)
        s.get=Mock(side_effect=[response(index()),response(b'%PDF-1.7\n%%EOF'),response(index()),response(b'%PDF-1.7\n%%EOF')])
        with patch('watchtower.sources.prac_signals.parse_pdf',return_value=[row()]):
            new,alerts=poll(s,old)
        self.assertEqual(old,new);self.assertEqual([],alerts)

    def test_category_epitt_identity_and_single_change(self):
        s=source();pdf=b'%PDF-1.7\n%%EOF'; install(s)
        with patch('watchtower.sources.prac_signals.parse_pdf',return_value=[row('12345',1)]): old,_=poll(s)
        changed=row('12345',1);changed['action_text']='Changed action'
        s.get=Mock(side_effect=[response(index()),response(pdf),response(index()),response(pdf)])
        with patch('watchtower.sources.prac_signals.parse_pdf',return_value=[changed]): new,alerts=poll(s,old)
        self.assertEqual(1,len(alerts));self.assertEqual('changed',alerts[0].item.metadata['event'])
        s.get=Mock(side_effect=[response(index()),response(pdf),response(index()),response(pdf)])
        repeat=row('12345',1);repeat['action_text']='Changed action'
        with patch('watchtower.sources.prac_signals.parse_pdf',return_value=[repeat]): self.assertEqual([],poll(s,new)[1])

    def test_pdf_transport_redirect_and_bounds(self):
        for status in (301,302):
            s=source();r=response(index(),status=status);s.get=Mock(return_value=r)
            with self.assertRaises(SourceError):s.read_records()
            self.assertGreaterEqual(r.close.call_count,1)
        s=source();r=response(index(),status=200);s.get=Mock(return_value=r)
        with patch('watchtower.sources.prac_signals.parse_pdf',side_effect=SourceError('bad pdf')):
            with self.assertRaises(SourceError):s.read_records()
        self.assertGreaterEqual(r.close.call_count,1)

    def test_state_preserved_on_index_or_pdf_drift(self):
        s=source();install(s)
        with patch('watchtower.sources.prac_signals.parse_pdf',return_value=[row()]): old,_=poll(s)
        saved=deepcopy(old)
        drift=index().replace(b'15/09/2026',b'16/09/2026')
        s.get=Mock(side_effect=[response(drift),response(b'%PDF-1.7\n%%EOF'),response(drift),response(b'%PDF-1.7\n%%EOF')])
        with patch('watchtower.sources.prac_signals.parse_pdf',return_value=[row()]):
            with self.assertRaises(SourceError):poll(s,old)
        self.assertEqual(saved,old)

    def test_valid_stale_date_is_rejected_by_age(self):
        body=index(published='03/08/2026').replace(b'datetime="2026-09-15"',b'datetime="2026-08-03"')
        self.assertEqual('2026-08-03',source(max_index_age_days=120)._index(body)[0]['first_published_date'])
        with self.assertRaises(SourceError):source(max_index_age_days=30)._index(body)

    def test_same_epitt_different_categories_are_distinct(self):
        s=source();install(s)
        with patch('watchtower.sources.prac_signals.parse_pdf',return_value=[row(category=1),row(category=2),row(category=3)]):old,alerts=poll(s)
        self.assertEqual(3,len(old['seen']));self.assertEqual([],alerts)
        self.assertEqual(3,len(s._next['rows']))

    def test_mid_read_index_and_pdf_changes_preserve_persistent_state(self):
        s=source();install(s)
        with patch('watchtower.sources.prac_signals.parse_pdf',return_value=[row()]):old,_=poll(s)
        saved=deepcopy(old);other=index(published='14/09/2026').replace(b'datetime="2026-09-15"',b'datetime="2026-09-14"');pdf=b'%PDF-1.7\n%%EOF'
        for responses in [[index(),pdf,other,pdf],[index(),pdf,index(),pdf+b'changed']]:
            s.get=Mock(side_effect=[response(x) for x in responses])
            with tempfile.TemporaryDirectory() as d:
                store=StateStore(d);store.save('prac',old)
                with patch('watchtower.sources.prac_signals.parse_pdf',return_value=[row()]):result=run(Config((s.config,)),store,None,source_factory=lambda _:s)
                self.assertIn('prac',result.errors);self.assertEqual(saved,store.load('prac'))

    def test_download_byte_limit_closes_response(self):
        s=source(max_bytes=1024);r=response(b'x'*1025);s.get=Mock(return_value=r)
        with self.assertRaises(SourceError):s._download(PAGE)
        r.close.assert_called_once()

    def test_configuration_rejects_complete_or_removals(self):
        for options in ({'complete_snapshot':True},{'events':['removed']},{'max_documents':0},{'max_pdf_pages':0}):
            with self.assertRaises(ValueError):source(**options)


if __name__=='__main__':unittest.main()
