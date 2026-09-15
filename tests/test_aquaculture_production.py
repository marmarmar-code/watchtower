import csv,io,unittest
from datetime import date
from unittest.mock import Mock,patch
from watchtower.config import SourceConfig,FilterRule
from watchtower.sources.aquaculture_production import AquacultureProductionSource,HEAD,MONTHS
from watchtower.sources.common import SourceError
from test_change_sources import response,poll
class T(unittest.TestCase):
 def cfg(self,**o):return SourceConfig(id='aqua',kind='aquaculture_production',label='Aqua',urls=(),filters=FilterRule(match_all=True),options={'events':['added','changed'],'latest_months':1,**o})
 def row(self,month=7,cohort='2024',**kw):
  r={k:'1' for k in HEAD};r.update({'ÅR':'2026','MÅNED_KODE':str(month),'MÅNED':MONTHS[month-1],'FYLKE':'Nordland','ARTSID':'LAKS','UTSETTSÅR':cohort});r.update(kw);return r
 def csv(self,rows,extra=False):
  h=HEAD+(['EXTRA'] if extra else []);b=io.StringIO();w=csv.DictWriter(b,fieldnames=h,delimiter=';');w.writeheader();w.writerows(rows);return b.getvalue().encode()
 def src(self,b,**o):
  s=AquacultureProductionSource(self.cfg(**o));s.max_bytes=100000;s.get=Mock(return_value=response(b));return s
 def test_baseline_repeat(self):
  s=self.src(self.csv([self.row()]),latest_months=1)
  with patch('watchtower.sources.aquaculture_production.today',return_value=date(2026,9,15)):
   st,a=poll(s);self.assertFalse(a);_,a=poll(s,st);self.assertFalse(a)
 def test_changed_numeric_once(self):
  s=self.src(self.csv([self.row()]),latest_months=1)
  with patch('watchtower.sources.aquaculture_production.today',return_value=date(2026,9,15)):
   st,_=poll(s);s.get.return_value=response(self.csv([self.row(BEHFISK_STK='2')]));_,a=poll(s,st);self.assertEqual(1,len(a))
 def test_negative_and_blank_cohort(self):
  s=self.src(self.csv([self.row(cohort='',TELLEFEIL_STK='-2')]),latest_months=1)
  with patch('watchtower.sources.aquaculture_production.today',return_value=date(2026,9,15)):r=s.read_records();self.assertIsNone(r[0]['fields']['UTSETTSÅR'])
 def test_invalid_metric(self):
  s=self.src(self.csv([self.row(BIOMASSE_KG='x')]));
  with patch('watchtower.sources.aquaculture_production.today',return_value=date(2026,9,15)),self.assertRaises(SourceError):s.read_records()
 def test_bad_month_label(self):
  s=self.src(self.csv([self.row(MÅNED='MAI')]));
  with patch('watchtower.sources.aquaculture_production.today',return_value=date(2026,9,15)),self.assertRaises(SourceError):s.read_records()
 def test_future_and_stale(self):
  for opts,row in [({},self.row(month=10,MÅNED='OKTOBER')),({'max_age_days':1},self.row(month=1,MÅNED='JANUAR'))]:
   s=self.src(self.csv([row]),**opts)
   with patch('watchtower.sources.aquaculture_production.today',return_value=date(2026,9,15)),self.assertRaises(SourceError):s.read_records()
 def test_duplicate_identity(self):
  s=self.src(self.csv([self.row(),self.row()]));
  with patch('watchtower.sources.aquaculture_production.today',return_value=date(2026,9,15)),self.assertRaises(SourceError):s.read_records()
 def test_extra_header(self):
  s=self.src(self.csv([self.row()],extra=True));
  with patch('watchtower.sources.aquaculture_production.today',return_value=date(2026,9,15)),self.assertRaises(SourceError):s.read_records()
 def test_invalid_filters(self):
  with self.assertRaises(ValueError):AquacultureProductionSource(self.cfg(counties='Nordland'))
 def test_no_removal(self):
  with self.assertRaises(ValueError):AquacultureProductionSource(self.cfg(events=['removed']))
 def test_full_read_change_outside_selected_window(self):
  a=self.csv([self.row(month=6),self.row()]);b=self.csv([self.row(month=6,BIOMASSE_KG='2'),self.row()]);s=self.src(a)
  s.get=Mock(side_effect=[response(a),response(b)])
  with patch('watchtower.sources.aquaculture_production.today',return_value=date(2026,9,15)),self.assertRaisesRegex(SourceError,'full export'):s.read_records()
 def test_gap_and_rolling_periods_no_removal(self):
  s=self.src(self.csv([self.row(month=5),self.row()]),latest_months=2)
  with patch('watchtower.sources.aquaculture_production.today',return_value=date(2026,9,15)):
   with self.assertRaisesRegex(SourceError,'gap'):s.read_records()
   s=self.src(self.csv([self.row(month=6),self.row()]),latest_months=2);old,_=poll(s)
   s.get=Mock(return_value=response(self.csv([self.row(),self.row(month=8)])));new,alerts=poll(s,old)
   self.assertEqual(1,len(alerts));self.assertEqual('added',alerts[0].item.metadata['event']);self.assertTrue(set(old['seen'])<=set(new['seen']))
 def test_latest_period_regression_preserves_saved_state(self):
  import tempfile
  from watchtower.config import Config
  from watchtower.engine import run
  from watchtower.state import StateStore
  s=self.src(self.csv([self.row()]))
  with patch('watchtower.sources.aquaculture_production.today',return_value=date(2026,9,15)):
   old,_=poll(s);s.get=Mock(return_value=response(self.csv([self.row(month=6)])))
   with tempfile.TemporaryDirectory() as d:
    store=StateStore(d);store.save('aqua',old);result=run(Config((s.config,)),store,None,source_factory=lambda _:s)
    self.assertIn('regressed',result.errors['aqua']);self.assertEqual(old,store.load('aqua'))
 def test_missing_metric_duplicate_header_extra_cell_blank_row(self):
  good=self.csv([self.row(),self.row(cohort='2023')])
  bads=[self.csv([self.row(BIOMASSE_KG='')]),good.replace(b'BIOMASSE_KG',b'BEHFISK_STK',1),good.replace(b'2026;7;',b'x;2026;7;',1),good.replace(b'\r\n2026',b'\r\n\r\n2026',1)]
  for raw in bads:
   with self.subTest(raw=raw[:50]),patch('watchtower.sources.aquaculture_production.today',return_value=date(2026,9,15)),self.assertRaises(SourceError):self.src(raw).read_records()
 def test_cohort_and_numeric_format(self):
  with patch('watchtower.sources.aquaculture_production.today',return_value=date(2026,9,15)):
   for bad in [self.row(cohort='2028'),self.row(BEHFISK_STK='1.5'),self.row(BIOMASSE_KG='NaN'),self.row(BIOMASSE_KG='1e99')]:
    with self.assertRaises(SourceError):self.src(self.csv([bad])).read_records()
   r=self.src(self.csv([self.row(cohort='2027',ANDRE_NY_STK='-2',BIOMASSE_KG='12.50')])).read_records()[0]['fields']
   self.assertEqual('2027',r['UTSETTSÅR']);self.assertEqual('-2',r['ANDRE_NY_STK']);self.assertEqual('12.50',r['BIOMASSE_KG'])
if __name__=='__main__':unittest.main()
