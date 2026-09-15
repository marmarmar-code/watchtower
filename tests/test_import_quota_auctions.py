import unittest
from copy import deepcopy
from unittest.mock import Mock, patch
from watchtower.config import SourceConfig, FilterRule
from watchtower.sources.import_quota_auctions import ImportQuotaAuctionsSource
from watchtower.sources.common import SourceError
from test_change_sources import response, poll

def index(ids=(1322,), year=2026):
    links=''.join(f'<a href="/history?year={y}" class="{ "border-orange" if y==year else "" }">{y}</a>' for y in (2025,year))
    nodes=''.join(f'<div x-data="aitem_entry()"><div @click="retrieve_data({i})">KORN Test {i}, {year}</div></div>' for i in ids)
    return f'<html><head><title>Auksjon - Landbruksdirektoratet</title></head><body><div class="swiper-wrapper">{links}</div>{nodes}</body></html>'

def result(awards=None, minimum='10,00', utf=False):
    awards=awards if awards is not None else [('Generic Firm','50','10,00')]
    vals=['500','10','500',minimum,'1,00','28.01.2026 07:00:00','29.01.2026 16:00:00','29.01.2026 16:03:00']
    blocks=''.join(f'<div><div class="font-bold mb-1">{l}</div><div class="mb-5">{v}</div></div>' for l,v in zip(['Totalmengde','Minste mengde','Største mengde','Minstepris','Budøkning','Auksjonen startet','Starttid','Sluttid'],vals))
    if awards == 'NO': body=''
    else:
        body='<table><thead><tr><td>Firma</td><td>Tildelt mengde</td><td>Bud pr. enhet</td></tr></thead><tbody>'+''.join(f'<tr><td>{n}</td><td>{q}</td><td>{p}</td></tr>' for n,q,p in awards)+'</tbody><tfoot><tr><td>Totalt tildelt mengde:</td><td>{}</td><td></td></tr></tfoot></table>'.format(sum(int(q) for _,q,_ in awards))
    return ('<html><body><div class="grid">'+blocks+'</div><!-- SOF i/bid_list -->'+body+'<!-- EOF i/bid_list --></body></html>').encode('iso-8859-1' if utf else 'utf-8')

def source(**opts):
    urls=opts.pop('urls',())
    return ImportQuotaAuctionsSource(SourceConfig(id='quota',kind='import_quota_auctions',label='Quota',urls=urls,filters=FilterRule(match_all=True),options={'title_prefixes':['KORN '],**opts}))

