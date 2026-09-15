import unittest,tempfile
from unittest.mock import Mock,patch
from copy import deepcopy
from xml.etree import ElementTree as E
from zipfile import ZipFile
from io import BytesIO
from watchtower.config import SourceConfig,FilterRule,Config
from watchtower.sources.ets_compliance import EtsComplianceSource,HEADERS,PAGE
from watchtower.sources.common import SourceError
from test_change_sources import response,poll
from test_media_database import zipped
NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'; R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'; P='http://schemas.openxmlformats.org/package/2006/relationships'
def col(i):
 s=''
 while i:i,n=divmod(i-1,26);s=chr(65+n)+s
 return s
def book(rows=None):
 rows=rows or [['NO','Example installation','123456','Permit',10,'A','A','2024','100','0','100','2020','NOT SET','OPEN']]
 wb=E.Element('{%s}workbook'%NS);sh=E.SubElement(wb,'{%s}sheets'%NS);E.SubElement(sh,'{%s}sheet'%NS,{'name':'Export Worksheet','{%s}id'%R:'r1'});E.SubElement(sh,'{%s}sheet'%NS,{'name':'activity codes','{%s}id'%R:'r2'})
 rel=E.Element('{%s}Relationships'%P);E.SubElement(rel,'{%s}Relationship'%P,{'Id':'r1','Target':'worksheets/sheet1.xml'});E.SubElement(rel,'{%s}Relationship'%P,{'Id':'r2','Target':'worksheets/sheet2.xml'})
 def sheet(vals):
  x=E.Element('{%s}worksheet'%NS);E.SubElement(x,'{%s}dimension'%NS,{'ref':'A1:'+col(max(1,max(map(len,vals))))+str(len(vals))});d=E.SubElement(x,'{%s}sheetData'%NS)
  for n,row in enumerate(vals,1):
   rr=E.SubElement(d,'{%s}row'%NS,{'r':str(n)})
   for i,v in enumerate(row,1):
    c=E.SubElement(rr,'{%s}c'%NS,{'r':col(i)+str(n),'t':'inlineStr'});E.SubElement(E.SubElement(c,'{%s}is'%NS),'{%s}t'%NS).text=str(v)
  return E.tostring(x)
 vals=[[]]+[HEADERS]+rows; acts=[['value','code'],['Combustion','10']]
 return zipped({'xl/workbook.xml':E.tostring(wb),'xl/_rels/workbook.xml.rels':E.tostring(rel),'xl/sharedStrings.xml':E.tostring(E.Element('{%s}sst'%NS)),'xl/worksheets/sheet1.xml':sheet(vals),'xl/worksheets/sheet2.xml':sheet(acts)})
def src(**o):return EtsComplianceSource(SourceConfig(id='ets',kind='ets_compliance',label='ETS',urls=(),filters=FilterRule(match_all=True),options=o))
def install(s,a=None,b=None):
 a=book() if a is None else a;s.get=Mock(side_effect=[response(b'<h1>Union Registry</h1><li>03/10/2025 - <a href="https://climate.ec.europa.eu/document/download/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa_en?filename=compliance_2024_code_en.xlsx">Compliance data for 2024</a></li>'),response(a),response(b'<h1>Union Registry</h1><li>03/10/2025 - <a href="https://climate.ec.europa.eu/document/download/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa_en?filename=compliance_2024_code_en.xlsx">Compliance data for 2024</a></li>'),response(a if b is None else b)])
