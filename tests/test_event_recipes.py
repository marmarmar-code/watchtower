from copy import deepcopy
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from watchtower.cli import main
from watchtower.config import load_config
from watchtower.engine import build_source
from watchtower.recipes import add_sources, load_recipes, selected_sources, source_toml
from watchtower.setup import make_config, write_runtime
from watchtower.sources.common import SourceError
from watchtower.sources.ssb_data import SsbDataSource, observations
from watchtower.sources.changes import SnapshotSource
from watchtower.sources.structured import StructuredSource
from test_change_sources import config, poll, response


def cube():
    return {'version':'2.0','class':'dataset','id':['Metric','Time'],'size':[1,2],
            'role':{'time':['Time'],'metric':['Metric']},
            'dimension':{'Metric':{'category':{'index':['index'],'label':{'index':'Synthetic index'},
                                             'unit':{'index':{'base':'index','decimals':1}}},
                                   'extension':{'basePeriod':{'index':'2025'}}},
                         'Time':{'category':{'index':{'2026M01':0,'2026M02':1}}}},
            'value':[101,102]}


class SsbObservationTests(unittest.TestCase):
    def source(self):
        return SsbDataSource(replace(config('ssb_data',table='14700',value_codes={'Metric':['index'],'Time':['top(2)']}),urls=()))

    def test_new_period_revision_and_units_are_separate_observations(self):
        source=self.source(); data=cube()
        source.get=Mock(return_value=response(data))
        previous,alerts=poll(source)
        self.assertEqual([],alerts)
        data['value'][0]=100
        source.get.return_value=response(data)
        previous,alerts=poll(source,previous)
        self.assertEqual(1,len(alerts))
        self.assertIn('Revidert statistikkobservasjon',alerts[0].item.alert_details)
        self.assertIn('101 → 100',' '.join(alerts[0].item.alert_details))
        self.assertIn('Enhet: index, basis 2025',alerts[0].item.alert_details)
        data['dimension']['Time']['category']['index']={'2026M02':0,'2026M03':1}
        data['value']=[102,103]
        source.get.return_value=response(data)
        previous,alerts=poll(source,previous)
        self.assertEqual(1,len(alerts))
        self.assertEqual('2026M03',alerts[0].item.published)
        self.assertIn('Ny statistikkobservasjon',alerts[0].item.alert_details)
        data['dimension']['Metric']['extension']['basePeriod']['index']='2026'
        source.get.return_value=response(data)
        _,alerts=poll(source,previous)
        self.assertEqual(2,len(alerts))
        self.assertTrue(all('basis 2025' in ' '.join(alert.item.alert_details)
                            and 'basis 2026' in ' '.join(alert.item.alert_details) for alert in alerts))

    def test_readable_units_preserve_observation_identity_and_unknown_metadata(self):
        source = self.source()
        for adjustment, expected in [('None', 'Enhet: index, basis 2025'),
                                     ('WorkAndSes', 'Enhet: index, basis 2025, kalender- og sesongjustert'),
                                     ('FutureCode', 'Enhet: index, basis 2025, justering: FutureCode')]:
            with self.subTest(adjustment=adjustment):
                data = cube()
                data['dimension']['Metric']['extension']['adjustment'] = {'index': adjustment}
                row = observations(data, table='14700', limit=4)[0]
                original = deepcopy(row)
                item = source._item(row, 'added', ('Ny registrering', 'raw unit metadata'), False)
                canonical = SnapshotSource._item(source, row, 'added', (), False)
                self.assertEqual(original, row)
                self.assertEqual((canonical.key, canonical.content_hash(), canonical.metadata, canonical.text),
                                 (item.key, item.content_hash(), item.metadata, item.text))
                self.assertIn(expected, item.alert_details)
                self.assertIn('Verdi: 101', item.alert_details)
                self.assertFalse(any('raw unit' in d or 'base_period' in d for d in item.alert_details))
        before = row['fields']['unit']
        after = deepcopy(before)
        after['Metric']['decimals'] = 2
        after['Metric']['extra'] = 'source note'
        detail = source.describe_change('unit', before, after)
        self.assertIn('desimaler: 1', detail)
        self.assertIn('desimaler: 2', detail)
        self.assertIn('extra: source note', detail)

    def test_dimension_order_and_sparse_vectors_preserve_identity(self):
        data=cube()
        first=observations(data,table='14700',limit=4)
        data['id']=['Time','Metric']; data['size']=[2,1]
        data['value']={'0':101,'1':102}
        second=observations(data,table='14700',limit=4)
        self.assertEqual([row['key'] for row in first],[row['key'] for row in second])
        self.assertEqual([row['fields'] for row in first],[row['fields'] for row in second])
        data['value']={'1':102};data['status']={'0':':' }
        rows=observations(data,table='14700',limit=4)
        self.assertIsNone(rows[0]['fields']['value'])
        self.assertEqual(':',rows[0]['fields']['status'])

    def test_status_and_missing_values_are_not_silently_coerced(self):
        source=self.source(); data=cube()
        source.get=Mock(return_value=response(data));previous,_=poll(source)
        data['value'][0]=None;data['status']={"0":":"}
        source.get.return_value=response(data)
        _,alerts=poll(source,previous)
        self.assertIn('ikke oppgitt',' '.join(alerts[0].item.alert_details))
        for values in ([101], {'2':100}, [True,102], ['101',102]):
            with self.subTest(values=values),self.assertRaises(SourceError):
                observations({**data,'value':values},table='14700',limit=4)

    def test_cube_limits_and_every_dimension_require_explicit_selection(self):
        with self.assertRaisesRegex(SourceError,'max_records'):observations(cube(),table='14700',limit=1)
        source=SsbDataSource(replace(config('ssb_data',table='14700',value_codes={'Time':['top(2)']}),urls=()))
        source.get=Mock(return_value=response(cube()))
        with self.assertRaisesRegex(SourceError,'every SSB dimension'):source.fetch()
        data=cube();data['dimension']['Metric']['category'].pop('unit')
        with self.assertRaisesRegex(SourceError,'unit'):observations(data,table='14700',limit=4)


