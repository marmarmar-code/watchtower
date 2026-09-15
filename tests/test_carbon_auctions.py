from copy import deepcopy
from datetime import date
from decimal import Decimal
from unittest.mock import Mock
from xml.etree import ElementTree as E
import tempfile
import unittest

from watchtower.config import Config,SourceConfig,FilterRule
from watchtower.engine import run
from watchtower.state import StateStore
from watchtower.sources.carbon_auctions import CarbonAuctionsSource,HEADERS,BENEFICIARIES,FORMULA,amount
from watchtower.sources.workbooks import NS,REL,RID
from watchtower.sources.common import SourceError
from test_media_database import zipped,mutate
from test_change_sources import response,poll


def col(i):
    s=''
    while i:i,n=divmod(i-1,26);s=chr(65+n)+s
    return s


def row(day='2026-09-15',status='successful',price='85.52',volume='1000',revenue='85520',zone='EU'):
    serial=str((date.fromisoformat(day)-date(1899,12,30)).days)
    return [serial,str(Decimal(serial)+Decimal('0.4587')),'Example auction','T3PA',status,price]+['1']*4+[volume]+['1']*12+[revenue,zone]+['']*35


def book(rows=None,formula=FORMULA,date1904='false',time_format='hh:mm',headers=None,data_formula=False):
    wb=E.Element(NS+'workbook');E.SubElement(wb,NS+'workbookPr',{'date1904':date1904});ss=E.SubElement(wb,NS+'sheets');E.SubElement(ss,NS+'sheet',{'name':'Primary Market Auction',RID:'rId1'})
    rel=E.Element(REL+'Relationships');E.SubElement(rel,REL+'Relationship',{'Id':'rId1','Target':'worksheets/sheet1.xml'})
    st=E.Element(NS+'styleSheet');fm=E.SubElement(st,NS+'numFmts');E.SubElement(fm,NS+'numFmt',{'numFmtId':'164','formatCode':'dd.mm.yyyy'});E.SubElement(fm,NS+'numFmt',{'numFmtId':'165','formatCode':time_format});xf=E.SubElement(st,NS+'cellXfs');E.SubElement(xf,NS+'xf',{'numFmtId':'164'});E.SubElement(xf,NS+'xf',{'numFmtId':'165'})
    sheet=E.Element(NS+'worksheet');E.SubElement(sheet,NS+'dimension',{'ref':'B2:BI'+str(6+len([row()] if rows is None else rows))});sd=E.SubElement(sheet,NS+'sheetData')
    def add(n,values):
        r=E.SubElement(sd,NS+'row',{'r':str(n)})
        for i,v in values.items():
            numeric=n>=7 and i in [2,3];c=E.SubElement(r,NS+'c',{'r':col(i)+str(n),'t':'n' if numeric else 'inlineStr'})
            if numeric:c.set('s','0' if i==2 else '1');E.SubElement(c,NS+'v').text=v
            else:E.SubElement(E.SubElement(c,NS+'is'),NS+'t').text=v
            if data_formula and n==7 and i==7:E.SubElement(c,NS+'f').text='1+1'
        return r
    add(2,{4:'Public'});r=add(3,{2:'More information'});c=E.SubElement(r,NS+'c',{'r':'D3'});E.SubElement(c,NS+'f').text=formula
    add(4,{12:'EEX Emissions market / Primary Market Auction'});add(5,{2:'References',7:'Prices',12:'Volumes',23:'Participants',25:'Revenue',27:'Revenue'})
    add(6,dict(enumerate((HEADERS if headers is None else headers)+BENEFICIARIES,2)))
    for n,values in enumerate([row()] if rows is None else rows,7):add(n,dict(enumerate(values,2)))
    return zipped({'xl/workbook.xml':E.tostring(wb),'xl/_rels/workbook.xml.rels':E.tostring(rel),'xl/sharedStrings.xml':E.tostring(E.Element(NS+'sst')),'xl/styles.xml':E.tostring(st),'xl/worksheets/sheet1.xml':E.tostring(sheet)})


def source(**options):return CarbonAuctionsSource(SourceConfig(id='carbon',kind='carbon_auctions',label='Carbon auctions',urls=(),filters=FilterRule(match_all=True),options=options))
def install(s,raw=None,second=None):
    raw=book() if raw is None else raw;s.get=Mock(side_effect=[response(raw),response(raw if second is None else second)])