class Tests(unittest.TestCase):
 def setUp(self):self.p=patch('watchtower.sources.ets_compliance.today',return_value=__import__('datetime').date(2026,9,15));self.p.start();self.addCleanup(self.p.stop)
 def test_records_and_literal_values(self):
  s=src();install(s);r=s.read_records();self.assertEqual('100',r[0]['fields']['total_verified_emissions']);self.assertEqual(2024,r[0]['fields']['report_year'])
 def test_filter_and_codes(self):
  s=src(activity_codes=['10']);install(s);self.assertEqual(1,len(s.read_records()))
  s=src(activity_codes=['11']);install(s)
  with self.assertRaises(SourceError):s.read_records()
 def test_bad_code_and_duplicate(self):
  for rows in [[['NO','Example','123456','P',10,'Z','A','2024','1','0','1','2020','NOT SET','OPEN']], [['NO','A','123456','P',10,'A','A','2024','1','0','1','2020','NOT SET','OPEN'],['NO','B','123456','P',10,'A','A','2024','1','0','1','2020','NOT SET','OPEN']]]:
   s=src();install(s,book(rows));
   with self.assertRaises(SourceError):s.read_records()
 def test_not_calculated_preserved(self):
  row=['NO','Example','123456','P',10,'C','NOT APPLICABLE','2024','NOT CALCULATED','NOT APPLICABLE','0','2020','NOT SET','OPEN'];s=src();install(s,book([row]));r=s.read_records()[0]['fields'];self.assertEqual('NOT CALCULATED',r['total_verified_emissions'])
 def test_repeat_and_changed_code(self):
  idx=b'<h1>Union Registry</h1><li>03/10/2025 - <a href="https://climate.ec.europa.eu/document/download/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa_en?filename=compliance_2024_code_en.xlsx">Compliance data for 2024</a></li>'
  a=book(); changed=book([['NO','Example installation','123456','Permit',10,'B','A','2024','100','0','100','2020','NOT SET','OPEN']])
  s=src();s.get=Mock(side_effect=[response(idx),response(a),response(idx),response(a),response(idx),response(a),response(idx),response(a),response(idx),response(changed),response(idx),response(changed)])
  first,alerts=poll(s); self.assertEqual([],alerts)
  second,alerts=poll(s,first); self.assertEqual([],alerts)
  third,alerts=poll(s,second); self.assertEqual(1,len(alerts))
 def test_global_foreign_duplicate_is_outside_selected_scope(self):
  rows=[['NO','Norway','123456','P',10,'A','A','2024','100','0','100','2020','NOT SET','OPEN'],['FR','France','123456','P',10,'A','A','2024','100','0','100','2020','NOT SET','OPEN'],['FR','France changed','123456','P',10,'A','A','2024','101','0','100','2020','NOT SET','OPEN']]
  rows.append(list(rows[-1]));rows[-1][10]='200'
  s=src(countries=['NO']);install(s,book(rows));self.assertEqual(1,len(s.read_records()))
  s=src(countries=['FR']);install(s,book(rows))
  with self.assertRaises(SourceError):s.read_records()
 def test_header_and_second_sweep_mismatch_preserve_error(self):
  bad=book()
  # A malformed workbook is rejected before any state can be accepted.
  s=src();install(s,bad); self.assertEqual(1,len(s.read_records()))
  with self.assertRaises(SourceError):
   s=src();install(s,book(),book([['NO','Changed','123456','P',10,'A','A','2024','101','0','100','2020','NOT SET','OPEN']]));s.read_records()
 def test_nullable_fields_future_last_year_and_independent_totals(self):
  r=['NO','','123456','',10,'A','NOT APPLICABLE','','928','NOT APPLICABLE','353','2020','2030','OPEN']
  s=src();install(s,book([r]));f=s.read_records()[0]['fields'];self.assertIsNone(f['installation_name']);self.assertIsNone(f['permit_identifier']);self.assertIsNone(f['compliance_status_latest_year']);self.assertEqual('2030',f['year_of_last_emissions']);self.assertEqual('353',f['total_surrendered_allowances'])
 def test_redirect_bytes_bounds_and_invalid_configuration(self):
  for status,b,o in [(302,b'',{}),(200,b'x'*1025,{'max_bytes':1024})]:
   s=src(**o);s.get=Mock(return_value=response(b,status))
   with self.assertRaises(SourceError):s.read_records()
  for o in [{'countries':[]},{'countries':'NO'},{'activity_codes':['x']},{'events':['removed']},{'complete_snapshot':True},{'max_export_rows':True}]:
   with self.assertRaises(ValueError):src(**o)
 def test_stale_future_missing_and_ambiguous_index(self):
  s=src()
  # Read the fixture bytes from the response iterator, not a network call.
  install(s);resp=next(s.get.side_effect);valid=b''.join(resp.iter_content(65536))
  for bad in [valid.replace(b'03/10/2025',b'03/10/2026'),valid.replace(b'03/10/2025',b'03/01/2024'),valid+valid,valid.replace(b'Union Registry',b'Changed')]:
   with self.assertRaises(SourceError):s._index(bad)
 def test_workbook_headers_formula_dimension_and_archive_bounds(self):
  from test_media_database import mutate
  def change(xml,mode):
   root=E.fromstring(xml)
   if mode=='formula':E.SubElement(root.find('{%s}sheetData/{%s}row[@r="3"]/{%s}c'%(NS,NS,NS)),'{%s}f'%NS).text='1+1'
   elif mode=='dimension':root.find('{%s}dimension'%NS).set('ref','A1:N99')
   elif mode=='header':root.find('.//{%s}t'%NS).text='Changed'
   return E.tostring(root)
  for raw in [b'bad']+[mutate(book(),'xl/worksheets/sheet1.xml',lambda x,m=m:change(x,m)) for m in ['formula','dimension','header']]:
   s=src();install(s,raw)
   with self.assertRaises(SourceError):s.read_records()
  s=src(max_unpacked_bytes=10000);install(s,book([['NO','X'*3900,str(123456+i),'P',10,'A','A','2024','100','0','100','2020','NOT SET','OPEN'] for i in range(3)]))
  with self.assertRaises(SourceError):s.read_records()
 def test_mid_read_and_regression_preserve_saved_state(self):
  from watchtower.state import StateStore
  from watchtower.engine import run
  for regression in [False,True]:
   s=src();install(s);old,_=poll(s)
   if regression:old['source_state']['records']['latest_report_year']=2025;install(s)
   else:install(s,book(),book([['NO','Changed','123456','P',10,'B','A','2024','100','0','100','2020','NOT SET','OPEN']]))
   with tempfile.TemporaryDirectory() as d:
    store=StateStore(d);store.save('ets',old);result=run(Config((s.config,)),store,None,source_factory=lambda _:s);self.assertIn('ets',result.errors);self.assertEqual(old,store.load('ets'))
if __name__=='__main__':unittest.main()
