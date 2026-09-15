import io
import unittest
import zipfile
from unittest.mock import Mock
from xml.etree import ElementTree as E

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.media_database import MediaDatabaseSource, PROFILES, PAGE_URL
from watchtower.sources.workbooks import NS, REL, RID, table_rows
from test_change_sources import response, poll

LINK='https://www.medietilsynet.no/globalassets/tema/mediedatabasen/260626_mediedatabasen_alle_data.xlsx'


def headers(dataset):
    name,width,mapping=PROFILES[dataset]
    return list(mapping)+['Other column '+str(i) for i in range(width-len(mapping))]


def row(dataset, **values):
    defaults={'name':'Example Publication','publisher':'Example Publisher','group':'Example Group','number':'1',
              'station':'Example Station','holder':'Example Operator','distribution':'FM lokalradio'}
    defaults.update(values)
    _,_,mapping=PROFILES[dataset]
    return [defaults.get(mapping.get(h),'') for h in headers(dataset)]


def book(dataset, values, *, trailing=0, shared=False):
    workbook=E.Element(NS+'workbook');sheets=E.SubElement(workbook,NS+'sheets')
    E.SubElement(sheets,NS+'sheet',{'name':PROFILES[dataset][0],RID:'rId1'})
    rels=E.Element(REL+'Relationships');E.SubElement(rels,REL+'Relationship',{'Id':'rId1','Target':'worksheets/sheet2.xml'})
    worksheet=E.Element(NS+'worksheet');data=E.SubElement(worksheet,NS+'sheetData');strings=E.Element(NS+'sst')
    for number,values_row in enumerate([headers(dataset),*values,*([[]]*trailing)],1):
        r=E.SubElement(data,NS+'row',{'r':str(number)})
        for i,value in enumerate(values_row):
            c=E.SubElement(r,NS+'c',{'r':chr(65+i)+str(number),'t':'s' if shared else 'inlineStr'})
            if shared:
                E.SubElement(c,NS+'v').text=str(len(strings));si=E.SubElement(strings,NS+'si');E.SubElement(si,NS+'t').text=value
            else:
                inline=E.SubElement(c,NS+'is');E.SubElement(inline,NS+'t').text=value
    entries={'xl/workbook.xml':E.tostring(workbook),'xl/_rels/workbook.xml.rels':E.tostring(rels),
             'xl/worksheets/sheet2.xml':E.tostring(worksheet),'xl/sharedStrings.xml':E.tostring(strings)}
    return zipped(entries)


def zipped(entries):
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
        for name,raw in entries.items():z.writestr(name,raw)
    return b.getvalue()


def mutate(raw,path,fn):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:entries={n:z.read(n) for n in z.namelist()}
    entries[path]=fn(entries[path]);return zipped(entries)


