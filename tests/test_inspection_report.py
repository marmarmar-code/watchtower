from copy import deepcopy
from datetime import datetime, timezone
import json
from unittest import TestCase
from unittest.mock import Mock, patch

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.inspection_report import (
    ENTITY, MODEL, PAGE, PROPERTIES, InspectionReportSource, report_query,
)
from test_change_sources import poll

INDUSTRY = 'F Bygge- og anleggsvirksomhet'
OTHER = 'C Industri'
STAMP = '2026-09-18T06:00:00+00:00'


def model():
    selects = []
    for i, prop in enumerate(PROPERTIES):
        col = {'Expression': {'SourceRef': {'Source': 'p'}}, 'Property': prop}
        selects.append({'Column':col, 'Name':ENTITY+'.'+prop} if i<2 else {
            'Aggregation': {'Expression': {'Column':col}, 'Function':0}, 'Name':'Sum('+ENTITY+'.'+prop+')'})
    query = {'Commands':[{'SemanticQueryDataShapeCommand':{
        'Query': {'Version':2, 'From':[{'Name':'p', 'Entity':ENTITY, 'Type':0}], 'Select':selects},
        'Binding':{'Primary': {'Groupings':[{'Projections':[0]}, {'Projections':list(range(1,7))}]}}}}]}
    return {'exploration': {'sections':[{'displayName':'Vedtak per næring', 'visualContainers':[{'query':json.dumps(query)}]}]}}


def payload(rows):
    _, selects = report_query(model(), 1000)
    descriptor = []
    schema = []
    for i, select in enumerate(selects):
        value = 'G'+str(i) if i<2 else 'M'+str(i-2)
        desc = {'Name':select['Name'], 'Value':value, 'Kind':1 if i<2 else 2}
        if i<2:
            desc.update(Depth=0, GroupKeys=[{'Source':{'Entity':ENTITY, 'Property':PROPERTIES[i]}, 'Calc':value, 'IsSameAsSelect':True}])
        descriptor.append(desc)
        schema.append({'N':value, 'T':1 if i==1 else 4})
    encoded = [{'S':schema, 'C':rows[0]}]+[{'C':r} for r in rows[1:]]
    return {'jobIds':[], 'results':[{'result':{'data':{
        'descriptor':{'Version':2, 'Select':descriptor},
        'dsr':{'Version':2, 'MinorVersion':1, 'DS':[{'N':'DS0', 'IC':True, 'HAD':True, 'PH':[{'DM0':encoded}]}]}
    }}}]}


def source(**options):
    return InspectionReportSource(SourceConfig(id='inspection', kind='inspection_report', label='Inspection aggregates',
        urls=(PAGE,), filters=FilterRule(match_all=True), options={
            'profile':'main_industry', 'main_industries':[INDUSTRY], 'from_year':2024, **options}))


def set_rows(s, rows=None, stamp=STAMP):
    rows = [[2025, INDUSTRY, 10, 0, 3, 2, 1]] if rows is None else rows
    s._poll = Mock(return_value=(deepcopy(rows), stamp))


