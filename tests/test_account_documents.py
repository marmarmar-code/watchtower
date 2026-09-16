import unittest
from unittest.mock import Mock
from watchtower.config import SourceConfig, FilterRule
from watchtower.sources.account_documents import AccountDocumentsSource
from watchtower.sources.common import SourceError
from test_change_sources import poll, response


def source(**options):
    return AccountDocumentsSource(SourceConfig(id='accounts', kind='account_documents', label='Hydro',
        urls=(), filters=FilterRule(match_all=True), options={'companies': ['914778271'], **options}))


class AccountDocumentTests(unittest.TestCase):
    def test_only_new_year_alerts_and_empty_availability_is_distinct(self):
        s = source(allow_empty=True)
        s.get = Mock(return_value=response(['2024']))
        state, alerts = poll(s)
        self.assertEqual([], alerts)
        s.get.return_value = response(['2024', '2025'])
        state, alerts = poll(s, state)
        self.assertEqual(1, len(alerts))
        self.assertIn('2025', alerts[0].item.title)
        self.assertIsNone(alerts[0].item.published)
        self.assertEqual(
            'https://data.brreg.no/regnskapsregisteret/regnskap/aarsregnskap/kopi/914778271/2025',
            alerts[0].item.url,
        )
        self.assertTrue(s.get.call_args.args[0].endswith('/aar'))
        s.get.return_value = response(['2025', '2024'])
        _, alerts = poll(s, state)
        self.assertEqual([], alerts)
        s.get.return_value = response([])
        _, alerts = poll(s, state)
        self.assertEqual([], alerts)

    def test_corrected_links_preserve_existing_identity_and_fingerprint(self):
        s = source()
        s.get = Mock(return_value=response(['2024']))
        previous, _ = poll(s)
        for record in previous['source_state']['records']['rows'].values():
            record['row']['url'] = record['row']['url'].rsplit('/', 1)[0] + '/aar'
        updated, alerts = poll(s, previous)
        self.assertEqual([], alerts)
        self.assertEqual(previous['seen'], updated['seen'])
        rows = updated['source_state']['records']['rows'].values()
        self.assertTrue(all(record['row']['url'].endswith('/2024') for record in rows))

    def test_invalid_years_fail_closed(self):
        for years in ([2025], [True], ['2099'], ['2025', '2025'], {'year': '2025'}):
            with self.subTest(years=years), self.assertRaises(SourceError):
                s = source(); s.get = Mock(return_value=response(years)); s.read_records()

    def test_invalid_companies_and_removals_rejected(self):
        for options in ({'companies': ['123456789']}, {'events': ['removed']}):
            with self.assertRaises(ValueError):
                source(**options)
