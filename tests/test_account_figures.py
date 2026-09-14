from copy import deepcopy
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import Mock

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.account_figures import AccountFiguresSource, API
from watchtower.sources.common import SourceError
from test_change_sources import poll

ORG = '923609016'


def account(org=ORG, year=None):
    year=year or datetime.now(timezone.utc).year-1
    return {'id':100,'journalnr':'2026000100','regnskapstype':'SELSKAP','virksomhet':{'organisasjonsnummer':org},
            'regnskapsperiode':{'fraDato':f'{year}-01-01','tilDato':f'{year}-12-31'},'valuta':'USD',
            'oppstillingsplan':'store','avviklingsregnskap':False,
            'eiendeler':{'sumEiendeler':100},'egenkapitalGjeld':{'egenkapital':{'sumEgenkapital':25},'sumEgenkapitalGjeld':100},
            'resultatregnskapResultat':{'aarsresultat':-2,'driftsresultat':{'driftsresultat':5,'driftsinntekter':{'sumDriftsinntekter':50}}}}


def response(value,status=200):
    raw=value if isinstance(value,bytes) else json.dumps(value).encode()
    return Mock(status_code=status,iter_content=Mock(return_value=[raw]),close=Mock())


def source(**options):
    return AccountFiguresSource(SourceConfig(id='accounts',kind='account_figures',label='Account figures',
        filters=FilterRule(match_all=True),options={'companies':[ORG],**options}))


