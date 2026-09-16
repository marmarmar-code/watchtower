import io
import unittest
from unittest.mock import Mock
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

from watchtower.config import FilterRule, SourceConfig
from watchtower.engine import evaluate
from watchtower.sources.common import SourceError
from watchtower.sources.production_grants import ProductionGrantsSource, PAGE_URL
from watchtower.sources.workbooks import NS, REL, RID


BASE = 'https://www.medietilsynet.no/globalassets/dokumenter/produksjonstilskudd/'


def workbook(year=2025, values=None, *, header=None, delta_override=None):
    values = values or [('Example Publication', 1200, 1000)]
    rows = [[f'Endelig tildeling av produksjonstilskudd {year}'], [],
            header or ['Mediets navn', 'Tilskuddskategori', f'Produksjons- tilskudd {year}',
                       f'Produksjons- tilskudd {year-1}', 'Endring i kroner', 'Endring i prosent']]
    for name, amount, prior in values:
        rows.append([name, 'Example category', amount, prior,
                     amount-prior if delta_override is None else delta_override, 0])
    book = ET.Element(NS+'workbook')
    sheets = ET.SubElement(book, NS+'sheets')
    ET.SubElement(sheets, NS+'sheet', {'name':'Ark1', RID:'rId1'})
    rels = ET.Element(REL+'Relationships')
    ET.SubElement(rels, REL+'Relationship', {'Id':'rId1', 'Target':'worksheets/sheet1.xml'})
    sheet = ET.Element(NS+'worksheet')
    data = ET.SubElement(sheet, NS+'sheetData')
    for number, cells in enumerate(rows, 1):
        row = ET.SubElement(data, NS+'row', {'r':str(number)})
        for index, value in enumerate(cells):
            cell = ET.SubElement(row, NS+'c', {'r':chr(65+index)+str(number)})
            if isinstance(value, int):
                if index == 4:
                    ET.SubElement(cell, NS+'f').text = f'C{number}-D{number}'
                ET.SubElement(cell, NS+'v').text = str(value)
            else:
                cell.set('t','inlineStr')
                ET.SubElement(ET.SubElement(cell, NS+'is'), NS+'t').text = value
    out = io.BytesIO()
    with ZipFile(out, 'w', ZIP_DEFLATED) as archive:
        for name, element in [('xl/workbook.xml',book), ('xl/_rels/workbook.xml.rels',rels),
                              ('xl/worksheets/sheet1.xml',sheet)]:
            archive.writestr(name, ET.tostring(element))
    return out.getvalue()


def response(raw):
    result = Mock(status_code=200)
    result.iter_content.return_value = [raw]
    return result


def poll(source, previous=None):
    items = source.fetch_with_state(previous)
    state, alerts, _ = evaluate(source.config, items, previous, max_seen=3000)
    return source.augment_state(state), alerts


