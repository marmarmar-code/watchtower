import unittest
from unittest.mock import Mock

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.public_cases import PublicCasesSource
from watchtower.sources.common import SourceError


class PublicCasesTests(unittest.TestCase):
    def config(self, **options):
        board = options.get("board", "kofa")
        urls = {"kofa": "https://www.klagenemndssekretariatet.no/klagenemda-for-offentlige-anskaffelser-kofa/innkomne-avgjorte-saker",
                "media": "https://www.klagenemndssekretariatet.no/medieklagenemnda/innkomne-avgjorte-saker",
                "marketing": "https://www.klagenemndssekretariatet.no/markedsradet/innkomne-avgjorte-saker"}
        return SourceConfig(id="kofa", kind="public_cases", label="KOFA",
                            urls=(urls.get(board, urls["kofa"]),),
                            filters=FilterRule(match_all=True), options=options)

    def source(self, html, **options):
        source = PublicCasesSource(self.config(**options))
        response = Mock(status_code=200, headers={}, iter_content=Mock(return_value=[html.encode()]))
        source.get = Mock(return_value=response)
        return source

    def test_normalizes_case_identity_fields_and_date(self):
        source = self.source("""<table><tr><th>Dato</th><th>Saknr.</th><th>Type</th><th>Innklaget</th><th>Saken gjelder</th><th>Avgjørelse</th><th>Status</th></tr>
        <tr><td>08.09.2026</td><td><a href='/sak/2026-2039'>2026/2039</a></td><td>Rådgivende sak</td><td>Vestland fylkeskommune</td><td>Tildelingsevaluering</td><td>–</td><td>Innkommet</td></tr></table>""")
        row = source.read_records()[0]
        self.assertEqual("kofa:2026/2039", row["key"])
        self.assertIsNone(row["published"])
        self.assertEqual("https://www.klagenemndssekretariatet.no/sak/2026-2039", row["url"])
        self.assertEqual("Innkommet", row["fields"]["status"])

    def test_status_filter_and_empty_selection_fail_closed(self):
        source = self.source("<table><tr><th>Dato</th><th>Saknr.</th><th>Type</th><th>Innklaget</th><th>Saken gjelder</th><th>Avgjørelse</th><th>Status</th></tr><tr><td>08.09.2026</td><td><a href='/sak/2026-2039'>2026/2039</a></td><td>x</td><td>Y</td><td>Z</td><td>–</td><td>Innkommet</td></tr></table>", statuses=["Avgjort"])
        self.assertEqual([], source.read_records())

    def test_media_and_marketing_use_their_own_schemas(self):
        media = self.source("<table><tr><th>Dato</th><th>Saknr.</th><th>Klager</th><th>Avgjørelse</th><th>Status</th><th>Saksbeh.</th></tr><tr><td>09.09.2026</td><td><a href='/medieklage/2026-780'>2026/780</a></td><td>A</td><td>B</td><td>Avgjort</td><td>X</td></tr></table>", board="media")
        self.assertEqual("media:2026/780", media.read_records()[0]["key"])
        marketing = self.source("<table><tr><th>Dato</th><th>Saknr.</th><th>Part/klager</th><th>Saken gjelder</th><th>Avgjørelse</th><th>Regelverk</th><th>Status</th><th>Saksbeh.</th></tr><tr><td>24.06.2026</td><td><a href='/markedsrad/2026-1031'>2026/1031</a></td><td>A</td><td>B</td><td>C</td><td>D</td><td>Avgjort</td><td>X</td></tr></table>", board="marketing")
        self.assertEqual("marketing:2026/1031", marketing.read_records()[0]["key"])

    def test_missing_table_is_an_error(self):
        with self.assertRaises(SourceError):
            self.source("<html>maintenance</html>").read_records()

    def test_incomplete_page_cannot_enable_removals(self):
        with self.assertRaises(ValueError):
            self.source("", complete_snapshot=True)

    def test_headers_and_rows_are_strict(self):
        bad = "<table><tr><th>Dato</th><th>Saknr.</th></tr><tr><td>08.09.2026</td><td>2026/1</td></tr></table>"
        with self.assertRaises(SourceError):
            self.source(bad).read_records()

    def test_invalid_date_and_external_detail_fail(self):
        base = "<table><tr><th>Dato</th><th>Saknr.</th><th>Type</th><th>Innklaget</th><th>Saken gjelder</th><th>Avgjørelse</th><th>Status</th></tr><tr><td>{date}</td><td><a href='{href}'>2026/1</a></td><td>x</td><td>Y</td><td>Z</td><td>–</td><td>Innkommet</td></tr></table>{next}"
        for html in (base.format(date="31.02.2026", href="/sak/1", next=""),
                     base.format(date="08.09.2026", href="https://example.test/sak/1", next="")):
            with self.assertRaises(SourceError): self.source(html).read_records()

    def test_duplicate_is_rejected_before_status_filter(self):
        row = "<tr><td>08.09.2026</td><td>2026/1</td><td>x</td><td>Y</td><td>Z</td><td>–</td><td>Innkommet</td></tr>"
        html = "<table><tr><th>Dato</th><th>Saknr.</th><th>Type</th><th>Innklaget</th><th>Saken gjelder</th><th>Avgjørelse</th><th>Status</th></tr>" + row + row.replace("Innkommet", "Avgjort") + "</table>"
        with self.assertRaises(SourceError):
            self.source(html, statuses=["Avgjort"]).read_records()


if __name__ == "__main__":
    unittest.main()
