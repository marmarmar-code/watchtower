import csv
from datetime import date
import io
import unittest
from unittest.mock import Mock

from watchtower.config import SourceConfig,FilterRule
from watchtower.engine import notification_entries
from watchtower.sources.common import SourceError
from watchtower.sources.petroleum_figures import PetroleumFiguresSource,HEADERS,PRODUCTION,RESERVES
from test_change_sources import poll,response


def source(profile='production',raw=None):
    s=PetroleumFiguresSource(SourceConfig(id='petroleum',kind='petroleum_figures',label='Source',
        filters=FilterRule(match_all=True),options={'profile':profile,'max_records':100,'latest_months':2,'max_period_age_days':730}))
    if raw is not None:s.get=Mock(side_effect=lambda *a,**kw:response(raw))
    return s


def production(month=None,oil='1.20',name='Example field'):
    year=date.today().year
    month=month or max(1,date.today().month-1)
    return dict(zip(HEADERS['production'],[name,str(year),str(month),oil,'2','0.1','0.2','3.5','1','123456']))


def reserves(remaining='1.5',version=None):
    year=version or date.today().year-1
    return dict(zip(HEADERS['reserves'],['Example field',str(year),*(['10']*5),remaining,'2','3','4','13.5',
        '31.12.'+str(year),'123456',date.today().strftime('%d.%m.%Y')]))


def export(profile,*rows):
    out=io.StringIO();writer=csv.DictWriter(out,fieldnames=HEADERS[profile]);writer.writeheader();writer.writerows(rows)
    return out.getvalue().encode()


class PetroleumTests(unittest.TestCase):
    def test_investment_revision_keeps_fixed_price_year_and_negative_values(self):
        row={'fldName':'Example field','fldInvestmentExpected':'125.0',
             'fldInvExpFixYear':str(date.today().year-1),'fldNpdidField':'123456'}
        a,alerts=poll(source('investments',export('investments',row)));self.assertFalse(alerts)
        row['fldInvestmentExpected']='-3'
        b,alerts=poll(source('investments',export('investments',row)),a)
        info=notification_entries(alerts)[0].details
        self.assertTrue(any('125 → -3' in line for line in info))
        self.assertTrue(any('prisbasisår: '+row['fldInvExpFixYear'] in line for line in info))
        self.assertTrue(any('Ikke faktisk årsforbruk' in line for line in info))
        self.assertFalse(poll(source('investments',export('investments',row)),b)[1])
        row['fldInvExpFixYear']=str(date.today().year)
        c,alerts=poll(source('investments',export('investments',row)),b)
        self.assertEqual(1,len(alerts));self.assertEqual(2,len(c['source_state']['records']['rows']))
        self.assertEqual('added',alerts[0].item.metadata['event'])

    def test_production_quiet_repeat_negative_revision_and_units(self):
        a,alerts=poll(source(raw=export('production',production())));self.assertFalse(alerts)
        b,alerts=poll(source(raw=export('production',production(oil='1.200'))),a);self.assertFalse(alerts)
        c,alerts=poll(source(raw=export('production',production(oil='-0.01'))),b)
        self.assertEqual(1,len(alerts));info=notification_entries(alerts)[0].details
        self.assertIn('Netto olje (mill. Sm3): 1.2 → -0.01',info)
        self.assertTrue(any('NGL (mill. Sm3)' in v for v in info));self.assertEqual(8,len(info))
        self.assertIsNone(alerts[0].item.published)
        self.assertFalse(poll(source(raw=export('production',production(oil='-0.010'))),c)[1])

    def test_reserves_missing_zero_negative_and_both_units_are_preserved(self):
        first=reserves(remaining='');s=source('reserves',export('reserves',first));a,_=poll(s)
        second=reserves(remaining='0');second[RESERVES[7]]='-0.01'
        b,alerts=poll(source('reserves',export('reserves',second)),a)
        info=notification_entries(alerts)[0].details
        self.assertEqual(8,len(info));self.assertTrue(any('ikke oppgitt → 0' in v for v in info))
        self.assertTrue(any('NGL (mill. tonn)' in v and '3 → -0.01' in v for v in info))
        self.assertTrue(any('Gass (mrd. Sm3)' in v for v in info))
        self.assertFalse(poll(source('reserves',export('reserves',second)),b)[1])

    def test_new_reserve_version_keeps_historical_state(self):
        old=reserves(version=date.today().year-2);new=reserves()
        a,_=poll(source('reserves',export('reserves',old)))
        b,alerts=poll(source('reserves',export('reserves',new,old)),a)
        self.assertEqual(1,len(alerts));self.assertEqual(2,len(b['source_state']['records']['rows']))

    def test_sync_date_only_change_does_not_alert(self):
        first=reserves();first['DatesyncNPD']='01.01.'+str(date.today().year)
        a,_=poll(source('reserves',export('reserves',first)))
        self.assertFalse(poll(source('reserves',export('reserves',reserves())),a)[1])

    def test_signed_zero_is_not_a_numeric_revision(self):
        a,_=poll(source(raw=export('production',production(oil='-0.0000'))))
        self.assertFalse(poll(source(raw=export('production',production(oil='0.00'))),a)[1])

    def test_duplicates_bad_numeric_header_and_truncation_fail(self):
        for raw in [export('production',production(),production()),export('production',production(oil='unknown')),
                    export('production',production()).replace(b'prfYear',b'year'),export('production',production())[:-2]]:
            with self.subTest(raw=raw[:40]),self.assertRaises(SourceError):source()._parse(raw)

    def test_selected_period_gap_or_missing_field_fails(self):
        s=source();s.months=3
        with self.assertRaises(SourceError):s._parse(export('production',production(month=5),production(month=7)))
        s.fields=('999',)
        with self.assertRaises(SourceError):s._parse(export('production',production()))

    def test_complete_reads_must_agree_and_period_must_not_regress(self):
        s=source();s.get=Mock(side_effect=[response(export('production',production())),response(export('production',production(oil='2')))])
        with self.assertRaises(SourceError):s.fetch_with_state(None)
        self.assertEqual({},s._next)
        a,_=poll(source('reserves',export('reserves',reserves())))
        with self.assertRaises(SourceError):poll(source('reserves',export('reserves',reserves(version=date.today().year-2))),a)

    def test_old_observation_is_explicitly_stale(self):
        s=source('reserves');s.max_age=30
        with self.assertRaises(SourceError):s._parse(export('reserves',reserves(version=date.today().year-2)))


if __name__=='__main__':unittest.main()