class AccountFiguresTests(unittest.TestCase):
    def test_quiet_repeat_metadata_noise_and_changed_figures(self):
        s=source();r=account();s.get=Mock(return_value=response([r]));state,alerts=poll(s);self.assertEqual([],alerts)
        r['virksomhet']['morselskap']=True;r['revisjon']={'fravalgRevisjon':True}
        s.get.return_value=response([r]);same,alerts=poll(s,state);self.assertEqual(state,same);self.assertEqual([],alerts)
        r['resultatregnskapResultat']['aarsresultat']=-3;s.get.return_value=response([r]);state,alerts=poll(s,state)
        self.assertEqual(1,len(alerts));self.assertIn('-2 → -3',' '.join(alerts[0].item.alert_details))
        s.get.return_value=response([r]);same,alerts=poll(s,state);self.assertEqual(state,same);self.assertEqual([],alerts)

    def test_replacement_id_same_period_and_new_period_are_distinguished(self):
        s=source();r=account(year=datetime.now(timezone.utc).year-2);s.get=Mock(return_value=response([r]));state,_=poll(s)
        key=next(iter(state['source_state']['records']['rows']));r['id']=101;r['journalnr']='2026000200'
        s.get.return_value=response([r]);state,alerts=poll(s,state);self.assertEqual(1,len(alerts))
        self.assertEqual('changed',alerts[0].item.metadata['event']);self.assertEqual([key],list(state['source_state']['records']['rows']))
        r=account();s.get.return_value=response([r]);state,alerts=poll(s,state)
        self.assertEqual(1,len(alerts));self.assertEqual('added',alerts[0].item.metadata['event'])
        self.assertNotIn(key,state['source_state']['records']['rows'])

    def test_currency_change_does_not_convert_and_missing_is_not_zero(self):
        s=source();r=account();s.get=Mock(return_value=response([r]));state,_=poll(s)
        r['valuta']='NOK';r['resultatregnskapResultat'].pop('aarsresultat');s.get.return_value=response([r]);state,alerts=poll(s,state)
        text=' '.join(alerts[0].item.alert_details);self.assertIn('USD → NOK',text);self.assertIn('-2 → ikke oppgitt',text)
        fields=next(iter(state['source_state']['records']['rows'].values()))['row']['fields'];self.assertIsNone(fields['annual_result']);self.assertEqual('100',fields['assets'])
        r['resultatregnskapResultat']['aarsresultat']=0;s.get.return_value=response([r]);_,alerts=poll(s,state)
        self.assertIn('ikke oppgitt → 0',' '.join(alerts[0].item.alert_details))

    def test_decimal_precision_notation_and_negative_zero(self):
        s=source();raw=json.dumps([account()]).encode().replace(b'"sumEiendeler": 100',b'"sumEiendeler": 12345678901234567890.123456')
        s.get=Mock(return_value=response(raw));state,_=poll(s)
        fields=next(iter(state['source_state']['records']['rows'].values()))['row']['fields']
        self.assertEqual('12345678901234567890.123456',fields['assets'])
        raw=raw.replace(b'12345678901234567890.123456',b'12345678901234567890123456e-6')
        s.get.return_value=response(raw);same,alerts=poll(s,state);self.assertEqual(state,same);self.assertEqual([],alerts)
        raw=raw.replace(b'12345678901234567890123456e-6',b'-0.00');s.get.return_value=response(raw)
        self.assertEqual('0',s.read_records()[0]['fields']['assets'])

    def test_period_regression_preserves_snapshot_and_multi_company_failure_is_atomic(self):
        s=source();s.get=Mock(return_value=response([account()]));state,_=poll(s);saved=deepcopy(state);next_saved=deepcopy(s._next)
        s.get.return_value=response([account(year=2000)])
        with self.assertRaisesRegex(SourceError,'regressed'):poll(s,state)
        self.assertEqual(saved,state);self.assertEqual(next_saved,s._next)
        other='976967631';s=source(companies=[ORG,other]);s.get=Mock(side_effect=[response([account()]),response([account(other)])]);state,_=poll(s)
        saved=deepcopy(state);next_saved=deepcopy(s._next);r=account();r['id']=200
        s.get=Mock(side_effect=[response([r]),response(b'{')])
        with self.assertRaises(SourceError):poll(s,state)
        self.assertEqual(saved,state);self.assertEqual(next_saved,s._next)

    def test_ambiguous_scope_invalid_identity_dates_and_statement_shapes(self):
        s=source()
        for body in [[],[account(),account()],account()]:
            s.get=Mock(return_value=response(body))
            with self.assertRaises(SourceError):s.read_records()
        for field,value in [('id',True),('regnskapstype','KONSERN'),('valuta',''),('journalnr',None),
                            ('eiendeler',None),('egenkapitalGjeld',{}),('resultatregnskapResultat',{}),('avviklingsregnskap','false')]:
            r=account();r[field]=value;s.get=Mock(return_value=response([r]))
            with self.subTest(field=field),self.assertRaises(SourceError):s.read_records()
        r=account('976967631');s.get=Mock(return_value=response([r]))
        with self.assertRaises(SourceError):s.read_records()
        for dates in [('2025-02-30','2025-12-31'),('2025-12-31','2025-01-01'),('2999-01-01','2999-12-31')]:
            r=account();r['regnskapsperiode']=dict(zip(['fraDato','tilDato'],dates));s.get=Mock(return_value=response([r]))
            with self.assertRaises(SourceError):s.read_records()

    def test_invalid_numbers_duplicate_json_limits_and_redirects(self):
        for replacement in [b'"100"',b'true',b'NaN',b'Infinity',b'1e1000000',b'1e-30']:
            raw=json.dumps([account()]).encode().replace(b'"sumEiendeler": 100',b'"sumEiendeler": '+replacement)
            s=source();s.get=Mock(return_value=response(raw))
            with self.subTest(value=replacement),self.assertRaises(SourceError):s.read_records()
        s=source();s.get=Mock(return_value=response(b'[{"id":1,"id":2}]'))
        with self.assertRaises(SourceError):s.read_records()
        s=source(max_bytes=1024);reply=response(b'x'*1025);s.get=Mock(return_value=reply)
        with self.assertRaisesRegex(SourceError,'max_bytes'):s.read_records()
        reply.close.assert_called_once()
        s=source();reply=response({},302);s.get=Mock(return_value=reply)
        with self.assertRaisesRegex(SourceError,'redirect'):s.read_records()
        reply.close.assert_called_once()
        for options in [{'companies':['123']},{'companies':[]},{'complete_snapshot':True},{'thresholds':{'assets':{'absolute':10}}}]:
            with self.assertRaises(ValueError):source(**options)


if __name__=='__main__':unittest.main()
