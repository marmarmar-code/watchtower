import unittest
from xml.etree import ElementTree as E

from watchtower.sources.common import SourceError
from watchtower.sources.workbooks import NS, _rows


def sheet(gap=False):
    root = E.Element(NS + 'worksheet'); data = E.SubElement(root, NS + 'sheetData')
    for number, text in [(2, 'Heading'), (4 if gap else 3, 'Value')]:
        row = E.SubElement(data, NS + 'row', {'r': str(number)})
        cell = E.SubElement(row, NS + 'c', {'r': 'A' + str(number), 't': 'inlineStr'})
        E.SubElement(E.SubElement(cell, NS + 'is'), NS + 't').text = text
    return root


class LeadingBlankWorkbookTests(unittest.TestCase):
    def test_opt_in_only_and_original_row_positions_are_preserved(self):
        with self.assertRaises(SourceError): _rows(sheet(), [], 10, 1)
        with self.assertRaises(SourceError): _rows(sheet(), [], 10, 1, allow_blank_rows=True)
        self.assertEqual([[''], ['Heading'], ['Value']],
                         _rows(sheet(), [], 10, 1, allow_leading_blank_rows=True))

    def test_leading_blanks_do_not_allow_interior_gaps_or_formulas(self):
        with self.assertRaises(SourceError): _rows(sheet(True), [], 10, 1, allow_leading_blank_rows=True)
        self.assertEqual(4, len(_rows(sheet(True), [], 10, 1, allow_leading_blank_rows=True, allow_blank_rows=True)))
        root = sheet(); E.SubElement(root.find('.//' + NS + 'c'), NS + 'f').text = '1+1'
        with self.assertRaises(SourceError): _rows(root, [], 10, 1, allow_leading_blank_rows=True)
