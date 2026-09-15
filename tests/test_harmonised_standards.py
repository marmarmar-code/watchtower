from copy import deepcopy
from html import escape
from unittest.mock import Mock
import tempfile
import unittest
from xml.etree import ElementTree as E

from watchtower.config import Config,SourceConfig,FilterRule
from watchtower.engine import run
from watchtower.state import StateStore
from watchtower.sources.common import SourceError
from watchtower.sources.harmonised_standards import HarmonisedStandardsSource,PAGE,HEADERS
from watchtower.sources.workbooks import NS,REL,RID
from test_media_database import zipped
from test_change_sources import poll,response

LINK='https://single-market-economy.ec.europa.eu/document/download/11111111-1111-1111-1111-111111111111_en?filename=summary.xlsx'
TARGET='https://webgate.ec.europa.eu/circabc-ewpp/d/d/workspace/SpacesStore/22222222-2222-2222-2222-222222222222/download'


def row(reference='EN ISO 12345:2025',amendment=None,end='',title='Example device standard'):
    return ['2017/745 - Medical Devices','CEN',reference+'\n'+title+('' if amendment is None else '\n'+amendment),'17.06.2026','OJ L','2021/1182','17.06.2026',end,'','','']


def book(rows=None,generated='17.6.2026',headers=None,formula=False):
    w=E.Element(NS+'workbook');s=E.SubElement(w,NS+'sheets');E.SubElement(s,NS+'sheet',{'name':'2017-745-Medical Devices',RID:'rId1'})
    rels=E.Element(REL+'Relationships');E.SubElement(rels,REL+'Relationship',{'Id':'rId1','Target':'worksheets/sheet1.xml'})
    sheet=E.Element(NS+'worksheet');data=E.SubElement(sheet,NS+'sheetData')
    cells=[['Generated on '+generated]+['']*10,['']*11,HEADERS if headers is None else headers]+([row()] if rows is None else rows)
    for n,values in enumerate(cells,1):
        r=E.SubElement(data,NS+'row',{'r':str(n)})
        for i,v in enumerate(values):
            c=E.SubElement(r,NS+'c',{'r':chr(65+i)+str(n),'t':'inlineStr'});E.SubElement(E.SubElement(c,NS+'is'),NS+'t').text=v
            if formula and n==4 and i==3:E.SubElement(c,NS+'f').text='1+1'
    return zipped({'xl/workbook.xml':E.tostring(w),'xl/_rels/workbook.xml.rels':E.tostring(rels),'xl/worksheets/sheet1.xml':E.tostring(sheet)})


def source(**options):return HarmonisedStandardsSource(SourceConfig(id='standards',kind='harmonised_standards',label='Standards',urls=(),filters=FilterRule(match_all=True),options=options))


def install(s,raw=None,second=None,redirect=True,page=None):
    raw=book() if raw is None else raw;page=('<h1>Medical devices</h1><a href="'+escape(LINK)+'">Summary list as xls file</a>').encode() if page is None else page
    sequence=[]
    for b in [raw,raw if second is None else second]:
        sequence.append(response(page))
        if redirect:sequence.append(response(b'',302,{'Location':TARGET}))
        sequence.append(response(b))
    s.get=Mock(side_effect=sequence)


