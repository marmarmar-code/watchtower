from pathlib import Path
from unittest.mock import Mock
import json
import unittest

from watchtower.config import SourceConfig, FilterRule
from watchtower.sources.food_recalls import FoodRecallsSource, parse_recall, NAMES
from watchtower.sources.common import SourceError
from test_change_sources import poll, response

URL = 'https://www.mattilsynet.no/tilbakekallinger/synthetic-only'


def notice(batch='A', contact='Synthetic contact', modified='2026-09-01T00:00:00Z'):
    metadata = {'@type': 'WebPage', 'url': URL, 'datePublished': '2026-08-01T00:00:00Z',
                'dateModified': modified, 'description': 'Mistanke om forurensning'}
    return ('<main><h1>Synthetic recall</h1><dl><dt>Produktnavn</dt><dd>Synthetic product</dd>'
            f'<dt>Batchnummer</dt><dd>{batch}</dd><dt>Kontaktinformasjon</dt><dd>{contact}</dd>'
            '</dl></main><script type="application/ld+json">' + json.dumps(metadata) + '</script>').encode()


class FoodRecallTests(unittest.TestCase):
    def test_product_scope_change_alerts_but_contact_and_review_time_are_quiet(self):
        source = FoodRecallsSource(SourceConfig('test', 'food_recalls', filters=FilterRule(match_all=True)))
        row = parse_recall(notice(), URL, NAMES)
        source.read_records = Mock(return_value=[row])
        state, alerts = poll(source)
        self.assertEqual([], alerts)
        source.read_records.return_value = [parse_recall(notice(contact='Changed', modified='2026-09-02T00:00:00Z'), URL, NAMES)]
        state, alerts = poll(source, state)
        self.assertEqual([], alerts)
        source.read_records.return_value = [parse_recall(notice(batch='A B'), URL, NAMES)]
        _, alerts = poll(source, state)
        self.assertEqual(1, len(alerts))
        self.assertIn('Batchnummer: A B', ' '.join(alerts[0].item.alert_details))
        self.assertEqual(row['key'], source.read_records.return_value[0]['key'])

    def test_nynorsk_and_optional_fields_preserve_missing_values(self):
        raw = notice().replace(b'Produktnavn', b'Produktnamn')
        row = parse_recall(raw, URL, NAMES)
        self.assertEqual('Synthetic product', row['fields']['products'][0]['product'])
        self.assertIsNone(row['fields']['products'][0]['importer'])
        self.assertIn('Mistanke', row['fields']['description'])

    def test_invalid_product_or_publication_fails(self):
        for raw in (notice().replace(b'Produktnavn', b'Unknown'),
                    notice().replace(b'2026-08-01T00:00:00Z', b'invalid'), b'<main>Unavailable</main>'):
            with self.subTest(raw=raw), self.assertRaises(SourceError):
                parse_recall(raw, URL, NAMES)

    def test_multiple_product_groups_keep_their_batch_relationships(self):
        second = '<dl><dt>Produktnavn</dt><dd>Second product</dd><dt>Batchnummer</dt><dd>B</dd></dl>'
        raw = notice().replace(b'</main>', second.encode() + b'</main>')
        products = parse_recall(raw, URL, NAMES)['fields']['products']
        self.assertEqual({'Second product': 'B', 'Synthetic product': 'A'},
                         {p['product']: p['batch'] for p in products})

    def test_source_metadata_host_never_replaces_the_official_link(self):
        raw = notice().replace(b'https://www.mattilsynet.no/', b'https://192.0.2.1/')
        row = parse_recall(raw, URL, NAMES)
        self.assertEqual(URL, row['key'])
        self.assertEqual(URL, row['url'])

    def test_index_links_must_be_unique_and_in_the_recall_section(self):
        for hrefs in ([URL, URL], ['https://example.org/product']):
            source = FoodRecallsSource(SourceConfig('test', 'food_recalls'))
            source.get = Mock(return_value=response(('<main><ol id="list">' +
                ''.join(f'<li><a href="{url}">Recall</a></li>' for url in hrefs) + '</ol></main>').encode()))
            with self.assertRaises(SourceError):
                source.fetch()

    def test_removal_and_unknown_fields_are_rejected(self):
        for options in ({'complete_snapshot': True}, {'events': ['removed']}, {'watched_fields': ['unknown']},
                        {'watched_fields': ['title']}, {'watched_fields': ['batch']}):
            with self.assertRaises(ValueError):
                FoodRecallsSource(SourceConfig('test', 'food_recalls', options=options))
