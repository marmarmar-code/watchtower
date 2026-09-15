from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from watchtower.config import Config, FilterRule, SourceConfig
from watchtower.engine import run
from watchtower.sources.common import SourceError
from watchtower.sources.short_positions import ShortPositionsSource, DATA_URL
from watchtower.state import StateStore
from test_change_sources import poll,response

A='NO0000000001'
B='NO0000000002'


def position(**changes):
    p={'date':'2026-09-01T00:00:00','shortPercent':0.6,'shares':600,'positionHolder':'Example Fund'}
    return {**p,**changes}


def event(**changes):
    e={'date':'2026-09-02T00:00:00','shortPercent':0.6,'shares':600,'activePositions':[position()]}
    return {**e,**changes}


def instrument(isin=A,**changes):
    return {'isin':isin,'issuerName':'Example Issuer','events':[event()],**changes}


def source(**options):
    return ShortPositionsSource(SourceConfig(id='short-positions',kind='short_positions',label='Short positions',urls=(),
        filters=FilterRule(match_all=True),options={'isins':[A],**options}))


def install(src,first,second=None):
    src.get=Mock(side_effect=[response(first),response(first if second is None else second)])


class ShortPositionTests(unittest.TestCase):
    def test_baseline_then_same_snapshot_is_quiet(self):
        src=source();install(src,[instrument()]);previous,alerts=poll(src)
        self.assertEqual([],alerts);self.assertEqual(1,len(previous['seen']))
        install(src,[instrument()]);repeat,alerts=poll(src,previous)
        self.assertEqual([],alerts);self.assertEqual(previous,repeat)
        self.assertEqual(DATA_URL,src.get.call_args.args[0])
        self.assertFalse(src.get.call_args.kwargs['allow_redirects'])

    def test_latest_event_and_holder_change_alert_once_with_separate_dates(self):
        src=source();install(src,[instrument()]);previous,_=poll(src)
        current=event(date='2026-09-03T00:00:00',shortPercent=0.7,shares=701,
                      activePositions=[position(date='2026-09-02T00:00:00',shortPercent=0.7,shares=700)])
        install(src,[instrument(events=[event(),current])]);after,alerts=poll(src,previous)
        self.assertEqual(1,len(alerts));self.assertEqual('changed',alerts[0].item.metadata['event'])
        detail=' '.join(alerts[0].item.alert_details)
        for value in ('2026-09-03','2026-09-02','0.7 %','701 aksjer','700 aksjer','EXAMPLE FUND'):
            self.assertIn(value,detail)
        install(src,[instrument(events=[current,event()])]);repeat,alerts=poll(src,after)
        self.assertEqual([],alerts);self.assertEqual(after,repeat)

    def test_holder_change_is_detected_even_when_aggregate_is_unchanged(self):
        src=source();install(src,[instrument()]);previous,_=poll(src)
        install(src,[instrument(events=[event(activePositions=[position(positionHolder='Other Fund')])])])
        _,alerts=poll(src,previous);self.assertEqual(1,len(alerts));self.assertIn('OTHER FUND',' '.join(alerts[0].item.alert_details))

    def test_source_aggregate_rounding_difference_is_preserved(self):
        src=source();install(src,[instrument(events=[event(shares=601)])])
        row=src.read_records()[0]
        self.assertEqual(601,row['fields']['short_shares']);self.assertEqual(600,row['fields']['active_positions'][0]['shares'])

    def test_empty_public_positions_are_explicit_zero_not_closure(self):
        src=source();install(src,[instrument()]);previous,_=poll(src)
        install(src,[instrument(events=[event(date='2026-09-03',shortPercent=0,shares=0,activePositions=[])])])
        state,alerts=poll(src,previous);self.assertEqual(1,len(alerts))
        f=next(iter(state['source_state']['records']['rows'].values()))['row']['fields']
        self.assertEqual('0',f['short_percent']);self.assertEqual([],f['active_positions'])
        self.assertIn('beviser ikke at en posisjon er lukket',' '.join(alerts[0].item.alert_details))

    def test_position_case_whitespace_and_order_do_not_create_changes(self):
        p=position(positionHolder='Other Fund',shares=500,shortPercent=0.5)
        src=source();e=event(shortPercent=1.1,shares=1100,activePositions=[position(),p])
        install(src,[instrument(events=[e])]);state,_=poll(src)
        changed=deepcopy(e);changed['activePositions']=[p,position(positionHolder='  EXAMPLE   FUND ')]
        install(src,[instrument(events=[changed])]);repeat,alerts=poll(src,state)
        self.assertEqual([],alerts);self.assertEqual(state,repeat)

    def test_missing_selected_isin_is_error_without_state_loss(self):
        src=source();install(src,[instrument()]);previous,_=poll(src)
        install(src,[instrument(B)])
        with tempfile.TemporaryDirectory() as directory:
            store=StateStore(directory);store.save(src.config.id,previous)
            result=run(Config((src.config,)),store,None,source_factory=lambda _:src)
            self.assertIn(src.config.id,result.errors);self.assertEqual(previous,store.load(src.config.id))

    def test_latest_date_regression_fails_closed(self):
        src=source();install(src,[instrument()]);previous,_=poll(src);saved=deepcopy(previous)
        install(src,[instrument(events=[event(date='2026-09-01')])])
        with self.assertRaisesRegex(SourceError,'regressed'):poll(src,previous)
        self.assertEqual(saved,previous)

    def test_changes_between_reads_are_rejected(self):
        src=source();install(src,[instrument()],[instrument(events=[event(shares=601)])])
        with self.assertRaisesRegex(SourceError,'between complete reads'):src.read_records()

    def test_only_latest_selected_snapshot_monitored_but_all_rows_validated(self):
        src=source();first=[instrument(),instrument(B)]
        second=[instrument(B,events=[event(shares=601)]),instrument()]
        install(src,first,second);self.assertEqual(1,len(src.read_records()))
        bad=instrument(B);bad['events'][0]['activePositions'][0]['shares']=-1
        install(src,[instrument(),bad])
        with self.assertRaises(SourceError):src.read_records()
        old=event(date='2026-08-01',activePositions=[position(date='2026-07-31')]);changed=deepcopy(old);changed['shares']=602
        install(src,[instrument(events=[old,event()])],[instrument(events=[event(),changed])])
        self.assertEqual('2026-09-02',src.read_records()[0]['fields']['event_date'])

    def test_duplicate_instruments_dates_and_holders_are_rejected(self):
        cases=[[instrument(),instrument()], [instrument(events=[event(),event(date='2026-09-02')])],
               [instrument(events=[event(activePositions=[position(),position(positionHolder=' EXAMPLE FUND ')])])]]
        for records in cases:
            src=source();install(src,records)
            with self.subTest(records=records),self.assertRaises(SourceError):src.read_records()

    def test_invalid_numeric_and_date_values_rejected(self):
        events=[event(shares=True),event(shares=-1),event(shares=1.5),event(shortPercent='0.6'),event(shortPercent=True),
                event(shortPercent=float('nan')),event(shortPercent=float('inf')),event(shortPercent=-1),
                event(date='2026-02-30'),event(date='2026-09-02T12:00:00'),event(date='2026-09-02T00:00:00Z'),
                event(activePositions=[position(date='2026-09-03')]),event(activePositions=[position(shortPercent=0.49)]),
                event(activePositions=[position(shares=0)]),event(activePositions=[position(positionHolder='')]),
                event(activePositions=[])]
        for e in events:
            src=source();install(src,[instrument(events=[e])])
            with self.subTest(event=e),self.assertRaises(SourceError):src.read_records()

    def test_schema_and_response_bounds_are_rejected(self):
        bads=[[],{},[instrument(events=[])],[instrument(isin='bad')],[instrument(extra=1)],
              [instrument(events=[event(extra=1)])],[instrument(events=[event(activePositions=[position(extra=1)])])]]
        for bad in bads:
            src=source();install(src,bad)
            with self.subTest(bad=bad),self.assertRaises(SourceError):src.read_records()
        for opts,records in [({'max_instruments':1},[instrument(),instrument(B)]),
                             ({'max_history_events':1},[instrument(),instrument(B)]),
                             ({'max_positions_per_event':1},[instrument(events=[event(activePositions=[position(),position(positionHolder='Other')])])]),
                             ({'max_records':1,'isins':[A,B]},[instrument(),instrument(B)])]:
            src=source(**opts);install(src,records)
            with self.subTest(opts=opts),self.assertRaises(SourceError):poll(src)

    def test_transport_errors_close_response_and_duplicate_json_keys_fail(self):
        for raw,status in [(b'x'*2048,200),(b'[]',302),(b'invalid',200),(b'[{"isin":"x","isin":"y"}]',200)]:
            src=source(max_bytes=1024);r=response(raw,status=status);src.get=Mock(return_value=r)
            with self.assertRaises(SourceError):src.read_records()
            r.close.assert_called_once()

    def test_excessive_decimal_exponent_is_rejected_before_rendering(self):
        from decimal import Decimal
        from watchtower.sources.short_positions import percent
        for value in ('1e-1000000000','0e-1000000000'):
            with self.subTest(value=value),self.assertRaises(SourceError):percent(Decimal(value))

    def test_configuration_is_bounded_and_disallows_removals(self):
        for options in [{'isins':[]},{'isins':['no0000000001']},{'isins':['bad']},{'max_history_events':True},
                        {'complete_snapshot':True},{'events':['removed']}]:
            with self.subTest(options=options),self.assertRaises(ValueError):source(**options)


if __name__=='__main__':unittest.main()