class HarmonisedStandardsTests(unittest.TestCase):
    def test_quiet_initial_repeat_and_absent_dates(self):
        s=source();install(s);old,alerts=poll(s);self.assertEqual([],alerts);r=next(iter(old['source_state']['records']['rows'].values()))['row']
        self.assertIsNone(r['published']);self.assertIsNone(r['fields']['end_of_legal_effect']);self.assertEqual('2026-06-17',r['fields']['publication_date'])
        install(s);new,alerts=poll(s,old);self.assertEqual(old,new);self.assertEqual([],alerts)

    def test_scheduled_withdrawal_changes_once_and_does_not_imply_ban(self):
        s=source();install(s);old,_=poll(s);changed=row(end='15.12.2027');changed[8:]=['OJ L','2026/100','18.06.2026']
        install(s,book([changed]));new,alerts=poll(s,old);self.assertEqual(1,len(alerts));self.assertIn('ikke et produktforbud',' '.join(alerts[0].item.alert_details))
        install(s,book([changed]));self.assertEqual([],poll(s,new)[1])

    def test_amendment_sets_keep_same_base_separate_and_title_revision_keeps_identity(self):
        s=source();install(s,book([row(),row(amendment='EN ISO 12345:2025/A1:2026')]));old,_=poll(s);self.assertEqual(2,len(s._next['rows']))
        install(s,book([row(title='Revised example title'),row(amendment='EN ISO 12345:2025/A1:2026')]));new,alerts=poll(s,old);self.assertEqual(1,len(alerts));self.assertEqual('changed',alerts[0].item.metadata['event'])
        self.assertEqual(set(old['source_state']['records']['rows']),set(new['source_state']['records']['rows']))

    def test_selected_reference_matches_sets_containing_it(self):
        s=source(references=['EN ISO 12345:2025']);install(s,book([row(),row(amendment='EN ISO 12345:2025/A1:2026'),row('EN ISO 99999:2025')]));self.assertEqual(2,len(s.read_records()))
        s=source(references=['EN ISO 88888:2025']);install(s)
        with self.assertRaises(SourceError):s.read_records()

    def test_absence_does_not_alert_or_erase_seen_history(self):
        s=source();install(s,book([row(),row('EN ISO 99999:2025')]));old,_=poll(s)
        install(s);new,alerts=poll(s,old);self.assertEqual([],alerts);self.assertEqual(old['seen'],new['seen'])

    def test_generation_date_does_not_alert_but_regression_preserves_state(self):
        s=source();install(s);old,_=poll(s);install(s,book(generated='18.6.2026'));new,alerts=poll(s,old);self.assertEqual([],alerts)
        install(s)
        with self.assertRaisesRegex(SourceError,'regressed'):poll(s,new)

    def test_mid_read_revision_and_bad_headers_preserve_saved_state(self):
        s=source();install(s);old,_=poll(s);saved=deepcopy(old)
        for first,second in [(book(),book([row(end='15.12.2027')])),(book(headers=['Changed']+HEADERS[1:]),None)]:
            install(s,first,second)
            with tempfile.TemporaryDirectory() as d:
                store=StateStore(d);store.save('standards',old);result=run(Config((s.config,)),store,None,source_factory=lambda _:s)
                self.assertIn('standards',result.errors);self.assertEqual(saved,store.load('standards'))

    def test_duplicate_references_blank_rows_and_bounds(self):
        for rows,options in [([row(),row()],{}),([row(),['']*11,row('EN ISO 99999:2025')],{}),([row(),row('EN ISO 99999:2025')],{'max_records':1}),([row(),row('EN ISO 99999:2025')],{'max_sheet_rows':4}),([row(amendment='EN ISO 12345:2025')],{})]:
            s=source(**options);install(s,book(rows))
            with self.subTest(options=options),self.assertRaises(SourceError):s.read_records()

    def test_invalid_dates_law_body_and_generation(self):
        bad=[]
        for index,value in [(0,'Other law'),(1,'Other body'),(2,'Example title'),(3,'31.02.2026'),(4,''),(5,'unknown'),(7,'01.01.2020')]:
            r=row();r[index]=value;bad.append(book([r]))
        bad+=[book(generated='31.12.2099'),book(formula=True),b'not a workbook']
        for b in bad:
            s=source();install(s,b)
            with self.assertRaises(SourceError):s.read_records()

    def test_direct_download_and_strict_redirect_destination(self):
        s=source();install(s,redirect=False);self.assertEqual(1,len(s.read_records()))
        for target in ['https://example.test/file.xlsx',TARGET+'?changed=1',TARGET.replace('https:','http:')]:
            s=source();s.get=Mock(side_effect=[response(('<h1>Medical devices</h1><a href="'+escape(LINK)+'">Summary list as xls file</a>').encode()),response(b'',302,{'Location':target})])
            with self.assertRaises(SourceError):s.read_records()
        s=source();s.get=Mock(return_value=response(b'',302,{'Location':TARGET}))
        with self.assertRaises(SourceError):s.read_records()

    def test_missing_or_duplicate_link_wrong_title_and_download_size(self):
        for html in [b'<h1>Other</h1>',b'<h1>Medical devices</h1>',('<h1>Medical devices</h1>'+('<a href="'+escape(LINK)+'">Summary list as xls file</a>')*2).encode()]:
            s=source();install(s,page=html)
            with self.assertRaises(SourceError):s.read_records()
        s=source(max_bytes=1024);s.get=Mock(return_value=response(b'x'*1025))
        with self.assertRaises(SourceError):s.read_records()

    def test_configuration_rejects_removals_and_bad_references(self):
        for options in [{'complete_snapshot':True},{'events':['removed']},{'references':[]},{'references':['example']},{'max_sheet_rows':True},{'max_unpacked_bytes':0}]:
            with self.assertRaises(ValueError):source(**options)


if __name__=='__main__':unittest.main()
