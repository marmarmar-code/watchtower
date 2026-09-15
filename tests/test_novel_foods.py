import unittest
from datetime import date
from unittest.mock import Mock, patch

from watchtower.config import FilterRule,SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.novel_foods import NovelFoodsSource,PAGE_URL
from test_change_sources import poll,response


def row(year=2026, number=10, description='authorising Example Ingredient as a novel food', correction=None, kind='Regulation'):
    label=('Corrigendum to ' if correction else '')+f'Commission Implementing {kind} (EU) {year}/{number}'
    url=f'https://eur-lex.europa.eu/eli/reg_impl/{year}/{number}'+(f'/corrigendum/{correction}' if correction else '')+'/oj'
    return f'<li><a href="{url}"><strong>{label}</strong></a> of 2 February {year}, {description}</li>'


def page(current=None, prior=None, intro='', year=2026):
    current=row(year) if current is None else current
    prior=row(year-1) if prior is None else prior
    return (f'<h1>Union list of novel foods</h1><main><h2>About the Union list</h2><ul>{intro}</ul>'
            f'<div><h3>Updates - {year}</h3><ul>{current}</ul></div>'
            f'<details><summary>Updates - {year-1}</summary><div><ul>{prior}</ul></div></details></main>').encode()


class NovelFoodsTests(unittest.TestCase):
    def source(self,urls=(PAGE_URL,),**options):
        return NovelFoodsSource(SourceConfig(id='novel',kind='novel_foods',label='Novel foods',urls=urls,
            filters=FilterRule(match_all=True),options=options),timeout=1,retry_attempts=1)

    def load(self,s,html):
        s.get=Mock(return_value=response(html))

    def test_baseline_repeat_and_whitespace_or_order_are_quiet(self):
        s=self.source(); html=page(current=row()+row(number=11),intro=row(2025,12))
        self.load(s,html);first,alerts=poll(s);self.assertEqual([],alerts)
        self.assertEqual(4,len(s._next['rows']))
        self.load(s,page(current=row(number=11)+row(),intro=row(2025,12)).replace(b'of 2',b'of  2'))
        second,alerts=poll(s,first);self.assertEqual([],alerts);self.assertEqual(first,second)

    def test_description_and_source_type_correction_alert_once(self):
        s=self.source();self.load(s,page(current=row(kind='Decision')));state,_=poll(s)
        self.assertTrue(s._next['rows']['reg_impl:2026/10']['row']['fields']['type_mismatch'])
        self.load(s,page(current=row(description='amending the labelling conditions for Example Ingredient',kind='Regulation')))
        state,alerts=poll(s,state);self.assertEqual(1,len(alerts))
        self.assertIn('norsk gjennomføring',' '.join(alerts[0].item.alert_details))
        self.assertFalse(s._next['rows']['reg_impl:2026/10']['row']['fields']['type_mismatch'])
        _,alerts=poll(s,state);self.assertEqual([],alerts)

    def test_corrigendum_is_distinct_and_dates_are_separate(self):
        s=self.source();self.load(s,page(prior=row(2025)));state,_=poll(s)
        self.load(s,page(prior=row(2025)+row(2025,correction='2025-12-01')))
        state,alerts=poll(s,state);self.assertEqual(1,len(alerts))
        values=s._next['rows']['reg_impl:2025/10:corrigendum:2025-12-01']['row']['fields']
        self.assertEqual('2025-02-02',values['listed_act_date']);self.assertEqual('2025-12-01',values['corrigendum_date'])
        self.assertIn('retting',alerts[0].item.title)
        self.assertEqual(3,len(s._next['rows']))
        self.load(s,page());_,alerts=poll(s,state);self.assertEqual([],alerts)

    def test_year_rollover_discovers_new_acts_without_rebaseline(self):
        class LaterDate(date):
            @classmethod
            def today(cls):return cls(2027,3,1)
        s=self.source();self.load(s,page());state,_=poll(s)
        self.load(s,page(year=2027))
        with patch('watchtower.sources.novel_foods.date',LaterDate):
            state,alerts=poll(s,state)
        self.assertEqual(1,len(alerts));self.assertIn('2027/10',alerts[0].item.title)
        self.assertEqual({'reg_impl:2026/10','reg_impl:2027/10'},set(state['source_state']['records']['rows']))
        self.load(s,page(year=2026))
        with self.assertRaisesRegex(SourceError,'regressed'):poll(s,state)

    def test_missing_sections_rows_or_link_reject_without_state_advancing(self):
        s=self.source();good=page();self.load(s,good);state,_=poll(s)
        bad=[page(current=''),good.replace(b'Updates - 2025',b'Archive'),page(current='<li>No link</li>'),
             page(current=row()+'<li>Incomplete extra row</li>'),page(current=row().replace('</a>','</a><a href="https://example.test">Extra</a>'))]
        for html in bad:
            self.load(s,html)
            with self.subTest(html=html),self.assertRaises(SourceError):poll(s,state)
        self.load(s,good);again,alerts=poll(s,state);self.assertEqual(state,again);self.assertEqual([],alerts)

    def test_invalid_dates_id_host_and_correction_shape_fail(self):
        changes=[(b'2 February 2026',b'31 February 2026'),(b'2 February 2026',b'2 February 2025'),
                 (b'/2026/10/oj',b'/2026/11/oj'),(b'eur-lex.europa.eu',b'eur-lex.europa.eu.example.test'),
                 (b'/2026/10/oj',b'/2026/10/oj?x=1'),(b'/2026/10/oj',b'/2026/10/corrigendum/2026-03-01/oj')]
        for before,after in changes:
            s=self.source();self.load(s,page().replace(before,after))
            with self.subTest(after=after),self.assertRaises(SourceError):s.read_records()
        s=self.source();self.load(s,page(prior=row(2025,correction='2025-01-01')))
        with self.assertRaises(SourceError):s.read_records()

    def test_conflicting_duplicates_bounds_and_redirects_fail(self):
        s=self.source();self.load(s,page(intro=row(2026,description='a conflicting description for Example Ingredient')))
        with self.assertRaises(SourceError):s.read_records()
        self.load(s,page(intro=row()));self.assertEqual(2,len(s.read_records()))
        s=self.source(max_records=1);self.load(s,page())
        with self.assertRaises(SourceError):s.read_records()
        s=self.source();s.get=Mock(return_value=response(b'',status=302,headers={'Location':PAGE_URL}))
        with self.assertRaises(SourceError):s.read_records()

    def test_configuration_and_single_latest_year(self):
        s=self.source(latest_years=1);self.load(s,page());self.assertEqual(1,len(s.read_records()))
        for options in [{'latest_years':0},{'latest_years':True},{'complete_snapshot':True},{'events':['removed']}]:
            with self.assertRaises(ValueError):self.source(**options)
        with self.assertRaises(ValueError):self.source(urls=('https://example.test',))


if __name__=='__main__':unittest.main()
