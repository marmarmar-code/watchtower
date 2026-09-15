from copy import deepcopy
import json
import unittest
from unittest.mock import Mock

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.customs_tariffs import CustomsTariffsSource
from test_change_sources import poll, response

URL='https://data.toll.no/dataset/6243277c-891e-4088-be79-0456d589d033/resource/876e3a41-7a9c-438a-8592-137547a9263c/download/tollavgiftssats.json'
def cfg(**opts):
    opts.setdefault('chapters',['04']); opts.setdefault('max_tariff_bytes',60000000)
    return SourceConfig(id='tariffs',kind='customs_tariffs',label='Tariffs',urls=(URL,),filters=FilterRule(match_all=True),options=opts)
def item(i='04011000', group='TALL', value='1,00', end=''):
    return {'id':i,'enhet':'Kg','enhetBeskrivelse':'Per kilo.','annenEnhet':None,'annenEnhetBeskrivelse':None,'avtalesatser':[{'landgruppe':group,'sats':[{'satsVerdi':value,'satsEnhet':'K','satsEnhetBeskrivelse':'Per kg','fomdato':'2026-01-01','tomdato':end}]}]}
def pay(items): return {'versjon':'1.2','varer':items}
class TariffTests(unittest.TestCase):
 def source(self,**o): return CustomsTariffsSource(cfg(**o),timeout=1,retry_attempts=1)
 def test_baseline_reorder_is_quiet_and_poll(self):
  s=self.source(); s.get=Mock(return_value=response(json.dumps(pay([item(),item('04012000',value='2,00')])).encode()))
  st,a=poll(s); self.assertEqual([],a)
  s.get.return_value=response(json.dumps(pay([item('04012000',value='2,00'),item()])).encode()); _,a=poll(s,st); self.assertEqual([],a)
 def test_rate_and_end_revision_one_alert_same_key(self):
  s=self.source(); rows=[item()]; s.get=Mock(return_value=response(json.dumps(pay(rows)).encode())); st,_=poll(s)
  rows=[item(value='2,00',end='2026-12-31')]; s.get.return_value=response(json.dumps(pay(rows)).encode()); _,a=poll(s,st)
  self.assertEqual(1,len(a)); self.assertIn('04011000',a[0].item.title)
 def test_future_period_preserved(self):
  r=self.source()._records(pay([item(end='2099-12-31')]))[0]; self.assertEqual('2099-12-31',r['fields']['rates'][0]['valid_to'])
 def test_bad_negative_date_and_reversed_rejected(self):
  for change in ({'satsVerdi':'-1,00'},{'fomdato':'bad'},{'fomdato':'2027-01-01','tomdato':'2026-01-01'}):
   x=item(); x['avtalesatser'][0]['sats'][0].update(change)
   with self.assertRaises(SourceError): self.source()._records(pay([x]))
 def test_duplicate_commodity_group_period_rejected(self):
  x=item(); x['avtalesatser'].append(x['avtalesatser'][0])
  with self.assertRaises(SourceError): self.source()._records(pay([x]))
  with self.assertRaises(SourceError): self.source()._records(pay([item(),item()]))
 def test_missing_chapter_rejected(self):
  with self.assertRaisesRegex(SourceError,'chapter'): self.source(chapters=['04','16'])._records(pay([item()]))
 def test_envelope_record_rate_bounds(self):
  with self.assertRaises(SourceError): self.source()._records({'versjon':'1.1','varer':[item()]})
  x=item(); x['avtalesatser'][0]['sats']=[]
  with self.assertRaises(SourceError): self.source()._records(pay([x]))
  with self.assertRaises(SourceError): self.source(max_records=1)._records(pay([item(),item('04012000')]))
 def test_unknown_and_null_units_preserved(self):
  x=item(); x['avtalesatser'][0]['sats'][0].update(satsEnhet='S',satsEnhetBeskrivelse='Per stk.')
  self.assertEqual('S',self.source()._records(pay([x]))[0]['fields']['rates'][0]['unit_code'])
  x=item(); x['avtalesatser'][0]['sats'][0].update(satsEnhet=None,satsEnhetBeskrivelse=None)
  self.assertIsNone(self.source()._records(pay([x]))[0]['fields']['rates'][0]['unit_code'])
 def test_redirect_and_byte_limit_rejected(self):
  s=self.source(); s.get=Mock(return_value=response(b'',status=302,headers={'Location':'https://evil.test'}))
  with self.assertRaises(SourceError): s.read_records()
  s=self.source(max_tariff_bytes=1024); s.get=Mock(return_value=response(b'x'*2048))
  with self.assertRaises(SourceError): s.read_records()
 def test_agreement_rate_order_and_numeric_format_are_quiet(self):
  x=item(); second=deepcopy(x['avtalesatser'][0]); second['landgruppe']='TGS1'; x['avtalesatser'].append(second)
  future=deepcopy(second['sats'][0]);future.update(fomdato='2030-01-01',satsVerdi='5,00');second['sats'].append(future)
  s=self.source();s.get=Mock(return_value=response(pay([x])));state,alerts=poll(s);self.assertEqual([],alerts)
  self.assertEqual(3,len(s._next['rows']['04011000']['row']['fields']['rates']))
  x['avtalesatser'].reverse();second['sats'].reverse();future['satsVerdi']='05,000'
  s.get.return_value=response(pay([x]));after,alerts=poll(s,state)
  self.assertEqual([],alerts);self.assertEqual(state,after)
  future['tomdato']='2031-12-31';s.get.return_value=response(pay([x]));after,alerts=poll(s,after)
  self.assertEqual(1,len(alerts));self.assertEqual(['04011000'],list(s._next['rows']))
  self.assertIn('2031-12-31',' '.join(alerts[0].item.alert_details))
 def test_period_conflicts_register_and_rate_caps(self):
  x=item();duplicate=deepcopy(x['avtalesatser'][0]['sats'][0]);duplicate['satsVerdi']='9,00';x['avtalesatser'][0]['sats'].append(duplicate)
  with self.assertRaisesRegex(SourceError,'conflicting'):self.source()._records(pay([x]))
  duplicate['fomdato']='2030-01-01'
  with self.assertRaisesRegex(SourceError,'bound'):self.source(max_rates_per_commodity=1)._records(pay([x]))
  with self.assertRaises(SourceError):self.source(max_register_records=1)._records(pay([item(),item('16010000')]))
  for options in ({'chapters':['00']},{'chapters':[]},{'max_bytes':5000000},{'events':['removed']},{'complete_snapshot':True},{'max_tariff_bytes':80000001}):
   with self.subTest(options=options),self.assertRaises(ValueError):self.source(**options)
 def test_transport_close_json_truncation_and_duplicate_fields(self):
  for raw,status in [(b'{',200),(b'{"versjon":"1.2","versjon":"1.2","varer":[]}',200),(b'x',302)]:
   s=self.source();reply=response(raw,status=status);s.get=Mock(return_value=reply)
   with self.assertRaises(SourceError):s.read_records()
   reply.close.assert_called_once();self.assertFalse(s.get.call_args.kwargs['allow_redirects'])
if __name__=='__main__': unittest.main()
