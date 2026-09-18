import copy
import unittest
from unittest.mock import Mock

from watchtower.config import FilterRule, SourceConfig
from watchtower.engine import notification_entries
from watchtower.sources.building_approvals import BuildingApprovalsSource, PAGE, enterprise_record
from watchtower.sources.common import SourceError
from test_change_sources import poll, response

ORG = '923609016'


def payload(grade='2', approved=True):
    return {'enterprise': {'enterprise': {'organizational_number': ORG, 'name': 'Example Builder', 'email': 'ignored'},
        'status': {'approved': approved, 'approval_period_to': '2027-02-01'},
        'valid_approval_areas': [{'function': 'Utførende', 'subject_area': 'Tømrerarbeid', 'pbl': 'PBL 2016',
            'function_xml': 'UTF', 'subject_area_xml': 'UTOMRE', 'pbl_xml': 'pbl2016', 'grade': grade}]}}


class BuildingApprovalsTests(unittest.TestCase):
    def source(self, **options):
        return BuildingApprovalsSource(SourceConfig(id='builder', kind='building_approvals', label='Builder',
            urls=(PAGE,), filters=FilterRule(match_all=True), options={'orgnrs': [ORG], **options}), retry_attempts=1)

    def load(self, source, value):
        source.get = Mock(side_effect=lambda *a, **kw: response(value))

    def test_quiet_baseline_reorder_exact_duplicates_and_contact_noise(self):
        source = self.source(); data = payload(); self.load(source, data)
        state, alerts = poll(source); self.assertEqual([], alerts); self.assertEqual(2, source.get.call_count)
        data['enterprise']['enterprise']['email'] = 'new ignored contact'
        data['enterprise']['valid_approval_areas'] *= 2
        self.load(source, data); repeat, alerts = poll(source, state)
        self.assertEqual(state, repeat); self.assertEqual([], alerts)
        self.assertNotIn('email', str(state))

    def test_documented_v1_and_v2_envelopes_share_state_and_real_changes(self):
        source = self.source(); self.load(source, payload()); state, _ = poll(source)
        v1 = {'dibk-sgdata': payload()['enterprise']}
        self.load(source, v1); repeated, alerts = poll(source, state)
        self.assertEqual(state, repeated); self.assertFalse(alerts)
        self.assertEqual({'Accept': 'application/vnd.sgpub.v2'}, source.get.call_args.kwargs['headers'])
        v1['dibk-sgdata']['valid_approval_areas'][0]['grade'] = '3'
        self.load(source, v1); updated, alerts = poll(source, repeated)
        self.assertEqual(1, len(alerts))
        self.assertIn('tiltaksklasse 2 → 3', ' '.join(notification_entries(alerts)[0].details))
        self.load(source, payload('3')); repeated, alerts = poll(source, updated)
        self.assertEqual(updated, repeated); self.assertFalse(alerts)

    def test_unknown_ambiguous_or_incomplete_v1_is_rejected_without_state_change(self):
        source = self.source(); self.load(source, payload()); state, _ = poll(source)
        saved = copy.deepcopy(state)
        malformed = [None, [], {'error': 'Source error'}, payload()['enterprise'],
                     {'enterprise': payload()['enterprise'], 'dibk-sgdata': payload()['enterprise']},
                     {'dibk-sgdata': None}, {'dibk-sgdata': {}}]
        wrong_identity = {'dibk-sgdata': copy.deepcopy(payload()['enterprise'])}
        wrong_identity['dibk-sgdata']['enterprise']['organizational_number'] = '976967631'
        malformed.append(wrong_identity)
        wrong_status = {'dibk-sgdata': copy.deepcopy(payload()['enterprise'])}
        wrong_status['dibk-sgdata']['status']['approved'] = 1
        malformed.append(wrong_status)
        for data in malformed:
            with self.subTest(data=data):
                self.load(source, data)
                with self.assertRaises(SourceError): poll(source, state)
                self.assertEqual(saved, state)

    def test_grade_change_retains_before_after_and_distinct_same_area_rows(self):
        source = self.source(); self.load(source, payload()); state, _ = poll(source)
        data = payload('3'); self.load(source, data); state, alerts = poll(source, state)
        self.assertEqual(1, len(alerts)); text = ' '.join(alerts[0].item.alert_details)
        self.assertIn('tiltaksklasse 2 → 3', text)
        data['enterprise']['valid_approval_areas'] += payload()['enterprise']['valid_approval_areas']
        self.assertEqual(2, len(enterprise_record(data, ORG)['fields']['approval_areas']))

    def test_explicit_nonapproval_name_and_expiry_changes(self):
        source = self.source(); self.load(source, payload()); state, _ = poll(source)
        data = payload(approved=False); data['enterprise']['valid_approval_areas'] = []
        data['enterprise']['enterprise']['name'] = 'Renamed Builder'
        data['enterprise']['status']['approval_period_to'] = '2026-02-01'
        self.load(source, data); _, alerts = poll(source, state)
        text = ' '.join(alerts[0].item.alert_details)
        self.assertIn('Godkjent → Ikke godkjent', text); self.assertIn('Example Builder → Renamed Builder', text)
        self.assertIn('2027-02-01 → 2026-02-01', text)

    def test_failures_preserve_state_and_race_does_not_alert(self):
        source = self.source(); self.load(source, payload()); state, _ = poll(source); saved = copy.deepcopy(state)
        source.get = Mock(side_effect=[response(payload()), response(payload('3'))])
        with self.assertRaisesRegex(SourceError, 'changed during reading'): poll(source, state)
        self.assertEqual(saved, state)
        source.get = Mock(side_effect=SourceError('HTTP 404'))
        with self.assertRaises(SourceError): poll(source, state)
        self.assertEqual(saved, state)

    def test_incomplete_identity_status_date_or_areas_are_rejected(self):
        for mutate in [lambda d: d['enterprise']['enterprise'].update(organizational_number='976967631'),
                       lambda d: d['enterprise']['status'].update(approved=1),
                       lambda d: d['enterprise']['status'].update(approval_period_to='2026-02-30'),
                       lambda d: d['enterprise'].update(valid_approval_areas=[]),
                       lambda d: d['enterprise']['valid_approval_areas'][0].pop('subject_area_xml')]:
            data = payload(); mutate(data)
            with self.subTest(mutate=mutate), self.assertRaises(SourceError): enterprise_record(data, ORG)

    def test_config_bounds_redirect_and_duplicate_json(self):
        for options in [{'orgnrs': []}, {'orgnrs': ['123456789']}, {'complete_snapshot': True}, {'allow_empty': True}]:
            with self.subTest(options=options), self.assertRaises(ValueError): self.source(**options)
        source = self.source(); source.get = Mock(return_value=response(b'', status=302))
        with self.assertRaises(SourceError): source.read_records()

    def test_notification_preserves_class_status_expiry_and_excerpt_warning(self):
        source = self.source(); data = payload()
        area = data['enterprise']['valid_approval_areas'][0]
        data['enterprise']['valid_approval_areas'] = [{**area, 'subject_area_xml': f'AREA{i}',
            'subject_area': 'Long subject ' * 20 + str(i)} for i in range(30)]
        self.load(source, data); state, _ = poll(source)
        for area in data['enterprise']['valid_approval_areas']: area['grade'] = '3'
        data['enterprise']['status'].update(approved=False, approval_period_to='2026-02-01')
        data['enterprise']['enterprise']['name'] = 'A renamed builder'
        self.load(source, data); _, alerts = poll(source, state)
        details = notification_entries(alerts)[0].details; text = ' '.join(details)
        self.assertLessEqual(len(details), 8); self.assertTrue(all(len(d) <= 500 for d in details))
        self.assertIn('tiltaksklasse 2 → 3', text); self.assertIn('Utdrag', text)
        self.assertIn('av 30 endringer', text); self.assertIn('Godkjent → Ikke godkjent', text)
        self.assertIn('2027-02-01 → 2026-02-01', text); self.assertIn('ikke en byggetillatelse', text)
        source.get = Mock(return_value=response(b'{"enterprise":{},"enterprise":{}}'))
        with self.assertRaises(SourceError): source.read_records()
        source = self.source(max_bytes=1024); source.get = Mock(return_value=response(b' ' * 1025))
        with self.assertRaises(SourceError): source.read_records()
