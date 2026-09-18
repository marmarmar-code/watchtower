from copy import deepcopy
import unittest
from unittest.mock import Mock

from watchtower.config import FilterRule, SourceConfig
from watchtower.engine import notification_entries
from watchtower.sources.common import SourceError
from watchtower.sources.trade_defence_cases import TradeDefenceCasesSource
from test_change_sources import poll, response

REVISION = 1789680761000


def index(ident=1, detailed=True):
    return {'id': ident, 'caseNumber': 'AD' + str(ident), 'shortName': 'Example metal products',
            'caseType': 'Initial Investigation', 'articleOfToc': '5', 'hasDetailPage': detailed,
            'caseCountries': [{'id': 1, 'countryName': 'Example Country', 'extraInformation': None}],
            'initDate': 1789423200000}


def detail(ident=1, **changes):
    return {**index(ident), 'ongoing': True, 'category': 'Anti-dumping', 'measStatus': 'No measure',
            'predisc': '01 October 2026', 'provMeasures': '01 November 2026',
            'returnCMTSDisc': '20 November 2026', 'returnCMTSFinalDisc': '02 July 2027',
            'defMeasures': '02 August 2027', 'tpvFrom': '01 December 2026', 'tpvTo': '31 December 2026',
            'publications': [{'casePublicationId': 10 * ident, 'caseId': ident,
                              'typeOfPublication': 'Initiation', 'contents': 'Official source notice'}], **changes}


def source(**options):
    return TradeDefenceCasesSource(SourceConfig(id='trade', kind='trade_defence_cases', label='Trade cases', urls=(),
        filters=FilterRule(match_all=True), options=options))


def install(src, rows=None, details=None, second_details=None, revisions=None):
    rows = [index()] if rows is None else rows
    details = [detail()] if details is None else details
    revisions = [REVISION] * 4 if revisions is None else revisions
    src.get = Mock(side_effect=[response(value) for value in [revisions[0], rows, *details, revisions[1],
        revisions[2], rows, *(details if second_details is None else second_details), revisions[3]]])


