from copy import deepcopy
from io import BytesIO
import unittest
from unittest.mock import Mock
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from watchtower.config import SourceConfig, FilterRule
from watchtower.sources.common import SourceError
from watchtower.sources.dmp_wholesale import DmpWholesaleSource, HEADERS, SHEET, PAGE, excel_date
from watchtower.sources.workbooks import NS, RID, REL
from test_change_sources import poll, response


def workbook(rows, as_of='19.05.2026', epoch='0', headers=HEADERS):
    root = ET.Element(NS+'workbook')
    ET.SubElement(root, NS+'workbookPr', {'date1904':epoch})
    sheets = ET.SubElement(root, NS+'sheets')
    ET.SubElement(sheets, NS+'sheet', {'name':SHEET, 'sheetId':'1', RID:'rId1'})
    rels = ET.Element(REL+'Relationships')
    ET.SubElement(rels, REL+'Relationship', {'Id':'rId1', 'Target':'worksheets/sheet1.xml'})
    sheet = ET.Element(NS+'worksheet'); data = ET.SubElement(sheet, NS+'sheetData')
    for number, values in enumerate([[SHEET], ['Oversikt oppdatert: '+as_of], headers, *rows], 1):
        row = ET.SubElement(data, NS+'row', {'r':str(number)})
        for column, value in enumerate(values):
            cell = ET.SubElement(row, NS+'c', {'r':chr(65+column)+str(number), 't':'inlineStr'})
            ET.SubElement(ET.SubElement(cell, NS+'is'), NS+'t').text = value
    output = BytesIO()
    with ZipFile(output, 'w') as archive:
        for name, node in [('xl/workbook.xml',root), ('xl/_rels/workbook.xml.rels',rels), ('xl/worksheets/sheet1.xml',sheet)]:
            archive.writestr(name, ET.tostring(node))
    return output.getvalue()


def row(location='Example North', end='47737'):
    return ['7000-1','26/12345-2','Example AS','123456789',location,'Grossisttillatelse','45911',end]


def source(raw=None, repeated=None, as_of='19.05.2026'):
    src = DmpWholesaleSource(SourceConfig(id='wholesale',kind='dmp_wholesale',label='Grossistregister',
                                         filters=FilterRule(match_all=True)))
    page = b'<h1>Godkjente legemiddelgrossister</h1><a href="/export.xlsx">Last ned excel-oversikt</a>'
    raw = raw or workbook([row()], as_of)
    src.get = Mock(side_effect=[response(page),response(raw),response(page),response(repeated or raw)])
    return src


class DmpWholesaleTests(unittest.TestCase):
    def test_full_register_preserves_multiple_locations_per_authorisation(self):
        src = source(workbook([row(),row('Example South')]))
        records = src.read_records()
        self.assertEqual(1,len(records))
        self.assertEqual(['Example North','Example South'],records[0]['fields']['locations'])
        self.assertEqual('7000-1',records[0]['key'])
        self.assertEqual('2025-09-11',records[0]['fields']['valid_from'])
        self.assertIsNone(records[0]['published'])
        self.assertEqual(2,src.raw_row_count)

    def test_date_and_order_only_revisions_are_quiet_but_validity_changes_alert(self):
        first = source(workbook([row(),row('Example South')]))
        state,alerts = poll(first); self.assertEqual([],alerts)
        reordered = source(workbook([row('Example South'),row()], '20.05.2026'))
        state,alerts = poll(reordered,state); self.assertEqual([],alerts)
        changed = source(workbook([row(end='47738'),row('Example South',end='47738')], '20.05.2026'))
        state,alerts = poll(changed,state)
        self.assertEqual(1,len(alerts))
        self.assertIn('Gyldig til: 2030-09-11 → 2030-09-12',' '.join(alerts[0].item.alert_details))
        self.assertIsNone(alerts[0].item.published)

    def test_conflicting_or_duplicate_subrecords_fail(self):
        conflict = row('Example South'); conflict[1] = '26/12345-3'
        for rows in ([row(),row()], [row(),conflict]):
            with self.subTest(rows=rows),self.assertRaises(SourceError):
                source(workbook(rows)).read_records()

    def test_disappearance_retains_history_for_later_revision(self):
        second = row(); second[0] = '7000-2'
        state,_ = poll(source(workbook([row(),second])))
        absent,alerts = poll(source(workbook([second])),state)
        self.assertFalse(alerts)
        self.assertIn('7000-1',absent['source_state']['records']['rows'])
        _,alerts = poll(source(workbook([row(end='47738'),second])),absent)
        self.assertEqual(1,len(alerts))
        self.assertIn('2030-09-11 → 2030-09-12',str(alerts[0].item.alert_details))

    def test_second_read_failure_and_regressed_date_preserve_input_state(self):
        state,_ = poll(source()); prior = deepcopy(state)
        for src in (source(repeated=workbook([row(end='47738')])),source(as_of='18.05.2026')):
            with self.assertRaises(SourceError): src.fetch_with_state(state)
            self.assertEqual(prior,state)

    def test_invalid_date_epoch_header_and_transport_are_visible_failures(self):
        for raw in (workbook([row()],epoch='1'), workbook([row()],headers=('Changed',*HEADERS[1:]))):
            with self.assertRaises(SourceError): source(raw).read_records()
        with self.assertRaises(SourceError): excel_date('today')
        with self.assertRaises(SourceError): excel_date('1.5')
        src=source(); src.get=Mock(return_value=response(b'no',status=302))
        with self.assertRaises(SourceError): src.read_records()


if __name__=='__main__': unittest.main()
