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
        self.assertTrue(alerts[0].item.url.endswith('/aar'))
        s.get.return_value = response(['2025', '2024'])
        _, alerts = poll(s, state)
        self.assertEqual([], alerts)
        s.get.return_value = response([])
        _, alerts = poll(s, state)
        self.assertEqual([], alerts)

    def test_invalid_years_fail_closed(self):
        for years in ([2025], [True], ['2099'], ['2025', '2025'], {'year': '2025'}):
            with self.subTest(years=years), self.assertRaises(SourceError):
                s = source(); s.get = Mock(return_value=response(years)); s.read_records()

    def test_invalid_companies_and_removals_rejected(self):
        for options in ({'companies': ['123456789']}, {'events': ['removed']}):
            with self.assertRaises(ValueError):
                source(**options)