class TradeDefenceTests(unittest.TestCase):
    def test_baseline_repeat_dates_and_no_inferred_initiation(self):
        src = source(); install(src); first, alerts = poll(src)
        self.assertEqual([], alerts)
        row = first['source_state']['records']['rows']['1']['row']
        self.assertIsNone(row['published'])
        self.assertEqual('2027-08-02', row['fields']['indicative_definitive'])
        self.assertNotIn('initDate', row['fields'])
        install(src, details=[detail(initDate=1789423300000, ignored_refresh_time='different')])
        self.assertEqual((first, []), poll(src, first))

    def test_changed_deadline_and_document_metadata_once_in_notification(self):
        src = source(); install(src); first, _ = poll(src)
        docs = detail()['publications'] + [{'casePublicationId': 11, 'caseId': 1,
                    'typeOfPublication': 'Correction', 'contents': 'Corrected source notice'}]
        changed = detail(defMeasures='03 August 2027', publications=docs)
        install(src, details=[changed]); after, alerts = poll(src, first)
        entry = notification_entries(alerts)[0]
        self.assertLessEqual(len(entry.details), 8); self.assertTrue(all(len(line) <= 500 for line in entry.details))
        text = ' '.join(entry.details)
        for expected in ('2027-08-02 → 2027-08-03', 'Dokumentmetadata: 1 → 2', 'ID-er: 11', 'veiledende', 'Fravær beviser ikke avslutning'):
            self.assertIn(expected, text)
        install(src, details=[changed]); self.assertEqual([], poll(src, after)[1])

    def test_new_notification_keeps_timetable_and_legal_limitation(self):
        src = source(); install(src); first, _ = poll(src)
        install(src, rows=[index(2)], details=[detail(2)])
        _, alerts = poll(src, first)
        text = ' '.join(notification_entries(alerts)[0].details)
        for expected in ('Anti-dumping', 'Example Country', '2026-11-01', '2027-08-02', '2027-07-02',
                         '2026-12-01', '2026-12-31', '2026-10-01', 'bare frister i regelverk'):
            self.assertIn(expected, text)

    def test_unavailable_details_and_empty_countries_are_explicit(self):
        src = source(); install(src, rows=[index(detailed=False)], details=[])
        rows = src.read_records(); self.assertIsNone(rows[0]['fields']['documents'])
        self.assertFalse(rows[0]['fields']['detail_available'])
        empty = {**index(), 'caseCountries': []}
        install(src, rows=[empty], details=[detail(caseCountries=[], defMeasures='Not applicable')])
        row = src.read_records()[0]
        self.assertEqual([], row['fields']['countries']); self.assertEqual('Not applicable', row['fields']['indicative_definitive'])

    def test_order_and_absence_do_not_claim_closure(self):
        src = source(); install(src, rows=[index(), index(2)], details=[detail(), detail(2)])
        first, _ = poll(src)
        install(src, rows=[index(2), index()], details=[detail(), detail(2)])
        self.assertEqual((first, []), poll(src, first))
        install(src, rows=[index(2)], details=[detail(2)])
        after, alerts = poll(src, first)
        self.assertEqual([], alerts); self.assertEqual(first['seen'], after['seen'])
        self.assertEqual(2, len(after['source_state']['records']['rows']))
        # Snapshot history still suppresses an unchanged return after generic seen eviction.
        after['seen'] = {}
        install(src, rows=[index(), index(2)], details=[detail(), detail(2)])
        returned, alerts = poll(src, after)
        self.assertEqual([], alerts)
        install(src, rows=[index(2)], details=[detail(2)])
        absent, _ = poll(src, returned)
        install(src, rows=[index(), index(2)], details=[detail(defMeasures='03 August 2027'), detail(2)])
        _, alerts = poll(src, absent)
        self.assertEqual(1, len(alerts))
        self.assertEqual('changed', alerts[0].item.metadata['event'])
        self.assertIn('2027-08-02 → 2027-08-03', ' '.join(notification_entries(alerts)[0].details))

    def test_retained_history_is_bounded_without_mutating_prior_state(self):
        src = source(max_records=1); install(src); state, _ = poll(src)
        install(src, rows=[index(2)], details=[detail(2)]); state, _ = poll(src, state)
        saved = deepcopy(state)
        install(src, rows=[index(3)], details=[detail(3)])
        with self.assertRaisesRegex(SourceError, 'history bound'): poll(src, state)
        self.assertEqual(saved, state)

    def test_late_product_and_document_edits_show_actual_bounded_differences(self):
        for field in ('product', 'documents'):
            with self.subTest(field=field):
                src = source(); before = 'x' * 700 + 'original ending'; after = 'x' * 700 + 'corrected ending'
                if field == 'product':
                    install(src, rows=[{**index(), 'shortName': before}], details=[detail(shortName=before)])
                    state, _ = poll(src)
                    install(src, rows=[{**index(), 'shortName': after}], details=[detail(shortName=after)])
                else:
                    doc = detail()['publications'][0]
                    install(src, details=[detail(publications=[{**doc, 'contents': before}])]); state, _ = poll(src)
                    install(src, details=[detail(publications=[{**doc, 'contents': after}])])
                _, alerts = poll(src, state)
                details = notification_entries(alerts)[0].details; text = ' '.join(details)
                self.assertLessEqual(len(details), 8); self.assertTrue(all(len(line) <= 500 for line in details))
                for expected in ('original ending', 'corrected ending', '(utdrag)', 'veiledende'):
                    self.assertIn(expected, text)

    def test_many_other_changes_are_explicitly_marked_not_silently_cut(self):
        src = source(); install(src); state, _ = poll(src)
        row = {**index(), 'shortName': 'x' * 700, 'articleOfToc': 'y' * 80,
               'caseCountries': [{**index()['caseCountries'][0], 'extraInformation': 'n' * 700}]}
        docs = [{**detail()['publications'][0], 'contents': 'd' * 700}]
        install(src, rows=[row], details=[detail(shortName=row['shortName'], articleOfToc=row['articleOfToc'],
                                               caseCountries=row['caseCountries'], publications=docs)])
        updated, alerts = poll(src, state)
        details = notification_entries(alerts)[0].details
        self.assertIn('flere feltendringer; se kilden.', ' '.join(details))
        self.assertLessEqual(len(details), 8); self.assertTrue(all(len(line) <= 500 for line in details))
        self.assertEqual('d' * 700, updated['source_state']['records']['rows']['1']['row']['fields']['documents'][0]['description'])

    def test_removed_document_ids_and_unavailable_metadata_are_named(self):
        src = source()
        self.assertIn('fjernede ID-er: 10', src.describe_change('documents', [dict(id=10, type='Notice', description='Old')], []))
        self.assertIn('ikke tilgjengelig → 0', src.describe_change('documents', None, []))

    def test_revision_and_second_sweep_drift_preserve_prior_state(self):
        src = source(); install(src); first, _ = poll(src); saved = deepcopy(first)
        install(src, revisions=[REVISION, REVISION + 1, REVISION + 1, REVISION + 1])
        with self.assertRaisesRegex(SourceError, 'during the complete'): poll(src, first)
        install(src, second_details=[detail(measStatus='Measures in force')])
        with self.assertRaisesRegex(SourceError, 'between complete'): poll(src, first)
        install(src, revisions=[REVISION - 1] * 4)
        with self.assertRaisesRegex(SourceError, 'regressed'): poll(src, first)
        self.assertEqual(saved, first)

    def test_identity_dates_country_and_document_contracts_fail_closed(self):
        bads = [detail(id=2), detail(ongoing=False), detail(shortName='Other product'),
                detail(defMeasures='31 February 2027'), detail(predisc=1789423200000),
                detail(caseCountries=None), detail(publications=[{'casePublicationId': 1, 'caseId': 2}]),
                detail(publications=detail()['publications'] * 2)]
        for bad in bads:
            src = source(); install(src, details=[bad])
            with self.subTest(bad=bad), self.assertRaises(SourceError): src.read_records()
        for rows in ([], [index(), index()]):
            src = source(); install(src, rows=rows)
            with self.assertRaises(SourceError): src.read_records()

    def test_config_and_result_bounds(self):
        for options in ({'events': ['removed']}, {'complete_snapshot': True}):
            with self.assertRaises(ValueError): source(**options)
        src = source(max_records=1); install(src, rows=[index(), index(2)])
        with self.assertRaises(SourceError): src.read_records()


if __name__ == '__main__':
    unittest.main()
