import html
import json
import unittest

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.medicine import MedicineSource


class Response:
    status_code = 200
    headers = {}
    def __init__(self, content): self.content = content
    def iter_content(self, _size): yield self.content
    def close(self): pass


def page(rows, status_date="10.09.2026", *, reorder=False):
    columns = {
        "Legemiddelnavn": [row[0] for row in rows],
        "Virkestoff(er)": [row[1] for row in rows],
        "Mangelperiode fra": [row[2] for row in rows],
        "Mangelperiode til": [row[3] for row in rows],
        "Informasjon/tiltak": [row[4] for row in rows],
        "Informasjon på nettside": [row[5] for row in rows],
        f"Status pr. {status_date}": [row[6] for row in rows],
    }
    if reorder:
        columns = dict(reversed(columns.items()))
    value = html.escape(json.dumps(columns, ensure_ascii=False), quote=True)
    return f'<html><input value="{value}" id="excelData" type="hidden"></html>'.encode()


ROW = ("Testmedisin 20 mg, 30 stk", "teststoff", "01.09.2026", "01.10.2026", "Bruk alternativ", "https://dmp.no/rad", "Pågående")


class MedicineTests(unittest.TestCase):
    def cfg(self, **options):
        return SourceConfig(id="medicine", kind="medicine", label="DMP", filters=FilterRule(match_all=True),
                            urls=("https://www.dmp.no/shortages",), options=options)

    def source(self, raw, **options):
        source = MedicineSource(self.cfg(**options))
        source.get = lambda *_a, **_k: Response(raw)
        return source

    def test_period_change_has_stable_key_and_norwegian_details(self):
        source = self.source(page([ROW]))
        first = source.fetch_with_state(None)
        state = source.augment_state({"source_state": {}})
        key = first[0].key
        changed_row = (*ROW[:3], "01.11.2026", *ROW[4:])
        source.get = lambda *_a, **_k: Response(page([changed_row]))
        changed = source.fetch_with_state(state)[0]
        self.assertEqual(key, changed.key)
        self.assertFalse(changed.suppress_alert)
        self.assertIn("Endrede periodeopplysninger før", changed.alert_details[1])
        self.assertNotIn('"periods"', " ".join(changed.alert_details))

    def test_reordered_periods_and_header_date_are_quiet(self):
        second = (*ROW[:2], "02.09.2026", "02.10.2026", "Annet tiltak", "", "Pågående")
        source = self.source(page([ROW, second]))
        source.fetch_with_state(None)
        state = source.augment_state({"source_state": {}})
        source.get = lambda *_a, **_k: Response(page([second, ROW], "11.09.2026", reorder=True))
        self.assertTrue(source.fetch_with_state(state)[0].suppress_alert)

    def test_identical_duplicates_dedupe_but_distinct_periods_remain(self):
        second = (*ROW[:2], "02.09.2026", "02.10.2026", "Annet", "", "Pågående")
        item = self.source(page([ROW, ROW, second])).fetch()[0]
        self.assertEqual(2, len(json.loads(item.text)["periods"]))

    def test_column_mismatch_invalid_content_and_malformed_identity_fail(self):
        mismatch = page([ROW]).replace(b'Mangelperiode til', b'Mangelperiode slutt')
        for raw, message in ((mismatch, "schema"), (b"<html></html>", "excelData"),
                             (page([("", *ROW[1:])]), "identity")):
            with self.subTest(message=message), self.assertRaisesRegex(SourceError, message):
                self.source(raw).fetch()

    def test_unequal_columns_and_non_text_cells_fail(self):
        raw = json.loads(html.unescape(page([ROW]).decode().split('value="', 1)[1].split('" id=', 1)[0]))
        for mutation in (lambda d: d["Legemiddelnavn"].append("x"), lambda d: d["Legemiddelnavn"].__setitem__(0, 7)):
            data = {key: list(value) for key, value in raw.items()}; mutation(data)
            encoded = html.escape(json.dumps(data), quote=True)
            with self.assertRaises(SourceError): self.source(f'<input id="excelData" value="{encoded}">'.encode()).fetch()

    def test_removal_and_complete_snapshot_are_rejected(self):
        for options in ({"events": ["removed"], "complete_snapshot": True}, {"complete_snapshot": True}):
            with self.assertRaises(ValueError): MedicineSource(self.cfg(**options))

    def test_default_limit_accepts_real_snapshot_scale(self):
        self.assertEqual(5000, MedicineSource(self.cfg()).max_records)


if __name__ == "__main__": unittest.main()
