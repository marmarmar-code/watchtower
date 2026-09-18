import copy
from datetime import date
from io import BytesIO
import unittest
from unittest.mock import Mock, patch
from xml.etree import ElementTree as E
from zipfile import ZipFile

from watchtower.config import FilterRule, SourceConfig
from watchtower.engine import notification_entries
from watchtower.sources.common import SourceError
from watchtower.sources.nav_redundancy import NavRedundancySource, PAGE, TITLE, TYPES, SUPPRESSED
from watchtower.sources.workbooks import NS, REL, RID
from test_change_sources import poll, response

URL = 'https://www.nav.no/_/attachment/download/example/202608_AG200%20Report.xlsx'


class Today(date):
    @classmethod
    def today(cls): return cls(2026, 9, 18)


def cells(**values):
    row = [''] * 30
    for index, value in values.items(): row[int(index[1:])] = str(value)
    return row


def tables(value='20', standard='SN2025', months=('202607', '202608')):
    updated = 'Rapport oppdatert: 02.09.2026'
    industries = [cells(), cells(c1='Kilde: NAV', c3=TITLE),
                  cells(c3='Næring Hovedområde. Antall berørte personer', c13=updated)]
    for kind in TYPES:
        industries += [cells(c1=kind), cells(c2='Næring Hovedområde ' + standard,
                                              **{'c'+str(i+4): m for i,m in enumerate(months)}),
                       cells(c2='I alt', **{'c'+str(i+4): '100' for i,m in enumerate(months)}),
                       cells(c1='F', c2='Bygge- og anleggsvirksomhet',
                             **{'c'+str(i+4): value for i,m in enumerate(months)}),
                       cells(c2='Ukjent, gammel næringsstandard',
                             **{'c'+str(i+4): '5' for i,m in enumerate(months)}), cells()]
    national = [cells(), cells(c1='Kilde: NAV', c3=TITLE), cells(c3='Tidsserie', c14=updated),
                cells(), cells(), cells(), cells(c2=TYPES[0], c6=TYPES[1]),
                cells(c1='Melding mottatt måned', c4='Antall berørte personer', c7='Antall berørte personer')]
    national += [cells(c1=m, c4='100', c7='100') for m in reversed(months)]
    notes = [cells(), cells(c1='Kilde: NAV'), cells(c3=updated),
             cells(c1='SN2025. Virksomheter med gammel næringstandard vises som ukjent.')]
    return {'Næring Hovedområde': industries, 'Tidsserie': national, 'Om statistikken': notes}


def workbook(sheets):
    output = BytesIO()
    with ZipFile(output, 'w') as z:
        wb = E.Element(NS+'workbook'); listing = E.SubElement(wb, NS+'sheets')
        rels = E.Element(REL+'Relationships')
        for index, (name, rows) in enumerate(sheets.items(), 1):
            E.SubElement(listing, NS+'sheet', {'name': name, 'sheetId': str(index), RID: 'rId'+str(index)})
            E.SubElement(rels, REL+'Relationship', {'Id': 'rId'+str(index), 'Target': 'worksheets/sheet'+str(index)+'.xml'})
            root = E.Element(NS+'worksheet'); data = E.SubElement(root, NS+'sheetData')
            for n, values in enumerate(rows, 1):
                row = E.SubElement(data, NS+'row', {'r': str(n)})
                for i, text in enumerate(values):
                    if not text: continue
                    col = ''; num = i+1
                    while num: num, rem = divmod(num-1, 26); col = chr(65+rem) + col
                    cell = E.SubElement(row, NS+'c', {'r': col+str(n), 't': 'inlineStr'})
                    E.SubElement(E.SubElement(cell, NS+'is'), NS+'t').text = text
            z.writestr('xl/worksheets/sheet'+str(index)+'.xml', E.tostring(root))
        z.writestr('xl/workbook.xml', E.tostring(wb)); z.writestr('xl/_rels/workbook.xml.rels', E.tostring(rels))
    return output.getvalue()


