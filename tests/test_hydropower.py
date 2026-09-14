from copy import deepcopy
import json
from unittest import TestCase
from unittest.mock import Mock

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.hydropower import HydropowerSource
from watchtower.sources.common import SourceError
from test_change_sources import poll


def plant(ident=1,status='Idrift'):
    return {'VannKraftverkID':ident,'Navn':'Example plant','VannKVType':'Kraftverk','VannKVTypeID':'K',
            'Kraftverkstatus':status,'ErIDrift':status=='Idrift','UnderBygging':status=='Under bygging',
            'UteAvDrift':2025 if status=='Ute av drift' else None,'IDriftDato':'2000-01-01T00:00:00',
            'ElspotomraadeNummer':'1','FylkesNr':'03','KommuneNr':'1','Kommune':'Example municipality',
            'MaksYtelse':10,'MidProd_91_20':20.5,'BruttoFallhoyde_M':None,'Slukeevne':1.2,'EnEkv':0}


def response(value,status=200):
    raw=value if isinstance(value,bytes) else json.dumps(value).encode()
    return Mock(status_code=status,iter_content=Mock(return_value=[raw]),close=Mock())


def source(**options):
    return HydropowerSource(SourceConfig(id='plants',kind='hydropower',label='Plant register',filters=FilterRule(match_all=True),options=options))


def reads(s,rows,operating=None):
    s.get=Mock(side_effect=[response(rows),response([r for r in rows if r['ErIDrift']] if operating is None else operating)])