class CarbonAuctionsTests(unittest.TestCase):
    def test_quiet_repeat_units_dates_and_missing_beneficiary(self):
        s=source();install(s);old,alerts=poll(s);self.assertEqual([],alerts);r=next(iter(s._next['rows'].values()))['row'];f=r['fields']
        self.assertIsNone(r['published']);self.assertEqual('2026-09-15',f['auction_date']);self.assertEqual('11:00',f['source_time']);self.assertEqual('85.52',f['price_eur_per_tco2']);self.assertIsNone(f['beneficiary_revenue_eur']['Norway\n(NO)'])
        install(s);new,alerts=poll(s,old);self.assertEqual(old,new);self.assertEqual([],alerts)

    def test_revised_price_and_revenue_alert_once(self):
        s=source();install(s);old,_=poll(s);b=book([row(price='90',revenue='90000')]);install(s,b);new,alerts=poll(s,old);self.assertEqual(1,len(alerts));self.assertEqual('changed',alerts[0].item.metadata['event']);install(s,b);self.assertEqual([],poll(s,new)[1])

    def test_non_successful_status_can_have_missing_amounts_without_zero(self):
        s=source();install(s);old,_=poll(s);install(s,book([row(status='cancelled',price='',volume='',revenue='')]))
        new,alerts=poll(s,old);self.assertEqual(1,len(alerts));f=next(iter(s._next['rows'].values()))['row']['fields'];self.assertIsNone(f['price_eur_per_tco2']);self.assertEqual('cancelled',f['status'])
        install(s,book([row(price='')]))
        with self.assertRaises(SourceError):s.read_records()

    def test_beneficiary_zero_is_distinct_from_blank(self):
        s=source();install(s);old,_=poll(s);r=row();r[25+27]='0';install(s,book([r]));_,alerts=poll(s,old);self.assertEqual(1,len(alerts))

    def test_scope_filter_new_auction_and_absence_not_cancellation(self):
        s=source(zones=['EU']);install(s,book([row(),row(zone='DE')]));old,_=poll(s);self.assertEqual(1,len(s._next['rows']))
        install(s,book([row(),row(day='2026-09-14')]));new,alerts=poll(s,old);self.assertEqual(1,len(alerts))
        install(s);missing,alerts=poll(s,new);self.assertEqual([],alerts);self.assertEqual(new['seen'],missing['seen'])
        s=source(zones=['NO']);install(s)
        with self.assertRaises(SourceError):s.read_records()

    def test_exact_decorative_formula_only_and_date_system(self):
        for b in [book(formula='1+1'),book(data_formula=True),book(date1904='true'),book(date1904='1'),book(time_format='mm:ss')]:
            s=source();install(s,b)
            with self.assertRaises(SourceError):s.read_records()

    def test_year_clock_and_nonfinite_numeric_guards(self):
        bad=[row(day='2025-12-31'),row(price='NaN'),row(price='-1'),row(price='1e99'),row(price='1e-99')];r=row();r[1]=str(Decimal(r[0])+1);bad.append(r)
        for r in bad:
            s=source();install(s,book([r]))
            with self.assertRaises(SourceError):s.read_records()

    def test_duplicates_headers_and_bounds(self):
        for b,options in [(book([row(),row()]),{}),(book(headers=['Changed']+HEADERS[1:]),{}),(book([row(),row(day='2026-09-14')]),{'max_records':1}),(book(),{'max_unpacked_bytes':1024}),(b'bad',{})]:
            s=source(**options);install(s,b)
            with self.assertRaises(SourceError):s.read_records()

    def test_mid_read_change_preserves_saved_state(self):
        s=source();install(s);old,_=poll(s);saved=deepcopy(old);install(s,book(),book([row(price='90')]))
        with tempfile.TemporaryDirectory() as d:
            store=StateStore(d);store.save('carbon',old);result=run(Config((s.config,)),store,None,source_factory=lambda _:s);self.assertIn('carbon',result.errors);self.assertEqual(saved,store.load('carbon'))

    def test_redirect_and_download_bounds(self):
        for status,b,options in [(302,b'',{}),(200,b'x'*1025,{'max_bytes':1024})]:
            s=source(**options);r=response(b,status);s.get=Mock(return_value=r)
            with self.assertRaises(SourceError):s.read_records()
            r.close.assert_called_once()

    def test_numeric_equivalent_representations_are_stable(self):
        self.assertEqual('100000000',amount('1.0E8'));self.assertEqual('0',amount('0.00'))

    def test_complete_rows_must_match_dimension(self):
        s=source();raw=book([row(),row(day='2026-09-14')])
        def truncate(xml):
            root=E.fromstring(xml);data=root.find(NS+'sheetData');data.remove(list(data)[-1]);return E.tostring(root)
        install(s,mutate(raw,'xl/worksheets/sheet1.xml',truncate))
        with self.assertRaisesRegex(SourceError,'dimension'):s.read_records()

    def test_bad_configuration(self):
        for options in [{'zones':[]},{'zones':['Norway']},{'max_rows':True},{'events':['removed']},{'complete_snapshot':True}]:
            with self.assertRaises(ValueError):source(**options)


if __name__=='__main__':unittest.main()
