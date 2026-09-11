from pathlib import Path
from copy import deepcopy
from io import BytesIO
import unittest
from unittest.mock import Mock
from zipfile import ZIP_DEFLATED, ZipFile

from tests.test_change_sources import poll
from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.ema_medicines import EmaMedicinesSource, HEADERS, XLSX_URL, _workbook_rows

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def config(**options):
    return SourceConfig(id="ema", kind="ema_medicines", label="EMA-legemidler", urls=(XLSX_URL,),
                        filters=FilterRule(match_all=True), options=options)


def excel_column(index):
    value = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        value = chr(65 + remainder) + value
    return value


def row_values(**changes):
    values = [""] * len(HEADERS)
    values[0] = "Human"; values[1] = "Testmedisin"; values[2] = "EMEA/H/C/000123"
    values[3] = "Authorised"; values[6] = "teststoff"; values[7] = "teststoff hydrate"
    values[8] = "Testområde"; values[15] = "Behandling av testsykdom"
    values[25] = "Test Pharma AS"; values[31] = "02/01/2020"
    values[36] = "03/01/2020"
    values[38] = "https://www.ema.europa.eu/en/medicines/human/EPAR/testmedisin"
    for name, value in changes.items(): values[HEADERS.index(name)] = value
    return values


def workbook(rows, *, formula=False, extra_size=0, headers=HEADERS, duplicate_cell=False):
    all_rows = [list(headers), *rows]
    xml = [f'<worksheet xmlns="{NS}"><sheetData>']
    for number, values in enumerate(all_rows, 9):
        xml.append(f'<row r="{number}">')
        for index, value in enumerate(values, 1):
            ref = f"{excel_column(index)}{number}"
            if duplicate_cell and number == 10 and index == 2: ref = f"A{number}"
            formula_xml = "<f>1+1</f>" if formula and number == 10 and index == 2 else ""
            xml.append(f'<c r="{ref}" t="inlineStr">{formula_xml}<is><t>{value}</t></is></c>')
        xml.append("</row>")
    xml.append("</sheetData></worksheet>")
    shared = f'<sst xmlns="{NS}"></sst>'
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", "".join(xml))
        if extra_size: archive.writestr("large.bin", b"x" * extra_size)
    return buffer.getvalue()


def duplicate_member_workbook():
    raw = workbook([row_values()])
    buffer = BytesIO(raw)
    output = BytesIO()
    with ZipFile(buffer) as source, ZipFile(output, "w", ZIP_DEFLATED) as target:
        for info in source.infolist(): target.writestr(info.filename, source.read(info.filename))
        target.writestr("xl/sharedStrings.xml", f'<sst xmlns="{NS}"></sst>')
    return output.getvalue()


def response(raw):
    return Mock(status_code=200, headers={}, iter_content=Mock(return_value=[raw]), close=Mock())


class EmaMedicinesTests(unittest.TestCase):
    def source(self, **options): return EmaMedicinesSource(config(**options))

    def test_maps_real_columns_and_filters_veterinary(self):
        veterinary = row_values(Category="Veterinary", **{"EMA product number": "EMEA/V/C/000123"})
        source = self.source(); reply = response(workbook([row_values(), veterinary])); source.get = Mock(return_value=reply)
        record = source.read_records()[0]
        self.assertEqual("EMEA/H/C/000123", record["key"])
        self.assertEqual("teststoff", record["fields"]["inn"])
        self.assertEqual("2020-01-02", record["fields"]["authorisation_date"])
        self.assertEqual("2020-01-03", record["published"])
        reply.close.assert_called_once()

    def test_generation_metadata_is_absent_and_substantive_change_alerts(self):
        source = self.source(); source.get = Mock(return_value=response(workbook([row_values()])))
        state, alerts = poll(source); self.assertEqual([], alerts)
        source.get = Mock(return_value=response(workbook([row_values()])))
        state, alerts = poll(source, state); self.assertEqual([], alerts)
        changed = row_values(**{"Medicine status": "Suspended", "Therapeutic indication": "Ny indikasjon"})
        source.get = Mock(return_value=response(workbook([changed])))
        _, alerts = poll(source, state)
        self.assertEqual(1, len(alerts)); text = " ".join(alerts[0].item.alert_details)
        self.assertIn("Godkjent → Suspendert", text); self.assertIn("Indikasjon endret", text)

    def test_actual_workbook_has_complete_unique_human_scope(self):
        with (Path(__file__).parent / "fixtures/event_sources/ema-medicines-2026-09-11.xlsx").open("rb") as handle:
            raw = handle.read()
        rows = _workbook_rows(raw, 8000000)
        self.assertEqual(2733, len(rows))
        source = self.source(); source.get = Mock(return_value=response(raw)); records = source.read_records()
        self.assertEqual(2339, len(records)); self.assertEqual(2339, len({row["key"] for row in records}))

    def test_invalid_header_width_identity_status_date_and_url_fail_closed(self):
        cases = []
        bad_header = list(HEADERS); bad_header[1] = "Changed"
        cases.append(workbook([row_values()], headers=bad_header))
        for field, value in [("EMA product number", "H123"), ("Medicine status", "Unknown"),
                             ("Opinion status", "Maybe"), ("Marketing authorisation date", "31/02/2020"),
                             ("First published date", "2020-01-03"), ("Category", "Unknown"),
                             ("Medicine URL", "https://example.test/product")]:
            cases.append(workbook([row_values(**{field: value})]))
        for raw in cases:
            source = self.source(); source.get = Mock(return_value=response(raw))
            with self.assertRaises(SourceError): source.read_records()

    def test_duplicate_formula_zip_and_record_bounds_fail_closed(self):
        for raw in (workbook([row_values(), row_values()]), workbook([row_values()], formula=True),
                    workbook([row_values()], duplicate_cell=True), duplicate_member_workbook(), b"not zip"):
            source = self.source(); source.get = Mock(return_value=response(raw))
            with self.assertRaises(SourceError): source.read_records()
        with self.assertRaisesRegex(SourceError, "bounds"):
            _workbook_rows(workbook([row_values()], extra_size=2000), 1024)
        source = self.source(max_records=1)
        source.get = Mock(return_value=response(workbook([row_values(), row_values(**{"EMA product number":"EMEA/H/C/000124"})])))
        with self.assertRaisesRegex(SourceError, "max_records"): source.read_records()

    def test_configuration_rejects_removal_wrong_url_and_weak_numbers(self):
        invalid = [config(events=["removed"]), config(complete_snapshot=True), config(max_records=True),
                   config(max_unpacked_bytes="8000000"),
                   SourceConfig(id="ema", kind="ema_medicines", label="EMA", urls=("https://example.test",),
                                filters=FilterRule(match_all=True), options={})]
        for candidate in invalid:
            with self.assertRaises(ValueError): EmaMedicinesSource(candidate)


if __name__ == "__main__": unittest.main()
