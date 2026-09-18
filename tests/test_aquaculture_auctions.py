import unittest
from unittest.mock import Mock

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.aquaculture_auctions import AquacultureAuctionsSource, PAGE
from watchtower.sources.common import SourceError
from test_change_sources import poll

URL = PAGE + '/auksjon-2024'


def source():
    return AquacultureAuctionsSource(SourceConfig(id='auction', kind='aquaculture_auctions',
        label='Auction', filters=FilterRule(match_all=True), options={'max_records':100}))


def page(capacity=100, price=200, *, total=None, pending=False):
    header='<html><main><h1>Auksjon av produksjonskapasitet 2024</h1>'
    if pending:
        return header + '<p>Resultatet publiseres etter auksjonen.</p></main></html>'
    return header + '''<p>Auksjonen ble holdt 24.-25. juni 2024 og minsteprisen var oppgitt.</p>
      <table><thead><tr><th>Selskap</th><th>Vunnet kapasitet</th><th>Vederlag</th></tr></thead><tbody>
      <tr><td>Example Fish AS</td><td>%(capacity)s</td><td>%(price)s</td></tr>
      <tr><td>Sum</td><td>%(total)s</td><td>%(price)s</td></tr></tbody></table>
      <table><thead><tr><th></th><th>Tonn MTB tildelt</th><th>Samlet vederlag (NOK)</th></tr></thead><tbody>
      <tr><td>1. Example area</td><td>%(capacity)s</td><td>%(price)s</td></tr></tbody></table>
      </main></html>''' % {'capacity':capacity,'price':price,'total':capacity if total is None else total}


def installed(s, raw):
    s._poll=Mock(return_value=s._auction(URL,2024,raw))


class AuctionTests(unittest.TestCase):
    def test_quiet_initial_repeat_and_one_explained_revision(self):
        s=source();installed(s,page());a,alerts=poll(s)
        self.assertFalse(alerts);self.assertEqual(2,len(a['source_state']['records']['rows']))
        b,alerts=poll(s,a);self.assertFalse(alerts);self.assertEqual(a,b)
        installed(s,page(capacity=110,price=230));c,alerts=poll(s,b)
        self.assertEqual(1,len(alerts));details=str(alerts[0].item.alert_details)
        self.assertIn('100 → 110',details);self.assertIn('200 → 230',details)
        self.assertIn('tonn MTB',details);self.assertIn('NOK',details)
        self.assertIsNone(alerts[0].item.published);self.assertFalse(poll(s,c)[1])

    def test_future_announced_auction_is_an_event_year(self):
        s=source();raw='<html><main><h1>Auksjon av produksjonskapasitet</h1><a href="'+PAGE+'/auksjon-2030">Auksjon 2030</a></main></html>'
        self.assertEqual([(PAGE+'/auksjon-2030',2030)],s._index(raw))

    def test_result_publication_explains_status_transition(self):
        s=source();installed(s,page(pending=True));a,_=poll(s)
        installed(s,page());b,alerts=poll(s,a)
        self.assertEqual(2,len(alerts))
        parent=next(a.item for a in alerts if a.item.url==URL and 'Resultatstatus' in str(a.item.alert_details))
        self.assertIn('resultattabell ikke publisert → resultattabell publisert',str(parent.alert_details))
        self.assertFalse(poll(s,b)[1])

    def test_previously_published_table_cannot_disappear(self):
        s=source();installed(s,page());a,_=poll(s)
        installed(s,page(pending=True))
        with self.assertRaisesRegex(SourceError,'disappeared'):poll(s,a)

    def test_total_mismatch_duplicate_or_incomplete_page_fails(self):
        s=source()
        for raw in [page(total=99),page().replace('Example Fish AS','Sum'),
                    page()[:-7],page().replace('Tonn MTB tildelt','Other')]:
            with self.assertRaises(SourceError):s._auction(URL,2024,raw)

    def test_no_result_table_is_not_allowed_when_page_claims_results(self):
        with self.assertRaises(SourceError):source()._auction(URL,2024,
            page(pending=True).replace('Resultatet publiseres etter auksjonen.','Selskap som kjøpte kapasitet'))

    def test_double_read_changes_do_not_commit_snapshot(self):
        s=source();s._poll=Mock(side_effect=[s._auction(URL,2024,page()),s._auction(URL,2024,page(price=201))])
        with self.assertRaises(SourceError):s.fetch_with_state(None)
        self.assertEqual({},s._next)


if __name__=='__main__':
    unittest.main()