class HydropowerTests(TestCase):
    def test_quiet_repeat_order_owner_noise_and_capacity_revision(self):
        s=source();rows=[plant(),plant(2)];reads(s,rows);state,alerts=poll(s);self.assertEqual([],alerts)
        rows[0]['HovedEier']='Example holder';rows[0]['Eiere']=[{'Navn':'Example person'}];rows.reverse();reads(s,rows)
        same,alerts=poll(s,state);self.assertEqual(state,same);self.assertEqual([],alerts);self.assertNotIn('Example person',json.dumps(state))
        rows[0]['MaksYtelse']=11;reads(s,rows);state,alerts=poll(s,state);self.assertEqual(1,len(alerts))
        self.assertIn('(MW): 10 → 11',' '.join(alerts[0].item.alert_details))
        reads(s,rows);same,alerts=poll(s,state);self.assertEqual(state,same);self.assertEqual([],alerts)

    def test_construction_to_registered_operation_and_retirement(self):
        s=source();rows=[plant(),plant(2,'Under bygging')];reads(s,rows);state,_=poll(s);keys=set(state['source_state']['records']['rows'])
        rows[1]=plant(2);reads(s,rows);state,alerts=poll(s,state);self.assertEqual(1,len(alerts));self.assertEqual(keys,set(state['source_state']['records']['rows']))
        self.assertIn('Under bygging → Idrift',' '.join(alerts[0].item.alert_details))
        rows[1]=plant(2,'Ute av drift');reads(s,rows);state,alerts=poll(s,state);self.assertEqual(1,len(alerts));self.assertEqual('changed',alerts[0].item.metadata['event'])
        text=' '.join(alerts[0].item.alert_details);self.assertIn('Idrift → Ute av drift',text);self.assertIn('ikke sanntidsdrift',text)
        reads(s,[plant()]);_,alerts=poll(s,state);self.assertEqual([],alerts)

    def test_new_ids_alert_and_explicit_missing_ids_fail(self):
        s=source();reads(s,[plant()]);state,_=poll(s);reads(s,[plant(),plant(2)]);_,alerts=poll(s,state)
        self.assertEqual(1,len(alerts));self.assertEqual('added',alerts[0].item.metadata['event'])
        s=source(plant_ids=['2']);reads(s,[plant(),plant(2)]);state,_=poll(s);self.assertEqual(1,len(state['source_state']['records']['rows']))
        saved=deepcopy(state);saved_next=deepcopy(s._next);reads(s,[plant()])
        with self.assertRaisesRegex(SourceError,'explicitly selected'):poll(s,state)
        self.assertEqual(saved,state);self.assertEqual(saved_next,s._next)

    def test_negative_pump_values_null_zero_and_decimal_precision(self):
        r=plant();r.update(VannKVType='Pumpe',VannKVTypeID='P',MaksYtelse=-20,MidProd_91_20=-76.428,BruttoFallhoyde_M=-270,EnEkv=-0.972)
        s=source();reads(s,[r]);state,_=poll(s);f=state['source_state']['records']['rows']['1']['row']['fields'];self.assertEqual('-20',f['capacity_mw']);self.assertEqual('-0.972',f['energy_equivalent_kwh_m3'])
        r['BruttoFallhoyde_M']=None;reads(s,[r]);state,alerts=poll(s,state);self.assertIn('-270 → ikke oppgitt',' '.join(alerts[0].item.alert_details))
        r['BruttoFallhoyde_M']=0;reads(s,[r]);_,alerts=poll(s,state);self.assertIn('ikke oppgitt → 0',' '.join(alerts[0].item.alert_details))
        raw=json.dumps([plant()]).encode().replace(b'"MaksYtelse": 10',b'"MaksYtelse": 123456789.123456789');s.get=Mock(side_effect=[response(raw),response(raw)]);state,_=poll(s)
        self.assertEqual('123456789.123456789',state['source_state']['records']['rows']['1']['row']['fields']['capacity_mw'])

    def test_operating_set_and_payload_mismatches_are_atomic(self):
        s=source();reads(s,[plant()]);state,_=poll(s);saved=deepcopy(state);saved_next=deepcopy(s._next)
        changed=plant();changed['MaksYtelse']=11
        for operating in [[plant(2)],[plant(),plant(2)],[changed],[plant(1,'Under bygging')]]:
            reads(s,[plant()],operating)
            with self.assertRaisesRegex(SourceError,'exports disagree'):poll(s,state)
            self.assertEqual(saved,state);self.assertEqual(saved_next,s._next)
        reads(s,[plant(),plant()])
        with self.assertRaisesRegex(SourceError,'duplicated'):poll(s,state)
        self.assertEqual(saved_next,s._next)

    def test_missing_fields_bad_identity_dates_status_and_numbers(self):
        s=source()
        for field,value in [('VannKraftverkID',True),('VannKVTypeID','other'),('UnderBygging','false'),('ErIDrift',False),('UteAvDrift',2025),('IDriftDato','2000-02-30T00:00:00'),('MaksYtelse','10'),('MaksYtelse',True),('ElspotomraadeNummer','6'),('FylkesNr',3)]:
            r=plant();r[field]=value;reads(s,[r])
            with self.subTest(field=field),self.assertRaises(SourceError):s.read_records()
        r=plant();del r['MidProd_91_20'];reads(s,[r])
        with self.assertRaisesRegex(SourceError,'required'):s.read_records()
        for raw in [b'[NaN]',b'[{"id":1,"id":2}]',json.dumps([plant()]).encode().replace(b'"MaksYtelse": 10',b'"MaksYtelse": 1e1000')]:
            s.get=Mock(return_value=response(raw))
            with self.assertRaises(SourceError):s.read_records()

    def test_export_record_byte_redirect_and_configuration_limits(self):
        s=source(max_export_records=1);reads(s,[plant(),plant(2)])
        with self.assertRaisesRegex(SourceError,'max_export_records'):s.read_records()
        s=source(max_records=1);reads(s,[plant(),plant(2)])
        with self.assertRaisesRegex(SourceError,'max_records'):poll(s)
        for body in [[],{}]:
            s=source();s.get=Mock(return_value=response(body))
            with self.assertRaises(SourceError):s.read_records()
        s=source(max_bytes=1024);r=response(b'x'*1025);s.get=Mock(return_value=r)
        with self.assertRaisesRegex(SourceError,'max_bytes'):s.read_records()
        r.close.assert_called_once()
        s=source();r=response({},302);s.get=Mock(return_value=r)
        with self.assertRaisesRegex(SourceError,'redirect'):s.read_records()
        r.close.assert_called_once()
        for options in [{'plant_ids':['0']},{'plant_ids':[True]},{'complete_snapshot':True},{'max_export_records':False}]:
            with self.assertRaises(ValueError):source(**options)