class Tests(unittest.TestCase):
    def setUp(self):
        self.t=patch('watchtower.sources.import_quota_auctions.today',return_value=__import__('datetime').date(2026,9,15));self.t.start();self.sleep=patch('watchtower.sources.import_quota_auctions.time.sleep');self.sleep.start();self.addCleanup(self.t.stop);self.addCleanup(self.sleep.stop)
    def install(self,s, idx=None, res=None):
        idx=idx or index();res=res or result();s.get=Mock(side_effect=[response(idx.encode() if isinstance(idx,str) else idx),response(res),response(idx.encode() if isinstance(idx,str) else idx),response(res)])
    def test_quiet_repeat_and_default_unit(self):
        s=source();self.install(s);s.get.side_effect=list(s.get.side_effect)*2;st,al=poll(s);self.assertFalse(al);st,al=poll(s,st);self.assertFalse(al);self.assertEqual('tonne',st['source_state']['records']['rows']['1322']['row']['fields']['quantity_unit'])
    def test_updated_bid_alert_once(self):
        a=result();b=result(awards=[('Generic Firm','50','11,00')]);s=source();s.get=Mock(side_effect=[response(x) for x in [index().encode(),a,index().encode(),a,index().encode(),a,index().encode(),a,index().encode(),b,index().encode(),b,index().encode(),b,index().encode(),b]])
        st,al=poll(s);self.assertFalse(al);st,al=poll(s,st);self.assertFalse(al);st,al=poll(s,st);self.assertEqual(1,len(al));st,al=poll(s,st);self.assertFalse(al)
    def test_lower_bid_than_displayed_minimum_is_retained(self):
        s=source();self.install(s,res=result(awards=[('Generic Firm','50','9,00')]));self.assertEqual('50',s.read_records()[0]['fields']['awards'][0]['quantity'])
    def test_no_award_is_explicit(self):
        s=source();self.install(s,res=result(awards='NO'));self.assertEqual('no_award_table',s.read_records()[0]['fields']['result_availability'])
    def test_award_arrival_and_disappearance_preserves_previous(self):
        empty=result(awards='NO');full=result();s=source();self.install(s,res=empty);previous=poll(s)[0]
        s.get=Mock(side_effect=[response(index().encode()),response(full),response(index().encode()),response(full)])
        state,alerts=poll(s,previous);self.assertEqual(1,len(alerts));self.assertEqual('published_table',state['source_state']['records']['rows']['1322']['row']['fields']['result_availability'])
        s.get=Mock(side_effect=[response(index().encode()),response(empty),response(index().encode()),response(empty)])
        saved=deepcopy(state)
        with self.assertRaises(SourceError):s.fetch_with_state(state)
        self.assertEqual(saved,state)
        self.assertEqual('published_table',state['source_state']['records']['rows']['1322']['row']['fields']['result_availability'])
    def test_duplicate_participant_fails(self):
        for aw in [[('X','5','10,00'),('X','5','10,00')]]:
            s=source();self.install(s,res=result(awards=aw))
            with self.assertRaises(SourceError):s.read_records()
    def test_footer_header_and_number_fail(self):
        raw=result().decode().replace('>50</td><td>10,00','>51</td><td>10,00')
        for html in (raw, result().decode().replace('Bud pr. enhet','Pris'), result().decode().replace('>500</div>','>500,0</div>')):
            s=source();self.install(s,res=html.encode())
            with self.assertRaises(SourceError):s.read_records()
    def test_iso_encoding_and_bounds(self):
        s=source();raw=result().decode('utf8').replace('Generic Firm','Å Firma').encode('cp1252');idx=index().encode();s.get=Mock(side_effect=[response(idx),response(raw,headers={'Content-Type':'text/html; charset=iso-8859-1'}),response(idx),response(raw,headers={'Content-Type':'text/html; charset=iso-8859-1'})]);self.assertIn('Å Firma',s.read_records()[0]['fields']['awards'][0]['participant_name'])
        s=source(max_bytes=1024);s.get=Mock(return_value=response(b'x'*2048))
        with self.assertRaises(SourceError):s.read_records()
    def test_index_filter_and_invalid_year(self):
        s=source(title_prefixes=['OTHER ']);self.install(s)
        with self.assertRaises(SourceError):s.read_records()
        s=source();self.install(s,idx=index(year=2025))
        with self.assertRaises(SourceError):s.read_records()
    def test_malformed_result_and_redirect(self):
        s=source();self.install(s,res=b'<html></html>')
        with self.assertRaises(SourceError):s.read_records()
        s=source();s.get=Mock(return_value=response(b'',302,{'Location':'https://evil.example'}))
        with self.assertRaises(SourceError):s.read_records()
    def test_second_sweep_mismatch(self):
        s=source();s.get=Mock(side_effect=[response(index().encode()),response(result()),response(index().encode()),response(result(minimum='11,00'))])
        with self.assertRaises(SourceError):s.read_records()
    def test_bad_rows_and_future_time_fail(self):
        original=result().decode()
        for raw in (original.replace('<td>50</td><td>10,00</td>', '<td>50</td>'),
                    original.replace('29.01.2026 16:03:00','29.12.2026 16:03:00'),
                    original.replace('29.01.2026 16:00:00','28.01.2026 06:00:00'),
                    original.replace('>10,00</td>','>1e1</td>')):
            with self.subTest(raw=raw):
                s=source();self.install(s,res=raw.encode())
                with self.assertRaises(SourceError):s.read_records()

    def test_duplicate_or_changed_index_fails(self):
        s=source();self.install(s,idx=index(ids=(1322,1322)))
        with self.assertRaises(SourceError):s.read_records()
        s=source();s.get=Mock(side_effect=[response(index().encode()),response(result()),response(index(ids=(1323,)).encode()),response(result())])
        with self.assertRaises(SourceError):s.read_records()

    def test_invalid_configuration(self):
        with self.assertRaises(ValueError):source(quantity_unit='lb')
        with self.assertRaises(ValueError):source(urls=('https://evil.example',))

if __name__=='__main__':unittest.main()
