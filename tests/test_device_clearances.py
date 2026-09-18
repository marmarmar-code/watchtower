from copy import deepcopy
from datetime import date
import unittest
from unittest.mock import Mock, patch

from watchtower.config import FilterRule, SourceConfig
from watchtower.engine import notification_entries
from watchtower.sources.common import SourceError
from watchtower.sources.device_clearances import DeviceClearancesSource
from test_change_sources import poll, response


def row(key='K260001', **changes):
    return {'k_number': key, 'applicant': 'Example Medical AS', 'country_code': 'NO',
            'device_name': 'Example diagnostic equipment', 'product_code': 'ABC',
            'decision_code': 'SESE', 'decision_description': 'Substantially Equivalent',
            'clearance_type': 'Traditional', 'advisory_committee': 'RA',
            'advisory_committee_description': 'Radiology', 'decision_date': '2026-09-06',
            'date_received': '2026-05-01', 'contact': 'Not monitored',
            'openfda': {'registration_number': ['unrelated aggregated metadata']}, **changes}


def payload(rows=None, skip=0, total=None, limit=100, exported='2026-09-07'):
    rows = [row()] if rows is None else rows
    return {'meta': {'last_updated': exported, 'results': {'skip': skip, 'limit': limit,
            'total': len(rows) if total is None else total}}, 'results': rows}


def source(**options):
    return DeviceClearancesSource(SourceConfig(id='clearances', kind='device_clearances', label='Device decisions',
        urls=(), filters=FilterRule(match_all=True), options={'allow_empty': True, **options}))


def install(src, pages=None, second=None):
    pages = [payload()] if pages is None else pages
    src.get = Mock(side_effect=[response(value) for value in [*pages, *(pages if second is None else second)]])


class DeviceClearanceTests(unittest.TestCase):
    def setUp(self):
        clock = patch('watchtower.sources.device_clearances.today', return_value=date(2026, 9, 18))
        clock.start(); self.addCleanup(clock.stop)

    def test_baseline_repeat_separate_dates_and_ignore_harmonisation(self):
        src = source(); install(src); first, alerts = poll(src)
        self.assertEqual([], alerts)
        stored = first['source_state']['records']['rows']['K260001']['row']
        self.assertIsNone(stored['published'])
        self.assertEqual('2026-09-06', stored['fields']['decision_date'])
        self.assertEqual('2026-05-01', stored['fields']['date_received'])
        self.assertNotIn('Not monitored', str(first)); self.assertNotIn('unrelated', str(first))
        install(src, [payload([row(openfda={'registration_number': ['different']}, contact='Changed')])])
        self.assertEqual((first, []), poll(src, first))

    def test_changed_decision_once_and_delivery_preserves_outcome(self):
        src = source(); install(src); first, _ = poll(src)
        changed = row(decision_code='SESP', decision_description='Substantially Equivalent - Postmarket Surveillance Required')
        install(src, [payload([changed])]); after, alerts = poll(src, first)
        self.assertEqual(1, len(alerts))
        details = ' '.join(notification_entries(alerts)[0].details)
        self.assertIn('SESE → SESP', details)
        self.assertIn('Postmarket Surveillance Required', details)
        install(src, [payload([changed])]); self.assertEqual([], poll(src, after)[1])

    def test_new_notification_retains_dates_outcome_and_limits(self):
        src = source(); install(src); first, _ = poll(src)
        install(src, [payload([row('K260002', device_name='Long device name ' * 100)])])
        _, alerts = poll(src, first)
        entry = notification_entries(alerts)[0]
        self.assertLessEqual(len(entry.details), 8)
        self.assertTrue(all(len(line) <= 500 for line in entry.details))
        text = ' '.join(entry.details)
        for value in ('SESE', 'Substantially Equivalent', '2026-09-06', '2026-05-01',
                      '2026-09-07', 'ikke en generell påstand', 'postland', 'tilbaketrekking'):
            self.assertIn(value, text)

    def test_complete_pagination_and_double_reads(self):
        src = source(page_size=1)
        pages = [payload([row()], total=2, limit=1), payload([row('K260002')], skip=1, total=2, limit=1)]
        install(src, pages)
        self.assertEqual(2, len(src.read_records())); self.assertEqual(4, src.get.call_count)
        self.assertIn('country_code%3ANO', src.get.call_args.args[0])

    def test_partial_duplicate_drift_and_bounds_fail_closed(self):
        bads = [payload([], total=1), payload([row(), row()]), payload([row()], total=1001),
                payload([row()], skip=1), payload([row()], limit=99),
                payload([row()], exported='2026-01-01')]
        for bad in bads:
            src = source(); install(src, [bad])
            with self.subTest(bad=bad['meta']), self.assertRaises(SourceError): src.read_records()
        src = source(); install(src); first, _ = poll(src); saved = deepcopy(first)
        install(src, second=[payload([row(applicant='Changed')])])
        with self.assertRaisesRegex(SourceError, 'between complete'): poll(src, first)
        self.assertEqual(saved, first)

    def test_identifiers_scope_dates_and_missing_fields_are_checked(self):
        for changed in [row('invalid'), row(country_code='US'), row(decision_date='2026-09-08'),
                        row(date_received='2026-10-01'), row(decision_date='2026-05-01'),
                        row(product_code='a'), row(decision_description=None), row(decision_code='')]:
            src = source(); install(src, [payload([changed])])
            with self.assertRaises(SourceError): src.read_records()
        src = source(); install(src, [payload([row('DEN260001')])])
        self.assertEqual('DEN260001', src.read_records()[0]['key'])

    def test_empty_contract_and_export_regression(self):
        src = source(); missing = response({'error': {'code': 'NOT_FOUND', 'message': 'No matches found!'}}, status=404)
        src.get = Mock(return_value=missing)
        state, alerts = poll(src)
        self.assertEqual([], alerts); self.assertEqual({}, state['source_state']['records']['rows'])
        src = source(); install(src); state, _ = poll(src)
        install(src, [payload(exported='2026-09-06')])
        with self.assertRaisesRegex(SourceError, 'export regressed'): poll(src, state)
        src.get = Mock(return_value=response({'error': 'not found'}, status=404))
        with self.assertRaises(SourceError): src.read_records()

    def test_transport_and_configuration_bounds(self):
        for status, content in [(302, payload()), (200, b'{bad'), (200, b'{"meta":1,"meta":2}'), (200, b'x' * 2048)]:
            src = source(max_bytes=1024); result = response(content, status=status); src.get = Mock(return_value=result)
            with self.assertRaises(SourceError): src.read_records()
            result.close.assert_called_once()
        for options in ({'events': ['removed']}, {'complete_snapshot': True}, {'country_codes': ['no']},
                        {'decision_days': 367}, {'max_pages': 11}):
            with self.assertRaises(ValueError): source(**options)


if __name__ == '__main__':
    unittest.main()
