from copy import deepcopy
import json
import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

from watchtower.config import SourceConfig, FilterRule
from watchtower.engine import notification_entries
from watchtower.sources.common import SourceError
from watchtower.sources.escape_incidents import EscapeIncidentsSource, TYPES, CAVEAT
from test_change_sources import poll, response


def report(number=1):
    return {'objectid':number, 'globalid':f'{{00000000-0000-4000-8000-{number:012d}}}',
            'loknr':10000+number, 'navn':'Example site', 'rommingsdato':1789689600000,
            'rommingsdato_antatt':None, 'selskapsnavn':'Example Aquaculture AS',
            'beskrivelse':'Possible escape reported', 'art':'Laks', 'antall_romt_estimert':'10-100',
            'antall_romt_fisk':None, 'status':'Del 1', 'gjenfangst_iverksatt':'yes',
            'gjenfangst_gjennomfort':'0', 'gjenfangst_beskrivelse':None,
            'antall_romt':None, 'kapsitet_lok':1000}


def metadata():
    return {'name':'Rømming', 'objectIdField':'objectid', 'globalIdField':'globalid',
            'datesInUnknownTimezone':False, 'maxRecordCount':2000,
            'fields':[{'name':name,'type':'esriFieldType'+kind} for name,kind in TYPES.items()]}


def source(rows=None):
    rows = [report()] if rows is None else rows
    src=EscapeIncidentsSource(SourceConfig(id='escape_reports',kind='escape_incidents',filters=FilterRule(match_all=True)))
    def fetch(url,**kwargs):
        query=parse_qs(urlparse(url).query)
        if not urlparse(url).path.endswith('/query'):
            payload=metadata()
        elif 'returnCountOnly' in query:
            payload={'count':len(rows)}
        elif 'returnIdsOnly' in query:
            payload={'objectIdFieldName':'objectid','objectIds':[row['objectid'] for row in rows]}
        else:
            payload={'objectIdFieldName':'objectid','globalIdFieldName':'globalid',
                     'features':[{'attributes':row} for row in rows]}
        return response(json.dumps(payload).encode())
    src.get=Mock(side_effect=fetch)
    return src


