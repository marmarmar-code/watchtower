import copy
import json
import unittest
from unittest.mock import Mock

from watchtower.config import FilterRule, SourceConfig
from watchtower.engine import notification_entries
from watchtower.sources.enova_projects import EnovaProjectsSource, PAGE, production_rows, project_records, template_literal
from watchtower.sources.common import SourceError
from test_change_sources import poll, response


def project(case='25/12', status='Prosjektgjennomføring'):
    return {'saksnummer': case, 'prosjekttittel': 'Example energy project', 'prosjekteier': 'Example Builder',
        'organisasjonsnummer': '923609016', 'stottebelop_vedtatt': 500000.0,
        'stottebelop_vedtatt_opprinnelig': 500000.0, 'levetid': 10, 'klimaresultat_vedtatt': -2.5,
        'energiresultat_vedtatt': 10000.0, 'stotteobjekt_navn': 'Anleggsmaskin',
        'stotteobjekt_underkategori': 'Ukjent', 'stotteobjekt_antall': 1.0,
        'status': status, 'stotteprogram': 'Example program', 'sektor': 'Bygg og eiendom',
        'prosjektfylke': None, 'prosjektkommune': None, 'prosjektstart_gjeldende_dato': 1735689600000,
        'prosjektslutt_gjeldende_dato': 1767225600000, 'vedtaksdato': 1735689600000}


def bundle(rows):
    # This is a clearly synthetic parser fixture, not a source evidence export.
    value = json.dumps(rows, ensure_ascii=False).replace('\\', '\\\\').replace('`', '\\`').replace('${', '\\${')
    mock = json.dumps([project('25/999', 'Kansellert')])
    return 'var actual=JSON.parse(`'+value+'`),fake=JSON.parse(`'+mock+'`),key=`prosjektlisteDataSource`,real=actual.map(mapper),test=fake.map(mapper);function defaultSource(){return choice()||`backend`}function route(e){return e===`backend`?real:test}'


