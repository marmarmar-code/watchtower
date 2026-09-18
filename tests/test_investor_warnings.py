from copy import deepcopy
from datetime import date
import json
import unittest
from unittest.mock import Mock, patch

from watchtower.config import FilterRule, SourceConfig
from watchtower.engine import notification_entries
from watchtower.sources.common import SourceError
from watchtower.sources.investor_warnings import AUTHORITY, InvestorWarningsSource
from test_change_sources import poll, response

META = [{'id': 70, 'type': 'NorwegianInvestorWarningPage', 'name': AUTHORITY}]


def row(ident=1, **values):
    return {'id': ident, 'name': 'Misbruk av Example Company sitt namn', 'metaData': AUTHORITY,
            'published': '9. september 2026', 'url': f'https://www.finanstilsynet.no/markedsadvarsler/2026/example-{ident}/',
            'preamble': 'Truncated excerpt…', **values}


def index(rows=None, page=1, total=None, pages=None):
    rows = [row()] if rows is None else rows
    total = len(rows) if total is None else total
    return {'items': rows, 'page': page, 'total': total, 'pageSize': 25,
            'totalPages': (total + 24) // 25 if pages is None else pages}


def article(title=None, lead='Source warning introduction', body='The legitimate company name has been misused.', published='9. september 2026'):
    title = title or row()['name']
    metadata = json.dumps({'lastChangedDateText': 'Sist publisert: ' + published})
    return (f'<html><main><article><section><h1>{title}</h1><p>{lead}</p></section>'
            f'<div id="articleMainBody"><p>{body}</p></div></article></main>'
            f'<script>React.createElement(PublishInfo,{metadata});</script></html>').encode()


def source(**options):
    return InvestorWarningsSource(SourceConfig(id='warnings', kind='investor_warnings', label='Official warnings',
        urls=(), filters=FilterRule(match_all=True), options={'allow_empty': True, **options}))


def install(src, indexes=None, details=None, second_details=None, metadata=None):
    indexes = [index()] if indexes is None else indexes
    details = [article()] if details is None else details
    metadata = META if metadata is None else metadata
    first = [metadata, *indexes, *details]
    second = [metadata, *indexes, *(details if second_details is None else second_details)]
    src.get = Mock(side_effect=[response(value) for value in [*first, *second]])


class InvestorWarningTests(unittest.TestCase):
    def setUp(self):
        clock = patch('watchtower.sources.investor_warnings.today', return_value=date(2026, 9, 18))
        clock.start(); self.addCleanup(clock.stop)

    def test_baseline_repeat_full_text_not_excerpt(self):
        src = source(); install(src); state, alerts = poll(src)
        self.assertEqual([], alerts)
        fields = state['source_state']['records']['rows']['1']['row']['fields']
        self.assertEqual('2026-09-09', fields['last_published'])
        self.assertEqual('The legitimate company name has been misused.', fields['notice_text'])
        self.assertNotIn('Truncated excerpt', str(state))
        install(src, [index([row(preamble='Different index excerpt')])])
        self.assertEqual((state, []), poll(src, state))

    def test_original_url_year_is_not_assumed_to_be_last_publication_year(self):
        src = source()
        install(src, [index([row(url='https://www.finanstilsynet.no/markedsadvarsler/2022/example')])])
        self.assertEqual('2026-09-09', src.read_records()[0]['published'])

    def test_new_and_changed_notifications_preserve_caveat_and_before_after(self):
        src = source(); install(src); state, _ = poll(src)
        install(src, [index([row(2)])], [article(lead='x' * 1500, body='y' * 1500)])
        after, alerts = poll(src, state)
        entry = notification_entries(alerts)[0]
        self.assertLessEqual(len(entry.details), 8); self.assertTrue(all(len(v) <= 500 for v in entry.details))
        self.assertIn('2026-09-09', ' '.join(entry.details)); self.assertIn('legitime virksomheten kan være offer', ' '.join(entry.details))
        install(src, [index([row(2, published='10. september 2026')])], [article(body='Changed source warning', published='10. september 2026')])
        changed, alerts = poll(src, after)
        entry = notification_entries(alerts)[0]
        self.assertIn('2026-09-09 → 2026-09-10', ' '.join(entry.details))
        self.assertIn(' → Changed source warning', ' '.join(entry.details))
        self.assertIn('kan være offer', ' '.join(entry.details))
        install(src, [index([row(2, published='10. september 2026')])], [article(body='Changed source warning', published='10. september 2026')])
        self.assertEqual([], poll(src, changed)[1])

    def test_complete_pagination_and_authority_metadata(self):
        src = source()
        rows = [row(value) for value in range(1, 27)]
        indexes = [index(rows[:25], total=26), index(rows[25:], page=2, total=26)]
        install(src, indexes, [article()] * 26)
        self.assertEqual(26, len(src.read_records())); self.assertEqual(58, src.get.call_count)
        install(src, metadata=[{**META[0], 'type': 'InternationalInvestorWarningPage'}])
        with self.assertRaisesRegex(SourceError, 'authority filter'): src.read_records()

    def test_late_article_change_is_visible_in_actual_notification(self):
        prefix = 'Identical introductory text. ' * 25
        self.assertGreater(len(prefix), 500)
        src = source(); install(src, details=[article(body=prefix + 'The permit was granted.')])
        state, _ = poll(src)
        install(src, details=[article(body=prefix + 'The permit was withdrawn.')])
        _, alerts = poll(src, state)
        entry = notification_entries(alerts)[0]
        text = ' '.join(entry.details)
        for expected in ('utdrag rundt endring', 'permit was granted', 'permit was withdrawn', ' → ', 'kan være offer'):
            self.assertIn(expected, text)
        self.assertLessEqual(len(entry.details), 8); self.assertTrue(all(len(line) <= 500 for line in entry.details))

    def test_counts_dates_duplicates_origin_and_authority_fail_closed(self):
        invalid = [index([row(), row()]), index(total=2), index(pages=2), index([row(published='9. september 2024')]),
                   index([row(metaData='Foreign authority')]), index([row(url='https://example.test/warning')]),
                   index([row(published='31. februar 2026')]), index([row(id=True)])]
        for bad in invalid:
            src = source(); install(src, [bad])
            with self.subTest(bad=bad), self.assertRaises(SourceError): src.read_records()

    def test_article_identity_full_body_and_publication_agreement(self):
        invalid = [article(title='Other warning'), article(published='8. september 2026'),
                   article()[:-20], article().replace(b'articleMainBody', b'missing-body'),
                   article().replace(b'PublishInfo', b'OtherInfo'), article().replace(b'<p>Source warning introduction</p>', b'')]
        for bad in invalid:
            src = source(); install(src, details=[bad])
            with self.assertRaises(SourceError): src.read_records()

    def test_double_read_drift_does_not_replace_prior_state(self):
        src = source(); install(src); state, _ = poll(src); saved = deepcopy(state)
        install(src, second_details=[article(body='Changed mid-read')])
        with self.assertRaisesRegex(SourceError, 'between complete'): poll(src, state)
        self.assertEqual(saved, state)

    def test_no_removal_or_unbounded_selection(self):
        for options in ({'events': ['removed']}, {'complete_snapshot': True}, {'years_back': 4}, {'max_pages': 21}):
            with self.assertRaises(ValueError): source(**options)

    def test_observed_zero_page_empty_contract_is_quiet(self):
        src = source(); install(src, [index([])], [])
        state, alerts = poll(src)
        self.assertEqual([], alerts); self.assertEqual({}, state['source_state']['records']['rows'])


if __name__ == '__main__':
    unittest.main()
