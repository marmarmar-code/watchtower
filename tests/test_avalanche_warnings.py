from copy import deepcopy
from datetime import date, timedelta
import json
from unittest import TestCase
from unittest.mock import Mock, patch

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.avalanche_warnings import AvalancheWarningsSource, TEXT_FIELDS, TIME_FIELDS, INT_FIELDS, LIST_FIELDS
from watchtower.sources.common import SourceError
from test_change_sources import poll

DAY = date(2026, 3, 1)


def warning(day=DAY, region=3028, assessed=True):
    r={name:None for name in TEXT_FIELDS+TIME_FIELDS+INT_FIELDS+LIST_FIELDS}
    r.update(RegId=1 if assessed else 0,RegionId=region,RegionName='Example Region',RegionTypeId=10,RegionTypeName='A',
             LangKey=1,DangerLevel='3' if assessed else '0',IsTendency=False,ValidFrom=f'{day}T00:00:00',ValidTo=f'{day}T23:59:59',
             PublishTime=f'{day}T00:00:00',NextWarningTime=f'{day}T17:00:00',MainText='Example main message' if assessed else 'Ikke vurdert',
             AvalancheDanger='Example assessment' if assessed else None,AvalancheProblems=[{'AvalancheProblemId':1,'DangerLevel':3}] if assessed else None,
             AvalancheAdvices=[] if assessed else None,MountainWeather={} if assessed else None)
    return r


def response(value,status=200):
    raw=value if isinstance(value,bytes) else json.dumps(value).encode()
    return Mock(status_code=status,iter_content=Mock(return_value=[raw]),close=Mock())


def source(**options):
    return AvalancheWarningsSource(SourceConfig(id='warnings',kind='avalanche_warnings',label='Regional warning notices',
        filters=FilterRule(match_all=True),options={'regions':['3028'],'lookback_days':0,'forecast_days':0,**options}))