class EnovaProjectsTests(unittest.TestCase):
    def source(self, **options):
        return EnovaProjectsSource(SourceConfig(id='projects', kind='enova_projects', label='Project execution',
            urls=(PAGE,), filters=FilterRule(match_all=True), options={'sectors': ['Bygg og eiendom'],
                'from_year': 2024, **options}), retry_attempts=1)

    def load(self, source, rows):
        body = bundle(rows).encode()
        source.get = Mock(side_effect=lambda url, **kw: response(
            b'<script type="module" src="/assets/index-ABC.js"></script>' if url == PAGE else body))

    def test_backend_is_selected_and_two_complete_reads_repeat_quietly(self):
        source = self.source(); self.load(source, [project()]); state, alerts = poll(source)
        self.assertEqual([], alerts); self.assertEqual(4, source.get.call_count)
        self.assertEqual({'25/12'}, set(source._next['rows']))
        repeated, alerts = poll(source, state); self.assertEqual(state, repeated); self.assertEqual([], alerts)
        self.assertEqual(['enova_status_update_time_unavailable'], source.coverage_warnings)

    def test_completed_cancelled_dates_and_new_project_are_distinct_observations(self):
        source = self.source(); self.load(source, [project()]); state, _ = poll(source)
        done = project(status='Ferdigstilt'); done['prosjektslutt_gjeldende_dato'] += 86400000
        self.load(source, [done]); state, alerts = poll(source, state)
        text = ' '.join(alerts[0].item.alert_details)
        self.assertIn('Prosjektgjennomføring → Ferdigstilt', text)
        self.assertIn('2026-01-01 → 2026-01-02', text)
        self.assertIn('statusens oppdateringstid er ikke oppgitt', text)
        self.load(source, [project(status='Kansellert'), project('25/15', 'Innvilget')]); _, alerts = poll(source, state)
        self.assertEqual(2, len(alerts))

    def test_composite_cases_keep_all_objects_without_summing_funding(self):
        second = project(); second.update(stotteobjekt_navn='Kjøretøy', stotteobjekt_antall=2.0)
        rows = project_records([project(), second]); self.assertEqual(1, len(rows)); self.assertEqual(2, len(rows[0]['objects']))
        self.assertNotIn('stottebelop_vedtatt', rows[0]['fields'])
        self.assertEqual(rows, project_records([second, project()]))
        second['status'] = 'Kansellert'
        with self.assertRaisesRegex(SourceError, 'disagree'): project_records([project(), second])

    def test_static_literals_decode_escapes_without_executing_code(self):
        data = project(); data['prosjekttittel'] = 'Quote " slash \\ tick ` literal ${anything}'
        self.assertEqual([data], production_rows(bundle([data]), 10))
        self.assertEqual(('abcAæ', 14), template_literal('abc\\x41\\u00e6`', 0))
        for value in ['${code}`', 'bad\\q`', 'missing', '\\xZ1`']:
            with self.subTest(value=value), self.assertRaises(SourceError): template_literal(value, 0)

    def test_routing_changes_mock_default_ambiguity_and_truncation_fail(self):
        original = bundle([project()])
        values = [original.replace('||`backend`', '||`mock`'), original.replace('real=actual.map', 'real=missing.map'),
                  original.replace('?real:test', '?test:test'), original + original,
                  original.replace('JSON.parse(`', 'JSON.parse(`broken', 1), original[:-30]]
        for data in values:
            with self.subTest(data=data[-60:]), self.assertRaises(SourceError): production_rows(data, 10)

    def test_missing_previous_project_race_and_unknown_sector_fail_without_mutation(self):
        source = self.source(); self.load(source, [project(), project('25/13')]); state, _ = poll(source); saved = copy.deepcopy(state)
        self.load(source, [project()])
        with self.assertRaisesRegex(SourceError, 'previously observed'): poll(source, state)
        self.assertEqual(saved, state)
        source._export = Mock(side_effect=[(project_records([project()]), 1), (project_records([project(status='Kansellert')]), 1)])
        with self.assertRaisesRegex(SourceError, 'changed during reading'): source.read_records()
        source = self.source(sectors=['Unpublished']); self.load(source, [project()])
        with self.assertRaisesRegex(SourceError, 'sector is absent'): source.read_records()

    def test_invalid_fields_dates_limits_and_redirects_fail(self):
        for key, value in [('status', 'Unknown'), ('prosjektstart_gjeldende_dato', True),
                           ('vedtaksdato', 1735689600001), ('saksnummer', 'invalid'), ('stotteobjekt_antall', float('nan'))]:
            data = project(); data[key] = value
            with self.subTest(key=key), self.assertRaises(SourceError): project_records([data])
        with self.assertRaises(SourceError): production_rows(bundle([project(), project('25/13')]), 1)
        for options in [{'max_records': 1}, {'max_bundle_bytes': 1024}]:
            source = self.source(**options); self.load(source, [project(), project('25/13')])
            with self.assertRaises(SourceError): source.read_records()
        source = self.source(); source.get = Mock(return_value=response(b'', status=302))
        with self.assertRaises(SourceError): source.read_records()
        for options in [{'sectors': []}, {'allow_empty': True}, {'complete_snapshot': True}, {'max_bundle_bytes': 60000001}]:
            with self.subTest(options=options), self.assertRaises(ValueError): self.source(**options)

    def test_asset_host_substitution_and_multiple_scripts_fail(self):
        for html in [b'<script type="module" src="https://other.example/a.js"></script>',
                     b'<script type="module" src="/assets/index-A.js"></script><script type="module" src="/assets/index-B.js"></script>']:
            source = self.source(); source.get = Mock(return_value=response(html))
            with self.assertRaises(SourceError): source.read_records()

    def test_notification_keeps_all_three_changes_and_both_caveats(self):
        source = self.source(); self.load(source, [project()]); state, _ = poll(source)
        data = project(status='Ferdigstilt')
        data['prosjektstart_gjeldende_dato'] += 86400000
        data['prosjektslutt_gjeldende_dato'] += 86400000
        self.load(source, [data]); _, alerts = poll(source, state)
        details = notification_entries(alerts)[0].details; text = ' '.join(details)
        self.assertEqual(8, len(details)); self.assertIn('Prosjektgjennomføring → Ferdigstilt', text)
        self.assertIn('2025-01-01 → 2025-01-02', text); self.assertIn('2026-01-01 → 2026-01-02', text)
        self.assertIn('statusens oppdateringstid er ikke oppgitt', text); self.assertIn('ikke utbetaling', text)
        self.load(source, [project(), project('25/15')]); _, alerts = poll(source, state)
        text = ' '.join(notification_entries(alerts)[0].details)
        self.assertIn('Gjeldende prosjektstart: 2025-01-01', text); self.assertIn('Gjeldende prosjektslutt: 2026-01-01', text)
