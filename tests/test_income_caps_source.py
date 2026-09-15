import unittest
from decimal import Decimal
from unittest.mock import Mock
from xml.etree import ElementTree as E

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.income_caps import IncomeCapsSource, PAGE_URL, BASE, footer_labels
from watchtower.sources.workbooks import NS, REL, RID
from test_change_sources import poll, response
from test_media_database import zipped, mutate

LINK = 'https://www.nve.no/media/12345/example-caps.xlsx'


def page(year=2025, link=LINK):
    return (f'<h1>Inntektsrammer for {year} - vedtak</h1>'
            f'<a href="{link}">Vedtak om inntektsramme {year} for andre nettselskaper (excel)</a>').encode()


def book(companies=None, year=2025, cost_year=2023):
    companies = companies if companies is not None else [('923609016','7','Example Grid','120')]
    workbook = E.Element(NS+'workbook'); sheets = E.SubElement(workbook,NS+'sheets')
    E.SubElement(sheets,NS+'sheet',{'name':f'Inntektsramme {year}',RID:'rId1'})
    rels = E.Element(REL+'Relationships')
    E.SubElement(rels,REL+'Relationship',{'Id':'rId1','Target':'worksheets/sheet2.xml'})
    worksheet = E.Element(NS+'worksheet'); data = E.SubElement(worksheet,NS+'sheetData')
    def add(number, values, formula_columns=()):
        r = E.SubElement(data,NS+'row',{'r':str(number)})
        for column, value in values.items():
            c = E.SubElement(r,NS+'c',{'r':column+str(number),'t':'n' if column in formula_columns else 'inlineStr'})
            if column in formula_columns:
                E.SubElement(c,NS+'f').text='1/0'  # The stored result is read, never this formula.
                E.SubElement(c,NS+'v').text=value
            else:
                E.SubElement(E.SubElement(c,NS+'is'),NS+'t').text=value
    add(1,{'A':'Tall i 1000 NOK','S':'Beregning og kalibrering av inntektsramme'})
    add(2,{'A':'Orgnr','B':'ID','C':f'Selskapsnavn (fra eRapp {cost_year})','D':f'Inntektsramme {year}'})
    for n, values in enumerate(companies,3):
        add(n,dict(zip('ABCD',values)),('D',))
    n = len(companies)+4
    add(n,{'C':'Sum','D':str(sum(Decimal(c[3]) for c in companies))},('D',))
    for i, (left, right) in enumerate(footer_labels(cost_year), n+2):
        add(i,{'I':left,'K':right,'N':'0'},('N',))
    return zipped({'xl/workbook.xml':E.tostring(workbook),'xl/_rels/workbook.xml.rels':E.tostring(rels),
                   'xl/worksheets/sheet2.xml':E.tostring(worksheet)})


