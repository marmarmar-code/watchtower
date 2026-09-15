import unittest
from unittest.mock import Mock

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.consumer_decisions import ConsumerDecisionsSource, URL
from watchtower.sources.common import SourceError
from test_change_sources import poll

LINK = '<a href="/lov-og-rett/vedtak/fov-2024-123">FOV-2024-123: First decision</a>'
SECOND = '<a href="/wp-content/uploads/2026/02/second.pdf">FOV-2024-123: Second decision</a>'
OLD = '<a href="/lov-og-rett/vedtak/fov-2020-1">FOV-2020-1: Old decision</a>'


def block(year, body, details=False):
    return (f'<details class="accordion__item"><summary><span>{year}</span></summary>{body}</details>' if details else
            f'<div class="page-list-accordion"><button><span>{year}</span></button><ul><li>{body}</li></ul></div>')


def page(*blocks):
    return '<main><article class="dokumenttype-vedtak">' + ''.join(blocks) + '</article></main><footer>' + OLD + '</footer>'


def source(html, **options):
    s = ConsumerDecisionsSource(SourceConfig(id='consumer', kind='consumer_decisions', label='Decisions', urls=(URL,),
                               filters=FilterRule(match_all=True), options={'latest_years':2, **options}))
    reply(s, html)
    return s


def reply(s, html):
    s.get = Mock(return_value=Mock(status_code=200, iter_content=Mock(return_value=[html.encode()]), close=Mock()))


class ConsumerDecisionsTests(unittest.TestCase):
    def test_actual_two_block_shapes_year_boundary_and_multiple_decisions_per_case(self):
        s = source(page(block(2026, LINK, True), block(2025, SECOND), block(2024, OLD)))
        rows = s.read_records()
        self.assertEqual(2, len(rows))
        self.assertEqual({'FOV-2024-123'}, {r['fields']['case_number'] for r in rows})
        self.assertEqual([['2026'], ['2025']], [r['source_years'] for r in rows])
        self.assertTrue(all(r['published'] is None for r in rows))
        # The source year is distinct from the year in the FOV identifier.
        self.assertNotIn('source_years', rows[0]['fields'])

    def test_quiet_baseline_repeat_year_movement_and_substantive_title_revision(self):
        s = source(page(block(2026, LINK, True), block(2025, SECOND)))
        state, alerts = poll(s); self.assertEqual([], alerts)
        state, alerts = poll(s, state); self.assertEqual([], alerts)
        reply(s, page(block(2026, SECOND, True), block(2025, LINK)))
        state, alerts = poll(s, state); self.assertEqual([], alerts)
        reply(s, page(block(2026, SECOND, True), block(2025, LINK.replace('First decision','Corrected list title'))))
        _, alerts = poll(s, state); self.assertEqual(1, len(alerts))
        self.assertEqual('Endrede listeopplysninger', alerts[0].item.alert_details[0])

    def test_published_years_do_not_require_current_calendar_year_and_duplicates_coalesce(self):
        s=source(page(block(2020, LINK, True), block(2019, LINK)))
        rows=s.read_records();self.assertEqual(1,len(rows));self.assertEqual(['2019','2020'],rows[0]['source_years'])
        reply(s,page(block(2020,LINK,True),block(2019,LINK.replace('First','Conflicting'))))
        with self.assertRaisesRegex(SourceError,'conflicting'):s.read_records()

    def test_bad_url_missing_identity_and_broken_year_structures_fail_closed(self):
        urls = ['https://other.example/a', 'https://user@www.forbrukertilsynet.no/lov-og-rett/vedtak/x',
                'https://www.forbrukertilsynet.no:444/lov-og-rett/vedtak/x', '/unknown.pdf',
                '/lov-og-rett/vedtak/x?query=1', '/lov-og-rett/vedtak/x#fragment', 'http://www.forbrukertilsynet.no/lov-og-rett/vedtak/x']
        for url in urls:
            html=page(block(2026,LINK.replace('/lov-og-rett/vedtak/fov-2024-123',url),True),block(2025,SECOND))
            with self.subTest(url=url),self.assertRaises(SourceError):source(html).read_records()
        for html in [page(block(2026,LINK,True)), page(block(2026,LINK,True),block(2026,SECOND)),
                     page(block('broken',LINK,True),block(2025,SECOND)), page(block(2026,'',True),block(2025,SECOND)),
                     page(block(2026,LINK.replace('FOV-2024-123: ',''),True),block(2025,SECOND)),
                     page(block(2026,LINK,True),block(2025,SECOND)).replace('dokumenttype-vedtak','changed')]:
            with self.assertRaises(SourceError):source(html).read_records()
        with self.assertRaises(SourceError):source(page(block(2026,LINK,True),block(2025,SECOND)),max_records=1).read_records()
        for opts in ({'events':['removed']},{'complete_snapshot':True},{'latest_years':0}):
            with self.assertRaises(ValueError):source('',**opts)


if __name__ == '__main__':
    unittest.main()
