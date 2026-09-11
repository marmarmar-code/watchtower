import json
import unittest
from unittest.mock import Mock
from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.research_awards import ResearchAwardsSource, HEADERS


def html(title='Innvilgede søknader', values=None, header=None, published='15.06.2026'):
    values = values or ['363504', 'Institutt', 'Prosjekt', 'Tema', '20000000', '639170784000000000']
    cells = [{'value': value} for value in values]
    cells[-1]['displayValue'] = published
    props = {'sections': [{'guid': 'results', 'content': {'tables': [
        {'title': title, 'table': {'header': header or HEADERS, 'rows': [cells]}}
    ]}}]}
    return ('<script>window.__REACT_COMPONENTS__.push({name: "ProposalPage", props: '
            + json.dumps(props) + '});</script>').encode()


def source(raw):
    config = SourceConfig(id='rcn', kind='research_awards', label='RCN',
        urls=('https://www.forskningsradet.no/utlysninger/2025/example/',),
        filters=FilterRule(match_all=True), options={})
    source = ResearchAwardsSource(config)
    source.get = Mock(return_value=Mock(status_code=200, headers={}, iter_content=Mock(return_value=[raw])))
    return source


def listing(cards):
    return (f'<form class="proposal-list-page"><div class="proposal-group"><div class="proposal-group--header">Type ({len(cards)} utlysninger)</div>' + ''.join(
        f'<div class="proposal"><div class="status {css}">{status}</div>'
        f'<a class="proposal--link" href="{url}">Call</a></div>'
        for status, url, css in cards) + '</div></form>').encode()


def discovery(list_raw, result_pages, **options):
    config = SourceConfig(id='rcn', kind='research_awards', label='RCN', urls=(),
        filters=FilterRule(match_all=True), options={'discover_results': True,
        'discovery_years': [2026], 'max_discovery_calls': 3, **options})
    source = ResearchAwardsSource(config)
    replies = [list_raw, *result_pages]
    source.get = Mock(side_effect=[Mock(status_code=200, headers={},
        iter_content=Mock(return_value=[raw]), close=Mock()) for raw in replies])
    return source