class RecipeTests(unittest.TestCase):
    def source(self, recipe):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.toml'
            path.write_text(source_toml(selected_sources(recipe=recipe,match_all=True)[0]))
            return build_source(load_config(path).sources[0])

    def test_every_recipe_and_rss_profile_builds_a_valid_configuration(self):
        self.assertEqual(90,len(load_recipes()))
        for row in load_recipes():
            with self.subTest(recipe=row['id']):self.source(row['id'])
        for profile in ('nkom','met_farevarsler'):
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'config.toml'
                path.write_text(source_toml(selected_sources(rss_profile=profile,topics=['Synthetic Topic'])[0]))
                build_source(load_config(path).sources[0])

    def test_business_feed_exclusions_survive_all_and_topic_selection(self):
        from watchtower.config import FilterRule
        for options in ({"match_all": True}, {"topics": ["tilsyn"]}):
            selected = selected_sources(recipe="forbrukertilsynet_news", **options)[0]
            rule = FilterRule(**selected["filter"])
            self.assertFalse(rule.matches("Ledig stilling i tilsynet"))
            self.assertTrue(rule.matches("Tilsynet undersøker skjult reklame"))
        nho = selected_sources(recipe="nho_business_news", match_all=True)[0]
        self.assertFalse(FilterRule(**nho["filter"]).matches("Ikoner"))
        self.assertEqual(["emp.jobylon.com"], nho["exclude_url_hosts"])
        self.assertEqual(["emp.jobylon.com"], selected_sources(recipe="nho_business_news", topics=["direktør"])[0]["exclude_url_hosts"])
        for source_id in ("nho_business_news", "forbrukertilsynet_news", "finansnorge_news", "sjomatnorge_news"):
            with self.subTest(source=source_id), self.assertRaisesRegex(ValueError, "--topic"):
                selected_sources(recipe=source_id)

    def test_news_requires_topic_or_explicit_all_but_narrow_data_does_not(self):
        for recipe in ('riksrevisjonen_reports','nkom_events','virke_pressemeldinger','konkurransetilsynet_news'):
            with self.assertRaisesRegex(ValueError,'--topic'):selected_sources(recipe=recipe)
        self.assertTrue(selected_sources(recipe='nb_policy_rate')[0]['filter']['match_all'])
        with self.assertRaisesRegex(ValueError,'unknown'):selected_sources(recipe='missing')

    def test_append_preserves_existing_configuration_and_state_and_duplicate_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            target=write_runtime(root,make_config('general',['Synthetic Topic'],[],'teams'))
            before=target.read_text();state=root/'state'/'existing.json';state.write_text('{"marker":true}')
            sources=selected_sources(recipe='ssb_cpi')
            add_sources(root,sources)
            self.assertEqual(before,target.read_text())
            add_sources(root,sources,apply=True)
            self.assertTrue(target.read_text().startswith(before.rstrip()))
            self.assertEqual(5,len(load_config(target).sources))
            self.assertEqual('{"marker":true}',state.read_text())
            after=target.read_bytes()
            with self.assertRaises(ValueError):add_sources(root,sources,apply=True)
            self.assertEqual(after,target.read_bytes())
            self.assertFalse((root/'config'/'.watchtower-config.lock').exists())

    def test_symlink_and_concurrent_lock_cannot_overwrite_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            target=write_runtime(root,make_config('general',['Synthetic Topic'],[],'teams'))
            before=target.read_bytes();sources=selected_sources(recipe='ssb_cpi')
            lock=root/'config'/'.watchtower-config.lock';lock.touch()
            with self.assertRaises(FileExistsError):add_sources(root,sources,apply=True)
            self.assertEqual(before,target.read_bytes());lock.unlink()
            alias=root/'alias';alias.symlink_to(root,target_is_directory=True)
            with self.assertRaisesRegex(ValueError,'symbolic'):add_sources(alias,sources,apply=True)

    def test_numeric_normalization_ignores_formatting_but_rejects_invalid_values(self):
        source=StructuredSource(config('csv_records',id_fields=['id'],fields=['value'],numeric_fields=['value']))
        source.get=Mock(return_value=response(b'id,value\nA,4.25\n'));previous,_=poll(source)
        source.get.return_value=response(b'id,value\nA,4.2500\n')
        previous,alerts=poll(source,previous);self.assertEqual([],alerts)
        source.get.return_value=response(b'id,value\nA,4.5\n')
        _,alerts=poll(source,previous);self.assertEqual(1,len(alerts))
        source.get.return_value=response(b'id,value\nA,invalid\n')
        with self.assertRaises(SourceError):source.fetch()

    def test_severe_weather_excludes_yellow_and_alerts_on_escalation(self):
        source=self.source('met_severe_weather')
        def event(color):return {'features':[{'properties':{'id':'synthetic','riskMatrixColor':color,
            'title':'Synthetic weather event','description':'Synthetic description','consequences':'Synthetic consequence',
            'eventEndingTime':'2026-09-09T12:00:00Z'}}]}
        source.get=Mock(return_value=response(event('Yellow')));previous,_=poll(source)
        source.get.return_value=response(event('Orange'))
        previous,alerts=poll(source,previous);self.assertEqual(1,len(alerts))
        source.get.return_value=response(event('Red'))
        _,alerts=poll(source,previous);self.assertEqual(1,len(alerts))
        self.assertIn('Orange → Red',' '.join(alerts[0].item.alert_details))

    def test_preview_reads_without_creating_state_or_notifier(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'config.toml';state=root/'state'
            path.write_text(source_toml(selected_sources(recipe='nb_policy_rate')[0]))
            source=build_source(load_config(path).sources[0])
            source.get=Mock(return_value=response(b'INSTRUMENT_TYPE;TENOR;OBS_VALUE;UNIT_MEASURE;TIME_PERIOD\nKPRA;SD;4.25;PCT;2026-09-01\n'))
            args=['watchtower','preview','--config',str(path),'--state-dir',str(state),'--source','nb_policy_rate','--redact-output']
            output=io.StringIO()
            with patch('sys.argv',args),patch('watchtower.cli.build_source',return_value=source),patch('watchtower.cli.build_notifier') as notifier,patch('sys.stdout',output):
                self.assertEqual(0,main())
            notifier.assert_not_called();self.assertFalse(state.exists())
            self.assertIn('items=1 alerts=0',output.getvalue())
            self.assertNotIn('4.25',output.getvalue())


if __name__=='__main__':unittest.main()