class MediaDatabaseTests(unittest.TestCase):
    def source(self,dataset='ownership',**options):
        return MediaDatabaseSource(SourceConfig(id='annual',kind='media_database',label='Annual data',urls=(PAGE_URL,),
            filters=FilterRule(match_all=True),options={'dataset':dataset,**options}),timeout=1,retry_attempts=1)

    def load(self,s,raw,year=2025,page=None):
        page=page if page is not None else f'<h1>Mediedatabasen</h1><p>Opplysninger gjelder for {year}.</p><a href="{LINK}">Last ned datagrunnlaget</a>'.encode()
        s.get=Mock(side_effect=lambda url,**kw:response(page if url==PAGE_URL else raw))

    def test_ownership_baseline_order_and_year_alone_are_quiet(self):
        s=self.source();a,b=row('ownership'),row('ownership',name='Second Publication')
        self.load(s,book('ownership',[a,b]));first,alerts=poll(s);self.assertEqual([],alerts)
        self.load(s,book('ownership',[b,a],shared=True));second,alerts=poll(s,first);self.assertEqual([],alerts);self.assertEqual(first,second)
        self.load(s,book('ownership',[b,a]),year=2026);third,alerts=poll(s,second);self.assertEqual([],alerts)
        self.assertTrue(all(v['row']['reference_year']==2026 for v in third['source_state']['records']['rows'].values()))
        self.load(s,book('ownership',[a,b]),year=2025)
        with self.assertRaisesRegex(SourceError,'regressed'):poll(s,third)

    def test_publisher_and_group_change_stay_under_publication_key(self):
        s=self.source();self.load(s,book('ownership',[row('ownership')]));state,_=poll(s)
        self.load(s,book('ownership',[row('ownership',publisher='New Publisher',group='New Group')]))
        state,alerts=poll(s,state);self.assertEqual(1,len(alerts));self.assertEqual(['name:example publication'],list(s._next['rows']))
        self.assertIn('2025',' '.join(alerts[0].item.alert_details));self.assertIn('New Publisher',' '.join(alerts[0].item.alert_details))
        _,alerts=poll(s,state);self.assertEqual([],alerts)

    def test_positive_number_allows_station_holder_and_distribution_changes(self):
        s=self.source('radio_content');self.load(s,book('radio_content',[row('radio_content')]));state,_=poll(s)
        self.load(s,book('radio_content',[row('radio_content',station='Revised Station',holder='New Operator',distribution='DAB')]))
        _,alerts=poll(s,state);self.assertEqual(1,len(alerts));self.assertEqual(['number:1'],list(s._next['rows']))

    def test_zero_numbers_use_distinct_names_and_duplicate_names_fail(self):
        s=self.source('radio_content');rows=[row('radio_content',number='0'),row('radio_content',number='0',station='Other Station')]
        self.load(s,book('radio_content',rows));records=s.read_records();self.assertEqual(2,len(records))
        self.assertTrue(all(r['identity_basis']=='name_for_zero_number' for r in records))
        self.load(s,book('radio_content',[rows[0],rows[0]]))
        with self.assertRaises(SourceError):s.read_records()
        self.load(s,book('radio_content',[row('radio_content'),row('radio_content',station='Other Station')]))
        with self.assertRaises(SourceError):s.read_records()

    def test_all_profiles_and_trailing_format_rows(self):
        for dataset in PROFILES:
            s=self.source(dataset);self.load(s,book(dataset,[row(dataset)],trailing=12,shared=True))
            with self.subTest(dataset=dataset):self.assertEqual(1,len(s.read_records()))

    def test_invalid_required_fields_schema_and_numbers_fail(self):
        for dataset,values in [('ownership',{'name':''}),('ownership',{'publisher':''}),('radio_content',{'number':'1.5'}),('radio_content',{'station':''}),('tv_content',{'holder':''})]:
            s=self.source(dataset);self.load(s,book(dataset,[row(dataset,**values)]))
            with self.subTest(values=values),self.assertRaises(SourceError):s.read_records()
        raw=book('ownership',[row('ownership')]);raw=mutate(raw,'xl/worksheets/sheet2.xml',lambda b:b.replace(b'>Avis<',b'>Unknown<'))
        s=self.source();self.load(s,raw)
        with self.assertRaises(SourceError):s.read_records()

    def test_link_year_and_redirect_guards(self):
        base=f'<h1>Mediedatabasen</h1><p>Opplysninger gjelder for 2025.</p><a href="{LINK}">Last ned datagrunnlaget</a>'
        for page in [base.replace('www.medietilsynet.no','www.medietilsynet.no.example.test'),base.replace('gjelder for 2025','gjelder for unknown'),base+'<p>Opplysninger gjelder for 2024.</p>']:
            s=self.source();self.load(s,book('ownership',[row('ownership')]),page=page.encode())
            with self.assertRaises(SourceError):s.read_records()
            self.assertEqual(1,s.get.call_count)
        s=self.source();reply=response(b'',status=302,headers={'Location':LINK});s.get=Mock(return_value=reply)
        with self.assertRaises(SourceError):s.read_records()
        reply.close.assert_called_once()

    def test_workbook_formula_external_relation_and_bad_shared_index_rejected(self):
        raw=book('ownership',[row('ownership')],shared=True)
        changes=[('xl/_rels/workbook.xml.rels',lambda b:b.replace(b'worksheets/sheet2.xml',b'../other.xml')),
                 ('xl/_rels/workbook.xml.rels',lambda b:b.replace(b'Id="rId1"',b'Id="rId1" TargetMode="External"')),
                 ('xl/worksheets/sheet2.xml',lambda b:b.replace(b'<ns0:v>0</ns0:v>',b'<ns0:v>99999</ns0:v>',1)),
                 ('xl/worksheets/sheet2.xml',lambda b:b.replace(b'<ns0:v>0</ns0:v>',b'<ns0:f>1+1</ns0:f><ns0:v>0</ns0:v>',1))]
        for path,fn in changes:
            s=self.source();self.load(s,mutate(raw,path,fn))
            with self.subTest(path=path),self.assertRaises(SourceError):s.read_records()

    def test_archive_size_record_bounds_and_interior_gap(self):
        raw=book('ownership',[row('ownership'),row('ownership',name='Other')])
        for options in [{'max_records':1},{'max_sheet_rows':2},{'max_unpacked_bytes':1024}]:
            s=self.source(**options);self.load(s,raw)
            with self.subTest(options=options),self.assertRaises(SourceError):s.read_records()
        s=self.source();self.load(s,mutate(raw,'xl/worksheets/sheet2.xml',lambda b:b.replace(b'r="2"',b'r="4"',1)))
        with self.assertRaises(SourceError):s.read_records()
        with self.assertRaises(SourceError):table_rows(b'invalid','Sheet',2000,10,2)

    def test_absence_does_not_claim_removal_and_config_is_explicit(self):
        s=self.source();self.load(s,book('ownership',[row('ownership'),row('ownership',name='Other')]));state,_=poll(s)
        self.load(s,book('ownership',[row('ownership')]));_,alerts=poll(s,state);self.assertEqual([],alerts)
        for options in [{'complete_snapshot':True},{'events':['removed']},{'dataset':'unknown'}]:
            if 'dataset' in options:
                with self.assertRaises(ValueError):self.source(**options)
            else:
                with self.assertRaises(ValueError):self.source(**options)


if __name__=='__main__':unittest.main()