class ResearchAwardsTests(unittest.TestCase):
    def test_requested_amount_and_source_outcome(self):
        row = source(html()).read_records()[0]
        self.assertEqual('20000000', row['fields']['requested_amount'])
        self.assertEqual('Innvilget søknad', row['fields']['outcome'])
        self.assertEqual('2026-06-15', row['published'])
        self.assertNotIn('awarded_amount', row['fields'])
        self.assertNotIn('published', row['fields'])

    def test_source_amount_grouping_spaces_are_normalized(self):
        row = source(html(values=['363504', 'Institutt', 'Prosjekt', 'Tema', '20 000 000', 'date'])).read_records()[0]
        self.assertEqual('20000000', row['fields']['requested_amount'])

    def test_rejects_wrong_heading_schema_types_and_date(self):
        for raw in (html(title='Avslåtte søknader'), html(header=['Wrong']),
                    html(published='31.02.2026'), html(values=['363504', None, 'Title', '', '200', 'date']),
                    html(values=['363504', 'Org', 'Title', '', 'NaN', 'date'])):
            with self.subTest(raw=raw), self.assertRaises(SourceError):
                source(raw).read_records()

    def test_duplicate_result_block_is_not_silently_selected(self):
        with self.assertRaises(SourceError):
            source(html() + html()).read_records()

    def test_requires_official_url(self):
        config = SourceConfig(id='rcn', kind='research_awards', label='RCN',
            urls=('https://example.com/utlysninger/example/',), filters=FilterRule(match_all=True), options={})
        with self.assertRaises(ValueError):
            ResearchAwardsSource(config)

    def test_opt_in_discovery_reads_every_selected_result_call(self):
        cards = [('Se Resultat', '/utlysninger/2026/first/#results', 'status--result-is-published'),
                 ('Gjennomført', '/utlysninger/2026/not-ready/', 'status--is-completed'),
                 ('Se Resultat', '/utlysninger/2025/older/#results', 'status--result-is-published'),
                 ('Se Resultat', '/utlysninger/2026/second/#results', 'status--result-is-published')]
        second = html(values=['363505', 'Institutt 2', 'Prosjekt 2', 'Tema', '300000', 'date'])
        source = discovery(listing(cards), [html(), second])
        rows = source.read_records()
        self.assertEqual(['363504', '363505'], [row['fields']['project_number'] for row in rows])
        self.assertEqual(3, source.get.call_count)
        self.assertTrue(all('/2026/' in row['url'] for row in rows))

    def test_discovery_supports_observed_result_table_variants(self):
        cards = [('Se Resultat', '/utlysninger/2026/phd/#results', 'status--result-is-published'),
                 ('Se Resultat', '/utlysninger/2026/award/#results', 'status--result-is-published')]
        phd_header = HEADERS[:4] + [' Gradsgivende institusjon '] + HEADERS[5:]
        phd = html(header=phd_header, values=['363504', 'Org', 'Ph.d.', '', 'Universitetet', 'date'])
        award_header = HEADERS[:4] + ['Tildelt beløp'] + HEADERS[5:]
        award = html(header=award_header, values=['363505', 'Org', 'Award', '', '1500000', 'date'])
        rows = discovery(listing(cards), [phd, award]).read_records()
        self.assertEqual('Universitetet', rows[0]['fields']['degree_institution'])
        self.assertEqual('1500000', rows[1]['fields']['awarded_amount'])

    def test_discovery_rejects_truncation_duplicates_and_bad_cards(self):
        card = ('Se Resultat', '/utlysninger/2026/first/#results', 'status--result-is-published')
        for raw, options in ((listing([card, card]), {}),
                             (listing([card, ('Se Resultat', '/utlysninger/2026/second/', 'x')]),
                              {'max_discovery_calls': 1}),
                             (listing([('Se Resultat', 'https://example.test/utlysninger/2026/x/', 'x')]), {}),
                             (b'<div class="proposal"><a class="proposal--link" href="/utlysninger/2026/x/">X</a></div>', {}),
                             (b'<html>maintenance</html>', {})):
            with self.subTest(raw=raw), self.assertRaises(SourceError):
                discovery(raw, [], **options).read_records()

    def test_discovery_empty_selected_year_is_valid_and_configuration_is_strict(self):
        cards = [('Se Resultat', '/utlysninger/2025/older/#results', 'status--result-is-published')]
        self.assertEqual([], discovery(listing(cards), []).read_records())
        invalid = [
            {'discover_results': 'yes', 'discovery_years': [2026]},
            {'discover_results': True, 'discovery_years': [True]},
            {'discover_results': True, 'discovery_years': [2026], 'max_discovery_calls': '3'},
            {'discover_results': True, 'discovery_years': [2026], 'max_listing_pages': 2},
            {'discover_results': True, 'discovery_years': [2026], 'max_listing_pages': True},
        ]
        for options in invalid:
            config = SourceConfig(id='rcn', kind='research_awards', label='RCN', urls=(),
                filters=FilterRule(match_all=True), options=options)
            with self.assertRaises(ValueError): ResearchAwardsSource(config)

    def test_discovery_rejects_partial_group_and_new_pagination(self):
        cards = [('Se Resultat', '/utlysninger/2026/first/', 'status--result-is-published')]
        for raw in (listing(cards).replace(b'(1 utlysninger)', b'(2 utlysninger)'),
                    listing(cards) + b'<a rel="next" href="?page=2">Neste</a>',
                    listing(cards).replace(b'proposal-group--header', b'new-header')):
            with self.subTest(raw=raw), self.assertRaises(SourceError):
                discovery(raw, [html()]).read_records()

    def test_notification_routing_preserves_rows_scope_and_history(self):
        from dataclasses import replace
        from tests.test_change_sources import poll
        from unittest.mock import patch
        url = 'https://www.forskningsradet.no/utlysninger/2026/first/'
        cards = [('Se Resultat', url, 'status--result-is-published')]
        plain = discovery(listing(cards), [html()])
        routed = discovery(listing(cards), [html()], suppress_call_urls=[url])
        self.assertEqual(plain.scope, routed.scope)
        first, alerts = poll(plain)
        routed_first, routed_alerts = poll(routed)
        self.assertEqual(first, routed_first)
        self.assertEqual([], alerts); self.assertEqual([], routed_alerts)
        changed = html(values=['363504', 'Changed institution', 'Prosjekt', 'Tema', '20000000', 'date'])
        plain = discovery(listing(cards), [changed])
        routed = discovery(listing(cards), [changed], suppress_call_urls=[url])
        next_plain, alerts = poll(plain, first)
        next_routed, routed_alerts = poll(routed, first)
        self.assertEqual(next_plain, next_routed)
        self.assertEqual(1, len(alerts)); self.assertEqual([], routed_alerts)
        with self.assertRaises(ValueError):
            discovery(listing(cards), [], suppress_call_urls=['https://example.test/x/'])
