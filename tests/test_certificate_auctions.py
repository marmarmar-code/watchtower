from copy import deepcopy
import tempfile
import unittest
from unittest.mock import Mock

from watchtower.config import Config,SourceConfig,FilterRule
from watchtower.engine import run
from watchtower.state import StateStore
from watchtower.sources.certificate_auctions import CertificateAuctionsSource,URL
from watchtower.sources.common import SourceError
from test_change_sources import poll,response

ISIN='NO0000000001'
TITLE='Auksjonsresultater i '+ISIN


def full(body,meta=''):
    return ('<html><head>'+meta+'</head><body>'+body+'</body></html>').encode()


def index():
    return full('<input id="newsListCurrentPageId" value="123"><input id="newsListLanguage" value="no">',f'<meta property="og:url" content="{URL}">')


def article(year=2026,slug='result',result=True):
    title=TITLE if result else 'Auksjoner i '+ISIN
    return f'<article class="article-list__item"><h3 class="article-list__item-heading"><a href="{URL}{year}/{slug}/">{title}</a></h3><div class="article-list__meta"><div class="meta">Publisert: 15. september {year}</div></div></article>'


def listing(rows=None,year=2026,selected=2026,page=1,more=False):
    content=article(year) if rows is None else ''.join(rows)
    if page==1:
        content='<select id="Resources_SelectedYear">'+''.join(f'<option value="{y}"'+(' selected' if y==selected else '')+'>'+('Alle' if y==0 else str(y))+'</option>' for y in [0,year])+'</select><h2 class="article-list__heading">Auksjonsresultater og innbydelser</h2>'+content
    if more:content+=f'<button class="_jsNewsListLoadMore_newslist" data-currentloaded="{page}">Se flere</button>'
    return content.encode()


def detail(year=2026,yield_='4,250 %',allocated='1 000 MNOK',marginal='50,00 %'):
    rows=[('Auksjonsdato:',f'15. september {year}'),('Oppgjørsdato:',f'17. september {year}'),
          ('ISIN:',ISIN),('Forfallsdato:',f'17. desember {year}'),('Tildelingskurs:','98,9500'),('Tildelingsrente:',yield_),
          ('Tildelt volum:',allocated),('Totalt budvolum:','2 000 MNOK'),('Tildeling på marginalrente:',marginal)]
    table='<div class="article__main-body"><table>'+''.join('<tr><td>'+k+'</td><td>'+v+'</td></tr>' for k,v in rows)+'</table></div>'
    meta=f'<meta property="og:url" content="{URL}{year}/result/"><meta property="og:title" content="{TITLE}"><meta property="article:published_time" content="15.09.{year} 11:10:00">'
    return full(table,meta)


def source(**options):
    return CertificateAuctionsSource(SourceConfig(id='auctions',kind='certificate_auctions',label='Certificate auctions',urls=(),filters=FilterRule(match_all=True),options=options))


def sweep(year=2026,body=None):
    return [index(),listing(year=year,selected=0),listing(year=year,selected=year),detail(year) if body is None else body]


def install(s,first=None,second=None):
    first=sweep() if first is None else first
    s.get=Mock(side_effect=[response(b) for b in first+(first if second is None else second)])


