from copy import deepcopy
from io import BytesIO
import unittest
from unittest.mock import Mock
from zipfile import ZIP_DEFLATED, ZipFile

from tests.test_change_sources import poll
from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.dmp_prices import DmpPricesSource, HEADERS, PAGE_URL

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
PAGE = (b'<a href="/contentassets/example/revised.xlsx">Oversikt over revurderte priser '
        b'gyldig fra 01.10.2026 - oppdatert (Excel)</a>')


def config(**options):
    return SourceConfig(id="prices", kind="dmp_prices", label="Maksimalpriser",
                        urls=(PAGE_URL,), filters=FilterRule(match_all=True), options=options)


def response(raw):
    return Mock(status_code=200, headers={}, iter_content=Mock(return_value=[raw]), close=Mock())


def values(**changes):
    row = dict(zip(HEADERS, (
        "012345", "Legemiddel", "Firma AS", "A01AA01", "Stoff", "Tablett", "10 mg",
        "", "1", "30", "stk", "149.08000000000001", "200.1", "140", "190.00",
        "0", "0", "Markedsført")))
    row.update(changes)
    return [row[name] for name in HEADERS]


def workbook(rows, title="Revurderte priser 1. okt 2026", formula=False, duplicate=False):
    content = [[title], ["Merknad"], [], list(HEADERS), *rows]
    strings, positions = [], {}
    def index(value):
        if value not in positions:
            positions[value] = len(strings); strings.append(value)
        return positions[value]
    xml_rows = []
    for number, row in enumerate(content, 1):
        if number == 3:
            continue
        cells = []
        for column, value in enumerate(row):
            ref = f"{chr(65 + column)}{number}"
            if formula and number == 5 and column == 11:
                cells.append(f'<c r="{ref}"><f>1+1</f><v>2</v></c>')
            elif value == "":
                cells.append(f'<c r="{ref}"/>')
            elif number >= 5 and column >= 11:
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="s"><v>{index(value)}</v></c>')
        xml_rows.append(f'<row r="{number}">{"".join(cells)}</row>')
    sheet = f'<worksheet xmlns="{NS}"><sheetData>{"".join(xml_rows)}</sheetData></worksheet>'
    shared = f'<sst xmlns="{NS}">' + "".join(f'<si><t>{x}</t></si>' for x in strings) + '</sst>'
    result = BytesIO()
    with ZipFile(result, "w", ZIP_DEFLATED) as archive:
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
        if duplicate:
            archive.writestr("xl/sharedStrings.xml", shared)
    return result.getvalue()


class DmpPricesTests(unittest.TestCase):
    def source(self, rows=None, page=PAGE, **options):
        source = DmpPricesSource(config(**options))
        source.get = Mock(side_effect=[response(page), response(workbook(rows or [values()]))])
        return source

    def test_discovery_row_baseline_and_repeat(self):
        source = self.source(); state, alerts = poll(source)
        self.assertEqual([], alerts)
        source = self.source(); rows = source.read_records()
        self.assertEqual("varenummer:012345", rows[0]["key"])
        self.assertEqual("149.08", rows[0]["fields"]["current_aip"])
        self.assertEqual("Vedtatt", rows[0]["fields"]["price_status"])
        self.assertIsNone(rows[0]["published"])
        source = self.source(); _, alerts = poll(source, state)
        self.assertEqual([], alerts)

    def test_change_alert_and_float_text_noise(self):
        source = self.source(); state, _ = poll(source)
        source = self.source([values(**{"Maks AIP Gyldig": "149.08"})])
        state, alerts = poll(source, state); self.assertEqual([], alerts)
        source = self.source([values(**{"Maks AIP Gyldig": "149.08", "Maks AIP Vedtatt": "141"})])
        _, alerts = poll(source, state)
        self.assertEqual(1, len(alerts))
        details = " ".join(alerts[0].item.alert_details)
        self.assertIn("140.00 → 141.00", details)
        self.assertNotIn("refusjon", details.lower())

    def test_price_only_revision_keeps_advance_notice_status_visible(self):
        original = values(**{"Maks AIP Vedtatt": "0", "Maks AUP Vedtatt": "0",
                             "Maks AIP Forhåndsvarslet": "130", "Maks AUP Forhåndsvarslet": "180"})
        state, _ = poll(self.source([original]))
        changed = deepcopy(original); changed[HEADERS.index("Maks AIP Forhåndsvarslet")] = "131"
        _, alerts = poll(self.source([changed]), state)
        self.assertEqual(1, len(alerts))
        details = " ".join(alerts[0].item.alert_details)
        self.assertIn("Prisstatus: Forhåndsvarslet", details)
        self.assertIn("Gyldig fra: 2026-10-01", details)

    def test_fallback_identity_and_ambiguity(self):
        self.assertEqual("varenummer:362", self.source([values(Varenummer="362")]).read_records()[0]["key"])
        self.assertEqual("varenummer:012345",
                         self.source([values(Varenummer="012345")]).read_records()[0]["key"])
        fallback = values(Varenummer="")
        self.assertTrue(self.source([fallback]).read_records()[0]["key"].startswith("pakning:"))
        with self.assertRaisesRegex(SourceError, "ambiguously"):
            self.source([fallback, deepcopy(fallback)]).read_records()

    def test_invalid_dates_rows_pairs_and_links(self):
        bad_rows = [
            values(**{"Maks AUP Vedtatt": "0"}),
            values(**{"Maks AIP Forhåndsvarslet": "130", "Maks AUP Forhåndsvarslet": "180"}),
            values(**{"Maks AIP Gyldig": "149.081"}),
            values(**{"Markedsføringsstatus": "Ukjent"}), values(Varenummer="abc"),
        ]
        for row in bad_rows:
            with self.subTest(row=row), self.assertRaises(SourceError):
                self.source([row]).read_records()
        for page in (b"<html></html>", PAGE.replace(b"01.10.2026", b"32.10.2026"),
                     PAGE.replace(b"/contentassets/example/revised.xlsx", b"https://example.test/a.xlsx")):
            with self.assertRaises(SourceError): self.source(page=page).read_records()
        source = DmpPricesSource(config())
        source.get = Mock(side_effect=[response(PAGE), response(workbook([values()],
                                                                      "Revurderte priser 1. nov 2026"))])
        with self.assertRaisesRegex(SourceError, "validity"):
            source.read_records()

    def test_archive_bounds_formula_and_configuration(self):
        for raw in (workbook([values()], formula=True), workbook([values()], duplicate=True)):
            source = DmpPricesSource(config())
            source.get = Mock(side_effect=[response(PAGE), response(raw)])
            with self.assertRaises(SourceError): source.read_records()
        with self.assertRaisesRegex(SourceError, "archive bounds"):
            self.source(max_unpacked_bytes=1024).read_records()
        for candidate in (config(events=["removed"]), config(complete_snapshot=True),
                          config(max_records=True)):
            with self.assertRaises(ValueError): DmpPricesSource(candidate)


if __name__ == "__main__": unittest.main()