class IncomeCapsTests(unittest.TestCase):
    def source(self, urls=(PAGE_URL,), **options):
        return IncomeCapsSource(SourceConfig(id='caps',kind='income_caps',label='Income caps',urls=urls,
            filters=FilterRule(match_all=True),options=options),timeout=1,retry_attempts=1)

    def load(self, source, raw, html=None):
        html = page() if html is None else html
        source.get = Mock(side_effect=lambda url,**kw: response(raw if url==LINK else html))

    def test_baseline_repeat_and_cached_precision_zero_and_integer(self):
        s = self.source()
        companies = [('923609016','7','Example Grid','120'),('976967631','8','Second Grid','0')]
        self.load(s,book(companies)); first, alerts = poll(s)
        self.assertEqual([],alerts)
        self.assertEqual('120',s._next['rows']['923609016:2025:vedtak']['row']['fields']['cap_1000_nok'])
        self.assertEqual('0',s._next['rows']['976967631:2025:vedtak']['row']['fields']['cap_1000_nok'])
        second, alerts = poll(s,first)
        self.assertEqual(first,second); self.assertEqual([],alerts)
        self.load(s,book([('923609016','7','Example Grid','111148.03944569195')]))
        records = s.read_records()
        self.assertEqual('111148.03944569195',records[0]['fields']['cap_1000_nok'])
        self.assertEqual(2023,records[0]['fields']['cost_base_year'])

    def test_amount_name_id_changes_alert_once_and_absence_is_not_withdrawal(self):
        s = self.source(); self.load(s,book()); state,_ = poll(s)
        self.load(s,book([('923609016','9','Revised Grid','130')]))
        state, alerts = poll(s,state); self.assertEqual(1,len(alerts))
        self.assertIn('1000 NOK',' '.join(alerts[0].item.alert_details))
        self.assertIn('2023',' '.join(alerts[0].item.alert_details))
        _, alerts = poll(s,state); self.assertEqual([],alerts)
        self.load(s,book([('923609016','9','Revised Grid','130'),('976967631','8','Second Grid','2')]))
        state, alerts = poll(s,state); self.assertEqual(1,len(alerts))
        self.load(s,book([('923609016','9','Revised Grid','130')]))
        _, alerts = poll(s,state); self.assertEqual([],alerts)

    def test_invalid_identity_duplicate_internal_id_and_record_bounds_fail(self):
        for companies in [ [('123456789','7','Grid','2')], [('923609016','0','Grid','2')],
                           [('923609016','7','','2')], [('923609016','7','Grid','2')]*2,
                           [('923609016','7','Grid','2'),('976967631','7','Other','3')] ]:
            s = self.source(); self.load(s,book(companies))
            with self.subTest(companies=companies),self.assertRaises(SourceError): s.read_records()
        s = self.source(max_records=1); self.load(s,book([('923609016','7','Grid','2'),('976967631','8','Other','3')]))
        with self.assertRaises(SourceError): s.read_records()

    def test_total_footer_blank_company_and_cache_corruption_fail_before_state(self):
        s = self.source(); raw = book(); self.load(s,raw); state,_ = poll(s)
        def xml_change(raw,fn):
            root = E.fromstring(raw); fn(root); return E.tostring(root)
        corruptions = [
            lambda r:r.find('.//'+NS+'c[@r="D3"]/'+NS+'v').__setattr__('text',None),
            lambda r:r.find('.//'+NS+'c[@r="D3"]/'+NS+'v').__setattr__('text','1E+99'),
            lambda r:r.find('.//'+NS+'c[@r="D5"]/'+NS+'v').__setattr__('text','999'),
            lambda r:r.find('.//'+NS+'c[@r="A3"]/'+NS+'is/'+NS+'t').__setattr__('text',''),
            lambda r:r.find('.//'+NS+'c[@r="K7"]/'+NS+'is/'+NS+'t').__setattr__('text','Unknown footer'),
            lambda r:r.find(NS+'sheetData').remove(r.find('.//'+NS+'row[@r="15"]')),
        ]
        for fn in corruptions:
            bad = mutate(raw,'xl/worksheets/sheet2.xml',lambda b:xml_change(b,fn))
            self.load(s,bad)
            with self.subTest(fn=fn),self.assertRaises(SourceError):poll(s,state)
        self.load(s,raw); repeated, alerts = poll(s,state)
        self.assertEqual(state,repeated); self.assertEqual([],alerts)

    def test_unit_and_cost_year_mismatch_fail(self):
        s = self.source()
        for raw in [book(cost_year=2025),mutate(book(),'xl/worksheets/sheet2.xml',lambda b:b.replace(b'Tall i 1000 NOK',b'Tall i NOK'))]:
            self.load(s,raw)
            with self.assertRaises(SourceError):s.read_records()

    def test_page_identity_external_attachment_and_race_fail(self):
        for html in [page().replace(b'<h1>',b'<h2>').replace(b'</h1>',b'</h2>'),page(2026),page(link='https://example.test/other.xlsx'),page()+page()]:
            s = self.source(); self.load(s,book(),html)
            with self.assertRaises(SourceError):s.read_records()
            self.assertEqual(1,s.get.call_count)
        s = self.source(); s.get = Mock(side_effect=[response(page()),response(book()),response(page(link=LINK.replace('12345','12346')))])
        with self.assertRaisesRegex(SourceError,'during reading'):s.read_records()
        s = self.source(); s.get = Mock(return_value=response(b'',status=302,headers={'Location':LINK}))
        with self.assertRaises(SourceError):s.read_records()

    def test_explicit_next_year_has_distinct_identity(self):
        url = BASE+'inntektsrammer-for-2026-vedtak/'
        s = self.source(urls=(url,)); self.load(s,book(year=2026,cost_year=2024),page(2026))
        records = s.read_records()
        self.assertEqual('923609016:2026:vedtak',records[0]['key'])
        self.assertEqual(2024,records[0]['fields']['cost_base_year'])
        for urls in [(),(PAGE_URL,PAGE_URL),('https://example.test',),(BASE+'inntektsrammer-for-2026-varsel/',)]:
            with self.assertRaises(ValueError):self.source(urls=urls)
        with self.assertRaises(ValueError):self.source(complete_snapshot=True)
        with self.assertRaises(ValueError):self.source(events=['removed'])


if __name__=='__main__':unittest.main()