@patch('watchtower.sources.avalanche_warnings.today',return_value=DAY)
class AvalancheWarningTests(TestCase):
    def test_quiet_repeat_author_whitespace_and_administrative_order(self,_):
        s=source();r=warning();r['CountyList']=[{'Id':'1'},{'Id':'2'}];s.get=Mock(return_value=response([r]));state,alerts=poll(s);self.assertEqual([],alerts)
        r['Author']='Example author';r['MainText']=' Example   main message ';r['CountyList'].reverse();s.get.return_value=response([r])
        same,alerts=poll(s,state);self.assertEqual(state,same);self.assertEqual([],alerts)
        self.assertNotIn('Example author',json.dumps(state))

    def test_problem_content_order_and_source_replacement_are_changes(self,_):
        s=source();r=warning();s.get=Mock(return_value=response([r]));state,_=poll(s);key=next(iter(state['source_state']['records']['rows']))
        for change in [('AvalancheProblems',[{'AvalancheProblemId':1,'DangerLevel':4}]),('MainText','Changed main message'),('RegId',2)]:
            r[change[0]]=change[1];s.get.return_value=response([r]);state,alerts=poll(s,state)
            self.assertEqual(1,len(alerts));self.assertEqual('changed',alerts[0].item.metadata['event'])
            text=' '.join(alerts[0].item.alert_details);self.assertIn('les hele varselet',text);self.assertNotIn('Changed main message',text)
            self.assertEqual([key],list(state['source_state']['records']['rows']))
        r['AvalancheProblems']=[{'AvalancheProblemId':1},{'AvalancheProblemId':2}];s.get.return_value=response([r]);state,_=poll(s,state)
        r['AvalancheProblems'].reverse();s.get.return_value=response([r]);_,alerts=poll(s,state);self.assertEqual(1,len(alerts))

    def test_unassessed_placeholder_clock_noise_and_assessment_transition(self,_):
        s=source();r=warning(assessed=False);s.get=Mock(return_value=response([r]));state,_=poll(s)
        fields=next(iter(state['source_state']['records']['rows'].values()))['row']['fields'];self.assertIsNone(fields['danger_level'])
        r['NextWarningTime']='2026-03-02T17:00:00';s.get.return_value=response([r]);same,alerts=poll(s,state);self.assertEqual(state,same);self.assertEqual([],alerts)
        s.get.return_value=response([warning()]);state,alerts=poll(s,state);self.assertEqual(1,len(alerts))
        s.get.return_value=response([warning(assessed=False)]);state,alerts=poll(s,state);self.assertEqual(1,len(alerts));self.assertIn('ikke er vurdert',alerts[0].item.alert_details[0])
        same,alerts=poll(s,state);self.assertEqual(state,same);self.assertEqual([],alerts)

    def test_rolling_window_new_placeholders_quiet_new_assessments_alert(self,clock):
        s=source();s.get=Mock(return_value=response([warning()]));state,_=poll(s)
        clock.return_value=DAY+timedelta(days=1);s.get.return_value=response([warning(day=clock.return_value,assessed=False)]);state,alerts=poll(s,state);self.assertEqual([],alerts)
        clock.return_value=DAY+timedelta(days=2);s.get.return_value=response([warning(day=clock.return_value)]);_,alerts=poll(s,state);self.assertEqual(1,len(alerts));self.assertEqual('added',alerts[0].item.metadata['event'])

    def test_tendency_remains_explicit_and_missing_day_preserves_state(self,_):
        s=source();r=warning();s.get=Mock(return_value=response([r]));state,_=poll(s)
        r.update(IsTendency=True,DangerLevel='0',AvalancheDanger=None,AvalancheProblems=None,AvalancheAdvices=None,MountainWeather=None)
        s.get.return_value=response([r]);state,alerts=poll(s,state);self.assertEqual(1,len(alerts))
        fields=next(iter(state['source_state']['records']['rows'].values()))['row']['fields'];self.assertEqual('tendency',fields['assessment']);self.assertIsNone(fields['danger_level'])
        saved=deepcopy(state);next_saved=deepcopy(s._next);s.get.return_value=response([])
        with self.assertRaisesRegex(SourceError,'incomplete'):poll(s,state)
        self.assertEqual(saved,state);self.assertEqual(next_saved,s._next)

    def test_partial_duplicate_wrong_region_language_or_dates_fail_atomically(self,_):
        s=source(regions=['3028','3010']);s.get=Mock(side_effect=[response([warning(region=3010)]),response([warning()])]);state,_=poll(s);saved=deepcopy(state);next_saved=deepcopy(s._next)
        for body in [[],[warning(),warning()],{},[warning(region=3010)]]:
            r=warning(region=3010);r['RegId']=2;s.get=Mock(side_effect=[response([r]),response(body)])
            with self.assertRaises(SourceError):poll(s,state)
            self.assertEqual(saved,state);self.assertEqual(next_saved,s._next)
        s=source(forecast_days=1);s.get=Mock(return_value=response([warning(),warning()]))
        with self.assertRaisesRegex(SourceError,'duplicated'):s.read_records()
        for key,value in [('LangKey',2),('RegionId',True),('ValidFrom','2026-02-30T00:00:00'),('ValidTo','2026-03-02T23:59:59'),('ValidFrom','2026-03-01T00:00:00Z'),('DangerLevel','6'),('RegId',True),('IsTendency','false'),('MainText',''),('AvalancheProblems',None)]:
            r=warning();r[key]=value;s=source();s.get=Mock(return_value=response([r]))
            with self.subTest(key=key),self.assertRaises(SourceError):s.read_records()

    def test_schema_transport_and_configuration_limits(self,_):
        for body in [b'[{"RegId":1,"RegId":2}]',b'[NaN]',b'not json']:
            s=source();s.get=Mock(return_value=response(body))
            with self.assertRaises(SourceError):s.read_records()
        r=warning();del r['SnowSurface'];s=source();s.get=Mock(return_value=response([r]))
        with self.assertRaisesRegex(SourceError,'missing'):s.read_records()
        r=warning(assessed=False);r['DangerLevel']='1';s.get.return_value=response([r])
        with self.assertRaisesRegex(SourceError,'contradicts'):s.read_records()
        r=warning();r['MainText']='x'*50001;s.get.return_value=response([r])
        with self.assertRaisesRegex(SourceError,'bounds'):s.read_records()
        s=source(max_bytes=1024);r=response(b'x'*1025);s.get=Mock(return_value=r)
        with self.assertRaisesRegex(SourceError,'max_bytes'):s.read_records()
        r.close.assert_called_once()
        s=source();r=response({},302);s.get=Mock(return_value=r)
        with self.assertRaisesRegex(SourceError,'redirect'):s.read_records()
        r.close.assert_called_once()
        for options in [{'regions':[]},{'regions':['bad']},{'lookback_days':8},{'forecast_days':3},{'complete_snapshot':True},{'thresholds':{'danger_level':{'absolute':1}}}]:
            with self.assertRaises(ValueError):source(**options)
