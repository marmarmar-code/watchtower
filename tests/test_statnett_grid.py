import unittest
from unittest.mock import Mock

from watchtower.config import FilterRule, SourceConfig
from watchtower.engine import notification_entries
from watchtower.sources.common import SourceError
from watchtower.sources.powerbi_public import flat_table
from watchtower.sources.statnett_grid import StatnettGridSource, date
from test_change_sources import poll


def source():
    return StatnettGridSource(SourceConfig(id='grid',kind='statnett_grid',label='Grid',
        filters=FilterRule(match_all=True),options={'max_records':3000}))


def row(case='26/00001', capacity=20, planned=1893456000000):
    return [case,'REF1','Example station','Example region','NO1','Example Grid AS',
            'Example Project AS','Datasenter',capacity,1767225600000,planned]


def installed(s, rows):
    s._poll=Mock(return_value=(rows,{'test':len(rows)},'2026-09-18T10:00:00+00:00'))


class GridTests(unittest.TestCase):
    def test_quiet_initial_repeat_and_distinct_simultaneous_categories(self):
        s=source();rows=[('queue','Forbruk',row()),('reservation','Forbruk',row(capacity=10))]
        installed(s,rows);a,alerts=poll(s)
        self.assertFalse(alerts);self.assertEqual(1,len(a['source_state']['records']['rows']))
        self.assertEqual(2,len(a['source_state']['records']['rows']['26/00001']['row']['fields']['observations']))
        installed(s,list(reversed(rows)));b,alerts=poll(s,a)
        self.assertFalse(alerts);self.assertEqual(a,b)

    def test_capacity_date_and_customer_revision_explained_once(self):
        s=source();installed(s,[('reservation','Forbruk',row())]);a,_=poll(s)
        changed=row(capacity=35,planned=1924992000000);changed[6]='Example Project Two AS'
        installed(s,[('reservation','Forbruk',changed)]);b,alerts=poll(s,a)
        self.assertEqual(1,len(alerts));details=str(alerts[0].item.alert_details)
        self.assertIn('20 → 35',details);self.assertIn('2030-01-01 → 2031-01-01',details)
        self.assertIn('Example Project AS → Example Project Two AS',details)
        self.assertIsNone(alerts[0].item.published);self.assertFalse(poll(s,b)[1])

    def test_whole_case_absence_retains_before_values_without_cancellation(self):
        s=source();installed(s,[('queue','Forbruk',row()),('queue','Forbruk',row('26/00002'))]);a,_=poll(s)
        installed(s,[('queue','Forbruk',row('26/00002'))]);b,alerts=poll(s,a)
        self.assertFalse(alerts);self.assertIn('26/00001',b['source_state']['records']['rows'])
        installed(s,[('queue','Forbruk',row(capacity=21)),('queue','Forbruk',row('26/00002'))]);_,alerts=poll(s,b)
        self.assertEqual(1,len(alerts));self.assertIn('20 → 21',str(alerts[0].item.alert_details))

    def test_phase_disappearance_does_not_claim_cancellation(self):
        s=source();installed(s,[('queue','Forbruk',row()),('reservation','Forbruk',row())]);a,_=poll(s)
        installed(s,[('reservation','Forbruk',row())]);_,alerts=poll(s,a)
        self.assertIn('årsak',str(alerts[0].item.alert_details))
        self.assertIn('ikke oppgitt',str(alerts[0].item.alert_details))

    def test_delivered_details_preserve_capacity_changes_across_phases(self):
        s=source();installed(s,[('queue','Forbruk',row()),('reservation','Forbruk',row())]);a,_=poll(s)
        changed=row();changed[2]='Long station '+('A'*170);changed[5]='Long customer '+('B'*170)
        installed(s,[('queue','Forbruk',changed),('reservation','Forbruk',row(capacity=30))])
        _,alerts=poll(s,a)
        delivered=notification_entries(alerts)[0].details
        self.assertTrue(any('Reservert' in d and '20 → 30' in d for d in delivered))
        self.assertTrue(all(len(d)<=500 for d in delivered))

    def test_ambiguous_duplicate_and_invalid_identity_fail(self):
        s=source()
        for rows in [[('queue','Forbruk',row()),('queue','Forbruk',row(capacity=21))],
                     [('queue','Forbruk',row(case='unknown'))]]:
            with self.assertRaises(SourceError):s._records(rows)

    def test_two_complete_reads_must_agree(self):
        s=source();s._poll=Mock(side_effect=[([('queue','Forbruk',row())],{'a':1},'2026-09-18'),
            ([('queue','Forbruk',row(capacity=21))],{'a':1},'2026-09-18')])
        with self.assertRaises(SourceError):s.read_records()

    def test_refresh_regression_and_invalid_date_fail(self):
        s=source();installed(s,[('queue','Forbruk',row())]);a,_=poll(s)
        a['source_state']['records']['model_refreshed']='2026-09-19T00:00:00+00:00'
        with self.assertRaises(SourceError):poll(s,a)
        self.assertIsNone(date(None));self.assertEqual('2030-01-01',date(1893456000000))
        with self.assertRaises(SourceError):date(1893456000001)