class InspectionReportTests(TestCase):
    def test_quiet_baseline_repeat_and_precise_count_changes(self):
        s=source(); set_rows(s); state,alerts=poll(s); self.assertEqual([],alerts)
        same,alerts=poll(s,state); self.assertEqual(state,same); self.assertEqual([],alerts)
        set_rows(s,[[2025, INDUSTRY, 8, 2, 3, 2, 1]])
        revised,alerts=poll(s,state); self.assertEqual(1,len(alerts))
        text=' '.join(alerts[0].item.alert_details)
        self.assertIn('Tilsyn med pålegg: 10 → 8',text)
        self.assertIn('Tilsyn med stans som pressmiddel: 0 → 2',text)
        self.assertIn('Reviderte årsaggregater',text)
        self.assertIn('ikke nye enkeltvedtak',text)
        self.assertIn('næringsgrupper er ikke dekket',text)
        same,alerts=poll(s,revised); self.assertEqual(revised,same); self.assertEqual([],alerts)

    def test_new_year_and_scope_change_use_distinct_identities(self):
        s=source(); set_rows(s); state,_=poll(s)
        set_rows(s,[[2025, INDUSTRY, 10, 0, 3, 2, 1], [2026, INDUSTRY, 5, 0, 0, 0, 0]])
        state,alerts=poll(s,state); self.assertEqual(1,len(alerts))
        self.assertEqual('added',alerts[0].item.metadata['event'])
        self.assertIn('Nyobservert årsaggregat',' '.join(alerts[0].item.alert_details))
        changed=source(main_industries=[OTHER]); set_rows(changed,[[2025, OTHER, 20, 1, 0, 1, 0]])
        state,alerts=poll(changed,state); self.assertEqual([],alerts)
        self.assertEqual(1,len(state['source_state']['records']['rows']))

    def test_selection_is_exact_and_retains_absent_years_without_removal(self):
        s=source(); set_rows(s,[[2023, INDUSTRY, 7, 0, 0, 0, 0], [2024, INDUSTRY, 10, 0, 0, 0, 0], [2025, INDUSTRY, 9, 0, 0, 0, 0], [2025, OTHER, 99, 0, 0, 0, 0]])
        state,alerts=poll(s); self.assertEqual(2,len(state['source_state']['records']['rows'])); self.assertEqual([],alerts)
        set_rows(s,[[2025, INDUSTRY, 9, 0, 0, 0, 0]])
        retained,alerts=poll(s,state); self.assertEqual(state['source_state']['records']['rows'],retained['source_state']['records']['rows']); self.assertEqual([],alerts)
        set_rows(s,[[2024, INDUSTRY, 11, 0, 0, 0, 0], [2025, INDUSTRY, 9, 0, 0, 0, 0]])
        _,alerts=poll(s,retained); self.assertEqual(1,len(alerts))
        self.assertIn('10 → 11',' '.join(alerts[0].item.alert_details))

    def test_refresh_is_not_an_alert_and_regression_preserves_staged_state(self):
        s=source(); set_rows(s); state,_=poll(s)
        set_rows(s,stamp='2026-09-18T07:00:00+00:00'); newer,alerts=poll(s,state); self.assertEqual([],alerts)
        staged=deepcopy(s._next); saved=deepcopy(newer)
        set_rows(s)
        with self.assertRaisesRegex(SourceError,'regressed'):poll(s,newer)
        self.assertEqual(staged,s._next); self.assertEqual(saved,newer)

    def test_racing_reads_missing_selection_and_size_fail_atomically(self):
        s=source(); set_rows(s); state,_=poll(s); staged=deepcopy(s._next); saved=deepcopy(state)
        for sides in [
            [([[2025, INDUSTRY, 10,0,3,2,1]],STAMP), ([[2025, INDUSTRY, 11,0,3,2,1]],STAMP)],
            [([[2025, INDUSTRY, 10,0,3,2,1]],STAMP), ([[2025, INDUSTRY, 10,0,3,2,1]],'2026-09-18T07:00:00+00:00')],
            [([[2025, OTHER, 10,0,3,2,1]],STAMP)]*2,
        ]:
            s._poll=Mock(side_effect=sides)
            with self.assertRaises(SourceError):poll(s,state)
            self.assertEqual(staged,s._next); self.assertEqual(saved,state)
        small=source(max_records=1);set_rows(small,[[2024, INDUSTRY, 10,0,3,2,1],[2025, INDUSTRY, 10,0,3,2,1]])
        with self.assertRaisesRegex(SourceError,'max_records'):poll(small)

    def test_query_validates_published_visual_fields_and_aggregation(self):
        original=model(); query,selects=report_query(original,250);self.assertEqual(model(),original)
        self.assertEqual(list(range(7)),query['Commands'][0]['SemanticQueryDataShapeCommand']['Binding']['Primary']['Groupings'][0]['Projections'])
        for mutation in ('Where','aggregate','field','duplicate','section'):
            bad=model(); section=bad['exploration']['sections'][0]
            q=json.loads(section['visualContainers'][0]['query']); semantic=q['Commands'][0]['SemanticQueryDataShapeCommand']['Query']
            if mutation=='Where':semantic['Where']=[]
            if mutation=='aggregate':semantic['Select'][2]['Aggregation']['Function']=3
            if mutation=='field':semantic['Select'][3]['Aggregation']['Expression']['Column']['Property']='Other'
            section['visualContainers'][0]['query']=json.dumps(q)
            if mutation=='duplicate':section['visualContainers']*=2
            if mutation=='section':section['displayName']='Other'
            with self.subTest(mutation=mutation),self.assertRaises(SourceError):report_query(bad,250)

    def test_real_poll_path_uses_shared_decoder_and_rejects_invalid_rows(self):
        s=source(); s._embed=Mock(return_value='https://app.powerbi.com/view')
        fake=Mock(model=model(),refreshed=datetime(2026,9,18,tzinfo=timezone.utc))
        good=[2025,INDUSTRY,10,0,3,2,1]
        with patch('watchtower.sources.inspection_report.PublicReport',return_value=fake) as factory:
            fake.query.return_value=payload([good]); rows,stamp=s._poll();self.assertEqual([good],rows)
            factory.assert_called_once_with(s,'https://app.powerbi.com/view',MODEL,7)
            for rows in [[good,good],[[2999,*good[1:]]],[[2025,'  '+INDUSTRY,*good[2:]]],[[*good[:2],None,*good[3:]]],[[*good[:2],-1,*good[3:]]],[[*good[:2],1.5,*good[3:]]]]:
                fake.query.return_value=payload(rows)
                with self.subTest(rows=rows),self.assertRaises(SourceError):s._poll()

    def test_page_identity_embed_and_byte_bound(self):
        s=source(max_bytes=1024)
        for html in ['<h1>Other</h1>','<h1>Tilsynsstatistikk</h1>', '<h1>Tilsynsstatistikk</h1>'+2*'<iframe src="https://app.powerbi.com/view"></iframe>', 'x'*1025]:
            response=Mock(iter_content=Mock(return_value=[html.encode()]),close=Mock());s.get=Mock(return_value=response)
            with self.assertRaises(SourceError):s._embed()
            response.close.assert_called_once()
        html='<h1>Tilsynsstatistikk</h1><iframe src="https://app.powerbi.com/view?r=example"></iframe>'
        s.get=Mock(return_value=Mock(iter_content=Mock(return_value=[html.encode()])))
        self.assertEqual('https://app.powerbi.com/view?r=example',s._embed())
        s.get.assert_called_once_with(PAGE,stream=True,allow_redirects=False)

    def test_configuration_requires_explicit_bounded_main_industry_profile(self):
        for options in [{'profile':'detail'},{'main_industries':[]},{'main_industries':[' F ']},{'main_industries':[str(i) for i in range(11)]},{'complete_snapshot':True},{'allow_empty':True},{'events':['removed']},{'from_year':True},{'max_report_rows':10},{'max_model_age_days':46}]:
            with self.subTest(options=options),self.assertRaises(ValueError):source(**options)