class ProductionGrantsTests(unittest.TestCase):
    def source(self, **options):
        cfg = SourceConfig(id='grant-table', kind='production_grants', label='Annual grants',
                           filters=FilterRule(match_all=True), alert_on_update=True, options=options)
        return ProductionGrantsSource(cfg, retry_attempts=1)

    def load(self, source, *, year=2025, values=None, raw=None, page=None):
        url = BASE+f'awards-{year}.xlsx'
        page = page or f'<a href="{BASE}awards-2023.pdf">Oversikt over tilskott gitt i 2023</a><a href="{url[:-5]}.pdf">Oversikt over tilskott gitt i {year}</a>'
        source.get = Mock(side_effect=lambda target, **kw: response(page.encode() if target == PAGE_URL else raw or workbook(year, values)))
        return url

    def test_latest_year_discovery_nok_amounts_and_quiet_repeat(self):
        source = self.source()
        url = self.load(source)
        rows = source.read_records()
        self.assertEqual('2025:publication:example publication', rows[0]['key'])
        self.assertEqual(url, rows[0]['url'])
        self.assertEqual(1200, rows[0]['fields']['amount_nok'])
        first, alerts = poll(source)
        self.assertEqual([], alerts)
        second, alerts = poll(source, first)
        self.assertEqual([], alerts)
        self.assertEqual(first, second)

    def test_new_year_is_new_event_and_same_year_revision_is_update(self):
        source = self.source()
        self.load(source, year=2024)
        previous, _ = poll(source)
        self.load(source, year=2025, values=[('Example Publication', 1500, 1200)])
        current, alerts = poll(source, previous)
        self.assertEqual(1, len(alerts))
        details = ' '.join(alerts[0].item.alert_details)
        self.assertIn('Tildeling 2025: 1 500 kr', details)
        self.assertIn('+300 kr (+25.0 %)', details)
        self.assertIn('ikke datoen for et nytt enkeltvedtak', details)
        self.load(source, year=2025, values=[('Example Publication', 1600, 1200)])
        revised, alerts = poll(source, current)
        self.assertEqual(1, len(alerts))
        self.assertIn('Revidert årstildeling', alerts[0].item.alert_details)
        self.assertIn('Tildelt beløp: 1 500 kr → 1 600 kr', alerts[0].item.alert_details)
        _, alerts = poll(source, revised)
        self.assertEqual([], alerts)

    def test_zero_prior_amount_does_not_invent_percentage(self):
        source = self.source()
        self.load(source, values=[('Example Publication', 1200, 0)])
        item = source.fetch()[0]
        self.assertIn('Endring fra 2024: +1 200 kr', item.alert_details)
        self.assertFalse(any('%' in detail for detail in item.alert_details))

    def test_reordering_is_quiet_and_absence_is_not_withdrawal(self):
        source = self.source()
        values = [('Example Publication', 1200, 1000), ('Other Publication', 3000, 2900)]
        self.load(source, values=values)
        first, _ = poll(source)
        self.load(source, values=list(reversed(values)))
        second, alerts = poll(source, first)
        self.assertEqual([], alerts)
        self.assertEqual(first, second)
        self.load(source, values=values[:1])
        _, alerts = poll(source, second)
        self.assertEqual([], alerts)
        with self.assertRaises(ValueError):
            self.source(complete_snapshot=True, events=['removed'])

    def test_year_regression_is_rejected(self):
        source = self.source()
        self.load(source, year=2025)
        previous, _ = poll(source)
        self.load(source, year=2024)
        with self.assertRaisesRegex(SourceError, 'regressed'):
            poll(source, previous)

    def test_title_year_and_column_units_must_match(self):
        source = self.source()
        self.load(source, raw=workbook(year=2024))
        with self.assertRaisesRegex(SourceError, 'title/year'):
            source.read_records()
        self.load(source, raw=workbook(header=['Mediets navn','Tilskuddskategori','Amount (thousands)',
                                             'Previous year','Endring i kroner','Endring i prosent']))
        with self.assertRaisesRegex(SourceError, 'columns or units'):
            source.read_records()

    def test_duplicate_publication_and_wrong_delta_are_rejected(self):
        source = self.source()
        self.load(source, values=[('Example Publication',1200,1000), ('example publication',3000,2000)])
        with self.assertRaisesRegex(SourceError, 'duplicated'):
            source.read_records()
        self.load(source, raw=workbook(delta_override=999))
        with self.assertRaisesRegex(SourceError, 'reconcile'):
            source.read_records()

    def test_external_and_ambiguous_latest_links_fail(self):
        source = self.source()
        for page in [
            '<a href="https://example.test/awards.xlsx">Oversikt over tilskott gitt i 2025</a>',
            f'<a href="{BASE}one.xlsx">Oversikt over tilskott gitt i 2025</a><a href="{BASE}two.xlsx">Oversikt over tilskott gitt i 2025</a>',
        ]:
            self.load(source, page=page)
            with self.assertRaises(SourceError):
                source.read_records()
            self.assertEqual(1, source.get.call_count)

    def test_explicit_excel_link_is_used(self):
        source = self.source()
        url = BASE+'grants.xlsx'
        self.load(source, page=f'<a href="{url}">Oversikt over tilskudd gitt i 2025</a>')
        source.read_records()
        self.assertEqual(url, source.get.call_args.args[0])

    def test_download_limits_and_unexpected_redirects_fail(self):
        source = self.source(max_bytes=1024)
        source.get = Mock(return_value=response(b'x'*1025))
        with self.assertRaisesRegex(SourceError, 'max_bytes'):
            source.read_records()
        source.get = Mock(return_value=Mock(status_code=302))
        with self.assertRaisesRegex(SourceError, 'redirect'):
            source.read_records()


if __name__ == '__main__':
    unittest.main()