def payload():
    selects=[{'Column':{'Expression':{'SourceRef':{'Source':'s'}},'Property':'Case'},'Name':'case'},
             {'Measure':{'Expression':{'SourceRef':{'Source':'s'}},'Property':'MW'},'Name':'mw'}]
    desc=[{'Kind':1,'Depth':0,'Value':'G0','GroupKeys':[{'Source':{'Entity':'Cases','Property':'Case'},'Calc':'G0','IsSameAsSelect':True}],'Name':'case'},
          {'Kind':2,'Value':'M0','Name':'mw'}]
    ds={'N':'DS0','IC':True,'HAD':True,'ValueDicts':{'D0':['26/00001','26/00002']},
        'PH':[{'DM0':[{'S':[{'N':'G0','T':1,'DN':'D0'},{'N':'M0','T':3}],'C':[0,1.5]},
                     {'C':[1],'R':2},{'C':[],'R':1,'Ø':2}]}]}
    return {'jobIds':['test'],'results':[{'result':{'data':{'descriptor':{'Version':2,'Select':desc},'dsr':{'Version':2,'MinorVersion':1,'DS':[ds]}}}}]},selects


class PublicTableTests(unittest.TestCase):
    def test_dictionary_repeat_null_and_projection_order(self):
        p,s=payload();self.assertEqual([['26/00001',1.5],['26/00002',1.5],['26/00002',None]],flat_table(p,s,10,{'s':'Cases'}))
        p['results'][0]['result']['data']['descriptor']['Select'].reverse();s.reverse()
        self.assertEqual([1.5,'26/00001'],flat_table(p,s,10,{'s':'Cases'})[0])

    def test_incomplete_truncated_bad_mask_and_extra_shape_fail(self):
        for change in [lambda ds:ds.update(IC=False),lambda ds:ds.update(RT='next'),
            lambda ds:ds['PH'][0]['DM0'][1].update(R=4),lambda ds:ds['PH'][0]['DM0'][1].update(R=True),
            lambda ds:ds['PH'][0]['DM0'][1].update(R=2,Ø=2),
            lambda ds:ds['PH'][0]['DM0'][0]['C'].__setitem__(0,99)]:
            p,s=payload();change(p['results'][0]['result']['data']['dsr']['DS'][0])
            with self.assertRaises(SourceError):flat_table(p,s,10,{'s':'Cases'})
        p,s=payload()
        with self.assertRaises(SourceError):flat_table(p,s,3,{'s':'Cases'})

    def test_source_schema_changed_and_invalid_numeric_fail(self):
        p,s=payload();s[0]['Column']['Property']='Other'
        with self.assertRaises(SourceError):flat_table(p,s,10,{'s':'Cases'})
        for value in [True,float('nan'),'20']:
            p,s=payload();p['results'][0]['result']['data']['dsr']['DS'][0]['PH'][0]['DM0'][0]['C'][1]=value
            with self.assertRaises(SourceError):flat_table(p,s,10,{'s':'Cases'})


if __name__=='__main__':
    unittest.main()