class CertificateTests(unittest.TestCase):
    def test_baseline_repeat_exact_units_and_distinct_dates(self):
        s=source();install(s);old,alerts=poll(s);self.assertEqual([],alerts)
        f=old['source_state']['records']['rows']['2026-09-15:'+ISIN]['row']['fields']
        self.assertEqual(('2026-09-15','2026-09-17','2026-12-17'),tuple(f[k] for k in ['auction_date','settlement_date','maturity_date']))
        self.assertEqual(('98.9500','4.250','1000','2000','50.00'),tuple(f[k] for k in ['allocation_price','allocation_yield_percent','allocated_mnok','bid_mnok','marginal_allocation_percent']))
        self.assertEqual('15.09.2026 11:10:00',f['published_local'])
        install(s);new,alerts=poll(s,old);self.assertEqual(old,new);self.assertEqual([],alerts)

    def test_revised_yield_and_volume_alert_once_with_before_after(self):
        s=source();install(s);old,_=poll(s)
        changed=sweep(body=detail(yield_='4,200 %',allocated='1 200 MNOK'));install(s,changed)
        new,alerts=poll(s,old);self.assertEqual(1,len(alerts));self.assertIn('1000 → 1200',' '.join(alerts[0].item.alert_details))
        install(s,changed);self.assertEqual([],poll(s,new)[1])

    def test_negative_yield_and_zero_allocation_are_not_invented_or_rejected(self):
        s=source();install(s,sweep(body=detail(yield_='-0,250 %',allocated='0 MNOK',marginal='0,00 %')));state,_=poll(s)
        f=state['source_state']['records']['rows']['2026-09-15:'+ISIN]['row']['fields'];self.assertEqual('-0.250',f['allocation_yield_percent']);self.assertEqual('0',f['allocated_mnok'])

    def test_invitations_are_validated_but_not_read_as_results(self):
        s=source();bodies=[index(),listing(selected=0),listing(rows=[article(),article(slug='invitation',result=False)]),detail()];install(s,bodies)
        self.assertEqual(1,len(s.read_records()));self.assertEqual(8,s.get.call_count)

    def test_full_pagination_marker_and_bound(self):
        first=[article(slug='result-'+str(i)) for i in range(20)]
        s=source();s.get=Mock(side_effect=[response(b) for b in [index(),listing(selected=0),listing(rows=first,more=True),listing(rows=[article(slug='last')],page=2)]])
        year,all_rows,selected=s._index();self.assertEqual(2026,year);self.assertEqual(21,len(all_rows));self.assertEqual(21,len(selected))
        s=source(max_pages=1);s.get=Mock(side_effect=[response(b) for b in [index(),listing(selected=0),listing(rows=first,more=True)]])
        with self.assertRaisesRegex(SourceError,'max_pages'):s._index()
        s=source();s.get=Mock(side_effect=[response(b) for b in [index(),listing(selected=0),listing(more=True)]])
        with self.assertRaisesRegex(SourceError,'pagination marker'):s._index()

    def test_year_filter_duplicate_url_foreign_links_and_invalid_index(self):
        bads=[listing(selected=0),listing(rows=[article(),article()]),listing().replace(URL.encode(),b'https://example.test/'),
              listing().replace(b'Publisert:',b'Other:'),listing().replace(b'15. september 2026',b'15. september 2025'),
              listing().replace(b'Auksjonsresultater i ',b'Other type '),listing(rows=[])]
        for bad in bads:
            s=source();s.get=Mock(side_effect=[response(b) for b in [index(),listing(selected=0),bad]])
            with self.subTest(bad=bad[:50]),self.assertRaises(SourceError):s._index()
        s=source();s.get=Mock(side_effect=[response(index()),response(listing(selected=0)[:-5])])
        with self.assertRaisesRegex(SourceError,'fragment is incomplete'):s._index()
        s=source();s.get=Mock(return_value=response(index().replace(b'value="no"',b'value="en"')))
        with self.assertRaises(SourceError):s._index()

    def test_detail_identity_and_publication_disagreement(self):
        bads=[detail().replace(b'2026/result/',b'2026/other/'),detail().replace(TITLE.encode(),b'Other title'),
              detail().replace(b'15.09.2026 11:10:00',b'14.09.2026 11:10:00'),detail().replace(b'11:10:00',b'25:10:00'),
              detail().replace(b'<td>ISIN:</td>',b'<td>Other:</td>'),detail().replace(ISIN.encode(),b'NO0000000002',1)]
        for bad in bads:
            s=source();install(s,sweep(body=bad))
            with self.subTest(bad=bad[:40]),self.assertRaises(SourceError):s.read_records()

    def test_units_numbers_date_order_and_marginal_range(self):
        bads=[detail(yield_='NaN %'),detail(allocated='1 000 NOK'),detail(allocated='1 00 MNOK'),detail(marginal='101,00 %'),
              detail().replace(b'17. desember 2026',b'17. juni 2026'),detail().replace(b'15. september 2026',b'31. februar 2026'),
              detail().replace(b'<td>98,9500</td>',b'<td colspan="2">98,9500</td>')]
        for bad in bads:
            s=source();install(s,sweep(body=bad))
            with self.subTest(bad=bad[:40]),self.assertRaises(SourceError):s.read_records()

    def test_empty_separator_rows_do_not_change_state(self):
        s=source();install(s);old,_=poll(s)
        body=detail().replace(b'<tr><td>ISIN:',b'<tr><td></td></tr><tr><td>ISIN:');install(s,sweep(body=body))
        new,alerts=poll(s,old);self.assertEqual(old,new);self.assertEqual([],alerts)

    def test_second_sweep_drift_and_year_regression_preserve_state(self):
        s=source();install(s);old,_=poll(s);saved=deepcopy(old)
        for a,b in [(sweep(),sweep(body=detail(allocated='900 MNOK'))),(sweep(2025),None)]:
            install(s,a,b)
            with tempfile.TemporaryDirectory() as directory:
                store=StateStore(directory);store.save('auctions',old)
                outcome=run(Config((s.config,)),store,None,source_factory=lambda _:s)
                self.assertIn('auctions',outcome.errors);self.assertEqual(saved,store.load('auctions'))

    def test_network_and_size_bounds_close_responses(self):
        for status,body,options in [(302,index(),{}),(200,b'x'*2048,{'max_bytes':1024}),(200,index()[:-10],{}),(200,index().replace(b'<body>',b'<body>\xff'),{})]:
            s=source(**options);r=response(body,status=status);s.get=Mock(return_value=r)
            with self.assertRaises(SourceError):s.read_records()
            r.close.assert_called_once()
        s=source(max_results=1);s.get=Mock(side_effect=[response(b) for b in [index(),listing(selected=0),listing(rows=[article(),article(slug='second')])]])
        with self.assertRaises(SourceError):s._index()

    def test_config_disallows_removal_and_invalid_bounds(self):
        for options in [{'max_pages':0},{'max_results':True},{'events':['removed']},{'complete_snapshot':True}]:
            with self.subTest(options=options),self.assertRaises(ValueError):source(**options)


if __name__=='__main__':unittest.main()
