"""Cached numeric results are an explicit opt-in and never calculated."""
import unittest
from xml.etree import ElementTree as E

from watchtower.sources.common import SourceError
from watchtower.sources.workbooks import NS, _rows


def sheet(cache='0', kind='n', formula='1/0', gap=False):
    root = E.Element(NS+'worksheet')
    data = E.SubElement(root, NS+'sheetData')
    header = E.SubElement(data, NS+'row', {'r':'1'})
    c = E.SubElement(header, NS+'c', {'r':'A1', 't':'inlineStr'})
    E.SubElement(E.SubElement(c, NS+'is'), NS+'t').text = 'Amount'
    number = '3' if gap else '2'
    row = E.SubElement(data, NS+'row', {'r':number})
    c = E.SubElement(row, NS+'c', {'r':'A'+number, 't':kind})
    E.SubElement(c, NS+'f').text = formula
    if cache is not None:
        E.SubElement(c, NS+'v').text = cache
    return root


class WorkbookCacheTests(unittest.TestCase):
    def test_default_rejects_formula_and_opt_in_reads_cache_without_evaluation(self):
        root = sheet('0', formula='1/0')
        with self.assertRaises(SourceError):
            _rows(root, [], 10, 1)
        self.assertEqual([['Amount'], ['0']], _rows(root, [], 10, 1, True))
        self.assertEqual('-1.2345E+3', _rows(sheet('-1.2345E+3'), [], 10, 1, True)[1][0])

    def test_missing_non_numeric_and_typed_formula_caches_fail(self):
        for cache, kind in [(None,'n'), ('','n'), ('NaN','n'), ('INF','n'), ('#VALUE!','e'), ('0','s'), ('0','inlineStr')]:
            with self.subTest(cache=cache, kind=kind), self.assertRaises(SourceError):
                _rows(sheet(cache, kind), ['Text'], 10, 1, True)

    def test_blank_row_permission_is_separate_and_retains_row_positions(self):
        root = sheet('1', gap=True)
        with self.assertRaises(SourceError):
            _rows(root, [], 10, 1, True)
        self.assertEqual([['Amount'], [''], ['1']], _rows(root, [], 10, 1, True, True))

    def test_duplicate_formula_and_rows_still_fail_in_cache_mode(self):
        root = sheet('1')
        E.SubElement(root.findall('.//'+NS+'c')[1], NS+'f').text = '2+2'
        with self.assertRaises(SourceError):
            _rows(root, [], 10, 1, True, True)
        root = sheet('1')
        E.SubElement(root.find(NS+'sheetData'), NS+'row', {'r':'2'})
        with self.assertRaises(SourceError):
            _rows(root, [], 10, 1, True, True)


if __name__ == '__main__':
    unittest.main()