class EscapeIncidentTests(unittest.TestCase):
    def test_complete_baseline_and_repeat_are_quiet(self):
        src=source([report(1),report(2)]);state,alerts=poll(src)
        self.assertFalse(alerts);self.assertEqual(7,src.get.call_count)
        self.assertFalse(poll(source([report(2),report(1)]),state)[1])
        self.assertEqual(2,len(state['source_state']['records']['rows']))

    def test_final_zero_is_not_unknown_or_estimated_interval(self):
        state,_=poll(source());changed=report();changed.update(antall_romt_fisk='0',status='Del 1 oppdatert')
        updated,alerts=poll(source([changed]),state)
        self.assertEqual(1,len(alerts));info=' '.join(notification_entries(alerts)[0].details)
        self.assertIn('Endelig oppgitt antall fisk: ikke oppgitt → 0',info)
        self.assertIn('Del 1 → Del 1 oppdatert',info);self.assertIn(CAVEAT,info)
        fields=next(iter(updated['source_state']['records']['rows'].values()))['row']['fields']
        self.assertEqual('10-100',fields['estimated_count']);self.assertEqual('0',fields['final_count'])
        self.assertIsNone(alerts[0].item.published)
        self.assertIn('globalid=',parse_qs(urlparse(alerts[0].item.url).query)['where'][0])
        self.assertFalse(poll(source([changed]),updated)[1])

    def test_transport_oid_and_unrelated_capacity_do_not_realert(self):
        state,_=poll(source());changed=report();changed.update(objectid=900,kapsitet_lok=5000,antall_romt=100)
        self.assertFalse(poll(source([changed]),state)[1])

    def test_absence_is_not_closure_and_reappearance_is_quiet(self):
        state,_=poll(source([report(1),report(2)]))
        reduced,alerts=poll(source([report(2)]),state)
        self.assertFalse(alerts);self.assertEqual(2,len(reduced['source_state']['records']['rows']))
        self.assertFalse(poll(source([report(1),report(2)]),reduced)[1])

    def test_new_report_is_dated_without_claiming_publication_or_confirmation(self):
        state,_=poll(source())
        for assumed,expected in [(None,'ikke oppgitt'),(1789603200000,'2026-09-17T00:00:00Z')]:
            with self.subTest(assumed=assumed):
                added=report(2);added['rommingsdato_antatt']=assumed
                _,alerts=poll(source([report(1),added]),state)
                self.assertEqual(1,len(alerts));details=notification_entries(alerts)[0].details
                self.assertEqual(8,len(details));self.assertIn(CAVEAT,details)
                self.assertTrue(all(len(line)<=500 for line in details))
                self.assertIn('Oppgitt rømmingstidspunkt (UTC): 2026-09-18T00:00:00Z',details[2])
                self.assertIn('Antatt rømmingstidspunkt (UTC): '+expected,details[2])
                self.assertIsNone(alerts[0].item.published)
                self.assertIn('mulig rømming',alerts[0].item.title)

    def test_identity_reassignment_fails_without_mutating_state(self):
        state,_=poll(source());before=deepcopy(state);changed=report()
        changed['globalid']=report(2)['globalid']
        with self.assertRaisesRegex(SourceError,'GlobalIDs'):source([changed]).fetch_with_state(state)
        self.assertEqual(before,state)
        # Two actual simultaneous reports retain their separate GlobalIDs.
        src=source([report(),changed]);changed['objectid']=2
        self.assertEqual(2,len(src.read_records()))

    def test_truncation_count_duplicate_or_unstable_rows_fail(self):
        for mode in ('truncated','count','duplicate','unstable','empty'):
            with self.subTest(mode=mode):
                src=source();original=src.get.side_effect;calls=0
                def altered(url,**kwargs):
                    nonlocal calls
                    answer=original(url,**kwargs)
                    payload=json.loads(b''.join(answer.iter_content(65536)))
                    if 'returnCountOnly' in url and mode=='count':payload['count']=2
                    if 'features' in payload:
                        calls+=1
                        if mode=='truncated':payload['exceededTransferLimit']=True
                        if mode=='duplicate':payload['features'].append(payload['features'][0])
                        if mode=='empty':payload['features']=[]
                        if mode=='unstable' and calls==2:payload['features'][0]['attributes']['antall_romt_fisk']='1'
                    return response(payload)
                src.get=Mock(side_effect=altered)
                with self.assertRaises(SourceError):src.fetch_with_state(None)

    def test_metadata_date_and_flag_contract_fail_closed(self):
        for modify in (lambda row:row.update(globalid=''),lambda row:row.update(rommingsdato=True),
                       lambda row:row.update(gjenfangst_gjennomfort='unknown'),lambda row:row.pop('antall_romt_fisk')):
            row=report();modify(row)
            with self.assertRaises(SourceError):source()._record(row)
        src=source();data=metadata();data['datesInUnknownTimezone']=True
        src.get=Mock(return_value=response(data))
        with self.assertRaises(SourceError):src.read_records()

    def test_many_changes_and_long_description_remain_useful_within_notification_bounds(self):
        original=report();original['beskrivelse']='same context '*65+'old ending'
        state,_=poll(source([original]));changed=deepcopy(original)
        changed.update(status='Del 2',antall_romt_fisk='42',antall_romt_estimert='100-1000',
            gjenfangst_iverksatt='no',gjenfangst_gjennomfort='1',gjenfangst_beskrivelse='Example net deployed',
            beskrivelse='same context '*65+'corrected ending',selskapsnavn='Example revised company')
        updated,alerts=poll(source([changed]),state);details=notification_entries(alerts)[0].details
        self.assertEqual(8,len(details));self.assertTrue(all(len(line)<=500 for line in details))
        self.assertIn(CAVEAT,details);self.assertIn('flere feltendringer',' '.join(details))
        self.assertIn('ikke oppgitt → 42',' '.join(details))
        fields=next(iter(updated['source_state']['records']['rows'].values()))['row']['fields']
        self.assertEqual(changed['beskrivelse'],fields['description'])
        changed['beskrivelse']='same context '*65+'another correction'
        _,alerts=poll(source([changed]),updated)
        details=' '.join(notification_entries(alerts)[0].details)
        self.assertIn('corrected ending',details);self.assertIn('another correction',details)


if __name__=='__main__':unittest.main()