@patch('watchtower.sources.nav_redundancy.date', Today)
class NavRedundancyTests(unittest.TestCase):
    def source(self, **options):
        return NavRedundancySource(SourceConfig(id='nav', kind='nav_redundancy', label='NAV', urls=(PAGE,),
            filters=FilterRule(match_all=True), options={'industry_codes': ['F'], **options}), retry_attempts=1)

    def load(self, source, raw):
        html = ('<a href="'+URL+'">Melding om permittering og masseoppsigelser. August 2026 (xls)</a>').encode()
        source.get = Mock(side_effect=lambda url, **kw: response(html if url == PAGE else raw))

    def test_realistic_workbook_quiet_repeat_and_two_complete_reads(self):
        source = self.source(); self.load(source, workbook(tables()))
        state, alerts = poll(source); self.assertEqual(4, len(source._next['rows'])); self.assertEqual([], alerts)
        self.assertEqual(4, source.get.call_count)
        repeated, alerts = poll(source, state); self.assertEqual(state, repeated); self.assertEqual([], alerts)
        self.assertEqual('2026-08', source.report_summary['latest_month'])

    def test_suppression_is_unknown_and_is_never_zero_in_notification(self):
        source = self.source(); self.load(source, workbook(tables('*'))); state, _ = poll(source)
        self.assertTrue(all(v['row']['fields']['affected_people'] == SUPPRESSED for v in source._next['rows'].values()))
        self.load(source, workbook(tables('0'))); state, alerts = poll(source, state)
        details = notification_entries(alerts)[0].details; text = ' '.join(details)
        self.assertIn(SUPPRESSED+' → 0', text); self.assertIn('ikke faktisk gjennomførte', text)
        self.assertIn('SN2025', text); self.assertIn('duplikater', text); self.assertLessEqual(len(details), 8)
        self.load(source, workbook(tables('*'))); _, alerts = poll(source, state)
        self.assertIn('0 → '+SUPPRESSED, ' '.join(notification_entries(alerts)[0].details))

    def test_standard_change_totals_missing_values_and_repeated_industries_fail(self):
        mutations = [lambda t: t['Næring Hovedområde'][4].__setitem__(2, 'Næring Hovedområde SN2007'),
                     lambda t: t['Næring Hovedområde'][5].__setitem__(4, '99'),
                     lambda t: t['Næring Hovedområde'][6].__setitem__(4, ''),
                     lambda t: t['Næring Hovedområde'].insert(7, copy.deepcopy(t['Næring Hovedområde'][6])),
                     lambda t: t['Næring Hovedområde'][6].__setitem__(4, '101'),
                     lambda t: t['Næring Hovedområde'][7].__setitem__(2, 'Unknown')]
        for mutation in mutations:
            data = tables(); mutation(data)
            with self.subTest(mutation=mutation), self.assertRaises(SourceError): self.source()._parse(workbook(data), '2026-08', URL)

    def test_month_gaps_bad_identity_stale_report_and_incomplete_notes_fail(self):
        bad = [tables(months=('202606', '202608')), tables(months=('202608', '202608'))]
        data = tables(); data['Næring Hovedområde'][2][13] = 'Rapport oppdatert: 01.01.2026'; bad.append(data)
        data = tables(); data['Om statistikken'][-1][1] = 'No classification'; bad.append(data)
        data = tables(); data['Tidsserie'][1][3] = 'Other statistics'; bad.append(data)
        for data in bad:
            with self.subTest(data=str(data)[:60]), self.assertRaises(SourceError): self.source()._parse(workbook(data), '2026-08', URL)
        with self.assertRaises(SourceError): self.source()._parse(workbook(tables()), '2026-07', URL)

    def test_race_and_regression_preserve_state(self):
        source = self.source(); self.load(source, workbook(tables())); state, _ = poll(source); saved = copy.deepcopy(state)
        first = source._parse(workbook(tables()), '2026-08', URL)
        second = source._parse(workbook(tables('21')), '2026-08', URL)
        source._export = Mock(side_effect=[first, second])
        with self.assertRaisesRegex(SourceError, 'changed during reading'): poll(source, state)
        self.assertEqual(saved, state)
        older = source._parse(workbook(tables(months=('202606', '202607'))), '2026-07', URL)
        source._export = Mock(return_value=older)
        with self.assertRaisesRegex(SourceError, 'regressed'): poll(source, state)

    def test_rolling_window_keeps_older_history_and_fails_missing_current_rows(self):
        source = self.source(); self.load(source, workbook(tables())); state, _ = poll(source)
        current = source._parse(workbook(tables(months=('202608',))), '2026-08', URL)
        source._export = Mock(return_value=current)
        repeated, alerts = poll(source, state); self.assertEqual(state, repeated); self.assertEqual([], alerts)
        full = source._parse(workbook(tables()), '2026-08', URL)
        source._export = Mock(return_value=(full[0][1:], full[1], full[2]))
        with self.assertRaisesRegex(SourceError, 'previously observed'): poll(source, state)

    def test_new_month_is_not_rebaseline(self):
        source = self.source(); earlier = source._parse(workbook(tables(months=('202607',))), '2026-07', URL)
        source._export = Mock(return_value=earlier); state, _ = poll(source)
        source._export = Mock(return_value=source._parse(workbook(tables()), '2026-08', URL))
        _, alerts = poll(source, state); self.assertEqual(2, len(alerts))
        self.assertTrue(all('2026-08' in ' '.join(e.details) for e in notification_entries(alerts)))

    def test_bounds_unknown_selection_empty_and_unexpected_download_fail(self):
        for options in [{'industry_codes': []}, {'industry_codes': ['41']}, {'allow_empty': True}, {'complete_snapshot': True}]:
            with self.subTest(options=options), self.assertRaises(ValueError): self.source(**options)
        source = self.source(industry_codes=['M']); self.load(source, workbook(tables()))
        with self.assertRaises(SourceError): source.read_records()
        source = self.source(max_records=1); self.load(source, workbook(tables()))
        with self.assertRaises(SourceError): source.read_records()
        source = self.source(); source.get = Mock(return_value=response(b'<html>Missing</html>'))
        with self.assertRaises(SourceError): source.read_records()
